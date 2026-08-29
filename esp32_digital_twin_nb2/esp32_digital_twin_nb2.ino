// ============================================================================
// DIGITAL TWIN — ESP32 FIRMWARE v2.0
// Full Integration: Motor + Encoder + Proximity + Temp + NB2 CHINT RS485
// ============================================================================
//
// HARDWARE MAP:
//   Motor BTS7960:    RPWM=18, LPWM=19, R_EN=3V3, L_EN=3V3
//   Encoder:          Phase A=34, Phase B=35
//   Proximity E18:    GPIO 4 (via optocoupler or 10K pullup to 3.3V)
//   DS18B20 Temp:     GPIO 16 (with 4.7K pullup to 3.3V)
//   RS485 (NB2):      TX=26, RX=27, DE/RE=23
//
// MQTT TOPICS:
//   Publish:  digital_twin/motor/telemetry   (JSON every ~500ms)
//   Subscribe: digital_twin/motor/command     (JSON commands)
//
// REQUIRED LIBRARIES (install via Arduino Library Manager):
//   - WiFi (built-in ESP32)
//   - PubSubClient by Nick O'Leary
//   - ModbusMaster by Doc Walker
//   - OneWire by Paul Stoffregen
//   - DallasTemperature by Miles Burton
// ============================================================================

#include <WiFi.h>
#include <PubSubClient.h>
#include <ModbusMaster.h>
#include <OneWire.h>
#include <DallasTemperature.h>

// ============================================================================
// PIN DEFINITIONS
// ============================================================================
// Motor Driver (BTS7960)
#define PIN_RPWM        18
#define PIN_LPWM        19

// Encoder
#define PIN_ENC_A       34
#define PIN_ENC_B       35

// Proximity Sensor (E18-D80NK / E3F-DS30C4)
#define PIN_PROXIMITY   4

// DS18B20 Temperature Sensor
#define PIN_DS18B20     16

// RS485 — MAX485 Module ↔ CHINT NB2 Breaker
#define PIN_RS485_TX    26      // ESP32 TX → MAX485 DI
#define PIN_RS485_RX    27      // ESP32 RX ← MAX485 RO
#define PIN_RS485_EN    23      // ESP32 → MAX485 DE+RE (tied together)

// ============================================================================
// NETWORK CONFIGURATION
// ============================================================================
const char* WIFI_SSID       = "H155-381_8C45";      // ← CHANGE THIS
const char* WIFI_PASSWORD   = "dqNdD3R72qb";   // ← CHANGE THIS
const char* MQTT_SERVER     = "192.168.1.27";        // Mosquitto broker IP
const int   MQTT_PORT       = 1883;
const char* MQTT_TOPIC_TELE = "digital_twin/motor/telemetry";
const char* MQTT_TOPIC_CMD  = "digital_twin/motor/command";
const char* MQTT_CLIENT_ID  = "esp32_dt_conveyor";

// ============================================================================
// MODBUS CONFIGURATION (NB2 CHINT Breaker)
// ============================================================================
#define NB2_SLAVE_ADDR      3       // Default CHINT NB2 address
#define NB2_BAUD_RATE       19200   // Default NB2 baud rate
// NB2 uses SERIAL_8E1: 8 data bits, Even parity, 1 stop bit

// ============================================================================
// MOTOR / ENCODER CONSTANTS
// ============================================================================
#define ENCODER_PPR         770     // Pulses per revolution (from encoder spec)
#define MOTOR_MAX_RPM       167     // Rated output RPM (after gearbox)
#define PWM_FREQUENCY       20000   // 20 kHz PWM for BTS7960
#define PWM_RESOLUTION      8       // 8-bit (0–255)
#define PWM_CHANNEL_R       0       // LEDC channel for RPWM
#define PWM_CHANNEL_L       1       // LEDC channel for LPWM

// ============================================================================
// TIMING INTERVALS (ms)
// ============================================================================
#define TELEMETRY_INTERVAL  500     // Publish telemetry every 500ms
#define NB2_POLL_INTERVAL   1000    // Poll NB2 Modbus every 1 second
#define TEMP_READ_INTERVAL  2000    // Read DS18B20 every 2 seconds
#define WIFI_RETRY_INTERVAL 5000    // WiFi reconnect attempt interval
#define MQTT_RETRY_INTERVAL 3000    // MQTT reconnect attempt interval

