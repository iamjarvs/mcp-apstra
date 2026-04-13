"""
handlers/counter_poller.py

Background asyncio task that keeps the CounterStore populated with a rolling
7-day interface counter time-series for every managed system on every instance.

Lifecycle
---------
  run_counter_poller(sessions, counter_store)
    → for each session: enumerate all systems via GET /api/systems
    → loop: every COUNTER_POLL_INTERVAL seconds:
        for each system: GET /api/systems/{id}/counters → upsert snapshots
    → every PRUNE_INTERVAL_POLLS iterations: prune old data

Poll strategy
-------------
  1. Initial system discovery: GET /api/systems (1 call per session)
  2. Refresh system list every SYSTEM_REFRESH_POLLS poll iterations so that
     newly onboarded devices are picked up.
  3. Per-system counter poll: GET /api/systems/{system_id}/counters (1 call/system)
     For a 20-device fabric, this is 20 calls every 5 minutes — negligible
     load on the Apstra controller.
  4. Prune every PRUNE_INTERVAL_POLLS iterations (every ~1 hour).

On startup the poller waits one full interval before its first poll so that
the server lifespan doesn't time out at startup on slow controllers.

Counter resets / device reboots
--------------------------------
Cumulative counter values are stored as-is.  If a counter decreases between
two consecutive snapshots, _compute_deltas() in counter_store.py detects
this as a reset, sets that interval's delta to 0, and flags has_reset=True.
No special handling is needed in the poller.
"""

import asyncio
import logging
from datetime import datetime, timezone

from primitives import live_data_client
from primitives.counter_store import CounterStore

log = logging.getLogger(__name__)

COUNTER_POLL_INTERVAL_SECONDS = 300   # 5 minutes
SYSTEM_REFRESH_POLLS          = 12    # refresh system list every 12 polls (~1 hour)
PRUNE_INTERVAL_POLLS          = 12    # prune DB every 12 polls (~1 hour)


# ── Public entry point ────────────────────────────────────────────────────────

async def run_counter_poller(sessions: list, counter_store: CounterStore) -> None:
    """
    Starts one counter poller task per session.  Each task discovers all
    managed systems, then polls their interface counters at a fixed interval.

    Designed to be launched as an asyncio background task from the server
    lifespan and to run indefinitely.
    """
    tasks = [
        asyncio.create_task(
            _session_counter_poller(session, counter_store),
            name=f"counter-poller-{session.name}",
        )
        for session in sessions
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


# ── Per-session loop ──────────────────────────────────────────────────────────

async def _session_counter_poller(session, counter_store: CounterStore) -> None:
    log.info("[%s] counter poller starting", session.name)

    system_ids: list[str] = []
    poll_count = 0

    while True:
        # ── Refresh system list periodically ──────────────────────────────────
        if poll_count == 0 or poll_count % SYSTEM_REFRESH_POLLS == 0:
            new_ids = await _discover_system_ids(session)
            if new_ids:
                added = set(new_ids) - set(system_ids)
                if added:
                    log.info("[%s] counter poller: discovered %d new system(s): %s",
                             session.name, len(added), sorted(added))
                system_ids = new_ids
            elif not system_ids:
                log.warning("[%s] counter poller: no systems found, retrying in %ds",
                            session.name, COUNTER_POLL_INTERVAL_SECONDS)
                await asyncio.sleep(COUNTER_POLL_INTERVAL_SECONDS)
                continue

        # Wait before polling (including first iteration — avoids startup spike)
        await asyncio.sleep(COUNTER_POLL_INTERVAL_SECONDS)

        # ── Poll counters for every known system ──────────────────────────────
        polled_at = datetime.now(timezone.utc).isoformat()
        snapshot_count = 0
        for system_id in system_ids:
            try:
                raw = await live_data_client.get_interface_counters(session, system_id)
                items = raw.get("items", [])
                for item in items:
                    iface_name = item.get("interface_name")
                    if not iface_name:
                        continue
                    iface_id = counter_store.upsert_interface(
                        session.name, system_id, iface_name
                    )
                    counter_store.insert_snapshot(iface_id, polled_at, item)
                    snapshot_count += 1
            except Exception as exc:
                log.debug("[%s] counter poll failed for %s: %s",
                          session.name, system_id, exc)

        if snapshot_count:
            log.debug("[%s] counter poller: wrote %d snapshots across %d systems",
                      session.name, snapshot_count, len(system_ids))

        poll_count += 1

        # ── Periodic prune ────────────────────────────────────────────────────
        if poll_count % PRUNE_INTERVAL_POLLS == 0:
            try:
                counter_store.prune()
            except Exception as exc:
                log.warning("[%s] counter_store prune failed: %s", session.name, exc)


# ── System discovery ──────────────────────────────────────────────────────────

async def _discover_system_ids(session) -> list[str]:
    """
    Returns a list of unique system_id values (hardware chassis serials) for
    all systems managed by this Apstra controller instance.

    Skips systems with a null or empty system_id (not yet assigned to a
    chassis, or devices whose telemetry is not yet active).
    """
    try:
        raw = await live_data_client.get_all_systems(session)
        ids = [
            item["system_id"]
            for item in raw.get("items", [])
            if item.get("system_id")
        ]
        log.debug("[%s] counter poller: discovered %d systems", session.name, len(ids))
        return ids
    except Exception as exc:
        log.warning("[%s] counter poller: system discovery failed: %s",
                    session.name, exc)
        return []
