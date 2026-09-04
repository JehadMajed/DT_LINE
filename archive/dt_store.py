"""SQLite logger for the digital-twin bridge.

Design notes:
 - All timestamps are UTC epoch MILLISECONDS as INTEGER. Never local strings:
   they sort wrong, they lie across DST, and they cannot be differenced.
 - Writes are queued and committed on a 1 s timer. A commit per row at 1 Hz
   would fsync the SD card 86,400 times a day.
 - WAL + synchronous=NORMAL: readers never block the writer, and we accept
   losing the last <1 s of rows on power loss rather than paying fsync per txn.
 - Logging REFUSES to start until the clock is NTP-synced. A Pi that boots
   without network has a 1970 clock, and those rows would poison every query
   that follows.
"""
import json
import os
import sqlite3
import subprocess
import threading
import time

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS telemetry (
         ts INTEGER NOT NULL, boot_id TEXT, device_ts_ms INTEGER,
         rpm REAL, pwm INTEGER, target_pwm INTEGER, dir TEXT, mode TEXT,
         prox INTEGER, temp_c REAL,
         voltage REAL, current REAL, active_power REAL, pf REAL, freq REAL,
         energy_wh REAL, breaker_on INTEGER, rs485_ok INTEGER,
         fault_flags INTEGER, alarm_flags INTEGER, device_online INTEGER)""",
    "CREATE INDEX IF NOT EXISTS ix_telemetry_ts ON telemetry(ts)",
    """CREATE TABLE IF NOT EXISTS events (
         id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
         severity TEXT, kind TEXT, detail_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts)",
    """CREATE TABLE IF NOT EXISTS commands (
         id INTEGER PRIMARY KEY AUTOINCREMENT, cmd_id TEXT,
         ts_received INTEGER NOT NULL,
         src TEXT, source_broker TEXT, payload TEXT,
         outcome TEXT, forwarded_ok INTEGER, error TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_commands_ts ON commands(ts_received)",
    "CREATE INDEX IF NOT EXISTS ix_commands_cmdid ON commands(cmd_id)",
    """CREATE TABLE IF NOT EXISTS connections (
         id INTEGER PRIMARY KEY AUTOINCREMENT, ts_up INTEGER, ts_down INTEGER,
         link TEXT, endpoint TEXT, downtime_ms INTEGER, reason TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_connections_ts ON connections(ts_down, ts_up)",
    """CREATE TABLE IF NOT EXISTS health (
         ts INTEGER NOT NULL, cpu_pct REAL, mem_pct REAL, cpu_temp_c REAL,
         disk_free_mb REAL, bridge_rss_mb REAL, telemetry_hz REAL,
         relay_active INTEGER, intent_armed INTEGER)""",
    "CREATE INDEX IF NOT EXISTS ix_health_ts ON health(ts)",
]

# events / commands / connections are kept indefinitely: they are small, and
# they are the incident record.
RETENTION_DAYS = {"telemetry": 14, "health": 90}
SANE_EPOCH_MS = 1735689600000       # 2025-01-01; below this the clock is lying


def clock_is_synced():
    """True only if systemd reports NTP sync AND the clock is plausible."""
    if time.time() * 1000 < SANE_EPOCH_MS:
        return False
    try:
        out = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        return out == "yes"
    except Exception:
        return False


