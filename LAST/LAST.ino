// ============================================================================
// ESP32 MOTOR NODE — merged firmware
// Base logic: original joystick/ramp/ArduinoJson version (unchanged behavior)
// Added:      NB2 CHINT breaker RS485/Modbus feature from the newer firmware
// ============================================================================

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>
// Raw Modbus RTU (replaces ModbusMaster library which had CRC issues with NB2)

const char* WIFI_SSID       = "M-4G";      // ← CHANGE THIS
const char* WIFI_PASSWORD   = "Aa4490810";   // ← CHANGE THIS
const char* MQTT_HOST = "192.168.1.27";
const uint16_t MQTT_PORT = 1883;
const char* MQTT_CLIENT_ID = "esp32_motor_node_01";

const char* MQTT_TELEMETRY_TOPIC = "digital_twin/motor/telemetry";
const char* MQTT_COMMAND_TOPIC = "digital_twin/motor/command";
const char* MQTT_ENCODER_TOPIC =
  "digital_twin/line_01/encoder/telemetry";
const char* MQTT_ENCODER_DIAG_TOPIC =
  "digital_twin/line_01/encoder/diagnostics";
static const uint8_t ENCODER_A_PIN = 34;
static const uint8_t ENCODER_B_PIN = 35;

static const uint8_t E18_PROXIMITY_PIN = 4;
static const uint8_t DS18B20_PIN = 16;

static const uint8_t JOYSTICK_X_PIN = 25;
static const uint8_t JOYSTICK_Y_PIN = 33;
static const uint8_t JOYSTICK_SW_PIN = 32;

static const uint8_t RPWM_PIN = 18;
static const uint8_t LPWM_PIN = 19;

static const uint32_t PWM_FREQ = 5000;
static const uint8_t PWM_RESOLUTION = 8;

static const int JOYSTICK_CENTER = 2048;
static const int JOYSTICK_DEADZONE = 350;

static const int JOYSTICK_STEP_PERCENT = 10;       // legacy (unused in gear mode)
static const unsigned long JOYSTICK_STEP_INTERVAL_MS = 50;    // legacy
static const unsigned long JOYSTICK_BUTTON_DEBOUNCE_MS = 50;

// ── Gear-style joystick ───────────────────────────────────────────────────
static const int   MANUAL_SPEED_STEP_PERCENT    = 10;   // one gear = 10% speed
static const unsigned long JOYSTICK_GESTURE_DEBOUNCE_MS = 150;

static const int MOTOR_RAMP_STEP_PERCENT = 2;        // Ramp up by 2% per step
static const unsigned long MOTOR_RAMP_INTERVAL_MS = 20; // Every 20ms (Takes 1 second to reach 100%)

// ---------------------------------------------------------------------------
// NEW: NB2 CHINT breaker (RS485 / Modbus) pins + config
// ---------------------------------------------------------------------------
static const uint8_t PIN_RS485_TX = 26;   // ESP32 TX -> MAX485 DI
static const uint8_t PIN_RS485_RX = 27;   // ESP32 RX <- MAX485 RO
static const uint8_t PIN_RS485_EN = 23;   // ESP32 -> MAX485 DE+RE (tied together)

#define NB2_SLAVE_ADDR   3        // Default CHINT NB2 address
#define NB2_BAUD_RATE    19200    // Default NB2 baud rate (SERIAL_8E1)
static const unsigned long NB2_POLL_INTERVAL_MS = 1000;

// ── NB2 physical unlock button ───────────────────────────────────────────────
// ► Choose a free GPIO that does NOT conflict with any other peripheral.
// ► Wire the button between the chosen pin and GND. The pin is INPUT_PULLUP.
// ► Leave as 255 (sentinel) until the physical pin is confirmed.
static const uint8_t  NB2_UNLOCK_BUTTON_PIN  = 255;   // TODO: set real GPIO
static const unsigned long NB2_UNLOCK_WINDOW_MS   = 30000; // 30 s authorization window
static const unsigned long NB2_UNLOCK_HOLD_MS     = 1500;  // hold 1.5 s to unlock

bool          nb2CommandUnlocked = false;
unsigned long nb2UnlockExpiresAt = 0;

volatile long encoderTotalCount = 0;  // CUMULATIVE — never reset
volatile long encoderWindowCount = 0;
volatile unsigned long lastPulseTime = 0;

static const int ENCODER_FORWARD_SIGN = 1;

// ── Encoder diagnostic mode ───────────────────────────────────────────────
// 1 = diagnostics ON + NB2 Modbus disabled for clean signal testing.
// 0 = production mode (NB2 polling re-enabled, diagnostic publisher removed).
#define ENCODER_DIAG_MODE 1

// Window counters — reset every 500 ms by publishEncoderDiagnostics().
// encoderTotalCount is intentionally excluded and is NEVER reset here.
volatile unsigned long encPulsesAccepted = 0;  // ISR entries that passed debounce
volatile unsigned long encPulsesRejected = 0;  // ISR entries rejected by debounce
volatile unsigned long encCountPos       = 0;  // pulses that incremented count
volatile unsigned long encCountNeg       = 0;  // pulses that decremented count

long lastPublishedEncoderCount = 0;

unsigned long encoderTelemetryIntervalMs = 100;
unsigned long lastEncoderTelemetryTime = 0;
float ppr = 770.0f; // Reverting to original, user can adjust if needed
float currentRPM = 0.0f;
float currentTempC = NAN;

int currentDirection = 0;
int currentSpeedPercent = 0;

int targetDirection = 0;
int targetSpeedPercent = 0;

// ── Control mode & joystick zone ─────────────────────────────────────────
// MANUAL  : joystick controls the motor; MQTT motor speed/dir cmds rejected.
// REMOTE  : MQTT commands control the motor; joystick button still stops it.
enum ControlMode  { CONTROL_MODE_MANUAL, CONTROL_MODE_REMOTE };
enum JoystickZone { JOYSTICK_ZONE_DOWN, JOYSTICK_ZONE_NEUTRAL, JOYSTICK_ZONE_UP };

ControlMode controlMode = CONTROL_MODE_MANUAL; // default: local joystick priority

unsigned long rpmIntervalMs = 1000;
unsigned long telemetryIntervalMs = 1000;
unsigned long temperatureIntervalMs = 2000;
unsigned long wifiRetryIntervalMs = 5000;
unsigned long mqttRetryIntervalMs = 5000;

unsigned long lastRpmTime = 0;
unsigned long lastTelemetryTime = 0;
unsigned long lastTemperatureTime = 0;
unsigned long lastWifiAttempt = 0;
unsigned long lastMqttAttempt = 0;
unsigned long lastJoystickStepTime = 0;
unsigned long lastMotorRampTime = 0;
unsigned long lastJoystickButtonChangeTime = 0;
unsigned long lastNb2PollTime = 0;   // NEW

