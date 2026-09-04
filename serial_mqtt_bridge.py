import serial
import json
import paho.mqtt.client as mqtt
import time
import threading
import os
import signal
import sys
import hmac

# Fail fast if a second copy of this script is already running (it would fight
# for the serial port and silently break commands). Uses a pid lock file.
_LOCK = "/tmp/serial_mqtt_bridge.lock"
if os.path.exists(_LOCK):
    try:
        with open(_LOCK) as f:
            other = int(f.read().strip())
        os.kill(other, 0)          # raises if pid is dead
        print(f"Another bridge is already running (pid {other}). Exiting.")
        sys.exit(1)
    except (ProcessLookupError, ValueError):
        pass                       # stale lock, take over
with open(_LOCK, "w") as f:
    f.write(str(os.getpid()))

_stop = threading.Event()

def _shutdown(signum, frame):
    print(f"Signal {signum} received — shutting down.")
    _stop.set()

signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)

# Configuration - CHANGE THESE AS NEEDED
SERIAL_PORT = '/dev/ttyUSB0'  # Usually /dev/ttyUSB0 or /dev/ttyACM0 on Raspberry Pi
BAUD_RATE = 115200
TOPIC_TELEMETRY = 'digital_twin/motor/telemetry'
TOPIC_ENCODER = 'digital_twin/encoder/telemetry'
TOPIC_COMMAND = 'digital_twin/motor/command'
TOPIC_EVENT = 'digital_twin/motor/event'

# ── Breaker-OFF authorization ────────────────────────────────────────────────
# Opening the NB2 breaker cuts AC mains to the whole station. The dashboard must
# include a passphrase ({"breaker":"off","key":"..."}) that matches this secret,
# checked HERE (server side) before the command is forwarded to the ESP32.
# The secret lives only in ~/DT_LINE/.dt_secret (git-ignored) — never in the repo
# or the deployed dashboard. Any command with breaker:"off" and no/wrong key is
# dropped and logged.
_SECRET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.dt_secret')
try:
    with open(_SECRET_PATH) as _f:
        BREAKER_SECRET = _f.read().strip()
    print(f"Breaker-OFF passphrase loaded from {_SECRET_PATH}")
except OSError:
    BREAKER_SECRET = ''
    print(f"WARNING: no {_SECRET_PATH} — ALL remote 'breaker: off' commands will be REJECTED.")

# ── Streaming Plans ──────────────────────────────────────────────────────────
# Telemetry is published to every broker with enabled=True below, so the
# dashboard can be pointed at any of the three (select-broker-profile dropdown
# in app.js) without touching the Pi. Commands (dashboard -> ESP32) are
# forwarded to serial from whichever broker delivers them first.
BROKERS = {
    # Plan A: local Mosquitto on the Pi, exposed publicly via `cloudflared tunnel`.
    'cloudflare': {
        'enabled': True,
        'host': '127.0.0.1',
        'port': 1883,
        'tls': False,
        'username': None,
        'password': None,
        'min_interval': 0,   # unthrottled — it's our own broker
    },
    # Plan B: HiveMQ Cloud free-tier cluster. Fill in from the HiveMQ Cloud
    # console (Free Tier -> Cluster -> "Access Management" -> create credentials).
    'hivemq': {
        'enabled': True,
        'host': 'dcec0602f95f444bb3fe2bcdfd5efc38.s1.eu.hivemq.cloud',
        'port': 8883,
        'tls': True,
        'username': 'Lamps',
        'password': 'Aa448866',
        'min_interval': 5,   # throttle to 1 message / 5s per topic — stay under free-tier limits
    },
    # Plan C: EMQX Cloud Serverless free-tier deployment. Fill in from the
    # EMQX Cloud console (Deployment -> Overview -> connection details).
    'emqx': {
        'enabled': True,
        'host': 'xb6e165f.ala.asia-southeast1.emqxsl.com',
        'port': 8883,
        'tls': True,
        'username': 'Lamps',
        'password': 'Aa448866',
        'min_interval': 5,   # throttle to 1 message / 5s per topic — stay under free-tier limits
    },
}

