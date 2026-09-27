#!/usr/bin/env bash
# Run ON THE PI:  bash ~/DT_LINE/archive/deploy/test_stability.sh
# Proves: services autostart, survive crashes, survive a serial unplug,
# survive an MQTT reconnect, and that commands actually reach the ESP32.
set -u
PASS=0; FAIL=0
ok()   { echo "  PASS: $*"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $*"; FAIL=$((FAIL+1)); }

echo "[1] Units enabled for boot?"
for U in dt-bridge go2rtc cloudflared; do
    systemctl is-enabled --quiet "$U" && ok "$U enabled" || bad "$U NOT enabled"
done

echo "[2] Units running now?"
systemctl is-active --quiet dt-bridge && ok "dt-bridge active" || bad "dt-bridge not active"
systemctl is-active --quiet go2rtc   && ok "go2rtc active"   || bad "go2rtc not active"

echo "[3] Telemetry flowing? (the bridge prints [SUMMARY] every 30s)"
# NOT "ESP32 -> MQTT". That is a per-packet line, and journal thinning collapses
# repeats ("seen 30x"), so the old check matched zero against a perfectly
# healthy bridge -- the same stale-string bug that once had the watchdog
# restarting the bridge every two minutes. [SUMMARY] is printed unconditionally
# every 30 s, which is why the watchdog switched to it; 35 s covers one period
# plus jitter.
SUM=$(timeout 35 journalctl -u dt-bridge -f --no-pager 2>/dev/null | grep -m1 "\[SUMMARY\]")
if [ -z "$SUM" ]; then
    bad "no [SUMMARY] in 35s — the bridge loop itself is stalled"
else
    echo "    ${SUM#*[SUMMARY] }"
    HZ=$(printf '%s' "$SUM" | sed -n 's/.*telemetry [0-9]* (\([0-9.]*\) Hz).*/\1/p')
    # Distinguish a wedged BRIDGE from a silent DEVICE: the loop can be turning
    # while the ESP32 says nothing, and those need different responses.
    if printf '%s' "$SUM" | grep -q "device_online=True"; then
        ok "telemetry ${HZ:-?} Hz, device online"
    else
        bad "bridge loop alive but device_online=False (ESP32 silent, not a bridge fault)"
    fi
fi

echo "[4] Crash recovery: kill the bridge, expect systemd restart within 10s"
sudo systemctl kill -s SIGKILL dt-bridge
sleep 10
systemctl is-active --quiet dt-bridge && ok "bridge auto-restarted" || bad "bridge did NOT restart"

echo "[5] MQTT command round-trip (readings OK but no commands = this is the test)"
echo "    Publishing a harmless command to the local broker..."
CMD='{"cmd":"ping"}'
if command -v mosquitto_pub >/dev/null; then
    mosquitto_pub -h 127.0.0.1 -t digital_twin/motor/command -m "$CMD"
    sleep 2
    if journalctl -u dt-bridge --since "5 seconds ago" --no-pager | grep -q "MQTT:.*-> ESP32.*Sending"; then
        ok "bridge received the command and wrote it to serial"
    else
        bad "bridge did NOT log forwarding the command — check subscription"
    fi
else
    echo "    (mosquitto_pub not installed: sudo apt install -y mosquitto-clients)"
fi
echo "    >>> Now confirm on the dashboard/ESP32 that a REAL command (e.g. set speed)"
echo "        visibly changes the motor. Send one from the web UI and watch:"
echo "        journalctl -u dt-bridge -f"

echo "[6] Serial resilience: replug test (manual)"
echo "    Unplug the ESP32 USB for 5s, plug back in. The bridge should log"
echo "    'Serial ... reconnecting' then resume telemetry WITHOUT a crash."
echo "    Verify:  journalctl -u dt-bridge -f"

echo "[7] Camera producer healthy?"
info=$(curl -s --max-time 5 "http://127.0.0.1:1984/api/streams?src=pi_cam" || true)
echo "$info" | grep -q '"producers"' && ok "go2rtc pi_cam has a producer" || bad "go2rtc pi_cam unhealthy: $info"

echo "[8] Camera reachable from the public internet?"
# Through the Cloudflare Tunnel, not the Tailscale Funnel -- the Funnel was the
# WebRTC-era path and no longer carries the stream. Fetching a real media
# segment, not just the manifest: a playlist can be served while the camera
# behind it produces nothing, and that is the failure worth catching.
CAM_HOST="${CAM_HOST:-cam.83838737rufhfhfucjfjdi8fi39.shop}"
systemctl is-active --quiet cloudflared && ok "cloudflared active" || bad "cloudflared not active"
VARIANT=$(curl -fsS --max-time 20 "https://$CAM_HOST/api/stream.m3u8?src=pi_cam" 2>/dev/null | grep -v '^#' | head -1 | tr -d '\r')
if [ -z "$VARIANT" ]; then
    bad "no HLS playlist from https://$CAM_HOST"
else
    SEG=$(curl -fsS --max-time 20 "https://$CAM_HOST/api/$VARIANT" 2>/dev/null | grep -v '^#' | head -1 | tr -d '\r')
    BYTES=$(curl -fsS --max-time 25 -o /dev/null -w '%{size_download}' "https://$CAM_HOST/api/hls/$SEG" 2>/dev/null)
    if [ "${BYTES:-0}" -gt 10000 ]; then
        ok "live video segment served publicly (${BYTES} bytes)"
    else
        bad "playlist served but the media segment was empty (${BYTES:-0} bytes)"
    fi
fi

echo
echo "==== $PASS passed, $FAIL failed ===="
echo "FINAL: reboot the Pi ('sudo reboot'), wait 2 min, re-run this script."
echo "All of [1][2][3][7] must PASS after a cold boot with no manual steps."
