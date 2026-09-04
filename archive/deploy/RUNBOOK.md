# Digital Twin — 24/7 stability runbook

Two things must survive reboots and run forever on the Pi:
1. `serial_mqtt_bridge.py` — ESP32 telemetry **and commands** over MQTT
2. go2rtc — the camera feed

## What was actually wrong

| Symptom | Root cause | Fix |
|---|---|---|
| After reboot nothing came back | Started by hand in a terminal. A reboot (or closing SSH) kills them. | `dt-bridge.service` with `Restart=always`, enabled for boot. Camera already had `go2rtc.service` + `tailscale-funnel.service` (both now confirmed `enabled`). |
| **Readings OK but commands never apply** (the real cause on this Pi) | A second, older service `dt-serial-bridge.service` was running the **same script**. Two processes both read `/dev/ttyUSB0` → "device reports readiness to read but returned no data (multiple access on port?)", garbled serial, and commands stolen/dropped. | `install.sh` now removes any duplicate bridge unit; the script takes a `/tmp/serial_mqtt_bridge.lock` and exits if another copy is alive; the watchdog kills extras. |
| Also fixed | Bridge subscribed to `digital_twin/motor/command` only once after connect — lost on any cloud-broker reconnect. | Subscription moved into `on_connect` (re-subscribes every reconnect), QoS 1. Plus serial auto-reconnect with backoff and clean SIGTERM shutdown so `systemctl restart` never leaves an orphan. |
| `device reports readiness to read but returned no data (multiple access on port?)` | A second copy of the script (old crashed session / double start) was holding `/dev/ttyUSB0`, or a USB brown-out. Any serial exception crashed the whole script. | `install.sh` kills stray copies first. Script now auto-reconnects the serial port instead of crashing, with a lock so reads and command-writes never collide. |
| Camera: `mse: streams: exec/pipe: EOF` | go2rtc's camera subprocess exited and go2rtc wasn't running as a managed service to recover cleanly. | `dt-camera.service` + hardened `rpicam-vid` exec line (`--inline --low-latency -t 0`) + watchdog. |

## Install (≈10 min, on the Pi)

```bash
cd ~/DT_LINE && git pull        # or copy the deploy/ folder + updated serial_mqtt_bridge.py over
bash ~/DT_LINE/archive/deploy/install.sh
```

Then merge `deploy/go2rtc.reference.yaml` into `~/go2rtc.yaml` and:

```bash
sudo systemctl restart dt-camera
```

Confirm the Tailscale Funnel still points at go2rtc (persists across reboots, just verify):

```bash
tailscale serve status
# expect  https://rpi5.tail05b01c.ts.net  ->  http://127.0.0.1:1984
```

## Test (≈15 min)

```bash
bash ~/DT_LINE/archive/deploy/test_stability.sh
sudo reboot
# wait 2 min, SSH back in
bash ~/DT_LINE/archive/deploy/test_stability.sh
```

Steps [1][2][3][7] must PASS after a cold boot **with zero manual commands**.

### The command test specifically
1. `journalctl -u dt-bridge -f` in one window.
2. On the dashboard, send a real command (set speed / start). Firmware note: the
   ESP32 boots in **MANUAL** mode and ignores motor commands until the UI arms
   **REMOTE** mode — do that first (same as the joystick path needs).
3. You should see `[MQTT:hivemq -> ESP32] Sending: {...}` and the motor react.
4. Leave it overnight, send another command next morning — it must still work
   (that is the reconnect bug this fixes).

## Day-to-day

```bash
systemctl status dt-bridge dt-camera
journalctl -u dt-bridge -f
journalctl -u dt-camera -f
tail -f ~/dt-watchdog.log
```

Restart manually if ever needed: `sudo systemctl restart dt-bridge`
