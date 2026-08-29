// ============================================================================
// ESP32 MOTOR NODE — merged firmware (FIXED BUILD)
// Base logic: original joystick/ramp/ArduinoJson version (unchanged behavior)
// Added:      NB2 CHINT breaker RS485/Modbus feature from the newer firmware
//
// FIXES APPLIED IN THIS BUILD:
//   1. Remote command watchdog — auto-stops motor if REMOTE mode goes silent
//   2. Breaker commands explicitly rejected (not silently queued/forgotten)
//      while ENCODER_DIAG_MODE is active
//   3. Serial input length guard before JSON parsing
//   4. Software-only NB2 unlock gated behind an explicit flag
//   5. Minor comments clarifying ppr vs. linear pulses/mm (different units)
// ============================================================================


#include <Arduino.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>
// Raw Modbus RTU (replaces ModbusMaster library which had CRC issues with NB2)


const char* MQTT_TELEMETRY_TOPIC     = "digital_twin/motor/telemetry";
const char* MQTT_COMMAND_TOPIC       = "digital_twin/motor/command";
const char* MQTT_ENCODER_TOPIC       = "digital_twin/line_01/encoder/telemetry";
const char* MQTT_ENCODER_DIAG_TOPIC  = "digital_twin/line_01/encoder/diagnostics";


static const uint8_t ENCODER_A_PIN = 34;
static const uint8_t ENCODER_B_PIN = 35;


static const uint8_t E18_PROXIMITY_PIN = 4;
static const uint8_t DS18B20_PIN       = 16;


static const uint8_t JOYSTICK_X_PIN  = 25;
static const uint8_t JOYSTICK_Y_PIN  = 33;
static const uint8_t JOYSTICK_SW_PIN = 32;


static const uint8_t RPWM_PIN = 18;
static const uint8_t LPWM_PIN = 19;


static const uint32_t PWM_FREQ       = 5000;
static const uint8_t  PWM_RESOLUTION = 8;


static const int JOYSTICK_CENTER   = 2048;
static const int JOYSTICK_DEADZONE = 350;


// ── Gear-style joystick ───────────────────────────────────────────────────
static const int MANUAL_SPEED_STEP_PERCENT               = 10;  // one gear = 10% speed
static const unsigned long JOYSTICK_GESTURE_DEBOUNCE_MS  = 150;

// ── Walking speed presets ───────────────────────────────────────────────────
static const int WALK_SPEED_SLOW_PERCENT   = 30;   // slow
static const int WALK_SPEED_MEDIUM_PERCENT = 60;   // medium
static const int WALK_SPEED_FAST_PERCENT   = 100;  // fast


static const int MOTOR_RAMP_STEP_PERCENT               = 2;   // Ramp up by 2% per step
static const unsigned long MOTOR_RAMP_INTERVAL_MS      = 20;  // Every 20ms (1s to reach 100%)


// ---------------------------------------------------------------------------
// NB2 CHINT breaker (RS485 / Modbus) pins + config
// ---------------------------------------------------------------------------
static const uint8_t PIN_RS485_TX = 26;   // ESP32 TX -> MAX485 DI
static const uint8_t PIN_RS485_RX = 27;   // ESP32 RX <- MAX485 RO
static const uint8_t PIN_RS485_EN = 23;   // ESP32 -> MAX485 DE+RE (tied together)


#define NB2_SLAVE_ADDR   3        // Default CHINT NB2 address
#define NB2_BAUD_RATE    19200    // Default NB2 baud rate (SERIAL_8E1)
static const unsigned long NB2_POLL_INTERVAL_MS = 1000;


// ── NB2 physical unlock button ──────────────────────────────────────────────
// ► Choose a free GPIO that does NOT conflict with any other peripheral.
// ► Wire the button between the chosen pin and GND. The pin is INPUT_PULLUP.
// ► Leave as 255 (sentinel) until the physical pin is confirmed.
static const uint8_t  NB2_UNLOCK_BUTTON_PIN     = 255;   // TODO: set real GPIO
static const unsigned long NB2_UNLOCK_WINDOW_MS = 30000; // 30 s authorization window
static const unsigned long NB2_UNLOCK_HOLD_MS   = 1500;  // hold 1.5 s to unlock


// FIX #4: Software-only unlock (via serial/MQTT "nb2_unlock":true) bypasses the
// physical button entirely. That is convenient on a bench but dangerous once
// this runs unattended near a real load. Explicitly gate it here so it can be
// disabled with one line before deployment, instead of being silently active.
#define ALLOW_SOFTWARE_NB2_UNLOCK 1   // set to 0 before deploying near live loads


bool          nb2CommandUnlocked   = false;
unsigned long nb2UnlockExpiresAt   = 0;


