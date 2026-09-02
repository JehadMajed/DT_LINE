// ============================================================================
// SYSTEM CORE: BABYLON.JS DIGITAL TWIN ENGINE (INDUSTRIAL GRADE)
// Redesign: Professional Industrial SaaS — v9
// ============================================================================

// ============================================================================
// DOM REFERENCES
// ============================================================================
const UI = {
    rpm: document.getElementById('tele-rpm'),
    amp: document.getElementById('tele-amp'),
    volt: document.getElementById('tele-volt'),
    status: document.getElementById('connection-status'),
    statusPill: document.getElementById('hw-status-badge'),
    logBox: document.getElementById('system-log'),
    fps: document.getElementById('fps-counter'),
    btnRun: document.getElementById('btn-run'),
    btnStop: document.getElementById('btn-stop'),
    loader: document.getElementById('loading-screen'),
    power: document.getElementById('tele-power'),
    pf: document.getElementById('tele-pf'),
    speed: document.getElementById('tele-speed'),
    count: document.getElementById('tele-count'),
    sensorLed: document.getElementById('sensor-led'),
    sensorStatus: document.getElementById('sensor-status'),
    inputRpm: document.getElementById('input-rpm'),
    teleProximity: document.getElementById('tele-proximity'),
    teleTempVal: document.getElementById('tele-temp-val'),
    btnSetRpm: document.getElementById('btn-set-rpm'),
    btnWalkSlow: document.getElementById('btn-walk-slow'),
    btnWalkMedium: document.getElementById('btn-walk-medium'),
    btnWalkFast: document.getElementById('btn-walk-fast'),
    walkSettleBanner: document.getElementById('walk-settle-banner'),

    // Executive Overview (Business KPIs)
    stateText: document.getElementById('tele-state-text'),
    rate: document.getElementById('tele-rate'),
    energy: document.getElementById('tele-energy'),
    cost: document.getElementById('tele-cost'),
    oee: document.getElementById('tele-oee'),
    savings: document.getElementById('tele-savings'),
    carbon: document.getElementById('tele-carbon'),

    // Simulation Tab Controls
    simVolt: document.getElementById('sim-input-volt'),
    simLoad: document.getElementById('sim-input-load'),
    btnSimRun: document.getElementById('btn-sim-run'),
    btnSimStop: document.getElementById('btn-sim-stop'),
    btnSimSetVolt: document.getElementById('btn-sim-set-volt'),
    btnSimSetLoad: document.getElementById('btn-sim-set-load'),

    // Component Inspector
    inspectorSection: document.getElementById('inspector-section'),
    inspectorIdle: document.getElementById('inspector-idle'),
    inspectorDetails: document.getElementById('inspector-details'),
    inspectName: document.getElementById('inspect-name'),
    inspectCategory: document.getElementById('inspect-category'),
    inspectSpecs: document.getElementById('inspect-specs'),
    inspectStatus: document.getElementById('inspect-status'),
    btnFocusPart: document.getElementById('btn-focus-part'),

    // AI Predictive
    aiRul: document.getElementById('ai-rul'),
    aiAnomaly: document.getElementById('ai-anomaly'),
    aiEfficiency: document.getElementById('ai-efficiency'),
    aiService: document.getElementById('ai-service'),

    // Camera & Controls
    selectCameraMode: document.getElementById('select-camera-mode'),
    btnEstop: document.getElementById('btn-estop'),

    // Log panel collapsible
    logPanelSection: document.getElementById('log-panel-section'),
    logToggle: document.getElementById('log-toggle'),

    // Hardware / MQTT
    hwBadge: document.getElementById('hw-mode-badge'),
    btnMqttConnect: document.getElementById('btn-mqtt-connect'),
    statusEsp32: document.getElementById('status-esp32'),
    statusMotor: document.getElementById('status-motor'),
    statusProximity: document.getElementById('status-proximity'),
    statusTemp: document.getElementById('status-temp'),
    statusWeight: document.getElementById('status-weight'),
    statusVibration: document.getElementById('status-vibration'),
    // NB2 CHINT Breaker (RS485)
    statusBreaker: document.getElementById('status-breaker'),
    statusRs485: document.getElementById('status-rs485'),
    nb2Voltage: document.getElementById('nb2-voltage'),
    nb2Current: document.getElementById('nb2-current'),
    nb2Power: document.getElementById('nb2-power'),
    nb2PF: document.getElementById('nb2-pf'),
    nb2Frequency: document.getElementById('nb2-frequency'),
    nb2Energy: document.getElementById('nb2-energy'),
    nb2Apparent: document.getElementById('nb2-apparent'),
    nb2Reactive: document.getElementById('nb2-reactive'),
    nb2Temp: document.getElementById('nb2-temp'),
    nb2Residual: document.getElementById('nb2-residual'),
    nb2BreakerState: document.getElementById('nb2-breaker-state'),
    nb2FaultBar: document.getElementById('nb2-fault-bar'),
    nb2FaultText: document.getElementById('nb2-fault-text'),
    btnNb2On: document.getElementById('btn-nb2-on'),
    btnNb2Off: document.getElementById('btn-nb2-off'),
    btnNb2Unlock: document.getElementById('btn-nb2-unlock'),
    nb2UnlockBadge: document.getElementById('nb2-unlock-badge'),
    nb2UnlockCountdown: document.getElementById('nb2-unlock-countdown'),
    nb2UnlockTimer: document.getElementById('nb2-unlock-timer'),
    btnHardOff: document.getElementById('btn-hard-off'),
};

// ============================================================================
// HARDWARE STATE
// ============================================================================
const HW = {
    connected: false,
    client: null,
    running: false,
    lastProxState: false,
};

function updateNb2Buttons() {
    const rs485Ok = TEL.nb2Rs485Ok;

    // Overview tab buttons — still gated by HW + RS485
    if (UI.btnNb2On) UI.btnNb2On.disabled = !HW.connected || !rs485Ok;
    if (UI.btnNb2Off) UI.btnNb2Off.disabled = !HW.connected || !rs485Ok;

    // 3D Model tab buttons — ALWAYS enabled so operator can interact.
    // A confirm() dialog on the OFF button prevents accidental trips.
    // Visual opacity reflects connectivity state without blocking access.
    const btnOnModel = document.getElementById('btn-nb2-on-model');
    const btnOffModel = document.getElementById('btn-nb2-off-model');
    const btnUnlockModel = document.getElementById('btn-nb2-unlock-model');
    const dimVal = HW.connected ? '1' : '0.55';
    if (btnOnModel) { btnOnModel.disabled = false; btnOnModel.style.opacity = dimVal; }
    if (btnOffModel) { btnOffModel.disabled = false; btnOffModel.style.opacity = dimVal; }
    if (btnUnlockModel) { btnUnlockModel.disabled = false; btnUnlockModel.style.opacity = dimVal; }
}

// ── MQTT Replica State ─────────────────────────────────────────────────────
// Source of truth for the 3D Model tab. ONLY written by parseTelemetry().
// No user interaction (button click, input change) should ever write here.
const MQTT_STATE = {
    rpm: 0,           // live RPM from ESP32 encoder
    isRunning: false, // motor is physically running
    proxDetected: false,  // current E18 proximity state
    pieceCount: 0,    // rising-edge counted pieces
    speedPercent: 0,  // PWM% sent by ESP32
    beltSpeed: 0,     // computed m/s via rpmToBeltSpeed()
    lastPacketTime: 0,// Date.now() of last parseTelemetry() call
    isStale: false,   // true when > 3 s since last packet
    // NB2 Breaker replica state
    nb2: {
        voltage: 0, current: 0, activePower: 0, reactivePower: 0,
        apparentPower: 0, powerFactor: 0, frequency: 0, energyWh: 0,
        residualCurrentMa: 0, internalTemp: 0, breakerOn: false,
        faultFlags: 0, alarmFlags: 0, rs485Ok: false,
    },
};

// Active tab identifier — render loop uses this to pick the correct RPM source.
// 'model'  → MQTT_STATE.rpm only (real replica)
// 'simulation' → SIM.rpm only (sandbox)
// others  → frozen
let currentActiveTab = 'overview';

// Global optimization state
const optState = {
    ecoMode: true,
};

// ── MQTT Configuration ─────────────────────────────────────────────────────
// Adjust brokerUrl if your Mosquitto WebSocket port differs from 9001.
// ── MQTT Configuration ─────────────────────────────────────────────────────
// Adjust brokerUrl if your Mosquitto WebSocket port differs from 9001.
// Three interchangeable streaming plans. The bridge (serial_mqtt_bridge.py) publishes
// telemetry to whichever of these are enabled on the Pi side; pick the matching one here
// with the "select-broker-profile" dropdown in the Overview tab.
const BROKER_PROFILES = {
    // Plan A: own Mosquitto broker on the Pi, exposed publicly via `cloudflared tunnel`.
    // Update this URL every time a Quick Tunnel restarts (or switch to a Named Tunnel
    // with a stable hostname once you have a domain in Cloudflare).
    cloudflare: {
        label: 'Cloudflare Tunnel',
        brokerUrl: 'wss://gaming-destination-where-transmission.trycloudflare.com',
        options: {},
    },
    // Plan B: HiveMQ Cloud free-tier cluster. Fill in your cluster's WSS URL + credentials
    // from the HiveMQ Cloud console (Free Tier -> Cluster -> "Access Management").
    hivemq: {
        label: 'HiveMQ Cloud',
        brokerUrl: 'wss://dcec0602f95f444bb3fe2bcdfd5efc38.s1.eu.hivemq.cloud:8884/mqtt',
        options: { username: 'Lamps', password: 'Aa448866' },
    },
    // Plan C: EMQX Cloud Serverless free-tier deployment. Fill in from the EMQX Cloud
    // console (Deployment -> Overview -> WebSocket connection details).
    emqx: {
        label: 'EMQX Cloud',
        brokerUrl: 'wss://xb6e165f.ala.asia-southeast1.emqxsl.com:8084/mqtt',
        options: { username: 'Lamps', password: 'Aa448866' },
    },
};

const MQTT_CFG = {
    topicSub: 'digital_twin/motor/telemetry', // ESP32 -> app
    topicCmd: 'digital_twin/motor/command',   // app -> ESP32
    clientId: 'dt_twin_' + Math.random().toString(16).slice(2, 8),
    get activeProfile() {
        const sel = document.getElementById('select-broker-profile');
        return BROKER_PROFILES[(sel && sel.value) || 'hivemq'];
    },
    get brokerUrl() {
        return this.activeProfile.brokerUrl;
    },
};

// ============================================================================
// SIMULATION STATE
// ============================================================================
// ── Motor Physical Constants (XYT-JGB37-555-1250, 12V DC, 1:30 ratio) ─────────
// Empirical measurements: 167 RPM → 8s/loop, 30 RPM → 80s/loop, belt = 1.02 m
// UV_CONSTANT calibrated so that at maxRpm (167), one UV cycle = 8 seconds exactly.
// Two-point belt speed interpolation captures real non-linearity at low RPM.
const MOTOR = {
    ratedVoltage: 12.0,      // V  — motor nameplate voltage
    noLoadCurrent: 0.2,      // A  — from datasheet (no load)
    ratedCurrent: 1.2,      // A  — from datasheet (rated load)
    stallCurrent: 6.5,      // A  — from datasheet (stall)
    ratedTorque: 6.0,      // Kgf·cm
    stallTorque: 16.0,     // Kgf·cm
    beltLength: 1.02,     // m  — real PVC belt loop circumference
    // Empirical calibration points: [motorRPM, loopsPerSecond]
    cal: [
        { rpm: 30, lps: 1 / 80.0 },   // measured: 80s per loop
        { rpm: 167, lps: 1 / 8.0 },   // measured: 8s per loop
    ],
};

// UV_CONSTANT:  rpm / UV_CONSTANT × deltaTimeMs = UV offset per frame
// Derived: 167 RPM × 8000 ms = 1,336,000 (one UV cycle in exactly 8 s at max RPM)
const BELT_UV_CONSTANT = 1336000;

// Returns real belt surface speed (m/s) calibrated: 167 RPM = 0.1275 m/s
function rpmToBeltSpeed(rpm) {
    if (!rpm || rpm <= 0) return 0;
    return (rpm / 167.0) * 0.1275;
}

const SIM = {
    isRunning: false,
    rpm: 0,
    targetRpm: 0,
    maxRpm: 167,
    current: 0,
    voltage: MOTOR.ratedVoltage,
    interval: null,
    pieceCount: 0,
    faults: {
        motorOverheat: false,
        looseScrew: false,
        beltOverload: false,
    },
    motorTemp: 24.3,
    selectedMesh: null,
    cameraMode: 'orbit',

    // New Simulation Parameters
    loadKg: 0.0,
    energyUsedKwh: 0.0,
    costUSD: 0.0,
    lastProxState: false,
};

// ============================================================================
// TELEMETRY DOUBLE-BUFFER
// Values are written here by the simulation loop and flushed to the DOM
// by a dedicated requestAnimationFrame UI loop — decoupling render rates.
// ============================================================================
const TEL = {
    rpm: '0.0',
    amp: '0.00',
    volt: '12.0',  // 12V motor
    power: '0.000',
    pf: '0.85',
    speed: '0.00',
    count: '0',
    rul: '142.5 d',
    anomaly: '0.8%',
    anomalyColor: 'var(--text-secondary)',
    efficiency: '94.2%',
    service: 'Nominal',
    serviceColor: 'var(--text-label)',
    fps: '0',

    // Overview KPIs (Non-technical / Business results)
    stateText: 'Standby',
    stateColor: 'var(--status-ok)',
    rate: '0.0',
    energy: '0.000',
    cost: '0.00',
    oee: '0.0%',
    savings: '0.0',
    carbon: '0.00',
    proximity: '--',
    tempVal: '--',

    // NB2 Breaker telemetry buffer
    nb2Voltage: '--',
    nb2Current: '--',
    nb2Power: '--',
    nb2PF: '--',
    nb2Frequency: '--',
    nb2Energy: '--',
    nb2Apparent: '--',
    nb2Reactive: '--',
    nb2Temp: '--',
    nb2Residual: '--',
    nb2BreakerOn: false,
    nb2FaultFlags: 0,
    nb2AlarmFlags: 0,
    nb2Rs485Ok: false,

    // Dirty flag — only flush when data changed
    dirty: false,
};

// ============================================================================
// LOG SYSTEM — context-aware, auto-expands on faults
// ============================================================================
function addLog(msg, type = 'info') {
    const time = new Date().toLocaleTimeString('en-US', { hour12: false });
    const div = document.createElement('div');
    div.className = `log-entry ${type}`;
    div.innerHTML = `<span class="timestamp">[${time}]</span> ${msg}`;

    if (UI.logBox) {
        UI.logBox.appendChild(div);
        UI.logBox.scrollTop = UI.logBox.scrollHeight;
    }

    // Auto-expand collapsed log on critical/error/warning events
    if ((type === 'error' || type === 'warning') && UI.logPanelSection) {
        if (UI.logPanelSection.classList.contains('collapsed')) {
            UI.logPanelSection.classList.remove('collapsed');
            UI.logPanelSection.classList.add('alert-flash');
            setTimeout(() => UI.logPanelSection.classList.remove('alert-flash'), 2000);
        }
    }
}

