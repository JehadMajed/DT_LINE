#!/usr/bin/env bash
# Run ON THE PI:  bash ~/DT_LINE/deploy/install.sh
# Idempotent: safe to re-run.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

echo "== Digital Twin 24/7 installer =="

# 0) Serial port group (one-time; needs re-login to take effect)
sudo usermod -aG dialout "$USER_NAME" || true

# 1) Remove ANY older duplicate bridge service. Two services running the same
#    script fight over /dev/ttyUSB0 and silently break commands.
for OLD in dt-serial-bridge serial_mqtt_bridge serial-bridge dt_bridge; do
    if systemctl list-unit-files | grep -q "^${OLD}.service"; then
        echo "Removing duplicate service ${OLD}.service"
        sudo systemctl disable --now "${OLD}.service" || true
        sudo rm -f "/etc/systemd/system/${OLD}.service"
    fi
done

# 2) Kill any hand-started copies
pkill -f serial_mqtt_bridge.py || true
sleep 1

# 3) Install / refresh the bridge unit
sudo cp "$HERE/dt-bridge.service" /etc/systemd/system/
sudo systemctl daemon-reload

# 4) Camera: this repo does NOT manage the camera. The Pi already has
#    go2rtc.service + tailscale-funnel.service. Just make sure they're enabled.
for SVC in go2rtc.service tailscale-funnel.service; do
    if systemctl list-unit-files | grep -q "^${SVC}"; then
        sudo systemctl enable "$SVC" || true
    else
        echo "WARNING: $SVC not found — camera autostart is NOT guaranteed."
    fi
done

# 5) Passwordless restart for the watchdog
SUDOERS=/etc/sudoers.d/dt-watchdog
echo "$USER_NAME ALL=(root) NOPASSWD: /bin/systemctl restart dt-bridge, /bin/systemctl restart go2rtc, /usr/bin/tailscale serve reset, /usr/bin/tailscale funnel *" | sudo tee "$SUDOERS" >/dev/null
sudo chmod 440 "$SUDOERS"

# 6) Enable + (re)start the bridge
sudo systemctl enable dt-bridge
sudo systemctl restart dt-bridge

# 7) Cron watchdog
chmod +x "$HERE/dt-watchdog.sh"
CRON_LINE="*/2 * * * * $HERE/dt-watchdog.sh >> /home/$USER_NAME/dt-watchdog.log 2>&1"
( crontab -l 2>/dev/null | grep -v dt-watchdog.sh ; echo "$CRON_LINE" ) | crontab -

echo
echo "== Status =="
sleep 2
systemctl is-active dt-bridge  && echo "dt-bridge: OK"  || echo "dt-bridge: FAILED"
systemctl is-active go2rtc     && echo "go2rtc: OK"     || echo "go2rtc: FAILED"
pgrep -cf serial_mqtt_bridge.py | xargs -I{} echo "bridge processes running: {} (must be 1)"
echo
echo "Follow logs:  journalctl -u dt-bridge -f"
echo "Now run:      bash $HERE/test_stability.sh"
