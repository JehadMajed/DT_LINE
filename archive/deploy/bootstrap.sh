#!/usr/bin/env bash
# Fresh Raspberry Pi OS  ->  working DT_LINE station, in one command.
#
#   bash ~/DT_LINE/archive/deploy/bootstrap.sh
#
# Idempotent: safe to re-run. install.sh covers the bridge and its watchdog;
# this covers everything around them that used to live only in memory --
# dependencies, the local broker, go2rtc, the Funnel, and the platform
# hardening from HARDENING.md.
#
# Two steps need a human and will pause for you: `tailscale up` (browser
# login) and the breaker passphrase. Everything else runs unattended.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
GO2RTC_VERSION="${GO2RTC_VERSION:-latest}"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
warn() { printf '   WARNING: %s\n' "$*"; }

# ── 0. Preflight ─────────────────────────────────────────────────────────────
# The unit files, the watchdog cron line and the sudoers rule all hardcode
# /home/jehadroot. A different username installs cleanly and then fails at
# runtime in ways that are genuinely hard to trace, so refuse early.
say "Preflight"
if [ "$USER_NAME" != "jehadroot" ]; then
    echo "ERROR: expected user 'jehadroot', got '$USER_NAME'."
    echo "       dt-bridge.service and the watchdog hardcode /home/jehadroot."
    echo "       Reflash with the correct username rather than patching paths."
    exit 1
fi
[ "$(id -u)" -eq 0 ] && { echo "ERROR: run as jehadroot, not root (sudo is used per-step)."; exit 1; }
note "user: $USER_NAME"
note "repo: $REPO"
note "model: $(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"

# ── 1. Packages ──────────────────────────────────────────────────────────────
# pyserial and paho-mqtt come from apt, NOT pip: Raspberry Pi OS marks the
# system Python as externally managed (PEP 668), so a system-wide pip install
# is refused. Using apt keeps dt-bridge.service pointed at /usr/bin/python3
# with no venv indirection.
say "Packages"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    git curl jq \
    python3-serial python3-paho-mqtt \
    mosquitto mosquitto-clients \
    uhubctl logrotate sqlite3 || { echo "apt install failed"; exit 1; }
note "installed"

# Serial port access. Takes effect on next login, which is why install.sh
# also runs the bridge under systemd rather than relying on the shell session.
sudo usermod -aG dialout "$USER_NAME" || true

# ── 2. Repository ────────────────────────────────────────────────────────────
say "Repository"
if [ ! -d "$REPO/.git" ]; then
    warn "$REPO is not a git checkout — skipping pull"
else
    git -C "$REPO" pull --ff-only || warn "pull failed; continuing with the working tree"
    note "at $(git -C "$REPO" log -1 --format='%h %s')"
fi
mkdir -p "$REPO/archive/data"      # dt_store writes dt.db here; gitignored

# ── 3. Platform hardening (HARDENING.md, tier 1) ─────────────────────────────
say "Platform hardening"

# 3a. Hardware watchdog. dtparam=watchdog=on belongs in config.txt and may
#     already be set from the card image; add it if missing.
BOOTCFG=/boot/firmware/config.txt
[ -f "$BOOTCFG" ] || BOOTCFG=/boot/config.txt
if grep -q "^dtparam=watchdog=on" "$BOOTCFG" 2>/dev/null; then
    note "hardware watchdog already armed in $BOOTCFG"
else
    echo "dtparam=watchdog=on" | sudo tee -a "$BOOTCFG" >/dev/null
    note "armed hardware watchdog in $BOOTCFG (takes effect next boot)"
fi

# systemd pets /dev/watchdog every RuntimeWatchdogSec/2. The bcm2835 timer
# caps at ~15 s, so a wedged kernel reboots itself in 15 s instead of hanging
# until someone drives to the site.
sudo mkdir -p /etc/systemd/system.conf.d
sudo tee /etc/systemd/system.conf.d/dt-watchdog.conf >/dev/null <<'EOF'
[Manager]
RuntimeWatchdogSec=15
RebootWatchdogSec=2min
ShutdownWatchdogSec=10min
EOF
note "systemd watchdog configured"

# 3b. Journal to RAM. Default persistent journald writes continuously to the
#     boot medium; on SD that is the dominant source of wear.
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/dt.conf >/dev/null <<'EOF'
[Journal]
Storage=volatile
RuntimeMaxUse=64M
EOF
sudo systemctl restart systemd-journald || true
note "journald: volatile, capped at 64M"