// ============================================================================
// GLOBAL OBJECTS
// ============================================================================
WiFiClient       wifiClient;
PubSubClient     mqttClient(wifiClient);
ModbusMaster     nb2;                           // Modbus master for NB2
HardwareSerial   rs485Serial(2);                // UART2 for RS485
OneWire          oneWire(PIN_DS18B20);
DallasTemperature tempSensor(&oneWire);

// ============================================================================
// ENCODER STATE (ISR-safe)
// ============================================================================
volatile long    encoderPulses = 0;
volatile bool    encoderDirection = true;        // true = forward
long             lastEncoderPulses = 0;
unsigned long    lastRpmCalcTime = 0;
float            currentRpm = 0.0;

// ============================================================================
// MOTOR STATE
// ============================================================================
bool    motorRunning = false;
String  motorDirection = "stop";    // "fwd", "rev", "stop"
int     speedPercent = 0;           // 0–100%
int     targetSpeedPercent = 0;

// ============================================================================
// PROXIMITY SENSOR STATE
// ============================================================================
bool    proximityActive = false;

// ============================================================================
// TEMPERATURE STATE
// ============================================================================
float   temperatureC = NAN;         // NAN = sensor not connected / no reading
unsigned long lastTempRead = 0;
bool    tempSensorPresent = false;

// ============================================================================
// NB2 BREAKER STATE
// ============================================================================
struct NB2Data {
    // Electrical parameters (single-phase: L1 only)
    float   voltage;            // V   (register 0x0048, UINT16, ×0.01)
    float   current;            // A   (register 0x0040–0x0041, INT32, ×0.001)
    float   activePower;        // W   (register 0x0051–0x0052, INT32, ×0.1)
    float   reactivePower;      // var (register 0x005F–0x0060, INT32, ×0.1)
    float   apparentPower;      // VA  (register 0x0067–0x0068, INT32, ×0.1)
    float   powerFactor;        // —   (register 0x006F, INT16, ×0.01)
    float   frequency;          // Hz  (register 0x004F, UINT16, ×0.01)
    int32_t energyWh;           // Wh  (register 0x0059–0x005A, INT32)
    int32_t reactiveEnergyVarh; // varh (register 0x005B–0x005C, INT32)
    float   residualCurrent;    // mA  (register 0x0050, UINT16)

    // Status
    bool    breakerOn;          // Bit7 of working state register 0x0020
    uint16_t faultFlags;        // Fault condition register 0x0021
    uint16_t alarmFlags;        // Alarm status register 0x0022
    float   internalTemp;       // °C  (register 0x0003, INT16)

    // Communication health
    bool    rs485Ok;            // true if last Modbus read succeeded
    uint8_t failCount;          // consecutive failures
    unsigned long lastReadTime; // millis() of last successful read
} nb2 = {0};

unsigned long lastNb2Poll = 0;

// ============================================================================
// TIMING
// ============================================================================
unsigned long lastTelemetryTime = 0;
unsigned long lastWifiAttempt = 0;
unsigned long lastMqttAttempt = 0;
unsigned long bootTime = 0;

// ============================================================================
// ENCODER ISR — count pulses on Phase A, determine direction from Phase B
// ============================================================================
void IRAM_ATTR encoderISR() {
    if (digitalRead(PIN_ENC_B)) {
        encoderPulses++;
    } else {
        encoderPulses--;
    }
}

// ============================================================================
// RS485 DIRECTION CONTROL CALLBACKS (for ModbusMaster library)
// ============================================================================
void rs485PreTransmission() {
    digitalWrite(PIN_RS485_EN, HIGH);   // Enable TX mode (DE=HIGH, RE=HIGH)
    delayMicroseconds(50);              // Small settling time
}

void rs485PostTransmission() {
    delayMicroseconds(50);
    digitalWrite(PIN_RS485_EN, LOW);    // Enable RX mode (DE=LOW, RE=LOW)
}

