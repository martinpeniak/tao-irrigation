#!/bin/bash
# Deploy homgar_timers to Home Assistant via SSH
# Usage: ./deploy.sh [HA_HOST]
set -e

HA_HOST=${1:-tao-ha.tail03c0af.ts.net}
SRC="$(dirname "$0")/homgar_timers"

echo "=== Deploying HomGar Timers to ${HA_HOST} ==="
ssh -o StrictHostKeyChecking=no root@${HA_HOST} \
    "mkdir -p /homeassistant/custom_components/homgar_timers/translations"

for f in __init__.py api.py config_flow.py mqtt.py switch.py number.py const.py manifest.json; do
    encoded=$(base64 < "${SRC}/${f}")
    ssh -o StrictHostKeyChecking=no root@${HA_HOST} \
        "echo '${encoded}' | base64 -d > /homeassistant/custom_components/homgar_timers/${f}"
    echo "  ✓ ${f}"
done

encoded=$(base64 < "${SRC}/translations/en.json")
ssh -o StrictHostKeyChecking=no root@${HA_HOST} \
    "echo '${encoded}' | base64 -d > /homeassistant/custom_components/homgar_timers/translations/en.json"
echo "  ✓ translations/en.json"

echo ""
echo "Restarting HA..."
ssh -o StrictHostKeyChecking=no root@${HA_HOST} 'ha core restart'

echo "Waiting 25s for restart..."
sleep 25

echo ""
echo "=== HomGar log output ==="
ssh -o StrictHostKeyChecking=no root@${HA_HOST} \
    'ha core logs 2>/dev/null | grep -i "homgar" | tail -20'