# 3c. Root filesystem options. errors=remount-ro is the important one: it
#     turns silent corruption into a loud, detectable read-only mount that the
#     health sampler can alert on. noatime and commit=60 cut write traffic.
#
#     fstab is edited defensively -- a malformed line makes the Pi unbootable,
#     and this machine is not always physically reachable. The edit is applied
#     to a copy, field count is verified, and only then installed.
FSTAB_BAK="/etc/fstab.dt-bootstrap.$(date +%Y%m%d%H%M%S)"
if awk '$2=="/" && $4 ~ /errors=remount-ro/ {found=1} END{exit !found}' /etc/fstab; then
    note "root mount options already hardened"
else
    sudo cp /etc/fstab "$FSTAB_BAK"
    TMP=$(mktemp)
    # Add only what is missing. Pi OS already ships noatime on root, and a
    # commit= value that is already set is the operator's choice to keep.
    awk '$1 !~ /^#/ && $2 == "/" && NF >= 4 {
             n = split("noatime,commit=60,errors=remount-ro", want, ",")
             for (i = 1; i <= n; i++) {
                 key = want[i]; sub(/=.*/, "", key)
                 if ($4 !~ "(^|,)" key "(=|,|$)") $4 = $4 "," want[i]
             }
         }
         { print }' OFS='\t' /etc/fstab > "$TMP"
    # Same number of non-comment lines, and the root line still has 6 fields.
    OLD_N=$(grep -cv '^\s*#' /etc/fstab); NEW_N=$(grep -cv '^\s*#' "$TMP")
    ROOT_NF=$(awk '$1 !~ /^#/ && $2=="/" {print NF; exit}' "$TMP")
    if [ "$OLD_N" = "$NEW_N" ] && [ "${ROOT_NF:-0}" -ge 4 ]; then
        sudo cp "$TMP" /etc/fstab
        note "root mount hardened (backup: $FSTAB_BAK)"
    else
        warn "fstab rewrite looked wrong (lines $OLD_N->$NEW_N, root fields ${ROOT_NF:-?}) — left unchanged"
    fi
    rm -f "$TMP"
fi

# 3d. The watchdog log is appended to every 2 minutes, forever.
sudo tee /etc/logrotate.d/dt-watchdog >/dev/null <<EOF
/home/$USER_NAME/dt-watchdog.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
    copytruncate
}
EOF
note "watchdog log rotation configured"

# ── 4. Local broker ──────────────────────────────────────────────────────────
# The bridge connects to 127.0.0.1:1883 with no credentials (BROKERS['local']),
# and this is where triage happens -- it carries the encoder topic at full rate
# while the cloud brokers do not. Bound to loopback only; public exposure is
# the tunnel's job, not the broker's.
say "Local MQTT broker"
sudo tee /etc/mosquitto/conf.d/dt.conf >/dev/null <<'EOF'
listener 1883 127.0.0.1
allow_anonymous true
EOF
sudo systemctl enable mosquitto >/dev/null 2>&1 || true
sudo systemctl restart mosquitto
systemctl is-active --quiet mosquitto && note "mosquitto active on 127.0.0.1:1883" \
                                      || warn "mosquitto did not start"

# ── 5. Camera (go2rtc) ───────────────────────────────────────────────────────
# Reconstructed here because the previous install existed only on the dead
# card: the unit was never in the repo, which is precisely why this rebuild
# was expensive.
say "Camera (go2rtc)"
if [ "$GO2RTC_VERSION" = "latest" ]; then
    GO2RTC_VERSION=$(curl -fsSL https://api.github.com/repos/AlexxIT/go2rtc/releases/latest \
                     | jq -r .tag_name 2>/dev/null)
fi
if [ -z "${GO2RTC_VERSION:-}" ] || [ "$GO2RTC_VERSION" = "null" ]; then
    warn "could not resolve a go2rtc version — skipping (set GO2RTC_VERSION and re-run)"
else
    if command -v go2rtc >/dev/null; then
        note "go2rtc already installed: $(go2rtc -version 2>&1 | head -1)"
    else
        URL="https://github.com/AlexxIT/go2rtc/releases/download/${GO2RTC_VERSION}/go2rtc_linux_arm64"
        note "downloading go2rtc ${GO2RTC_VERSION}"
        if curl -fsSL "$URL" -o /tmp/go2rtc; then
            sudo install -m 755 /tmp/go2rtc /usr/local/bin/go2rtc
            rm -f /tmp/go2rtc
            note "installed $(go2rtc -version 2>&1 | head -1)"
        else
            warn "go2rtc download failed ($URL)"
        fi
    fi

    # Config: the repo copy is authoritative. Do not overwrite a local file
    # that has been tuned on the Pi -- back it up first.
    if [ -f "$HOME/go2rtc.yaml" ] && ! cmp -s "$HERE/go2rtc.yaml" "$HOME/go2rtc.yaml"; then
        cp "$HOME/go2rtc.yaml" "$HOME/go2rtc.yaml.bak.$(date +%Y%m%d%H%M%S)"
        note "existing go2rtc.yaml differed from the repo — backed up"
    fi
    cp "$HERE/go2rtc.yaml" "$HOME/go2rtc.yaml"

    sudo tee /etc/systemd/system/go2rtc.service >/dev/null <<EOF