// ============================================================================
// SETUP
// ============================================================================
void setup() {
    Serial.begin(115200);
    Serial.println("\n[BOOT] Digital Twin ESP32 Firmware v2.0");
    Serial.println("[BOOT] NB2 CHINT Breaker RS485 Integration");

    bootTime = millis();

    // ── Pin Modes ──────────────────────────────────────────────────────────
    pinMode(PIN_PROXIMITY, INPUT);
    pinMode(PIN_RS485_EN, OUTPUT);
    digitalWrite(PIN_RS485_EN, LOW);    // Start in RX mode

    // ── Motor PWM Setup (LEDC) ────────────────────────────────────────────
    ledcSetup(PWM_CHANNEL_R, PWM_FREQUENCY, PWM_RESOLUTION);
    ledcSetup(PWM_CHANNEL_L, PWM_FREQUENCY, PWM_RESOLUTION);
    ledcAttachPin(PIN_RPWM, PWM_CHANNEL_R);
    ledcAttachPin(PIN_LPWM, PWM_CHANNEL_L);
    ledcWrite(PWM_CHANNEL_R, 0);
    ledcWrite(PWM_CHANNEL_L, 0);

    // ── Encoder Setup ─────────────────────────────────────────────────────
    pinMode(PIN_ENC_A, INPUT_PULLUP);
    pinMode(PIN_ENC_B, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(PIN_ENC_A), encoderISR, RISING);

    // ── DS18B20 Temperature Sensor ────────────────────────────────────────
    tempSensor.begin();
    tempSensorPresent = (tempSensor.getDeviceCount() > 0);
    if (tempSensorPresent) {
        tempSensor.setResolution(12);
        tempSensor.setWaitForConversion(false);  // Non-blocking reads
        tempSensor.requestTemperatures();
        Serial.println("[BOOT] DS18B20 sensor detected.");
    } else {
        Serial.println("[BOOT] DS18B20 not found — temperature will report null.");
    }

    // ── RS485 / Modbus Setup ──────────────────────────────────────────────
    rs485Serial.begin(NB2_BAUD_RATE, SERIAL_8E1, PIN_RS485_RX, PIN_RS485_TX);
    nb2.begin(NB2_SLAVE_ADDR, rs485Serial);
    nb2.preTransmission(rs485PreTransmission);
    nb2.postTransmission(rs485PostTransmission);
    Serial.printf("[BOOT] RS485 Modbus initialized — Slave: %d, Baud: %d, Config: 8E1\n",
                  NB2_SLAVE_ADDR, NB2_BAUD_RATE);

    // ── WiFi ──────────────────────────────────────────────────────────────
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.printf("[BOOT] Connecting to WiFi: %s", WIFI_SSID);
    unsigned long wifiStart = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - wifiStart < 10000) {
        delay(500);
        Serial.print(".");
    }
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n[BOOT] WiFi connected — IP: %s\n", WiFi.localIP().toString().c_str());
    } else {
        Serial.println("\n[BOOT] WiFi connection timed out — will retry in loop.");
    }

    // ── MQTT ──────────────────────────────────────────────────────────────
    mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setBufferSize(1024);  // Larger buffer for NB2 data JSON

    Serial.println("[BOOT] Setup complete. Entering main loop.\n");
}

// ============================================================================
// MAIN LOOP
// ============================================================================
void loop() {
    unsigned long now = millis();

    // ── WiFi Reconnect ────────────────────────────────────────────────────
    if (WiFi.status() != WL_CONNECTED) {
        if (now - lastWifiAttempt > WIFI_RETRY_INTERVAL) {
            lastWifiAttempt = now;
            Serial.println("[WIFI] Reconnecting...");
            WiFi.disconnect();
            WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
        }
    }

    // ── MQTT Reconnect ────────────────────────────────────────────────────
    if (WiFi.status() == WL_CONNECTED && !mqttClient.connected()) {
        if (now - lastMqttAttempt > MQTT_RETRY_INTERVAL) {
            lastMqttAttempt = now;
            reconnectMQTT();
        }
    }
    if (mqttClient.connected()) {
        mqttClient.loop();
    }

    // ── Read Proximity Sensor ─────────────────────────────────────────────
    proximityActive = (digitalRead(PIN_PROXIMITY) == LOW);  // NPN NO: LOW when object detected

    // ── Calculate RPM from Encoder ────────────────────────────────────────
    if (now - lastRpmCalcTime >= 200) {  // Calculate every 200ms
        noInterrupts();
        long pulses = encoderPulses;
        encoderPulses = 0;
        interrupts();

        float dt = (now - lastRpmCalcTime) / 1000.0;
        currentRpm = abs((pulses / (float)ENCODER_PPR) * 60.0 / dt);
        encoderDirection = (pulses >= 0);
        lastRpmCalcTime = now;
    }

    // ── Motor Speed Ramp ──────────────────────────────────────────────────
    updateMotorSpeed();

    // ── DS18B20 Temperature Read (non-blocking) ──────────────────────────
    if (tempSensorPresent && now - lastTempRead >= TEMP_READ_INTERVAL) {
        lastTempRead = now;
        float t = tempSensor.getTempCByIndex(0);
        if (t != DEVICE_DISCONNECTED_C && t > -50.0 && t < 125.0) {
            temperatureC = t;
        } else {
            temperatureC = NAN;
        }
        tempSensor.requestTemperatures();  // Start next conversion
    }

    // ── NB2 Modbus Poll ───────────────────────────────────────────────────
    if (now - lastNb2Poll >= NB2_POLL_INTERVAL) {
        lastNb2Poll = now;
        pollNB2Breaker();
    }

    // ── Publish Telemetry via MQTT ────────────────────────────────────────
    if (now - lastTelemetryTime >= TELEMETRY_INTERVAL) {
        lastTelemetryTime = now;
        publishTelemetry();
    }
}

