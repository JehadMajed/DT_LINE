#!/usr/bin/env bash
# Measure the firmware remote-command deadman: arm remote, start at the lowest
# usable speed, then send nothing and time how long until the firmware
# force-stops the motor. Always sends a stop at the end.
set -u
RUN="$1"
LOG="$HOME/deadman_run${RUN}.log"
T=digital_twin/motor/command

journalctl -u dt-bridge -f -o short-precise --since "now" > "$LOG" 2>&1 &
JPID=$!
sleep 2

echo "[run $RUN] arming + start speed=15"
mosquitto_pub -h 127.0.0.1 -t $T -m "{\"mode\":\"remote\",\"cmd\":\"start\",\"dir\":\"fwd\",\"speed\":15}"

# Wait up to 30s for the watchdog line to appear.
for i in $(seq 1 60); do
    grep -q "WATCHDOG" "$LOG" && break
    sleep 0.5
done

sleep 1
echo "[run $RUN] sending stop (always)"
mosquitto_pub -h 127.0.0.1 -t $T -m "{\"cmd\":\"stop\"}"
sleep 2
kill $JPID 2>/dev/null
wait $JPID 2>/dev/null

echo "--- run $RUN relevant lines ---"
grep -E "Sending:|WATCHDOG|\[CMD\]" "$LOG"