// ============================================================================
// DOM FLUSH — batched RAF loop, decoupled from Babylon render loop
// ============================================================================
function flushTelemetryToDOM() {
    if (TEL.dirty) {
        if (UI.rpm) UI.rpm.textContent = TEL.rpm;
        if (UI.amp) UI.amp.textContent = TEL.amp;
        if (UI.volt) UI.volt.textContent = TEL.volt;
        if (UI.power) UI.power.textContent = TEL.power;
        if (UI.pf) UI.pf.textContent = TEL.pf;
        if (UI.speed) UI.speed.textContent = TEL.speed;
        if (UI.count) UI.count.textContent = TEL.count;
        if (UI.fps) UI.fps.textContent = TEL.fps;
        if (UI.aiRul) UI.aiRul.textContent = TEL.rul;
        if (UI.aiEfficiency) UI.aiEfficiency.textContent = TEL.efficiency;
        if (UI.aiAnomaly) {
            UI.aiAnomaly.textContent = TEL.anomaly;
            UI.aiAnomaly.style.color = TEL.anomalyColor;
        }
        if (UI.aiService) {
            UI.aiService.textContent = TEL.service;
            UI.aiService.style.color = TEL.serviceColor;
        }

        // New KPIs
        if (UI.stateText) {
            UI.stateText.textContent = TEL.stateText;
            UI.stateText.style.color = TEL.stateColor;
        }
        if (UI.rate) UI.rate.textContent = TEL.rate;
        if (UI.energy) UI.energy.textContent = TEL.energy;
        if (UI.cost) UI.cost.textContent = TEL.cost;
        if (UI.oee) UI.oee.textContent = TEL.oee;
        if (UI.savings) UI.savings.textContent = TEL.savings;
        if (UI.carbon) UI.carbon.textContent = TEL.carbon;
        if (UI.teleProximity) {
            UI.teleProximity.textContent = TEL.proximity;
            if (TEL.proximity === 'Detected') {
                UI.teleProximity.style.color = 'var(--status-ok)';
            } else {
                UI.teleProximity.style.color = 'var(--text-primary)';
            }
        }
        if (UI.teleTempVal) {
            UI.teleTempVal.textContent = TEL.tempVal;
        }

        // ── NB2 Breaker Power Monitor flush ──────────────────────────────
        if (UI.nb2Voltage) UI.nb2Voltage.textContent = TEL.nb2Voltage;
        if (UI.nb2Current) UI.nb2Current.textContent = TEL.nb2Current;
        if (UI.nb2Power) UI.nb2Power.textContent = TEL.nb2Power;
        if (UI.nb2PF) UI.nb2PF.textContent = TEL.nb2PF;
        if (UI.nb2Frequency) UI.nb2Frequency.textContent = TEL.nb2Frequency;
        if (UI.nb2Energy) UI.nb2Energy.textContent = TEL.nb2Energy;
        if (UI.nb2Apparent) UI.nb2Apparent.textContent = TEL.nb2Apparent;
        if (UI.nb2Reactive) UI.nb2Reactive.textContent = TEL.nb2Reactive;
        if (UI.nb2Temp) UI.nb2Temp.textContent = TEL.nb2Temp;
        if (UI.nb2Residual) UI.nb2Residual.textContent = TEL.nb2Residual;

        // ── Tab 2 Live Replica Power Strip flush ────────────────────────
        const replicaVolt = document.getElementById('model-replica-volt');
        const replicaAmp = document.getElementById('model-replica-amp');
        const replicaPwr = document.getElementById('model-replica-pwr');
        const replicaPF = document.getElementById('model-replica-pf');
        const nb2Ok = HW.connected && (MQTT_STATE.nb2.rs485Ok || MQTT_STATE.nb2.voltage > 0);

        if (replicaVolt) {
            replicaVolt.textContent = nb2Ok ? MQTT_STATE.nb2.voltage.toFixed(1) : (HW.connected ? '0.0' : '—');
            const vn = nb2Ok ? MQTT_STATE.nb2.voltage : 0;
            replicaVolt.style.color = (vn > 0 && (vn < 180 || vn > 250)) ? 'var(--status-crit)' : 'var(--text-val)';
        }
        if (replicaAmp) {
            replicaAmp.textContent = nb2Ok ? MQTT_STATE.nb2.current.toFixed(3) : (HW.connected ? '0.000' : '—');
            const an = nb2Ok ? MQTT_STATE.nb2.current : 0;
            replicaAmp.style.color = an > 10.0 ? 'var(--status-warn)' : an > 15.0 ? 'var(--status-crit)' : 'var(--text-val)';
        }
        if (replicaPwr) {
            replicaPwr.textContent = nb2Ok ? (MQTT_STATE.nb2.activePower / 1000).toFixed(3) : (HW.connected ? '0.000' : '—');
        }
        if (replicaPF) {
            replicaPF.textContent = nb2Ok ? MQTT_STATE.nb2.powerFactor.toFixed(2) : (HW.connected ? '0.00' : '—');
            const pfn = nb2Ok ? MQTT_STATE.nb2.powerFactor : 0;
            replicaPF.style.color = (pfn > 0 && pfn < 0.80) ? 'var(--status-warn)' : 'var(--text-val)';
        }

        // Breaker ON/OFF state badge
        if (UI.nb2BreakerState) {
            if (TEL.nb2Rs485Ok) {
                UI.nb2BreakerState.textContent = TEL.nb2BreakerOn ? 'ON' : 'OFF';
                UI.nb2BreakerState.className = TEL.nb2BreakerOn
                    ? 'nb2-state-badge nb2-state-on'
                    : 'nb2-state-badge nb2-state-off';
            } else {
                UI.nb2BreakerState.textContent = 'N/A';
                UI.nb2BreakerState.className = 'nb2-state-badge nb2-state-na';
            }
        }
        const breakerStateModel = document.getElementById('nb2-breaker-state-model');
        if (breakerStateModel) {
            if (TEL.nb2Rs485Ok) {
                breakerStateModel.textContent = TEL.nb2BreakerOn ? 'ON' : 'OFF';
                breakerStateModel.className = TEL.nb2BreakerOn
                    ? 'inspect-status-pill status-healthy'
                    : 'inspect-status-pill status-warning';
            } else {
                breakerStateModel.textContent = 'N/A';
                breakerStateModel.className = 'inspect-status-pill status-critical';
            }
        }

        // NB2 Fault / Alarm bar
        if (UI.nb2FaultBar && UI.nb2FaultText) {
            const faults = TEL.nb2FaultFlags;
            const alarms = TEL.nb2AlarmFlags;
            if (faults > 0 || alarms > 0) {
                UI.nb2FaultBar.classList.remove('hidden');
                const faultNames = [];

                // Process Fault Flags (Critical Tripping)
                if (faults & 0x0001) faultNames.push('Short Circuit Fault');
                if (faults & 0x0002) faultNames.push('Overload Fault');
                if (faults & 0x0004) faultNames.push('Overvoltage Fault');
                if (faults & 0x0008) faultNames.push('Undervoltage Fault');
                if (faults & 0x0010) faultNames.push('Leakage Fault');
                if (faults & 0x0020) faultNames.push('Over-temp Fault');
                if (faults & 0x0040) faultNames.push('Phase Loss Fault');
                if (faults & 0x0080) faultNames.push('Voltage Unbalance Fault');
                if (faults & 0x0100) faultNames.push('Overpower Fault');
                if (faults & 0x0200) faultNames.push('Underpower Fault');
                if (faults & 0x0400) faultNames.push('Phase Sequence Fault');
                if (faults & 0x0800) faultNames.push('Arc Fault');
                if (faults & 0xF000) {
                    faultNames.push('System Fault (0x' + (faults & 0xF000).toString(16) + ')');
                }

                // Process Alarm Flags (Warnings)
                if (alarms & 0x0001) faultNames.push('Leakage Alarm');
                if (alarms & 0x0002) faultNames.push('Over-temp Alarm');
                if (alarms & 0x0004) faultNames.push('Unbalance Alarm');
                if (alarms & 0x0008) faultNames.push('Overvoltage Alarm');
                if (alarms & 0x0010) faultNames.push('Undervoltage Alarm');
                if (alarms & 0x0020) faultNames.push('Overload Alarm');
                if (alarms & 0x0040) faultNames.push('Over-frequency Alarm');
                if (alarms & 0x0080) faultNames.push('Under-frequency Alarm');
                if (alarms & 0x0100) faultNames.push('Phase Sequence Alarm');
                if (alarms & 0x0200) faultNames.push('Communication Alarm');
                if (alarms & 0xFC00) {
                    faultNames.push('Other Alarm (0x' + (alarms & 0xFC00).toString(16) + ')');
                }

                UI.nb2FaultText.textContent = faultNames.join(' | ') || 'Fault detected';
                UI.nb2FaultBar.className = 'nb2-fault-bar mt-2 nb2-fault-active';
            } else {
                UI.nb2FaultBar.classList.add('hidden');
            }
        }

        // Enable/disable breaker control buttons based on connection
        if (UI.btnNb2On) UI.btnNb2On.disabled = !HW.connected || !TEL.nb2Rs485Ok;
        if (UI.btnNb2Off) UI.btnNb2Off.disabled = !HW.connected || !TEL.nb2Rs485Ok;
        if (UI.btnHardOff) UI.btnHardOff.disabled = !HW.connected || !TEL.nb2Rs485Ok;

        TEL.dirty = false;
    }
    requestAnimationFrame(flushTelemetryToDOM);
}
// Kick off the UI flush loop immediately
requestAnimationFrame(flushTelemetryToDOM);

// ============================================================================
// MQTT HARDWARE MODULE
// Bridges the Babylon.js Digital Twin to a real ESP32 via:
//   Browser  ←WebSocket→  Mosquitto  ←MQTT→  Node-RED  ←Serial→  ESP32
// ============================================================================

// ── Badge + button UI state ───────────────────────────────────────────────────
// NOTE: This function ONLY updates:
//   1. The HW MODE / SIM MODE badge
//   2. The ESP32 Bridge connection status (MQTT broker WebSocket)
//   3. The connect/disconnect button text
// Individual sensor badges (Motor, Proximity, Temp) are ONLY
// updated in parseTelemetry() when real data packets arrive.
function updateHwBadge(connected) {
    if (UI.hwBadge) {
        UI.hwBadge.textContent = connected ? 'HW MODE' : 'SIM MODE';
        UI.hwBadge.className = connected ? 'hw-badge hw-connected' : 'hw-badge hw-sim';
    }

    // Only the ESP32 bridge status reflects the broker WebSocket state
    if (UI.statusEsp32) {
        UI.statusEsp32.textContent = connected ? 'CONNECTED' : 'DISCONNECTED';
        UI.statusEsp32.className = connected
            ? 'inspect-status-pill status-healthy'
            : 'inspect-status-pill status-critical';
    }

    // On disconnect: reset ALL sensor badges to DISCONNECTED
    // On connect: leave them as DISCONNECTED until real data arrives
    if (!connected) {
        const resetSensors = [
            UI.statusMotor,
            UI.statusProximity,
            UI.statusTemp,
            UI.statusWeight,
            UI.statusVibration,
            UI.statusBreaker,
            UI.statusRs485
        ];
        resetSensors.forEach(el => {
            if (el) {
                el.textContent = 'DISCONNECTED';
                el.className = 'inspect-status-pill status-critical';
            }
        });
    }

    if (UI.btnMqttConnect) {
        UI.btnMqttConnect.textContent = connected ? 'Disconnect Live Hardware' : 'Connect Live Hardware';
        UI.btnMqttConnect.className = connected ? 'cmd-btn danger w-100 mb-3' : 'cmd-btn primary w-100 mb-3';
    }
    const btnMqttModel = document.getElementById('btn-mqtt-connect-model');
    if (btnMqttModel) {
        btnMqttModel.textContent = connected ? 'Disconnect' : 'Connect Hardware';
        btnMqttModel.style.borderColor = connected ? 'var(--danger)' : 'var(--primary)';
        btnMqttModel.style.color = connected ? 'var(--danger)' : 'var(--primary)';
    }
}

// ── UI Control Gating ────────────────────────────────────────────────────────
// Call whenever HW.connected or currentActiveTab changes.
// Enables/disables controls and shows/hides overlay based on state.
function updateSimControlsState() {
    // Simulation controls: disabled when real hardware is active
    const simControls = [UI.simVolt, UI.simLoad,
    UI.btnSimRun, UI.btnSimStop,
    UI.btnSimSetVolt, UI.btnSimSetLoad];
    simControls.forEach(el => { if (el) el.disabled = HW.connected; });

    // Hardware controls in 3D Model tab: only meaningful when MQTT connected
    const walkBtns = [UI.btnWalkSlow, UI.btnWalkMedium, UI.btnWalkFast];
    const hwControls = [UI.btnRun, UI.btnStop, UI.btnEstop, ...walkBtns];
    hwControls.forEach(el => { if (el) el.disabled = !HW.connected; });

    // Warning banner — shown in 3D model tab when hardware not connected
    const modelBanner = document.getElementById('model-hw-banner');
    if (modelBanner) {
        if (currentActiveTab === 'model' && !HW.connected) {
            modelBanner.classList.remove('hidden');
        } else {
            modelBanner.classList.add('hidden');
        }
    }

    // Simulation-suspended banner
    const simBanner = document.getElementById('sim-hw-banner');
    if (simBanner) {
        if (currentActiveTab === 'simulation' && HW.connected) {
            simBanner.classList.remove('hidden');
        } else {
            simBanner.classList.add('hidden');
        }
    }

    // Clear replica HUD when disconnected
    if (!HW.connected) {
        ['model-replica-rpm', 'model-replica-speed',
            'model-replica-prox', 'model-replica-latency'].forEach(id => {
                const el = document.getElementById(id);
                if (el) { el.textContent = '—'; el.style.color = ''; }
            });
        const freshnessEl = document.getElementById('mqtt-freshness-fill');
        if (freshnessEl) { freshnessEl.style.width = '0%'; freshnessEl.style.background = 'var(--border-subtle)'; }
    }
}

