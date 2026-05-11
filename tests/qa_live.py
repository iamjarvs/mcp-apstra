"""
qa_live.py  —  one-shot live QA script.

Runs the real _backfill() against the live Apstra API into a temp DB,
then prints a breakdown of what was written vs what the API exposes.
"""
import asyncio
import json
import logging
import os
import sqlite3
import tempfile
from pathlib import Path

# Set creds before importing project code
os.environ.setdefault("APSTRA_HOST",     "https://10.28.216.3")
os.environ.setdefault("APSTRA_USERNAME", "admin")
os.environ.setdefault("APSTRA_PASSWORD", "admin")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from primitives.auth_manager import ApstraSession
from primitives.anomaly_store import AnomalyStore
from primitives import live_data_client
from handlers.anomaly_poller import _backfill, BACKFILL_DAYS


async def main():
    tmp = Path(tempfile.mktemp(suffix=".db"))
    store = AnomalyStore(tmp)

    session = ApstraSession(
        name="qa",
        host="https://10.28.216.3",
        username="admin",
        password="admin",
        ssl_verify=False,
    )
    await session.authenticate()
    print("✓ Authenticated")

    raw = await live_data_client.get_blueprints(session)
    blueprints = raw.get("items", [])
    print(f"✓ Blueprints: {[b['label'] for b in blueprints]}")

    for bp in blueprints:
        print(f"\n── Backfilling: {bp['label'][:50]} ──")
        await _backfill(session, bp["id"], store)

    # ── DB summary ────────────────────────────────────────────────────────────
    con = sqlite3.connect(str(tmp))
    con.row_factory = sqlite3.Row

    a_count = con.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0]
    e_count = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    print(f"\n{'='*60}")
    print(f"  DB RESULT: {a_count} anomaly identities, {e_count} events")
    print(f"{'='*60}")

    # Per-type breakdown
    rows = con.execute("""
        SELECT a.anomaly_type,
               COUNT(DISTINCT a.id)                          AS identities,
               COUNT(ev.id)                                  AS events,
               SUM(CASE WHEN ev.raised=1 THEN 1 ELSE 0 END) AS raises,
               SUM(CASE WHEN ev.raised=0 THEN 1 ELSE 0 END) AS clears
        FROM anomalies a
        JOIN events ev ON ev.anomaly_id = a.id
        GROUP BY a.anomaly_type
        ORDER BY identities DESC
    """).fetchall()

    print(f"\n  {'type':15s} {'identities':>10} {'events':>7} {'raises':>7} {'clears':>7}")
    print(f"  {'-'*50}")
    for r in rows:
        print(f"  {r['anomaly_type']:15s} {r['identities']:>10} {r['events']:>7} {r['raises']:>7} {r['clears']:>7}")

    # expected_json / actual_json coverage
    with_exp = con.execute("SELECT COUNT(*) FROM anomalies WHERE expected_json IS NOT NULL AND expected_json != 'null'").fetchone()[0]
    with_act = con.execute("SELECT COUNT(*) FROM anomalies WHERE actual_json IS NOT NULL AND actual_json != 'null'").fetchone()[0]
    print(f"\n  expected_json populated: {with_exp}/{a_count}")
    print(f"  actual_json  populated: {with_act}/{a_count}")

    # Sample 3 non-liveness anomalies
    print(f"\n  Sample anomalies (non-liveness):")
    samples = con.execute("""
        SELECT a.anomaly_type, a.device_hostname, a.identity_json,
               a.expected_json, a.actual_json, COUNT(ev.id) AS ev_count
        FROM anomalies a
        JOIN events ev ON ev.anomaly_id = a.id
        WHERE a.anomaly_type != 'liveness'
        GROUP BY a.id
        ORDER BY RANDOM()
        LIMIT 5
    """).fetchall()
    for s in samples:
        print(f"    [{s['anomaly_type']}] dev={s['device_hostname']}  id={s['identity_json']}")
        print(f"      expected={s['expected_json']}  actual={s['actual_json']}  events={s['ev_count']}")

    # ── Compare against live API counts ───────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  API COMPARISON (30-day counts)")
    print(f"{'='*60}")

    for bp in blueprints:
        counts_raw = await live_data_client.get_anomaly_history_counts(
            session, bp["id"], begin_time=f"-{BACKFILL_DAYS}:0"
        )
        counts = counts_raw.get("counts", {})
        api_change_points = sum(len(v) for v in counts.values())
        api_types_with_activity = {t for t, v in counts.items() if v}

        db_types = {r["anomaly_type"] for r in rows}

        print(f"\n  Blueprint: {bp['label'][:50]}")
        print(f"  API change-points (30d): {api_change_points}")
        print(f"  API types with activity: {sorted(api_types_with_activity)}")
        print(f"  DB types captured:       {sorted(db_types)}")
        missing_types = api_types_with_activity - db_types
        if missing_types:
            print(f"  ⚠  MISSING TYPES: {sorted(missing_types)}")
        else:
            print(f"  ✓ All active types are in DB")

    tmp.unlink(missing_ok=True)
    print(f"\nDone.")


asyncio.run(main())
