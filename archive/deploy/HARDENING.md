# Platform hardening — surviving the layer under the application

Companion to `RUNBOOK.md`. That document hardened the **application**: service
supervision, the process lock, serial reconnect, the recovery ladder, broker
failover, the audit trail. All of it worked.

This document hardens the **platform underneath it**, which had no protection at
all — and which failed on 2026-09-16 when mains power was pulled from the Pi
while the SD card was writing. The card's flash translation layer did not
survive. The controller still answers every read and reports its full 116.51 GB,
but every sector returns `FF`: 2001 samples spanning the whole card, zero read
errors, zero bytes of data. Nothing on that card is recoverable through the card
interface.

The application tier did everything right and still lost everything, because
nothing it protects lives anywhere except that one card.

---

## Tier 0 — what died with the card

Recreate these first. Everything else is prevention; this is the hole.

| Asset | Status | Action |
|---|---|---|
| `~/DT_LINE/archive/.dt_secret` | **Lost.** Gitignored, card-only. | Generate a new breaker passphrase, store it in a password manager, and update the dashboard. |
| `~/DT_LINE/archive/data/dt.db` | **Lost.** The entire incident record — events, commands, connections kept "indefinitely". | Accept the loss; Tier 2 stops it recurring. |
| `~/go2rtc.yaml` | **Recoverable** — `archive/deploy/go2rtc.yaml`. | Copy from repo. |
| `go2rtc.service` | **Not in repo.** Owned by a separate session. | Reconstruct and commit it (Tier 2, item 6). |
| `tailscale-funnel.service` | **Not in repo.** | Same. |
| Tailscale node identity | Lost with the card. | Re-authenticate; remove the stale node from the tailnet admin console. |
| `dt-bridge.service`, watchdog, crontab | **Recoverable** — `install.sh` recreates all three. | Run `install.sh`. |
| SSH host keys | Lost. | Clients will warn on next connect; clear the old entry. |

**Before rebuilding, decide the boot medium** — see item 1. Rebuilding onto
another SD card reinstates the exact failure that just cost you the system.

---

## Tier 1 — stop the same power cut from doing this again

### 1. Move the root filesystem off the SD card *(highest value of anything here)*

The Pi 5 boots from NVMe (via the M.2 HAT) or from USB. An SSD has real wear
levelling, a power-loss-tolerant FTL, and orders of magnitude more write
endurance. This does not mitigate the failure class — it removes it.

```bash
sudo rpi-eeprom-config --edit      # BOOT_ORDER=0xf416  (NVMe -> USB -> SD)
```

Migrate with `rpi-clone` or a fresh install plus the Tier 2 bootstrap. Keep a
prepared SD card as a cold spare, not as the live medium.

If the hardware budget does not allow it, items 2–4 make an SD card survivable.
They do not make it safe.

### 2. Clean shutdown on power loss (addresses the literal root cause)

A UPS HAT with battery or supercapacitor buys the 10–20 seconds needed to
unmount cleanly. Any of the common HATs expose capacity over I²C; the pattern
is the same:

```bash
# /etc/systemd/system/dt-powerfail.service — poll the UPS, shut down on low battery
# When capacity < 25% AND mains is absent:  sudo systemctl poweroff
```

Pair it with a labelled physical rule: **never pull the plug — press the Pi 5
power button**, which triggers a clean shutdown in firmware.

### 3. Hardware watchdog

systemd restarts a crashed service. Nothing currently restarts a **hung kernel**
— the failure mode where the Pi is powered, unreachable, and the belt is
unobserved.

```bash
# /boot/firmware/config.txt
dtparam=watchdog=on
```

```ini
# /etc/systemd/system.conf.d/watchdog.conf
[Manager]
RuntimeWatchdogSec=15
RebootWatchdogSec=2min
ShutdownWatchdogSec=10min
```

The bcm2835 watchdog caps at ~15 s; systemd pets it every `RuntimeWatchdogSec/2`.
A wedged kernel reboots itself in 15 seconds instead of waiting for a site visit.

