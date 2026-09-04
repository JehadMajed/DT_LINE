#!/usr/bin/env bash
# Belt-and-suspenders watchdog. systemd already restarts crashed services;
# this catches "process alive but wedged". Runs from cron every 2 minutes:
#   */2 * * * * /home/jehadroot/DT_LINE/archive/deploy/dt-watchdog.sh >> /home/jehadroot/dt-watchdog.log 2>&1
set -u
LOG() { echo "$(date '+%F %T') $*"; }

# Count/kill only REAL bridge processes: those whose executable is python.
# `pgrep -f serial_mqtt_bridge.py` also matches any shell, ssh command, grep or
# log tail that merely mentions the script, and acting on that would pkill -9
# the live bridge. This bit us during testing and caused a real outage.
bridge_pids() {
    local p exe
    for p in $(pgrep -f serial_mqtt_bridge.py 2>/dev/null); do
        exe=$(readlink -f "/proc/$p/exe" 2>/dev/null || true)
        case "$exe" in
            */python*) echo "$p" ;;
        esac
    done
}

# 1) Exactly one bridge process. If 0 -> restart. If >1 -> kill all, restart.
N=$(bridge_pids | grep -c . || true)
if [ "${N:-0}" -eq 0 ]; then
    LOG "no bridge process -> restart"
    sudo systemctl restart dt-bridge
elif [ "${N:-0}" -gt 1 ]; then
    LOG "$N bridge processes (duplicate!) -> kill all + restart"
    for p in $(bridge_pids); do kill -9 "$p" 2>/dev/null || true; done
    sleep 2
    sudo systemctl restart dt-bridge
fi

# 2) Bridge liveness: is the bridge main loop still turning?
#    NOT "is telemetry arriving" -- that conflates a wedged BRIDGE with a silent
#    DEVICE, and the escalating recovery ladder already owns device silence.
#    Restarting the bridge because the ESP32 went quiet would fight the ladder.
#    The [SUMMARY] line is printed every 30s regardless of device state, so its
#    absence means the loop itself is stuck. (The old check grepped for
#    "ESP32 -> MQTT", a per-packet line that journal thinning removed -- it
#    would have matched zero and restarted the bridge every 2 minutes forever.)
if systemctl is-active --quiet dt-bridge; then
    seen=$(journalctl -u dt-bridge --since "90 seconds ago" --no-pager 2>/dev/null | grep -c "\[SUMMARY\]")
    if [ "${seen:-0}" -eq 0 ]; then
        LOG "bridge loop stalled (no [SUMMARY] in 90s) -> restart"
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
