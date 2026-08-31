import serial
import json
import paho.mqtt.client as mqtt
import time

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

# Initialize Serial
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    print(f"Connected to ESP32 on {SERIAL_PORT}")
except Exception as e:
    print(f"Failed to connect to Serial: {e}")
    print("Please check the port (dmesg | grep tty) and try again.")
    exit(1)

# MQTT Callback for receiving commands from the Dashboard (any broker)
def on_message(client, userdata, msg):
    try:
        command_json = msg.payload.decode('utf-8')
        print(f"[MQTT:{userdata} -> ESP32] Sending: {command_json}")
        # Send the raw JSON string directly to the ESP32 via Serial
        ser.write((command_json + '\n').encode('utf-8'))
    except Exception as e:
        print(f"Error forwarding command to serial: {e}")

# Initialize one MQTT client per enabled broker plan
clients = {}
for name, cfg in BROKERS.items():
    if not cfg['enabled']:
        continue
    client = mqtt.Client(client_id=f"rpi_serial_bridge_{name}", userdata=name)
    client.on_message = on_message
    if cfg['tls']:
        client.tls_set()
    if cfg['username']:
        client.username_pw_set(cfg['username'], cfg['password'])
    try:
        client.connect(cfg['host'], cfg['port'], 60)
        client.subscribe(TOPIC_COMMAND)
        client.loop_start()
        clients[name] = client
        print(f"[{name}] Connected to {cfg['host']}:{cfg['port']}")
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
        if ser.in_waiting > 0:
            line = ser.readline().decode('utf-8', errors='ignore').strip()

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
                pass  # Optional: print(f"[ESP32] {line}")
        time.sleep(0.001)

except KeyboardInterrupt:
    print("\nExiting...")
finally:
    ser.close()
    for client in clients.values():
        client.loop_stop()
        client.disconnect()