bool wifiStarted = false;
bool joystickButtonLastReading = HIGH;
bool joystickButtonStableState = HIGH;

WiFiClient espClient;
PubSubClient mqttClient(espClient);

OneWire oneWire(DS18B20_PIN);
DallasTemperature tempSensors(&oneWire);

// ---------------------------------------------------------------------------
// NEW: NB2 raw Modbus RTU + state
// ---------------------------------------------------------------------------
HardwareSerial rs485Serial(2);   // UART2

struct NB2Data {
  float   voltage;
  float   current;
  float   activePower;
  float   reactivePower;
  float   apparentPower;
  float   powerFactor;
  float   frequency;
  int32_t energyWh;
  int32_t reactiveEnergyVarh;
  float   residualCurrent;

  bool     breakerOn;
  uint16_t faultFlags;
  uint16_t alarmFlags;
  float    internalTemp;

  bool          rs485Ok;
  uint8_t       failCount;
  unsigned long lastReadTime;
} nb2Data = {0};

// ── NB2 breaker command queue ─────────────────────────────────────────────
// Set by mqttCallback (which must not block). Consumed by serviceNB2Commands()
// in the main loop, where blocking RS485 calls are acceptable.
volatile bool nb2CloseRequested = false;
volatile bool nb2OpenRequested  = false;

void IRAM_ATTR onEncoderPulse() {
  unsigned long now = micros();

  if (now - lastPulseTime <= 150) {
    encPulsesRejected++;   // debounced — count for diagnostics
    return;
  }

  // One-way conveyor: every accepted rising edge on Channel A is forward.
  //
  // Channel B / GPIO35 is intentionally NOT read here.
  // Using Channel B introduced direction ambiguity when the input was
  // floating, noisy, or incorrectly phased, causing pos and neg counts
  // to cancel and producing a near-zero net total.
  //
  // The conveyor has reverse permanently disabled in firmware, so
  // direction is fixed. Re-enable quadrature decoding only after the
  // encoder wiring and signal quality are fully verified.
  int direction = ENCODER_FORWARD_SIGN;

  encoderTotalCount  += direction;   // cumulative — NEVER reset externally
  encoderWindowCount += direction;

  // Diagnostic counters (window-reset by publishEncoderDiagnostics)
  encPulsesAccepted++;
  if (direction > 0) encCountPos++;
  else               encCountNeg++;  // dead branch while ENCODER_FORWARD_SIGN > 0

  lastPulseTime = now;
}

const char* directionText() {
  if (currentDirection > 0) return "fwd";
  if (currentDirection < 0) return "rev";
  return "stop";
}

void writeMotorOutput(int direction, int speedPercent) {
  speedPercent = constrain(speedPercent, 0, 100);
  uint32_t duty = map(speedPercent, 0, 100, 0, 255);

  if (direction > 0 && speedPercent > 0) {
    ledcWrite(RPWM_PIN, duty);
    ledcWrite(LPWM_PIN, 0);

    currentDirection = 1;
    currentSpeedPercent = speedPercent;

  } else if (direction < 0 && speedPercent > 0) {
    // FAILSAFE: Reverse is permanently disabled!
    ledcWrite(RPWM_PIN, 0);
    ledcWrite(LPWM_PIN, 0);

    currentDirection = 0;
    currentSpeedPercent = 0;

  } else {
    ledcWrite(RPWM_PIN, 0);
    ledcWrite(LPWM_PIN, 0);

    currentDirection = 0;
    currentSpeedPercent = 0;
  }
}

void setMotorTarget(int direction, int speedPercent) {
  targetDirection = direction;
  targetSpeedPercent = constrain(speedPercent, 0, 100);

  if (targetSpeedPercent == 0) {
    targetDirection = 0;
  }
}

void gradualMotorControl() {
  unsigned long now = millis();

  if (now - lastMotorRampTime < MOTOR_RAMP_INTERVAL_MS) {
    return;
  }

  // Calculate how many ramp steps we missed if the loop was blocked (e.g. by Modbus)
  unsigned long elapsed = now - lastMotorRampTime;
  int steps = elapsed / MOTOR_RAMP_INTERVAL_MS;
  if (steps > 50) steps = 50; // Cap to prevent massive jumps
  
  lastMotorRampTime = now - (elapsed % MOTOR_RAMP_INTERVAL_MS);

  int totalRampAmount = MOTOR_RAMP_STEP_PERCENT * steps;

  if (currentSpeedPercent > targetSpeedPercent) {
    int newSpeed = currentSpeedPercent - totalRampAmount;
    if (newSpeed < targetSpeedPercent) newSpeed = targetSpeedPercent;
    writeMotorOutput(currentDirection, newSpeed);
    return;
  }

  if (currentSpeedPercent == 0 && targetSpeedPercent > 0) {
    writeMotorOutput(targetDirection, totalRampAmount > targetSpeedPercent ? targetSpeedPercent : totalRampAmount);
    return;
  }

  if (currentDirection != targetDirection && currentSpeedPercent > 0) {
    int newSpeed = currentSpeedPercent - totalRampAmount;
    if (newSpeed < 0) newSpeed = 0;
    writeMotorOutput(currentDirection, newSpeed);
    return;
  }

  if (currentSpeedPercent < targetSpeedPercent) {
    int newSpeed = currentSpeedPercent + totalRampAmount;
    if (newSpeed > targetSpeedPercent) newSpeed = targetSpeedPercent;
    writeMotorOutput(targetDirection, newSpeed);
    return;
  }

  if (targetSpeedPercent == 0 && currentSpeedPercent == 0) {
    writeMotorOutput(0, 0);
  }
}


