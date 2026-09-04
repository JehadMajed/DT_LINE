#!/usr/bin/env bash
# Belt-and-suspenders watchdog. systemd already restarts crashed services;
# this catches "process alive but wedged". Runs from cron every 2 minutes:
#   */2 * * * * /home/jehadroot/DT_LINE/archive/deploy/dt-watchdog.sh >> /home/jehadroot/dt-watchdog.log 2>&1
set -u
LOG() { echo "$(date '+%F %T') $*"; }

# 1) Exactly one bridge process. If 0 -> restart. If >1 -> kill all, restart.
N=$(pgrep -cf serial_mqtt_bridge.py || true)
if [ "${N:-0}" -eq 0 ]; then
    LOG "no bridge process -> restart"
    sudo systemctl restart dt-bridge
elif [ "${N:-0}" -gt 1 ]; then
    LOG "$N bridge processes (duplicate!) -> kill all + restart"
    pkill -9 -f serial_mqtt_bridge.py
    sleep 2
    sudo systemctl restart dt-bridge
fi

# 2) Bridge liveness: a telemetry line logged in the last 90s?
if systemctl is-active --quiet dt-bridge; then
    seen=$(journalctl -u dt-bridge --since "90 seconds ago" --no-pager 2>/dev/null | grep -c "ESP32 -> MQTT")
    if [ "${seen:-0}" -eq 0 ]; then
        LOG "bridge alive but no telemetry in 90s -> restart"
        sudo systemctl restart dt-bridge
    fi
fi

# 3) Camera: go2rtc up and pi_cam has a producer?
if systemctl cat go2rtc.service >/dev/null 2>&1; then
    if ! systemctl is-active --quiet go2rtc; then
        LOG "go2rtc not active -> restart"
        sudo systemctl restart go2rtc
    else
        info=$(curl -s --max-time 5 "http://127.0.0.1:1984/api/streams" || true)
        if ! echo "$info" | grep -q '"producers"'; then
            LOG "go2rtc has no producer ($info) -> restart"
            sudo systemctl restart go2rtc
        fi
    fi
fi

# 4) Tailscale Funnel: is the public URL actually reachable and mapped to :1984?
#    (the oneshot funnel unit can fail at boot if tailscaled isn't online yet)
if command -v tailscale >/dev/null; then
    if ! tailscale serve status 2>/dev/null | grep -q '127.0.0.1:1984'; then
        LOG "funnel mapping missing -> re-applying"
        sudo tailscale serve reset 2>/dev/null || true
        sudo tailscale funnel --bg --https=443 http://127.0.0.1:1984 || true
    fi
fi
