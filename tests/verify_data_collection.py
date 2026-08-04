#!/usr/bin/env python3
"""
verify_data_collection.py

Confirm the background pollers are actively extracting data from Apstra into the
local SQLite stores. The server runs two collection pipelines:

  * anomaly poller  — 30-day anomaly timeline   (polls every ~60s)
  * counter poller  — interface counter samples (polls every ~5 min)

This tool opens both stores read-only and reports whether data is present and
recent, so you can confirm collection is healthy without watching server logs.

Run it a few minutes AFTER starting the server, so the pollers have had time to
backfill history and take at least one incremental sample.

Usage:
    python tests/verify_data_collection.py
    python tests/verify_data_collection.py --max-age-minutes 15
    python tests/verify_data_collection.py --json

Exit code:
    0  data present and fresh (collection is healthy)
    1  stores missing/empty, or newest data is older than --max-age-minutes
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from primitives.anomaly_store import AnomalyStore
from primitives.counter_store import CounterStore


def _supports_color() -> bool:
    return sys.stdout.isatty() and not os.getenv("NO_COLOR")


def _c(text: str, code: str) -> str:
    if not _supports_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def _ok(msg: str) -> str:
    return f"{_c('✓', '32')} {msg}"


def _warn(msg: str) -> str:
    return f"{_c('⚠', '33')} {msg}"


def _fail(msg: str) -> str:
    return f"{_c('✗', '31')} {msg}"


def _resolve_store_paths() -> tuple[Path, Path]:
    """Mirror server._resolve_store_paths so this tool inspects the same files."""
    data_dir_env = os.environ.get("MCP_DATA_DIR")
    base_dir = Path(data_dir_env).expanduser() if data_dir_env else (REPO_ROOT / "data")

    anomaly_override = os.environ.get("MCP_ANOMALY_DB_PATH")
    counter_override = os.environ.get("MCP_COUNTER_DB_PATH")

    anomaly_path = (
        Path(anomaly_override).expanduser()
        if anomaly_override
        else base_dir / "anomaly_timeseries.db"
    )
    counter_path = (
        Path(counter_override).expanduser()
        if counter_override
        else base_dir / "counter_timeseries.db"
    )
    return anomaly_path, counter_path


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    text = ts.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_minutes(ts: str | None, now: datetime) -> float | None:
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    return (now - parsed).total_seconds() / 60.0


def _fmt_age(age: float | None) -> str:
    if age is None:
        return "unknown age"
    if age < 1:
        return "just now"
    if age < 120:
        return f"{age:.0f} min ago"
    return f"{age / 60:.1f} h ago"


def check_anomaly_collection(path: Path, max_age: float, now: datetime) -> tuple[bool, dict, list[str]]:
    lines: list[str] = []
    if not path.exists():
        lines.append(_fail(f"Anomaly store not found: {path}"))
        lines.append("    Start the server first (the poller creates and fills this store).")
        return False, {"present": False}, lines

    store = AnomalyStore(db_path=path)
    try:
        status = store.get_collection_status()
    finally:
        store.close()

    poll_states = status["poll_states"]
    lines.append(
        f"Anomaly store: {status['anomaly_count']} anomalies, "
        f"{status['event_count']} events across {len(poll_states)} blueprint(s)"
    )

    if not poll_states:
        lines.append(_fail("No blueprint poll state yet — the anomaly poller has not run."))
        return False, status, lines

    healthy = True
    for ps in poll_states:
        label = f"{ps['instance_name']}/{ps['blueprint_id'][:8]}"
        age = _age_minutes(ps["last_poll_at"], now)
        if not ps["backfill_complete"]:
            lines.append(_warn(f"{label}: backfill still in progress ({_fmt_age(age)})"))
            continue
        if age is None:
            lines.append(_fail(f"{label}: no last-poll timestamp recorded"))
            healthy = False
        elif age > max_age:
            lines.append(_fail(f"{label}: last poll {_fmt_age(age)} — exceeds {max_age:.0f} min"))
            healthy = False
        else:
            lines.append(_ok(f"{label}: last poll {_fmt_age(age)}"))

    return healthy, status, lines


def check_counter_collection(path: Path, max_age: float, now: datetime) -> tuple[bool, dict, list[str]]:
    lines: list[str] = []
    if not path.exists():
        lines.append(_fail(f"Counter store not found: {path}"))
        lines.append("    Start the server first (the poller creates and fills this store).")
        return False, {"present": False}, lines

    store = CounterStore(db_path=path)
    try:
        instances = store.list_instances()
        summaries = [store.get_coverage_summary(name) for name in instances]
    finally:
        store.close()

    if not summaries:
        lines.append(_fail("No interface counter data yet — the counter poller has not sampled."))
        lines.append("    Note: the counter poller samples every ~5 minutes.")
        return False, {"instances": []}, lines

    healthy = True
    for summary in summaries:
        name = summary["instance_name"]
        age = _age_minutes(summary["newest_snapshot"], now)
        detail = f"{summary['interface_count']} interfaces, {summary['snapshot_count']} snapshots"
        if summary["snapshot_count"] == 0:
            lines.append(_warn(f"{name}: interfaces discovered but no snapshots yet ({detail})"))
            continue
        if age is None:
            lines.append(_fail(f"{name}: no snapshot timestamp recorded ({detail})"))
            healthy = False
        elif age > max_age:
            lines.append(_fail(f"{name}: newest sample {_fmt_age(age)} — exceeds {max_age:.0f} min ({detail})"))
            healthy = False
        else:
            lines.append(_ok(f"{name}: newest sample {_fmt_age(age)} ({detail})"))

    return healthy, {"instances": summaries}, lines


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="verify_data_collection.py",
        description="Confirm the background pollers are extracting Apstra data into the local stores.",
    )
    parser.add_argument(
        "--max-age-minutes",
        type=float,
        default=15.0,
        help="Fail if the newest sample is older than this (default: 15; counter poller runs every 5 min).",
    )
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable JSON report instead of text.")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    anomaly_path, counter_path = _resolve_store_paths()

    anomaly_ok, anomaly_status, anomaly_lines = check_anomaly_collection(anomaly_path, args.max_age_minutes, now)
    counter_ok, counter_status, counter_lines = check_counter_collection(counter_path, args.max_age_minutes, now)

    overall_ok = anomaly_ok and counter_ok

    if args.json:
        report = {
            "checked_at": now.isoformat(),
            "max_age_minutes": args.max_age_minutes,
            "healthy": overall_ok,
            "anomaly": {"path": str(anomaly_path), "healthy": anomaly_ok, "status": anomaly_status},
            "counter": {"path": str(counter_path), "healthy": counter_ok, "status": counter_status},
        }
        print(json.dumps(report, indent=2, default=str))
        return 0 if overall_ok else 1

    print()
    print("=" * 68)
    print("Apstra MCP — data collection verification")
    print("=" * 68)
    print(f"Checked at {now.isoformat(timespec='seconds')} (max age {args.max_age_minutes:.0f} min)")

    print()
    print("Anomaly timeline poller")
    print("-" * 68)
    for line in anomaly_lines:
        print(f"  {line}")

    print()
    print("Interface counter poller")
    print("-" * 68)
    for line in counter_lines:
        print(f"  {line}")

    print()
    if overall_ok:
        print(_ok("Data collection is healthy — both pipelines are extracting recent data."))
    else:
        print(_fail("Data collection is NOT healthy. See the details above."))
        print("    Common causes: the server was just started (give it a few minutes),")
        print("    the server is not running, or it cannot authenticate to Apstra")
        print("    (run: python tests/diagnose_connection.py).")
    print()
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