// ============================================================================
// MOTOR CONTROL
// ============================================================================
void updateMotorSpeed() {
    // Soft ramp toward target
    if (speedPercent < targetSpeedPercent) {
        speedPercent = min(speedPercent + 1, targetSpeedPercent);
    } else if (speedPercent > targetSpeedPercent) {
        speedPercent = max(speedPercent - 2, targetSpeedPercent);  // Faster decel
    }

    int pwmValue = map(speedPercent, 0, 100, 0, 255);

    if (motorDirection == "fwd") {
        ledcWrite(PWM_CHANNEL_R, pwmValue);
        ledcWrite(PWM_CHANNEL_L, 0);
    } else if (motorDirection == "rev") {
        ledcWrite(PWM_CHANNEL_R, 0);
        ledcWrite(PWM_CHANNEL_L, pwmValue);
    } else {
        ledcWrite(PWM_CHANNEL_R, 0);
        ledcWrite(PWM_CHANNEL_L, 0);
    }
}

void stopMotor() {
    targetSpeedPercent = 0;
    speedPercent = 0;
    motorRunning = false;
    motorDirection = "stop";
    ledcWrite(PWM_CHANNEL_R, 0);
    ledcWrite(PWM_CHANNEL_L, 0);
}

void startMotor(int speed, String dir) {
    motorDirection = dir;
    targetSpeedPercent = constrain(speed, 0, 100);
    motorRunning = true;
}

