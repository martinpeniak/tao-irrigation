# Codex Prompt — HomGar Irrigation Integration

## Status: Partially working. Commands reach the hub but control has bugs.

Repo: `github.com/martinpeniak/tao-irrigation`
Local: `/Users/mpeniak/Documents/Tao/tao-irrigation/`
Deployed to HA at: `root@tao-ha.tail03c0af.ts.net`
Component path on HA: `/homeassistant/custom_components/homgar_timers/`

---

## What works

- HA starts, logs in to HomGar, discovers 4 timers, connects MQTT — all clean
- REST command `POST /app/device/sub/update {sid, mid, param}` is accepted (code=0, paramVer increments)
- Hub wakes up within 20-90s and opens the physical valve
- Water pump load is visible when valve opens — physical confirmation works
- MQTT state updates from hub arrive on `/sys/{productKey}/{deviceName}/thing/service/property/set`
- Payload codec (build_open/close_command) is correct

---

## What is broken

### THE CORE PROBLEM — On/Off control does not work reliably

**Expected behaviour:** Tap zone ON in HA → valve opens within ~60s. Tap OFF → valve closes within ~60s.

**Actual behaviour:**
- Tapping ON sometimes works (valve opens after 20-90s delay) but sometimes doesn't
- Tapping OFF does NOT stop the valve. The valve keeps running for the full duration.
- After tapping OFF, the HA tile shows the zone as ON again moments later (entity reverts)
- The valve ran for 37 minutes uncontrolled during testing despite repeated OFF attempts

**Root cause — the retry loop:**

`turn_on` in `switch.py` spawns a background thread that re-sends the open command
every 20 seconds for up to 4 minutes (12 retries × 20s). This was added to compensate
for the hub's slow poll cycle. It caused a catastrophic failure:

1. User taps ON → 1 open command sent immediately
2. Retry thread sends open again at +20s, +40s, +60s, +80s... (up to 12 times)
3. User taps OFF → retry thread stops, 1 close command sent
4. But the hub has already queued 4-6 open commands from the retry loop
5. Hub executes each queued open command sequentially, each for the full duration
6. Close command arrives but hub ignores it — it's already mid-execution of a queue
7. Valve runs for (number_of_queued_opens × duration) minutes uncontrolled

**The fix — remove the retry loop entirely:**

The hub DOES pick up a single command within 20-90 seconds on its own poll cycle.
No retrying is needed. One send is sufficient.

```python
def turn_on(self, **kwargs):
    _LOGGER.warning("HomGar OPEN %s zone %d for %ds",
                    self._timer_name, self._zone_addr, self._duration_seconds)
    self._mqtt.send_open(self._hub_mid, self._timer_addr,
                         self._zone_addr, self._duration_seconds, sid=self._sid)
    self._is_on = True
    self.schedule_update_ha_state()

def turn_off(self, **kwargs):
    _LOGGER.warning("HomGar CLOSE %s zone %d",
                    self._timer_name, self._zone_addr)
    self._mqtt.send_close(self._hub_mid, self._timer_addr, sid=self._sid)
    self._is_on = False
    self.schedule_update_ha_state()
```

Remove entirely: `_retry_thread`, `_retry_stop`, `_stop_retry()`,
`RETRY_INTERVAL`, `RETRY_MAX`, and `import threading`.

**After this fix:** Turn on → hub picks up within 60s → valve opens.
Turn off → hub picks up within 60s → valve closes. No runaway. No queue buildup.
The 20-90s delay is acceptable for irrigation — this is not a light switch.

**IMPORTANT — Emergency close script:**
While this fix is being deployed, if a valve gets stuck open, run:
```bash
python3 emergency_close.py
```
This sends close commands directly to all 4 hubs via REST, bypassing HA entirely.

### Bug 2 — HA entity shows OFF while valve is physically running

**Symptom:** HA tile shows zone as OFF even though the physical valve is open
(water is flowing). Or shows ON even though valve is closed.

**Root cause:** The HA entity state is set optimistically in `turn_on`/`turn_off`
and only updated when an MQTT state update arrives from the hub. If the MQTT
connection is disrupted (e.g., user logged in via HomGar app, causing rc=7 kick),
state updates are missed and the entity shows stale state.

**Fix:** The `async_added_to_hass` listener is already wired correctly to update
state from MQTT. Just make sure the MQTT reconnect logic works. No code change
needed beyond the retry loop removal.

### Bug 3 — Duration number entity doesn't propagate to switch on HA restart

