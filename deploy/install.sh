#!/usr/bin/env bash
# Run ON THE PI:  bash ~/DT_LINE/deploy/install.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="${SUDO_USER:-$USER}"

echo "== Digital Twin 24/7 installer =="

# 0) Make sure the login user owns the serial port group (one-time; needs re-login)
sudo usermod -aG dialout "$USER_NAME" || true

# 1) Stop any hand-started copies so they don't fight systemd for /dev/ttyUSB0
pkill -f serial_mqtt_bridge.py || true
pkill -x go2rtc || true
sleep 1

# 2) Install unit files
sudo cp "$HERE/dt-bridge.service" /etc/systemd/system/
sudo cp "$HERE/dt-camera.service" /etc/systemd/system/
sudo systemctl daemon-reload

# 3) Allow the watchdog to restart services without a password prompt
SUDOERS=/etc/sudoers.d/dt-watchdog
echo "$USER_NAME ALL=(root) NOPASSWD: /bin/systemctl restart dt-bridge, /bin/systemctl restart dt-camera" | sudo tee "$SUDOERS" >/dev/null
sudo chmod 440 "$SUDOERS"

# 4) Enable + start
sudo systemctl enable --now dt-bridge
sudo systemctl enable --now dt-camera

# 5) Install the cron watchdog
chmod +x "$HERE/dt-watchdog.sh"
CRON_LINE="*/2 * * * * $HERE/dt-watchdog.sh >> /home/$USER_NAME/dt-watchdog.log 2>&1"
( crontab -l 2>/dev/null | grep -v dt-watchdog.sh ; echo "$CRON_LINE" ) | crontab -

echo
echo "== Done. Status: =="
systemctl --no-pager status dt-bridge  | head -n 6
systemctl --no-pager status dt-camera  | head -n 6
echo
echo "Follow logs with:  journalctl -u dt-bridge -f"
