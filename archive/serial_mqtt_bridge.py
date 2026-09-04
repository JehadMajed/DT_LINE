import serial
import json
import paho.mqtt.client as mqtt
import time
import threading
import os
import signal
import sys
import hmac
import uuid
import dt_store
import hashlib
import subprocess

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

# New, ADDITIVE topics. The existing motor/telemetry topic is deliberately left
# alone and NOT retained: its payload carries only uptime_ms, no wall clock, and
# the dashboard measures freshness from arrival time -- so a retained copy would
# arrive looking perfectly fresh even if the Pi had been dead for an hour, which
# is precisely the stale-data failure this project exists to eliminate.
# The state document below carries a wall-clock ts, which is what makes
# retaining it safe: a consumer can compute the true age.
TOPIC_BRIDGE_STATUS = 'digital_twin/line01/bridge/status'
TOPIC_STATE = 'digital_twin/line01/state'

BOOT_ID = uuid.uuid4().hex[:12]   # new per bridge start; distinguishes restarts
DEVICE_TIMEOUT_S = 3.0            # no serial telemetry for this long -> offline

def now_ms():
    """UTC epoch milliseconds. The Pi is NTP-synced (verified)."""
    return int(time.time() * 1000)

# -- SQLite logger ------------------------------------------------------------
# Lives outside git (data/ is gitignored). Retention: raw telemetry 14 days,
# health 90 days, events/commands/connections indefinitely -- they are the
# incident record and they are small.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "dt.db")
STORE = dt_store.Store(DB_PATH, BOOT_ID)
STORE.start()   # before brokers connect, or their first connect rows are lost

# -- Cloud publishing budget --------------------------------------------------
# Both cloud plans sit on a ~1M message/month free tier. Measured per broker at
# idle before tuning: encoder 11.5/min + telemetry 10/min + state 10.5/min
# = ~1.4M/month, i.e. over tier by day ~21.
#
# The encoder topic has NO cloud consumer: the deployed dashboard subscribes to
# digital_twin/# but dispatches only motor/telemetry and motor/event, and the
# NodeRED flow in Scripts/ contains no MQTT nodes at all. It was ~497k
# messages/month published to brokers nobody reads -- and the encoder is faulty
# and under investigation, so the data is not trustworthy yet either. It stays
# on the LOCAL broker at full 1 Hz, which is where triage actually happens.
LOCAL_ONLY_TOPICS = {TOPIC_ENCODER}

# Per-topic cloud floor, applied on top of the per-broker min_interval. The
# state document is RETAINED, so a page load is instant regardless of cadence,
# and signature changes plus device_online flips force-publish past the
# throttle. Only the idle heartbeat slows down.
TOPIC_CLOUD_MIN_INTERVAL = {TOPIC_STATE: 15}

# -- Cross-broker command dedupe ----------------------------------------------
# All three subscriptions stay live: the dashboard fails over between brokers
# after 25 s of silence, so a single operator legitimately changes broker
# mid-run. Arbitrating by broker would turn that failover into a command
# blackout. Real arbitration needs a client identity the deployed dashboard
# does not send yet, so this dedupes and audits only -- it does not arbitrate.
#
# Suppression is deliberately narrow: the SAME key from a DIFFERENT broker
# inside the window. Sequential repeats from ONE broker must pass through
# untouched -- the dashboard heartbeat is a repeated identical
# {"mode":"remote"}, and suppressing those would break intent refresh and stop
# the belt.
DEDUPE_WINDOW_S = 1.0
DEDUPE_MAX = 256
_dedupe = {}                  # key -> (first_seen_monotonic, broker)
_dedupe_lock = threading.Lock()

def dedupe_key(obj, raw):
    """Prefer an explicit command id; fall back to a hash of the payload."""
    if isinstance(obj, dict) and obj.get("id"):
        return "id:" + str(obj["id"])
    return "sha:" + hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()