# ── Serial connection (auto-reconnecting) ────────────────────────────────────
# A single lock guards every write to the port so the telemetry-reading main
# loop and the MQTT command callbacks (which run on paho's network threads)
# never touch the port at the same time.
ser = None
_ser_lock = threading.Lock()

def open_serial():
    """(Re)open the ESP32 serial port. Blocks until it succeeds."""
    global ser
    while not _stop.is_set():
        try:
            with _ser_lock:
                if ser is not None:
                    try:
                        ser.close()
                    except Exception:
                        pass
                ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
            print(f"Connected to ESP32 on {SERIAL_PORT}")
            return
        except Exception as e:
            print(f"Serial open failed ({e}); retrying in 3s. "
                  f"Check `dmesg | grep tty` / that no other process holds {SERIAL_PORT}.")
            _stop.wait(3)

def try_reopen_serial_once():
    """Single non-blocking reopen attempt (safe to call from paho threads)."""
    global ser
    try:
        with _ser_lock:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        print(f"Reconnected to ESP32 on {SERIAL_PORT}")
        return True
    except Exception as e:
        print(f"Serial reopen failed: {e}")
        return False

open_serial()

# MQTT Callback: subscribe here so the subscription is re-established on every
# (re)connect. Cloud brokers drop idle connections; without this, telemetry
# (publish-only) keeps working after a reconnect but commands silently stop.
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe(TOPIC_COMMAND, qos=1)
        print(f"[{userdata}] Connected (rc=0), subscribed to {TOPIC_COMMAND}")
    else:
        print(f"[{userdata}] Connect failed rc={rc}")

def on_disconnect(client, userdata, rc):
    print(f"[{userdata}] Disconnected (rc={rc}); paho will auto-reconnect.")

# -- Operator intent + heartbeat relay ---------------------------------------
# The firmware force-stops the motor (and reverts to MANUAL) if it sees no
# serial command for REMOTE_COMMAND_TIMEOUT_MS = 3000 -- measured, not assumed.
# Previously the dashboard heartbeat had to cross a cloud broker to satisfy
# that, so any broker hiccup >3s stopped the belt and silently disarmed REMOTE.
#
# Now the dashboard heartbeat expresses OPERATOR INTENT only, held here with a
# 6s budget, and this thread feeds the firmware deadman locally over USB every
# 1s. The safety property holds by construction: if this process dies the relay
# dies with it, and the firmware stops the motor within 3s. The relay can only
# ever repeat a command the operator actually sent.
OPERATOR_TIMEOUT_S = 6.0     # no dashboard traffic for this long -> stop
RELAY_INTERVAL_S   = 1.0     # must stay well under the firmware 3s deadman
MAX_RUN_S          = 600.0   # unattended run cap; expires intent regardless
RELAY_PAYLOAD      = json.dumps({"mode": "remote"})   # feeds deadman, starts nothing

_intent_lock     = threading.Lock()
_intent_cmd      = None      # last motor command the operator actually sent
_intent_deadline = 0.0       # wall-clock time when operator intent expires
_intent_started  = 0.0       # when this run began (for MAX_RUN_S)

def serial_write(command_json, label):
    """Write one command to the ESP32, reopening the port once on failure."""
    for attempt in range(2):
        try:
            with _ser_lock:
                if ser is None:
                    raise serial.SerialException("port not open")
                ser.write((command_json + "\n").encode("utf-8"))
                ser.flush()
            return True
        except Exception as e:
            print(f"Error forwarding command to serial ({label}, attempt {attempt+1}): {e}")
            try_reopen_serial_once()
    print(f"Command DROPPED ({label}) - serial unavailable after reconnect.")
    return False

def note_operator_command(obj, raw):
    """Update run-intent from a dashboard command. Any stop clears it at once."""
    global _intent_cmd, _intent_deadline, _intent_started
    if not isinstance(obj, dict):
        return
    now = time.time()

    # Anything meaning "not running" drops intent immediately.
    if obj.get("cmd") == "stop" or obj.get("mode") == "manual" or obj.get("speed") == 0:
        with _intent_lock:
            if _intent_cmd is not None:
                print("[RELAY] operator intent cleared (explicit stop/manual)")
            _intent_cmd = None
            _intent_deadline = 0.0
        return

    if obj.get("cmd") == "start":
        with _intent_lock:
            if _intent_cmd is None:
                _intent_started = now
                print(f"[RELAY] operator intent ARMED: {raw}")
            _intent_cmd = raw
            _intent_deadline = now + OPERATOR_TIMEOUT_S
    elif _intent_cmd is not None:
        # Heartbeat / mode refresh while running: extend the budget only.
        with _intent_lock:
            _intent_deadline = now + OPERATOR_TIMEOUT_S

