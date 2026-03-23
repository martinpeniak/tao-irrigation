# CODEX_PROMPT.md — HomGar Irrigation Integration Continuation

## Your mission

You are continuing work on a custom Home Assistant integration for HomGar/RainPoint
HTV0540FRF WiFi irrigation timers deployed on **Project TAO** — a fully off-grid
solar-powered property in Oliva, Valencia, Spain.

The integration is **already deployed and working**. All 12 zone switch entities
and 12 duration number entities are live in HA. Your job is to extend it.

Branch: `feature/homgar-irrigation-integration`
Files:  `ha-integrations/homgar_timers/`

---

## TAO system context

### HA instance
- Core 2026.2.2, HAOS 17.1
- SSH: `ssh root@tao-ha.tail03c0af.ts.net`
- Tailscale URL: `http://tao-ha.tail03c0af.ts.net:8123`

### 4 independent solar islands (fully off-grid)
| Island | Inverter | Total PV sensor |
|--------|----------|----------------|
| Workshop | Growatt SPF | `sensor.growatt_spf_pv_power` |
| House | Growatt SPF _2 | `sensor.growatt_spf_pv_power_3` ⚠️ |
| Cabin | Luxpower SNA | `sensor.luxpower_sna_pv_power` |
| Gardens | Luxpower SNA _2 | `sensor.luxpower_sna_pv_power_3` ⚠️ |

⚠️ `_3` suffix = second device total PV, NOT string 3. Critical naming trap.

### Key entities for irrigation logic
```yaml
sensor.well_depth                    # metres (deeper = more available)
sensor.well_rate_lpm                 # L/min (negative = drawing down)
sensor.luxpower_sna_pv_power_3       # Gardens solar live W
sensor.luxpower_sna_battery_state_of_charge_2  # Gardens SOC %
sensor.smart_switch_23070736089517510d0248e1e9cfcfd7_power  # water pump W
switch.smart_switch_23070736089517510d0248e1e9cfcfd7_outlet # water pump
weather.forecast_home
input_boolean.irrigation_auto_enabled  # TO BE CREATED
```

### Irrigation switch entities (all working)
```
switch.irrigation_olivers_orchard_first_year_trees
switch.irrigation_olivers_orchard_olivers_orchard_2
switch.irrigation_olivers_orchard_nebulizer
switch.irrigation_house_garden_3_olivers_walk
switch.irrigation_house_garden_3_buddha_garden
switch.irrigation_house_garden_3_papaya_40
switch.irrigation_house_garden_2_entrance_garden
switch.irrigation_house_garden_2_dome_side
switch.irrigation_house_garden_2_shower_side
switch.irrigation_oasis_frontyard_garden
switch.irrigation_oasis_nebulizer
switch.irrigation_oasis_zone_3
```

---

## Task 1 (Priority HIGH) — Smart irrigation automation

Create `ha-integrations/automations/smart_irrigation.yaml`.

### Conditions (ALL must pass before any zone opens)
1. `input_boolean.irrigation_auto_enabled` is ON
2. `sensor.luxpower_sna_pv_power_3` > 300  (Gardens solar producing)
3. `sensor.luxpower_sna_battery_state_of_charge_2` > 50  (Gardens battery healthy)
4. `sensor.well_depth` > 0.5  (well has water)
5. `sensor.smart_switch_23070736089517510d0248e1e9cfcfd7_power` < 100  (pump not running)
6. `weather.forecast_home` state NOT in [rainy, pouring, lightning, lightning-rainy]

### Trigger
- Time: 07:00 daily
- OR: manual via `input_button.run_irrigation_now` (also to be created)

### Action sequence
- Check conditions → if fail, send notification explaining which condition failed
- If pass: run zones sequentially with 30s gap between zones:
  - Each zone duration comes from the companion `number.*_duration` entity
  - Only run zones where duration > 0
- Send completion notification: zones run, total water time, conditions summary

### Also create
`ha-integrations/helpers/irrigation_helpers.yaml` with:
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

## Task 2 (Priority MEDIUM) — Token refresh / auto-reconnect

In `mqtt.py`, implement reconnection logic in `HomGarMQTTClient`:

```python
def _on_disconnect(self, client, userdata, rc):
    self._connected = False
    _LOGGER.warning("HomGar MQTT disconnected rc=%s — will reconnect", rc)
    if rc != 0:  # unexpected disconnect
        threading.Thread(target=self._reconnect_loop, daemon=True).start()

def _reconnect_loop(self):
    """Re-login and reconnect after unexpected disconnect."""
    import time
    for attempt in range(1, 6):
        time.sleep(min(30 * attempt, 300))
        try:
            # Re-login to get fresh IoT credentials
            # (needs reference back to HomGarApi — pass in constructor)
            ...
        except Exception as e:
            _LOGGER.error("Reconnect attempt %d failed: %s", attempt, e)
```

You'll need to pass the `HomGarApi` instance into `HomGarMQTTClient.__init__`
and expose a `re_login()` method on it that refreshes `self._iot_credentials`.

---

## Task 3 (Priority MEDIUM) — Unit tests

Create `ha-integrations/tests/test_mqtt_codec.py`:

Test cases using actual captured payloads:

```python
# Baseline (all zones off):
BASELINE = "11#17E1BE0019D8001AD8001BD8001D201E201F2018DC0121B70000000022B70000000023B70000000025AD000026AD000027AD0000"

# After opening zone 1 for 10 minutes (600s):
ZONE1_ON = "11#17E1BF0019D8211AD8001BD8001D201E201F2018DC0121B77029EE1822B70000000023B70000000025AD580226AD000027AD0000"

# Test: decode_d01(ZONE1_ON)["active_zone"] == 1
# Test: decode_d01(ZONE1_ON)["duration_seconds"] == 600
# Test: decode_d01(BASELINE)["active_zone"] is None
# Test: build_open_command(BASELINE, zone_addr=1, duration_seconds=600) produces
#       correct byte[6]=0x21 and byte[42:44]=0x5802
# Test: build_close_command(ZONE1_ON) == BASELINE (byte[6], [24:28], [42:44] zeroed)
```

---

## Task 4 (Priority LOW) — Config flow

Add UI-based setup so users don't need to edit configuration.yaml.

1. Create `ha-integrations/homgar_timers/config_flow.py`
2. Update `manifest.json`: set `"config_flow": true`
3. Update `__init__.py` to handle both `async_setup` (legacy yaml) and
   `async_setup_entry` (config entry)
4. Create `ha-integrations/homgar_timers/translations/en.json` with UI strings

---

## Deployment

After making changes:
```bash
chmod +x ha-integrations/deploy.sh
./ha-integrations/deploy.sh
```

Check logs:
```bash
ssh root@tao-ha.tail03c0af.ts.net 'ha core logs | grep -i homgar | tail -20'
```

---

## File structure

```
ha-integrations/
├── deploy.sh                    # SSH deploy + restart script
├── automations/
│   └── smart_irrigation.yaml    # TO BE CREATED (Task 1)
├── helpers/
│   └── irrigation_helpers.yaml  # TO BE CREATED (Task 1)
├── tests/
│   └── test_mqtt_codec.py       # TO BE CREATED (Task 3)
└── homgar_timers/               # Custom component (deployed + working)
    ├── __init__.py
    ├── api.py
    ├── mqtt.py
    ├── switch.py
    ├── number.py
    ├── const.py
    ├── manifest.json
    ├── README.md
    └── CODEX_PROMPT.md          # This file
```
