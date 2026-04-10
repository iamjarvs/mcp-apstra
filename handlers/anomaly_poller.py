"""
anomaly_poller.py

Background asyncio task that keeps the AnomalyStore populated with a
rolling 7-day anomaly time-series for every blueprint on every instance.

Lifecycle
---------
  run_anomaly_poller(sessions, store)
    → for each session: enumerate blueprints
    → for each blueprint: run _backfill() then loop _incremental_poll()

Backfill strategy (bounded API calls)
--------------------------------------
  1. Take a coarse set of historical snapshots (every 6 hours over 7 days)
     to discover anomaly identities that have since cleared  ≈ 28 API calls
  2. Grab the current live anomalies list                            1 call
  3. Trace every discovered identity for the full 7-day window   N calls
     (N ≈ total unique identities, typically 30-100 per fabric)

  Total: ≈ 30 + N API calls per blueprint.  On a 30-anomaly fabric this is
  well under 100 calls, completing in a few seconds.

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
from datetime import datetime, timedelta, timezone

from primitives import live_data_client
from primitives.anomaly_store import AnomalyStore

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60
BACKFILL_DAYS = 7
BACKFILL_SNAPSHOT_INTERVAL_HOURS = 6  # 28 snapshots over 7 days


# ── Public entry point ────────────────────────────────────────────────────────

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
    state = store.get_poll_state(blueprint_id, session.name)
    if state.get("backfill_complete"):
        log.info("[%s/%s] backfill already complete, skipping",
                 session.name, blueprint_id[:8])
        return

    log.info("[%s/%s] starting 7-day backfill ...", session.name, blueprint_id[:8])
    now = datetime.now(timezone.utc)

    # ── Step 1: coarse historical snapshots ──────────────────────────────────
    snapshots: list[dict] = []          # list of {"key": key, "anomaly": a}
    seen_keys: set[str] = set()

    interval = timedelta(hours=BACKFILL_SNAPSHOT_INTERVAL_HOURS)
    ts = now - timedelta(days=BACKFILL_DAYS)
    while ts <= now:
        iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            raw = await live_data_client.get_anomaly_history_snapshot(
                session, blueprint_id, iso
            )
            for a in raw.get("items", []):
                k = json.dumps(a["identity"], sort_keys=True)
                if k not in seen_keys:
                    seen_keys.add(k)
                    snapshots.append({"key": k, "anomaly": a})
        except Exception as exc:
            log.warning("[%s/%s] snapshot at %s failed: %s",
                        session.name, blueprint_id[:8], iso, exc)
        ts += interval

    # ── Step 2: current live anomalies ───────────────────────────────────────
    try:
        live_raw = await live_data_client.get_anomalies(session, blueprint_id)
        for a in live_raw.get("items", []):
            k = json.dumps(a["identity"], sort_keys=True)
            if k not in seen_keys:
                seen_keys.add(k)
                snapshots.append({"key": k, "anomaly": a})
    except Exception as exc:
        log.warning("[%s/%s] live anomalies fetch failed: %s",
                    session.name, blueprint_id[:8], exc)

    log.info("[%s/%s] backfill: discovered %d unique anomaly identities",
             session.name, blueprint_id[:8], len(snapshots))

    # ── Step 3: trace each identity ──────────────────────────────────────────
    total_events = 0
    for item in snapshots:
        a = item["anomaly"]
        aid = store.upsert_anomaly(blueprint_id, session.name, a)
        events_written = 0
        try:
            trace_raw = await live_data_client.get_anomaly_trace(
                session, blueprint_id,
                a["anomaly_type"], a["identity"],
                begin_time=f"-{BACKFILL_DAYS}:0",
            )
            for ev in trace_raw.get("items", []):
                ts_val = ev.get("detected_at") or ev.get("timestamp")
                if not ts_val or ts_val.startswith("1970"):
                    continue
                written = store.insert_event(
                    aid, _norm_ts(ts_val), bool(ev.get("raised")),
                    ev.get("actual"), source="trace_backfill",
                )
                if written:
                    total_events += 1
                    events_written += 1
        except Exception as exc:
            log.debug("[%s/%s] trace failed for %s: %s",
                      session.name, blueprint_id[:8], a.get("anomaly_type"), exc)

        # Persistent anomalies (e.g. BGP down since Jan) have no trace events
        # within the 7-day window.  Write a synthetic raise event anchored to
        # their detected_at so they appear in the store as currently active.
        if events_written == 0:
            detected = a.get("detected_at") or ""
            if detected and not detected.startswith("1970"):
                store.insert_event(
                    aid, _norm_ts(detected), raised=True,
                    actual=a.get("actual"), source="synthetic_raise",
                )
                total_events += 1

    # ── Step 4: snapshot current state as baseline for incremental polling ───
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

    log.info("[%s/%s] backfill complete — %d events written",
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
        json.dumps(a["identity"], sort_keys=True): a
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