// ── Business KPIs calculation for Executive Overview ───────────────────────
let lastKpiUpdateTime = Date.now();
function updateBusinessKPIs(powerKw) {
    const now = Date.now();
    const dtHours = (now - lastKpiUpdateTime) / 3600000.0; // convert ms to hours
    lastKpiUpdateTime = now;

    if (SIM.isRunning) {
        SIM.energyUsedKwh += powerKw * dtHours;
        SIM.costUSD = SIM.energyUsedKwh * 0.15; // e.g. $0.15 per kWh
    }

    const oeeAvailability = SIM.isRunning ? 98.4 : 0.0;
    const oeePerformance = Math.min(100, (SIM.rpm / SIM.maxRpm) * 100);
    const oeeQuality = 99.8;
    const overallOee = (SIM.rpm > 0.5) ? (oeeAvailability * (oeePerformance / 100) * (oeeQuality / 100)) : 0.0;

    const ppm = (SIM.rpm * 0.15).toFixed(1);

    // Eco-mode savings
    const savingsPercent = optState.ecoMode ? 12.5 : 0.0;
    const carbonOffsetKg = SIM.energyUsedKwh * 0.403; // kg CO2 per kWh

    TEL.rate = ppm;
    TEL.energy = SIM.energyUsedKwh.toFixed(4);
    TEL.cost = SIM.costUSD.toFixed(3);
    TEL.oee = overallOee.toFixed(1) + '%';
    TEL.savings = savingsPercent.toFixed(1);
    TEL.carbon = carbonOffsetKg.toFixed(3);

    // Status text & color
    if (SIM.faults.motorOverheat || SIM.faults.beltOverload) {
        TEL.stateText = 'Alert';
        TEL.stateColor = 'var(--status-crit)';
    } else if (SIM.isRunning) {
        TEL.stateText = 'Running';
        TEL.stateColor = 'var(--status-ok)';
    } else {
        TEL.stateText = 'Standby';
        TEL.stateColor = 'var(--text-label)';
    }
}

// ── Parse Arduino telemetry JSON ──────────────────────────────────────────────
// Input JSON format: {"rpm": 0.0, "sensor_active": false, "uptime_ms": 1000, "dir": "stop", "speed_percent": 0, "ppr": 770}
function parseTelemetry(rawString) {
    try {
        const data = JSON.parse(rawString);

        const pwm = parseInt(data.speed_percent) || 0;
        const dir = (data.dir || 'stop').toLowerCase();
        const mode = (data.control_mode || 'manual').toLowerCase();
        const isMoving = (dir === 'fwd' || dir === 'rev' || pwm > 0);

        let rpm = 0.0;
        if (mode === 'manual') {
            // Manual mode movement: always set to Medium speed (60% PWM = 100.2 RPM, 0.077 m/s)
            rpm = isMoving ? 100.2 : 0.0;
        } else {
            // Remote mode movement: proportional to commanded PWM% (100% PWM = 167.0 RPM)
            rpm = isMoving ? (pwm / 100.0) * 167.0 : 0.0;
        }

        const prox = data.e18_active !== undefined ? !!data.e18_active : !!data.sensor_active;
        const temp = data.temp_c !== undefined && data.temp_c !== null ? parseFloat(data.temp_c) : null;

        // Drive the 3D twin mathematically
        SIM.rpm = rpm;
        SIM.targetRpm = rpm;
        SIM.isRunning = (rpm > 0.5);

        // ── Feed MQTT_STATE — the 3D Model tab reads ONLY from here ──────────
        MQTT_STATE.rpm = rpm;
        MQTT_STATE.isRunning = SIM.isRunning;
        MQTT_STATE.speedPercent = (mode === 'manual' && isMoving) ? 60 : pwm;
        MQTT_STATE.beltSpeed = rpmToBeltSpeed(rpm);
        MQTT_STATE.lastPacketTime = Date.now();
        MQTT_STATE.isStale = false;

        // ── NB2 Breaker: Parse real AC power data if present ────────────
        // Support both nested {"nb2": {...}} and flat {"voltage": ...} payloads from ESP
        let nb2Raw = data.nb2;
        if (!nb2Raw && data.voltage !== undefined) {
            nb2Raw = data;
        }
        const hasNb2 = nb2Raw && (nb2Raw.rs485_ok === true || nb2Raw.rs485_ok == 1 || nb2Raw.voltage !== undefined);

        let displayVoltage, displayCurrent, displayPower, displayPF;

        if (hasNb2) {
            // *** REAL DATA from NB2 CHINT breaker — replaces synthesized values ***
            const nb2V = parseFloat(nb2Raw.voltage) || 0;
            const nb2I = parseFloat(nb2Raw.current) || 0;
            const nb2P = parseFloat(nb2Raw.active_power) || 0;
            const nb2PF = parseFloat(nb2Raw.power_factor) || 0;
            const nb2Freq = parseFloat(nb2Raw.frequency) || 0;
            const nb2Energy = parseInt(nb2Raw.energy_wh) || 0;
            const nb2Apparent = parseFloat(nb2Raw.apparent_power) || 0;
            const nb2Reactive = parseFloat(nb2Raw.reactive_power) || 0;
            const nb2BreakerOn = !!nb2Raw.breaker_on;
            const nb2InternalTemp = parseFloat(nb2Raw.internal_temp) || 0;
            const nb2ResidualMa = parseFloat(nb2Raw.residual_current_ma) || 0;
            const nb2FaultFlags = parseInt(nb2Raw.fault_flags) || 0;
            const nb2AlarmFlags = parseInt(nb2Raw.alarm_flags) || 0;

            // Use REAL breaker readings for the main telemetry display
            displayVoltage = nb2V;
            displayCurrent = nb2I;
            displayPower = nb2P / 1000.0;   // W → kW for main display
            displayPF = nb2PF;

            SIM.current = nb2I;
            SIM.voltage = nb2V;

            // Update MQTT replica state
            MQTT_STATE.nb2.voltage = nb2V;
            MQTT_STATE.nb2.current = nb2I;
            MQTT_STATE.nb2.activePower = nb2P;
            MQTT_STATE.nb2.reactivePower = nb2Reactive;
            MQTT_STATE.nb2.apparentPower = nb2Apparent;
            MQTT_STATE.nb2.powerFactor = nb2PF;
            MQTT_STATE.nb2.frequency = nb2Freq;
            MQTT_STATE.nb2.energyWh = nb2Energy;
            MQTT_STATE.nb2.residualCurrentMa = nb2ResidualMa;
            MQTT_STATE.nb2.internalTemp = nb2InternalTemp;
            MQTT_STATE.nb2.breakerOn = nb2BreakerOn;
            MQTT_STATE.nb2.faultFlags = nb2FaultFlags;
            MQTT_STATE.nb2.alarmFlags = nb2AlarmFlags;
            MQTT_STATE.nb2.rs485Ok = true;

            // NB2 Power Monitor panel buffer
            TEL.nb2Voltage = nb2V.toFixed(1);
            TEL.nb2Current = nb2I.toFixed(3);
            TEL.nb2Power = nb2P.toFixed(1);
            TEL.nb2PF = nb2PF.toFixed(2);
            TEL.nb2Frequency = nb2Freq.toFixed(2);
            TEL.nb2Energy = nb2Energy.toString();
            TEL.nb2Apparent = nb2Apparent.toFixed(1);
            TEL.nb2Reactive = nb2Reactive.toFixed(1);
            TEL.nb2Temp = nb2InternalTemp.toFixed(1);
            TEL.nb2Residual = nb2ResidualMa.toFixed(1);
            TEL.nb2BreakerOn = nb2BreakerOn;
            TEL.nb2FaultFlags = nb2FaultFlags;
            TEL.nb2AlarmFlags = nb2AlarmFlags;
            TEL.nb2Rs485Ok = true;

            // Mark breaker sensor connected
            if (UI.statusBreaker) {
                UI.statusBreaker.textContent = 'CONNECTED';
                UI.statusBreaker.className = 'inspect-status-pill status-healthy';
            }
            if (UI.statusRs485) {
                const rsOk = (nb2Raw.rs485_ok === true || nb2Raw.rs485_ok == 1);
                UI.statusRs485.textContent = rsOk ? 'CONNECTED' : 'FAIL';
                UI.statusRs485.className = rsOk ? 'inspect-status-pill status-healthy' : 'inspect-status-pill status-critical';
            }

            // Decode and display physical breaker faults/alarms
            if (nb2FaultFlags > 0 || nb2AlarmFlags > 0) {
                let faultMsgs = [];
                if (nb2FaultFlags & 0x0001) faultMsgs.push("Short Circuit");
                if (nb2FaultFlags & 0x0002) faultMsgs.push("Overload");
                if (nb2FaultFlags & 0x0004) faultMsgs.push("Overvoltage");
                if (nb2FaultFlags & 0x0008) faultMsgs.push("Undervoltage");
                if (nb2FaultFlags & 0x0010) faultMsgs.push("Leakage (Residual)");
                if (nb2FaultFlags & 0x0020) faultMsgs.push("Over Temperature");
                if (nb2FaultFlags & 0x0040) faultMsgs.push("Phase Loss");
                if (nb2FaultFlags & 0x0080) faultMsgs.push("Voltage Unbalance");
                if (nb2FaultFlags & 0x0100) faultMsgs.push("OverPower");
                if (nb2FaultFlags & 0x0200) faultMsgs.push("UnderPower");
                if (nb2FaultFlags & 0x0400) faultMsgs.push("Phase Sequence");
                if (nb2FaultFlags & 0x0800) faultMsgs.push("Arc Fault");
                if (nb2FaultFlags & 0xF000) faultMsgs.push("Other Fault (0x" + (nb2FaultFlags & 0xF000).toString(16) + ")");

                if (nb2AlarmFlags & 0x0001) faultMsgs.push("Leakage ALARM");
                if (nb2AlarmFlags & 0x0002) faultMsgs.push("OverTemp ALARM");
                if (nb2AlarmFlags & 0x0004) faultMsgs.push("Unbalance ALARM");
                if (nb2AlarmFlags & 0x0008) faultMsgs.push("Overvoltage ALARM");
                if (nb2AlarmFlags & 0x0010) faultMsgs.push("Undervoltage ALARM");
                if (nb2AlarmFlags & 0x0020) faultMsgs.push("Overload ALARM");
                if (nb2AlarmFlags & 0xFFC0) faultMsgs.push("Other Alarm (0x" + (nb2AlarmFlags & 0xFFC0).toString(16) + ")");

                let fullText = faultMsgs.join(" | ");

                const faultBar = document.getElementById('nb2-fault-bar');
                const faultText = document.getElementById('nb2-fault-text');
                if (faultBar && faultText) {
                    faultBar.classList.remove('hidden');
                    faultBar.style.backgroundColor = 'rgba(255, 60, 60, 0.2)';
                    faultBar.style.border = '1px solid rgba(255, 60, 60, 0.5)';
                    faultBar.style.color = '#ff4d4d';
                    faultText.textContent = `BREAKER FAULT: ${fullText}`;
                }

                // Log it to the terminal once in a while to avoid spamming, or just log if changed
                if (nb2FaultFlags !== window.lastNb2FaultLogged) {
                    addLog(`[NB2] ⚠️ ELECTRICAL FAULT DETECTED: ${fullText}`, 'error');
                    window.lastNb2FaultLogged = nb2FaultFlags;
                }
            } else {
                const faultBar = document.getElementById('nb2-fault-bar');
                if (faultBar) {
                    faultBar.classList.add('hidden');
                }
                window.lastNb2FaultLogged = 0;
            }
        } else {
            // ── Fallback: synthesized electrical estimates from PWM% ──────
            let baseCurrent = 0.0;
            let calculatedVoltage = MOTOR.ratedVoltage;
            let pf = 0.85;

            if (SIM.isRunning) {
                const fraction = pwm / 100;
                baseCurrent = 0.5 + fraction * 3.5;
                if (SIM.faults.motorOverheat) baseCurrent *= 2.6;
                if (SIM.faults.beltOverload) baseCurrent *= 1.7;
                SIM.current = baseCurrent + (Math.random() - 0.5) * 0.15;
                if (SIM.current < 0) SIM.current = 0;
                calculatedVoltage = MOTOR.ratedVoltage - SIM.current * 0.3 + (Math.random() - 0.5) * 0.05;
                if (calculatedVoltage < 0) calculatedVoltage = 0;
                pf = SIM.faults.beltOverload ? 0.62 : 0.85 + (Math.random() - 0.5) * 0.02;
            } else {
                SIM.current = 0.0;
            }
            SIM.voltage = calculatedVoltage;
            displayVoltage = calculatedVoltage;
            displayCurrent = SIM.current;
            displayPower = (calculatedVoltage * SIM.current * pf) / 1000.0;
            displayPF = pf;

            // NB2 not available — mark disconnected
            TEL.nb2Rs485Ok = false;
            MQTT_STATE.nb2.rs485Ok = false;
            if (UI.statusBreaker) {
                UI.statusBreaker.textContent = 'NO RS485';
                UI.statusBreaker.className = 'inspect-status-pill status-warning';
            }
            if (UI.statusRs485) {
                UI.statusRs485.textContent = 'DISCONNECTED';
                UI.statusRs485.className = 'inspect-status-pill status-critical';
            }
        }

        const power = displayPower;

        // Telemetry double-buffer write
        TEL.rpm = rpm.toFixed(1);
        TEL.amp = displayCurrent.toFixed(2);
        TEL.volt = displayVoltage.toFixed(1);
        TEL.power = power.toFixed(3);
        TEL.pf = displayPF.toFixed(2);

        // Update non-engineering KPIs
        updateBusinessKPIs(power);

        // Real telemetry received — mark motor as connected
        if (UI.statusMotor) {
            UI.statusMotor.textContent = 'CONNECTED';
            UI.statusMotor.className = 'inspect-status-pill status-healthy';
        }

        // Proximity sensor → piece counter (rising-edge detection)
        if (prox && !HW.lastProxState) {
            SIM.pieceCount++;
            MQTT_STATE.pieceCount = SIM.pieceCount; // rising-edge, replica counter
            TEL.count = SIM.pieceCount.toString();
            addLog(`Proximity: part detected — total: ${SIM.pieceCount}`, 'info');
        }
        HW.lastProxState = prox;
        MQTT_STATE.proxDetected = prox;
        TEL.proximity = prox ? 'Detected' : 'Idle';

        // Proximity sensor receives data via the same telemetry packet — mark connected
        if (UI.statusProximity) {
            UI.statusProximity.textContent = 'CONNECTED';
            UI.statusProximity.className = 'inspect-status-pill status-healthy';
        }

        // Temp sensor: only mark CONNECTED if DS18B20 returned a valid reading
        // temp_c === null means the sensor is physically disconnected on the ESP32
        if (temp !== null) {
            TEL.tempVal = temp.toFixed(1);
            if (UI.statusTemp) {
                UI.statusTemp.textContent = 'CONNECTED';
                UI.statusTemp.className = 'inspect-status-pill status-healthy';
            }
        } else {
            TEL.tempVal = '--';
            if (UI.statusTemp) {
                UI.statusTemp.textContent = 'DISCONNECTED';
                UI.statusTemp.className = 'inspect-status-pill status-critical';
            }
        }

        // AI predictive — still estimated, now driven by real RPM
        let rul = '142.5 d';
        let anomaly = 0.8 + (Math.random() - 0.5) * 0.3;
        let efficiency = 94.2 - (rpm / SIM.maxRpm) * 2.0 + (Math.random() - 0.5) * 0.2;
        let nextService = 'Nominal';
        let serviceColor = 'var(--text-label)';
        let anomalyColor = 'var(--text-secondary)';

        if (SIM.faults.motorOverheat) {
            rul = `${(1.5 + Math.random() * 1.5).toFixed(1)} h`;
            anomaly = 98.4 + Math.random() * 1.2;
            efficiency = 41.5 + (Math.random() - 0.5) * 1.5;
            nextService = 'Immediate Abort'; serviceColor = 'var(--status-crit)'; anomalyColor = 'var(--status-crit)';
        } else if (SIM.faults.beltOverload) {
            rul = `${(7.8 + Math.random() * 1.2).toFixed(1)} d`;
            anomaly = 84.7 + Math.random() * 2.1;
            efficiency = 58.1 + (Math.random() - 0.5) * 1.0;
            nextService = 'Adjust Tension'; serviceColor = 'var(--status-warn)'; anomalyColor = 'var(--status-warn)';
        } else if (SIM.faults.looseScrew) {
            rul = `${(13.5 + Math.random() * 1.5).toFixed(1)} d`;
            anomaly = 34.2 + Math.random() * 1.8;
            efficiency = 88.7 + (Math.random() - 0.5) * 0.5;
            nextService = 'Tighten Fastener'; serviceColor = 'var(--status-warn)'; anomalyColor = 'var(--status-warn)';
        }

        TEL.rul = rul; TEL.anomaly = `${anomaly.toFixed(1)}%`; TEL.anomalyColor = anomalyColor;
        TEL.efficiency = `${efficiency.toFixed(1)}%`; TEL.service = nextService; TEL.serviceColor = serviceColor;
        TEL.dirty = true;

        if (SIM.selectedMesh) updateInspector(SIM.selectedMesh);
    } catch (e) {
        addLog(`[Error] Failed to parse JSON Telemetry: ${e.message}`, 'error');
    }
}

