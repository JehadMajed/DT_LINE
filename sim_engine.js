/**
 * Digital Twin - Virtual Conveyor Simulation Engine
 * Independent isolated physics engine for the Simulation Tab
 */

const simEngine = {
    isRunning: false,
    timeScale: 1.0,
    t_sim: 0,         // simulated seconds
    lastTick: 0,
    lastGaugeUpdate: 0, // real-time ms, throttles gauge text refresh
    intervalId: null,

    // State
    rpm: 0,
    targetRpm: 0,
    beltSpeed: 0,
    motorTemp: 25.0, // Ambient

    // Physics Constants
    maxRpm: 167,
    rpmToBeltSpeedFactor: 0.1275 / 167, // 167 RPM = 0.1275 m/s
    ambientTemp: 25.0,
    R_th: 2.5,  // Thermal resistance (°C/W)
    C_th: 300,  // Thermal capacitance (J/°C)

    // Scenario
    scenario: null,

    // Faults
    faults: {
        motorOverheat: false,
        looseScrew: false,
        beltOverload: false
    }
};

// UI Elements
const simUI = {
    btnRun: document.getElementById('sim-run-btn'),
    btnPause: document.getElementById('sim-pause-btn'),
    btnReset: document.getElementById('sim-reset-btn'),
    slider: document.getElementById('tsb-slider'),
    displaySpeed: document.getElementById('tsb-display'),
    presets: document.querySelectorAll('.tsb-preset'),
    
    // Gauges
    arcRpm: document.getElementById('g-rpm-arc'),
    valRpm: document.getElementById('g-rpm-val'),
    arcSpeed: document.getElementById('g-speed-arc'),
    valSpeed: document.getElementById('g-speed-val'),
    arcTemp: document.getElementById('g-temp-sim-arc'),
    valTemp: document.getElementById('g-temp-sim-val'),
    
    // Log
    logBody: document.getElementById('sim-log'),
    btnClearLog: document.getElementById('sim-log-clear-btn'),
    
    // Charts
    ctxRpm: document.getElementById('chart-rpm'),
    ctxTemp: document.getElementById('chart-temp-sim'),
    timeBadge: document.getElementById('sim-time-badge')
};

// ============================================================================
// LOGGER
// ============================================================================
function simLog(type, msg) {
    if (!simUI.logBody) return;
    const date = new Date(simEngine.t_sim * 1000);
    const mm = String(date.getUTCMinutes()).padStart(2, '0');
    const ss = String(date.getUTCSeconds()).padStart(2, '0');
    const ms = String(Math.floor(date.getUTCMilliseconds() / 10)).padStart(2, '0');
    
    const div = document.createElement('div');
    div.className = `log-entry ${type}`;
    div.innerHTML = `<span class="log-ts">${mm}:${ss}:${ms}</span><span class="log-msg">${msg}</span>`;
    
    simUI.logBody.appendChild(div);
    simUI.logBody.scrollTop = simUI.logBody.scrollHeight;
}

if (simUI.btnClearLog) {
    simUI.btnClearLog.addEventListener('click', () => {
        simUI.logBody.innerHTML = '';
        simLog('info', 'Simulation log cleared.');
    });
}

// ============================================================================
// CHARTS
// ============================================================================
let chartRpm, chartTemp;

function initCharts() {
    if (!simUI.ctxRpm || !simUI.ctxTemp) return;

    Chart.defaults.color = '#64748B';
    Chart.defaults.font.family = "'Inter', sans-serif";

    const commonOptions = {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { display: false } },
        scales: {
            x: { type: 'linear', display: false, min: 0, max: 600 },
            y: { beginAtZero: true, grid: { color: 'rgba(15, 23, 42, 0.05)' } }
        },
        elements: { point: { radius: 0 }, line: { borderWidth: 2, tension: 0.1 } }
    };

    chartRpm = new Chart(simUI.ctxRpm, {
        type: 'line',
        data: { datasets: [{ data: [], borderColor: '#0284C7', backgroundColor: 'rgba(2,132,199,0.1)', fill: true }] },
        options: { ...commonOptions, scales: { ...commonOptions.scales, y: { ...commonOptions.scales.y, max: 200 } } }
    });

    chartTemp = new Chart(simUI.ctxTemp, {
        type: 'line',
        data: { datasets: [{ data: [], borderColor: '#D97706', backgroundColor: 'rgba(217,119,6,0.1)', fill: true }] },
        options: { ...commonOptions, scales: { ...commonOptions.scales, y: { ...commonOptions.scales.y, max: 100 } } }
    });
}