**Symptom:** The number entity stores duration (e.g., 10 min) but after HA restart,
the switch entity resets to DEFAULT_DURATION_SECONDS (600s = 10 min). This is fine
for the default but if a user changes duration to 5 min, it resets on restart.

**Fix:** Store duration in HA storage or just accept the stateless reset (10 min
default is fine for most zones).

---

## Architecture — what we know about the protocol

### Control flow (REST)
```
HA → POST /app/device/sub/update {sid, mid, param} → HomGar Cloud
HomGar Cloud → stores param, increments paramVersion
Hub → polls cloud every ~30-90s → reads new param → actuates valve
Hub → pushes state update via Alibaba IoT MQTT → HomGar Cloud → HA
```

### CRITICAL: HomGar uses a custom epoch
```python
HOMGAR_EPOCH_OFFSET = 1355964032  # seconds offset from Unix epoch (2012-12-20)

# CORRECT stop timestamp:
homgar_stop = (int(time.time()) - HOMGAR_EPOCH_OFFSET) + duration_seconds
struct.pack_into('<I', b, 24, homgar_stop)

# WRONG (what we had before, caused hub to ignore/immediately close):
struct.pack_into('<I', b, 24, int(time.time()) + duration_seconds)
```

### D01 payload byte offsets (52 bytes after `11#` prefix)
```
byte[6]:    0x20 | zone_addr  = zone running (zone 1 = 0x21, zone 2 = 0x22, zone 3 = 0x23)
            0x00              = all zones off
byte[24:28]: LE uint32        = stop timestamp in HomGar epoch
byte[42:44]: LE uint16        = duration in seconds
```

### Hub device IDs (TAO property)
| Timer name       | hub mid | sub addr | sid   |
|-----------------|---------|----------|-------|
| Oliver's Orchard | 33679  | 1        | 63474 |
| House Garden - 3 | 41212  | 1        | 77343 → D01 |
| House Garden - 2 | 41212  | 2        | 77344 → D02 |
| Oasis            | 50612  | 1        | 94930 |

### MQTT state update format (inbound from hub)
```
Topic: /sys/{productKey}/{deviceName}/thing/service/property/set
Param: "#P{ts}{hub_mid}|{D_updates_json}|{ts}|{propVer}#"
D01 value in D_updates_json contains the current valve state
```

### Payload seeding on startup
On startup, `__init__.py` calls `_seed_payloads()` which fetches real current D01
from `GET /app/device/getDeviceStatus?mid={mid}` and pre-populates
`mqtt_client._current_payloads`. This ensures `build_open_command` uses the real
hub payload as base, not a zero-filled fallback.

### Single session limitation
HomGar cloud only allows ONE active session per account. If the HomGar app logs in,
our MQTT client gets kicked (rc=7 disconnect). After the app closes, the client
reconnects automatically. The MQTT `_on_disconnect` / reconnect logic handles this.

---

## Tasks for Codex

### Task 1 (CRITICAL) — Fix runaway valve

Remove the retry loop from `switch.py` entirely. The hub picks up commands
within 30-90s without retrying. Retry caused queued open commands that couldn't
be cancelled.

Replace `turn_on` and `turn_off` with simple single-send:

```python
def turn_on(self, **kwargs):
    _LOGGER.warning("HomGar OPEN %s zone %d for %ds",
                    self._timer_name, self._zone_addr, self._duration_seconds)
    self._mqtt.send_open(self._hub_mid, self._timer_addr,
                         self._zone_addr, self._duration_seconds, sid=self._sid)
    self._is_on = True
    self.schedule_update_ha_state()

def turn_off(self, **kwargs):
    _LOGGER.warning("HomGar CLOSE %s zone %d",
                    self._timer_name, self._zone_addr)
    self._mqtt.send_close(self._hub_mid, self._timer_addr, sid=self._sid)
    self._is_on = False
    self.schedule_update_ha_state()
```

Also remove: `_retry_thread`, `_retry_stop`, `_stop_retry()`, `RETRY_INTERVAL`,
`RETRY_MAX` constants, and the `threading` import.

### Task 2 — Emergency stop script

Create a standalone Python script `emergency_close.py` at repo root:
- Logs in to HomGar
- Sends close command to ALL 4 hubs/timers immediately
- Useful when HA misbehaves

```python
# Usage: python3 emergency_close.py
# Closes all valves on all 4 HomGar hubs at TAO
```

### Task 3 — Deploy script