[Unit]
Description=go2rtc camera streamer
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$USER_NAME
# rpicam-vid needs the video group to reach the CSI camera.
SupplementaryGroups=video
WorkingDirectory=/home/$USER_NAME
ExecStart=/usr/local/bin/go2rtc -config /home/$USER_NAME/go2rtc.yaml
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable go2rtc >/dev/null 2>&1 || true
    sudo systemctl restart go2rtc
    sleep 3
    systemctl is-active --quiet go2rtc && note "go2rtc active on :1984" \
                                       || warn "go2rtc did not start (journalctl -u go2rtc)"
fi

# ── 6. Tailscale + Funnel ────────────────────────────────────────────────────
say "Tailscale"
if ! command -v tailscale >/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh || warn "tailscale install failed"
fi
if command -v tailscale >/dev/null; then
    if ! tailscale status >/dev/null 2>&1; then
        echo
        echo "   ACTION NEEDED: authenticate this Pi to your tailnet."
        echo "   A URL will be printed -- open it, approve, then return here."
        echo "   Remove the old rpi5 node from the admin console first; it died with the card."
        echo
        sudo tailscale up || warn "tailscale up did not complete"
    else
        note "already authenticated: $(tailscale status --json 2>/dev/null | jq -r .Self.DNSName 2>/dev/null)"
    fi

    # The funnel mapping is what makes the camera reachable off-site. As a
    # oneshot unit it can fire before tailscaled is online, which is why the
    # watchdog re-applies it (dt-watchdog.sh check 4).
    sudo tee /etc/systemd/system/tailscale-funnel.service >/dev/null <<'EOF'
[Unit]
Description=Tailscale Funnel -> go2rtc (:1984)
After=tailscaled.service go2rtc.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/usr/bin/tailscale serve reset
ExecStart=/usr/bin/tailscale funnel --bg --https=443 http://127.0.0.1:1984

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable tailscale-funnel >/dev/null 2>&1 || true
    sudo systemctl restart tailscale-funnel || warn "funnel unit failed (is the tailnet authenticated?)"
    tailscale serve status 2>/dev/null | grep -q 1984 && note "funnel -> 127.0.0.1:1984" \
                                                      || warn "funnel mapping not visible yet"
fi

# ── 7. Breaker passphrase ────────────────────────────────────────────────────
# Gates {"breaker":"off"} server-side. Absent, the bridge REJECTS every remote
# breaker-off command -- which fails safe, but the dashboard button stops
# working. The old value died with the card; pick a new one.
say "Breaker passphrase"
SECRET="$REPO/archive/.dt_secret"
if [ -s "$SECRET" ]; then
    note ".dt_secret already present — leaving it alone"
else
    echo "   The dashboard prompts the operator for this; the Pi checks it here."
    read -rsp "   New breaker passphrase (blank to skip): " PASS; echo
    if [ -n "$PASS" ]; then
        printf '%s' "$PASS" > "$SECRET"
        chmod 600 "$SECRET"
        unset PASS
        note "written to $SECRET (chmod 600, gitignored)"
        note "store it in a password manager -- it is not backed up anywhere yet"
    else
        warn "skipped: remote breaker-off will be rejected until this exists"
    fi
fi

# ── 8. Bridge + watchdog ─────────────────────────────────────────────────────
say "Bridge and watchdog"
bash "$HERE/install.sh"

# ── 9. Summary ───────────────────────────────────────────────────────────────
say "Summary"
for U in dt-bridge go2rtc tailscale-funnel mosquitto; do
    printf '   %-20s enabled=%-8s active=%s\n' "$U" \
        "$(systemctl is-enabled "$U" 2>/dev/null || echo no)" \
        "$(systemctl is-active  "$U" 2>/dev/null || echo no)"
done
echo
note "Reboot is REQUIRED: dtparam=watchdog=on and the fstab options only"
note "apply on a cold boot -- and a cold boot is the real test anyway."
echo
echo "   sudo reboot"
echo "   # wait 2 min, then:"
echo "   bash $HERE/test_stability.sh"