function updateCharts() {
    if (!chartRpm || !chartTemp) return;
    
    const t = simEngine.t_sim;
    
    // Keep window sliding
    if (t > 600) {
        chartRpm.options.scales.x.min = t - 600;
        chartRpm.options.scales.x.max = t;
        chartTemp.options.scales.x.min = t - 600;
        chartTemp.options.scales.x.max = t;
    }

    chartRpm.data.datasets[0].data.push({ x: t, y: simEngine.rpm });
    chartTemp.data.datasets[0].data.push({ x: t, y: simEngine.motorTemp });
    
    // Trim memory
    if (chartRpm.data.datasets[0].data.length > 1000) {
        chartRpm.data.datasets[0].data.shift();
        chartTemp.data.datasets[0].data.shift();
    }
    
    chartRpm.update();
    chartTemp.update();

    if (simUI.timeBadge) {
        simUI.timeBadge.textContent = `t = ${Math.floor(t)} s`;
    }
}

// ============================================================================
// PHYSICS ENGINE
// ============================================================================
function physicsTick(dt) {
    // 1. Motor RPM (Inertia smoothing)
    const accel = 80; // RPM/s
    if (simEngine.rpm < simEngine.targetRpm) {
        simEngine.rpm = Math.min(simEngine.targetRpm, simEngine.rpm + accel * dt);
    } else if (simEngine.rpm > simEngine.targetRpm) {
        simEngine.rpm = Math.max(simEngine.targetRpm, simEngine.rpm - accel * dt);
    }
    
    // Fault modifiers
    if (simEngine.faults.beltOverload) {
        simEngine.rpm *= 0.6; // Load drags speed down
    }

    // 2. Belt Speed
    simEngine.beltSpeed = simEngine.rpm * simEngine.rpmToBeltSpeedFactor;

    // 3. Thermal Model (1st-order RC)
    // P_loss ? (RPM/167)^2 * 50W (base friction/copper loss)
    let p_loss = Math.pow(simEngine.rpm / 167, 2) * 50.0;
    if (simEngine.faults.beltOverload) p_loss *= 2.0; // Higher friction
    if (simEngine.faults.motorOverheat) p_loss += 150.0; // Fault injection

    const dT_dt = (p_loss - (simEngine.motorTemp - simEngine.ambientTemp) / simEngine.R_th) / simEngine.C_th;
    simEngine.motorTemp += dT_dt * dt;

    // Bridge to Babylon.js (assuming app.js SIM global exists)
    if (typeof window !== 'undefined' && window.SIM) {
        window.SIM.rpm = simEngine.rpm;
        window.SIM.faults = simEngine.faults;
    }
}

// ============================================================================
// SCENARIOS
// ============================================================================
const SCENARIOS = [
    {
        id: 's1',
        name: 'Slow Conveyor',
        risk: 'LOW',
        desc: 'Steady 30% duty cycle for low-throughput visual inspection.',
        targetRpm: 50,
        faults: {},
        duration: 600
    },
    {
        id: 's2',
        name: 'Full Throughput',
        risk: 'MEDIUM',
        desc: 'Maximum rated speed (100% duty). Monitor thermal rise over 10 minutes.',
        targetRpm: 167,
        faults: {},
        duration: 600
    },
    {
        id: 's3',
        name: 'Thermal Stress',
        risk: 'HIGH',
        desc: 'Motor cooling fan failure at full load. Watch junction temperature rise rapidly.',
        targetRpm: 167,
        faults: { motorOverheat: true },
        duration: 300
    },
    {
        id: 's4',
        name: 'Overload + Jam',
        risk: 'CRITICAL',
        desc: 'Belt overloading causes severe friction, dropping speed and raising power draw.',
        targetRpm: 167,
        faults: { beltOverload: true },
        duration: 300
    }
];