Update `deploy.sh` to also run `ha core restart` and tail logs:
```bash
#!/bin/bash
HA_HOST=${1:-tao-ha.tail03c0af.ts.net}
for f in __init__.py api.py mqtt.py switch.py number.py const.py manifest.json; do
    encoded=$(base64 < "homgar_timers/$f")
    ssh -o StrictHostKeyChecking=no root@${HA_HOST} \
        "echo '${encoded}' | base64 -d > /homeassistant/custom_components/homgar_timers/$f"
    echo "✓ $f"
done
ssh -o StrictHostKeyChecking=no root@${HA_HOST} 'ha core restart'
sleep 35
ssh -o StrictHostKeyChecking=no root@${HA_HOST} 'ha core logs 2>/dev/null | grep -i homgar | tail -15'
```

### Task 4 — Unit tests for codec

Create `tests/test_codec.py`:

Test with real captured payloads:
```python
# Baseline (all zones off) — real payload from Oliver's Orchard hub
BASELINE = "11#17E1BE0019D8001AD8001BD8001D201E201F2018DC0121B70000000022B70000000023B70000000025AD000026AD000027AD0000"

# App-triggered open (zone 1, 60s) — captured from MQTT sniff
APP_OPEN = "11#17E1BE0019D8211AD8001BD8001D201E201F2018DC0121B725BAEE1822B70000000023B70000000025AD3C0026AD000027AD0000"
```

Tests:
- `decode_d01(APP_OPEN)["active_zone"] == 1`
- `decode_d01(APP_OPEN)["duration_seconds"] == 60`
- `decode_d01(BASELINE)["active_zone"] is None`
- `build_open_command(BASELINE, zone_addr=1, duration_seconds=60)` produces
  correct `byte[6]=0x21`, `byte[42]=0x3C`, and HomGar epoch stop timestamp
- `build_close_command(APP_OPEN)` zeros bytes 6, 24-27, 42-43
- HomGar epoch: `homgar_now() + 60` matches `0x25BAEE18` approximately

### Task 5 — Smart irrigation automation (HA YAML)

Create `automations/smart_irrigation.yaml`:

Conditions before running any zone:
1. `sensor.luxpower_sna_pv_power_3` > 300W (Gardens solar producing)
2. `sensor.luxpower_sna_battery_state_of_charge_2` > 50% (battery healthy)
3. `sensor.well_depth` > 0.5m (well has water)
4. `weather.forecast_home` NOT in [rainy, pouring]

Trigger: daily at 07:00 OR `input_button.run_irrigation_now`

Action: run zones sequentially (one at a time, delay between zones),
duration from companion number entity, notify on completion or skip.

Also create `helpers/irrigation_helpers.yaml`:
```yaml
input_boolean:
  irrigation_auto_enabled:
    name: "Auto Irrigation Enabled"
    icon: mdi:sprinkler-variant
input_button:
  run_irrigation_now:
    name: "Run Irrigation Now"
    icon: mdi:play-circle
```

---

## HA instance
- SSH: `ssh root@tao-ha.tail03c0af.ts.net`
- Core: 2026.2.2, HAOS 17.1
- Component: `/homeassistant/custom_components/homgar_timers/`

## Key HA entities
```
switch.irrigation_olivers_orchard_first_year_trees   (hub 33679, sid 63474, zone 1)
switch.irrigation_olivers_orchard_olivers_orchard_2  (hub 33679, sid 63474, zone 2)
switch.irrigation_olivers_orchard_nebulizer          (hub 33679, sid 63474, zone 3)
switch.irrigation_house_garden_3_olivers_walk        (hub 41212, sid 77343, zone 1)
switch.irrigation_house_garden_3_buddha_garden       (hub 41212, sid 77343, zone 2)
switch.irrigation_house_garden_3_papaya_40           (hub 41212, sid 77343, zone 3)
switch.irrigation_house_garden_2_entrance_garden     (hub 41212, sid 77344, zone 1)
switch.irrigation_house_garden_2_dome_side           (hub 41212, sid 77344, zone 2)
switch.irrigation_house_garden_2_shower_side         (hub 41212, sid 77344, zone 3)
switch.irrigation_oasis_frontyard_garden             (hub 50612, sid 94930, zone 1)
switch.irrigation_oasis_nebulizer                    (hub 50612, sid 94930, zone 2)
switch.irrigation_oasis_zone_3                       (hub 50612, sid 94930, zone 3)
number.irrigation_<timer>_<zone>_duration            (1-120 min, default 10)

sensor.luxpower_sna_pv_power_3                       (Gardens solar W)
sensor.luxpower_sna_battery_state_of_charge_2        (Gardens SOC %)
sensor.well_depth                                    (metres)
sensor.well_rate_lpm                                 (L/min)
weather.forecast_home
```
