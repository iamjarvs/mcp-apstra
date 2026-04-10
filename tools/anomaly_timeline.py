"""
tools/anomaly_timeline.py

MCP tools that query the rolling anomaly time-series store.

The store is populated by the anomaly_poller background task that starts at
server startup.  Three tools are exposed:

  get_anomaly_events   — query raise/clear events with optional filters
  get_anomaly_summary  — counts and coverage metadata for a blueprint
  get_active_anomalies_from_store — current state from the local store
                                    (same intent as get_current_anomalies but
                                     cached — zero additional API calls)
"""

from datetime import datetime, timezone, timedelta

from fastmcp import Context


def register(mcp):
    @mcp.tool()
    async def get_anomaly_events(
        blueprint_id: str,
        hours_back: int = 24,
        anomaly_type: str = None,
        device: str = None,
        raised_only: bool = False,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Query the local anomaly time-series store for raise and clear events.

        The store is built from 7 days of Apstra history at server startup
        and updated every 60 seconds, so results are available instantly with
        no additional API calls to Apstra.

        Parameters
        ----------
        blueprint_id   : ID of the blueprint to query.
        hours_back     : How far back to look (1–168).  Default 24 hours.
        anomaly_type   : Filter to a single type: bgp, cabling, interface,
                         route, lag, mac, probe, deployment, liveness, config.
        device         : Filter by device hostname (e.g. "Leaf1").
        raised_only    : If True, return only raise events (no clears).
        instance_name  : Target a specific Apstra instance.

        Returns
        -------
        A list of events, newest first.  Each event includes:
          timestamp     — when the raise or clear was recorded
          raised        — true = anomaly raised, false = anomaly cleared
          anomaly_type  — e.g. "bgp", "interface"
          device        — hostname of the affected device
          expected      — what Apstra expects (the intent)
          actual        — what was observed
          identity      — full identity dict (uniquely identifies this anomaly)
          first_detected — when this anomaly was first ever seen
          source        — "trace_backfill", "trace_incremental", or
                         "snapshot_diff" (how the event was captured)

        Data source: local SQLite time-series store (anomaly_timeseries.db)
        Updated: every 60 seconds by background poller
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
        blueprint_id: str,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns coverage and count statistics for the anomaly time-series store
        for a given blueprint.

        Shows how much history is available, which anomaly types have been seen,
        how many raise and clear events are stored per type, and how many
        anomalies are currently active according to the local store.

        Also reports whether the initial 7-day backfill has completed for this
        blueprint.

        Data source: local SQLite time-series store (anomaly_timeseries.db)
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
        blueprint_id: str,
        anomaly_type: str = None,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns anomalies that are currently active according to the local
        time-series store — those whose most recent event is a raise with no
        subsequent clear.

        Equivalent in intent to get_current_anomalies but served from the
        local SQLite store (zero API calls to Apstra, instantaneous response).
        Additionally returns first_detected and last_event_at timestamps that
        the live API does not provide.

        Use get_current_anomalies for the definitive real-time view, and this
        tool when you need historical context alongside the current state.

        Parameters
        ----------
        blueprint_id  : ID of the blueprint to query.
        anomaly_type  : Optional filter by type.
        instance_name : Target a specific Apstra instance.

        Data source: local SQLite time-series store (anomaly_timeseries.db)
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