function buildScenarioCards() {
    const list = document.getElementById('scenario-list');
    if (!list) return;
    
    list.innerHTML = '';
    SCENARIOS.forEach(sc => {
        const div = document.createElement('div');
        div.className = 'scenario-card';
        div.id = `sc-card-${sc.id}`;
        
        let riskClass = 'normal';
        if (sc.risk === 'MEDIUM') riskClass = 'warning';
        if (sc.risk === 'HIGH') riskClass = 'fault';
        if (sc.risk === 'CRITICAL') riskClass = 'fault';

        div.innerHTML = `
            <div class="sc-header">
                <span class="sc-name">${sc.name}</span>
                <span class="sc-risk ${riskClass}">${sc.risk}</span>
            </div>
            <p class="sc-desc">${sc.desc}</p>
            <div class="sc-params">
                <span class="sc-param">Target RPM: ${sc.targetRpm}</span>
                <span class="sc-param">Duration: ${sc.duration}s</span>
            </div>
            <button class="sc-run-btn" data-id="${sc.id}">Select & Run</button>
        `;
        list.appendChild(div);
    });

    // Bind selection
    list.querySelectorAll('.sc-run-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const sc = SCENARIOS.find(s => s.id === e.target.getAttribute('data-id'));
            if (!sc) return;
            
            // UI styling
            document.querySelectorAll('.scenario-card').forEach(c => c.classList.remove('active'));
            document.getElementById(`sc-card-${sc.id}`).classList.add('active');

            // Apply scenario
            simEngine.scenario = sc;
            simEngine.targetRpm = sc.targetRpm;
            simEngine.faults = { ...sc.faults };
            
            simLog('info', `Loaded Scenario: "${sc.name}"`);
            
            // Auto start
            if (!simEngine.isRunning) {
                toggleSim(true);
            }
        });
    });
}

// ============================================================================
// GAUGES & UI UPDATES
// ============================================================================
function updateGauges() {
    // 173 is the SVG dasharray circumference
    if (simUI.valRpm) {
        simUI.valRpm.textContent = simEngine.rpm.toFixed(1);
        const pRpm = Math.min(1, simEngine.rpm / 200);
        simUI.arcRpm.style.strokeDasharray = `${pRpm * 173} 173`;
    }
    
    if (simUI.valSpeed) {
        simUI.valSpeed.textContent = simEngine.beltSpeed.toFixed(3);
        const pSpd = Math.min(1, simEngine.beltSpeed / 0.15);
        simUI.arcSpeed.style.strokeDasharray = `${pSpd * 173} 173`;
    }
    
    if (simUI.valTemp) {
        simUI.valTemp.textContent = simEngine.motorTemp.toFixed(1);
        const pTemp = Math.min(1, (simEngine.motorTemp - 20) / 80);
        simUI.arcTemp.style.strokeDasharray = `${pTemp * 173} 173`;
    }
}

// ============================================================================
// MAIN ENGINE LOOP
// ============================================================================
function engineStep() {
    if (!simEngine.isRunning) return;
    
    const now = performance.now();
    let dt_real = (now - simEngine.lastTick) / 1000.0;
    simEngine.lastTick = now;

    // Cap dt for lag spikes
    if (dt_real > 0.1) dt_real = 0.1;
    
    const dt_sim = dt_real * simEngine.timeScale;
    simEngine.t_sim += dt_sim;

    // Check scenario end
    if (simEngine.scenario && simEngine.t_sim >= simEngine.scenario.duration) {
        simLog('ok', `Scenario "${simEngine.scenario.name}" completed.`);
        toggleSim(false);
        simEngine.scenario = null;
        document.querySelectorAll('.scenario-card').forEach(c => c.classList.remove('active'));
    }

    physicsTick(dt_sim);

    // Gauge text is throttled to ~10 Hz real-time so digits stay readable
    // even at high time-scale multipliers (physics itself still ticks every frame).
    if (now - simEngine.lastGaugeUpdate >= 100) {
        simEngine.lastGaugeUpdate = now;
        updateGauges();
    }

    // Chart update is expensive, limit to ~10 Hz (in sim time)
    if (Math.floor(simEngine.t_sim * 10) % Math.max(1, Math.floor(10 / simEngine.timeScale)) === 0) {
        updateCharts();
    }
}