// ============================================================================
// NB2 MODBUS POLLING
// Reads all relevant registers from the CHINT NB2 smart breaker
// ============================================================================
void pollNB2Breaker() {
    uint8_t result;
    bool anySuccess = false;

    // ── Read Internal Temperature (0x0003, 1 register) ────────────────────
    result = nb2.readHoldingRegisters(0x0003, 1);
    if (result == nb2.ku8MBSuccess) {
        int16_t rawTemp = (int16_t)nb2.getResponseBuffer(0);
        nb2Data.internalTemp = (float)rawTemp;
        anySuccess = true;
    }

    // ── Read Status Registers (0x0020–0x0022, 3 registers) ───────────────
    result = nb2.readHoldingRegisters(0x0020, 3);
    if (result == nb2.ku8MBSuccess) {
        uint16_t workState  = nb2.getResponseBuffer(0);
        nb2Data.faultFlags      = nb2.getResponseBuffer(1);
        nb2Data.alarmFlags      = nb2.getResponseBuffer(2);
        nb2Data.breakerOn       = (workState >> 7) & 0x01;  // Bit7: switch position
        anySuccess = true;
    }

    // ── Read L1 Current (0x0040–0x0041, 2 registers, INT32, ×0.001A) ─────
    result = nb2.readHoldingRegisters(0x0040, 2);
    if (result == nb2.ku8MBSuccess) {
        int32_t rawCurrent = ((int32_t)nb2.getResponseBuffer(0) << 16) |
                              (int32_t)nb2.getResponseBuffer(1);
        nb2Data.current = rawCurrent * 0.001;   // Convert to Amps
        anySuccess = true;
    }

    // ── Read L1 Voltage (0x0048, 1 register, UINT16, ×0.01V) ────────────
    result = nb2.readHoldingRegisters(0x0048, 1);
    if (result == nb2.ku8MBSuccess) {
        uint16_t rawVoltage = nb2.getResponseBuffer(0);
        nb2Data.voltage = rawVoltage * 0.01;    // Convert to Volts
        anySuccess = true;
    }

    // ── Read Frequency (0x004F, 1 register, UINT16, ×0.01Hz) ─────────────
    result = nb2.readHoldingRegisters(0x004F, 1);
    if (result == nb2.ku8MBSuccess) {
        uint16_t rawFreq = nb2.getResponseBuffer(0);
        nb2Data.frequency = rawFreq * 0.01;     // Convert to Hz
        anySuccess = true;
    }

    // ── Read Residual Current (0x0050, 1 register, UINT16, mA) ───────────
    result = nb2.readHoldingRegisters(0x0050, 1);
    if (result == nb2.ku8MBSuccess) {
        nb2Data.residualCurrent = (float)nb2.getResponseBuffer(0);  // Already in mA
        anySuccess = true;
    }

    // ── Read L1 Active Power (0x0051–0x0052, 2 registers, INT32, ×0.1W) ─
    result = nb2.readHoldingRegisters(0x0051, 2);
    if (result == nb2.ku8MBSuccess) {
        int32_t rawPower = ((int32_t)nb2.getResponseBuffer(0) << 16) |
                            (int32_t)nb2.getResponseBuffer(1);
        nb2Data.activePower = rawPower * 0.1;    // Convert to Watts
        anySuccess = true;
    }

    // ── Read Forward Total Active Energy (0x0059–0x005A, INT32, Wh) ──────
    result = nb2.readHoldingRegisters(0x0059, 2);
    if (result == nb2.ku8MBSuccess) {
        nb2Data.energyWh = ((int32_t)nb2.getResponseBuffer(0) << 16) |
                        (int32_t)nb2.getResponseBuffer(1);
        anySuccess = true;
    }

    // ── Read Forward Total Reactive Energy (0x005B–0x005C, INT32, varh) ──
    result = nb2.readHoldingRegisters(0x005B, 2);
    if (result == nb2.ku8MBSuccess) {
        nb2Data.reactiveEnergyVarh = ((int32_t)nb2.getResponseBuffer(0) << 16) |
                                  (int32_t)nb2.getResponseBuffer(1);
        anySuccess = true;
    }

    // ── Read L1 Reactive Power (0x005F–0x0060, INT32, ×0.1var) ───────────
    result = nb2.readHoldingRegisters(0x005F, 2);
    if (result == nb2.ku8MBSuccess) {
        int32_t rawReactive = ((int32_t)nb2.getResponseBuffer(0) << 16) |
                               (int32_t)nb2.getResponseBuffer(1);
        nb2Data.reactivePower = rawReactive * 0.1;
        anySuccess = true;
    }

    // ── Read L1 Apparent Power (0x0067–0x0068, INT32, ×0.1VA) ────────────
    result = nb2.readHoldingRegisters(0x0067, 2);
    if (result == nb2.ku8MBSuccess) {
        int32_t rawApparent = ((int32_t)nb2.getResponseBuffer(0) << 16) |
                               (int32_t)nb2.getResponseBuffer(1);
        nb2Data.apparentPower = rawApparent * 0.1;
        anySuccess = true;
    }

    // ── Read Phase A Power Factor (0x006F, 1 register, INT16, ×0.01) ─────
    result = nb2.readHoldingRegisters(0x006F, 1);
    if (result == nb2.ku8MBSuccess) {
        int16_t rawPF = (int16_t)nb2.getResponseBuffer(0);
        nb2Data.powerFactor = rawPF * 0.01;
        anySuccess = true;
    }

    // ── Update communication health ──────────────────────────────────────
    if (anySuccess) {
        nb2Data.rs485Ok = true;
        nb2Data.failCount = 0;
        nb2Data.lastReadTime = millis();
    } else {
        nb2Data.failCount++;
        if (nb2Data.failCount >= 5) {
            nb2Data.rs485Ok = false;
        }
        Serial.printf("[NB2] Modbus read failed (consecutive: %d)\n", nb2Data.failCount);
    }
}