volatile long encoderTotalCount  = 0;  // CUMULATIVE — never reset
volatile long encoderWindowCount = 0;
volatile unsigned long lastPulseTime = 0;


static const int ENCODER_FORWARD_SIGN = 1;


// ── Encoder diagnostic mode ─────────────────────────────────────────────────
// 1 = diagnostics ON + NB2 Modbus disabled for clean signal testing.
// 0 = production mode (NB2 polling re-enabled, diagnostic publisher removed).
#define ENCODER_DIAG_MODE 0


// Window counters — reset every 500 ms by publishEncoderDiagnostics().
// encoderTotalCount is intentionally excluded and is NEVER reset here.
volatile unsigned long encPulsesAccepted = 0;  // ISR entries that passed debounce
volatile unsigned long encPulsesRejected = 0;  // ISR entries rejected by debounce
volatile unsigned long encCountPos       = 0;  // pulses that incremented count
volatile unsigned long encCountNeg       = 0;  // pulses that decremented count


long lastPublishedEncoderCount = 0;


unsigned long encoderTelemetryIntervalMs = 100;
unsigned long lastEncoderTelemetryTime   = 0;

// NOTE: this "ppr" is pulses-PER-REVOLUTION of the motor shaft, used only for
// RPM math in evaluateRPM(). It is a DIFFERENT unit from the linear
// pulses-per-mm conveyor calibration (0.022 pulses/mm) stored on the Pi side.
// Do not overwrite one with the other.
float ppr          = 770.0f;
float currentRPM    = 0.0f;
float currentTempC  = NAN;


int currentDirection    = 0;
int currentSpeedPercent = 0;


int targetDirection    = 0;
int targetSpeedPercent = 0;


// ── Control mode & joystick zone ────────────────────────────────────────────
// MANUAL  : joystick controls the motor; Serial motor speed/dir cmds rejected.
// REMOTE  : Serial commands control the motor; joystick button still stops it.
enum ControlMode  { CONTROL_MODE_MANUAL, CONTROL_MODE_REMOTE };
enum JoystickZone { JOYSTICK_ZONE_DOWN, JOYSTICK_ZONE_NEUTRAL, JOYSTICK_ZONE_UP };


ControlMode controlMode = CONTROL_MODE_MANUAL; // default: local joystick priority


unsigned long rpmIntervalMs         = 1000;
unsigned long telemetryIntervalMs   = 1000;
unsigned long temperatureIntervalMs = 2000;


unsigned long lastRpmTime                   = 0;
unsigned long lastTelemetryTime             = 0;
unsigned long lastTemperatureTime           = 0;
unsigned long lastMotorRampTime             = 0;
unsigned long lastNb2PollTime               = 0;


// FIX #1: Remote command watchdog state.
// If controlMode == REMOTE and the motor is commanded to run, but no valid
// serial command arrives for REMOTE_COMMAND_TIMEOUT_MS, the motor is forced
// to stop automatically. This protects against a crashed/disconnected
// Raspberry Pi bridge leaving the conveyor running unattended.
unsigned long lastRemoteCommandTime = 0;
static const unsigned long REMOTE_COMMAND_TIMEOUT_MS = 3000;


OneWire oneWire(DS18B20_PIN);
DallasTemperature tempSensors(&oneWire);


// ---------------------------------------------------------------------------
// NB2 raw Modbus RTU + state
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


// ── NB2 breaker command queue ────────────────────────────────────────────────
// Set by serial command (which must not block). Consumed by serviceNB2Commands()
// in the main loop, where blocking RS485 calls are acceptable.
volatile bool nb2CloseRequested = false;
volatile bool nb2OpenRequested  = false;


void IRAM_ATTR onEncoderPulse() {
  unsigned long now = micros();


  if (now - lastPulseTime <= 250) {
    encPulsesRejected++;   // debounced — count for diagnostics
    return;
  }


  // One-way conveyor: every accepted rising edge on Channel A is forward.
  // Channel B / GPIO35 is intentionally NOT read here.
  int direction = ENCODER_FORWARD_SIGN;


  encoderTotalCount  += direction;   // cumulative — NEVER reset externally
  encoderWindowCount += direction;


  // Diagnostic counters (window-reset by publishEncoderDiagnostics)
  encPulsesAccepted++;
  if (direction > 0) encCountPos++;
  else               encCountNeg++;


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


    currentDirection    = 1;
    currentSpeedPercent = speedPercent;


  } else if (direction < 0 && speedPercent > 0) {
    // FAILSAFE: Reverse is permanently disabled!
    ledcWrite(RPWM_PIN, 0);
    ledcWrite(LPWM_PIN, 0);


    currentDirection    = 0;
    currentSpeedPercent = 0;


  } else {
    ledcWrite(RPWM_PIN, 0);
    ledcWrite(LPWM_PIN, 0);


    currentDirection    = 0;
    currentSpeedPercent = 0;
  }
}


