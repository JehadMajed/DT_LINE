// ============================================================================
// DIGITAL TWIN — ESP32 FIRMWARE v3.0
// Full Integration: Motor + Encoder + Proximity + Temp + NB2 CHINT RS485
// NB2 polling: raw UART batch-read (proven working — no ModbusMaster library)
// ============================================================================
//
// HARDWARE MAP:
//   Motor BTS7960:    RPWM=18, LPWM=19, R_EN=3V3, L_EN=3V3
//   Encoder:          Phase A=34, Phase B=35
//   Proximity E18:    GPIO 4 (via optocoupler or 10K pullup to 3.3V)
//   DS18B20 Temp:     GPIO 16 (with 4.7K pullup to 3.3V)
//   RS485 (NB2):      TX=26, RX=27, DE/RE=23
//
//   ── MANUAL CONTROL (parallel — auto-priority, no mode switch needed) ──
//   Joystick KY-023:  VRx=GPIO32 (ADC1), VRy=GPIO33 (ADC1), SW=GPIO25
//   Priority rule:    Joystick outside deadband → overrides MQTT motor cmds
//                     Joystick at center        → MQTT controls freely
//   Both run in parallel at all times. No conflict possible.
//   Wiring: KY-023 VCC→3.3V, GND→GND, VRx→GPIO32, SW→GPIO25
//
// MQTT TOPICS:
//   Publish:  digital_twin/motor/telemetry   (JSON every ~500ms)
//   Subscribe: digital_twin/motor/command     (JSON commands)
//
// REQUIRED LIBRARIES (install via Arduino Library Manager):
//   - WiFi (built-in ESP32)
//   - PubSubClient by Nick O'Leary
//   - OneWire by Paul Stoffregen
//   - DallasTemperature by Miles Burton
//   NOTE: ModbusMaster is NO LONGER needed — replaced by raw UART helpers
// ============================================================================

#include <WiFi.h>
#include <PubSubClient.h>
#include <HardwareSerial.h>
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

// RS485 — MAX485 Module <-> CHINT NB2 Breaker
// Pins match NB2_ESP32_Clean (verified working): RX=GPIO26, TX=GPIO27
#define PIN_RS485_TX    27      // ESP32 TX -> MAX485 DI
#define PIN_RS485_RX    26      // ESP32 RX <- MAX485 RO
#define PIN_RS485_EN    23      // ESP32 -> MAX485 DE+RE (tied together)

// ============================================================================
// MANUAL CTRL — Joystick + Mode Switch pins (ADC1 only — safe during WiFi)
// ============================================================================
#define PIN_JOY_X       32      // Analog X axis  — forward/reverse speed
#define PIN_JOY_Y       33      // Analog Y axis  — reserved / future use
#define PIN_JOY_BTN     25      // Joystick push-button — E-stop (always works)
// No mode switch pin — priority is automatic

// Joystick ADC tuning (12-bit, 0-4095)
#define JOY_CENTER      2048    // Resting center value
#define JOY_DEADBAND    250     // ±250 counts around center = stop zone

// ============================================================================
// NETWORK CONFIGURATION
// ============================================================================
const char* WIFI_SSID       = "H155-381_8C45";
const char* WIFI_PASSWORD   = "dqNdD3R72qb";
const char* MQTT_SERVER     = "192.168.1.27";
const int   MQTT_PORT       = 1883;
const char* MQTT_TOPIC_TELE = "digital_twin/motor/telemetry";
const char* MQTT_TOPIC_CMD  = "digital_twin/motor/command";
const char* MQTT_CLIENT_ID  = "esp32_dt_conveyor";

// ============================================================================
// MODBUS CONFIGURATION (NB2 CHINT Breaker — raw UART, no library)
// ============================================================================
#define NB2_SLAVE_ADDR  0x03    // Default CHINT NB2 Modbus address
#define NB2_BAUD_RATE   19200   // MUST MATCH BREAKER SCREEN (8E1 parity)

HardwareSerial rs485Serial(2); // UART2 for RS485

// ============================================================================
// MOTOR / ENCODER CONSTANTS
// ============================================================================
#define ENCODER_PPR         770     // Pulses per revolution (from encoder spec)
#define MOTOR_MAX_RPM       167     // Rated output RPM (after gearbox)
#define PWM_FREQUENCY       20000   // 20 kHz PWM for BTS7960
#define PWM_RESOLUTION      8       // 8-bit (0-255)
#define PWM_CHANNEL_R       0
#define PWM_CHANNEL_L       1