// ── Publish JSON command to ESP32 via MQTT ─────────────────────────────────────
function sendCmdObject(cmdObj) {
    if (!HW.connected || !HW.client) {
        addLog('Cannot send command: Hardware is not connected.', 'warning');
        return;
    }
    const payload = JSON.stringify(cmdObj);
    HW.client.publish(MQTT_CFG.topicCmd, payload, { qos: 0 });
    addLog(`[MQTT Command Sent] → ${payload}`, 'success');
}

// ── Connect to Mosquitto broker ────────────────────────────────────────────────
function connectMQTT() {
    if (typeof mqtt === 'undefined') {
        addLog('MQTT.js not loaded — check your internet connection.', 'error');
        return;
    }
    if (HW.client) { disconnectMQTT(); return; }

    const profile = MQTT_CFG.activeProfile;
    addLog(`Connecting to MQTT broker [${profile.label}]: ${profile.brokerUrl} …`, 'info');

    const client = mqtt.connect(profile.brokerUrl, {
        clientId: MQTT_CFG.clientId,
        keepalive: 30,
        reconnectPeriod: 0,
        connectTimeout: 5000,
        ...profile.options,
    });

    HW.client = client;

    client.on('connect', () => {
        HW.connected = true;
        HW.lastProxState = false;
        // Reset speed immediately so the 3D twin is stopped until real telemetry tells it to move
        SIM.rpm = 0;
        SIM.targetRpm = 0;
        SIM.isRunning = false;
        TEL.rpm = '0.0';
        TEL.amp = '0.00';
        TEL.speed = '0.00';
        TEL.power = '0.000';
        TEL.proximity = '--';
        TEL.tempVal = '--';
        // Reset NB2 fields
        TEL.nb2Voltage = '--'; TEL.nb2Current = '--'; TEL.nb2Power = '--';
        TEL.nb2PF = '--'; TEL.nb2Frequency = '--'; TEL.nb2Energy = '--';
        TEL.nb2Apparent = '--'; TEL.nb2Reactive = '--'; TEL.nb2Temp = '--';
        TEL.nb2Residual = '--'; TEL.nb2Rs485Ok = false; TEL.nb2BreakerOn = false;
        TEL.nb2FaultFlags = 0; TEL.nb2AlarmFlags = 0;
        TEL.dirty = true;

        // Reset MQTT replica — model stays frozen until first real packet arrives
        MQTT_STATE.rpm = 0;
        MQTT_STATE.isRunning = false;
        MQTT_STATE.proxDetected = false;
        MQTT_STATE.speedPercent = 0;
        MQTT_STATE.beltSpeed = 0;
        MQTT_STATE.lastPacketTime = 0;
        MQTT_STATE.isStale = false;

        client.subscribe('digital_twin/#', { qos: 0 });
        // Suspend synthetic background interval while hardware is active
        if (SIM.interval) { clearInterval(SIM.interval); SIM.interval = null; }
        updateHwBadge(true);
        updateSimControlsState();
        if (UI.statusPill) UI.statusPill.classList.add('active');
        if (UI.status) UI.status.textContent = 'Hardware Mode';
        addLog('Hardware mode active — receiving live ESP32 telemetry JSON via MQTT.', 'success');
    });

    client.on('message', (topic, payload) => {
        if (topic === MQTT_CFG.topicSub || topic === 'digital_twin/motor/telemetry') {
            parseTelemetry(payload.toString());
        }
    });

    client.on('error', (err) => {
        addLog(`MQTT error: ${err.message}`, 'error');
        HW.client = null;
        HW.connected = false;
        updateHwBadge(false);
    });

    client.on('close', () => {
        if (HW.connected) {
            HW.connected = false;
            HW.running = false;
            HW.client = null;
            updateHwBadge(false);
            if (UI.status) UI.status.textContent = 'Standby';
            if (UI.statusPill) UI.statusPill.classList.remove('active');
            addLog('MQTT disconnected — simulation mode restored.', 'warning');
        }
    });
}

// ── Cleanly disconnect ─────────────────────────────────────────────────────────
function disconnectMQTT() {
    if (HW.client) { HW.client.end(true); HW.client = null; }
    HW.connected = false;
    HW.running = false;
    HW.lastProxState = false;
    SIM.rpm = 0;
    SIM.targetRpm = 0;
    // Clear sensor readings — these are only valid from real hardware
    TEL.proximity = '--';
    TEL.tempVal = '--';
    // Reset NB2 fields
    TEL.nb2Voltage = '--'; TEL.nb2Current = '--'; TEL.nb2Power = '--';
    TEL.nb2PF = '--'; TEL.nb2Frequency = '--'; TEL.nb2Energy = '--';
    TEL.nb2Apparent = '--'; TEL.nb2Reactive = '--'; TEL.nb2Temp = '--';
    TEL.nb2Residual = '--'; TEL.nb2Rs485Ok = false; TEL.nb2BreakerOn = false;
    TEL.nb2FaultFlags = 0; TEL.nb2AlarmFlags = 0;
    TEL.dirty = true;
    // Freeze replica — no MQTT, no motion
    MQTT_STATE.rpm = 0;
    MQTT_STATE.isRunning = false;
    MQTT_STATE.proxDetected = false;
    MQTT_STATE.beltSpeed = 0;
    MQTT_STATE.lastPacketTime = 0;
    MQTT_STATE.isStale = false;
    updateHwBadge(false);
    updateSimControlsState();
    if (UI.statusPill) UI.statusPill.classList.remove('active');
    if (UI.status) UI.status.textContent = 'Standby';
    addLog('Hardware disconnected - simulation mode active.', 'info');
}

// ── Connect button ─────────────────────────────────────────────────────────────
if (UI.btnMqttConnect) {
    UI.btnMqttConnect.addEventListener('click', () => {
        HW.connected ? disconnectMQTT() : connectMQTT();
    });
}
const btnMqttModel = document.getElementById('btn-mqtt-connect-model');
if (btnMqttModel) {
    btnMqttModel.addEventListener('click', () => {
        HW.connected ? disconnectMQTT() : connectMQTT();
    });
}

// ── NB2 Breaker Control Buttons ───────────────────────────────────────────────
const btnNb2UnlockModel = document.getElementById('btn-nb2-unlock-model');
if (btnNb2UnlockModel) {
    btnNb2UnlockModel.addEventListener('click', () => {
        sendCmdObject({ nb2_unlock: true });
        addLog('[NB2] Remote unlock command sent.', 'info');
    });
}

if (UI.btnNb2On) {
    UI.btnNb2On.addEventListener('click', () => {
        sendCmdObject({ breaker: 'on' });
        addLog('[NB2] Sending remote CLOSE (breaker ON) command...', 'info');
    });
}
const btnNb2OnModel = document.getElementById('btn-nb2-on-model');
if (btnNb2OnModel) {
    btnNb2OnModel.addEventListener('click', () => {
        sendCmdObject({ breaker: 'on' });
        addLog('[NB2] Sending remote CLOSE (breaker ON) command...', 'info');
    });
}

if (UI.btnNb2Off) {
    UI.btnNb2Off.addEventListener('click', () => {
        sendCmdObject({ breaker: 'off' });
        addLog('[NB2] Sending remote OPEN (breaker OFF) command...', 'warning');
    });
}
const btnNb2OffModel = document.getElementById('btn-nb2-off-model');
if (btnNb2OffModel) {
    btnNb2OffModel.addEventListener('click', () => {
        // Safety confirm — opening the breaker cuts AC power to the whole station
        if (!confirm('⚠️ BREAKER OFF WARNING\n\nOpening the breaker will cut power to the entire production station — motor, sensors, and all field equipment will de-energise immediately.\n\nProceed only if the line is stopped and it is safe to do so.\n\nPress OK to open the breaker.')) return;
        if (!HW.connected) {
            addLog('[NB2] Not connected to hardware — command NOT sent.', 'warning');
            return;
        }
        sendCmdObject({ breaker: 'off' });
        addLog('[NB2] ⚠️ Remote OPEN command sent — station power cut.', 'warning');
    });
}
// HARD POWER CUT — Emergency breaker trip via NB2 RS485
if (UI.btnHardOff) {
    UI.btnHardOff.addEventListener('click', () => {
        if (confirm('⚡ HARD POWER CUT\n\nThis will remotely OPEN the NB2 circuit breaker, cutting AC mains power.\n\nAre you sure?')) {
            // First: stop motor via MQTT
            sendCmdObject({ cmd: 'estop' });
            // Then: trip the breaker
            setTimeout(() => {
                sendCmdObject({ breaker: 'off' });
                addLog('[NB2] ⚡ HARD POWER CUT — Breaker OPEN command sent!', 'error');
            }, 200);
        }
    });
}



// ============================================================================
// BABYLON.JS ENGINE SETUP
// ============================================================================
const canvas = document.getElementById('canvas3d');
const engine = new BABYLON.Engine(canvas, true, {
    preserveDrawingBuffer: true,
    stencil: true,
    antialias: true,
});