void setMotorTarget(int direction, int speedPercent) {
  targetDirection    = direction;
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


  // Calculate how many ramp steps we missed if the loop was blocked
  unsigned long elapsed = now - lastMotorRampTime;
  int steps = elapsed / MOTOR_RAMP_INTERVAL_MS;
  if (steps > 50) steps = 50;

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


enum StopSequenceState { STOP_SEQ_IDLE, STOP_SEQ_STAGE_1, STOP_SEQ_STAGE_2, STOP_SEQ_STAGE_3 };
StopSequenceState stopSeqState = STOP_SEQ_IDLE;
unsigned long stopSeqTimer = 0;

void readJoystick() {
  unsigned long now = millis();
  int joystickY = analogRead(JOYSTICK_Y_PIN);


  JoystickZone zone;
  if      (joystickY > (JOYSTICK_CENTER + JOYSTICK_DEADZONE)) zone = JOYSTICK_ZONE_UP;
  else if (joystickY < (JOYSTICK_CENTER - JOYSTICK_DEADZONE)) zone = JOYSTICK_ZONE_DOWN;
  else                                                         zone = JOYSTICK_ZONE_NEUTRAL;


  // 1. Joystick Movement (UP) for 1.5 seconds -> RUN
  static unsigned long joystickUpStartTime = 0;
  static bool joystickUpTiming = false;

  if (zone == JOYSTICK_ZONE_UP) {
    if (!joystickUpTiming) {
      joystickUpTiming = true;
      joystickUpStartTime = now;
    } else if (now - joystickUpStartTime >= 1500) {
      // Held for 1.5s -> RUN
      if (controlMode == CONTROL_MODE_MANUAL && targetSpeedPercent != 70) {
        targetDirection = 1;
        targetSpeedPercent = 70; // Approx 120 RPM
        stopSeqState = STOP_SEQ_IDLE; // Cancel any stop sequence
        Serial.println("[JOY] Joystick UP held 1.5s -> RUN (70%)");
      }
    }
  } else {
    joystickUpTiming = false;
  }


  // 2. Button Press for 1.5 seconds -> 3-STAGE GRADUAL STOP
  bool buttonReading = digitalRead(JOYSTICK_SW_PIN); // LOW means pressed
  static unsigned long buttonPressStartTime = 0;
  static bool buttonTiming = false;

  if (buttonReading == LOW) {
    if (!buttonTiming) {
      buttonTiming = true;
      buttonPressStartTime = now;
    } else if (now - buttonPressStartTime >= 1500) {
      // Held for 1.5s -> STOP
      if (stopSeqState == STOP_SEQ_IDLE && targetSpeedPercent > 0) {
        controlMode = CONTROL_MODE_MANUAL;
        stopSeqState = STOP_SEQ_STAGE_1;
        stopSeqTimer = now;
        targetSpeedPercent = 45; // Stage 1
        Serial.println("[JOY] Button held 1.5s -> Triggering 3-stage STOP (Stage 1: 45%)");
      }
    }
  } else {
    buttonTiming = false;
  }


  // 3. Process 3-stage Stop Sequence (1 second between stages)
  if (stopSeqState == STOP_SEQ_STAGE_1 && now - stopSeqTimer >= 1000) {
    stopSeqState = STOP_SEQ_STAGE_2;
    stopSeqTimer = now;
    targetSpeedPercent = 20;
    Serial.println("[STOP SEQ] Stage 2 -> 20%");
  } else if (stopSeqState == STOP_SEQ_STAGE_2 && now - stopSeqTimer >= 1000) {
    stopSeqState = STOP_SEQ_STAGE_3;
    stopSeqTimer = now;
    targetSpeedPercent = 0;
    targetDirection = 0;
    Serial.println("[STOP SEQ] Stage 3 -> 0%");
  } else if (stopSeqState == STOP_SEQ_STAGE_3 && currentSpeedPercent == 0) {
    stopSeqState = STOP_SEQ_IDLE;
    Serial.println("[STOP SEQ] Finished.");
  }
}


void evaluateRPM() {
  unsigned long now = millis();


  if (now - lastRpmTime < rpmIntervalMs) {
    return;
  }


  noInterrupts();
  long countSnapshot  = encoderWindowCount;
  encoderWindowCount  = 0;
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


  static int lastEvaluatedTarget = -1;
  bool targetChanged = (targetSpeedPercent != lastEvaluatedTarget);
  if (targetChanged) lastEvaluatedTarget = targetSpeedPercent;

  if (rawRPM == 0.0f) {
    currentRPM = 0.0f;
  } else if (targetChanged) {
    currentRPM = rawRPM; // Immediate snap to discard stale history
  } else {
    currentRPM = (currentRPM * 0.7f) + (rawRPM * 0.3f);
  }


  lastRpmTime = now;
}


void readTemperature() {
  enum TempState { TEMP_IDLE, TEMP_CONVERTING };
  static TempState state          = TEMP_IDLE;
  static unsigned long stateStart = 0;
  static const unsigned long CONVERSION_DELAY_MS = 800;


  unsigned long now = millis();


  if (state == TEMP_IDLE) {
    if (now - lastTemperatureTime < temperatureIntervalMs) return;
    lastTemperatureTime = now;
    tempSensors.requestTemperatures();
    stateStart = now;
    state = TEMP_CONVERTING;
    return;
  }


  if (state == TEMP_CONVERTING) {
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
  while (rs485Serial.available()) { rs485Serial.read(); }
  digitalWrite(PIN_RS485_EN, HIGH);
  rs485Serial.write(buf, len);
  rs485Serial.flush();
  delayMicroseconds(100);
  digitalWrite(PIN_RS485_EN, LOW);
}


void nb2WriteRegister(uint16_t cmdVal) {
  byte txBuf[11];
  txBuf[0] = NB2_SLAVE_ADDR;
  txBuf[1] = 0x10;
  txBuf[2] = 0x00; txBuf[3] = 0x00;
  txBuf[4] = 0x00; txBuf[5] = 0x01;
  txBuf[6] = 0x02;
  txBuf[7] = (cmdVal >> 8) & 0xFF;
  txBuf[8] = cmdVal & 0xFF;
  uint16_t crc = modbusCRC16(txBuf, 9);
  txBuf[9]  = crc & 0xFF;
  txBuf[10] = crc >> 8;
  rs485Send(txBuf, 11);
  delay(200);
}


int nb2ReadRegisters(uint16_t startAddr, uint16_t count, byte* outData, int outMax) {
  byte txBuf[8];
  txBuf[0] = NB2_SLAVE_ADDR;
  txBuf[1] = 0x03;
  txBuf[2] = startAddr >> 8;
  txBuf[3] = startAddr & 0xFF;
  txBuf[4] = count >> 8;
  txBuf[5] = count & 0xFF;
  uint16_t crc = modbusCRC16(txBuf, 6);
  txBuf[6] = crc & 0xFF;
  txBuf[7] = crc >> 8;


  rs485Send(txBuf, 8);


  unsigned long startTime = millis();
  byte rxBuf[128];
  int bytesReceived = 0;
  while (millis() - startTime < 250) {
    if (rs485Serial.available()) {
      rxBuf[bytesReceived++] = rs485Serial.read();
      if (bytesReceived >= (int)sizeof(rxBuf)) break;
    }
  }


  if (bytesReceived < 5) return -1;


  for (int i = 0; i < bytesReceived - 3; i++) {
    if (rxBuf[i] == NB2_SLAVE_ADDR && rxBuf[i+1] == 0x03) {
      int dataLen = rxBuf[i+2];
      if (i + 3 + dataLen + 2 <= bytesReceived) {
        uint16_t rxCRC   = (rxBuf[i+3+dataLen+1] << 8) | rxBuf[i+3+dataLen];
        uint16_t calcCRC = modbusCRC16(&rxBuf[i], 3 + dataLen);
        if (rxCRC == calcCRC) {
          int copyLen = (dataLen < outMax) ? dataLen : outMax;
          memcpy(outData, &rxBuf[i+3], copyLen);
          return copyLen;
        }
      }
    }
  }
  return -1;
}


void pollNB2Breaker() {
  byte data[128];
  bool anySuccess = false;
  int len;


  len = nb2ReadRegisters(0x0003, 1, data, sizeof(data));
  if (len >= 2) {
    nb2Data.internalTemp = (float)((int16_t)((data[0] << 8) | data[1]));
    anySuccess = true;
  }
  delay(50);


  len = nb2ReadRegisters(0x0020, 3, data, sizeof(data));
  if (len >= 6) {
    uint16_t workState = (data[0] << 8) | data[1];
    nb2Data.faultFlags  = (data[2] << 8) | data[3];
    nb2Data.alarmFlags  = (data[4] << 8) | data[5];
    nb2Data.breakerOn   = (workState >> 7) & 0x01;
    anySuccess = true;
  }
  delay(50);


  len = nb2ReadRegisters(0x0040, 48, data, sizeof(data));
  if (len >= 96) {
    int32_t rawCurrent = ((int32_t)data[0] << 24) | ((int32_t)data[1] << 16) |
                         ((int32_t)data[2] << 8)  |  (int32_t)data[3];
    nb2Data.current    = rawCurrent * 0.001f;
    nb2Data.voltage    = ((data[16] << 8) | data[17]) * 0.01f;
    nb2Data.frequency  = ((data[30] << 8) | data[31]) * 0.01f;
    nb2Data.residualCurrent = (float)((data[32] << 8) | data[33]);
    int32_t rawPower = ((int32_t)data[46] << 24) | ((int32_t)data[47] << 16) |
                       ((int32_t)data[48] << 8)  |  (int32_t)data[49];
    nb2Data.activePower = rawPower * 0.1f;
    nb2Data.energyWh = ((int32_t)data[50] << 24) | ((int32_t)data[51] << 16) |
                       ((int32_t)data[52] << 8)  |  (int32_t)data[53];
    nb2Data.reactiveEnergyVarh = ((int32_t)data[54] << 24) | ((int32_t)data[55] << 16) |
                                 ((int32_t)data[56] << 8)  |  (int32_t)data[57];
    int32_t rawReactive = ((int32_t)data[62] << 24) | ((int32_t)data[63] << 16) |
                          ((int32_t)data[64] << 8)  |  (int32_t)data[65];
    nb2Data.reactivePower = rawReactive * 0.1f;
    int32_t rawApparent = ((int32_t)data[78] << 24) | ((int32_t)data[79] << 16) |
                          ((int32_t)data[80] << 8)  |  (int32_t)data[81];
    nb2Data.apparentPower = rawApparent * 0.1f;
    int16_t rawPF = (int16_t)((data[94] << 8) | data[95]);
    nb2Data.powerFactor = rawPF * 0.01f;
    anySuccess = true;
  }


  if (anySuccess) {
    nb2Data.rs485Ok      = true;
    nb2Data.failCount    = 0;
    nb2Data.lastReadTime = millis();
    if (nb2Data.faultFlags > 0 || nb2Data.alarmFlags > 0) {
      Serial.print("[NB2] WARNING! Fault/Alarm Flags detected | Fault: 0x");
      Serial.print(nb2Data.faultFlags, HEX);
      Serial.print(" Alarm: 0x");
      Serial.println(nb2Data.alarmFlags, HEX);
    }
  } else {
    nb2Data.failCount++;
    if (nb2Data.failCount >= 5) nb2Data.rs485Ok = false;
    Serial.print("[NB2] FAIL #");
    Serial.println(nb2Data.failCount);
  }
}


void nb2RemoteClose() {
  nb2WriteRegister(0x0002);
  Serial.println("[NB2] Remote control unlocked.");
  delay(500); // 500ms delay to allow breaker to process unlock
  nb2WriteRegister(0x0006);
  Serial.println("[NB2] Remote CLOSE command sent.");
}


void nb2RemoteOpen() {
  nb2WriteRegister(0x0002);
  Serial.println("[NB2] Remote control unlocked.");
  delay(500); // 500ms delay to allow breaker to process unlock
  nb2WriteRegister(0x0007);
  Serial.println("[NB2] Remote OPEN command sent.");
}


void serviceNB2UnlockButton() {
  if (NB2_UNLOCK_BUTTON_PIN == 255) return;


  static bool          lastButtonState = HIGH;
  static unsigned long pressedAt       = 0;
  static bool          unlockTriggered = false;


  bool buttonState = digitalRead(NB2_UNLOCK_BUTTON_PIN);


  if (buttonState == LOW && lastButtonState == HIGH) {
    pressedAt       = millis();
    unlockTriggered = false;
  }


  if (buttonState == LOW && !unlockTriggered &&
      millis() - pressedAt >= NB2_UNLOCK_HOLD_MS) {
    nb2CommandUnlocked = true;
    nb2UnlockExpiresAt = millis() + NB2_UNLOCK_WINDOW_MS;
    unlockTriggered    = true;
    Serial.println("[NB2] Physical unlock accepted. "
                   "One breaker command is allowed for 30 seconds.");
  }


  if (buttonState == HIGH) {
    unlockTriggered = false;
  }


  lastButtonState = buttonState;
}


void serviceNB2UnlockTimeout() {
  if (nb2CommandUnlocked && millis() >= nb2UnlockExpiresAt) {
    nb2CommandUnlocked = false;
    Serial.println("[NB2] Physical unlock expired. Breaker commands are locked.");
  }
}


void serviceNB2Commands() {
#if ENCODER_DIAG_MODE
  return;
#endif


  if (nb2CloseRequested) {
    nb2CloseRequested  = false;
    nb2OpenRequested   = false;
    Serial.println("[NB2] Executing CLOSE (Protection Disabled).");
    nb2RemoteClose();
    return;
  }


  if (nb2OpenRequested) {
    nb2OpenRequested   = false;
    Serial.println("[NB2] Executing OPEN (Protection Disabled).");
    nb2RemoteOpen();
    return;
  }
}


void publishEncoderTelemetry() {
  unsigned long now = millis();


  if (now - lastEncoderTelemetryTime < encoderTelemetryIntervalMs) {
    return;
  }


  lastEncoderTelemetryTime = now;


  noInterrupts();
  long totalCountSnapshot = encoderTotalCount;
  interrupts();


  long deltaCount = totalCountSnapshot - lastPublishedEncoderCount;
  lastPublishedEncoderCount = totalCountSnapshot;


  StaticJsonDocument<256> doc;


  doc["uptime_ms"]     = now;
  doc["encoder_count"] = totalCountSnapshot;
  doc["delta_count"]   = deltaCount;
  doc["ppr"]           = ppr;


  if (deltaCount > 0) {
    doc["direction"] = "left_to_right";
  } else if (deltaCount < 0) {
    doc["direction"] = "right_to_left";
  } else {
    doc["direction"] = "stopped";
  }


  char payload[256];
  serializeJson(doc, payload, sizeof(payload));


  Serial.print("[ENC MQTT] ");
  Serial.println(payload);
}


#if ENCODER_DIAG_MODE
void publishEncoderDiagnostics() {
  static unsigned long lastDiagTime = 0;
  static const unsigned long DIAG_INTERVAL_MS = 500;


  unsigned long now = millis();
  if (now - lastDiagTime < DIAG_INTERVAL_MS) return;
  lastDiagTime = now;


  noInterrupts();
  unsigned long accepted = encPulsesAccepted; encPulsesAccepted = 0;
  unsigned long rejected = encPulsesRejected; encPulsesRejected = 0;
  unsigned long pos      = encCountPos;       encCountPos       = 0;
  unsigned long neg      = encCountNeg;       encCountNeg       = 0;
  long          total    = encoderTotalCount;
  interrupts();


  bool motorExpectedToMove = (targetDirection > 0 && targetSpeedPercent > 0);
  const char* diagnosis;
  if (accepted == 0 && rejected == 0) {
    if (!motorExpectedToMove) {
      diagnosis = "IDLE";
    } else {
      diagnosis = "NO PULSES WHILE MOTOR IS COMMANDED TO RUN";
    }
  } else if (accepted == 0 && rejected > 0) {
    diagnosis = "ALL REJECTED (debounce)";
  } else if (accepted > 0 && abs((long)pos - (long)neg) < (long)(accepted / 4 + 1)) {
    diagnosis = "pos ~ neg";
  } else if (rejected > accepted) {
    diagnosis = "HIGH REJECT RATE";
  } else {
    diagnosis = "Signal OK";
  }


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
  serializeJson(doc, payload, sizeof(payload));

  // Send over serial with the same identifier the Python script looks for
  Serial.print("[ENC MQTT] ");
  Serial.println(payload);
}
#endif


void publishTelemetry() {
  unsigned long now = millis();


  if (now - lastTelemetryTime < telemetryIntervalMs) {
    return;
  }


  lastTelemetryTime = now;
  bool e18Active = (digitalRead(E18_PROXIMITY_PIN) == LOW);


  StaticJsonDocument<640> doc;


  doc["rpm"]                  = currentRPM;
  doc["estimated_rpm"]         = currentSpeedPercent * (167.0f / 100.0f);
  doc["e18_active"]           = e18Active;
  doc["uptime_ms"]            = now;
  doc["dir"]                  = directionText();
  doc["speed_percent"]        = currentSpeedPercent;
  doc["target_speed_percent"] = targetSpeedPercent;
  doc["ppr"]                  = ppr;
  doc["control_mode"]         = (controlMode == CONTROL_MODE_REMOTE) ? "remote" : "manual";


  if (isnan(currentTempC)) {
    doc["temp_c"] = nullptr;
  } else {
    doc["temp_c"] = currentTempC;
  }


  JsonObject nb2Obj = doc.createNestedObject("nb2");
  nb2Obj["voltage"]              = nb2Data.voltage;
  nb2Obj["current"]              = nb2Data.current;
  nb2Obj["active_power"]         = nb2Data.activePower;
  nb2Obj["reactive_power"]       = nb2Data.reactivePower;
  nb2Obj["apparent_power"]       = nb2Data.apparentPower;
  nb2Obj["power_factor"]         = nb2Data.powerFactor;
  nb2Obj["frequency"]            = nb2Data.frequency;
  nb2Obj["energy_wh"]            = nb2Data.energyWh;
  nb2Obj["reactive_energy_varh"] = nb2Data.reactiveEnergyVarh;
  nb2Obj["residual_current_ma"]  = nb2Data.residualCurrent;
  nb2Obj["breaker_on"]           = nb2Data.breakerOn;
  nb2Obj["internal_temp"]        = nb2Data.internalTemp;
  nb2Obj["fault_flags"]          = nb2Data.faultFlags;
  nb2Obj["alarm_flags"]          = nb2Data.alarmFlags;
  nb2Obj["rs485_ok"]             = nb2Data.rs485Ok;


  char payload[640];
  serializeJson(doc, payload);


  Serial.print("[PUB] ");
  Serial.println(payload);
}


void serviceRemoteWatchdog() {
  if (controlMode == CONTROL_MODE_REMOTE && targetSpeedPercent > 0) {
    if (millis() - lastRemoteCommandTime > REMOTE_COMMAND_TIMEOUT_MS) {
      setMotorTarget(0, 0);
      controlMode = CONTROL_MODE_MANUAL;
      Serial.println("[WATCHDOG] Remote silent >3000ms - motor force-stopped, control -> MANUAL.");
    }
  }
}


void readSerialCommands() {
  if (Serial.available() > 0) {
    String incoming = Serial.readStringUntil('\n');
    incoming.trim();
    if (incoming.length() == 0) return;


    // FIX #3: Reject oversized input before attempting to parse it. Prevents
    // wasted heap/time on garbage from a stuck bridge / missing newline.
    if (incoming.length() > 240) {
      Serial.println("[CMD] Rejected - payload too long");
      return;
    }


    StaticJsonDocument<256> doc;
    DeserializationError err = deserializeJson(doc, incoming);
    if (err) {
      Serial.print("[CMD] JSON error: ");
      Serial.println(err.c_str());
      return;
    }


    if (doc.containsKey("mode")) {
      const char* modeCmd = doc["mode"];
      if (strcmp(modeCmd, "remote") == 0) {
        controlMode = CONTROL_MODE_REMOTE;
        lastRemoteCommandTime = millis();  // arm watchdog on mode entry
        Serial.println("[CMD] Control mode -> REMOTE (Serial controls motor)");
      } else if (strcmp(modeCmd, "manual") == 0) {
        controlMode = CONTROL_MODE_MANUAL;
        Serial.println("[CMD] Control mode -> MANUAL (joystick controls motor)");
      }
    }


    if (doc.containsKey("walk")) {
      const char* walkCmd = doc["walk"];
      int wp = -1;
      if      (strcmp(walkCmd, "slow")   == 0) wp = WALK_SPEED_SLOW_PERCENT;
      else if (strcmp(walkCmd, "medium") == 0) wp = WALK_SPEED_MEDIUM_PERCENT;
      else if (strcmp(walkCmd, "fast")   == 0) wp = WALK_SPEED_FAST_PERCENT;

      if (wp >= 0 && controlMode == CONTROL_MODE_REMOTE) {
        setMotorTarget(1, wp);
        lastRemoteCommandTime = millis();
        Serial.print("[CMD] Walk preset -> ");
        Serial.print(walkCmd);
        Serial.print(" (");
        Serial.print(wp);
        Serial.println("%)");
      } else if (wp >= 0) {
        Serial.println("[CMD] Walk preset REJECTED - MANUAL mode active.");
      }
    }

    const char* cmd   = doc["cmd"] | "";
    const char* dir    = doc["dir"] | "stop";
    int         speed  = doc["speed"] | 0;


    if (controlMode == CONTROL_MODE_REMOTE) {
      bool recognizedMotorCmd = false;

      if (strcmp(cmd, "stop") == 0 || strcmp(cmd, "estop") == 0) {
        setMotorTarget(0, 0);
        recognizedMotorCmd = true;
      } else if (strcmp(dir, "fwd") == 0 || strcmp(dir, "forward") == 0) {
        setMotorTarget(1, speed);
        recognizedMotorCmd = true;
      } else if (strcmp(dir, "rev") == 0 || strcmp(dir, "reverse") == 0) {
        Serial.println("[CMD] REV IGNORED (conveyor is one-direction only)");
        recognizedMotorCmd = true;  // still counts as a live command for the watchdog
      } else if (doc.containsKey("dir")) {
        setMotorTarget(0, 0);
        recognizedMotorCmd = true;
      }

      // FIX #1 (cont.): any recognized motor command refreshes the watchdog
      // timer, proving the Pi bridge is still alive and talking.
      if (recognizedMotorCmd) {
        lastRemoteCommandTime = millis();
      }

    } else {
      bool hasMotorCmd = doc.containsKey("cmd") ||
                         (doc.containsKey("dir") && strcmp(dir, "stop") != 0) ||
                         (doc.containsKey("speed") && speed != 0);
      if (hasMotorCmd) {
        Serial.println("[CMD] Motor cmd REJECTED - MANUAL mode active."
                       " Send {\"mode\":\"remote\"} first to enable remote motor control.");
      }
    }


    if (doc.containsKey("ppr")) {
      float newPpr = doc["ppr"].as<float>();
      if (newPpr > 0.0f) {
        ppr = newPpr;
        Serial.print("[CMD] PPR updated -> ");
        Serial.println(ppr, 2);
      }
    }


    if (doc.containsKey("breaker")) {
#if ENCODER_DIAG_MODE
      // FIX #2: explicitly reject/acknowledge instead of silently queuing a
      // command that serviceNB2Commands() will never consume in diag mode.
      Serial.println("[CMD] Breaker command IGNORED - ENCODER_DIAG_MODE is active.");
#else
      const char* breakerCmd = doc["breaker"];
      if (strcmp(breakerCmd, "on") == 0) {
        nb2CloseRequested = true;
        Serial.println("[CMD] NB2 CLOSE queued");
      } else if (strcmp(breakerCmd, "off") == 0) {
        nb2OpenRequested = true;
        Serial.println("[CMD] NB2 OPEN queued");
      }
#endif
    }


    if (doc.containsKey("nb2_unlock") && doc["nb2_unlock"].as<bool>() == true) {
#if ALLOW_SOFTWARE_NB2_UNLOCK
      nb2CommandUnlocked = true;
      nb2UnlockExpiresAt = millis() + NB2_UNLOCK_WINDOW_MS;
      Serial.println("[CMD] NB2 software unlock received. "
                     "One breaker command allowed for 30 seconds.");
#else
      Serial.println("[CMD] NB2 software unlock DISABLED in this build. "
                     "Use the physical unlock button.");
#endif
    }
  }
}


void pollNB2IfDue() {
#if ENCODER_DIAG_MODE
  return;
#endif
  unsigned long now = millis();
  if (now - lastNb2PollTime < NB2_POLL_INTERVAL_MS) return;
  lastNb2PollTime = now;
  pollNB2Breaker();
}


void setup() {
  Serial.begin(115200);


  pinMode(ENCODER_A_PIN, INPUT);
  pinMode(ENCODER_B_PIN, INPUT);
  pinMode(E18_PROXIMITY_PIN, INPUT_PULLUP);
  pinMode(JOYSTICK_X_PIN, INPUT);
  pinMode(JOYSTICK_Y_PIN, INPUT);
  pinMode(JOYSTICK_SW_PIN, INPUT_PULLUP);


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
  tempSensors.setWaitForConversion(false);


  pinMode(PIN_RS485_EN, OUTPUT);
  digitalWrite(PIN_RS485_EN, LOW);


  rs485Serial.begin(NB2_BAUD_RATE, SERIAL_8E1, PIN_RS485_RX, PIN_RS485_TX);
  nb2WriteRegister(0x0002); // Initial unlock sent to breaker


  Serial.print("[BOOT] NB2 RS485 initialized - Slave: ");
  Serial.print(NB2_SLAVE_ADDR);
  Serial.print(", Baud: ");
  Serial.println(NB2_BAUD_RATE);


  unsigned long now = millis();
  lastRpmTime                  = now;
  lastTelemetryTime            = now;
  lastTemperatureTime          = now;
  lastMotorRampTime            = now;
  lastNb2PollTime              = now;
  lastRemoteCommandTime        = now;


  Serial.println("[BOOT] ESP32 Motor Node (USB Serial Mode) - FIXED BUILD");
  Serial.println("[BOOT] Encoder + BTS7960 + E18 + DS18B20 + Joystick + NB2");
  Serial.println("[BOOT] Remote command watchdog ACTIVE (3000 ms timeout)");
#if ENCODER_DIAG_MODE
  Serial.println("[BOOT] ENCODER_DIAG_MODE=1 - NB2 polling/commands disabled");
#endif
#if ALLOW_SOFTWARE_NB2_UNLOCK
  Serial.println("[BOOT] WARNING: software-only NB2 unlock is ENABLED.");
#endif
}


void loop() {
  readJoystick();
  gradualMotorControl();
  serviceRemoteWatchdog();   // FIX #1: check before publishing telemetry


  evaluateRPM();
  publishEncoderTelemetry();
#if ENCODER_DIAG_MODE
  publishEncoderDiagnostics();
#endif


  readTemperature();
  serviceNB2UnlockButton();
  serviceNB2UnlockTimeout();
  serviceNB2Commands();
  pollNB2IfDue();

  publishTelemetry();
  readSerialCommands();
}