// ============================================================================
// TIMING INTERVALS (ms)
// ============================================================================
#define TELEMETRY_INTERVAL  500
#define NB2_POLL_INTERVAL   2000
#define TEMP_READ_INTERVAL  2000
#define WIFI_RETRY_INTERVAL 5000
#define MQTT_RETRY_INTERVAL 3000

// ============================================================================
// GLOBAL OBJECTS
// ============================================================================
WiFiClient        wifiClient;
PubSubClient      mqttClient(wifiClient);
OneWire           oneWire(PIN_DS18B20);
DallasTemperature tempSensor(&oneWire);

// ============================================================================
// ENCODER STATE (ISR-safe)
// ============================================================================
volatile long    encoderPulses = 0;
volatile bool    encoderDirection = true;
long             lastEncoderPulses = 0;
unsigned long    lastRpmCalcTime = 0;
float            currentRpm = 0.0;

// ============================================================================
// MOTOR STATE
// ============================================================================
bool    motorRunning = false;
String  motorDirection = "stop";

// ============================================================================
// MANUAL CTRL — Parallel priority state
// ============================================================================
// joystickHasPriority = true when joystick is actively moved outside deadband.
// When true: MQTT motor commands are silently ignored (no conflict).
// When false: MQTT controls the motor freely.
// Both systems are ALWAYS active — auto-handoff, no manual switch needed.
bool    joystickHasPriority = false;  // auto-set by handleManualControl()
bool    joyBtnPressed       = false;  // E-stop button state
int     speedPercent = 0;
int     targetSpeedPercent = 0;

// ============================================================================
// PROXIMITY SENSOR STATE
// ============================================================================
bool proximityActive = false;

// ============================================================================
// TEMPERATURE STATE (DS18B20)
// ============================================================================
float         temperatureC = NAN;
unsigned long lastTempRead = 0;
bool          tempSensorPresent = false;

// ============================================================================
// NB2 BREAKER STATE
// ============================================================================
struct NB2Data {
    // Electrical parameters (single-phase: L1)
    float   voltage;            // V   (batch byte offset 16-17,  x0.01)
    float   current;            // A   (batch byte offset 0-3,    x0.001)
    float   activePower;        // W   (batch byte offset 34-37,  x0.1)
    float   reactivePower;      // var (batch byte offset 62-65,  x0.1)
    float   apparentPower;      // VA  (batch byte offset 78-81,  x0.1)
    float   powerFactor;        // --  (batch byte offset 94-95,  x0.01)
    float   frequency;          // Hz  (batch byte offset 30-31,  x0.01)

    // Status
    bool     breakerOn;         // Bit7 of working state register 0x0020
    uint16_t faultFlags;        // Fault condition register 0x0021
    uint16_t alarmFlags;        // Alarm status register 0x0022
    int16_t  internalTemp;      // degC (register 0x0003)
    uint16_t modelCode;         // 4-bit model code from register 0x0010

    // Communication health
    bool          rs485Ok;
    uint8_t       failCount;
    unsigned long lastReadTime;
} nb2Data = {0};

unsigned long lastNb2Poll = 0;

// ============================================================================
// TIMING
// ============================================================================
unsigned long lastTelemetryTime = 0;
unsigned long lastWifiAttempt   = 0;
unsigned long lastMqttAttempt   = 0;
unsigned long bootTime          = 0;

// ============================================================================
// FORWARD DECLARATIONS
// ============================================================================
void   reconnectMQTT();
void   mqttCallback(char* topic, byte* payload, unsigned int length);
void   publishTelemetry();
void   updateMotorSpeed();
void   stopMotor();
void   startMotor(int speed, String dir);
void   pollNB2Breaker();
void   nb2RemoteClose();
void   nb2RemoteOpen();
void   printSingleLineDashboard();
int    extractInt(String json, String key);
String extractString(String json, String key);
void   handleManualControl();         // MANUAL CTRL — physical joystick handler