// ── Gear-style joystick ───────────────────────────────────────────────────
// Speed changes ONLY on a NEUTRAL→UP or NEUTRAL→DOWN zone transition.
// Holding the joystick in a direction does NOT repeat the speed change.
// Returning to neutral re-arms the joystick for the next pulse.
// The button is always active regardless of control mode (local safety stop).
void readJoystick() {
  unsigned long now = millis();

  int joystickY = analogRead(JOYSTICK_Y_PIN);

  // ── Classify current zone ─────────────────────────────────────────────
  JoystickZone zone;
  if      (joystickY > (JOYSTICK_CENTER + JOYSTICK_DEADZONE)) zone = JOYSTICK_ZONE_UP;
  else if (joystickY < (JOYSTICK_CENTER - JOYSTICK_DEADZONE)) zone = JOYSTICK_ZONE_DOWN;
  else                                                         zone = JOYSTICK_ZONE_NEUTRAL;

  // ── Debounce zone transitions ─────────────────────────────────────────
  static JoystickZone stableZone     = JOYSTICK_ZONE_NEUTRAL;
  static JoystickZone candidateZone  = JOYSTICK_ZONE_NEUTRAL;
  static unsigned long zoneChangeTime = 0;

  if (zone != candidateZone) {
    candidateZone  = zone;
    zoneChangeTime = now;
  }

  if (now - zoneChangeTime >= JOYSTICK_GESTURE_DEBOUNCE_MS) {
    if (stableZone != candidateZone) {
      JoystickZone prevStable = stableZone;
      stableZone = candidateZone;

      if (controlMode == CONTROL_MODE_MANUAL) {
        // ── Gear UP: NEUTRAL → UP ────────────────────────────────────────
        if (prevStable == JOYSTICK_ZONE_NEUTRAL && stableZone == JOYSTICK_ZONE_UP) {
          targetDirection    = 1;
          targetSpeedPercent = constrain(
            targetSpeedPercent + MANUAL_SPEED_STEP_PERCENT, 0, 100);
          Serial.print("[JOY] Gear UP → target=");
          Serial.print(targetSpeedPercent);
          Serial.println("%");

        // ── Gear DOWN: NEUTRAL → DOWN (no reverse) ───────────────────────
        } else if (prevStable == JOYSTICK_ZONE_NEUTRAL && stableZone == JOYSTICK_ZONE_DOWN) {
          targetSpeedPercent = constrain(
            targetSpeedPercent - MANUAL_SPEED_STEP_PERCENT, 0, 100);
          if (targetSpeedPercent == 0) targetDirection = 0;
          Serial.print("[JOY] Gear DOWN → target=");
          Serial.print(targetSpeedPercent);
          Serial.println("%");
        }
        // NEUTRAL held or UP/DOWN held → no action (gear already selected)

      } else {
        // REMOTE mode: joystick direction changes are silently ignored
        if (stableZone != JOYSTICK_ZONE_NEUTRAL) {
          Serial.println("[JOY] Ignored — REMOTE mode active. Send {\"mode\":\"manual\"} to regain local control.");
        }
      }
    }
  }

  // ── Button: local safety stop — always active, mode-independent ──────
  bool buttonReading = digitalRead(JOYSTICK_SW_PIN);

  if (buttonReading != joystickButtonLastReading) {
    lastJoystickButtonChangeTime = now;
  }

  if (now - lastJoystickButtonChangeTime > JOYSTICK_BUTTON_DEBOUNCE_MS) {
    if (buttonReading != joystickButtonStableState) {
      joystickButtonStableState = buttonReading;

      if (joystickButtonStableState == LOW) {
        targetSpeedPercent = 0;
        targetDirection    = 0;
        // Force MANUAL mode so a previously active MQTT command
        // cannot restart the conveyor after a local operator stop.
        controlMode = CONTROL_MODE_MANUAL;
        Serial.println("[JOY] Button -> STOP. Control mode forced to MANUAL.");
      }
    }
  }

  joystickButtonLastReading = buttonReading;
}

void evaluateRPM() {
  unsigned long now = millis();

  if (now - lastRpmTime < rpmIntervalMs) {
    return;
  }

  noInterrupts();
  long countSnapshot = encoderWindowCount;
  encoderWindowCount = 0;
  interrupts();

  unsigned long elapsed = now - lastRpmTime;

  if (targetSpeedPercent == 0 && targetDirection == 0) {
    currentRPM = 0.0f;
    lastRpmTime = now;
    return;
  }

  float rawRPM = 0.0f;

  if (abs(countSnapshot) >= 2 && elapsed > 0 && ppr > 0.0f) {
    rawRPM = (
      (float)abs(countSnapshot)
      / (float)elapsed
    ) * (60000.0f / ppr);
  }

  if (rawRPM == 0.0f) {
    currentRPM = 0.0f;
  } else {
    currentRPM = (currentRPM * 0.7f) + (rawRPM * 0.3f);
  }

  lastRpmTime = now;
}
// ── Non-blocking DS18B20 temperature read ─────────────────────────────────────
// setWaitForConversion(false) is called in setup(), so requestTemperatures()
// returns immediately. The result is read only after the conversion time has
// elapsed, keeping the main loop free during the ~750 ms conversion.
void readTemperature() {
  enum TempState { TEMP_IDLE, TEMP_CONVERTING };
  static TempState state          = TEMP_IDLE;
  static unsigned long stateStart = 0;
  static const unsigned long CONVERSION_DELAY_MS = 800; // DS18B20 max at 12-bit

  unsigned long now = millis();

  if (state == TEMP_IDLE) {
    // Wait for the inter-sample interval before requesting a new conversion
    if (now - lastTemperatureTime < temperatureIntervalMs) return;
    lastTemperatureTime = now;
    tempSensors.requestTemperatures(); // returns immediately (non-blocking)
    stateStart = now;
    state = TEMP_CONVERTING;
    return;
  }

  if (state == TEMP_CONVERTING) {
    // Wait for conversion to finish before reading the result
    if (now - stateStart < CONVERSION_DELAY_MS) return;
    state = TEMP_IDLE;

    float temp = tempSensors.getTempCByIndex(0);
    if (temp != DEVICE_DISCONNECTED_C) {
      currentTempC = temp;
      Serial.print("[TEMP] ");
      Serial.print(currentTempC, 2);
      Serial.println(" C");
    } else {
      currentTempC = NAN;
      Serial.println("[TEMP] DS18B20 disconnected or read failed");
    }
  }
}

// ---------------------------------------------------------------------------
// RAW MODBUS RTU (replaces ModbusMaster library which had CRC issues)
// ---------------------------------------------------------------------------
uint16_t modbusCRC16(const byte* data, int length) {
  uint16_t crc = 0xFFFF;
  for (int pos = 0; pos < length; pos++) {
    crc ^= (uint16_t)data[pos];
    for (int i = 8; i != 0; i--) {
      if ((crc & 0x0001) != 0) { crc >>= 1; crc ^= 0xA001; }
      else { crc >>= 1; }
    }
  }
  return crc;
}

void rs485Send(const byte* buf, int len) {
  while (rs485Serial.available()) { rs485Serial.read(); }  // flush RX
  digitalWrite(PIN_RS485_EN, HIGH);   // TX mode
  rs485Serial.write(buf, len);
  rs485Serial.flush();                // wait for TX to complete
  delayMicroseconds(100);             // guard time to ensure stop bit fully shifts out
  digitalWrite(PIN_RS485_EN, LOW);    // RX mode
}