### 4. Stop writing to the card

Current state: full journald to disk, default `relatime`, default 5 s ext4
commit, 1 Hz telemetry into SQLite, plus `dt-watchdog.log` appended every two
minutes forever. That is continuous write traffic on the most write-fragile
component in the system.

```bash
sudo apt install -y log2ram        # /var/log in RAM, flushed on clean shutdown
```

```ini
# /etc/systemd/journald.conf.d/dt.conf
[Journal]
Storage=volatile
RuntimeMaxUse=64M
```

```bash
# /etc/fstab — root line
defaults,noatime,commit=60,errors=remount-ro
```

`errors=remount-ro` matters independently: it converts silent corruption into a
loud, detectable read-only filesystem, which item 9 then alerts on.

Cap the watchdog log, which currently grows without bound:

```ini
# /etc/logrotate.d/dt-watchdog
/home/jehadroot/dt-watchdog.log { weekly rotate 4 compress missingok notifempty copytruncate }
```

### 5. Consider a read-only root

For a fixed-function always-on station this is the end state: `overlayroot`
puts the root filesystem behind a tmpfs overlay, so power loss cannot corrupt
it — the card is only written during deliberate maintenance windows. `dt.db`
and `.dt_secret` then live on a small writable partition or, better, on the
network.

Worth adopting only once Tier 2 exists, because updates become a two-step
procedure (`overlayroot-chroot`).

---

## Tier 2 — make a card death a 20-minute rebuild, not a total loss

### 6. Provisioning as code

`install.sh` covers the bridge and its watchdog. It does not cover: go2rtc,
the camera unit, the Tailscale Funnel, the mosquitto broker, Python
dependencies, `dialout` membership, or any of Tier 1. Those steps exist only as
institutional memory, which is why this rebuild is expensive.

**Done** — `archive/deploy/bootstrap.sh` takes a fresh Pi OS install to fully
working, idempotent, in one command. It pauses for exactly two things that need
a human: the Tailscale browser login and the breaker passphrase.

```
bootstrap.sh
  ├── apt deps        python3-serial, paho-mqtt, mosquitto{,-clients}, uhubctl, log2ram
  ├── go2rtc          binary fetch + go2rtc.service + go2rtc.yaml from repo
  ├── tailscale       install, `tailscale up`, funnel unit
  ├── tier-1 hardening  watchdog, journald, fstab, logrotate
  ├── secrets         prompt for .dt_secret, or pull from the backup host
  ├── install.sh      bridge + cron watchdog (existing, unchanged)
  └── test_stability.sh
```

The two previously-missing units — `go2rtc.service` and
`tailscale-funnel.service` — are now generated by `bootstrap.sh` rather than
committed as standalone files, because both embed the account name and its
home directory. The camera path is reproducible either way; this keeps a single
source of truth for the username.

Still outstanding: the first boot of a card flashed without Imager
customisation has no user and no SSH. Provision it before it ever boots by
writing `custom.toml` to the FAT32 boot partition — it is readable from any
machine, needs no Pi, and is consumed and deleted on first boot.

### 7. Off-card backup

Nothing currently leaves the Pi. A nightly push of the small, irreplaceable
things is sufficient — the bulk telemetry is expendable, the incident record is
not.

```bash
# /etc/systemd/system/dt-backup.service + .timer  (daily)
sqlite3 ~/DT_LINE/archive/data/dt.db ".backup /tmp/dt-backup.db"   # safe while in use
restic backup /tmp/dt-backup.db ~/DT_LINE/archive/.dt_secret /etc/systemd/system/dt-*.service ~/go2rtc.yaml
```

Target your PC over Tailscale, or any S3-compatible bucket. `.backup` is the
correct call here — copying a live SQLite file yields a torn database.

Verify restores quarterly. An unverified backup is a belief, not a backup.

### 8. Golden image after every known-good state

