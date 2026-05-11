"""
anomaly_poller.py

Background asyncio task that keeps the AnomalyStore populated with a
rolling 7-day anomaly time-series for every blueprint on every instance.

Lifecycle
---------
  run_anomaly_poller(sessions, store)
    → for each session: enumerate blueprints
    → for each blueprint: run _backfill() then loop _incremental_poll()

Backfill strategy (bounded concurrent API calls)
-------------------------------------------------
  1. Take a coarse set of historical snapshots (every 6 hours over 7 days)
     to discover anomaly identities that have since cleared
     ≈ 28 API calls, executed concurrently (up to 20 at a time)
  2. Snapshot around historical count-change timestamps to catch short-lived
     anomalies (up to 500 calls, executed concurrently in batches of 20)
  3. Grab the current live anomalies list (1 call)
  4. Trace every discovered identity for the full 7-day window (N calls,
     executed concurrently in batches of 20)
     (N ≈ total unique identities, typically 30-100 per fabric)

  Total: ≈ 30 + N API calls per blueprint.  On a 30-anomaly fabric this is
  well under 100 calls. With concurrent batching (20 at a time), this
  completes in ~5 seconds instead of sequential 30+ seconds.

Incremental poll (every 60 s)
------------------------------
  1. Fetch /counts — cheapest possible change-detection (1 call)
  2. If any type changed since last poll: take one /history snapshot (1 call)
  3. Diff against stored previous snapshot → raise/clear events
  4. For any NEW identity: kick off a trace to backfill its history
  5. Prune events older than 7 days

Cost: 1–2 API calls per blueprint per minute at steady state.
"""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from primitives import live_data_client
from primitives.anomaly_store import AnomalyStore

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
BACKFILL_DAYS = 30              # days of event history to discover on startup
MAX_COUNT_CHANGE_SNAPSHOTS = 1000  # safety cap; real-world fabrics produce ~1000/30 days
MAX_CONCURRENT_API_CALLS = 20      # max concurrent snapshot calls during backfill


# ── Concurrent helper ──────────────────────────────────────────────────────────

