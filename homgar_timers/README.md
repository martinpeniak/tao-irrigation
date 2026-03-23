# HomGar Irrigation Timers — HA Custom Component

Custom Home Assistant integration for RainPoint/HomGar **HTV0540FRF** WiFi irrigation timers.
Fully reverse-engineered from live API + MQTT traffic. Not affiliated with HomGar.

**Status: Deployed and working on TAO HA (March 2026)**

---

## What this does

- Logs into HomGar Cloud, discovers all HTV0540FRF timers across all homes
- Connects to Alibaba IoT MQTT for real-time valve state (cloud_push)
- Creates **switch entities** per zone — turn on = open valve, turn off = close
- Creates **number entities** per zone for duration (1–120 min, default 10)
- Auto-starts with HA on every boot, no manual intervention needed

---

## Supported Hardware

| Model | Type | Zones | Hub required |
|---|---|---|---|
| HTV0540FRF (WT-11W) | WiFi irrigation timer | 3 per unit | HWG0538WRF (WG03) |

---

## Quick Install

### 1. Copy files to HA

```bash
chmod +x ha-integrations/deploy.sh
./ha-integrations/deploy.sh [HA_HOST]
# Default HA_HOST: tao-ha.tail03c0af.ts.net
```

Or manually copy `ha-integrations/homgar_timers/` to `/homeassistant/custom_components/`.

### 2. Add to configuration.yaml

```yaml
homgar_timers:
  email: your@email.com
  password: "yourpassword"   # double-quotes needed if password contains $
  area_code: 34              # Spain=34, UK=44, US=1
```

### 3. Restart HA

```bash
ha core restart
```

### 4. Verify

```bash
ha core logs | grep -i homgar | tail -20
```

Expected:
```
INFO HomGar login OK token=a9ed024ce9...
INFO HomGar timer: Oliver's Orchard (mid=33679) zones=['First Year Trees', ...]
INFO HomGar MQTT connected, subscribed: /sys/a3iCXW3C5CP/...
INFO HomGar: added 12 zone switches
INFO HomGar: added 12 zone duration numbers
```

---

## Entities (TAO example — 4 timers, 12 zones)

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

number.irrigation_<timer>_<zone>_duration  (1–120 min, default 10)
```


---

## Architecture

```
HomGar Cloud (region3.homgarus.com)
  ↓ REST /auth/basic/app/login  →  token + Alibaba IoT credentials
  ↓ REST /app/member/appHome/list + /app/device/getDeviceByHid  →  timer list
  ↓ Alibaba IoT MQTT (aliyuncs.com:1883)  →  live state + commands
       ↓
  HA custom component (homgar_timers)
       ↓
  switch.* + number.* entities  →  automations / dashboard / voice
```

### Auth flow

1. POST `/auth/basic/app/login` — password is MD5-hashed, deviceId = MD5(email+area_code)
2. Response includes Alibaba IoT: `productKey`, `deviceName`, `deviceSecret`, `mqttHostUrl`
3. MQTT auth: `sign = HMAC-SHA1(deviceSecret, "clientId{dn}deviceName{dn}productKey{pk}")`
4. Subscribe: `/sys/{productKey}/{deviceName}/thing/service/property/set`

### D01 payload codec

```
hex payload after "11#" prefix:
  byte[6]:    0x20 | zone_addr  if zone running
              0x00              if all zones off
  byte[24:28]: LE uint32  stop timestamp (unix seconds)
  byte[42:44]: LE uint16  duration (seconds)
```

---

## Known Limitations

1. **Single session** — HomGar allows one active session. Logging in via API logs
   out the mobile app. Create a dedicated API account and share devices to it.

2. **Token expiry** — Alibaba IoT token expires (~60 days). Re-login needed.
   TODO: auto-reconnect with fresh token on disconnect.

3. **No schedule control** — We control manual open/close only. Schedules defined
   in the HomGar app continue to run independently.

---

## TODO / Next Steps

See `CODEX_PROMPT.md` for the full continuation prompt for Codex.