Once `test_stability.sh` passes clean after a cold boot, image the medium:

```bash
sudo dd if=/dev/mmcblk0 bs=4M | zstd -T0 > dt-line-$(date +%F).img.zst
```

Keep the last two. Restoring a verified image beats re-running provisioning
under pressure.

---

## Tier 3 — see the failure before it takes the system

### 9. Watch for the filesystem going read-only

The characteristic first symptom of a dying card is ext4 remounting read-only.
The bridge would keep running, keep publishing, and silently fail every write —
and nothing would report it.

Add to the health sampler in `dt_store.py` (schema at `dt_store.py:45`):

| Field | Source | Why |
|---|---|---|
| `fs_readonly` | `/proc/mounts` contains ` / ... ro,` | The card is failing, now. |
| `ext4_errors` | `dmesg \| grep -c "EXT4-fs error"` | Corruption in progress. |
| `throttled` | `vcgencmd get_throttled` | **Bit 0 = undervoltage.** Directly relevant: marginal supply is what makes power events destructive. Non-zero here predicts exactly this incident. |
| `card_life` | `/sys/block/mmcblk0/device/life_time` | eMMC/industrial cards report remaining wear. |
| `uptime_s` | `/proc/uptime` | Distinguishes a silent reboot from a hang. |

Publish them in the retained state document so the dashboard can show platform
health next to line health.

### 10. Alert outward

The system records incidents impeccably and notifies nobody. `recovery_gave_up`
(`serial_mqtt_bridge.py:446`) is the loudest event in the codebase and it lands
in SQLite and an MQTT topic that no consumer watches.

Route severity `error` events — plus `fs_readonly`, `throttled`, and
`recovery_gave_up` — to something that reaches a human: ntfy.sh, a Telegram bot,
or email. One HTTP POST from the bridge is enough.

---

## Tier 4 — operational polish

### 11. cron watchdog → systemd timer

`install.sh:53` installs a cron line. A timer gives you `systemctl list-timers`,
journal integration, `Persistent=true`, and no dependence on the user crontab
surviving a rebuild.

### 12. Resolve the camera ownership split

`dt-watchdog.sh:56` ships with `MANAGE_CAMERA=0` because a concurrent session
owned go2rtc, and two supervisors restarting one service caused a real outage.
The card death ended that session. Reclaim ownership deliberately: set
`MANAGE_CAMERA=1`, commit the camera units, and let one watchdog own the path —
or document the split as permanent. Leaving it ambiguous means the camera has no
supervisor at all.

### 13. SSH hardening

The Pi is reachable from the public internet through the Tailscale Funnel, and
the repo carries a plaintext credentials note (`archive/rpi5.txt`) with a weak
password. Before the rebuild is exposed again:

- Key-only authentication: `PasswordAuthentication no`, `PermitRootLogin no`
- Delete `archive/rpi5.txt` from the working tree **and** purge it from git
  history (`git filter-repo`), then rotate that password everywhere it was used
- Confirm the Funnel publishes only `127.0.0.1:1984`, never SSH
- Consider Tailscale SSH + ACLs instead of exposed OpenSSH

### 14. Restore-time acceptance test

Extend `test_stability.sh` with the platform tier, so a rebuild is provably
complete rather than apparently complete:

```
[9]  hardware watchdog armed   (RuntimeWatchdogSec, /dev/watchdog present)
[10] root fs is read-write and error-free
[11] no undervoltage since boot (vcgencmd get_throttled == 0x0)
[12] last successful off-card backup < 48 h old
```

---

## Suggested execution order

```
Tier 0  →  decide boot medium (item 1)  →  bootstrap.sh (item 6)
        →  Tier 1 (2,3,4)               →  backup (item 7)
        →  golden image (item 8)        →  Tier 3 visibility (9,10)
        →  Tier 4 polish
```

Items 1, 2, 3 and 7 address the incident that happened. Everything else
addresses the next one.