def is_cross_broker_duplicate(key, broker):
    now = time.monotonic()
    with _dedupe_lock:
        if len(_dedupe) > DEDUPE_MAX:
            cutoff = now - DEDUPE_WINDOW_S
            for k in [k for k, (t, _) in _dedupe.items() if t < cutoff]:
                del _dedupe[k]
            if len(_dedupe) > DEDUPE_MAX:      # still full of fresh entries
                _dedupe.clear()
        hit = _dedupe.get(key)
        if hit and hit[1] != broker and (now - hit[0]) < DEDUPE_WINDOW_S:
            return True
        # Record/refresh only for the broker that actually owns this key, so a
        # same-broker repeat simply resets its own timestamp and passes.
        _dedupe[key] = (now, broker)
        return False

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
    'local': {
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
            STORE.log_event("serial_open", detail={"port": SERIAL_PORT})
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
        note_link_up(f"mqtt:{userdata}", BROKERS[userdata]["host"])
        STORE.log_event("broker_up", detail={"broker": userdata})
        # Birth: retained, so a fresh subscriber learns we are up immediately.
        client.publish(TOPIC_BRIDGE_STATUS, json.dumps(
            {"status": "online", "broker": userdata,
             "boot_id": BOOT_ID, "ts": now_ms()}), qos=1, retain=True)
    else:
        print(f"[{userdata}] Connect failed rc={rc}")

_link_down_ms = {}      # link name -> when it went down, for downtime_ms

def note_link_down(link, endpoint, reason):
    _link_down_ms[link] = now_ms()
    STORE.log_connection(link, endpoint, ts_down=now_ms(), reason=reason)

def note_link_up(link, endpoint, reason="connected"):
    down = _link_down_ms.pop(link, None)
    STORE.log_connection(link, endpoint, ts_up=now_ms(), ts_down=down,
                         downtime_ms=(now_ms() - down) if down else None,
                         reason=reason)

def on_disconnect(client, userdata, rc):
    print(f"[{userdata}] Disconnected (rc={rc}); paho will auto-reconnect.")
    note_link_down(f"mqtt:{userdata}", BROKERS[userdata]["host"], f"rc={rc}")
    STORE.log_event("broker_down", severity="warn",
                    detail={"broker": userdata, "rc": rc})

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

# The 1 Hz relay makes the ESP32 echo the same [CMD] line every second (~3600
# lines/hour while running), which would bury real events and, once the
# SQLite logger lands, be ingested wholesale. Collapse consecutive identical
# console lines: print the first, then every Nth with a repeat count.
DUP_LOG_EVERY      = 30
_dbg_counts        = {}   # console line -> times seen (the ESP32 interleaves
                          # [TEMP] with [CMD], so consecutive-dedupe would miss)

# -- Host health sampling (1/min) --------------------------------------------
_tel_hz_recent = 0.0

def _read_cpu_times():
    with open("/proc/stat") as f:
        parts = [float(x) for x in f.readline().split()[1:]]
    idle = parts[3] + parts[4]
    return sum(parts), idle

def health_loop():
    prev_total, prev_idle = _read_cpu_times()
    while not _stop.is_set():
        _stop.wait(60)
        if _stop.is_set():
            break
        try:
            total, idle = _read_cpu_times()
            dt_total, dt_idle = total - prev_total, idle - prev_idle
            cpu = 100.0 * (1 - dt_idle / dt_total) if dt_total > 0 else None
            prev_total, prev_idle = total, idle

            mem = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    mem[k] = float(v.strip().split()[0])
            mem_pct = 100.0 * (1 - mem.get("MemAvailable", 0) / mem.get("MemTotal", 1))

            try:
                with open("/sys/class/thermal/thermal_zone0/temp") as f:
                    cpu_temp = float(f.read().strip()) / 1000.0
            except Exception:
                cpu_temp = None

            st = os.statvfs("/")
            disk_free_mb = st.f_bavail * st.f_frsize / 1048576.0

            rss_mb = None
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_mb = float(line.split()[1]) / 1024.0
                        break

            STORE.log_health(cpu_pct=cpu, mem_pct=mem_pct, cpu_temp_c=cpu_temp,
                             disk_free_mb=disk_free_mb, bridge_rss_mb=rss_mb,
                             telemetry_hz=_tel_hz_recent,
                             relay_active=_relay_thread.is_alive(),
                             intent_armed=_intent_cmd is not None)
        except Exception as e:
            print(f"[HEALTH] sample failed: {e}")

# -- Escalating recovery ------------------------------------------------------
# Every rung stops the belt as a side effect, which is the safe direction: a
# DTR/RTS reset and a USB power cut both leave the firmware booting into MANUAL
# with the motor at zero. Nothing here can start a motor.
#
# The ladder only climbs while the DEVICE is silent, and it is armed only after
# telemetry has actually been seen once -- it will not fire against a device
# that was never present (e.g. a boot with the ESP32 unplugged).
#
# Thresholds are measured against the last serial telemetry, which arrives at
# 1 Hz, so 15 s is 15 missed packets: far outside jitter, and well past the
# firmware 3 s deadman, so by the time rung 1 fires the motor is already
# stopped by the firmware itself.
RECOVERY_SOFT_RESET_S  = 15    # rung 1: DTR/RTS soft reset (no power interruption)
RECOVERY_POWER_CYCLE_S = 30    # rung 2: DISABLED (see below)
RECOVERY_GIVE_UP_S     = 60    # rung 3: alert and STOP retrying

# Rung 2 -- the uhubctl VBUS cycle -- is PERMANENTLY DISABLED by owner
# instruction. It is the only automatic action that could leave the site
# unrecoverable: if the port fails to re-enumerate after the power cut, there is
# no physical access to plug it back in. The ladder therefore runs
# 15 s soft reset -> 60 s give up. The rung is kept in code and logs what it
# WOULD have done, so the escalation stays visible in the incident record and a
# human can still run the cycle manually if they judge it worth the risk:
#     sudo uhubctl -e -l 1 -p 1 -a cycle -d 2
RECOVERY_POWER_CYCLE_ENABLED = False

# -e is mandatory: without it uhubctl USB3-duality handling also hits hub 2.
UHUBCTL_CYCLE = ["sudo", "-n", "/usr/sbin/uhubctl",
                 "-e", "-l", "1", "-p", "1", "-a", "cycle", "-d", "2"]

_recovery_stage = 0            # 0 none, 1 soft reset done, 2 power cycle done,
                               # 3 gave up
_recovery_lock = threading.Lock()

def _drop_intent(reason):
    """Any recovery action is an unexplained stop: require a fresh, deliberate
    start command afterwards rather than letting the relay resume on its own."""
    global _intent_cmd, _intent_deadline
    with _intent_lock:
        had = _intent_cmd is not None
        _intent_cmd = None
        _intent_deadline = 0.0
    if had:
        print(f"[RECOVERY] operator intent dropped ({reason})")
        STORE.log_event("run_intent_dropped", severity="warn",
                        detail={"reason": reason})

def recovery_soft_reset(gap_ms):
    """Pulse DTR/RTS on the port we already hold. No power interruption."""
    print(f"[RECOVERY] rung 1: DTR/RTS soft reset (device silent {gap_ms} ms)")
    STORE.log_event("recovery_soft_reset", severity="warn", detail={"gap_ms": gap_ms})
    publish_all(TOPIC_EVENT, json.dumps(
        {"kind": "recovery_soft_reset", "gap_ms": gap_ms, "ts": now_ms()}), force=True)
    try:
        with _ser_lock:
            if ser is None:
                raise serial.SerialException("port not open")
            ser.setDTR(False)
            ser.setRTS(True)
            time.sleep(0.1)
            ser.setRTS(False)
        return True
    except Exception as e:
        print(f"[RECOVERY] soft reset failed: {e}")
        STORE.log_event("recovery_soft_reset_failed", severity="error",
                        detail={"error": str(e)})
        return False

def recovery_power_cycle(gap_ms):
    """Cut VBUS to the ESP32. Proven to re-enumerate on this Pi in ~2 s, but it
    is the last automatic lever: if the port fails to come back there is no
    physical access to recover it."""
    print(f"[RECOVERY] rung 2: uhubctl VBUS cycle (device silent {gap_ms} ms)")
    STORE.log_event("recovery_power_cycle", severity="warn", detail={"gap_ms": gap_ms})
    publish_all(TOPIC_EVENT, json.dumps(
        {"kind": "recovery_power_cycle", "gap_ms": gap_ms, "ts": now_ms()}), force=True)
    try:
        r = subprocess.run(UHUBCTL_CYCLE, capture_output=True, text=True, timeout=40)
        ok = r.returncode == 0
        if not ok:
            print(f"[RECOVERY] uhubctl failed rc={r.returncode}: {r.stderr.strip()[:200]}")
            STORE.log_event("recovery_power_cycle_failed", severity="error",
                            detail={"rc": r.returncode, "stderr": r.stderr[:400]})
        return ok
    except Exception as e:
        print(f"[RECOVERY] uhubctl error: {e}")
        STORE.log_event("recovery_power_cycle_failed", severity="error",
                        detail={"error": str(e)})
        return False

def recovery_power_cycle_skipped(gap_ms):
    """Rung 2 reached but disabled. Record it loudly: the operator needs to know
    the ladder wanted to power-cycle and was not permitted to."""
    print(f"[RECOVERY] rung 2 REACHED but DISABLED (device silent {gap_ms} ms). "
          f"A human may run: sudo uhubctl -e -l 1 -p 1 -a cycle -d 2")
    STORE.log_event("recovery_power_cycle_skipped", severity="warn",
                    detail={"gap_ms": gap_ms, "reason": "disabled_by_policy"})
    publish_all(TOPIC_EVENT, json.dumps(
        {"kind": "recovery_power_cycle_skipped", "gap_ms": gap_ms, "ts": now_ms(),
         "detail": "automatic USB power cycle is disabled; manual action required"}),
        force=True)

def recovery_give_up(gap_ms):
    """Stop acting. Repeating a failed power cycle forever would hide the fault
    and thrash the port; a human needs to look at it."""
    print(f"[RECOVERY] rung 3: GIVING UP after {gap_ms} ms of silence. "
          f"The soft reset did not restore telemetry and the power cycle is "
          f"disabled by policy. Manual intervention required.")
    STORE.log_event("recovery_gave_up", severity="error", detail={"gap_ms": gap_ms})
    publish_all(TOPIC_EVENT, json.dumps(
        {"kind": "recovery_gave_up", "gap_ms": gap_ms, "ts": now_ms(),
         "detail": "soft reset failed; power cycle disabled by policy"}),
        force=True)

def service_recovery(gap_ms):
    """Climb one rung at most per call. Called from the liveness timer."""
    global _recovery_stage
    with _recovery_lock:
        stage = _recovery_stage
        if stage >= 3:
            return
        if gap_ms >= RECOVERY_GIVE_UP_S * 1000 and stage >= 2:
            _recovery_stage = 3
            action = recovery_give_up
        elif gap_ms >= RECOVERY_POWER_CYCLE_S * 1000 and stage == 1:
            _recovery_stage = 2
            action = (recovery_power_cycle if RECOVERY_POWER_CYCLE_ENABLED
                      else recovery_power_cycle_skipped)
        elif gap_ms >= RECOVERY_SOFT_RESET_S * 1000 and stage == 0:
            _recovery_stage = 1
            action = recovery_soft_reset
        else:
            return
    _drop_intent("recovery action")
    action(gap_ms)

def recovery_reset(stage_when_recovered):
    global _recovery_stage
    with _recovery_lock:
        _recovery_stage = 0
    if stage_when_recovered:
        names = {1: "soft_reset", 2: "power_cycle", 3: "after_give_up"}
        print(f"[RECOVERY] device returned after "
              f"{names.get(stage_when_recovered, stage_when_recovered)}")
        STORE.log_event("recovery_succeeded",
                        detail={"stage": names.get(stage_when_recovered)})
        publish_all(TOPIC_EVENT, json.dumps(
            {"kind": "recovery_succeeded", "ts": now_ms(),
             "stage": names.get(stage_when_recovered)}), force=True)

# -- Device liveness + retained state document -------------------------------
_state_lock     = threading.Lock()
_last_device_ms = 0        # when serial telemetry last arrived
_last_telemetry = None     # the last motor telemetry object, verbatim
_device_online  = False

def build_state_doc():
    with _state_lock:
        last, tel, online = _last_device_ms, _last_telemetry, _device_online
    with _intent_lock:
        armed = _intent_cmd is not None
        expires = int(max(0.0, _intent_deadline - time.time()) * 1000) if armed else None
    n = now_ms()
    return json.dumps({
        "schema": 1,
        "boot_id": BOOT_ID,
        "ts": n,
        "device_online": online,
        "serial_ok": ser is not None,
        "last_device_ts": last or None,
        "device_age_ms": (n - last) if last else None,
        "telemetry": tel,
        "intent": {"armed": armed, "expires_in_ms": expires},
    })

def publish_state(force=False):
    publish_all(TOPIC_STATE, build_state_doc(), force=force, qos=1, retain=True)

def note_device_telemetry(t):
    """Serial telemetry arrived: refresh liveness, and announce recovery."""
    global _last_device_ms, _last_telemetry, _device_online
    recovered = False
    with _state_lock:
        _last_device_ms = now_ms()
        _last_telemetry = t
        if not _device_online:
            _device_online = True
            recovered = True
    if recovered:
        recovery_reset(_recovery_stage)
        print("[LIVENESS] device_online -> TRUE")
        STORE.log_event("device_online", detail={"boot_id": BOOT_ID})
        note_link_up("device", SERIAL_PORT, "telemetry resumed")
        publish_all(TOPIC_EVENT, json.dumps(
            {"kind": "device_online", "ts": now_ms()}), force=True)
        publish_state(force=True)

def liveness_loop():
    """Detect the ABSENCE of packets, so it must be timer-driven -- a handler
    that runs on arrival can never fire when nothing arrives."""
    global _device_online
    while not _stop.is_set():
        _stop.wait(0.5)
        if _stop.is_set():
            break
        with _state_lock:
            last, online = _last_device_ms, _device_online
        if not last:
            continue          # never seen the device: nothing to recover
        gap = now_ms() - last
        if online and gap > DEVICE_TIMEOUT_S * 1000:
            with _state_lock:
                _device_online = False
            print(f"[LIVENESS] device_online -> FALSE (silent {gap} ms)")
            STORE.log_event("device_offline", severity="warn", detail={"gap_ms": gap})
            note_link_down("device", SERIAL_PORT, f"silent {gap} ms")
            publish_all(TOPIC_EVENT, json.dumps(
                {"kind": "device_offline", "gap_ms": gap, "ts": now_ms()}), force=True)
            publish_state(force=True)
        elif not online:
            # Already known offline: climb the recovery ladder. Armed only
            # because `last` is non-zero, i.e. telemetry was seen at least once.
            service_recovery(gap)

# -- State signature -> throttle bypass --------------------------------------
# Cloud plans are throttled to 1 msg/5s per topic to stay inside free tiers.
# That throttle is also what made the twin lag the machine by up to 8s, so a
# real STATE CHANGE must publish immediately. It must be a state change and not
# analogue drift: NB2 current/voltage/power/PF and temperature jitter every
# single packet even with the belt stopped, so comparing raw payloads would
# report "changed" on essentially every message, disable throttling entirely,
# and burn ~5.2M cloud messages/month against a ~1M free tier.
#
# So compare only discrete, decision-relevant state. Analogue drift is excluded
# by construction. Belt stopped -> signature constant -> stays throttled at
# 1 msg/5s. Start, stop, fault, breaker trip, RS485 loss -> immediate publish.
RPM_MOTION_THRESHOLD = 1.0

def state_signature(t):
    """Discrete machine state. Analogue values are deliberately excluded."""
    nb2 = t.get("nb2") or {}
    try:
        rpm = float(t.get("rpm") or 0.0)
    except (TypeError, ValueError):
        rpm = 0.0
    return (
        t.get("dir"),
        t.get("speed_percent"),
        t.get("target_speed_percent"),
        t.get("control_mode"),
        bool(t.get("e18_active")),
        bool(nb2.get("breaker_on")),
        bool(nb2.get("rs485_ok")),
        bool(nb2.get("fault_flags")),
        rpm > RPM_MOTION_THRESHOLD,
    )

_last_signature = None

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
    STORE.log_event("run_intent_latched_off", severity="warn",
                    detail={"reason": "device_left_remote"})
    serial_write(json.dumps({"cmd": "stop"}), "latch-stop")
    serial_write(json.dumps({"mode": "manual"}), "latch-disarm")
    publish_all(TOPIC_EVENT, json.dumps({
        "ts": time.time(), "kind": "run_intent_latched_off",
        "reason": "device_left_remote"}), force=True)

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
            print(f"[RELAY] intent EXPIRED ({reason}) -> stop + disarm")
            STORE.log_event("run_intent_expired", severity="warn",
                            detail={"reason": reason})
            serial_write(json.dumps({"cmd": "stop"}), "relay-stop")
            # Return the device to MANUAL so it is not left armed. The firmware
            # deadman only fires while targetSpeedPercent > 0, so a stopped-but-
            # REMOTE device would otherwise accept a stray start immediately.
            serial_write(json.dumps({"mode": "manual"}), "relay-disarm")
            publish_all(TOPIC_EVENT, json.dumps({
                "ts": now, "kind": "run_intent_expired", "reason": reason}), force=True)
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
                STORE.log_event("breaker_off_rejected", severity="warn",
                                detail={"broker": userdata})
                STORE.log_command(str(obj.get("id") or uuid.uuid4().hex), userdata,
                                  "{\"breaker\":\"off\"}", "rejected",
                                  src=obj.get("src"),
                                  error="bad or missing breaker passphrase")
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

    # Malformed payloads are recorded rather than silently dropped.
    if not isinstance(obj, dict):
        print(f"[MQTT:{userdata}] MALFORMED command dropped: {command_json[:120]}")
        STORE.log_command(uuid.uuid4().hex, userdata, command_json, "malformed",
                          error="payload is not a JSON object")
        return

    cmd_id = str(obj.get("id") or uuid.uuid4().hex)
    src = obj.get("src")
    key = dedupe_key(obj, command_json)

    if is_cross_broker_duplicate(key, userdata):
        print(f"[MQTT:{userdata}] duplicate {cmd_id} suppressed "
              f"(same command already taken from another broker)")
        STORE.log_command(cmd_id, userdata, command_json, "duplicate", src=src)
        return

    # Strip routing metadata before the wire: the firmware parses strictly and
    # rejects payloads over 240 chars, so there is no reason to spend the bytes.
    if "id" in obj or "src" in obj:
        wire = json.dumps({k: v for k, v in obj.items() if k not in ("id", "src")},
                          separators=(",", ":"))
    else:
        wire = command_json          # legacy command: forward byte-for-byte

    print(f"[MQTT:{userdata} -> ESP32] Sending: {wire}")
    note_operator_command(obj, wire)
    ok = serial_write(wire, f"mqtt:{userdata}")

    STORE.log_command(cmd_id, userdata, wire,
                      "forwarded" if ok else "serial_error", src=src,
                      error=None if ok else "serial write failed")

    # Stage (b): received by the bridge and written to the port. This is a
    # fact, unlike inferring success from a later telemetry effect, which
    # cannot distinguish "never reached the device" from "reached it and had
    # no effect".
    #
    # NOT emitted for a bare mode refresh. The dashboard heartbeat is
    # {"mode":"remote"} every 700 ms, i.e. ~1.43 commands/s while running, and
    # acking each one would force-publish ~86 events/min per cloud broker past
    # the throttle -- about 617k messages/month per broker for 4 h/day of
    # running, on a ~1M tier. Heartbeats are not operator actions and the
    # frontend does not track them; real commands still get their ack.
    is_heartbeat = set(obj) <= {"mode", "id", "src"}
    if ok and not is_heartbeat:
        publish_all(TOPIC_EVENT, json.dumps(
            {"kind": "cmd_forwarded", "id": cmd_id, "ts": now_ms(),
             "broker": userdata}), force=True)

# Initialize one MQTT client per enabled broker plan
clients = {}
for name, cfg in BROKERS.items():
    if not cfg['enabled']:
        continue
    client = mqtt.Client(client_id=f"rpi_serial_bridge_{name}", userdata=name)
    # Last will, registered before connect: fires only on UNGRACEFUL loss.
    client.will_set(TOPIC_BRIDGE_STATUS, json.dumps(
        {"status": "offline", "broker": name, "reason": "lwt"}), qos=1, retain=True)
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

def publish_all(topic, payload, force=False, qos=0, retain=False):
    """Publish to every enabled broker. force=True bypasses the per-broker
    throttle -- used only for genuine state changes and events, never for
    routine analogue drift."""
    now = time.time()
    for name, client in clients.items():
        min_interval = BROKERS[name].get('min_interval', 0)
        is_cloud = min_interval > 0          # local plan runs unthrottled
        if is_cloud and topic in LOCAL_ONLY_TOPICS:
            continue                          # no cloud consumer for this topic
        if is_cloud:
            min_interval = max(min_interval, TOPIC_CLOUD_MIN_INTERVAL.get(topic, 0))
        key = (name, topic)
        if not force and min_interval and (now - _last_publish.get(key, 0)) < min_interval:
            continue
        try:
            client.publish(topic, payload, qos=qos, retain=retain)
            _last_publish[key] = now
        except Exception as e:
            print(f"[{name}] Publish failed: {e}")

# Main loop: Read from Serial and publish to all connected brokers
_relay_thread = threading.Thread(target=relay_loop, name="relay", daemon=True)
_relay_thread.start()
threading.Thread(target=liveness_loop, name="liveness", daemon=True).start()
threading.Thread(target=health_loop, name="health", daemon=True).start()
print(f"[RELAY] heartbeat relay active (operator budget {OPERATOR_TIMEOUT_S}s, relay every {RELAY_INTERVAL_S}s, run cap {MAX_RUN_S}s)")

print(f"Listening for telemetry... Active plans: {list(clients.keys())}. Press Ctrl+C to exit.")
# Journal thinning. Every telemetry row now lands in SQLite, so the journal
# does not need to carry a line per packet (previously ~180 lines/min, which
# buried real events). Summarise instead.
SUMMARY_INTERVAL_S = 30
_tel_count = 0
_enc_count = 0
_last_summary = time.time()

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
                STORE.log_event("serial_error", severity="warn",
                                detail={"n": _read_errors, "error": str(e)})
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

                    # Publish immediately when discrete state changes;
                    # otherwise let the per-broker throttle apply.
                    _t = json.loads(json_str)
                    _sig = state_signature(_t)
                    _changed = _sig != _last_signature
                    if _changed and _last_signature is not None:
                        print(f"[STATE] {_last_signature} -> {_sig} (immediate publish)")
                    _last_signature = _sig
                    publish_all(TOPIC_TELEMETRY, json_str, force=_changed)

                    note_device_telemetry(_t)
                    publish_state(force=_changed)

                    # Latch intent off if the device left REMOTE.
                    try:
                        note_device_state(json.loads(json_str))
                    except Exception:
                        pass

                    STORE.log_telemetry(_t, _device_online)
                    _tel_count += 1
                except json.JSONDecodeError:
                    print(f"Invalid JSON from Serial: {line}")

            # Encoder telemetry marker
            elif "[ENC MQTT] {" in line:
                try:
                    json_str = line[line.find('{'):]
                    publish_all(TOPIC_ENCODER, json_str)
                    _enc_count += 1
                except json.JSONDecodeError:
                    pass

            # Print debug lines so you can still see ESP32 console output
            elif line:
                n = _dbg_counts.get(line, 0) + 1
                _dbg_counts[line] = n
                if n == 1:
                    print(f"[ESP32] {line}")
                elif n % DUP_LOG_EVERY == 0:
                    print(f"[ESP32] {line}  (seen {n}x)")
                if len(_dbg_counts) > 200:      # bound the table
                    _dbg_counts.clear()
        if time.time() - _last_summary >= SUMMARY_INTERVAL_S:
            _win = time.time() - _last_summary
            print(f"[SUMMARY] {_win:.0f}s: telemetry {_tel_count} "
                  f"({_tel_count/_win:.2f} Hz), encoder {_enc_count} "
                  f"({_enc_count/_win:.2f} Hz), device_online={_device_online}, "
                  f"intent_armed={_intent_cmd is not None}")
            _tel_hz_recent = _tel_count / _win
            _tel_count = 0
            _enc_count = 0
            _last_summary = time.time()
        _stop.wait(0.001)

except KeyboardInterrupt:
    print("\nExiting...")
finally:
    try:
        if ser is not None:
            ser.close()
    except Exception:
        pass
    try:
        STORE.log_event("bridge_stopping", detail={"boot_id": BOOT_ID})
        STORE.stop()
    except Exception:
        pass
    for _name, client in clients.items():
        try:
            client.publish(TOPIC_BRIDGE_STATUS, json.dumps(
                {"status": "offline", "broker": _name,
                 "reason": "sigterm", "ts": now_ms()}), qos=1, retain=True)
        except Exception:
            pass
    time.sleep(0.5)          # let the retained offline message reach the broker
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
