# tao-irrigation

Smart irrigation control for Project TAO — a fully off-grid solar-powered property in Oliva, Valencia, Spain.

## What's in here

```
homgar_timers/      Custom HA component for HomGar/RainPoint HTV0540FRF WiFi timers
deploy.sh           One-command SSH deploy to HA
```

## Quick start

```bash
chmod +x deploy.sh
./deploy.sh [HA_HOST]   # default: tao-ha.tail03c0af.ts.net
```

Add to `configuration.yaml`:
```yaml
homgar_timers:
  email: your@email.com
  password: "yourpassword"
  area_code: 34
```

Restart HA. All zone switches and duration entities appear automatically.

## Status

✅ Deployed and working on TAO HA (March 2026)
- 4 timers, 12 zones across Oliver's Orchard, House Garden -2/-3, Oasis
- Real-time valve state via Alibaba IoT MQTT
- Live Trees Auto confirmation uses pump power, not HomGar state, because HomGar
  state frequently lags or stays null while a valve is physically running
- Measured Papaya 40 latency on 2026-04-06:
  - pump start lag after `switch.turn_on`: about 38 seconds
  - final pump idle after `switch.turn_off`: about 50 seconds
  - live automation now allows up to 200 seconds for pump-start confirmation and
    uses a 20-second post-stop delay plus a 90-second idle-confirmation window

## Next steps

See `homgar_timers/CODEX_PROMPT.md` for the Codex continuation prompt covering:
- Smart automation (solar + well + weather conditions)
- Token refresh / auto-reconnect
- Unit tests
- Config flow UI setup
