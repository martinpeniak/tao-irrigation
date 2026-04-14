# tao-irrigation
## Canonical Status

This standalone repo is no longer the canonical source of truth.

Canonical home now:
- repo: `https://github.com/martinpeniak/tao-ops`
- path: `home-assistant/irrigation/`

Use `tao-ops` for all future durable irrigation changes, docs, and agent handoff work.
Treat this repo as a migration source or archive unless explicitly needed for history.


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

## Next steps

See `homgar_timers/CODEX_PROMPT.md` for the Codex continuation prompt covering:
- Smart automation (solar + well + weather conditions)
- Token refresh / auto-reconnect
- Unit tests
- Config flow UI setup