// Send function-code-0x10 write command (for breaker remote control)
void nb2WriteRegister(uint16_t cmdVal) {
  byte txBuf[11];
  txBuf[0] = NB2_SLAVE_ADDR;
  txBuf[1] = 0x10;  // Function 16 (Write Multiple)
  txBuf[2] = 0x00; txBuf[3] = 0x00;  // Register 0x0000
  txBuf[4] = 0x00; txBuf[5] = 0x01;  // 1 register
  txBuf[6] = 0x02;                    // 2 bytes
  txBuf[7] = (cmdVal >> 8) & 0xFF;
  txBuf[8] = cmdVal & 0xFF;
  uint16_t crc = modbusCRC16(txBuf, 9);
  txBuf[9]  = crc & 0xFF;
  txBuf[10] = crc >> 8;
  rs485Send(txBuf, 11);
  delay(200);
}

// Read holding registers via raw Modbus RTU, returns data bytes or -1 on fail
int nb2ReadRegisters(uint16_t startAddr, uint16_t count, byte* outData, int outMax) {
  byte txBuf[8];
  txBuf[0] = NB2_SLAVE_ADDR;
  txBuf[1] = 0x03;  // Function 3 (Read Holding Registers)
  txBuf[2] = startAddr >> 8;
  txBuf[3] = startAddr & 0xFF;
  txBuf[4] = count >> 8;
  txBuf[5] = count & 0xFF;
  uint16_t crc = modbusCRC16(txBuf, 6);
  txBuf[6] = crc & 0xFF;
  txBuf[7] = crc >> 8;

  rs485Send(txBuf, 8);

  // Receive response with 250ms timeout
  unsigned long startTime = millis();
  byte rxBuf[128];
  int bytesReceived = 0;
  while (millis() - startTime < 250) {
    if (rs485Serial.available()) {
      rxBuf[bytesReceived++] = rs485Serial.read();
      if (bytesReceived >= (int)sizeof(rxBuf)) break;
    }
  }

  if (bytesReceived < 5) return -1;  // too short

  // Scan for valid frame: [slaveID=NB2_SLAVE_ADDR] [func=0x03] [byteCount] [data...] [CRC]
  for (int i = 0; i < bytesReceived - 3; i++) {
    if (rxBuf[i] == NB2_SLAVE_ADDR && rxBuf[i+1] == 0x03) {
      int dataLen = rxBuf[i+2];
      if (i + 3 + dataLen + 2 <= bytesReceived) {
        uint16_t rxCRC = (rxBuf[i+3+dataLen+1] << 8) | rxBuf[i+3+dataLen];
        uint16_t calcCRC = modbusCRC16(&rxBuf[i], 3 + dataLen);
        if (rxCRC == calcCRC) {
          int copyLen = (dataLen < outMax) ? dataLen : outMax;
          memcpy(outData, &rxBuf[i+3], copyLen);
          return copyLen;
        }
      }
    }
  }
  return -1;  // no valid frame found
}

// ---------------------------------------------------------------------------
// Poll all NB2 registers using raw Modbus (proven working approach)
// ---------------------------------------------------------------------------
void pollNB2Breaker() {
  byte data[128];
  bool anySuccess = false;
  int len;

  // Read temperature (0x0003, 1 register = 2 bytes)
  len = nb2ReadRegisters(0x0003, 1, data, sizeof(data));
  if (len >= 2) {
    nb2Data.internalTemp = (float)((int16_t)((data[0] << 8) | data[1]));
    anySuccess = true;
  }
  delay(50);

  // Read status/fault/alarm (0x0020, 3 registers = 6 bytes)
  len = nb2ReadRegisters(0x0020, 3, data, sizeof(data));
  if (len >= 6) {
    uint16_t workState = (data[0] << 8) | data[1];
    nb2Data.faultFlags  = (data[2] << 8) | data[3];
    nb2Data.alarmFlags  = (data[4] << 8) | data[5];
    nb2Data.breakerOn   = (workState >> 7) & 0x01;
    anySuccess = true;
  }
  delay(50);

  // Read big electrical block (0x0040, 48 registers = 96 bytes)
  len = nb2ReadRegisters(0x0040, 48, data, sizeof(data));
  if (len >= 96) {
    // Current I1 (0x0040-0041) = bytes 0-3
    int32_t rawCurrent = ((int32_t)data[0] << 24) | ((int32_t)data[1] << 16) |
                         ((int32_t)data[2] << 8) | (int32_t)data[3];
    nb2Data.current = rawCurrent * 0.001f;

    // Voltage V1 (0x0048) = bytes 16-17
    nb2Data.voltage = ((data[16] << 8) | data[17]) * 0.01f;

    // Frequency (0x004F) = bytes 30-31
    nb2Data.frequency = ((data[30] << 8) | data[31]) * 0.01f;

    // Residual current (0x0050) = bytes 32-33
    nb2Data.residualCurrent = (float)((data[32] << 8) | data[33]);

    // Active power total (0x0057-0x0058) = bytes 46-49
    int32_t rawPower = ((int32_t)data[46] << 24) | ((int32_t)data[47] << 16) |
                       ((int32_t)data[48] << 8) | (int32_t)data[49];
    nb2Data.activePower = rawPower * 0.1f;

    // Energy forward (0x0059-0x005A) = bytes 50-53
    nb2Data.energyWh = ((int32_t)data[50] << 24) | ((int32_t)data[51] << 16) |
                       ((int32_t)data[52] << 8) | (int32_t)data[53];

    // Reactive energy (0x005B-0x005C) = bytes 54-57
    nb2Data.reactiveEnergyVarh = ((int32_t)data[54] << 24) | ((int32_t)data[55] << 16) |
                                 ((int32_t)data[56] << 8) | (int32_t)data[57];

    // Reactive power (0x005F-0x0060) = bytes 62-65
    int32_t rawReactive = ((int32_t)data[62] << 24) | ((int32_t)data[63] << 16) |
                          ((int32_t)data[64] << 8) | (int32_t)data[65];
    nb2Data.reactivePower = rawReactive * 0.1f;

    // Apparent power (0x0067-0x0068) = bytes 78-81
    int32_t rawApparent = ((int32_t)data[78] << 24) | ((int32_t)data[79] << 16) |
                          ((int32_t)data[80] << 8) | (int32_t)data[81];
    nb2Data.apparentPower = rawApparent * 0.1f;

    // Power factor (0x006F) = bytes 94-95
    int16_t rawPF = (int16_t)((data[94] << 8) | data[95]);
    nb2Data.powerFactor = rawPF * 0.01f;

    anySuccess = true;
  }

  if (anySuccess) {
    nb2Data.rs485Ok = true;
    nb2Data.failCount = 0;
    nb2Data.lastReadTime = millis();
    
    // Decode and print faults if present
    if (nb2Data.faultFlags > 0 || nb2Data.alarmFlags > 0) {
      Serial.print("[NB2] WARNING! Fault/Alarm Flags detected | Fault: 0x");
      Serial.print(nb2Data.faultFlags, HEX);
      Serial.print(" Alarm: 0x");
      Serial.print(nb2Data.alarmFlags, HEX);
      Serial.print(" -> ");
      
      // Decode typical NB2 bits
      if (nb2Data.faultFlags & 0x0001) Serial.print("Short Circuit, ");
      if (nb2Data.faultFlags & 0x0002) Serial.print("Overload, ");
      if (nb2Data.faultFlags & 0x0004) Serial.print("Overvoltage, ");
      if (nb2Data.faultFlags & 0x0008) Serial.print("Undervoltage, ");
      if (nb2Data.faultFlags & 0x0010) Serial.print("Leakage, ");
      if (nb2Data.faultFlags & 0x0020) Serial.print("OverTemp, ");
      if (nb2Data.faultFlags & 0x0040) Serial.print("PhaseLoss, ");
      if (nb2Data.faultFlags & 0x0080) Serial.print("Unbalance, ");
      if (nb2Data.faultFlags & 0x0100) Serial.print("OverPower, ");
      if (nb2Data.faultFlags & 0x0200) Serial.print("UnderPower, ");
      if (nb2Data.faultFlags & 0x0400) Serial.print("PhaseSeq, ");
      if (nb2Data.faultFlags & 0x0800) Serial.print("ArcFault, ");
      if (nb2Data.faultFlags & 0xF000) { Serial.print("OtherFault(0x"); Serial.print(nb2Data.faultFlags & 0xF000, HEX); Serial.print("), "); }

      if (nb2Data.alarmFlags & 0x0001) Serial.print("Leakage ALARM, ");
      if (nb2Data.alarmFlags & 0x0002) Serial.print("OverTemp ALARM, ");
      if (nb2Data.alarmFlags & 0x0004) Serial.print("Unbalance ALARM, ");
      if (nb2Data.alarmFlags & 0x0008) Serial.print("Overvoltage ALARM, ");
      if (nb2Data.alarmFlags & 0x0010) Serial.print("Undervoltage ALARM, ");
      if (nb2Data.alarmFlags & 0x0020) Serial.print("Overload ALARM, ");
      if (nb2Data.alarmFlags & 0xFFC0) { Serial.print("OtherAlarm(0x"); Serial.print(nb2Data.alarmFlags & 0xFFC0, HEX); Serial.print("), "); }
      
      Serial.println();
    }
  } else {
    nb2Data.failCount++;
    if (nb2Data.failCount >= 5) nb2Data.rs485Ok = false;
    Serial.print("[NB2] FAIL #");
    Serial.println(nb2Data.failCount);
  }
}