// ============================================================================
// MODBUS CRC16
// ============================================================================
uint16_t ModbusCRC16(const byte* data, int length) {
    uint16_t crc = 0xFFFF;
    for (int pos = 0; pos < length; pos++) {
        crc ^= (uint16_t)data[pos];
        for (int i = 8; i != 0; i--) {
            if (crc & 0x0001) { crc >>= 1; crc ^= 0xA001; }
            else              { crc >>= 1; }
        }
    }
    return crc;
}

// ============================================================================
// RAW MODBUS READ — FC03 Read Holding Registers
// Sends request, waits up to 250 ms, CRC-verifies reply.
// Returns true on success; sets dataStart = index of first data byte in rxBuf,
// dataLen = byte-count field from reply header.
// ============================================================================
bool modbusReadRegisters(uint16_t startAddress, uint16_t count,
                         byte* rxBuf, int rxBufSize,
                         int& dataStart, int& dataLen) {
    byte txBuf[8];
    txBuf[0] = NB2_SLAVE_ADDR;
    txBuf[1] = 0x03;               // FC03
    txBuf[2] = startAddress >> 8;
    txBuf[3] = startAddress & 0xFF;
    txBuf[4] = count >> 8;
    txBuf[5] = count & 0xFF;
    uint16_t crc = ModbusCRC16(txBuf, 6);
    txBuf[6] = crc & 0xFF;
    txBuf[7] = crc >> 8;

    // Flush stale RX bytes before transmitting
    while (rs485Serial.available()) rs485Serial.read();

    // Transmit
    digitalWrite(PIN_RS485_EN, HIGH);
    rs485Serial.write(txBuf, 8);
    rs485Serial.flush();
    delayMicroseconds(100);
    digitalWrite(PIN_RS485_EN, LOW);

    // Receive with 250 ms timeout
    unsigned long t0 = millis();
    int bytesReceived = 0;
    while (millis() - t0 < 250) {
        if (rs485Serial.available()) {
            rxBuf[bytesReceived++] = rs485Serial.read();
            if (bytesReceived >= rxBufSize) break;
        }
    }

    // Parse and CRC-verify reply
    dataStart = -1;
    dataLen   = 0;
    for (int i = 0; i < bytesReceived - 3; i++) {
        if (rxBuf[i] == NB2_SLAVE_ADDR && rxBuf[i + 1] == 0x03) {
            int len = rxBuf[i + 2];
            if (i + 3 + len + 2 <= bytesReceived) {
                uint16_t rxCRC   = ((uint16_t)rxBuf[i + 3 + len + 1] << 8) | rxBuf[i + 3 + len];
                uint16_t calcCRC = ModbusCRC16(&rxBuf[i], 3 + len);
                if (rxCRC == calcCRC) {
                    dataStart = i + 3;
                    dataLen   = len;
                    return true;
                }
            }
        }
    }
    return false;
}

// ============================================================================
// RAW MODBUS WRITE — FC16 Write Multiple Registers (single register)
// Used for NB2 remote control commands to address 0x0000
// ============================================================================
void modbusWriteCommand(uint16_t address, uint16_t value) {
    byte txBuf[11];
    txBuf[0] = NB2_SLAVE_ADDR;
    txBuf[1] = 0x10;                    // FC16
    txBuf[2] = address >> 8;
    txBuf[3] = address & 0xFF;
    txBuf[4] = 0x00; txBuf[5] = 0x01;  // 1 register
    txBuf[6] = 0x02;                    // 2 data bytes follow
    txBuf[7] = value >> 8;
    txBuf[8] = value & 0xFF;
    uint16_t crc = ModbusCRC16(txBuf, 9);
    txBuf[9]  = crc & 0xFF;
    txBuf[10] = crc >> 8;

    while (rs485Serial.available()) rs485Serial.read();
    digitalWrite(PIN_RS485_EN, HIGH);
    rs485Serial.write(txBuf, 11);
    rs485Serial.flush();
    delayMicroseconds(100);
    digitalWrite(PIN_RS485_EN, LOW);
    delay(200);
}

// ============================================================================
// ENCODER ISR — count pulses on Phase A, direction from Phase B
// ============================================================================
void IRAM_ATTR encoderISR() {
    if (digitalRead(PIN_ENC_B)) encoderPulses++;
    else                        encoderPulses--;
}