async def _gather_with_semaphore(coroutines: list, max_concurrent: int = 20):
    """
    Execute a list of coroutines concurrently with a concurrency limit.
    
    Uses asyncio.Semaphore to limit the number of concurrent tasks to
    max_concurrent. This prevents overwhelming the Apstra API during backfill.
    
    Returns: list of results in the same order as input coroutines.
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def limited(coro):
        async with semaphore:
            return await coro
    
    return await asyncio.gather(*[limited(coro) for coro in coroutines], return_exceptions=False)

def _identity_key(a: dict) -> str:
    """
    Return a stable, normalised string key for an anomaly's physical identity.

    Strips fields that are not part of the device identity (e.g. some API
    endpoints embed anomaly_type inside the identity dict) so the same anomaly
    always produces the same key regardless of which endpoint returned it.
    This key is used for snapshot diffing and must match the key stored in
    the anomaly_store (which also strips these fields in upsert_anomaly).
    """
    _NON_IDENTITY_FIELDS = frozenset({"anomaly_type"})
    clean = {k: v for k, v in a["identity"].items() if k not in _NON_IDENTITY_FIELDS}
    return json.dumps(clean, sort_keys=True)

async def run_anomaly_poller(sessions: list, store: AnomalyStore) -> None:
    """
    Starts one poller task per session.  Each task enumerates the session's
    blueprints, backfills 7 days of history, then polls incrementally.

    Designed to be launched as an asyncio background task from the server
    lifespan and to run indefinitely.
    """
    tasks = [
        asyncio.create_task(
            _session_poller(session, store),
            name=f"anomaly-poller-{session.name}",
        )
        for session in sessions
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


# ── Per-session loop ──────────────────────────────────────────────────────────

async def _session_poller(session, store: AnomalyStore) -> None:
    log.info("[%s] anomaly poller starting", session.name)
    try:
        raw = await live_data_client.get_blueprints(session)
        blueprints = [
            {"id": bp["id"], "label": bp.get("label", bp["id"])}
            for bp in raw.get("items", [])
        ]
    except Exception as exc:
        log.error("[%s] failed to enumerate blueprints: %s", session.name, exc)
        return

    log.info("[%s] found %d blueprint(s): %s",
             session.name, len(blueprints), [b["label"] for b in blueprints])

    # Backfill all blueprints concurrently (each one is independent)
    await asyncio.gather(
        *[_backfill(session, bp["id"], store) for bp in blueprints],
        return_exceptions=True,
    )

    # Incremental loop
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        await asyncio.gather(
            *[_incremental_poll(session, bp["id"], store) for bp in blueprints],
            return_exceptions=True,
        )
        store.prune()


# ── Backfill ──────────────────────────────────────────────────────────────────

async def _backfill(session, blueprint_id: str, store: AnomalyStore) -> None:
    """
    One-shot 30-day history collection using the counts → snapshot approach.

    Strategy
    --------
    1. Call the counts API for all 11 anomaly types over the last BACKFILL_DAYS.
       This returns every timestamp at which any count changed — these are the
       exact moments when anomalies raised or cleared.
    2. Snapshot the fabric concurrently at every change-point timestamp.  Each
       snapshot returns the full anomaly list (with identity, expected, actual)
       active at that instant.
    3. Walk snapshots in chronological order.  Anomalies that *appear* generate
       a raised=True event; anomalies that *disappear* generate a raised=False
       event.  This accurately reconstructs raise/clear history without needing
       the trace API and covers the full 30-day window.
    4. Reconcile the last snapshot against the current live state to close out
       any anomalies that cleared after the final count-change timestamp.
    """
    state = store.get_poll_state(blueprint_id, session.name)
    if state.get("backfill_complete"):
        log.info("[%s/%s] backfill already complete, skipping",
                 session.name, blueprint_id[:8])
        return

    log.info("[%s/%s] starting %d-day backfill via counts+snapshots ...",
             session.name, blueprint_id[:8], BACKFILL_DAYS)

    # ── Step 1: collect all count-change timestamps ───────────────────────────
    try:
        counts_raw = await live_data_client.get_anomaly_history_counts(
            session, blueprint_id,
            begin_time=f"-{BACKFILL_DAYS}:0",
        )
    except Exception as exc:
        log.error("[%s/%s] failed to fetch count history: %s",
                  session.name, blueprint_id[:8], exc)
        return

    # Build the list of targeted (anomaly_type, timestamp) snapshot requests.
    # Only include entries where count > 0 — count=0 entries mean no active
    # anomalies of that type, so a snapshot would return nothing useful.
    # Also track the first count=0 timestamp *after* each type's last non-zero
    # entry so we can emit precise clear events at that moment.
    per_type_requests: list[tuple[str, str]] = []   # (atype, ts) where count > 0
    per_type_clear_ts: dict[str, str | None] = {}   # atype → first count=0 ts after last non-zero
    type_summary: dict[str, int] = {}               # non-zero entry count per type (for logging)

    for atype, series in counts_raw.get("counts", {}).items():
        sorted_series = sorted(series, key=lambda x: x.get("timestamp", ""))
        nonzero = [
            item for item in sorted_series
            if item.get("timestamp") and item.get("count", 0) > 0
        ]
        type_summary[atype] = len(nonzero)
        if not nonzero:
            per_type_clear_ts[atype] = None
            continue
        for item in nonzero:
            per_type_requests.append((atype, item["timestamp"]))
        # First count=0 entry after the last non-zero timestamp → clear moment
        last_ts = nonzero[-1]["timestamp"]
        per_type_clear_ts[atype] = next(
            (item["timestamp"] for item in sorted_series
             if item.get("timestamp", "") > last_ts and item.get("count", 0) == 0),
            None,
        )

    total_requests = len(per_type_requests)
    log.info("[%s/%s] found %d non-zero change-points over %d days — %s",
             session.name, blueprint_id[:8],
             total_requests, BACKFILL_DAYS,
             ", ".join(f"{t}:{n}" for t, n in sorted(type_summary.items())))

    if total_requests > MAX_COUNT_CHANGE_SNAPSHOTS:
        log.warning("[%s/%s] capping %d per-type requests to the %d most recent",
                    session.name, blueprint_id[:8],
                    total_requests, MAX_COUNT_CHANGE_SNAPSHOTS)
        per_type_requests.sort(key=lambda x: x[1])
        per_type_requests = per_type_requests[-MAX_COUNT_CHANGE_SNAPSHOTS:]

    # ── Step 2: targeted snapshot per (type, timestamp) (concurrent) ─────────
    # Each request sends anomaly_types=[atype] so the server only returns data
    # for that one type, keeping responses small and avoiding wasted work.
    async def _fetch_snapshot_for_type(atype: str, ts: str):
        try:
            raw = await live_data_client.get_anomaly_history_snapshot(
                session, blueprint_id, ts, anomaly_types=[atype]
            )
            return (atype, ts), raw.get("items", [])
        except Exception as exc:
            log.debug("[%s/%s] snapshot %s@%s failed: %s",
                      session.name, blueprint_id[:8], atype, ts, exc)
            return (atype, ts), []

    snapshot_results = await _gather_with_semaphore(
        [_fetch_snapshot_for_type(atype, ts) for atype, ts in per_type_requests],
        max_concurrent=MAX_CONCURRENT_API_CALLS,
    )

    log.info("[%s/%s] all snapshots fetched, processing transitions ...",
             session.name, blueprint_id[:8])

    # ── Step 3: per-type raise/clear event derivation ─────────────────────────
    # Group results by type, sort chronologically within each group, then diff
    # consecutive snapshots of the same type to detect raise/clear events.
    # After the last non-zero snapshot for a type, emit clear events at the
    # first count=0 timestamp (the precise moment everything of that type ended).
    type_snapshots: dict[str, list[tuple[str, list]]] = defaultdict(list)
    for (atype, ts), items in snapshot_results:
        type_snapshots[atype].append((ts, items))
    for atype in type_snapshots:
        type_snapshots[atype].sort(key=lambda x: x[0])

    prev_active: dict[str, dict] = {}  # merged final active state (used by live reconcile)
    total_events = 0
    identities_seen: set[str] = set()

    for atype, ts_items in type_snapshots.items():
        prev_active_type: dict[str, dict] = {}

        for ts, items in ts_items:
            current_active = {_identity_key(a): a for a in items}

            # Anomalies that appeared since the previous snapshot → raised
            for key, a in current_active.items():
                if key not in prev_active_type:
                    aid = store.upsert_anomaly(blueprint_id, session.name, a)
                    identities_seen.add(key)
                    written = store.insert_event(
                        aid, ts, raised=True,
                        actual=a.get("actual"), source="snapshot_backfill",
                    )
                    if written:
                        total_events += 1

            # Anomalies that disappeared since the previous snapshot → cleared
            for key, a in prev_active_type.items():
                if key not in current_active:
                    aid = store.upsert_anomaly(blueprint_id, session.name, a)
                    written = store.insert_event(
                        aid, ts, raised=False,
                        actual=None, source="snapshot_backfill",
                    )
                    if written:
                        total_events += 1

            prev_active_type = current_active

        # Emit clears at the count=0 timestamp for anomalies still active after
        # the last non-zero snapshot (i.e. the count dropped to 0 at clear_ts).
        clear_ts = per_type_clear_ts.get(atype)
        if clear_ts and prev_active_type:
            for key, a in prev_active_type.items():
                aid = store.upsert_anomaly(blueprint_id, session.name, a)
                written = store.insert_event(
                    aid, clear_ts, raised=False,
                    actual=None, source="snapshot_backfill",
                )
                if written:
                    total_events += 1
            prev_active_type = {}

        prev_active.update(prev_active_type)

    log.info("[%s/%s] transition pass: %d events from %d unique identities across %d requests",
             session.name, blueprint_id[:8],
             total_events, len(identities_seen), total_requests)

    # ── Step 4: reconcile final snapshot against live state ───────────────────
    # Anomalies that were in the last count-change snapshot may have cleared
    # since then (no further count-change entry was generated yet).
    # Anomalies raised before the 30-day window will not appear in any
    # count-change snapshot but will be visible in the live state.
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        live_snapshot = await _take_snapshot(session, blueprint_id)

        # Still in last change-point snapshot but not live → cleared since then
        for key, a in prev_active.items():
            if key not in live_snapshot:
                aid = store.upsert_anomaly(blueprint_id, session.name, a)
                written = store.insert_event(
                    aid, now_iso, raised=False,
                    actual=None, source="live_reconcile",
                )
                if written:
                    total_events += 1

        # Currently live but not seen in any change-point snapshot
        # → raised before our discovery window; write a raise event at now()
        for key, a in live_snapshot.items():
            if key not in identities_seen:
                aid = store.upsert_anomaly(blueprint_id, session.name, a)
                written = store.insert_event(
                    aid, now_iso, raised=True,
                    actual=a.get("actual"), source="live_reconcile",
                )
                if written:
                    total_events += 1

        log.info("[%s/%s] live reconcile: %d active now, %d in final change-point snapshot",
                 session.name, blueprint_id[:8], len(live_snapshot), len(prev_active))
    except Exception as exc:
        log.warning("[%s/%s] live reconciliation failed: %s",
                    session.name, blueprint_id[:8], exc)

    # ── Step 5: capture baseline for incremental polling ─────────────────────
    try:
        current_snapshot = await _take_snapshot(session, blueprint_id)
        current_counts   = await _take_counts(session, blueprint_id)
        store.set_poll_state(
            blueprint_id, session.name,
            last_counts=current_counts,
            last_snapshot=current_snapshot,
            backfill_complete=True,
        )
    except Exception as exc:
        log.warning("[%s/%s] failed to capture baseline snapshot: %s",
                    session.name, blueprint_id[:8], exc)
        store.set_poll_state(
            blueprint_id, session.name,
            last_counts={}, last_snapshot={},
            backfill_complete=True,
        )

    log.info("[%s/%s] backfill complete — %d total events written",
             session.name, blueprint_id[:8], total_events)


# ── Incremental poll ──────────────────────────────────────────────────────────

async def _incremental_poll(session, blueprint_id: str, store: AnomalyStore) -> None:
    state = store.get_poll_state(blueprint_id, session.name)
    if not state.get("backfill_complete"):
        return  # still backfilling

    try:
        current_counts = await _take_counts(session, blueprint_id)
    except Exception as exc:
        log.warning("[%s/%s] counts fetch failed: %s",
                    session.name, blueprint_id[:8], exc)
        return

    # Compare totals: if nothing changed since last poll, skip the snapshot
    prev_counts  = state.get("last_counts", {})
    changed = _counts_changed(prev_counts, current_counts)
    if not changed:
        # Still update last_poll_at without touching snapshot
        store.set_poll_state(
            blueprint_id, session.name,
            last_counts=current_counts,
            last_snapshot=state.get("last_snapshot", {}),
        )
        return

    # Something changed — take a full snapshot and diff
    try:
        current_snapshot = await _take_snapshot(session, blueprint_id)
    except Exception as exc:
        log.warning("[%s/%s] snapshot fetch failed: %s",
                    session.name, blueprint_id[:8], exc)
        return

    prev_snapshot = state.get("last_snapshot", {})
    now_iso = _norm_ts(datetime.now(timezone.utc).isoformat())

    new_keys     = set(current_snapshot) - set(prev_snapshot)
    cleared_keys = set(prev_snapshot) - set(current_snapshot)

    for key in new_keys:
        a = current_snapshot[key]
        aid = store.upsert_anomaly(blueprint_id, session.name, a)
        # Write a raise event at the current time
        store.insert_event(aid, now_iso, raised=True,
                           actual=a.get("actual"), source="snapshot_diff")
        # Trace to get more precise timing
        asyncio.create_task(
            _trace_new_identity(session, blueprint_id, a, aid, store),
            name=f"trace-{blueprint_id[:8]}-{a.get('anomaly_type')}",
        )

    for key in cleared_keys:
        a = prev_snapshot[key]
        aid = store.upsert_anomaly(blueprint_id, session.name, a)
        store.insert_event(aid, now_iso, raised=False,
                           actual=None, source="snapshot_diff")

    if new_keys or cleared_keys:
        log.info("[%s/%s] poll: +%d raised, -%d cleared",
                 session.name, blueprint_id[:8], len(new_keys), len(cleared_keys))

    store.set_poll_state(
        blueprint_id, session.name,
        last_counts=current_counts,
        last_snapshot=current_snapshot,
    )


async def _trace_new_identity(session, blueprint_id, a, aid, store):
    """Trace a newly-discovered identity and backfill its recent history."""
    events_written = 0
    try:
        raw = await live_data_client.get_anomaly_trace(
            session, blueprint_id,
            a["anomaly_type"], a["identity"],
            begin_time=f"-{BACKFILL_DAYS}:0",
        )
        for ev in raw.get("items", []):
            ts_val = ev.get("detected_at") or ev.get("timestamp")
            if not ts_val or ts_val.startswith("1970"):
                continue
            written = store.insert_event(
                aid, _norm_ts(ts_val), bool(ev.get("raised")),
                ev.get("actual"), source="trace_incremental",
            )
            if written:
                events_written += 1
    except Exception as exc:
        log.debug("trace for new identity %s failed: %s",
                  a.get("anomaly_type"), exc)
    if events_written == 0:
        detected = a.get("detected_at") or ""
        if detected and not detected.startswith("1970"):
            store.insert_event(
                aid, _norm_ts(detected), raised=True,
                actual=a.get("actual"), source="synthetic_raise",
            )


# ── API helpers ───────────────────────────────────────────────────────────────

async def _take_snapshot(session, blueprint_id: str) -> dict:
    """Return {identity_key: anomaly_dict} for the current live state."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = await live_data_client.get_anomaly_history_snapshot(
        session, blueprint_id, now_iso
    )
    return {
        _identity_key(a): a
        for a in raw.get("items", [])
    }


async def _take_counts(session, blueprint_id: str) -> dict:
    """Return {anomaly_type: latest_count} from the counts API."""
    raw = await live_data_client.get_anomaly_history_counts(
        session, blueprint_id, begin_time=None
    )
    counts = raw.get("counts", {})
    # Each type's list is sorted by time; last entry is the current count
    return {
        atype: series[-1]["count"]
        for atype, series in counts.items()
        if series
    }


def _counts_changed(prev: dict, current: dict) -> bool:
    """True if any anomaly type's count differs between the two dicts."""
    all_types = set(prev) | set(current)
    return any(prev.get(t, 0) != current.get(t, 0) for t in all_types)


def _norm_ts(ts: str) -> str:
    """Normalise Apstra timestamp variants to a consistent UTC Z-suffix string."""
    # Replace +00:00 suffix with Z for uniform sorting
    if ts.endswith("+00:00"):
        return ts[:-6] + "Z"
    return ts