// ---------------------------------------------------------------------------
// NB2 remote breaker control (raw Modbus RTU)
// ---------------------------------------------------------------------------
void nb2RemoteClose() {
  nb2WriteRegister(0x0002);   // unlock remote control
  Serial.println("[NB2] Remote control unlocked.");
  delay(100);
  nb2WriteRegister(0x0006);   // remote closing
  Serial.println("[NB2] Remote CLOSE command sent.");
}

void nb2RemoteOpen() {
  nb2WriteRegister(0x0002);   // unlock remote control
  Serial.println("[NB2] Remote control unlocked.");
  delay(100);
  nb2WriteRegister(0x0007);   // remote opening
  Serial.println("[NB2] Remote OPEN command sent.");
}

// ── NB2 physical unlock button service ────────────────────────────────────────
// An operator must press and hold the physical button for NB2_UNLOCK_HOLD_MS
// before any queued breaker command is allowed to execute.
// This is separate from the NB2 protocol-level unlock (nb2WriteRegister(0x0002)):
//   Protocol unlock  = electronic authorization of the breaker hardware.
//   Physical button  = firmware authorization to issue that command at all.
void serviceNB2UnlockButton() {
  // Skip if no button pin has been assigned
  if (NB2_UNLOCK_BUTTON_PIN == 255) return;

  static bool          lastButtonState  = HIGH;
  static unsigned long pressedAt        = 0;
  static bool          unlockTriggered  = false;

  bool buttonState = digitalRead(NB2_UNLOCK_BUTTON_PIN);

  // Detect falling edge (active-low, INPUT_PULLUP)
  if (buttonState == LOW && lastButtonState == HIGH) {
    pressedAt       = millis();
    unlockTriggered = false;
  }

  // Grant unlock after the hold duration has elapsed
  if (buttonState == LOW && !unlockTriggered &&
      millis() - pressedAt >= NB2_UNLOCK_HOLD_MS) {
    nb2CommandUnlocked  = true;
    nb2UnlockExpiresAt  = millis() + NB2_UNLOCK_WINDOW_MS;
    unlockTriggered     = true;
    Serial.println("[NB2] Physical unlock accepted. "
                   "One breaker command is allowed for 30 seconds.");
  }

  // Re-arm on release
  if (buttonState == HIGH) {
    unlockTriggered = false;
  }

  lastButtonState = buttonState;
}

// ── NB2 unlock window expiry ───────────────────────────────────────────────
void serviceNB2UnlockTimeout() {
  if (nb2CommandUnlocked && millis() >= nb2UnlockExpiresAt) {
    nb2CommandUnlocked = false;
    Serial.println("[NB2] Physical unlock expired. Breaker commands are locked.");
  }
}