// ============================================================================
// SETUP
// ============================================================================
void setup() {
    Serial.begin(115200);
    delay(500);

    bootTime = millis();

    // ── Pin Modes ──────────────────────────────────────────────────────────
    pinMode(PIN_PROXIMITY, INPUT);
    pinMode(PIN_RS485_EN, OUTPUT);
    digitalWrite(PIN_RS485_EN, LOW);    // Start in RX mode

    // MANUAL CTRL — Setup joystick pins (no mode switch)
    pinMode(PIN_JOY_BTN, INPUT_PULLUP); // Joystick button: active LOW
    analogReadResolution(12);           // 12-bit ADC (0-4095)
    Serial.println("[BOOT] Joystick initialized — parallel priority mode active.");
    Serial.println("[BOOT] Both joystick and MQTT run simultaneously.");
    Serial.println("[BOOT] Joystick auto-overrides MQTT when moved outside deadband.");

    // ── Motor PWM Setup (LEDC) ────────────────────────────────────────────
    ledcAttach(PIN_RPWM, PWM_FREQUENCY, PWM_RESOLUTION);
    ledcAttach(PIN_LPWM, PWM_FREQUENCY, PWM_RESOLUTION);
    ledcWrite(PIN_RPWM, 0);
    ledcWrite(PIN_LPWM, 0);

    // ── Encoder Setup ─────────────────────────────────────────────────────
    pinMode(PIN_ENC_A, INPUT_PULLUP);
    pinMode(PIN_ENC_B, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(PIN_ENC_A), encoderISR, RISING);

    // ── DS18B20 Temperature Sensor ────────────────────────────────────────
    tempSensor.begin();
    tempSensorPresent = (tempSensor.getDeviceCount() > 0);
    if (tempSensorPresent) {
        tempSensor.setResolution(12);
        tempSensor.setWaitForConversion(false);
        tempSensor.requestTemperatures();
        Serial.println("[BOOT] DS18B20 sensor detected.");
    } else {
        Serial.println("[BOOT] DS18B20 not found -- temperature will report null.");
    }

    // ── RS485 Setup (raw UART, no library) ───────────────────────────────
    rs485Serial.begin(NB2_BAUD_RATE, SERIAL_8E1, PIN_RS485_RX, PIN_RS485_TX);
    modbusWriteCommand(0x0000, 0x0002);  // Unlock remote control on boot
    delay(300);
    Serial.printf("[BOOT] RS485 initialized -- Slave: 0x%02X, Baud: %d, 8E1\n",
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
        Serial.printf("\n[BOOT] WiFi connected -- IP: %s\n", WiFi.localIP().toString().c_str());
    } else {
        Serial.println("\n[BOOT] WiFi timed out -- will retry in loop.");
    }

    // ── MQTT ──────────────────────────────────────────────────────────────
    mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setBufferSize(1024);

    // ── Live dashboard anchor (must be LAST print in setup) ───────────────
    Serial.println("\n===========================================");
    Serial.println("  DIGITAL TWIN v3.0 -- LIVE DASHBOARD      ");
    Serial.println("===========================================");
    Serial.print("\n");     // Blank line the dashboard will overwrite
    Serial.print("\033[s"); // Save cursor position here (ANSI anchor)

    Serial.println("\n[BOOT] Setup complete. Entering main loop.");
}

