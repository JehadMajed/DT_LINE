#!/usr/bin/env bash
# Belt-and-suspenders watchdog. systemd already restarts crashed services;
# this catches "process alive but wedged". Driven by dt-watchdog.timer every
# 2 minutes (installed by install.sh); output goes to the journal:
#   systemctl list-timers dt-watchdog.timer
#   journalctl -u dt-watchdog.service -f
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

# CAMERA OWNERSHIP: this was 0 because a separate concurrent session owned
# go2rtc and the Funnel, and two supervisors restarting one service is the
# failure that cost us a serial-port debugging session. That session ended
# with the SD card on 2026-09-16, and the camera was rebuilt here, so this
# watchdog owns the path again. Ambiguous ownership meant nothing supervised
# the camera at all.
MANAGE_CAMERA="${MANAGE_CAMERA:-1}"

# 3) Camera: go2rtc up and pi_cam has a producer?
if [ "$MANAGE_CAMERA" = "1" ] && systemctl cat go2rtc.service >/dev/null 2>&1; then
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

# 4) Camera's public path: the Cloudflare Tunnel carries the stream to the
#    deployed dashboard. systemd restarts cloudflared if it exits, but a
#    connector can stay up while registering no connections -- the process is
#    alive and the camera is still dark from outside, which is precisely the
#    "alive but wedged" case this watchdog exists for.
if [ "$MANAGE_CAMERA" = "1" ] && systemctl cat cloudflared.service >/dev/null 2>&1; then
    if ! systemctl is-active --quiet cloudflared; then
        LOG "cloudflared not active -> restart"
        sudo systemctl restart cloudflared
    elif ! journalctl -u cloudflared --since "5 minutes ago" --no-pager 2>/dev/null \
            | grep -q "Registered tunnel connection"; then
        # No registration in the last 5 min AND no live connection means it is
        # not merely quiet -- it never came up. Metrics are the cheap check.
        if ! curl -fsS --max-time 5 http://127.0.0.1:20241/ready 2>/dev/null | grep -q '"readyConnections":[1-9]'; then
            LOG "cloudflared has no ready connections -> restart"
            sudo systemctl restart cloudflared
        fi
    fi
fi