// ── NB2 command queue processor ────────────────────────────────────────────────
// Conditions that must ALL be true before a queued command executes:
//   1. ENCODER_DIAG_MODE == 0  (not in diagnostic phase)
//   2. Conveyor fully stopped  (no motion commanded or occurring)
//   3. Physical unlock granted  (operator pressed and held the button)
// After one command executes, the unlock is immediately consumed.
void serviceNB2Commands() {
#if ENCODER_DIAG_MODE
  return; // NB2 commands suppressed during encoder diagnostic phase
#endif

  // Guard 1: Conveyor must be completely stopped.
  // Blocking RS485 calls must not run while the belt is in motion.
  bool conveyorStopped = (currentSpeedPercent == 0 &&
                          targetSpeedPercent  == 0 &&
                          currentDirection    == 0 &&
                          targetDirection     == 0);
  if (!conveyorStopped) return;

  // Guard 2: Physical unlock button must have been pressed and held.
  if (!nb2CommandUnlocked) return;

  // Execute one queued command, then immediately consume the unlock.
  if (nb2CloseRequested) {
    nb2CloseRequested  = false;
    nb2OpenRequested   = false; // discard any simultaneous open request
    nb2CommandUnlocked = false;
    Serial.println("[NB2] Executing CLOSE. Physical unlock consumed; NB2 commands locked.");
    nb2RemoteClose();
    return;
  }

  if (nb2OpenRequested) {
    nb2OpenRequested   = false;
    nb2CommandUnlocked = false;
    Serial.println("[NB2] Executing OPEN. Physical unlock consumed; NB2 commands locked.");
    nb2RemoteOpen();
    return;
  }
}
void publishEncoderTelemetry() {
  if (!mqttClient.connected()) {
    return;
  }

  unsigned long now = millis();

  if (
    now - lastEncoderTelemetryTime
    < encoderTelemetryIntervalMs
  ) {
    return;
  }

  lastEncoderTelemetryTime = now;

  noInterrupts();
  long totalCountSnapshot = encoderTotalCount;
  interrupts();

  long deltaCount = (
    totalCountSnapshot - lastPublishedEncoderCount
  );

  lastPublishedEncoderCount = totalCountSnapshot;

  StaticJsonDocument<256> doc;

  doc["uptime_ms"] = now;
  doc["encoder_count"] = totalCountSnapshot;
  doc["delta_count"] = deltaCount;
  doc["ppr"] = ppr;

  if (deltaCount > 0) {
    doc["direction"] = "left_to_right";
  } else if (deltaCount < 0) {
    doc["direction"] = "right_to_left";
  } else {
    doc["direction"] = "stopped";
  }

  char payload[256];

  size_t len = serializeJson(
    doc,
    payload,
    sizeof(payload)
  );

  bool published = mqttClient.publish(
    MQTT_ENCODER_TOPIC,
    payload,
    len
  );

  if (!published) {
    Serial.println("[ENC MQTT] publish failed");
  }
}

#if ENCODER_DIAG_MODE
// ── Encoder diagnostic publisher ─────────────────────────────────────────
// Publishes a 500 ms window breakdown of ISR pulse counts to Serial + MQTT.
// Includes an interpreted diagnosis string to speed up hardware debugging.
// NOTE: encoderTotalCount is NEVER reset here — only window counters are.
void publishEncoderDiagnostics() {
  static unsigned long lastDiagTime = 0;
  static const unsigned long DIAG_INTERVAL_MS = 500;

  unsigned long now = millis();
  if (now - lastDiagTime < DIAG_INTERVAL_MS) return;
  lastDiagTime = now;

  // Atomically snapshot and reset window counters
  noInterrupts();
  unsigned long accepted = encPulsesAccepted; encPulsesAccepted = 0;
  unsigned long rejected = encPulsesRejected; encPulsesRejected = 0;
  unsigned long pos      = encCountPos;       encCountPos       = 0;
  unsigned long neg      = encCountNeg;       encCountNeg       = 0;
  long          total    = encoderTotalCount; // snapshot only — cumulative, not reset
  interrupts();

  // ── Interpret signal quality ──────────────────────────────────────────
  // motorExpectedToMove distinguishes a healthy idle from a real fault:
  // "no pulses" is expected and correct when the belt is intentionally stopped.
  bool motorExpectedToMove = (targetDirection > 0 && targetSpeedPercent > 0);

  const char* diagnosis;
  if (accepted == 0 && rejected == 0) {
    if (!motorExpectedToMove) {
      diagnosis = "IDLE — belt stopped, no encoder pulses expected";
    } else {
      diagnosis = "NO PULSES WHILE MOTOR IS COMMANDED TO RUN — "
                  "check Channel A wiring, encoder power, "
                  "common GND, mechanical coupling, and signal voltage";
    }
  } else if (accepted == 0 && rejected > 0) {
    diagnosis = "ALL REJECTED (debounce) — noise/ringing on Channel A; "
                "check pull-ups and cable shielding";
  } else if (accepted > 0 &&
             abs((long)pos - (long)neg) < (long)(accepted / 4 + 1)) {
    diagnosis = "pos ~ neg — Channel B may be floating, noisy, "
                "incorrectly phased, or incorrectly wired; "
                "add 4.7k pull-up to GPIO35 (3.3 V)";
  } else if (rejected > accepted) {
    diagnosis = "HIGH REJECT RATE — EMI from motor; "
                "check shielding, cable routing, pull-ups";
  } else {
    diagnosis = "Signal OK — one direction dominates; "
                "proceed with 500 mm calibration";
  }

  Serial.printf(
    "[ENC DIAG] total=%ld | win accepted=%lu (pos=%lu neg=%lu) rejected=%lu | %s\n",
    total, accepted, pos, neg, rejected, diagnosis
  );

//  if (!mqttClient.connected()) return; // Removed so it prints to Serial for USB Bridge
  StaticJsonDocument<384> doc;
  doc["uptime_ms"]       = now;
  doc["total_count"]     = total;
  doc["accepted_pulses"] = accepted;
  doc["pos_pulses"]      = pos;
  doc["neg_pulses"]      = neg;
  doc["rejected_pulses"] = rejected;
  doc["net_window"]      = (long)pos - (long)neg;
  doc["diagnosis"]       = diagnosis;

  char payload[384];
  size_t len = serializeJson(doc, payload, sizeof(payload));
  mqttClient.publish(MQTT_ENCODER_DIAG_TOPIC, payload, len);
}
#endif

