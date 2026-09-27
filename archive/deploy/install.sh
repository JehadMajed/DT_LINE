#!/usr/bin/env bash
# Run ON THE PI:  bash ~/DT_LINE/archive/deploy/install.sh
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
    if systemctl cat "$SVC" >/dev/null 2>&1; then
        sudo systemctl enable "$SVC" >/dev/null 2>&1 || true
        echo "$SVC: enabled for boot"
    else
        echo "WARNING: $SVC not found — camera autostart is NOT guaranteed."
    fi
done

# 5) Passwordless restart for the watchdog
SUDOERS=/etc/sudoers.d/dt-watchdog
# Narrow on purpose: restarts only, and only these three units. cloudflared is
# here because the watchdog now owns the camera's public path too.
echo "$USER_NAME ALL=(root) NOPASSWD: /bin/systemctl restart dt-bridge, /bin/systemctl restart go2rtc, /bin/systemctl restart cloudflared" | sudo tee "$SUDOERS" >/dev/null
sudo chmod 440 "$SUDOERS"

# 6) Enable + (re)start the bridge
sudo systemctl enable dt-bridge
sudo systemctl restart dt-bridge

# 7) Watchdog, as a systemd timer rather than cron.
#    A user crontab is invisible to `systemctl`, does not survive a rebuild
#    unless someone remembers it, and sends its output to a file nothing
#    rotates. A timer shows up in `systemctl list-timers`, logs to the journal
#    with the rest of the system, and Persistent=true catches up a run missed
#    while the Pi was off.
chmod +x "$HERE/dt-watchdog.sh"
crontab -l 2>/dev/null | grep -q dt-watchdog.sh && {
    crontab -l 2>/dev/null | grep -v dt-watchdog.sh | crontab -
    echo "Removed the old cron watchdog line (replaced by the timer)"
}

sudo tee /etc/systemd/system/dt-watchdog.service >/dev/null <<EOF
[Unit]
Description=Digital Twin - watchdog sweep (bridge liveness, camera, tunnel)
After=dt-bridge.service

[Service]
Type=oneshot
User=$USER_NAME
ExecStart=$HERE/dt-watchdog.sh
StandardOutput=journal
StandardError=journal
EOF

sudo tee /etc/systemd/system/dt-watchdog.timer >/dev/null <<'EOF'
[Unit]
Description=Run the Digital Twin watchdog every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
AccuracySec=10s
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now dt-watchdog.timer >/dev/null 2>&1

echo
echo "== Status =="
sleep 2
systemctl is-active dt-bridge  && echo "dt-bridge: OK"  || echo "dt-bridge: FAILED"
systemctl is-active go2rtc     && echo "go2rtc: OK"     || echo "go2rtc: FAILED"
pgrep -cf serial_mqtt_bridge.py | xargs -I{} echo "bridge processes running: {} (must be 1)"
echo
echo "Follow logs:  journalctl -u dt-bridge -f"
echo "Now run:      bash $HERE/test_stability.sh"
