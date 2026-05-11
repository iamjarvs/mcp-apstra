"""
QA script: simulate the new counts+snapshot backfill against the live API
and verify all data is correctly stored (identity, expected, actual, events).
Run with:  python3 qa_backfill.py
"""
import asyncio, httpx, json, sqlite3, tempfile, pathlib, sys
sys.path.insert(0, ".")

HOST  = "https://10.28.216.3"
USER  = "admin"
PASS  = "admin"
BPID  = "evpn-vex-virtual"
INST  = "local"
ALL_TYPES = ["mac","hostname","interface","config","route","mlag",
             "cabling","deployment","bgp","liveness","lag"]

_NON_ID = frozenset({"anomaly_type"})

def ikey(a):
    return json.dumps({k:v for k,v in a["identity"].items() if k not in _NON_ID},
                      sort_keys=True)

async def main():
    async with httpx.AsyncClient(verify=False, timeout=60) as c:
        # auth
        r = await c.post(f"{HOST}/api/aaa/login",
                         json={"username": USER, "password": PASS})
        token = r.json()["token"]
        hdrs  = {"AuthToken": token}

        # 1. counts 30 days
        r = await c.post(f"{HOST}/api/blueprints/{BPID}/anomalies-history/counts",
                         headers=hdrs,
                         json={"begin_time": "-30:0", "anomaly_types": ALL_TYPES})
        counts_data = r.json().get("counts", {})
        all_ts = sorted({item["timestamp"] for s in counts_data.values()
                         for item in s if item.get("timestamp")})
        print(f"[API] {len(all_ts)} count change-points over 30 days")
        for t in sorted(counts_data):
            s = counts_data[t]
            if s:
                print(f"  {t}: {len(s)} change-points")

        # 2. concurrent snapshots
        sem = asyncio.Semaphore(20)
        async def snap(ts):
            async with sem:
                r2 = await c.post(
                    f"{HOST}/api/blueprints/{BPID}/anomalies-history",
                    headers=hdrs, json={"timestamp": ts})
                return ts, r2.json().get("items", [])

        print(f"\nFetching {len(all_ts)} snapshots (20 concurrent)...")
        results = await asyncio.gather(*[snap(ts) for ts in all_ts])
        results.sort(key=lambda x: x[0])
        print(f"Snapshots received.")

        # 3. run through new backfill logic into a temp DB
        db_path = pathlib.Path(tempfile.mkdtemp()) / "qa.db"
        from primitives.anomaly_store import AnomalyStore
        store = AnomalyStore(db_path)

        prev, seen, total = {}, set(), 0
        from datetime import datetime, timezone

        for ts, items in results:
            curr = {ikey(a): a for a in items}
            for k, a in curr.items():
                if k not in prev:
                    aid = store.upsert_anomaly(BPID, INST, a)
                    seen.add(k)
                    if store.insert_event(aid, ts, True, a.get("actual"),
                                          "snapshot_backfill"):
                        total += 1
            for k, a in prev.items():
                if k not in curr:
                    aid = store.upsert_anomaly(BPID, INST, a)
                    if store.insert_event(aid, ts, False, None,
                                          "snapshot_backfill"):
                        total += 1
            prev = curr

        # 4. live reconcile
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = await c.post(f"{HOST}/api/blueprints/{BPID}/anomalies-history",
                         headers=hdrs, json={"timestamp": now})
        live = {ikey(a): a for a in r.json().get("items", [])}

        for k, a in prev.items():
            if k not in live:
                aid = store.upsert_anomaly(BPID, INST, a)
                if store.insert_event(aid, now, False, None, "live_reconcile"):
                    total += 1
        for k, a in live.items():
            if k not in seen:
                aid = store.upsert_anomaly(BPID, INST, a)
                if store.insert_event(aid, now, True, a.get("actual"),
                                      "live_reconcile"):
                    total += 1

        # 5. DB summary
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        n_anom = con.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0]
        n_ev   = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        n_with_actual = con.execute(
            "SELECT COUNT(*) FROM anomalies "
            "WHERE actual_json IS NOT NULL AND actual_json != 'null'"
        ).fetchone()[0]

        print(f"\n=== DATABASE SUMMARY ===")
        print(f"  Anomaly identities : {n_anom}")
        print(f"  Events             : {n_ev}  (total written this run: {total})")
        print(f"  With actual_json   : {n_with_actual}")

        print("\n  Per-type breakdown:")
        for row in con.execute(
            "SELECT a.anomaly_type, COUNT(DISTINCT a.id) i, "
            "       SUM(e.raised) r, SUM(1-e.raised) cl "
            "FROM anomalies a JOIN events e ON e.anomaly_id=a.id "
            "GROUP BY a.anomaly_type ORDER BY i DESC"
        ).fetchall():
            print(f"    {row['anomaly_type']:15s} identities={row['i']:3d}"
                  f"  raises={int(row['r']):4d}  clears={int(row['cl']):4d}")

        # 6. schema spot-check: expected / actual / identity columns
        print("\n=== SAMPLE ROWS (identity + expected + actual for LLM use) ===")
        for row in con.execute(
            "SELECT a.anomaly_type, a.device_hostname, a.role, "
            "       a.identity_json, a.expected_json, a.actual_json "
            "FROM anomalies a "
            "WHERE a.actual_json IS NOT NULL AND a.actual_json != 'null' "
            "ORDER BY a.anomaly_type LIMIT 6"
        ).fetchall():
            print(f"\n  type={row['anomaly_type']}  device={row['device_hostname']}"
                  f"  role={row['role']}")
            print(f"    identity : {row['identity_json']}")
            print(f"    expected : {row['expected_json']}")
            print(f"    actual   : {row['actual_json']}")

        # 7. active-state accuracy check
        active_db = store.get_currently_active(BPID)
        db_active_keys = {ikey(a) for a in active_db}
        missing = set(live.keys()) - db_active_keys
        extra   = db_active_keys - set(live.keys())

        print(f"\n=== ACTIVE STATE COMPARISON ===")
        print(f"  DB currently-active : {len(active_db)}")
        print(f"  Live API            : {len(live)}")
        if missing:
            print(f"  !! MISSING from DB ({len(missing)}):")
            for k in sorted(missing)[:5]:
                print(f"     {k}")
        else:
            print(f"  ✓ All {len(live)} live API anomalies present in DB active set")
        if extra:
            print(f"  NOTE: {len(extra)} in DB active but cleared since reconcile snapshot")

        con.close()

asyncio.run(main())