void publishTelemetry() {

  unsigned long now = millis();

  if (now - lastTelemetryTime < telemetryIntervalMs) {
    return;
  }

  lastTelemetryTime = now;
  // NOTE: lastEncoderTelemetryTime is intentionally NOT set here.
  // The encoder MQTT topic has its own independent schedule in publishEncoderTelemetry().
  bool e18Active = (digitalRead(E18_PROXIMITY_PIN) == LOW);

  // NOTE: buffer enlarged (was 320) to fit the new nested "nb2" object
  StaticJsonDocument<640> doc;

  doc["rpm"] = currentRPM;
  doc["e18_active"] = e18Active;
  doc["uptime_ms"] = now;
  doc["dir"] = directionText();
  doc["speed_percent"] = currentSpeedPercent;
  doc["target_speed_percent"] = targetSpeedPercent;
  doc["ppr"] = ppr;

  if (isnan(currentTempC)) {
    doc["temp_c"] = nullptr;
  } else {
    doc["temp_c"] = currentTempC;
  }

  // NEW: nested NB2 breaker telemetry
  JsonObject nb2Obj = doc.createNestedObject("nb2");
  nb2Obj["voltage"] = nb2Data.voltage;
  nb2Obj["current"] = nb2Data.current;
  nb2Obj["active_power"] = nb2Data.activePower;
  nb2Obj["reactive_power"] = nb2Data.reactivePower;
  nb2Obj["apparent_power"] = nb2Data.apparentPower;
  nb2Obj["power_factor"] = nb2Data.powerFactor;
  nb2Obj["frequency"] = nb2Data.frequency;
  nb2Obj["energy_wh"] = nb2Data.energyWh;
  nb2Obj["reactive_energy_varh"] = nb2Data.reactiveEnergyVarh;
  nb2Obj["residual_current_ma"] = nb2Data.residualCurrent;
  nb2Obj["breaker_on"] = nb2Data.breakerOn;
  nb2Obj["internal_temp"] = nb2Data.internalTemp;
  nb2Obj["fault_flags"] = nb2Data.faultFlags;
  nb2Obj["alarm_flags"] = nb2Data.alarmFlags;
  nb2Obj["rs485_ok"] = nb2Data.rs485Ok;

  char payload[640];
  size_t len = serializeJson(doc, payload);

  bool published = false;
  if (mqttClient.connected()) {
    published = mqttClient.publish(
      MQTT_TELEMETRY_TOPIC,
      payload,
      len
    );
  }

  Serial.print("[PUB] ");
  Serial.print(payload);

  if (!published && mqttClient.connected()) {
    Serial.println("  <-- publish failed");
  } else {
    Serial.println();
  }
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  StaticJsonDocument<256> doc;

  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    Serial.print("[MQTT CMD] JSON error: ");
    Serial.println(err.c_str());
    return;
  }

  // ── 1. Mode switching — always accepted ──────────────────────────────
  // {"mode":"remote"} or {"mode":"manual"}
  if (doc.containsKey("mode")) {
    const char* modeCmd = doc["mode"];
    if (strcmp(modeCmd, "remote") == 0) {
      controlMode = CONTROL_MODE_REMOTE;
      Serial.println("[MQTT CMD] Control mode → REMOTE (MQTT controls motor)");
    } else if (strcmp(modeCmd, "manual") == 0) {
      controlMode = CONTROL_MODE_MANUAL;
      Serial.println("[MQTT CMD] Control mode → MANUAL (joystick controls motor)");
    }
  }

  // ── 2. Motor speed/direction commands ────────────────────────────────
  // Only executed in REMOTE mode; rejected in MANUAL mode to protect operator.
  const char* cmd = doc["cmd"] | "";
  const char* dir = doc["dir"] | "stop";
  int speed = doc["speed"] | 0;

  if (controlMode == CONTROL_MODE_REMOTE) {
    if (strcmp(cmd, "stop") == 0 || strcmp(cmd, "estop") == 0) {
      setMotorTarget(0, 0);
    } else if (strcmp(dir, "fwd") == 0 || strcmp(dir, "forward") == 0) {
      setMotorTarget(1, speed); // gradualMotorControl ramps toward target
    } else if (strcmp(dir, "rev") == 0 || strcmp(dir, "reverse") == 0) {
      Serial.println("[MQTT CMD] REV IGNORED (conveyor is one-direction only)");
    } else if (doc.containsKey("dir")) {
      setMotorTarget(0, 0);
    }
  } else {
    // MANUAL mode: log rejection so operator can see what was ignored
    bool hasMotorCmd = doc.containsKey("cmd") ||
                       (doc.containsKey("dir") && strcmp(dir, "stop") != 0) ||
                       (doc.containsKey("speed") && speed != 0);
    if (hasMotorCmd) {
      Serial.println("[MQTT CMD] Motor cmd REJECTED — MANUAL mode active."
                     " Send {\"mode\":\"remote\"} first to enable remote motor control.");
    }
  }

  // ── 3. PPR update — always accepted ──────────────────────────────────
  if (doc.containsKey("ppr")) {
    float newPpr = doc["ppr"].as<float>();
    if (newPpr > 0.0f) {
      ppr = newPpr;
      Serial.print("[MQTT CMD] PPR updated → ");
      Serial.println(ppr, 2);
    }
  }

  // ── 4. NB2 breaker commands — queue only; blocking RS485 runs in serviceNB2Commands()
  // mqttCallback must not block; it runs inside mqttClient.loop() on the main thread.
  if (doc.containsKey("breaker")) {
    const char* breakerCmd = doc["breaker"];
    if (strcmp(breakerCmd, "on") == 0) {
      nb2CloseRequested = true;
      Serial.println("[MQTT CMD] NB2 CLOSE queued (will execute in serviceNB2Commands)");
    } else if (strcmp(breakerCmd, "off") == 0) {
      nb2OpenRequested = true;
      Serial.println("[MQTT CMD] NB2 OPEN queued (will execute in serviceNB2Commands)");
    }
  }

  // ── 5. NB2 software unlock — opens authorization window from the website ──
  // Equivalent to holding the physical NB2_UNLOCK_BUTTON_PIN for 1.5 s.
  // The conveyor-stopped guard in serviceNB2Commands() still applies;
  // the website cannot override that hardware safety check.
  if (doc.containsKey("nb2_unlock") && doc["nb2_unlock"].as<bool>() == true) {
    nb2CommandUnlocked = true;
    nb2UnlockExpiresAt = millis() + NB2_UNLOCK_WINDOW_MS;
    Serial.println("[MQTT CMD] NB2 software unlock received. "
                   "One breaker command allowed for 30 seconds.");
  }

  Serial.print("[MQTT CMD] mode=");
  Serial.print(controlMode == CONTROL_MODE_MANUAL ? "MANUAL" : "REMOTE");
  Serial.print(" target_speed=");
  Serial.print(targetSpeedPercent);
  Serial.print(" ppr=");
  Serial.println(ppr, 2);
}

void startWiFiIfNeeded() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  unsigned long now = millis();

  if (wifiStarted && (now - lastWifiAttempt < wifiRetryIntervalMs)) {
    return;
  }

  lastWifiAttempt = now;
  wifiStarted = true;

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false); // Disable sleep to reduce latency and packet dropouts
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("[WIFI] Connecting to ");
  Serial.println(WIFI_SSID);
}

void monitorWiFi() {
  static wl_status_t lastStatus = WL_IDLE_STATUS;

  wl_status_t status = WiFi.status();

  if (status != lastStatus) {
    lastStatus = status;

    if (status == WL_CONNECTED) {
      Serial.print("[WIFI] Connected, IP=");
      Serial.println(WiFi.localIP());

    } else {
      Serial.print("[WIFI] Status=");
      Serial.println((int)status);
    }
  }

  if (status != WL_CONNECTED) {
    unsigned long now = millis();

    if (now - lastWifiAttempt >= wifiRetryIntervalMs) {
      WiFi.disconnect();
      wifiStarted = false;
      startWiFiIfNeeded();
      Serial.print("[BOOT] Encoder MQTT topic: ");
      Serial.println(MQTT_ENCODER_TOPIC);
    }
  }
}