// ============================================================================
// NB2 BREAKER REMOTE CONTROL
// Uses special command codes via function code 0x10 to address 0x0000
// ============================================================================
void nb2RemoteClose() {
    // Special command 0x02: Unlock remote control (required before close)
    uint16_t unlockData = 0x0002;
    nb2.setTransmitBuffer(0, unlockData);
    uint8_t result = nb2.writeMultipleRegisters(0x0000, 1);
    if (result == nb2.ku8MBSuccess) {
        Serial.println("[NB2] Remote control unlocked.");
    }
    delay(100);

    // Special command 0x06: Remote closing
    uint16_t closeData = 0x0006;
    nb2.setTransmitBuffer(0, closeData);
    result = nb2.writeMultipleRegisters(0x0000, 1);
    if (result == nb2.ku8MBSuccess) {
        Serial.println("[NB2] Remote CLOSE command sent.");
    } else {
        Serial.printf("[NB2] Remote CLOSE failed — error: 0x%02X\n", result);
    }
}

void nb2RemoteOpen() {
    // Special command 0x02: Unlock remote control (required before open)
    uint16_t unlockData = 0x0002;
    nb2.setTransmitBuffer(0, unlockData);
    uint8_t result = nb2.writeMultipleRegisters(0x0000, 1);
    if (result == nb2.ku8MBSuccess) {
        Serial.println("[NB2] Remote control unlocked.");
    }
    delay(100);

    // Special command 0x07: Remote opening
    uint16_t openData = 0x0007;
    nb2.setTransmitBuffer(0, openData);
    result = nb2.writeMultipleRegisters(0x0000, 1);
    if (result == nb2.ku8MBSuccess) {
        Serial.println("[NB2] Remote OPEN command sent.");
    } else {
        Serial.printf("[NB2] Remote OPEN failed — error: 0x%02X\n", result);
    }
}

// ============================================================================
// MQTT RECONNECT
// ============================================================================
void reconnectMQTT() {
    Serial.print("[MQTT] Connecting...");
    if (mqttClient.connect(MQTT_CLIENT_ID)) {
        Serial.println(" connected.");
        mqttClient.subscribe(MQTT_TOPIC_CMD);
        Serial.printf("[MQTT] Subscribed to: %s\n", MQTT_TOPIC_CMD);
    } else {
        Serial.printf(" failed (rc=%d). Will retry.\n", mqttClient.state());
    }
}

// ============================================================================
// MQTT COMMAND HANDLER
// Receives JSON commands from the Digital Twin dashboard
// ============================================================================
void mqttCallback(char* topic, byte* payload, unsigned int length) {
    // Build string from payload
    char json[256];
    int len = min((unsigned int)255, length);
    memcpy(json, payload, len);
    json[len] = '\0';

    Serial.printf("[MQTT CMD] %s → %s\n", topic, json);

    // Quick manual JSON parsing (no ArduinoJson dependency)
    String msg = String(json);

    // ── Motor Start Command ───────────────────────────────────────────────
    // {"cmd": "start", "speed": 75, "dir": "fwd"}
    if (msg.indexOf("\"start\"") >= 0) {
        int speed = extractInt(msg, "speed");
        String dir = extractString(msg, "dir");
        if (speed < 0) speed = 75;      // Default speed
        if (dir == "") dir = "fwd";      // Default direction
        startMotor(speed, dir);
        Serial.printf("[CMD] Motor START — speed: %d%%, dir: %s\n", speed, dir.c_str());
    }

    // ── Motor Stop Command ────────────────────────────────────────────────
    // {"cmd": "stop"}
    else if (msg.indexOf("\"stop\"") >= 0) {
        stopMotor();
        Serial.println("[CMD] Motor STOP.");
    }

    // ── Set Speed Command ─────────────────────────────────────────────────
    // {"cmd": "set_speed", "speed": 50}
    else if (msg.indexOf("\"set_speed\"") >= 0) {
        int speed = extractInt(msg, "speed");
        if (speed >= 0) {
            targetSpeedPercent = constrain(speed, 0, 100);
            Serial.printf("[CMD] Set speed: %d%%\n", targetSpeedPercent);
        }
    }

    // ── Emergency Stop Command ────────────────────────────────────────────
    // {"cmd": "estop"}
    else if (msg.indexOf("\"estop\"") >= 0) {
        stopMotor();
        Serial.println("[CMD] E-STOP activated!");
    }

    // ── Breaker ON Command ────────────────────────────────────────────────
    // {"breaker": "on"}
    else if (msg.indexOf("\"breaker\"") >= 0) {
        if (msg.indexOf("\"on\"") >= 0) {
            Serial.println("[CMD] NB2 Breaker → CLOSE (ON)");
            nb2RemoteClose();
        } else if (msg.indexOf("\"off\"") >= 0) {
            Serial.println("[CMD] NB2 Breaker → OPEN (OFF)");
            nb2RemoteOpen();
        }
    }
}

