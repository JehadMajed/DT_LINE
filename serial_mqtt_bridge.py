import serial
import json
import paho.mqtt.client as mqtt
import time
import threading

# Configuration - CHANGE THESE AS NEEDED
SERIAL_PORT = '/dev/ttyUSB0'  # Usually /dev/ttyUSB0 or /dev/ttyACM0 on Raspberry Pi
BAUD_RATE = 115200
TOPIC_TELEMETRY = 'digital_twin/motor/telemetry'
TOPIC_ENCODER = 'digital_twin/encoder/telemetry'
TOPIC_COMMAND = 'digital_twin/motor/command'

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
    while True:
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
            time.sleep(3)

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

# MQTT Callback for receiving commands from the Dashboard (any broker)
def on_message(client, userdata, msg):
    global ser
    command_json = msg.payload.decode('utf-8', errors='ignore').strip()
    print(f"[MQTT:{userdata} -> ESP32] Sending: {command_json}")
    for attempt in range(2):
        try:
            with _ser_lock:
                if ser is None:
                    raise serial.SerialException("port not open")
                ser.write((command_json + '\n').encode('utf-8'))
                ser.flush()
            return
        except Exception as e:
            print(f"Error forwarding command to serial (attempt {attempt+1}): {e}")
            try_reopen_serial_once()
    print("Command DROPPED — serial unavailable after reconnect.")

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
print(f"Listening for telemetry... Active plans: {list(clients.keys())}. Press Ctrl+C to exit.")
try:
    while True:
        try:
            with _ser_lock:
                waiting = ser.in_waiting if ser is not None else 0
                line = ser.readline().decode('utf-8', errors='ignore').strip() if waiting > 0 else None
        except Exception as e:
            print(f"Serial read error ({e}); reconnecting…")
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
        time.sleep(0.001)

except KeyboardInterrupt:
    print("\nExiting...")
finally:
    ser.close()
    for client in clients.values():
        client.loop_stop()
        client.disconnect()