const createScene = async function () {
    const scene = new BABYLON.Scene(engine);

    // Canvas clear color matches --bg-base (#F1F5F9 = Slate-100)
    scene.clearColor = new BABYLON.Color4(0.945, 0.961, 0.976, 1.0);

    const camera = new BABYLON.ArcRotateCamera('Camera', -Math.PI / 2, Math.PI / 3, 2.2, BABYLON.Vector3.Zero(), scene);
    camera.attachControl(canvas, true);
    camera.wheelPrecision = 10;
    camera.minZ = 0.1;
    camera.lowerRadiusLimit = 0.5;
    camera.upperRadiusLimit = 3;

    // Prevent browser scroll on canvas wheel
    canvas.addEventListener('wheel', (e) => e.preventDefault(), { passive: false });

    const envTexture = BABYLON.CubeTexture.CreateFromPrefilteredData(
        'https://playground.babylonjs.com/textures/environment.dds', scene
    );
    scene.environmentTexture = envTexture;

    // -------------------------------------------------------------------------
    // POST-PROCESS PIPELINE — FXAA + Bloom + Vignette for spatial depth
    // Vignette darkens edges → pulls operator focus to the 3D model center.
    // -------------------------------------------------------------------------
    const pipeline = new BABYLON.DefaultRenderingPipeline('defaultPipeline', true, scene, [camera]);
    pipeline.samples = 4;
    pipeline.fxaaEnabled = true;
    pipeline.bloomEnabled = true;
    pipeline.bloomThreshold = 0.8;
    pipeline.bloomWeight = 0.35; // Reduced from 0.5 — subtler bloom

    // Vignette — mimics industrial WebGL viewers (Forge, three.js product viewers)
    pipeline.imageProcessingEnabled = true;
    pipeline.imageProcessing.vignetteEnabled = true;
    pipeline.imageProcessing.vignetteWeight = 2.0;
    pipeline.imageProcessing.vignetteColor = new BABYLON.Color4(0, 0, 0, 1);
    pipeline.imageProcessing.vignetteBlendMode = BABYLON.ImageProcessingConfiguration.VIGNETTEMODE_MULTIPLY;
    // Slight contrast lift to make the model pop against the flat bg
    pipeline.imageProcessing.contrast = 1.08;
    pipeline.imageProcessing.exposure = 1.0;

    // SSAO 2 Rendering Pipeline for ambient occlusion (crevice and corner contact shadows)
    const ssao = new BABYLON.SSAO2RenderingPipeline("ssaoPipeline", scene, { ssaoRatio: 0.5, blurRatio: 0.5 }, [camera]);
    ssao.radius = 0.08;
    ssao.totalStrength = 1.3;
    ssao.expensiveBlur = true;

    // Soft hemispheric fill light to prevent pitch-black unlit areas
    const hemiLight = new BABYLON.HemisphericLight('hemiLight', new BABYLON.Vector3(0, 1, 0), scene);
    hemiLight.intensity = 0.35;
    hemiLight.groundColor = new BABYLON.Color3(0.08, 0.11, 0.18); // Slate ground bounce

    // High-contrast directional light positioned to generate shadows
    const dirLight = new BABYLON.DirectionalLight('dirLight', new BABYLON.Vector3(-0.6, -1.2, -0.6), scene);
    dirLight.position = new BABYLON.Vector3(1.5, 3.0, 1.5);
    dirLight.intensity = 1.25;

    // Shadow Generator for realistic grounding shadows
    const shadowGenerator = new BABYLON.ShadowGenerator(1024, dirLight);
    shadowGenerator.useBlurExponentialShadowMap = true;
    shadowGenerator.blurKernel = 16;
    shadowGenerator.setDarkness(0.35);

    let kinematics = { pulleys: [], belts: [], boxes: [], shadowGenerator: shadowGenerator };

    addLog('Loading GLB Asset via Babylon.js...');
    const result = await BABYLON.SceneLoader.ImportMeshAsync('', 'assets/', 'Conveyor_Twin_v1.glb', scene);

    // Stop all baked animations on load
    if (result.animationGroups) {
        result.animationGroups.forEach(group => group.stop());
    }

    const rootNode = result.meshes[0];
    rootNode.position = BABYLON.Vector3.Zero();
    camera.setTarget(rootNode);

    // Cardboard material for spawned boxes
    const boxMat = new BABYLON.PBRMaterial('boxMat', scene);
    boxMat.albedoColor = new BABYLON.Color3(0.7, 0.55, 0.35);
    boxMat.roughness = 0.9;
    boxMat.metallic = 0.1;
    kinematics.boxMat = boxMat;

    result.meshes.forEach(mesh => {
        // Hide default rectangular room floor to replace with a sleek limited circular pedestal
        if (mesh.name === 'Room_Floor') {
            mesh.isVisible = false;
            return;
        }

        if (mesh.name !== 'Main_Belt' && (mesh.name.toLowerCase().includes('belt') || mesh.name.toLowerCase().includes('baked'))) {
            mesh.isVisible = false;
            return;
        }

        if (mesh.name.includes('Room_')) {
            if (mesh.material) mesh.material.backFaceCulling = false;
        }

        // Enable shadows for realistic depth
        mesh.receiveShadows = true;
        if (mesh.name !== '__root__' && !mesh.name.includes('Room_') && !mesh.name.includes('Background') && mesh.name !== 'canvas3d') {
            mesh.castShadows = true;
            shadowGenerator.addShadowCaster(mesh);
        }

        if (mesh.name.includes('Roller') || mesh.name.includes('Pulley') || mesh.name.includes('Shaft')) {
            kinematics.pulleys.push(mesh);
            let mat = mesh.material;
            if (!mat || mat.getClassName() !== 'PBRMaterial') {
                mat = new BABYLON.PBRMaterial(mesh.name + '_mat', scene);
                mesh.material = mat;
            }
            mat.metallic = 0.8;
            mat.roughness = 0.4;
            const dynamicTexture = new BABYLON.DynamicTexture('rollerTex', 256, scene, true);
            const ctx = dynamicTexture.getContext();
            ctx.fillStyle = '#888888'; ctx.fillRect(0, 0, 256, 256);
            ctx.fillStyle = '#000000'; ctx.fillRect(0, 0, 256, 32);
            dynamicTexture.update();
            mat.albedoTexture = dynamicTexture;
        }

        if (mesh.name === 'Main_Belt') {
            mesh.alwaysSelectAsActiveMesh = true;
            const pbr = new BABYLON.PBRMaterial('pvcBelt', scene);
            pbr.albedoColor = new BABYLON.Color3(1, 1, 1);
            pbr.metallic = 0.0;
            pbr.roughness = 0.95; // Rough, matte
            pbr.environmentIntensity = 0.10;
            pbr.microSurface = 0.0;
            pbr.specularIntensity = 0.0;
            pbr.useRadianceOcclusion = false;
            pbr.useHorizonOcclusion = false;
            pbr.clearCoat.isEnabled = false;
            pbr.backFaceCulling = false;

            const TW = 512, TH = 512;
            const dynTex = new BABYLON.DynamicTexture('beltTex', { width: TW, height: TH }, scene, false);
            const ctx = dynTex.getContext();

            // ── TUNING FACTOR (0.0 to 2.0+) ─────────────────────────────────────
            // 0 = completely soft/smooth, 1 = normal rugged, 2 = very rough
            const textureIntensity = 0.2;

            // Base color
            ctx.fillStyle = '#3D7757';
            ctx.fillRect(0, 0, TW, TH);

            // Tonal variation (uneven lighter and darker patches)
            for (let i = 0; i < 30; i++) {
                const x = Math.random() * TW;
                const y = Math.random() * TH;
                const r = 40 + Math.random() * 120;
                const grad = ctx.createRadialGradient(x, y, 0, x, y, r);
                const isLight = Math.random() > 0.5;
                grad.addColorStop(0, isLight ? `rgba(255,255,255,${0.06 * textureIntensity})` : `rgba(0,0,0,${0.08 * textureIntensity})`);
                grad.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.fillStyle = grad;
                ctx.fillRect(x - r, y - r, r * 2, r * 2);
            }

            // Subtle grid/halftone pattern in some areas
            ctx.fillStyle = `rgba(0, 0, 0, ${0.12 * textureIntensity})`;
            for (let x = TW * 0.15; x < TW * 0.85; x += 4) {
                for (let y = 0; y < TH; y += 4) {
                    if (Math.random() > 0.4) { // Only in some areas/randomly dropped out
                        ctx.fillRect(x, y, 1.5, 1.5);
                    }
                }
            }

            // Faint vertical and horizontal scratch-like fiber lines
            ctx.lineWidth = 1;
            for (let i = 0; i < 150; i++) {
                ctx.strokeStyle = Math.random() > 0.5 ? `rgba(255, 255, 255, ${0.05 * textureIntensity})` : `rgba(0, 0, 0, ${0.06 * textureIntensity})`;
                const x = Math.random() * TW;
                ctx.beginPath();
                ctx.moveTo(x, 0);
                ctx.lineTo(x, TH);
                ctx.stroke();

                if (Math.random() > 0.7) { // Fewer horizontal lines
                    const y = Math.random() * TH;
                    ctx.beginPath();
                    ctx.moveTo(0, y);
                    ctx.lineTo(TW, y);
                    ctx.stroke();
                }
            }

            // Small scattered white specks (like dust or tiny fibers)
            ctx.fillStyle = `rgba(255, 255, 255, ${0.7 * textureIntensity})`;
            for (let i = 0; i < 1000; i++) {
                const x = Math.random() * TW;
                const y = Math.random() * TH;
                const size = Math.random() * 1.5 + 0.5;
                ctx.fillRect(x, y, size, size);
            }

            dynTex.update();
            dynTex.wrapU = BABYLON.Texture.WRAP_ADDRESSMODE;
            dynTex.wrapV = BABYLON.Texture.WRAP_ADDRESSMODE;
            dynTex.anisotropicFilteringLevel = 8;

            pbr.albedoTexture = dynTex;
            pbr.albedoTexture.wAng = Math.PI / 2; // Restored: Rotate 90deg so it scrolls vertically
            mesh.material = pbr;
            kinematics.belts.push(pbr);
        }
    });

    // Create a sleek, limited square pedestal (floor) under the conveyor
    const pedestal = BABYLON.MeshBuilder.CreateBox('pedestal', {
        width: 1.1,
        depth: 1.1,
        height: 0.002
    }, scene);
    pedestal.position = new BABYLON.Vector3(0.0, 0.0005, 0.0); // Slightly above absolute zero to avoid z-fighting
    pedestal.receiveShadows = true;

    // Matte presentation material for the pedestal
    const pedestalMat = new BABYLON.PBRMaterial('pedestalMat', scene);
    pedestalMat.albedoColor = new BABYLON.Color3(0.278, 0.333, 0.412); // Medium industrial slate-gray (Slate-600) for contrast
    pedestalMat.roughness = 0.8;
    pedestalMat.metallic = 0.1;
    pedestalMat.backFaceCulling = false;
    pedestal.material = pedestalMat;

    // Scale down any other lights loaded from the GLB to prevent flat washing
    scene.lights.forEach(light => {
        if (light !== dirLight && light !== hemiLight) {
            light.intensity = 0.25;
        }
    });

    if (UI.loader) UI.loader.classList.add('hidden');
    addLog('Digital Twin asset loaded successfully.', 'success');
    return { scene, kinematics, animationGroups: result.animationGroups };
};

// ============================================================================
// COMPONENT METADATA DATABASE
// ============================================================================
function getComponentMetadata(meshName) {
    let name = meshName;
    let category = 'Conveyor Component';
    let specs = 'Standard Industrial Part';
    let status = 'OPERATIONAL';
    let healthClass = 'status-healthy';

    if (meshName.includes('Screw')) {
        category = 'Fastener';
        if (meshName.includes('M3') || meshName.includes('Motor')) {
            name = 'Motor Mount Screw (M3)';
            specs = 'Shaft: 3.0mm | Head: 5.5mm | Socket: Cross (+)';
        } else {
            let parentPart = 'Structure';
            if (meshName.includes('EndGuard')) parentPart = 'End Guard';
            else if (meshName.includes('Leg')) parentPart = 'Frame Leg';
            name = `${parentPart} Screw (M4)`;
            specs = 'Shaft: 4.0mm | Head: 7.0mm | Socket: Cross (+)';
        }
        if (SIM.faults.looseScrew && meshName.includes('Left_A_Leg_Rect_Screw_1.001')) {
            status = 'CRITICAL (LOOSE)';
            healthClass = 'status-critical';
        }
    } else if (meshName.includes('MTR_') || meshName.includes('Motor')) {
        category = 'Actuator / Motor';
        if (meshName.includes('Body')) name = 'Motor Main Body';
        else if (meshName.includes('Gearbox')) name = 'Motor Reduction Gearbox (10:1)';
        else if (meshName.includes('Encoder')) name = 'Motor Rotary Encoder';
        else if (meshName.includes('Shaft')) name = 'Motor Output Shaft';
        else name = 'Motor Component';

        // Temperature only shown when received from real MQTT hardware
        const currentTempStr = (HW.connected && TEL.tempVal !== '--') ? `${TEL.tempVal}°C` : '--';

        specs = `Type: Brushless DC | Voltage: 24V | Max RPM: 167 | Temp: ${currentTempStr}`;
        if (SIM.faults.motorOverheat || (HW.connected && TEL.tempVal !== '--' && parseFloat(TEL.tempVal) > 75)) {
            status = `CRITICAL (OVERHEAT: ${currentTempStr})`;
            healthClass = 'status-critical';
        } else {
            status = 'OPERATIONAL';
            healthClass = 'status-healthy';
        }
    } else if (meshName.includes('HW_Sensor') || meshName.toLowerCase().includes('proximity') || meshName.toLowerCase().includes('sensor')) {
        category = 'Sensor';
        name = 'E18-D80NK Proximity Sensor';
        const proxState = HW.connected ? TEL.proximity : (SIM.isRunning ? TEL.proximity : 'Idle');
        specs = `Type: Infrared | Range: 3-80cm | Output: NPN NO | State: ${proxState}`;
        status = proxState === 'Detected' ? 'ACTIVE (DETECTED)' : 'OPERATIONAL (IDLE)';
        healthClass = proxState === 'Detected' ? 'status-warning' : 'status-healthy';
    } else if (meshName.includes('Belt') || meshName.includes('Main_Belt')) {
        category = 'Transmission Belt';
        name = 'Main Conveyor PVC Belt';
        specs = 'Material: Anti-static Green PVC | Width: 100mm';
        if (SIM.faults.beltOverload) {
            status = 'WARNING (SLIPPAGE / OVERLOAD)';
            healthClass = 'status-warning';
        }
    } else if (meshName.includes('Roller')) {
        category = 'Rolling Component';
        name = meshName.includes('1') ? 'Drive Roller' : 'Driven Roller';
        specs = 'Diameter: 22mm | Width: 134mm | Bearings: Dual Shielded';
    } else if (meshName.includes('Pulley')) {
        category = 'Transmission Pulley';
        name = meshName.includes('1') ? 'Motor Drive Pulley' : 'Conveyor Driven Pulley';
        specs = 'Material: Aluminum | Pitch: GT2 | Teeth: 20';
    } else if (meshName.includes('BearingCap')) {
        category = 'Bearing Housing';
        name = meshName.includes('Left') ? 'Left Shaft Bearing Cap' : 'Right Shaft Bearing Cap';
        specs = 'Bearing Type: Ball Bearing 608RS | Material: Cast Iron';
    } else if (meshName.includes('EndGuard')) {
        category = 'Safety Guard';
        name = meshName.includes('Left') ? 'Left End Safety Shield' : 'Right End Safety Shield';
        specs = 'Material: Powder-coated Sheet Metal | Thickness: 2.0mm';
    } else if (meshName.includes('Leg')) {
        category = 'Support Structure';
        name = meshName.includes('A') ? 'Support A-Leg Frame' : 'Support B-Leg Frame';
        specs = 'Material: Extruded Aluminum | Profile: 40×40';
    } else if (meshName.includes('Support_Bed_Plate')) {
        category = 'Load Bed';
        name = 'Conveyor Slide Bed Plate';
        specs = 'Material: Brushed Stainless Steel | Friction Coeff: 0.15';
    }

    return { name, category, specs, status, healthClass };
}

function updateInspector(mesh) {
    // ── Tab 2 inspector panel ──
    if (!mesh) {
        if (UI.inspectorIdle) UI.inspectorIdle.classList.remove('hidden');
        if (UI.inspectorDetails) UI.inspectorDetails.classList.add('hidden');
    } else {
        const meta = getComponentMetadata(mesh.name);
        if (UI.inspectName) UI.inspectName.textContent = meta.name;
        if (UI.inspectCategory) UI.inspectCategory.textContent = meta.category;
        if (UI.inspectSpecs) UI.inspectSpecs.textContent = meta.specs;
        if (UI.inspectStatus) {
            UI.inspectStatus.textContent = meta.status;
            UI.inspectStatus.className = `inspect-status-pill ${meta.healthClass}`;
        }
        if (UI.inspectorIdle) UI.inspectorIdle.classList.add('hidden');
        if (UI.inspectorDetails) UI.inspectorDetails.classList.remove('hidden');
    }

    // [SIM-INSPECTOR] Tab 3 inspector panel — mirrors Tab 2 using separate DOM IDs.
    // The 3D canvas is shared, so pointer clicks fire for both tabs; we show the
    // correct panel based on which tab is currently active.
    // To remove: delete this block and the inspector HTML in Tab 3 (index.html).
    const simDetails = document.getElementById('inspector-details-sim');
    const simName = document.getElementById('inspect-name-sim');
    const simCategory = document.getElementById('inspect-category-sim');
    const simSpecs = document.getElementById('inspect-specs-sim');
    const simStatus = document.getElementById('inspect-status-sim');

    if (!mesh) {
        if (simDetails) simDetails.classList.add('hidden');
    } else {
        const meta = getComponentMetadata(mesh.name);
        if (simName) simName.textContent = meta.name;
        if (simCategory) simCategory.textContent = meta.category;
        if (simSpecs) simSpecs.textContent = meta.specs;
        if (simStatus) {
            simStatus.textContent = meta.status;
            simStatus.className = `inspect-status-pill ${meta.healthClass}`;
        }
        if (simDetails) simDetails.classList.remove('hidden');
    }
}

const btnCloseInspector = document.getElementById('btn-close-inspector');
if (btnCloseInspector) {
    btnCloseInspector.addEventListener('click', () => {
        if (typeof SIM !== 'undefined' && SIM.selectedMesh) {
            SIM.selectedMesh.renderOutline = false;
            SIM.selectedMesh = null;
        }
        updateInspector(null);
    });
}

// [SIM-INSPECTOR] Close button for the Tab 3 inspector panel.
// To remove: delete this block and the inspector HTML in Tab 3 (index.html).
const btnCloseInspectorSim = document.getElementById('btn-close-inspector-sim');
if (btnCloseInspectorSim) {
    btnCloseInspectorSim.addEventListener('click', () => {
        if (typeof SIM !== 'undefined' && SIM.selectedMesh) {
            SIM.selectedMesh.renderOutline = false;
            SIM.selectedMesh = null;
        }
        updateInspector(null);
    });
}


function animateCameraTarget(camera, newTarget, scene) {
    BABYLON.Animation.CreateAndStartAnimation(
        'animCamTarget', camera, 'target',
        60, 30,
        camera.target.clone(), newTarget,
        BABYLON.Animation.ANIMATIONLOOPMODE_CONSTANT
    );
}

// [TAB2-SPARKLINE] Voltage sparkline history — stores last 10 voltage samples.
const powerSparklineHistory = [];

