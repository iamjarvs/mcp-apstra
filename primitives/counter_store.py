"""
primitives/counter_store.py

Rolling 7-day interface counter time-series store backed by SQLite.

The CounterPoller writes raw cumulative counter snapshots here every
COUNTER_POLL_INTERVAL seconds (default 5 min).  Because the Apstra
counter API returns *cumulative* values since the last device reset, the
store itself stores raw snapshots — delta computation between consecutive
snapshots is done at query time.

This enables trend analysis questions like:
  - "Is ge-0/0/1 accumulating CRC errors faster over time?"
  - "Which interfaces have had the most new FCS errors in the past hour?"
  - "Did the error rate on this interface change after the maintenance window?"

Schema
------
interfaces        — one row per unique (instance_name, system_id, interface_name)
counter_snapshots — one row per poll per interface; all cumulative counter values

Delta logic
-----------
To convert cumulative snapshots to per-interval deltas:
  delta = curr_value - prev_value

A negative delta means the counter was reset (device reboot or counter wrap).
Negative deltas are treated as 0 (not meaningful for error trending).
The delta row is flagged has_reset=True so callers can note the event.

Thread safety: all writes are guarded by a threading.Lock.
"""

import logging
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

WINDOW_DAYS = 7

# Counter fields stored for every snapshot (subset of the API response)
ERROR_FIELDS = [
    "fcs_errors",
    "alignment_errors",
    "symbol_errors",
    "rx_error_packets",
    "tx_error_packets",
    "runts",
    "giants",
]
TRAFFIC_FIELDS = [
    "rx_bytes",
    "tx_bytes",
    "rx_discard_packets",
    "tx_discard_packets",
    "rx_unicast_packets",
    "tx_unicast_packets",
]
ALL_COUNTER_FIELDS = ERROR_FIELDS + TRAFFIC_FIELDS

_DEFAULT_DB_PATH = (
    Path(__file__).parent.parent / "data" / "counter_timeseries.db"
)


