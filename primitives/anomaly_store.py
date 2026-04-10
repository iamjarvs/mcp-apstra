"""
anomaly_store.py

Rolling 7-day anomaly time-series store backed by SQLite.

Holds the complete raise/clear event log for every anomaly identity seen
across all monitored blueprints.  The AnomalyPoller writes to this store;
MCP tools read from it.

Thread safety: all writes are guarded by a threading.Lock so that the
asyncio event loop and any concurrent handler calls can share one instance
safely.

Schema
------
anomalies   — one row per unique anomaly identity (blueprint + identity JSON)
events      — one row per raise or clear event, linked to anomalies
poll_state  — one row per blueprint, storing last snapshot and counts for
              incremental polling
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

WINDOW_DAYS = 7

_DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "anomaly_timeseries.db"


class AnomalyStore:
    def __init__(self, db_path: Path | None = None):
        self._path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._con = sqlite3.connect(str(self._path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        log.info("AnomalyStore opened: %s", self._path)

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_schema(self):
        self._con.executescript("""
            CREATE TABLE IF NOT EXISTS anomalies (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                blueprint_id    TEXT    NOT NULL,
                instance_name   TEXT    NOT NULL,
                anomaly_type    TEXT    NOT NULL,
                device_hostname TEXT,
                role            TEXT,
                identity_json   TEXT    NOT NULL,
                expected_json   TEXT,
                first_detected  TEXT,
                UNIQUE(blueprint_id, instance_name, identity_json)
            );

            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                anomaly_id  INTEGER NOT NULL REFERENCES anomalies(id),
                timestamp   TEXT    NOT NULL,
                raised      INTEGER NOT NULL,
                actual_json TEXT,
                source      TEXT
            );

            CREATE TABLE IF NOT EXISTS poll_state (
                blueprint_id        TEXT NOT NULL,
                instance_name       TEXT NOT NULL,
                last_counts_json    TEXT,
                last_snapshot_json  TEXT,
                last_poll_at        TEXT,
                backfill_complete   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(blueprint_id, instance_name)
            );

            CREATE INDEX IF NOT EXISTS idx_events_ts     ON events(timestamp);
            CREATE INDEX IF NOT EXISTS idx_events_anom   ON events(anomaly_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_anom_bp       ON anomalies(blueprint_id, instance_name);
            CREATE INDEX IF NOT EXISTS idx_anom_type     ON anomalies(anomaly_type);
        """)
        self._con.commit()

    # ── Write helpers ─────────────────────────────────────────────────────────

    def upsert_anomaly(
        self,
        blueprint_id: str,
        instance_name: str,
        a: dict,
    ) -> int:
        """
        Insert an anomaly identity if it doesn't exist; return its row id.
        `a` is a raw anomaly dict from the Apstra history API.
        """
        id_json = json.dumps(a["identity"], sort_keys=True)
        with self._lock:
            row = self._con.execute(
                "SELECT id FROM anomalies "
                "WHERE blueprint_id=? AND instance_name=? AND identity_json=?",
                (blueprint_id, instance_name, id_json),
            ).fetchone()
            if row:
                return row["id"]
            self._con.execute(
                """INSERT INTO anomalies
                   (blueprint_id, instance_name, anomaly_type, device_hostname,
                    role, identity_json, expected_json, first_detected)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    blueprint_id,
                    instance_name,
                    a.get("anomaly_type"),
                    a.get("device_hostname"),
                    a.get("role"),
                    id_json,
                    json.dumps(a.get("expected")),
                    a.get("detected_at"),
                ),
            )
            self._con.commit()
            return self._con.execute(
                "SELECT id FROM anomalies "
                "WHERE blueprint_id=? AND instance_name=? AND identity_json=?",
                (blueprint_id, instance_name, id_json),
            ).fetchone()["id"]

    def insert_event(
        self,
        anomaly_id: int,
        timestamp: str,
        raised: bool,
        actual: dict | None,
        source: str,
    ) -> bool:
        """
        Insert a raise or clear event.  Silently deduplicates: if an event
        with the same (anomaly_id, timestamp, raised) already exists nothing
        is written and False is returned.
        """
        with self._lock:
            exists = self._con.execute(
                "SELECT 1 FROM events "
                "WHERE anomaly_id=? AND timestamp=? AND raised=?",
                (anomaly_id, timestamp, int(raised)),
            ).fetchone()
            if exists:
                return False
            self._con.execute(
                "INSERT INTO events(anomaly_id, timestamp, raised, actual_json, source) "
                "VALUES (?,?,?,?,?)",
                (anomaly_id, timestamp, int(raised), json.dumps(actual), source),
            )
            self._con.commit()
            return True

    def prune(self):
        """Delete events and orphaned anomalies outside the rolling window."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
        ).isoformat()
        with self._lock:
            self._con.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
            self._con.execute(
                "DELETE FROM anomalies "
                "WHERE id NOT IN (SELECT DISTINCT anomaly_id FROM events)"
            )
            self._con.commit()
        log.debug("AnomalyStore pruned events older than %s", cutoff[:19])

    # ── Poll state ────────────────────────────────────────────────────────────

    def get_poll_state(self, blueprint_id: str, instance_name: str) -> dict:
        row = self._con.execute(
            "SELECT last_counts_json, last_snapshot_json, last_poll_at, backfill_complete "
            "FROM poll_state WHERE blueprint_id=? AND instance_name=?",
            (blueprint_id, instance_name),
        ).fetchone()
        if not row:
            return {"last_counts": {}, "last_snapshot": {}, "last_poll_at": None, "backfill_complete": False}
        return {
            "last_counts":        json.loads(row["last_counts_json"])   if row["last_counts_json"]   else {},
            "last_snapshot":      json.loads(row["last_snapshot_json"]) if row["last_snapshot_json"] else {},
            "last_poll_at":       row["last_poll_at"],
            "backfill_complete":  bool(row["backfill_complete"]),
        }

    def set_poll_state(
        self,
        blueprint_id: str,
        instance_name: str,
        last_counts: dict,
        last_snapshot: dict,
        backfill_complete: bool | None = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            existing = self._con.execute(
                "SELECT backfill_complete FROM poll_state "
                "WHERE blueprint_id=? AND instance_name=?",
                (blueprint_id, instance_name),
            ).fetchone()
            bf = int(backfill_complete) if backfill_complete is not None else (
                existing["backfill_complete"] if existing else 0
            )
            self._con.execute(
                """INSERT INTO poll_state
                   (blueprint_id, instance_name, last_counts_json,
                    last_snapshot_json, last_poll_at, backfill_complete)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(blueprint_id, instance_name) DO UPDATE SET
                       last_counts_json    = excluded.last_counts_json,
                       last_snapshot_json  = excluded.last_snapshot_json,
                       last_poll_at        = excluded.last_poll_at,
                       backfill_complete   = excluded.backfill_complete""",
                (blueprint_id, instance_name,
                 json.dumps(last_counts), json.dumps(last_snapshot), now, bf),
            )
            self._con.commit()

    # ── Query operations ──────────────────────────────────────────────────────

    def query_events(
        self,
        blueprint_id: str,
        instance_name: str | None = None,
        anomaly_type: str | None = None,
        device: str | None = None,
        since: str | None = None,
        until: str | None = None,
        raised_only: bool = False,
        limit: int = 500,
    ) -> list[dict]:
        """
        Return events matching the given filters, newest first.
        """
        where = ["a.blueprint_id = ?"]
        params: list = [blueprint_id]

        if instance_name:
            where.append("a.instance_name = ?")
            params.append(instance_name)
        if anomaly_type:
            where.append("a.anomaly_type = ?")
            params.append(anomaly_type)
        if device:
            where.append("a.device_hostname = ?")
            params.append(device)
        if since:
            where.append("e.timestamp >= ?")
            params.append(since)
        if until:
            where.append("e.timestamp <= ?")
            params.append(until)
        if raised_only:
            where.append("e.raised = 1")

        params.append(limit)
        rows = self._con.execute(
            f"""SELECT
                    e.timestamp, e.raised, e.actual_json, e.source,
                    a.anomaly_type, a.device_hostname, a.role,
                    a.identity_json, a.expected_json, a.first_detected,
                    a.instance_name, a.blueprint_id
                FROM events e
                JOIN anomalies a ON a.id = e.anomaly_id
                WHERE {' AND '.join(where)}
                ORDER BY e.timestamp DESC
                LIMIT ?""",
            params,
        ).fetchall()

        results = []
        for r in rows:
            results.append({
                "timestamp":      r["timestamp"],
                "raised":         bool(r["raised"]),
                "actual":         json.loads(r["actual_json"]) if r["actual_json"] else None,
                "source":         r["source"],
                "anomaly_type":   r["anomaly_type"],
                "device":         r["device_hostname"],
                "role":           r["role"],
                "identity":       json.loads(r["identity_json"]),
                "expected":       json.loads(r["expected_json"]) if r["expected_json"] else None,
                "first_detected": r["first_detected"],
                "instance":       r["instance_name"],
            })
        return results

    def get_currently_active(
        self,
        blueprint_id: str,
        instance_name: str | None = None,
    ) -> list[dict]:
        """
        Return anomalies whose most recent event is a raise (not cleared).
        """
        where = ["a.blueprint_id = ?"]
        params: list = [blueprint_id]
        if instance_name:
            where.append("a.instance_name = ?")
            params.append(instance_name)

        rows = self._con.execute(
            f"""SELECT
                    a.anomaly_type, a.device_hostname, a.role,
                    a.identity_json, a.expected_json, a.first_detected,
                    a.instance_name,
                    (SELECT actual_json  FROM events WHERE anomaly_id=a.id
                     ORDER BY timestamp DESC, id DESC LIMIT 1) AS last_actual,
                    (SELECT timestamp    FROM events WHERE anomaly_id=a.id
                     ORDER BY timestamp DESC, id DESC LIMIT 1) AS last_event_ts,
                    (SELECT raised       FROM events WHERE anomaly_id=a.id
                     ORDER BY timestamp DESC, id DESC LIMIT 1) AS last_raised
                FROM anomalies a
                WHERE {' AND '.join(where)}
                AND (
                    SELECT raised FROM events WHERE anomaly_id=a.id
                    ORDER BY timestamp DESC, id DESC LIMIT 1
                ) = 1
                ORDER BY a.anomaly_type, a.device_hostname""",
            params,
        ).fetchall()

        return [
            {
                "anomaly_type":   r["anomaly_type"],
                "device":         r["device_hostname"],
                "role":           r["role"],
                "identity":       json.loads(r["identity_json"]),
                "expected":       json.loads(r["expected_json"]) if r["expected_json"] else None,
                "actual":         json.loads(r["last_actual"])   if r["last_actual"]   else None,
                "first_detected": r["first_detected"],
                "last_event_at":  r["last_event_ts"],
                "instance":       r["instance_name"],
            }
            for r in rows
        ]

    def get_summary(self, blueprint_id: str) -> dict:
        total = self._con.execute(
            "SELECT COUNT(*) FROM anomalies WHERE blueprint_id=?",
            (blueprint_id,),
        ).fetchone()[0]

        active = len(self.get_currently_active(blueprint_id))

        oldest = self._con.execute(
            "SELECT MIN(e.timestamp) FROM events e "
            "JOIN anomalies a ON a.id=e.anomaly_id WHERE a.blueprint_id=?",
            (blueprint_id,),
        ).fetchone()[0]

        newest = self._con.execute(
            "SELECT MAX(e.timestamp) FROM events e "
            "JOIN anomalies a ON a.id=e.anomaly_id WHERE a.blueprint_id=?",
            (blueprint_id,),
        ).fetchone()[0]

        total_events = self._con.execute(
            "SELECT COUNT(*) FROM events e "
            "JOIN anomalies a ON a.id=e.anomaly_id WHERE a.blueprint_id=?",
            (blueprint_id,),
        ).fetchone()[0]

        by_type = self._con.execute(
            """SELECT a.anomaly_type,
                      COUNT(DISTINCT a.id)                         AS identities,
                      SUM(CASE WHEN e.raised=1 THEN 1 ELSE 0 END) AS raises,
                      SUM(CASE WHEN e.raised=0 THEN 1 ELSE 0 END) AS clears
               FROM anomalies a
               JOIN events e ON e.anomaly_id = a.id
               WHERE a.blueprint_id = ?
               GROUP BY a.anomaly_type
               ORDER BY raises DESC""",
            (blueprint_id,),
        ).fetchall()

        return {
            "total_identities": total,
            "currently_active": active,
            "total_events":     total_events,
            "oldest_event":     oldest,
            "newest_event":     newest,
            "by_type": [
                {
                    "anomaly_type": r["anomaly_type"],
                    "identities":   r["identities"],
                    "raises":       r["raises"],
                    "clears":       r["clears"],
                }
                for r in by_type
            ],
        }

    def is_ready(self, blueprint_id: str, instance_name: str) -> bool:
        """True once the initial backfill for this blueprint has completed."""
        state = self.get_poll_state(blueprint_id, instance_name)
        return state.get("backfill_complete", False)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self):
        self._con.close()