class Store:
    """Queue-and-batch SQLite writer. Every log_* call is non-blocking."""

    def __init__(self, path, boot_id):
        self.path = path
        self.boot_id = boot_id
        self._q = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._enabled = False
        self._warned = False
        self._last_prune = 0.0
        self._db = None
        self._thread = threading.Thread(target=self._writer, name="sqlite", daemon=True)

    def start(self):
        # Try to open synchronously first. The local broker connects in well
        # under the time the writer thread needs for its first clock check, and
        # its broker_up row would otherwise be dropped before logging begins.
        # If the clock is not yet synced we fall back to the retry loop, which
        # is the correct behaviour -- rows with a 1970 timestamp poison the
        # series and must never be written.
        if clock_is_synced():
            try:
                self._db = self._open()
                self._enabled = True
                print("[STORE] logging to %s (clock NTP-synced)" % self.path)
                self.log_event("store_started", detail={"path": self.path})
            except Exception as e:
                print("[STORE] open failed (%s); retrying in background" % e)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)

    def _put(self, sql, params):
        if not self._enabled:
            return
        with self._lock:
            self._q.append((sql, params))

    def log_telemetry(self, t, device_online, ts=None):
        nb2 = t.get("nb2") or {}
        self._put(
            """INSERT INTO telemetry (ts, boot_id, device_ts_ms, rpm, pwm, target_pwm,
                 dir, mode, prox, temp_c, voltage, current, active_power, pf, freq,
                 energy_wh, breaker_on, rs485_ok, fault_flags, alarm_flags, device_online)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts or int(time.time() * 1000), self.boot_id, t.get("uptime_ms"),
             t.get("rpm"), t.get("speed_percent"), t.get("target_speed_percent"),
             t.get("dir"), t.get("control_mode"), int(bool(t.get("e18_active"))),
             t.get("temp_c"), nb2.get("voltage"), nb2.get("current"),
             nb2.get("active_power"), nb2.get("power_factor"), nb2.get("frequency"),
             nb2.get("energy_wh"), int(bool(nb2.get("breaker_on"))),
             int(bool(nb2.get("rs485_ok"))), nb2.get("fault_flags"),
             nb2.get("alarm_flags"), int(bool(device_online))))

    def log_event(self, kind, severity="info", detail=None, ts=None):
        self._put(
            "INSERT INTO events (ts, severity, kind, detail_json) VALUES (?,?,?,?)",
            (ts or int(time.time() * 1000), severity, kind,
             json.dumps(detail) if detail is not None else None))

    def log_command(self, cmd_id, source_broker, payload, outcome,
                    src=None, error=None, ts=None):
        """outcome: forwarded | duplicate | serial_error | malformed | rejected.
        Everything is recorded, including commands we refused to act on -- a
        command that fails to parse is exactly the thing there was previously
        no record of."""
        self._put(
            """INSERT INTO commands
                 (cmd_id, ts_received, src, source_broker, payload,
                  outcome, forwarded_ok, error)
               VALUES (?,?,?,?,?,?,?,?)""",
            (cmd_id, ts or int(time.time() * 1000), src, source_broker, payload,
             outcome, int(outcome == "forwarded"), error))

    def log_connection(self, link, endpoint, ts_down=None, ts_up=None,
                       downtime_ms=None, reason=None):
        self._put(
            """INSERT INTO connections
                 (ts_up, ts_down, link, endpoint, downtime_ms, reason)
               VALUES (?,?,?,?,?,?)""",
            (ts_up, ts_down, link, endpoint, downtime_ms, reason))

    def log_health(self, **kw):
        self._put(
            """INSERT INTO health (ts, cpu_pct, mem_pct, cpu_temp_c, disk_free_mb,
                 bridge_rss_mb, telemetry_hz, relay_active, intent_armed)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (int(time.time() * 1000), kw.get("cpu_pct"), kw.get("mem_pct"),
             kw.get("cpu_temp_c"), kw.get("disk_free_mb"), kw.get("bridge_rss_mb"),
             kw.get("telemetry_hz"), int(bool(kw.get("relay_active"))),
             int(bool(kw.get("intent_armed")))))

    def _open(self):
        first = not os.path.exists(self.path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # check_same_thread=False: the connection is opened on the main thread
        # (schema creation, before the writer starts) but every subsequent
        # statement runs on the writer thread and nowhere else, so access is
        # still strictly serialised.
        db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        if first:
            # auto_vacuum must be set before any table exists.
            db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        for stmt in SCHEMA:
            db.execute(stmt)
        # Additive migration for databases created before src/outcome existed.
        for col, decl in (("src", "TEXT"), ("outcome", "TEXT")):
            try:
                db.execute("ALTER TABLE commands ADD COLUMN %s %s" % (col, decl))
            except sqlite3.OperationalError:
                pass          # already present
        # Rebuild migration: older databases had cmd_id as the PRIMARY KEY,
        # which made a duplicate delivery overwrite the forwarded one.
        cols = [r[1] for r in db.execute("PRAGMA table_info(commands)")]
        if "id" not in cols:
            db.execute("ALTER TABLE commands RENAME TO commands_old")
            for stmt in SCHEMA:
                if "commands" in stmt:
                    db.execute(stmt)
            db.execute("""INSERT INTO commands
                            (cmd_id, ts_received, src, source_broker, payload,
                             outcome, forwarded_ok, error)
                          SELECT cmd_id, ts_received, src, source_broker, payload,
                                 outcome, forwarded_ok, error FROM commands_old""")
            db.execute("DROP TABLE commands_old")
            print("[STORE] migrated commands table to a surrogate primary key")
        db.commit()
        return db

    def _writer(self):
        while not self._stop.is_set():
            if not self._enabled:
                if clock_is_synced():
                    self._db = self._open()
                    self._enabled = True
                    print(f"[STORE] logging to {self.path} (clock NTP-synced)")
                    self.log_event("store_started", detail={"path": self.path})
                elif not self._warned:
                    self._warned = True
                    print("[STORE] *** NOT LOGGING: system clock is not NTP-synced.")
                    print("[STORE] *** Rows would carry a false timestamp and poison "
                          "the series. Retrying every 30s.")
                if not self._enabled:
                    self._stop.wait(30)
                continue

            self._stop.wait(1.0)
            with self._lock:
                batch, self._q = self._q, []
            if batch:
                try:
                    for sql, params in batch:
                        self._db.execute(sql, params)
                    self._db.commit()
                except Exception as e:
                    print(f"[STORE] write failed ({e}); dropped {len(batch)} rows")
            self._maybe_prune()

        if self._enabled:
            try:
                with self._lock:
                    batch, self._q = self._q, []
                for sql, params in batch:
                    self._db.execute(sql, params)
                self._db.commit()
                self._db.close()
            except Exception:
                pass

    def _maybe_prune(self):
        now = time.time()
        if now - self._last_prune < 86400:
            return
        self._last_prune = now
        try:
            for table, days in RETENTION_DAYS.items():
                cutoff = int((now - days * 86400) * 1000)
                cur = self._db.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))
                if cur.rowcount:
                    print(f"[STORE] pruned {cur.rowcount} rows from {table} (>{days}d)")
            self._db.commit()
            # Incremental only: a full VACUUM would lock the database for the
            # entire rebuild while the service is running.
            self._db.execute("PRAGMA incremental_vacuum")
            self._db.commit()
        except Exception as e:
            print(f"[STORE] prune failed: {e}")
