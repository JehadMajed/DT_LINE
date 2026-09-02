#!/usr/bin/env bash
# Belt-and-suspenders watchdog. systemd already restarts crashed services;
# this catches the "process alive but wedged" case (serial stuck, go2rtc
# producing EOF, funnel down). Run from cron every 2 minutes:
#   */2 * * * * /home/jehadroot/DT_LINE/deploy/dt-watchdog.sh >> /home/jehadroot/dt-watchdog.log 2>&1
set -u
LOG() { echo "$(date '+%F %T') $*"; }

# 1) Bridge: is the unit running at all?
if ! systemctl is-active --quiet dt-bridge; then
    LOG "dt-bridge not active -> restart"
    sudo systemctl restart dt-bridge
fi

# 2) Bridge liveness: has it logged a telemetry line in the last 90s?
last=$(journalctl -u dt-bridge --since "90 seconds ago" --no-pager 2>/dev/null | grep -c "ESP32 -> MQTT")
if [ "${last:-0}" -eq 0 ] && systemctl is-active --quiet dt-bridge; then
    LOG "dt-bridge alive but no telemetry in 90s -> restart"
    sudo systemctl restart dt-bridge
fi

# 3) Camera: unit running?
if ! systemctl is-active --quiet dt-camera; then
    LOG "dt-camera not active -> restart"
    sudo systemctl restart dt-camera
fi

# 4) Camera liveness: does the local go2rtc API report the pi_cam stream with a producer?
if systemctl is-active --quiet dt-camera; then
    info=$(curl -s --max-time 5 "http://127.0.0.1:1984/api/streams?src=pi_cam" || true)
    if ! echo "$info" | grep -q '"producers"'; then
        LOG "dt-camera API unhealthy ($info) -> restart"
        sudo systemctl restart dt-camera
    fi
fi