// ============================================================================
// PUBLISH TELEMETRY JSON
// ============================================================================
void publishTelemetry() {
    if (!mqttClient.connected()) return;

    unsigned long uptime = millis() - bootTime;

    // Build JSON string manually for efficiency (no ArduinoJson overhead)
    String json = "{";

    // Motor / Encoder data
    json += "\"rpm\":" + String(currentRpm, 1);
    json += ",\"speed_percent\":" + String(speedPercent);
    json += ",\"dir\":\"" + motorDirection + "\"";
    json += ",\"e18_active\":" + String(proximityActive ? "true" : "false");

    // Temperature (null if sensor not connected)
    if (!isnan(temperatureC)) {
        json += ",\"temp_c\":" + String(temperatureC, 1);
    } else {
        json += ",\"temp_c\":null";
    }

    json += ",\"uptime_ms\":" + String(uptime);
    json += ",\"ppr\":" + String(ENCODER_PPR);

    // ── NB2 Breaker Data (nested object) ──────────────────────────────────
    json += ",\"nb2\":{";
    json += "\"voltage\":" + String(nb2Data.voltage, 2);
    json += ",\"current\":" + String(nb2Data.current, 3);
    json += ",\"active_power\":" + String(nb2Data.activePower, 1);
    json += ",\"reactive_power\":" + String(nb2Data.reactivePower, 1);
    json += ",\"apparent_power\":" + String(nb2Data.apparentPower, 1);
    json += ",\"power_factor\":" + String(nb2Data.powerFactor, 2);
    json += ",\"frequency\":" + String(nb2Data.frequency, 2);
    json += ",\"energy_wh\":" + String(nb2Data.energyWh);
    json += ",\"reactive_energy_varh\":" + String(nb2Data.reactiveEnergyVarh);
    json += ",\"residual_current_ma\":" + String(nb2Data.residualCurrent, 1);
    json += ",\"breaker_on\":" + String(nb2Data.breakerOn ? "true" : "false");
    json += ",\"internal_temp\":" + String(nb2Data.internalTemp, 1);
    json += ",\"fault_flags\":" + String(nb2Data.faultFlags);
    json += ",\"alarm_flags\":" + String(nb2Data.alarmFlags);
    json += ",\"rs485_ok\":" + String(nb2Data.rs485Ok ? "true" : "false");
    json += "}";

    json += "}";

    // Publish
    if (mqttClient.publish(MQTT_TOPIC_TELE, json.c_str())) {
        // Success — no serial spam in normal operation
    } else {
        Serial.println("[MQTT] Publish failed — buffer too small?");
    }
}

// ============================================================================
// SIMPLE JSON HELPERS (no ArduinoJson dependency)
// ============================================================================
int extractInt(String json, String key) {
    String search = "\"" + key + "\"";
    int idx = json.indexOf(search);
    if (idx < 0) return -1;
    idx = json.indexOf(':', idx);
    if (idx < 0) return -1;
    idx++;
    while (idx < json.length() && json[idx] == ' ') idx++;
    String numStr = "";
    while (idx < json.length() && (isDigit(json[idx]) || json[idx] == '-')) {
        numStr += json[idx];
        idx++;
    }
    return numStr.length() > 0 ? numStr.toInt() : -1;
}

String extractString(String json, String key) {
    String search = "\"" + key + "\":\"";
    int idx = json.indexOf(search);
    if (idx < 0) return "";
    idx += search.length();
    int endIdx = json.indexOf('"', idx);
    if (endIdx < 0) return "";
    return json.substring(idx, endIdx);
}
