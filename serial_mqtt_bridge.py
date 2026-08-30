import serial
import json
import paho.mqtt.client as mqtt
import time
import threading

# Configuration - CHANGE THESE AS NEEDED
SERIAL_PORT = '/dev/ttyUSB0'  # Usually /dev/ttyUSB0 or /dev/ttyACM0 on Raspberry Pi
BAUD_RATE = 115200
MQTT_BROKER = '127.0.0.1'     # Local Mosquitto on the Pi
MQTT_PORT = 1883
TOPIC_TELEMETRY = 'digital_twin/motor/telemetry'
TOPIC_ENCODER = 'digital_twin/encoder/telemetry'
TOPIC_COMMAND = 'digital_twin/motor/command'

# Initialize Serial
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    print(f"Connected to ESP32 on {SERIAL_PORT}")
except Exception as e:
    print(f"Failed to connect to Serial: {e}")
    print("Please check the port (dmesg | grep tty) and try again.")
    exit(1)

# MQTT Callback for receiving commands from the Dashboard
def on_message(client, userdata, msg):
    try:
        command_json = msg.payload.decode('utf-8')
        print(f"[MQTT -> ESP32] Sending: {command_json}")
        # Send the raw JSON string directly to the ESP32 via Serial
        ser.write((command_json + '\n').encode('utf-8'))
    except Exception as e:
        print(f"Error forwarding command to serial: {e}")

# Initialize MQTT
mqtt_client = mqtt.Client(client_id="rpi_serial_bridge")
mqtt_client.on_message = on_message

try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
    mqtt_client.subscribe(TOPIC_COMMAND)
    mqtt_client.loop_start()
    print(f"Connected to local Mosquitto on {MQTT_BROKER}")
except Exception as e:
    print(f"Failed to connect to MQTT Broker: {e}")
    exit(1)

# Main loop: Read from Serial and publish to MQTT
print("Listening for telemetry... Press Ctrl+C to exit.")
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
                    json.loads(json_str) # validate it's json
                    
                    # Publish to MQTT
                    mqtt_client.publish(TOPIC_TELEMETRY, json_str)
                    
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
                    mqtt_client.publish(TOPIC_ENCODER, json_str)
                    print(f"[ESP32 -> MQTT] Encoder: {json_str[:50]}...")
                except json.JSONDecodeError:
                    pass
            
            # Print debug lines so you can still see ESP32 console output
            elif line:
                pass # Optional: print(f"[ESP32] {line}")
        time.sleep(0.001)

except KeyboardInterrupt:
    print("\nExiting...")
finally:
    ser.close()
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
