from datetime import datetime, timezone, timedelta
from typing import Annotated

from fastmcp import Context
from pydantic import Field


def register(mcp):
    @mcp.tool()
    async def get_anomaly_events(
        blueprint_id: Annotated[str, "Apstra blueprint ID."],
        hours_back: Annotated[
            int,
            Field(default=24, description="How far back to look (1–168 hours). Default 24.", ge=1, le=168),
        ] = 24,
        anomaly_type: Annotated[
            str | None,
            Field(default=None, description="Filter to one type: bgp, cabling, interface, route, lag, mac, probe, deployment, liveness, config."),
        ] = None,
        device: Annotated[
            str | None,
            Field(default=None, description="Filter by device hostname (e.g. 'Leaf1')."),
        ] = None,
        raised_only: Annotated[
            bool,
            Field(default=False, description="If True, return only raise events (no clears)."),
        ] = False,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Query the local anomaly time-series store for raise and clear events.

        Use this to see exactly when anomalies appeared and were resolved, trace the
        sequence of events during an incident, or verify that a remediation actually
        cleared a specific anomaly. The store is backfilled with 7 days of history at
        startup and updated every 60 s — no API calls to Apstra on each invocation.
        Filter by anomaly_type and device to narrow to a specific fault. Set raised_only=True
        to see only fault-onset events without the corresponding clears.

        Each event includes: timestamp, raised (true = new anomaly, false = anomaly cleared),
        anomaly_type (bgp/cabling/interface/route/lag/mac/probe/deployment/liveness/config),
        device (hostname), expected (Apstra's intent), actual (observed state), identity
        (dict uniquely identifying this anomaly instance), first_detected, source.
        Returns newest-first, up to 500 events. Data source: local SQLite store.
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available — server may still be starting up"}

        hours_back = max(1, min(hours_back, 168))
        since = (
            datetime.now(timezone.utc) - timedelta(hours=hours_back)
        ).isoformat()

        events = store.query_events(
            blueprint_id=blueprint_id,
            instance_name=instance_name,
            anomaly_type=anomaly_type,
            device=device,
            since=since,
            raised_only=raised_only,
            limit=500,
        )

        return {
            "blueprint_id": blueprint_id,
            "hours_back":   hours_back,
            "filters": {
                "anomaly_type": anomaly_type,
                "device":       device,
                "raised_only":  raised_only,
                "instance":     instance_name,
            },
            "event_count": len(events),
            "events":      events,
            "_meta": {
                "data_source": "local_anomaly_store",
                "note": "Store is backfilled with 7 days of history at startup and updated every 60 s",
            },
        }

    @mcp.tool()
    async def get_anomaly_summary(
        blueprint_id: Annotated[str, "Apstra blueprint ID."],
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return coverage and count statistics for the local anomaly store for a blueprint.

        Use this before querying anomaly data to confirm data is available and understand
        how much history is loaded. Shows whether the initial 7-day backfill has completed,
        how many raise and clear events are stored per anomaly type, and how many anomalies
        are currently active according to the store.

        Returns: backfill_ready (bool), last_poll_at, type_counts (raises/clears per
        anomaly type), active_count (anomalies currently raised), oldest_event, newest_event.
        Data source: local SQLite store (updated every 60 s by background poller).
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available"}

        sessions   = ctx.lifespan_context["sessions"]
        target_sessions = [s for s in sessions if instance_name is None or s.name == instance_name]

        summaries = []
        for session in target_sessions:
            summary = store.get_summary(blueprint_id)
            ps = store.get_poll_state(blueprint_id, session.name)
            summaries.append({
                "instance":         session.name,
                "backfill_ready":   ps.get("backfill_complete", False),
                "last_poll_at":     ps.get("last_poll_at"),
                **summary,
            })

        if len(summaries) == 1:
            return {
                "blueprint_id": blueprint_id,
                **summaries[0],
                "_meta": {"data_source": "local_anomaly_store"},
            }
        return {
            "blueprint_id": blueprint_id,
            "instances":    summaries,
            "_meta":        {"data_source": "local_anomaly_store"},
        }

    @mcp.tool()
    async def get_active_anomalies_from_store(
        blueprint_id: Annotated[str, "Apstra blueprint ID."],
        anomaly_type: Annotated[
            str | None,
            Field(default=None, description="Filter by anomaly type (e.g. 'bgp', 'cabling', 'interface')."),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return currently active anomalies from the local store with zero Apstra API calls.

        Use this as a fast alternative to get_current_anomalies when you also need
        first_detected and last_event_at timestamps (which the live API does not provide),
        or when API latency is a concern. An anomaly is active when its most recent store
        event is a raise with no subsequent clear.

        Each anomaly includes: anomaly_type, device, expected, actual, identity (unique key),
        first_detected, last_event_at.
        Data source: local SQLite store (updated every 60 s by background poller).
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available"}

        active = store.get_currently_active(blueprint_id, instance_name)

        if anomaly_type:
            active = [a for a in active if a["anomaly_type"] == anomaly_type]

        from collections import Counter
        by_type = dict(Counter(a["anomaly_type"] for a in active))

        return {
            "blueprint_id":  blueprint_id,
            "active_count":  len(active),
            "by_type":       by_type,
            "anomalies":     active,
            "_meta": {
                "data_source": "local_anomaly_store",
                "note": "State reflects last successful store update (≤60 s ago)",
            },
        }