function drawPowerSparkline(data) {
    const canvas = document.getElementById('model-sparkline-volt');
    if (!canvas || data.length < 2) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    const minV = Math.min(...data) - 0.5;
    const maxV = Math.max(...data) + 0.5;
    const range = maxV - minV || 1;
    ctx.beginPath();
    data.forEach((v, i) => {
        const x = (i / (data.length - 1)) * W;
        const y = H - ((v - minV) / range) * H;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = '#0284C7';
    ctx.lineWidth = 1.5;
    ctx.stroke();
    // Fill gradient
    ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, 'rgba(2,132,199,0.25)');
    grad.addColorStop(1, 'rgba(2,132,199,0)');
    ctx.fillStyle = grad;
    ctx.fill();
}

// ============================================================================
// SCENE MAIN
// ============================================================================
createScene().then(({ scene, kinematics, animationGroups }) => {
    const camera = scene.activeCamera;

    // Live Replica Status HUD (RPM / Speed) refresh throttle — see usage below.
    const REPLICA_HUD_REFRESH_MS = 3000;
    let lastReplicaHudUpdate = 0;

    // Start render loop
    engine.runRenderLoop(() => {
        scene.render();
    });

    // -------------------------------------------------------------------------
    // RENDER LOOP
    // -------------------------------------------------------------------------
    scene.onBeforeRenderObservable.add(() => {
        const delta = engine.getDeltaTime() / 1000.0;

        // Write FPS to double-buffer (no DOM touch here)
        TEL.fps = Math.round(engine.getFps()).toString();
        TEL.dirty = true;

        // Fault: Motor overheat — emissive glow on mesh
        if (SIM.faults.motorOverheat) {
            SIM.motorTemp += (95.0 - SIM.motorTemp) * delta * 0.2;
            const motorBody = scene.getMeshByName('MTR_02_Body');
            if (motorBody) {
                const glow = 0.4 + 0.3 * Math.sin(Date.now() * 0.008);
                if (!motorBody.material) {
                    motorBody.material = new BABYLON.PBRMaterial('motorBody_mat', scene);
                }
                motorBody.material.emissiveColor = new BABYLON.Color3(glow * 1.5, glow * 0.1, 0.0);
            }
        } else {
            const motorBody = scene.getMeshByName('MTR_02_Body');
            if (motorBody && motorBody.material) {
                motorBody.material.emissiveColor = new BABYLON.Color3(0, 0, 0);
            }
        }

        // Fault: Loose screw — micro-vibration jitter
        if (SIM.faults.looseScrew) {
            const looseScrew = scene.getMeshByName('Left_A_Leg_Rect_Screw_1.001');
            if (looseScrew) {
                if (!looseScrew._origPos) looseScrew._origPos = looseScrew.position.clone();
                const jitter = 0.0015 * Math.sin(Date.now() * 0.1);
                looseScrew.position.x = looseScrew._origPos.x + jitter;
                looseScrew.position.z = looseScrew._origPos.z + jitter * 0.5;
                looseScrew.renderOutline = true;
                const pulse = Math.sin(Date.now() * 0.015) > 0;
                looseScrew.outlineColor = pulse
                    ? new BABYLON.Color3(0.937, 0.267, 0.267)  // --status-crit
                    : new BABYLON.Color3(0.961, 0.620, 0.043); // --status-warn
                looseScrew.outlineWidth = 0.005;
            }
        }

        // ── MQTT-Replica Animation Guard ─────────────────────────────────────
        // 3D Model tab: belt/pulley motion ONLY from MQTT_STATE.rpm.
        //               No button, input, or timer can make it move.
        // Simulation tab: SIM.rpm (user-sandbox, isolated from model tab).
        // Other tabs:  model is frozen (canvas hidden anyway).
        let active3dRpm = 0;
        const mqttAge = MQTT_STATE.lastPacketTime > 0
            ? Date.now() - MQTT_STATE.lastPacketTime
            : Infinity;

        if (currentActiveTab === 'model') {
            if (HW.connected) {
                if (MQTT_STATE.lastPacketTime > 0 && mqttAge > 12000) {
                    // Stale data: stream dropped mid-session — freeze model (12 s tolerance for 5 s telemetry)
                    if (!MQTT_STATE.isStale) {
                        MQTT_STATE.isStale = true;
                        addLog('⚠ MQTT stream stale (>12 s) — 3D replica frozen. Check ESP32.', 'warning');
                    }
                    active3dRpm = 0;
                } else {
                    MQTT_STATE.isStale = false;
                    active3dRpm = MQTT_STATE.rpm; // the ONLY legitimate source
                }
            } else {
                active3dRpm = 0; // No hardware → always frozen
            }
            SIM.rpm = active3dRpm; // keep in sync for telemetry readouts

            // ── Per-frame Live Replica HUD updates ───────────────────────────
            const staleEl = document.getElementById('replica-stale-warning');
            if (staleEl) {
                if (MQTT_STATE.isStale) staleEl.classList.remove('hidden');
                else staleEl.classList.add('hidden');
            }
            const freshnessEl = document.getElementById('mqtt-freshness-fill');
            if (freshnessEl) {
                if (!HW.connected || MQTT_STATE.lastPacketTime === 0) {
                    freshnessEl.style.width = '0%';
                    freshnessEl.style.background = 'var(--border-subtle)';
                } else {
                    const freshPct = Math.max(0, 1 - mqttAge / 10000) * 100;
                    freshnessEl.style.width = freshPct + '%';
                    freshnessEl.style.background =
                        freshPct > 50 ? 'var(--status-ok)' :
                            freshPct > 20 ? 'var(--status-warn)' : 'var(--status-crit)';
                }
            }
            const latEl = document.getElementById('model-replica-latency');
            if (latEl) {
                if (!HW.connected || MQTT_STATE.lastPacketTime === 0)
                    latEl.textContent = '—';
                else if (mqttAge === Infinity)
                    latEl.textContent = 'No data';
                else
                    latEl.textContent = mqttAge < 1000
                        ? `${Math.round(mqttAge)} ms ago`
                        : `${(mqttAge / 1000).toFixed(1)} s ago`;
            }
            // Sensor-noise tolerance for display only — never applied to a true 0
            // (stop command / motor idle), so STOP always reads exactly 0.
            // Refreshed only every 3s (see REPLICA_HUD_REFRESH_MS below) so the
            // readout doesn't flicker every render frame.
            const applyTolerance = (value, pct) =>
                value === 0 ? 0 : value * (1 + (Math.random() * 2 - 1) * pct / 100);

            const now = Date.now();
            if (now - lastReplicaHudUpdate >= REPLICA_HUD_REFRESH_MS) {
                lastReplicaHudUpdate = now;
                const replicaRpm = document.getElementById('model-replica-rpm');
                if (replicaRpm) replicaRpm.textContent = HW.connected ? applyTolerance(MQTT_STATE.rpm, 5).toFixed(1) : '—';
                const replicaSpeed = document.getElementById('model-replica-speed');
                if (replicaSpeed) replicaSpeed.textContent = HW.connected ? applyTolerance(MQTT_STATE.beltSpeed, 2).toFixed(3) : '—';
            }
            const replicaProx = document.getElementById('model-replica-prox');
            if (replicaProx) {
                replicaProx.textContent = HW.connected
                    ? (MQTT_STATE.proxDetected ? 'Detected' : 'Idle') : '—';
                replicaProx.style.color = (HW.connected && MQTT_STATE.proxDetected)
                    ? 'var(--status-ok)' : '';
            }
            const replicaCount = document.getElementById('model-replica-count');
            if (replicaCount) replicaCount.textContent = MQTT_STATE.pieceCount.toString();

            // [TAB2-POWER] Live Replica power strip — voltage / current / power / PF.
            // These elements are added in index.html inside the 'Live Replica Status' card.
            // Source: MQTT_STATE.nb2 when RS485 OK, otherwise simulated TEL values.
            // To remove this feature: delete this block AND the four tele-items in index.html.
            const nb2Ok = HW.connected && MQTT_STATE.nb2.rs485Ok;
            const replicaVolt = document.getElementById('model-replica-volt');
            const replicaAmp = document.getElementById('model-replica-amp');
            const replicaPwr = document.getElementById('model-replica-pwr');
            const replicaPF = document.getElementById('model-replica-pf');

            if (replicaVolt) {
                const v = nb2Ok ? MQTT_STATE.nb2.voltage : '--';
                replicaVolt.textContent = v === '--' ? '—' : parseFloat(v).toFixed(1);
                // Color: red if voltage deviates >15% from rated 220V AC
                if (v !== '--') {
                    const vn = parseFloat(v);
                    replicaVolt.style.color = (vn < 180 || vn > 250) ? 'var(--status-crit)' : 'var(--text-val)';
                } else {
                    replicaVolt.style.color = 'var(--text-val)';
                }
            }
            if (replicaAmp) {
                const a = nb2Ok ? MQTT_STATE.nb2.current : '--';
                replicaAmp.textContent = a === '--' ? '—' : parseFloat(a).toFixed(3);
                // Color: amber warning if AC current exceeds normal station draw
                if (a !== '--') {
                    const an = parseFloat(a);
                    replicaAmp.style.color = an > 10.0 ? 'var(--status-warn)' : an > 15.0 ? 'var(--status-crit)' : 'var(--text-val)';
                } else {
                    replicaAmp.style.color = 'var(--text-val)';
                }
            }
            if (replicaPwr) {
                const p = nb2Ok ? (MQTT_STATE.nb2.activePower / 1000) : '--';
                replicaPwr.textContent = p === '--' ? '—' : parseFloat(p).toFixed(3);
            }
            if (replicaPF) {
                const pf = nb2Ok ? MQTT_STATE.nb2.powerFactor : '--';
                replicaPF.textContent = pf === '--' ? '—' : parseFloat(pf).toFixed(2);
                // Color: amber if PF < 0.80 (poor power factor)
                if (pf !== '--') {
                    const pfn = parseFloat(pf);
                    replicaPF.style.color = pfn < 0.80 ? 'var(--status-warn)' : 'var(--text-val)';
                } else {
                    replicaPF.style.color = 'var(--text-val)';
                }
            }

            // [TAB2-SPARKLINE] Push voltage history for mini sparkline chart.
            // powerSparklineHistory is declared near the top of initBabylon().
            // To remove sparkline: delete this push + the canvas in index.html.
            if (nb2Ok || HW.connected) {
                const vPush = nb2Ok ? MQTT_STATE.nb2.voltage : parseFloat(TEL.volt) || 0;
                powerSparklineHistory.push(vPush);
                if (powerSparklineHistory.length > 10) powerSparklineHistory.shift();
                drawPowerSparkline(powerSparklineHistory);
            }

        } else if (currentActiveTab === 'simulation') {
            // ── Tab 3 Simulation belt animation — FULLY INDEPENDENT from Tab 2 ──
            // Source: simEngine.rpm (sim_engine.js isolated physics object).
            // Independent of hardware connection status.
            // Gated on isRunning: when paused, simEngine.rpm holds its last
            // nonzero value (physics tick stopped, not zeroed), so without this
            // check the belt would keep spinning forever at that frozen rate.
            if (typeof simEngine !== 'undefined' && simEngine.isRunning) {
                active3dRpm = simEngine.rpm;
            } else {
                active3dRpm = 0;
            }
        } else {
            // Overview / Optimization tabs — canvas hidden, freeze
            active3dRpm = 0;
        }

        // ── Animate belt, pulleys and GLB groups using active3dRpm ────────────
        // active3dRpm source:
        //   Tab 2 (3D Model)  → MQTT_STATE.rpm  (real hardware only, frozen in sim)
        //   Tab 3 (Simulation) → simEngine.rpm  (isolated physics, no HW coupling)
        //   Other tabs         → 0 (canvas hidden)
        if (Math.abs(active3dRpm) > 0.1) {
            const rotationStep = ((active3dRpm * Math.PI) / 30) * delta;
            kinematics.pulleys.forEach(pulley => {
                pulley.rotate(BABYLON.Axis.Z, rotationStep, BABYLON.Space.LOCAL);
            });

            // Fault multiplier: Tab 3 uses simEngine.faults; Tab 2 uses SIM.faults
            const activeFaults = (currentActiveTab === 'simulation' && typeof simEngine !== 'undefined')
                ? simEngine.faults : SIM.faults;
            const speedMultiplier = activeFaults.beltOverload ? 0.35 : 1.0;

            // UV scroll: calibrated from real measurement (167 RPM → 8 s per loop)
            // BELT_UV_CONSTANT = 167 × 8000 = 1,336,000
            const uvScrollStep = (active3dRpm / BELT_UV_CONSTANT) * engine.getDeltaTime() * speedMultiplier;
            kinematics.belts.forEach(belt => {
                if (belt.albedoTexture) {
                    belt.albedoTexture.vOffset -= uvScrollStep;
                    belt.albedoTexture.vOffset = belt.albedoTexture.vOffset % 1.0;
                }
            });

            // Belt speed display: accurate calibrated linear speed (m/s)
            let beltSpeed = rpmToBeltSpeed(active3dRpm) * speedMultiplier;
            TEL.speed = beltSpeed.toFixed(3);
            TEL.dirty = true;

            const beltMesh = scene.getMeshByName('Main_Belt');
            let beltY = 0.14;
            if (beltMesh) {
                beltMesh.computeWorldMatrix(true);
                beltY = beltMesh.getBoundingInfo().boundingBox.maximumWorld.y;
            }

            if (animationGroups) {
                animationGroups.forEach(group => {
                    // Base speed ratio at max RPM (167) = 1.0x, scaled from real calibration
                    group.speedRatio = (active3dRpm / 167.0) * speedMultiplier;
                    if (!group.isPlaying) group.play(true);
                });
            }
        } else {
            if (animationGroups) animationGroups.forEach(group => group.stop());
            TEL.speed = '0.00';
            TEL.dirty = true;
        }


        // Product follow camera
        if (SIM.cameraMode === 'product' && camera) {
            const leadBox = kinematics.boxes[0];
            if (leadBox) {
                camera.target = BABYLON.Vector3.Lerp(camera.target, leadBox.position, 0.15);
                camera.radius = BABYLON.Scalar.Lerp(camera.radius, 0.45, 0.1);
                camera.alpha = BABYLON.Scalar.Lerp(camera.alpha, Math.PI, 0.1);
                camera.beta = BABYLON.Scalar.Lerp(camera.beta, Math.PI / 3, 0.1);
            } else {
                camera.target = BABYLON.Vector3.Lerp(camera.target, BABYLON.Vector3.Zero(), 0.05);
                camera.radius = BABYLON.Scalar.Lerp(camera.radius, 2.2, 0.05);
            }
        }
    });

    // -------------------------------------------------------------------------
    // 3D POINTER INTERACTIONS
    // -------------------------------------------------------------------------
    scene.onPointerObservable.add((pointerInfo) => {
        switch (pointerInfo.type) {
            case BABYLON.PointerEventTypes.POINTERDOWN: {
                let isHit = false;
                if (pointerInfo.pickInfo.hit && pointerInfo.pickInfo.pickedMesh) {
                    const mesh = pointerInfo.pickInfo.pickedMesh;
                    if (!mesh.name.includes('Room_') && !mesh.name.includes('Light_') &&
                        mesh.name !== 'canvas3d' && !mesh.name.includes('Background')) {
                        isHit = true;
                        if (SIM.selectedMesh && SIM.selectedMesh !== mesh) {
                            SIM.selectedMesh.renderOutline = false;
                        }
                        SIM.selectedMesh = mesh;
                        mesh.renderOutline = true;
                        // Professional blue outline — matches --accent (#0284C7)
                        mesh.outlineColor = new BABYLON.Color3(0.067, 0.518, 0.784);
                        mesh.outlineWidth = 0.008;
                        updateInspector(mesh);
                        animateCameraTarget(camera, mesh.absolutePosition, scene);
                        addLog(`Inspecting: ${mesh.name}`, 'success');
                    }
                }
                if (!isHit) {
                    if (SIM.selectedMesh) {
                        SIM.selectedMesh.renderOutline = false;
                        SIM.selectedMesh = null;
                        updateInspector(null);
                        animateCameraTarget(camera, BABYLON.Vector3.Zero(), scene);
                        addLog('Selection cleared — viewport reset.', 'info');
                    }
                }
                break;
            }
            case BABYLON.PointerEventTypes.POINTERMOVE: {
                if (pointerInfo.pickInfo.hit && pointerInfo.pickInfo.pickedMesh) {
                    const mesh = pointerInfo.pickInfo.pickedMesh;
                    if (!mesh.name.includes('Room_') && !mesh.name.includes('Light_') &&
                        mesh.name !== 'canvas3d' && !mesh.name.includes('Background')) {
                        canvas.style.cursor = 'pointer';
                        if (mesh !== SIM.selectedMesh) {
                            mesh.renderOutline = true;
                            mesh.outlineColor = new BABYLON.Color3(0.067, 0.518, 0.784);
                            mesh.outlineWidth = 0.004;
                        }
                    } else {
                        canvas.style.cursor = 'default';
                    }
                } else {
                    canvas.style.cursor = 'default';
                }
                scene.meshes.forEach(m => {
                    if (m !== SIM.selectedMesh && m !== pointerInfo.pickInfo.pickedMesh) {
                        m.renderOutline = false;
                    }
                });
                break;
            }
        }
    });

    // -------------------------------------------------------------------------
    // FOCUS CAMERA BUTTON
    // -------------------------------------------------------------------------
    if (UI.btnFocusPart) {
        UI.btnFocusPart.addEventListener('click', () => {
            if (SIM.selectedMesh && camera) {
                animateCameraTarget(camera, SIM.selectedMesh.absolutePosition, scene);
                BABYLON.Animation.CreateAndStartAnimation(
                    'animCamRadius', camera, 'radius',
                    60, 30, camera.radius, 1.4,
                    BABYLON.Animation.ANIMATIONLOOPMODE_CONSTANT
                );
            }
        });
    }

    // -------------------------------------------------------------------------
    // CAMERA MODE SELECT
    // -------------------------------------------------------------------------
    if (UI.selectCameraMode) {
        UI.selectCameraMode.addEventListener('change', (e) => {
            SIM.cameraMode = e.target.value;
            if (SIM.cameraMode === 'orbit') {
                animateCameraTarget(camera, BABYLON.Vector3.Zero(), scene);
                BABYLON.Animation.CreateAndStartAnimation(
                    'animCamRadiusReset', camera, 'radius',
                    60, 30, camera.radius, 2.2,
                    BABYLON.Animation.ANIMATIONLOOPMODE_CONSTANT
                );
                addLog('Camera: Free Orbit.', 'info');

            } else if (SIM.cameraMode === 'product') {
                addLog('Camera: Product Follow — tracking lead package.', 'info');
            }
        });
    }

    // NOTE: btn-fault-motor/screw/belt/reset live exclusively in the Simulation
    // tab (Tab 3) and are owned entirely by sim_engine.js (simEngine.faults).
    // app.js must never bind to them — doing so previously leaked fault state
    // into this file's legacy SIM.faults (Tab 2), breaking tab isolation.

    // ---------------------------------
    function generateTelemetry() {
        // Skip simulation when live hardware is connected
        if (!SIM.isRunning || HW.connected) return;

        // Datasheet: XYT-JGB37-555-1250, 12V, 1:30 ratio
        // No Load: 0.2A | Rated Load: 1.2A | Stall: 6.5A
        const loadFraction = SIM.rpm / SIM.maxRpm; // 0 (stall) → 1 (max speed)
        const noise = (Math.random() - 0.5) * 0.05;

        // Current: at no-load (full speed) → 0.2A; at rated load → 1.2A; at stall → 6.5A.
        // Also increases with the simulated material load: e.g. +0.18A per kg of load.
        let baseCurrent = MOTOR.stallCurrent - (MOTOR.stallCurrent - MOTOR.noLoadCurrent) * loadFraction;
        baseCurrent += SIM.loadKg * 0.18;
        if (SIM.faults.motorOverheat) baseCurrent = Math.min(baseCurrent * 3.2, MOTOR.stallCurrent * 1.1);
        if (SIM.faults.beltOverload) baseCurrent = Math.min(baseCurrent * 2.1, MOTOR.stallCurrent);

        SIM.current += (baseCurrent - SIM.current) * 0.25 + (Math.random() - 0.5) * 0.05;
        if (SIM.current < 0) SIM.current = 0;

        // Voltage defaults to user selected value (SIM.voltage), and sags under current load
        const sourceVoltage = SIM.voltage || MOTOR.ratedVoltage;
        const calculatedVoltage = Math.max(0, sourceVoltage - (SIM.current * 0.3) + (Math.random() - 0.5) * 0.05);

        const pf = SIM.faults.beltOverload ? 0.62 : (0.85 + (Math.random() - 0.5) * 0.02);
        const powerKw = (calculatedVoltage * SIM.current * pf) / 1000.0;

        // Write to double-buffer
        TEL.rpm = (SIM.rpm + (noise * 3)).toFixed(1);
        TEL.amp = SIM.current.toFixed(2);
        TEL.volt = calculatedVoltage.toFixed(1);
        TEL.power = powerKw.toFixed(3);
        TEL.pf = pf.toFixed(2);

        // Update non-engineering KPIs
        updateBusinessKPIs(powerKw);

        // AI Predictive values
        let rul = '142.5 d';
        let anomaly = 0.8 + (Math.random() - 0.5) * 0.3;
        let efficiency = 94.2 - (SIM.rpm / SIM.maxRpm) * 2.0 + (Math.random() - 0.5) * 0.2;
        let nextService = 'Nominal';
        let serviceColor = 'var(--text-label)';
        let anomalyColor = 'var(--text-secondary)';

        if (SIM.faults.motorOverheat) {
            rul = `${(1.5 + Math.random() * 1.5).toFixed(1)} h`;
            anomaly = 98.4 + Math.random() * 1.2;
            efficiency = 41.5 + (Math.random() - 0.5) * 1.5;
            nextService = 'Immediate Abort';
            serviceColor = 'var(--status-crit)';
            anomalyColor = 'var(--status-crit)';
        } else if (SIM.faults.beltOverload) {
            rul = `${(7.8 + Math.random() * 1.2).toFixed(1)} d`;
            anomaly = 84.7 + Math.random() * 2.1;
            efficiency = 58.1 + (Math.random() - 0.5) * 1.0;
            nextService = 'Adjust Tension';
            serviceColor = 'var(--status-warn)';
            anomalyColor = 'var(--status-warn)';
        } else if (SIM.faults.looseScrew) {
            rul = `${(13.5 + Math.random() * 1.5).toFixed(1)} d`;
            anomaly = 34.2 + Math.random() * 1.8;
            efficiency = 88.7 + (Math.random() - 0.5) * 0.5;
            nextService = 'Tighten Fastener';
            serviceColor = 'var(--status-warn)';
            anomalyColor = 'var(--status-warn)';
        }

        // Proximity and Temperature are ONLY sourced from real MQTT hardware — never simulated
        // TEL.proximity and TEL.tempVal remain unchanged here (set by parseTelemetry only)

        TEL.rul = rul;
        TEL.anomaly = `${anomaly.toFixed(1)}%`;
        TEL.anomalyColor = anomalyColor;
        TEL.efficiency = `${efficiency.toFixed(1)}%`;
        TEL.service = nextService;
        TEL.serviceColor = serviceColor;
        TEL.dirty = true;

        if (SIM.selectedMesh) updateInspector(SIM.selectedMesh);
    }

    // ============================================================================
    // SYSTEM CONTROLS
    // ============================================================================
    let stopRampInterval = null;

    function completeStop() {
        SIM.isRunning = false;
        SIM.targetRpm = 0;
        SIM.rpm = 0;
        SIM.current = 0;
        SIM.voltage = MOTOR.ratedVoltage;
        clearInterval(SIM.interval);
        SIM.interval = null;
        if (stopRampInterval) {
            clearInterval(stopRampInterval);
            stopRampInterval = null;
        }
        if (UI.statusPill) UI.statusPill.classList.remove('active');

        // Clear walk preset selection
        [UI.btnWalkSlow, UI.btnWalkMedium, UI.btnWalkFast].forEach(btn => {
            if (btn) btn.classList.remove('prog-active');
        });
        if (UI.walkSettleBanner) UI.walkSettleBanner.classList.add('hidden');
        clearTimeout(walkSettleTimer);

        TEL.rpm = '0.0'; TEL.amp = '0.00'; TEL.volt = MOTOR.ratedVoltage.toFixed(1);
        TEL.power = '0.000'; TEL.speed = '0.00';
        TEL.dirty = true;

        if (HW.connected) {
            // Return firmware to MANUAL mode after stop so MQTT commands
            // cannot restart the belt without an explicit operator action.
            sendCmdObject({ cmd: 'stop' });
            HW.running = false;
            if (UI.status) UI.status.textContent = 'Hardware Mode';
            addLog('[HW] Motor stop command sent (gradual stop completed).', 'info');
        } else {
            if (UI.status) UI.status.textContent = 'Standby';
            addLog('System stopped — entering standby.', 'info');
        }
    }

    if (UI.btnRun) {
        UI.btnRun.addEventListener('click', () => {
            if (stopRampInterval) {
                clearInterval(stopRampInterval);
                stopRampInterval = null;
                SIM.isRunning = false; // reset running state to allow restart
            }
            if (SIM.isRunning) return;
            SIM.isRunning = true;
            const inputVal = UI.inputRpm ? parseFloat(UI.inputRpm.value) : NaN;
            SIM.targetRpm = !isNaN(inputVal) && inputVal > 0 ? inputVal : 120;
            if (UI.statusPill) UI.statusPill.classList.add('active');

            if (HW.connected) {
                // Hardware mode: translate RPM setpoint → PWM% and send to ESP32.
                // The firmware boots in MANUAL mode and blocks motor commands until
                // it receives {"mode":"remote"}. Always send the mode switch first.
                const speedPct = Math.min(100, Math.round((SIM.targetRpm / SIM.maxRpm) * 100));
                sendCmdObject({ mode: 'remote' });
                setTimeout(() => {
                    sendCmdObject({ cmd: 'start', dir: 'fwd', speed: speedPct });
                }, 80); // small delay so firmware processes mode switch before motor cmd
                HW.running = true;
                if (UI.status) UI.status.textContent = 'Hardware Mode';
                addLog(`[HW] Motor engaged — Forward ${speedPct}% PWM (setpoint: ${SIM.targetRpm} RPM).`, 'success');
            } else {
                // Simulation mode
                if (UI.status) UI.status.textContent = 'Data Stream Active';
                SIM.interval = setInterval(generateTelemetry, 200);
                addLog('System engaged — data stream active.', 'success');
            }
        });
    }

    if (UI.btnStop) {
        UI.btnStop.addEventListener('click', () => {
            if (!SIM.isRunning) return;
            if (stopRampInterval) return; // already decelerating

            addLog('System decelerating gradually...', 'info');
            if (UI.status) UI.status.textContent = 'Decelerating...';

            let rampRpm = SIM.rpm;
            stopRampInterval = setInterval(() => {
                rampRpm -= 20; // Decelerate by 20 RPM per step
                // [STOP-FIX] Threshold was <=10, which caused the display to
                // show a residual ~16 RPM (last ramp step before trigger).
                // Changed to <=0 so the motor always ramps fully to zero.
                if (rampRpm <= 0) {
                    completeStop();
                } else {
                    SIM.targetRpm = rampRpm;
                    if (HW.connected) {
                        const speedPct = Math.min(100, Math.round((rampRpm / SIM.maxRpm) * 100));
                        sendCmdObject({ cmd: "start", dir: "fwd", speed: speedPct });
                    }
                }
            }, 200); // 200ms step interval for smooth ramp
        });
    }

    if (UI.btnSetRpm) {
        UI.btnSetRpm.addEventListener('click', () => {
            if (!UI.inputRpm) return;
            let val = parseFloat(UI.inputRpm.value);
            if (isNaN(val) || val < 0) val = 0;
            if (val > SIM.maxRpm) val = SIM.maxRpm;
            UI.inputRpm.value = val;
            if (SIM.isRunning) {
                SIM.targetRpm = val;
                if (HW.connected) {
                    const speedPct = Math.min(100, Math.round((val / SIM.maxRpm) * 100));
                    sendCmdObject({ cmd: "start", dir: "fwd", speed: speedPct });
                    addLog(`[HW] Speed updated → F${speedPct}% PWM (setpoint: ${val} RPM).`, 'info');
                } else {
                    addLog(`RPM setpoint updated → ${val} RPM.`, 'info');
                }
            }
        });
    }

    // ============================================================================
    // SPEED PROGRAM (WALK PRESET) BUTTONS
    // ============================================================================
    let walkSettleTimer = null;
    let activeWalkPreset = null;

    // Send a heartbeat every 1.5s to keep the ESP32 watchdog from force-stopping the motor
    setInterval(() => {
        if (HW.connected && SIM.isRunning) {
            sendCmdObject({ mode: 'remote' });
        }
    }, 1500);

    function activateWalkPreset(preset) {
        if (!HW.connected) return;

        // Highlight the active button, clear others
        [UI.btnWalkSlow, UI.btnWalkMedium, UI.btnWalkFast].forEach(btn => {
            if (btn) btn.classList.remove('prog-active');
        });
        const map = { slow: UI.btnWalkSlow, medium: UI.btnWalkMedium, fast: UI.btnWalkFast };
        if (map[preset]) map[preset].classList.add('prog-active');
        activeWalkPreset = preset;

        // Arm the motor in REMOTE mode then send walk command
        sendCmdObject({ mode: 'remote' });
        setTimeout(() => sendCmdObject({ walk: preset }), 80);

        HW.lastCommandTime = Date.now();
        SIM.isRunning = true;
        if (UI.statusPill) UI.statusPill.classList.add('active');
        if (UI.status) UI.status.textContent = 'Hardware Mode';
        addLog(`[HW] Walk preset → ${preset.toUpperCase()} (PWM direct, bypasses RPM math).`, 'success');

        // Show settle banner for 4 s
        if (UI.walkSettleBanner) UI.walkSettleBanner.classList.remove('hidden');
        clearTimeout(walkSettleTimer);
        walkSettleTimer = setTimeout(() => {
            if (UI.walkSettleBanner) UI.walkSettleBanner.classList.add('hidden');
        }, 4000);
    }

    if (UI.btnWalkSlow) UI.btnWalkSlow.addEventListener('click', () => activateWalkPreset('slow'));
    if (UI.btnWalkMedium) UI.btnWalkMedium.addEventListener('click', () => activateWalkPreset('medium'));
    if (UI.btnWalkFast) UI.btnWalkFast.addEventListener('click', () => activateWalkPreset('fast'));

    // ============================================================================
    // E-STOP — Hold-to-Activate (1 second hold required)
    // ============================================================================
    function executeEmergencyStop() {
        if (HW.connected) {
            sendCmdObject({ cmd: 'stop' });
            HW.running = false;
        }

        SIM.isRunning = false;
        SIM.targetRpm = 0;
        SIM.rpm = 0;
        SIM.current = 0;
        SIM.voltage = 24.0;
        clearInterval(SIM.interval);

        if (UI.statusPill) UI.statusPill.classList.remove('active');
        TEL.rpm = '0.0'; TEL.amp = '0.00'; TEL.volt = '24.0';
        TEL.power = '0.000'; TEL.speed = '0.00';
        TEL.dirty = true;

        addLog('⬡ EMERGENCY STOP activated — system immediately halted.', 'error');
    }

    if (UI.btnEstop) {
        let pressTimer = null;
        const progress = UI.btnEstop.querySelector('.estop-progress');

        function cancelPress() {
            clearTimeout(pressTimer);
            pressTimer = null;
            UI.btnEstop.classList.remove('is-pressing');
        }

        UI.btnEstop.addEventListener('mousedown', () => {
            UI.btnEstop.classList.add('is-pressing');
            pressTimer = setTimeout(() => {
                executeEmergencyStop();
                UI.btnEstop.classList.remove('is-pressing');
            }, 1000);
        });

        UI.btnEstop.addEventListener('touchstart', (e) => {
            e.preventDefault();
            UI.btnEstop.classList.add('is-pressing');
            pressTimer = setTimeout(() => {
                executeEmergencyStop();
                UI.btnEstop.classList.remove('is-pressing');
            }, 1000);
        }, { passive: false });

        UI.btnEstop.addEventListener('mouseup', cancelPress);
        UI.btnEstop.addEventListener('mouseleave', cancelPress);
        UI.btnEstop.addEventListener('touchend', cancelPress);
        UI.btnEstop.addEventListener('touchcancel', cancelPress);
    }

    // ============================================================================
    // LOG COLLAPSIBLE TOGGLE
    // ============================================================================
    if (UI.logToggle && UI.logPanelSection) {
        UI.logToggle.addEventListener('click', () => {
            UI.logPanelSection.classList.toggle('collapsed');
        });
    }

    // ============================================================================
    // TAB NAVIGATION & OPTIMIZATION MODULE
    // ============================================================================

    function initTabNavigation() {
        const tabs = document.querySelectorAll('.tab-btn');
        const views = document.querySelectorAll('.tab-panel');
        const canvasElement = document.getElementById('canvas3d');

        // Default: Executive Overview is active, canvas3d is hidden
        if (canvasElement) {
            canvasElement.classList.add('hidden');
        }

        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const targetTab = tab.getAttribute('data-tab');

                // Deactivate all tabs and views
                tabs.forEach(t => {
                    t.classList.remove('active');
                    t.setAttribute('aria-selected', 'false');
                });
                views.forEach(v => v.classList.remove('active'));

                // Activate target tab and view
                tab.classList.add('active');
                tab.setAttribute('aria-selected', 'true');
                const targetView = document.getElementById(`tab-${targetTab}`);
                if (targetView) {
                    targetView.classList.add('active');
                }

                // Hide/Show 3D Canvas
                if (targetTab === 'model' || targetTab === 'simulation') {
                    if (canvasElement) {
                        canvasElement.classList.remove('hidden');

                        // Move the canvas to the correct placeholder
                        const modelPlaceholder = document.getElementById('model-canvas-wrap');
                        const simPlaceholder = document.getElementById('sim-viewport-placeholder');
                        if (targetTab === 'model' && modelPlaceholder) {
                            modelPlaceholder.appendChild(canvasElement);
                            canvasElement.style.position = 'absolute';
                            canvasElement.style.inset = '0';
                            if (typeof engine !== 'undefined' && engine) engine.resize();
                        } else if (targetTab === 'simulation' && simPlaceholder) {
                            simPlaceholder.appendChild(canvasElement);
                            canvasElement.style.position = 'absolute';
                            canvasElement.style.inset = '0';
                        } else {
                            document.body.appendChild(canvasElement);
                        }

                        // Force Babylon.js engine resize to fit viewport
                        if (engine) {
                            engine.resize();
                        }
                    }
                    // Sync camera mode
                    if (UI.selectCameraMode) {
                        SIM.cameraMode = UI.selectCameraMode.value;
                    }
                } else {
                    if (canvasElement) {
                        canvasElement.classList.add('hidden');
                    }
                }

                currentActiveTab = targetTab;
                updateSimControlsState();
                addLog(`Navigation: Switched to ${tab.textContent.trim()} tab.`, 'info');
            });
        });

        // Optimization tab bindings
        const btnToggleEco = document.getElementById('btn-toggle-eco');
        const ecoModeStatus = document.getElementById('eco-mode-status');
        const btnOptimizeSpeed = document.getElementById('btn-optimize-speed');
        const optLog = document.getElementById('optimization-log');

        function addOptLog(msg, type = 'info') {
            const time = new Date().toLocaleTimeString('en-US', { hour12: false });
            const div = document.createElement('div');
            div.className = `log-entry ${type}`;
            div.innerHTML = `<span class="timestamp">[${time}]</span> ${msg}`;
            if (optLog) {
                optLog.appendChild(div);
                optLog.scrollTop = optLog.scrollHeight;
            }
        }

        if (btnToggleEco && ecoModeStatus) {
            btnToggleEco.addEventListener('click', () => {
                optState.ecoMode = !optState.ecoMode;
                if (optState.ecoMode) {
                    ecoModeStatus.textContent = 'ACTIVE';
                    ecoModeStatus.style.color = 'var(--status-ok)';
                    btnToggleEco.textContent = 'Disable Eco-Mode';
                    btnToggleEco.className = 'btn-aero primary w-100 mt-2';
                    addOptLog('Eco-Mode: Activated energy saving profile. Motor current setpoint optimized.', 'success');
                    addLog('Optimization: Eco-Mode enabled.', 'success');
                } else {
                    ecoModeStatus.textContent = 'INACTIVE';
                    ecoModeStatus.style.color = 'var(--status-warn)';
                    btnToggleEco.textContent = 'Enable Eco-Mode';
                    btnToggleEco.className = 'btn-aero outline w-100 mt-2';
                    addOptLog('Eco-Mode: Deactivated. System running on standard performance profile.', 'warning');
                    addLog('Optimization: Eco-Mode disabled.', 'warning');
                }
            });
        }

        if (btnOptimizeSpeed) {
            btnOptimizeSpeed.addEventListener('click', () => {
                addOptLog('Optimizer: Initiating speed optimization sweep...', 'info');
                btnOptimizeSpeed.disabled = true;
                btnOptimizeSpeed.textContent = 'Optimizing...';
                setTimeout(() => {
                    const optRpm = Math.floor(110 + Math.random() * 20);
                    addOptLog(`Optimizer: Sweep completed. Peak efficiency ratio identified at ${optRpm} RPM.`, 'success');
                    addLog(`Optimization: Speed optimizer sweep completed. Recommended setpoint: ${optRpm} RPM.`, 'success');
                    btnOptimizeSpeed.disabled = false;
                    btnOptimizeSpeed.textContent = 'Run Speed Optimization Sweep';
                }, 1500);
            });
        }

        // Theme toggle logic
        const btnThemeToggle = document.getElementById('btn-theme-toggle');
        const themeToggleIcon = document.getElementById('theme-toggle-icon');
        const themeToggleText = document.getElementById('theme-toggle-text');

        if (btnThemeToggle) {
            btnThemeToggle.addEventListener('click', () => {
                const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
                const nextTheme = currentTheme === 'light' ? 'dark' : 'light';

                // Toggle HTML attribute
                document.documentElement.setAttribute('data-theme', nextTheme);

                // Update button UI
                if (themeToggleIcon) themeToggleIcon.textContent = nextTheme === 'light' ? '🌙' : '☀️';
                if (themeToggleText) themeToggleText.textContent = nextTheme === 'light' ? 'Dark Mode' : 'Light Mode';

                // Update Babylon.js scene clear color
                if (scene) {
                    if (nextTheme === 'light') {
                        scene.clearColor = new BABYLON.Color4(0.945, 0.961, 0.976, 1.0);
                    } else {
                        scene.clearColor = new BABYLON.Color4(0.059, 0.090, 0.165, 1.0);
                    }
                }

                // Update pedestal material color
                const pedestal = scene.getMeshByName('pedestal');
                if (pedestal && pedestal.material) {
                    if (nextTheme === 'light') {
                        pedestal.material.albedoColor = new BABYLON.Color3(0.278, 0.333, 0.412);
                    } else {
                        pedestal.material.albedoColor = new BABYLON.Color3(0.12, 0.16, 0.23);
                    }
                }

                addLog(`System Theme: Switched to ${nextTheme.toUpperCase()} mode.`, 'info');
            });
        }
    }

    // Initialize navigation on load — sync control states immediately on boot
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => { initTabNavigation(); updateSimControlsState(); });
    } else {
        initTabNavigation();
        updateSimControlsState();
    }
});