def note_device_state(t):
    """Defence in depth: if the device leaves REMOTE while we hold operator
    intent, something stopped the machine that we did not ask for -- the
    firmware deadman fired, the operator chose MANUAL, or someone hit the
    joystick. All three must require a fresh, deliberate start command.
    With the mode-only relay this should be unreachable in normal operation,
    so if it ever fires it is telling us something we do not understand
    happened."""
    global _intent_cmd, _intent_deadline
    if not isinstance(t, dict) or t.get("control_mode") != "manual":
        return
    with _intent_lock:
        if _intent_cmd is None:
            return
        _intent_cmd = None
        _intent_deadline = 0.0
    print("[RELAY] device reverted to MANUAL while intent held -> "
          "intent LATCHED OFF, a fresh start command is required")
    publish_all(TOPIC_EVENT, json.dumps({
        "ts": time.time(), "kind": "run_intent_latched_off",
        "reason": "device_left_remote"}))

def relay_loop():
    """Feed the firmware deadman locally while operator intent is live."""
    global _intent_cmd, _intent_deadline
    while not _stop.is_set():
        _stop.wait(RELAY_INTERVAL_S)
        if _stop.is_set():
            break
        now = time.time()
        with _intent_lock:
            cmd = _intent_cmd
            deadline = _intent_deadline
            started = _intent_started
        if cmd is None:
            continue

        reason = None
        if now >= deadline:
            reason = "operator_timeout"
        elif now - started >= MAX_RUN_S:
            reason = "max_run_duration"
        if reason:
            with _intent_lock:
                _intent_cmd = None
                _intent_deadline = 0.0
            print(f"[RELAY] intent EXPIRED ({reason}) -> sending stop")
            serial_write(json.dumps({"cmd": "stop"}), "relay-stop")
            publish_all(TOPIC_EVENT, json.dumps({
                "ts": now, "kind": "run_intent_expired", "reason": reason}))
            continue

        # Mode-only heartbeat. This refreshes the firmware deadman
        # (lastRemoteCommandTime) but carries no speed, so it can never restart
        # a motor the firmware has safety-stopped. targetSpeedPercent is held in
        # firmware, so a normally running motor keeps running.
        serial_write(RELAY_PAYLOAD, "relay")

# MQTT Callback for receiving commands from the Dashboard (any broker)
def on_message(client, userdata, msg):
    global ser
    command_json = msg.payload.decode('utf-8', errors='ignore').strip()

    # Gate the one destructive command: breaker OFF must carry the passphrase.
    obj = None
    if command_json.startswith('{'):
        try:
            obj = json.loads(command_json)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and obj.get('breaker') == 'off':
            supplied = obj.get('key', '')
            if not BREAKER_SECRET or not hmac.compare_digest(str(supplied), BREAKER_SECRET):
                print(f"[AUTH] REJECTED breaker OFF from {userdata} (bad/missing key)")
                try:
                    for c in clients.values():
                        c.publish(TOPIC_EVENT, json.dumps({
                            'ts': time.time(), 'kind': 'breaker_off_rejected', 'broker': userdata}))
                except Exception:
                    pass
                return
            obj.pop('key', None)
            command_json = json.dumps(obj)   # forward without the passphrase
            print(f"[AUTH] breaker OFF authorized from {userdata}")

    print(f"[MQTT:{userdata} -> ESP32] Sending: {command_json}")
    note_operator_command(obj, command_json)
    serial_write(command_json, f"mqtt:{userdata}")