// ============================================================================
// MAIN LOOP
// ============================================================================
void loop() {
    unsigned long now = millis();

    // ── MANUAL CTRL — Always first, no network dependency ─────────────────
    handleManualControl();

    // ── WiFi Reconnect (only needed for telemetry + wireless cmds) ────────
    if (WiFi.status() != WL_CONNECTED) {
        if (now - lastWifiAttempt > WIFI_RETRY_INTERVAL) {
            lastWifiAttempt = now;
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
    if (mqttClient.connected()) mqttClient.loop();

    // ── Proximity Sensor ──────────────────────────────────────────────────
    proximityActive = (digitalRead(PIN_PROXIMITY) == LOW);

    // ── RPM Calculation ───────────────────────────────────────────────────
    if (now - lastRpmCalcTime >= 200) {
        noInterrupts();
        long pulses = encoderPulses;
        encoderPulses = 0;
        interrupts();
        float dt    = (now - lastRpmCalcTime) / 1000.0;
        currentRpm  = abs((pulses / (float)ENCODER_PPR) * 60.0 / dt);
        encoderDirection = (pulses >= 0);
        lastRpmCalcTime  = now;
    }

    // ── Motor Speed Ramp ──────────────────────────────────────────────────
    updateMotorSpeed();

    // ── DS18B20 Temperature ───────────────────────────────────────────────
    if (tempSensorPresent && now - lastTempRead >= TEMP_READ_INTERVAL) {
        lastTempRead = now;
        float t = tempSensor.getTempCByIndex(0);
        temperatureC = (t != DEVICE_DISCONNECTED_C && t > -50.0 && t < 125.0) ? t : NAN;
        tempSensor.requestTemperatures();
    }

    // ── NB2 Modbus Poll + Live Dashboard ─────────────────────────────────
    if (now - lastNb2Poll >= NB2_POLL_INTERVAL) {
        lastNb2Poll = now;
        pollNB2Breaker();
        printSingleLineDashboard(); // Overwrites same line, no scroll
    }

    // ── Publish Telemetry via MQTT ────────────────────────────────────────
    if (now - lastTelemetryTime >= TELEMETRY_INTERVAL) {
        lastTelemetryTime = now;
        publishTelemetry();
    }
}

// ============================================================================
// MANUAL CTRL — Physical Joystick Handler (Parallel Priority System)
//
// Both joystick and MQTT are ALWAYS active simultaneously.
// Priority rule (auto, no switch):
//   • Joystick outside deadband → joystick controls, MQTT motor cmds ignored
//   • Joystick at center        → MQTT controls freely
//   • Joystick button pressed   → immediate E-stop (overrides everything)
// ============================================================================
void handleManualControl() {
    // ── Joystick E-stop button (active LOW) — always works regardless of mode ─
    bool btnState = (digitalRead(PIN_JOY_BTN) == LOW);
    if (btnState && !joyBtnPressed) {
        joyBtnPressed = true;
        stopMotor();
        joystickHasPriority = true;
        Serial.println("\n[MANUAL] E-STOP — motor halted by joystick button.");
    }
    if (!btnState && joyBtnPressed) {
        joyBtnPressed = false;
        // Button released: hand back to joystick axis (or MQTT if at center)
        joystickHasPriority = false;
        Serial.println("\n[MANUAL] E-STOP released — control resumed.");
    }
    if (joyBtnPressed) return; // Keep motor stopped while button held

    // ── Read X-axis ─────────────────────────────────────────────────────────
    int rawX      = analogRead(PIN_JOY_X);
    int deviation = rawX - JOY_CENTER;

    if (abs(deviation) <= JOY_DEADBAND) {
        // ── Joystick at center — release priority back to MQTT ───────────────
        if (joystickHasPriority) {
            joystickHasPriority = false;
            // Stop motor when joystick returns to center so MQTT starts clean
            stopMotor();
        }
        // MQTT is now free to command the motor

    } else {
        // ── Joystick moved — take priority from MQTT ─────────────────────────
        if (!joystickHasPriority) {
            joystickHasPriority = true;
            Serial.println("\n[MANUAL] Joystick active — overriding MQTT.");
        }

        if (deviation > JOY_DEADBAND) {
            // Forward
            int speed = map(deviation - JOY_DEADBAND,
                            0, JOY_CENTER - JOY_DEADBAND, 0, 100);
            startMotor(constrain(speed, 0, 100), "fwd");
        } else {
            // Reverse
            int speed = map(-(deviation + JOY_DEADBAND),
                            0, JOY_CENTER - JOY_DEADBAND, 0, 100);
            startMotor(constrain(speed, 0, 100), "rev");
        }
    }
}

// ============================================================================
// MOTOR CONTROL
// ============================================================================
void updateMotorSpeed() {
    if      (speedPercent < targetSpeedPercent) speedPercent = min(speedPercent + 1, targetSpeedPercent);
    else if (speedPercent > targetSpeedPercent) speedPercent = max(speedPercent - 2, targetSpeedPercent);

    int pwmValue = map(speedPercent, 0, 100, 0, 255);
    if      (motorDirection == "fwd") { ledcWrite(PIN_RPWM, pwmValue); ledcWrite(PIN_LPWM, 0); }
    else if (motorDirection == "rev") { ledcWrite(PIN_RPWM, 0); ledcWrite(PIN_LPWM, pwmValue); }
    else                              { ledcWrite(PIN_RPWM, 0); ledcWrite(PIN_LPWM, 0); }
}

void stopMotor() {
    targetSpeedPercent = 0; speedPercent = 0;
    motorRunning = false;   motorDirection = "stop";
    ledcWrite(PIN_RPWM, 0); ledcWrite(PIN_LPWM, 0);
}

void startMotor(int speed, String dir) {
    motorDirection     = dir;
    targetSpeedPercent = constrain(speed, 0, 100);
    motorRunning       = true;
}

// ============================================================================
// NB2 BREAKER POLLING
// Single 48-register batch read (0x0040-0x006F) for all electrical metrics.
// Byte offsets inside the 96-byte payload:
//   [0..3]   Current     INT32   x0.001 A
//   [16..17] Voltage     UINT16  x0.01  V
//   [30..31] Frequency   UINT16  x0.01  Hz
//   [34..37] Active Pwr  INT32   x0.1   W
//   [62..65] Reactive    INT32   x0.1   var
//   [78..81] Apparent    INT32   x0.1   VA
//   [94..95] Power Fac   INT16   x0.01
// ============================================================================
void pollNB2Breaker() {
    byte rxBuf[128];
    int  dataStart, dataLen;
    bool anySuccess = false;

    // 1. Internal Temperature (0x0003, 1 reg)
    if (modbusReadRegisters(0x0003, 1, rxBuf, sizeof(rxBuf), dataStart, dataLen)) {
        if (dataLen >= 2)
            nb2Data.internalTemp = (int16_t)((rxBuf[dataStart] << 8) | rxBuf[dataStart + 1]);
        anySuccess = true;
    }
    delay(50);

    // 2. Hardware Model Code (0x0010, 1 reg)
    if (modbusReadRegisters(0x0010, 1, rxBuf, sizeof(rxBuf), dataStart, dataLen)) {
        if (dataLen >= 2) {
            uint16_t devStatus = (rxBuf[dataStart] << 8) | rxBuf[dataStart + 1];
            nb2Data.modelCode  = (devStatus >> 4) & 0x0F;
        }
        anySuccess = true;
    }
    delay(50);

    // 3. Status Registers (0x0020-0x0022, 3 regs)
    if (modbusReadRegisters(0x0020, 3, rxBuf, sizeof(rxBuf), dataStart, dataLen)) {
        if (dataLen >= 6) {
            uint16_t workState = (rxBuf[dataStart]     << 8) | rxBuf[dataStart + 1];
            nb2Data.faultFlags = (rxBuf[dataStart + 2] << 8) | rxBuf[dataStart + 3];
            nb2Data.alarmFlags = (rxBuf[dataStart + 4] << 8) | rxBuf[dataStart + 5];
            nb2Data.breakerOn  = (workState & (1 << 7)) != 0;
        }
        anySuccess = true;
    }
    delay(50);

    // 4. Electrical Metrics — 48-register batch (0x0040-0x006F)
    if (modbusReadRegisters(0x0040, 48, rxBuf, sizeof(rxBuf), dataStart, dataLen)) {
        if (dataLen >= 96) {
            // Current
            int32_t rawCurrent = (int32_t)(
                ((uint32_t)rxBuf[dataStart +  0] << 24) |
                ((uint32_t)rxBuf[dataStart +  1] << 16) |
                ((uint32_t)rxBuf[dataStart +  2] <<  8) |
                ((uint32_t)rxBuf[dataStart +  3]      ));
            nb2Data.current = rawCurrent * 0.001f;

            // Voltage (reg 0x0048 = batch idx 8 = byte offset 16)
            uint16_t rawVoltage = ((uint16_t)rxBuf[dataStart + 16] << 8) | rxBuf[dataStart + 17];
            nb2Data.voltage = rawVoltage * 0.01f;

            // Frequency (reg 0x004F = batch idx 15 = byte offset 30)
            uint16_t rawFreq = ((uint16_t)rxBuf[dataStart + 30] << 8) | rxBuf[dataStart + 31];
            nb2Data.frequency = rawFreq * 0.01f;

            // Active Power (reg 0x0051 = batch idx 17 = byte offset 34)
            int32_t rawActive = (int32_t)(
                ((uint32_t)rxBuf[dataStart + 34] << 24) |
                ((uint32_t)rxBuf[dataStart + 35] << 16) |
                ((uint32_t)rxBuf[dataStart + 36] <<  8) |
                ((uint32_t)rxBuf[dataStart + 37]      ));
            nb2Data.activePower = rawActive * 0.1f;

            // Reactive Power (reg 0x005F = batch idx 31 = byte offset 62)
            int32_t rawReactive = (int32_t)(
                ((uint32_t)rxBuf[dataStart + 62] << 24) |
                ((uint32_t)rxBuf[dataStart + 63] << 16) |
                ((uint32_t)rxBuf[dataStart + 64] <<  8) |
                ((uint32_t)rxBuf[dataStart + 65]      ));
            nb2Data.reactivePower = rawReactive * 0.1f;

            // Apparent Power (reg 0x0067 = batch idx 39 = byte offset 78)
            int32_t rawApparent = (int32_t)(
                ((uint32_t)rxBuf[dataStart + 78] << 24) |
                ((uint32_t)rxBuf[dataStart + 79] << 16) |
                ((uint32_t)rxBuf[dataStart + 80] <<  8) |
                ((uint32_t)rxBuf[dataStart + 81]      ));
            nb2Data.apparentPower = rawApparent * 0.1f;

            // Power Factor (reg 0x006F = batch idx 47 = byte offset 94)
            int16_t rawPF = (int16_t)(((uint16_t)rxBuf[dataStart + 94] << 8) | rxBuf[dataStart + 95]);
            nb2Data.powerFactor = rawPF * 0.01f;

            anySuccess = true;
        }
    }
    delay(50);

    // Update communication health
    if (anySuccess) {
        nb2Data.rs485Ok      = true;
        nb2Data.failCount    = 0;
        nb2Data.lastReadTime = millis();
    } else {
        nb2Data.failCount++;
        if (nb2Data.failCount >= 5) nb2Data.rs485Ok = false;
        Serial.printf("\n[NB2] Poll failed (consecutive: %d)\n", nb2Data.failCount);
    }
}

// ============================================================================
// NB2 BREAKER REMOTE CONTROL
// ============================================================================
void nb2RemoteClose() {
    modbusWriteCommand(0x0000, 0x0002); // Unlock
    delay(100);
    modbusWriteCommand(0x0000, 0x0006); // Close
    Serial.println("\n[NB2] Remote CLOSE sent.");
}

void nb2RemoteOpen() {
    modbusWriteCommand(0x0000, 0x0002); // Unlock
    delay(100);
    modbusWriteCommand(0x0000, 0x0007); // Open
    Serial.println("\n[NB2] Remote OPEN sent.");
}

// ============================================================================
// SERIAL DASHBOARD — rewrites the same line in Arduino Serial Monitor
// \r = return to start of line, \033[2K = erase entire line
// ============================================================================
void printSingleLineDashboard() {
    Serial.printf(
        "\r\033[2K[NB2] %-3s | V:%6.2fV | I:%6.3fA | P:%6.1fW | PF:%5.2f | "
        "F:%5.2fHz | T:%2ddegC | RS485:%s",
        nb2Data.breakerOn ? "ON" : "OFF",
        nb2Data.voltage,
        nb2Data.current,
        nb2Data.activePower,
        nb2Data.powerFactor,
        nb2Data.frequency,
        nb2Data.internalTemp,
        nb2Data.rs485Ok ? "OK " : "ERR"
    );
}

// ============================================================================
// MQTT RECONNECT
// ============================================================================
void reconnectMQTT() {
    Serial.print("\n[MQTT] Connecting...");
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
// ============================================================================
void mqttCallback(char* topic, byte* payload, unsigned int length) {
    char json[256];
    int len = min((unsigned int)255, length);
    memcpy(json, payload, len);
    json[len] = '\0';
    String msg = String(json);

    // MANUAL CTRL — Joystick has auto-priority: block MOTOR commands only.
    // Breaker commands always pass through regardless of joystick state.
    bool isMotorCmd  = (msg.indexOf("\"speed\"") >= 0 ||
                        msg.indexOf("\"stop\"")  >= 0 ||
                        msg.indexOf("\"set_speed\"") >= 0 ||
                        msg.indexOf("\"estop\"") >= 0);
    if (isMotorCmd && joystickHasPriority) {
        Serial.println("[MQTT] Motor command ignored — joystick has priority.");
        return;
    }

    // Motor start: {"cmd":"start","speed":75,"dir":"fwd"}
    if (msg.indexOf("\"speed\"") >= 0 && msg.indexOf("\"dir\"") >= 0 && msg.indexOf("\"stop\"") == -1) {
        int speed  = extractInt(msg, "speed");
        String dir = extractString(msg, "dir");
        if (speed < 0) speed = 75;
        if (dir == "") dir   = "fwd";
        startMotor(speed, dir);
        Serial.printf("\n[CMD] Motor START -- speed: %d%%, dir: %s\n", speed, dir.c_str());
    }
    // Motor stop: {"cmd":"stop"}
    else if (msg.indexOf("\"stop\"") >= 0) {
        stopMotor();
        Serial.println("\n[CMD] Motor STOP.");
    }
    // Set speed: {"cmd":"set_speed","speed":50}
    else if (msg.indexOf("\"set_speed\"") >= 0) {
        int speed = extractInt(msg, "speed");
        if (speed >= 0) {
            targetSpeedPercent = constrain(speed, 0, 100);
            Serial.printf("\n[CMD] Set speed: %d%%\n", targetSpeedPercent);
        }
    }
    // E-stop: {"cmd":"estop"}
    else if (msg.indexOf("\"estop\"") >= 0) {
        stopMotor();
        Serial.println("\n[CMD] E-STOP activated!");
    }
    // Breaker: {"breaker":"on"} or {"breaker":"off"}
    else if (msg.indexOf("\"breaker\"") >= 0) {
        if      (msg.indexOf("\"on\"")  >= 0) nb2RemoteClose();
        else if (msg.indexOf("\"off\"") >= 0) nb2RemoteOpen();
    }
}

// ============================================================================
// PUBLISH TELEMETRY JSON
// ============================================================================
void publishTelemetry() {
    if (!mqttClient.connected()) return;

    unsigned long uptime = millis() - bootTime;
    String json = "{";

    json += "\"rpm\":"            + String(currentRpm, 1);
    json += ",\"speed_percent\":" + String(speedPercent);
    json += ",\"dir\":\""         + motorDirection + "\"";
    json += ",\"e18_active\":"    + String(proximityActive ? "true" : "false");
    json += isnan(temperatureC)
            ? ",\"temp_c\":null"
            : ",\"temp_c\":"  + String(temperatureC, 1);
    json += ",\"uptime_ms\":"     + String(uptime);
    json += ",\"ppr\":"           + String(ENCODER_PPR);

    json += ",\"nb2\":{";
    json += "\"voltage\":"              + String(nb2Data.voltage, 2);
    json += ",\"current\":"             + String(nb2Data.current, 3);
    json += ",\"active_power\":"        + String(nb2Data.activePower, 1);
    json += ",\"reactive_power\":"      + String(nb2Data.reactivePower, 1);
    json += ",\"apparent_power\":"      + String(nb2Data.apparentPower, 1);
    json += ",\"power_factor\":"        + String(nb2Data.powerFactor, 2);
    json += ",\"frequency\":"           + String(nb2Data.frequency, 2);
    json += ",\"breaker_on\":"          + String(nb2Data.breakerOn ? "true" : "false");
    json += ",\"internal_temp\":"       + String(nb2Data.internalTemp);
    json += ",\"fault_flags\":"         + String(nb2Data.faultFlags);
    json += ",\"alarm_flags\":"         + String(nb2Data.alarmFlags);
    json += ",\"model_code\":"          + String(nb2Data.modelCode);
    json += ",\"rs485_ok\":"            + String(nb2Data.rs485Ok ? "true" : "false");
    json += "}";
    json += "}";

    if (!mqttClient.publish(MQTT_TOPIC_TELE, json.c_str()))
        Serial.println("\n[MQTT] Publish failed -- buffer too small?");
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
    while (idx < (int)json.length() && json[idx] == ' ') idx++;
    String numStr = "";
    while (idx < (int)json.length() && (isDigit(json[idx]) || json[idx] == '-'))
        numStr += json[idx++];
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