// --- Live Camera Feed Controls ---
// The camera panel is a docked flex sibling of #model-canvas-wrap (see style.css
// .cam-overlay-box), so it always reserves its own space next to the 3D canvas —
// no manual offset math needed. Just tell the engine to re-measure the canvas
// whenever the layout changes (panel toggled, window resized).
function updateCanvasOffset() {
    if (typeof engine !== 'undefined' && engine) {
        engine.resize();
    }
}

window.addEventListener('resize', () => {
    updateCanvasOffset();
});

const camOverlayBox = document.getElementById("cam-overlay-box");
const btnToggleCam = document.getElementById("btn-camfeed-toggle");
const camShowBtn = document.getElementById("cam-show-btn");

if (btnToggleCam && camOverlayBox) {
    btnToggleCam.addEventListener("click", () => {
        camOverlayBox.style.display = "none";
        if (camShowBtn) camShowBtn.style.display = "inline-flex";
        updateCanvasOffset();
    });
}

if (camShowBtn && camOverlayBox) {
    camShowBtn.addEventListener("click", () => {
        camOverlayBox.style.display = "flex";
        camShowBtn.style.display = "none";
        updateCanvasOffset();
    });
}

const DEFAULT_ZOOM = 1.0;
let camZoom = DEFAULT_ZOOM;
let panX = 0;
let panY = 0;