# Initialize one MQTT client per enabled broker plan
clients = {}
for name, cfg in BROKERS.items():
    if not cfg['enabled']:
        continue
    client = mqtt.Client(client_id=f"rpi_serial_bridge_{name}", userdata=name)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    if cfg['tls']:
        client.tls_set()
    if cfg['username']:
        client.username_pw_set(cfg['username'], cfg['password'])
    try:
        client.connect(cfg['host'], cfg['port'], keepalive=30)
        client.loop_start()
        clients[name] = client
        print(f"[{name}] Connecting to {cfg['host']}:{cfg['port']} …")
    except Exception as e:
        print(f"[{name}] Failed to connect: {e}")

if not clients:
    print("No brokers connected. Enable at least one plan in BROKERS and retry.")
    exit(1)

_last_publish = {}  # (broker_name, topic) -> last publish timestamp

def publish_all(topic, payload):
    now = time.time()
    for name, client in clients.items():
        min_interval = BROKERS[name].get('min_interval', 0)
        key = (name, topic)
        if min_interval and (now - _last_publish.get(key, 0)) < min_interval:
            continue
        try:
            client.publish(topic, payload)
            _last_publish[key] = now
        except Exception as e:
            print(f"[{name}] Publish failed: {e}")

# Main loop: Read from Serial and publish to all connected brokers
_relay_thread = threading.Thread(target=relay_loop, name="relay", daemon=True)
_relay_thread.start()
print(f"[RELAY] heartbeat relay active (operator budget {OPERATOR_TIMEOUT_S}s, relay every {RELAY_INTERVAL_S}s, run cap {MAX_RUN_S}s)")

print(f"Listening for telemetry... Active plans: {list(clients.keys())}. Press Ctrl+C to exit.")
_read_errors = 0
try:
    while not _stop.is_set():
        try:
            with _ser_lock:
                waiting = ser.in_waiting if ser is not None else 0
                line = ser.readline().decode('utf-8', errors='ignore').strip() if waiting > 0 else None
            _read_errors = 0
        except Exception as e:
            _read_errors += 1
            # Back off so a flaky port doesn't turn into a reopen storm.
            backoff = min(_read_errors, 5)
            if _read_errors <= 3 or _read_errors % 10 == 0:
                print(f"Serial read error #{_read_errors} ({e}); reopen in {backoff}s…")
            _stop.wait(backoff)
            open_serial()
            continue
        if line:

            # Look for the telemetry marker from LAST.ino
            # Current LAST.ino prints: [PUB] {"rpm":...}
            if "[PUB] {" in line:
                try:
                    # Extract just the JSON part
                    json_str = line[line.find('{'):]
                    json.loads(json_str)  # validate it's json

                    # Publish to every active broker plan
                    publish_all(TOPIC_TELEMETRY, json_str)

                    # Latch intent off if the device left REMOTE.
                    try:
                        note_device_state(json.loads(json_str))
                    except Exception:
                        pass

                    # Parse and print full formatted readings
                    try:
                        t_data = json.loads(json_str)
                        nb2 = t_data.get('nb2', {})
                        v = nb2.get('voltage', '--')
                        c = nb2.get('current', '--')
                        p = nb2.get('active_power', '--')
                        pf = nb2.get('power_factor', '--')
                        rs = "OK" if nb2.get('rs485_ok') else "FAIL"
                        print(f"[ESP32 -> MQTT] RPM: {t_data.get('rpm', 0):.1f} | AC: {v}V, {c}A, {p}W (PF: {pf}) | RS485: {rs}")
                    except Exception:
                        print(f"[ESP32 -> MQTT] Telemetry: {json_str}")
                except json.JSONDecodeError:
                    print(f"Invalid JSON from Serial: {line}")

            # Encoder telemetry marker
            elif "[ENC MQTT] {" in line:
                try:
                    json_str = line[line.find('{'):]
                    publish_all(TOPIC_ENCODER, json_str)
                    print(f"[ESP32 -> MQTT] Encoder: {json_str[:50]}...")
                except json.JSONDecodeError:
                    pass

            # Print debug lines so you can still see ESP32 console output
            elif line:
                print(f"[ESP32] {line}")
        _stop.wait(0.001)

except KeyboardInterrupt:
    print("\nExiting...")
finally:
    try:
        if ser is not None:
            ser.close()
    except Exception:
        pass
    for client in clients.values():
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
    try:
        if open(_LOCK).read().strip() == str(os.getpid()):
            os.remove(_LOCK)
    except Exception:
        pass
    print("Shutdown complete.")