class CounterStore:
    """
    SQLite-backed store for interface counter time-series data.

    Typical usage:
        store = CounterStore()
        iface_id = store.upsert_interface("dc-primary", "5254002D005F", "ge-0/0/0")
        store.insert_snapshot(iface_id, "2026-01-01T00:00:00Z", {"fcs_errors": 12, ...})
        trend = store.get_error_trend("dc-primary", "5254002D005F", "ge-0/0/0", hours_back=24)
    """

    def __init__(self, db_path: Path | None = None):
        self._path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._con = sqlite3.connect(str(self._path), check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA foreign_keys=ON")
        self._con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._init_schema()
        log.info("CounterStore opened: %s", self._path)

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_schema(self):
        cols = "\n".join(f"    {f:<22} INTEGER DEFAULT 0," for f in ALL_COUNTER_FIELDS)
        self._con.executescript(f"""
            CREATE TABLE IF NOT EXISTS interfaces (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_name   TEXT    NOT NULL,
                system_id       TEXT    NOT NULL,
                interface_name  TEXT    NOT NULL,
                UNIQUE(instance_name, system_id, interface_name)
            );

            CREATE TABLE IF NOT EXISTS counter_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                interface_id    INTEGER NOT NULL REFERENCES interfaces(id),
                polled_at       TEXT    NOT NULL,
                {cols}
                UNIQUE(interface_id, polled_at)
            );

            CREATE INDEX IF NOT EXISTS idx_cs_iface_ts  ON counter_snapshots(interface_id, polled_at);
            CREATE INDEX IF NOT EXISTS idx_iface_inst   ON interfaces(instance_name);
            CREATE INDEX IF NOT EXISTS idx_iface_sys    ON interfaces(instance_name, system_id);
        """)
        self._con.commit()

    # ── Write helpers ─────────────────────────────────────────────────────────

    def upsert_interface(
        self,
        instance_name: str,
        system_id: str,
        interface_name: str,
    ) -> int:
        """
        Insert the interface identity if it doesn't exist; return its row id.
        """
        with self._lock:
            row = self._con.execute(
                "SELECT id FROM interfaces "
                "WHERE instance_name=? AND system_id=? AND interface_name=?",
                (instance_name, system_id, interface_name),
            ).fetchone()
            if row:
                return row["id"]
            self._con.execute(
                "INSERT INTO interfaces(instance_name, system_id, interface_name) "
                "VALUES (?,?,?)",
                (instance_name, system_id, interface_name),
            )
            self._con.commit()
            return self._con.execute(
                "SELECT id FROM interfaces "
                "WHERE instance_name=? AND system_id=? AND interface_name=?",
                (instance_name, system_id, interface_name),
            ).fetchone()["id"]

    def insert_snapshot(
        self,
        interface_id: int,
        polled_at: str,
        counters: dict,
    ) -> bool:
        """
        Insert a single counter snapshot.  Silently deduplicates: returns
        False (and writes nothing) if a snapshot at this timestamp already
        exists for this interface.

        `counters` should be the raw API item dict — any fields not in
        ALL_COUNTER_FIELDS are ignored.
        """
        with self._lock:
            exists = self._con.execute(
                "SELECT 1 FROM counter_snapshots "
                "WHERE interface_id=? AND polled_at=?",
                (interface_id, polled_at),
            ).fetchone()
            if exists:
                return False

            values = [counters.get(f, 0) or 0 for f in ALL_COUNTER_FIELDS]
            placeholders = ", ".join("?" * len(ALL_COUNTER_FIELDS))
            col_names = ", ".join(ALL_COUNTER_FIELDS)
            self._con.execute(
                f"INSERT INTO counter_snapshots"
                f"(interface_id, polled_at, {col_names}) "
                f"VALUES (?, ?, {placeholders})",
                [interface_id, polled_at, *values],
            )
            self._con.commit()
            return True

    def prune(self, window_days: int = WINDOW_DAYS):
        """Delete snapshots older than window_days."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=window_days)
        ).isoformat()
        with self._lock:
            self._con.execute(
                "DELETE FROM counter_snapshots WHERE polled_at < ?",
                (cutoff,),
            )
            # Clean up interfaces that have no snapshots remaining
            self._con.execute(
                "DELETE FROM interfaces "
                "WHERE id NOT IN (SELECT DISTINCT interface_id FROM counter_snapshots)"
            )
            self._con.commit()
        log.debug("CounterStore pruned snapshots older than %s", cutoff[:19])

    def close(self):
        self._con.close()

    # ── Query helpers ─────────────────────────────────────────────────────────

    def _get_snapshots_raw(
        self,
        instance_name: str,
        system_id: str,
        interface_name: str,
        since: str,
        until: str,
    ) -> list[dict]:
        """Return raw snapshot rows for a single interface, oldest first."""
        rows = self._con.execute(
            """SELECT s.*
               FROM counter_snapshots s
               JOIN interfaces i ON i.id = s.interface_id
               WHERE i.instance_name = ?
                 AND i.system_id     = ?
                 AND i.interface_name = ?
                 AND s.polled_at >= ?
                 AND s.polled_at <= ?
               ORDER BY s.polled_at ASC""",
            (instance_name, system_id, interface_name, since, until),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Analytics queries ─────────────────────────────────────────────────────

    def get_error_trend(
        self,
        instance_name: str,
        system_id: str,
        interface_name: str,
        hours_back: int = 24,
        until: str | None = None,
    ) -> list[dict]:
        """
        Return per-interval error (and traffic) deltas for a single interface
        over the requested time window, oldest → newest.

        Each row represents the change in counter values between consecutive
        poll snapshots:

          polled_at         — timestamp of the later snapshot
          interval_seconds  — seconds between the two snapshots
          fcs_errors        — number of new FCS errors in this interval
          alignment_errors
          symbol_errors
          rx_error_packets
          tx_error_packets
          runts / giants
          rx_bytes / tx_bytes  — traffic in this interval (context for errors)
          rx_discard_packets / tx_discard_packets
          total_errors      — sum of all error deltas for quick sorting
          has_reset         — True if any counter decreased (counter reset /
                              device reboot); error deltas are set to 0 in
                              that row but traffic deltas are still included

        Returns an empty list if fewer than 2 snapshots exist in the window
        (cannot compute a delta from a single point).
        """
        until_iso = until or datetime.now(timezone.utc).isoformat()
        since_iso = (
            datetime.now(timezone.utc) - timedelta(hours=hours_back)
        ).isoformat()

        snapshots = self._get_snapshots_raw(
            instance_name, system_id, interface_name, since_iso, until_iso
        )
        return _compute_deltas(snapshots)

    def get_top_error_growers(
        self,
        instance_name: str,
        system_ids: list[str] | None = None,
        hours_back: int = 24,
        until: str | None = None,
        top_n: int = 20,
    ) -> list[dict]:
        """
        Return the top N interfaces ranked by total error counter growth over
        the requested window.

        Queries all interfaces matching `instance_name` (and optionally
        `system_ids`), computes per-interval deltas for each, then aggregates
        into a per-interface summary:

          system_id         — hardware chassis serial
          interface_name
          snapshot_count    — number of polls in the window
          total_fcs_errors
          total_alignment_errors
          total_symbol_errors
          total_rx_error_packets
          total_tx_error_packets
          total_runts
          total_giants
          total_discards    — rx_discard + tx_discard
          total_errors      — sum of all error fields
          error_rate_per_hour — total_errors / hours_back (approx)
          reset_count       — number of intervals that had a counter reset
          has_any_errors    — True if total_errors > 0

        Sorted by total_errors descending, top_n returned.
        """
        until_iso = until or datetime.now(timezone.utc).isoformat()
        since_iso = (
            datetime.now(timezone.utc) - timedelta(hours=hours_back)
        ).isoformat()

        # Build the WHERE clause for interface selection
        where = "WHERE i.instance_name = ? AND s.polled_at >= ? AND s.polled_at <= ?"
        params: list = [instance_name, since_iso, until_iso]

        if system_ids:
            placeholders = ",".join("?" * len(system_ids))
            where += f" AND i.system_id IN ({placeholders})"
            params.extend(system_ids)

        rows = self._con.execute(
            f"""SELECT i.system_id, i.interface_name, s.*
                FROM counter_snapshots s
                JOIN interfaces i ON i.id = s.interface_id
                {where}
                ORDER BY i.system_id, i.interface_name, s.polled_at ASC""",
            params,
        ).fetchall()

        # Group by interface, compute deltas, aggregate
        from itertools import groupby
        groups: dict[tuple, list] = {}
        for row in rows:
            key = (row["system_id"], row["interface_name"])
            groups.setdefault(key, []).append(dict(row))

        results = []
        for (sid, ifname), snaps in groups.items():
            deltas = _compute_deltas(snaps)
            if not deltas:
                continue

            agg: dict[str, int] = {f"total_{f}": 0 for f in ERROR_FIELDS}
            agg["total_rx_discard_packets"] = 0
            agg["total_tx_discard_packets"] = 0
            reset_count = 0
            for d in deltas:
                for f in ERROR_FIELDS:
                    agg[f"total_{f}"] += d.get(f, 0)
                agg["total_rx_discard_packets"] += d.get("rx_discard_packets", 0)
                agg["total_tx_discard_packets"] += d.get("tx_discard_packets", 0)
                if d.get("has_reset"):
                    reset_count += 1

            total_errors = sum(agg[f"total_{f}"] for f in ERROR_FIELDS)
            total_discards = (
                agg["total_rx_discard_packets"] + agg["total_tx_discard_packets"]
            )
            results.append({
                "system_id":          sid,
                "interface_name":     ifname,
                "snapshot_count":     len(snaps),
                **agg,
                "total_discards":     total_discards,
                "total_errors":       total_errors,
                "error_rate_per_hour": round(total_errors / max(hours_back, 1), 4),
                "reset_count":        reset_count,
                "has_any_errors":     total_errors > 0,
            })

        results.sort(key=lambda r: r["total_errors"], reverse=True)
        return results[:top_n]

    def get_coverage_summary(self, instance_name: str) -> dict:
        """
        Return a summary of what's stored: how many interfaces and snapshots,
        when data coverage starts.
        """
        iface_count = self._con.execute(
            "SELECT COUNT(*) FROM interfaces WHERE instance_name=?",
            (instance_name,),
        ).fetchone()[0]

        snap_count = self._con.execute(
            """SELECT COUNT(*) FROM counter_snapshots s
               JOIN interfaces i ON i.id = s.interface_id
               WHERE i.instance_name = ?""",
            (instance_name,),
        ).fetchone()[0]

        oldest = self._con.execute(
            """SELECT MIN(s.polled_at) FROM counter_snapshots s
               JOIN interfaces i ON i.id = s.interface_id
               WHERE i.instance_name = ?""",
            (instance_name,),
        ).fetchone()[0]

        newest = self._con.execute(
            """SELECT MAX(s.polled_at) FROM counter_snapshots s
               JOIN interfaces i ON i.id = s.interface_id
               WHERE i.instance_name = ?""",
            (instance_name,),
        ).fetchone()[0]

        return {
            "instance_name":    instance_name,
            "interface_count":  iface_count,
            "snapshot_count":   snap_count,
            "oldest_snapshot":  oldest,
            "newest_snapshot":  newest,
        }


# ── Delta computation (pure function, no DB dependency) ────────────────────────

def _compute_deltas(snapshots: list[dict]) -> list[dict]:
    """
    Given a list of raw snapshot dicts (oldest first), return a list of
    per-interval delta dicts.

    The returned list has len(snapshots) - 1 entries.  Returns [] if fewer
    than 2 snapshots are provided.

    Any counter that decreases between two consecutive snapshots is treated as
    a counter reset/wrap: that field's delta is set to 0 and `has_reset` is
    set True on that row.
    """
    if len(snapshots) < 2:
        return []

    deltas = []
    for i in range(1, len(snapshots)):
        prev = snapshots[i - 1]
        curr = snapshots[i]

        # Compute interval duration
        try:
            t1 = datetime.fromisoformat(prev["polled_at"].rstrip("Z") + "+00:00")
            t2 = datetime.fromisoformat(curr["polled_at"].rstrip("Z") + "+00:00")
            interval_s = max((t2 - t1).total_seconds(), 1.0)
        except (ValueError, KeyError):
            interval_s = 300.0  # assume 5-min default

        delta: dict = {
            "polled_at":        curr["polled_at"],
            "interval_seconds": interval_s,
        }
        has_reset = False
        for f in ALL_COUNTER_FIELDS:
            d = (curr.get(f) or 0) - (prev.get(f) or 0)
            if d < 0:
                has_reset = True
                d = 0
            delta[f] = d

        delta["has_reset"] = has_reset
        delta["total_errors"] = sum(delta.get(f, 0) for f in ERROR_FIELDS)
        deltas.append(delta)

    return deltas