function toggleSim(run) {
    if (run) {
        if (!simEngine.isRunning) {
            simEngine.isRunning = true;
            simEngine.lastTick = performance.now();
            simEngine.intervalId = setInterval(engineStep, 16); // ~60fps logic
            simUI.btnRun.disabled = true;
            simUI.btnPause.disabled = false;
            document.getElementById('sim2-state-badge').textContent = 'RUNNING';
            document.getElementById('sim2-state-badge').classList.add('active');
            simLog('ok', `Simulation started at ${simEngine.timeScale}x`);
        }
    } else {
        if (simEngine.isRunning) {
            simEngine.isRunning = false;
            clearInterval(simEngine.intervalId);
            simUI.btnRun.disabled = false;
            simUI.btnPause.disabled = true;
            document.getElementById('sim2-state-badge').textContent = 'PAUSED';
            document.getElementById('sim2-state-badge').classList.remove('active');
            simLog('warn', 'Simulation paused.');
        }
    }
}

function resetSim() {
    toggleSim(false);
    simEngine.t_sim = 0;
    simEngine.rpm = 0;
    simEngine.targetRpm = 0;
    simEngine.motorTemp = 25.0;
    simEngine.faults = { motorOverheat: false, looseScrew: false, beltOverload: false };
    
    // Clear charts
    if (chartRpm) chartRpm.data.datasets[0].data = [];
    if (chartTemp) chartTemp.data.datasets[0].data = [];
    
    updateGauges();
    updateCharts();
    simLog('info', 'Simulation reset to t=0.');
    document.getElementById('sim2-state-badge').textContent = 'Standby';
    
    // Bridge to Babylon
    if (typeof window !== 'undefined' && window.SIM) {
        window.SIM.rpm = 0;
        window.SIM.faults = simEngine.faults;
    }
}

// ============================================================================
// EVENT BINDINGS
// ============================================================================
function initSimEngine() {
    initCharts();
    buildScenarioCards();
    
    // Transport controls
    if (simUI.btnRun) simUI.btnRun.addEventListener('click', () => toggleSim(true));
    if (simUI.btnPause) simUI.btnPause.addEventListener('click', () => toggleSim(false));
    if (simUI.btnReset) simUI.btnReset.addEventListener('click', resetSim);
    
    // Speed slider
    if (simUI.slider) {
        const scales = [0.1, 0.5, 1, 10, 50, 100];
        
        simUI.slider.addEventListener('input', (e) => {
            const val = parseInt(e.target.value);
            simEngine.timeScale = scales[val];
            simUI.displaySpeed.textContent = simEngine.timeScale + 'x';
            
            simUI.presets.forEach(p => p.classList.remove('active'));
            document.getElementById(`tsb-p${val}`).classList.add('active');
            
            simLog('info', `Time scale set to ${simEngine.timeScale}x`);
        });
        
        simUI.presets.forEach(preset => {
            preset.addEventListener('click', (e) => {
                const step = e.target.getAttribute('data-step');
                simUI.slider.value = step;
                simUI.slider.dispatchEvent(new Event('input'));
            });
        });
    }

    // Manual Faults
    const fm = document.getElementById('btn-fault-motor');
    const fs = document.getElementById('btn-fault-screw');
    const fb = document.getElementById('btn-fault-belt');
    const fr = document.getElementById('btn-fault-reset');

    if (fm) fm.addEventListener('click', () => { simEngine.faults.motorOverheat = true; simLog('bad', 'Fault Injected: Motor Overheat'); });
    if (fs) fs.addEventListener('click', () => { simEngine.faults.looseScrew = true; simLog('bad', 'Fault Injected: Loose Screw'); });
    if (fb) fb.addEventListener('click', () => { simEngine.faults.beltOverload = true; simLog('bad', 'Fault Injected: Belt Overload'); });
    if (fr) fr.addEventListener('click', () => { 
        simEngine.faults = { motorOverheat: false, looseScrew: false, beltOverload: false }; 
        simLog('ok', 'All faults cleared.'); 
    });

    updateGauges();
}

// Auto init on load
window.addEventListener('DOMContentLoaded', initSimEngine);