void ensureMQTT() {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  if (mqttClient.connected()) {
    return;
  }

  unsigned long now = millis();

  if (now - lastMqttAttempt < mqttRetryIntervalMs) {
    return;
  }

  lastMqttAttempt = now;

  Serial.print("[MQTT] Connecting to ");
  Serial.print(MQTT_HOST);
  Serial.print(":");
  Serial.println(MQTT_PORT);

  if (mqttClient.connect(MQTT_CLIENT_ID)) {
    mqttClient.subscribe(MQTT_COMMAND_TOPIC);

    Serial.println("[MQTT] Connected");
    Serial.print("[MQTT] Subscribed: ");
    Serial.println(MQTT_COMMAND_TOPIC);

  } else {
    Serial.print("[MQTT] Failed, state=");
    Serial.println(mqttClient.state());
  }
}

// NB2 poll wrapper — disabled in ENCODER_DIAG_MODE to remove ~900 ms/s
// of blocking Modbus receive loops that distort MQTT telemetry timing.
// Re-enable by setting ENCODER_DIAG_MODE 0 after encoder signal is validated.
void pollNB2IfDue() {
#if ENCODER_DIAG_MODE
  // NB2 Modbus polling SUSPENDED during encoder diagnostic phase.
  // The ISR continues counting pulses unaffected; only the main-loop
  // blocking delay is eliminated so MQTT timestamps remain clean.
  return;
#endif
  unsigned long now = millis();
  if (now - lastNb2PollTime < NB2_POLL_INTERVAL_MS) return;
  lastNb2PollTime = now;
  pollNB2Breaker();
}

void setup() {
  Serial.begin(115200);

// Encoder input configuration:
//
// GPIO34 is Channel A and is the ONLY channel used for pulse counting.
// An interrupt on RISING edges fires onEncoderPulse() for each accepted pulse.
//
// GPIO35 remains physically wired (White wire) but is intentionally ignored
// by the firmware. Direction is fixed to ENCODER_FORWARD_SIGN because the
// conveyor is one-direction only and reverse motion is permanently disabled.
//
// Do not use Channel B to determine motion direction until the encoder wiring,
// electrical levels, and quadrature signal quality are fully verified.
pinMode(ENCODER_A_PIN, INPUT);   // Channel A — interrupt source
pinMode(ENCODER_B_PIN, INPUT);   // Channel B — wired but intentionally unused

  pinMode(E18_PROXIMITY_PIN, INPUT_PULLUP);

  pinMode(JOYSTICK_X_PIN, INPUT);
  pinMode(JOYSTICK_Y_PIN, INPUT);
  pinMode(JOYSTICK_SW_PIN, INPUT_PULLUP);

  // NB2 physical unlock button (INPUT_PULLUP, active-low).
  // Skip if NB2_UNLOCK_BUTTON_PIN is still the sentinel value 255.
  if (NB2_UNLOCK_BUTTON_PIN != 255) {
    pinMode(NB2_UNLOCK_BUTTON_PIN, INPUT_PULLUP);
  }

  analogReadResolution(12);

  attachInterrupt(
    digitalPinToInterrupt(ENCODER_A_PIN),
    onEncoderPulse,
    RISING
  );

  bool ok1 = ledcAttach(RPWM_PIN, PWM_FREQ, PWM_RESOLUTION);
  bool ok2 = ledcAttach(LPWM_PIN, PWM_FREQ, PWM_RESOLUTION);

  if (!ok1 || !ok2) {
    Serial.println("[ERR] PWM attach failed");

    while (true) {
      delay(1000);
    }
  }

  writeMotorOutput(0, 0);

  tempSensors.begin();
  // Non-blocking temperature conversions: requestTemperatures() returns immediately.
  // readTemperature() reads the result only after the conversion delay has elapsed.
  tempSensors.setWaitForConversion(false);

  // NEW: RS485 / NB2 Modbus setup
  pinMode(PIN_RS485_EN, OUTPUT);
  digitalWrite(PIN_RS485_EN, LOW);   // start in RX mode

  rs485Serial.begin(NB2_BAUD_RATE, SERIAL_8E1, PIN_RS485_RX, PIN_RS485_TX);
  // Send unlock command on boot (like the working NB2 code)
  nb2WriteRegister(0x0002);

  Serial.print("[BOOT] NB2 RS485 initialized - Slave: ");
  Serial.print(NB2_SLAVE_ADDR);
  Serial.print(", Baud: ");
  Serial.println(NB2_BAUD_RATE);

  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setBufferSize(1024);   // NEW: bigger buffer for the nb2 telemetry JSON
  mqttClient.setKeepAlive(60);      // 60-second keepalive to prevent timeouts during blocking ops
  mqttClient.setSocketTimeout(15);  // 15-second socket timeout for better reliability

  unsigned long now = millis();

  lastRpmTime = now;
  lastTelemetryTime = now;
  lastTemperatureTime = now;
  lastJoystickStepTime = now;
  lastMotorRampTime = now;
  lastNb2PollTime = now;

  Serial.println("[BOOT] ESP32 Motor Node");
  Serial.println("[BOOT] WiFi + MQTT + Encoder + BTS7960 + E18 + DS18B20 + Joystick + NB2");

  startWiFiIfNeeded();
}

void loop() {
  // ── 1. Safety-critical local path — always runs FIRST ─────────────────
  // Must not wait for Wi-Fi, MQTT, Modbus, or any networking path.
  readJoystick();        // gear-style manual control (state machine)
  gradualMotorControl(); // ramp actual motor output toward target

  // ── 2. Encoder & RPM ──────────────────────────────────────────────────
  evaluateRPM();
  publishEncoderTelemetry();
#if ENCODER_DIAG_MODE
  publishEncoderDiagnostics(); // 500 ms window: accepted/pos/neg/rejected
#endif

  // ── 3. Networking (after safety-critical path) ────────────────────────
  monitorWiFi();
  ensureMQTT();
  if (mqttClient.connected()) {
    mqttClient.loop();
  }

  // ── 4. Peripheral polling & telemetry publication ─────────────────────
  readTemperature();          // non-blocking 2-stage state machine
  serviceNB2UnlockButton();   // debounce physical button, open auth window
  serviceNB2UnlockTimeout();  // expire auth window after 30 s
  serviceNB2Commands();       // execute queued cmd only when stopped + unlocked
  pollNB2IfDue();             // no-op in ENCODER_DIAG_MODE
  publishTelemetry();
}