const ZOOM_MIN = 1.0;
const ZOOM_MAX = 4.0;
const ZOOM_STEP = 0.25;
const PAN_STEP = 25;

const camZoomIn = document.getElementById("cam-zoom-in");
const camZoomOut = document.getElementById("cam-zoom-out");
const camZoomReset = document.getElementById("cam-zoom-reset");
const zoomFill = document.getElementById("camfeed-zoom-fill");

const camPanUp = document.getElementById("cam-pan-up");
const camPanDown = document.getElementById("cam-pan-down");
const camPanLeft = document.getElementById("cam-pan-left");
const camPanRight = document.getElementById("cam-pan-right");

const camZoomWrapper = document.getElementById("cam-zoom-wrapper");
const camfeedViewport = document.getElementById("camfeed-viewport");
const camFeedIframe = document.getElementById("cam-feed-iframe");

// go2rtc's stream.html won't render its player below 320px wide and overflows a
// smaller panel. Render the iframe at a fixed 640px logical width and scale it
// down to exactly fill the viewport. (camZoom is applied separately on the
// wrapper, so pinch-zoom still composes on top of this.)
const CAM_IFRAME_W = 640;
function fitCamIframe() {
    if (!camFeedIframe || !camfeedViewport) return;
    const s = camfeedViewport.clientWidth / CAM_IFRAME_W;
    if (s > 0) camFeedIframe.style.transform = `scale(${s})`;
}
if (camfeedViewport && typeof ResizeObserver !== "undefined") {
    new ResizeObserver(fitCamIframe).observe(camfeedViewport);
}
window.addEventListener("resize", fitCamIframe);
fitCamIframe();

function clampPan() {
    if (camZoom <= 1.0) {
        panX = 0;
        panY = 0;
        return;
    }

    const vpW = camfeedViewport ? camfeedViewport.clientWidth : 576;
    const vpH = camfeedViewport ? camfeedViewport.clientHeight : 324;

    const maxPanX = ((camZoom - 1.0) / (2.0 * camZoom)) * vpW;
    const maxPanY = ((camZoom - 1.0) / (2.0 * camZoom)) * vpH;

    panX = Math.max(-maxPanX, Math.min(maxPanX, panX));
    panY = Math.max(-maxPanY, Math.min(maxPanY, panY));
}

function updateZoom() {
    clampPan();

    if (zoomFill) {
        const pct = ((camZoom - ZOOM_MIN) / (ZOOM_MAX - ZOOM_MIN)) * 100;
        zoomFill.style.width = `${pct}%`;
    }

    if (camZoomWrapper) {
        camZoomWrapper.style.transform = `scale(${camZoom}) translate(${panX}px, ${panY}px)`;
    }
}

if (camZoomIn) {
    camZoomIn.addEventListener("click", () => {
        camZoom = Math.min(camZoom + ZOOM_STEP, ZOOM_MAX);
        updateZoom();
    });
}

if (camZoomOut) {
    camZoomOut.addEventListener("click", () => {
        camZoom = Math.max(camZoom - ZOOM_STEP, ZOOM_MIN);
        updateZoom();
    });
}

if (camZoomReset) {
    camZoomReset.addEventListener("click", () => {
        camZoom = DEFAULT_ZOOM;
        panX = 0;
        panY = 0;
        updateZoom();
    });
}

if (camPanUp) {
    camPanUp.addEventListener("click", () => {
        if (camZoom > 1.0) {
            panY += PAN_STEP / camZoom;
            updateZoom();
        }
    });
}
if (camPanDown) {
    camPanDown.addEventListener("click", () => {
        if (camZoom > 1.0) {
            panY -= PAN_STEP / camZoom;
            updateZoom();
        }
    });
}
if (camPanLeft) {
    camPanLeft.addEventListener("click", () => {
        if (camZoom > 1.0) {
            panX += PAN_STEP / camZoom;
            updateZoom();
        }
    });
}
if (camPanRight) {
    camPanRight.addEventListener("click", () => {
        if (camZoom > 1.0) {
            panX -= PAN_STEP / camZoom;
            updateZoom();
        }
    });
}
