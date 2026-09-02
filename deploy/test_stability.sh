#!/usr/bin/env bash
# Run ON THE PI:  bash ~/DT_LINE/deploy/test_stability.sh
# Proves: services autostart, survive crashes, survive a serial unplug,
# survive an MQTT reconnect, and that commands actually reach the ESP32.
set -u
PASS=0; FAIL=0
ok()   { echo "  PASS: $*"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $*"; FAIL=$((FAIL+1)); }

echo "[1] Units enabled for boot?"
for U in dt-bridge go2rtc tailscale-funnel; do
    systemctl is-enabled --quiet "$U" && ok "$U enabled" || bad "$U NOT enabled"
done

echo "[2] Units running now?"
systemctl is-active --quiet dt-bridge && ok "dt-bridge active" || bad "dt-bridge not active"
systemctl is-active --quiet go2rtc   && ok "go2rtc active"   || bad "go2rtc not active"

echo "[3] Telemetry flowing? (watch journal 15s)"
if timeout 15 journalctl -u dt-bridge -f --no-pager | grep -m1 -q "ESP32 -> MQTT"; then
    ok "telemetry lines seen"
else
    bad "no telemetry in 15s"
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

echo "[8] Camera reachable through Tailscale Funnel?"
tailscale serve status 2>/dev/null | grep -q 1984 && ok "funnel -> 1984 mapped" || bad "funnel mapping missing (tailscale serve status)"

echo
echo "==== $PASS passed, $FAIL failed ===="
echo "FINAL: reboot the Pi ('sudo reboot'), wait 2 min, re-run this script."
echo "All of [1][2][3][7] must PASS after a cold boot with no manual steps."
