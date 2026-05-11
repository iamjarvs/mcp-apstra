from datetime import datetime, timezone, timedelta
from typing import Annotated, Literal

from fastmcp import Context
from pydantic import Field

from handlers.blueprints import resolve_blueprints

_BP_DESC = (
    "Apstra blueprint ID, partial label, or null. "
    "Pass null or 'all' for every blueprint. "
    "Pass a partial name (e.g. 'DC1') to match by label. "
    "Pass a full UUID for a specific blueprint."
)


def register(mcp):
    @mcp.tool()
    async def get_anomaly_events(
        blueprint_id: Annotated[str | None, Field(default=None, description=_BP_DESC)] = None,
        hours_back: Annotated[
            int,
            Field(default=168, description="How far back to look in hours (>=1). Default 168 (7 days). Ignored if days_back is supplied.", ge=1),
        ] = 168,
        days_back: Annotated[
            int | None,
            Field(default=None, description="How far back to look in days (>=1). Converts to hours and takes precedence over hours_back.", ge=1),
        ] = None,
        anomaly_type: Annotated[
            str | None,
            Field(default=None, description="Filter to one type: bgp, cabling, interface, route, lag, mac, probe, deployment, liveness, config."),
        ] = None,
        device: Annotated[
            str | None,
            Field(default=None, description="Filter by device hostname (e.g. 'Leaf1')."),
        ] = None,
        from_time: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Start of query window as an ISO-8601 UTC timestamp "
                    "(e.g. '2026-04-15T08:00:00Z'). "
                    "When provided, days_back/hours_back are ignored."
                ),
            ),
        ] = None,
        to_time: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "End of query window as an ISO-8601 UTC timestamp "
                    "(e.g. '2026-04-15T10:00:00Z'). "
                    "If omitted, defaults to now."
                ),
            ),
        ] = None,
        raised_only: Annotated[
            bool,
            Field(default=False, description="If True, return only raise events (no clears)."),
        ] = False,
        limit: Annotated[
            int,
            Field(
                default=50,
                ge=1,
                le=200,
                description=(
                    "Maximum number of events to return in this page. "
                    "Use next_cursor from the response to request older pages."
                ),
            ),
        ] = 50,
        cursor_timestamp: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Pagination cursor timestamp from next_cursor.cursor_timestamp "
                    "in a previous response. Must be used with cursor_event_id."
                ),
            ),
        ] = None,
        cursor_event_id: Annotated[
            int | None,
            Field(
                default=None,
                ge=1,
                description=(
                    "Pagination cursor event id from next_cursor.cursor_event_id "
                    "in a previous response. Must be used with cursor_timestamp."
                ),
            ),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Query the local anomaly time-series store for raise and clear events across one or all blueprints.

        Call get_anomaly_summary first to understand event volume, then use this tool for
        targeted drill-down with anomaly_type/device filters and pagination.

        Use this to see exactly when anomalies appeared and were resolved, trace the
        sequence of events during an incident, or verify that a remediation actually
        cleared a specific anomaly. The store is backfilled with 30 days of history at
        startup and updated every 60 s — no API calls to Apstra on each invocation.
        Filter by anomaly_type and device to narrow to a specific fault. Set raised_only=True
        to see only fault-onset events without the corresponding clears.

        Pass blueprint_id=null to query all blueprints at once.

        Each event includes: timestamp, raised (true = new anomaly, false = anomaly cleared),
        anomaly_type, device (hostname), expected (Apstra's intent), actual (observed state),
        identity (dict uniquely identifying this anomaly instance), first_detected, source.
        Returns newest-first in bounded pages. If has_more=true, request the next page
        using next_cursor. Data source: local SQLite store.
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available — server may still be starting up", "hint": "Use get_current_anomalies for real-time data; the anomaly store is ready ~60 s after server start."}

        if (cursor_timestamp is None) != (cursor_event_id is None):
            return {
                "error": "cursor_timestamp and cursor_event_id must be provided together",
                "hint": "Use the next_cursor object returned by a previous get_anomaly_events call.",
            }

        blu_list = await resolve_blueprints(ctx.lifespan_context["sessions"], blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'", "hint": "Call get_blueprints to list available blueprints and their labels."}

        if len(blu_list) > 1:
            if cursor_timestamp is not None or cursor_event_id is not None:
                return {
                    "error": "Pagination across multiple blueprints is not supported with a shared cursor.",
                    "hint": "Set blueprint_id to a single blueprint before using cursor pagination.",
                }
            results = []
            for bp in blu_list:
                r = await get_anomaly_events(
                    blueprint_id=bp["id"], hours_back=hours_back, days_back=days_back,
                    anomaly_type=anomaly_type,
                    device=device, from_time=from_time, to_time=to_time,
                    raised_only=raised_only, limit=limit,
                    cursor_timestamp=cursor_timestamp, cursor_event_id=cursor_event_id,
                    instance_name=instance_name, ctx=ctx,
                )
                r["blueprint_label"] = bp["label"]
                results.append(r)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "results": results}

        blueprint_id = blu_list[0]["id"]
        now = datetime.now(timezone.utc)
        if from_time:
            since = from_time
            until = to_time if to_time else now.isoformat()
            window_label = f"{since} -> {until}"
        else:
            if days_back is not None:
                hours_back = max(1, days_back * 24)
            else:
                hours_back = max(1, hours_back)
            since = (now - timedelta(hours=hours_back)).isoformat()
            until = to_time if to_time else now.isoformat()
            window_label = f"last_{hours_back}h"

        page = store.query_events_page(
            blueprint_id=blueprint_id,
            instance_name=instance_name,
            anomaly_type=anomaly_type,
            device=device,
            since=since,
            until=until,
            raised_only=raised_only,
            limit=limit,
            cursor_timestamp=cursor_timestamp,
            cursor_event_id=cursor_event_id,
        )

        return {
            "blueprint_id":  blueprint_id,
            "blueprint_label": blu_list[0]["label"],
            "time_window":   window_label,
            "since":         since,
            "until":         until,
            "filters": {
                "anomaly_type": anomaly_type,
                "device":       device,
                "raised_only":  raised_only,
                "instance":     instance_name,
            },
            "event_count": page["returned_count"],
            "events":      page["events"],
            "pagination": {
                "limit": limit,
                "has_more": page["has_more"],
                "next_cursor": page["next_cursor"],
                "hint": "If has_more is true, call get_anomaly_events again with next_cursor fields.",
            },
            "_meta": {
                "data_source": "local_anomaly_store",
                "note": "Store is backfilled with 30 days of history at startup and updated every 60 s",
                "recommended_flow": "Call get_anomaly_summary first, then drill down with filtered, paginated get_anomaly_events.",
            },
        }

    @mcp.tool()
    async def get_anomaly_summary(
        blueprint_id: Annotated[str | None, Field(default=None, description=_BP_DESC)] = None,
        time_window: Annotated[
            str,
            Field(
                default="1-day",
                description=(
                    "Time window for event counts. Preferred values: "
                    "1-hour, 1-day, 7-day, 14-day, 30-day. "
                    "Backward-compatible aliases: 1hr, 24hr, 7d."
                ),
            ),
        ] = "1-day",
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return event counts and per-type breakdown for the local anomaly store over a
        specific time window.

        Use this before querying anomaly data to confirm data is available and understand
        how much activity occurred in the chosen window. Shows how many raise and clear
        events fired per anomaly type, and how many anomalies are currently active
        (always reflects present state, independent of the chosen window).

        Preferred time window options: 1-hour, 1-day, 7-day, 14-day, 30-day.

        Returns: time_window, since (ISO timestamp), currently_active, events_in_window,
        window_oldest, window_newest, by_type (raises/clears/identities per anomaly type),
        backfill_ready, last_poll_at.
        Data source: local SQLite store (updated every 60 s by background poller).
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available", "hint": "The anomaly store populates ~60 s after server start. Use get_current_anomalies for real-time data in the meantime."}

        blu_list = await resolve_blueprints(ctx.lifespan_context["sessions"], blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'", "hint": "Call get_blueprints to list available blueprints and their labels."}

        if len(blu_list) > 1:
            results = []
            for bp in blu_list:
                r = await get_anomaly_summary(
                    blueprint_id=bp["id"], time_window=time_window,
                    instance_name=instance_name, ctx=ctx,
                )
                r["blueprint_label"] = bp["label"]
                results.append(r)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "results": results}

        blueprint_id = blu_list[0]["id"]
        _window_to_delta = {
            "1-hour": timedelta(hours=1),
            "1-day":  timedelta(days=1),
            "7-day":  timedelta(days=7),
            "14-day": timedelta(days=14),
            "30-day": timedelta(days=30),
            # Backward-compatible aliases
            "1hr":    timedelta(hours=1),
            "24hr":   timedelta(days=1),
            "7d":     timedelta(days=7),
        }
        if time_window not in _window_to_delta:
            return {
                "error": f"Unsupported time_window '{time_window}'",
                "supported_values": ["1-hour", "1-day", "7-day", "14-day", "30-day"],
                "hint": "Use one of the supported values above.",
            }

        canonical_time_window = {
            "1hr": "1-hour",
            "24hr": "1-day",
            "7d": "7-day",
        }.get(time_window, time_window)

        since_dt = datetime.now(timezone.utc) - _window_to_delta[time_window]
        since_iso = since_dt.isoformat()

        sessions = ctx.lifespan_context["sessions"]
        target_sessions = [s for s in sessions if instance_name is None or s.name == instance_name]

        summaries = []
        for session in target_sessions:
            summary = store.get_summary(blueprint_id, since=since_iso)
            ps = store.get_poll_state(blueprint_id, session.name)
            summaries.append({
                "instance":       session.name,
                "backfill_ready": ps.get("backfill_complete", False),
                "last_poll_at":   ps.get("last_poll_at"),
                **summary,
            })

        base = {
            "blueprint_id":    blueprint_id,
            "blueprint_label": blu_list[0]["label"],
            "time_window":     canonical_time_window,
            "since":           since_iso,
        }

        if len(summaries) == 1:
            return {**base, **summaries[0], "_meta": {"data_source": "local_anomaly_store"}}
        return {
            **base,
            "instances": summaries,
            "_meta":     {"data_source": "local_anomaly_store"},
        }

    @mcp.tool()
    async def get_active_anomalies_from_store(
        blueprint_id: Annotated[str | None, Field(default=None, description=_BP_DESC)] = None,
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

        IMPORTANT — warm-up period: On a freshly started server the background poller
        has not yet run and the store will be empty (count=0) even if anomalies exist.
        If count=0 and the server was recently restarted, call get_current_anomalies
        instead to get the live count. The store is fully populated within ~60 seconds
        of server startup.

        Pass blueprint_id=null to query all blueprints at once.

        Each anomaly includes: anomaly_type, device, expected, actual, identity (unique key),
        first_detected, last_event_at.
        Data source: local SQLite store (updated every 60 s by background poller).
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available", "hint": "The anomaly store populates ~60 s after server start. Use get_current_anomalies for real-time data in the meantime."}

        blu_list = await resolve_blueprints(ctx.lifespan_context["sessions"], blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'", "hint": "Call get_blueprints to list available blueprints and their labels."}

        if len(blu_list) > 1:
            results = []
            for bp in blu_list:
                r = await get_active_anomalies_from_store(
                    blueprint_id=bp["id"], anomaly_type=anomaly_type,
                    instance_name=instance_name, ctx=ctx,
                )
                r["blueprint_label"] = bp["label"]
                results.append(r)
            total_active = sum(r.get("active_count", 0) for r in results)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "total_active": total_active, "results": results}

        blueprint_id = blu_list[0]["id"]
        active = store.get_currently_active(blueprint_id, instance_name)

        if anomaly_type:
            active = [a for a in active if a["anomaly_type"] == anomaly_type]

        from collections import Counter
        by_type = dict(Counter(a["anomaly_type"] for a in active))

        return {
            "blueprint_id":    blueprint_id,
            "blueprint_label": blu_list[0]["label"],
            "active_count":    len(active),
            "by_type":       by_type,
            "anomalies":     active,
            "_meta": {
                "data_source": "local_anomaly_store",
                "note": "State reflects last successful store update (≤60 s ago)",
            },
        }

    @mcp.tool()
    async def get_device_anomaly_history(
        blueprint_id: Annotated[str | None, Field(default=None, description=_BP_DESC)] = None,
        device_label: Annotated[
            str,
            Field(
                default=None,
                description=(
                    "Device hostname/label exactly as shown in Apstra (e.g. 'Leaf1', 'Spine2'). "
                    "Use the 'label' field from get_systems. "
                    "Do NOT pass system_id (hardware serial) here — this parameter expects a hostname. "
                    "Required."
                ),
            ),
        ] = None,
        from_time: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Start of the query window as an ISO-8601 UTC timestamp "
                    "(e.g. '2026-04-15T08:00:00Z'). "
                    "When provided, time_window is ignored. "
                    "If only from_time is given, to_time defaults to now."
                ),
            ),
        ] = None,
        to_time: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "End of the query window as an ISO-8601 UTC timestamp "
                    "(e.g. '2026-04-15T10:00:00Z'). "
                    "Only used when from_time is also provided. Defaults to now."
                ),
            ),
        ] = None,
        days_back: Annotated[
            int,
            Field(
                default=7,
                ge=1,
                description=(
                    "Rolling look-back window in days used when from_time is NOT supplied. "
                    "Default 7 days."
                ),
            ),
        ] = 7,
        time_window: Annotated[
            Literal["5min", "30min", "1hr", "12hr", "24hr", "7d"] | None,
            Field(
                default="24hr",
                description=(
                    "Backward-compatible alias window used when from_time is NOT supplied "
                    "and days_back is not explicitly provided. Prefer days_back for user-defined lookback. "
                    "Allowed aliases: 5min, 30min, 1hr, 12hr, 24hr, 7d."
                ),
            ),
        ] = None,
        anomaly_type: Annotated[
            str | None,
            Field(
                default=None,
                description="Narrow to one anomaly type: bgp, cabling, interface, route, lag, mac, probe, deployment, liveness, config.",
            ),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return the complete anomaly history for a specific device over a chosen time window,
        with full raise-and-clear lifecycle detail for every anomaly.

        Use this when you need to understand everything that happened on a device during an
        incident window, a maintenance period, or across a user-specified time range.

        Time selection:
          - Supply from_time (and optionally to_time) for an exact window.
            Both must be ISO-8601 UTC strings, e.g. "2026-04-15T08:00:00Z".
            If to_time is omitted the window ends at the current time.
                    - If from_time is not given, days_back is used (default: 7 days).
                    - time_window remains as a backward-compatible alias.

        Results are grouped by anomaly identity so you see one entry per distinct fault
        rather than a raw flat event list. Each entry shows:
          - anomaly_type, role: what kind of fault and which device role
          - identity: the unique key Apstra uses to distinguish this anomaly instance
            (e.g. which BGP session, which interface, which route prefix)
          - expected: Apstra's intent for this element
          - first_detected: when this anomaly identity first appeared in the store
          - currently_active: whether this anomaly is still raised right now
          - raise_count, clear_count: how many times it toggled in the window
          - events: full chronological list of all raise/clear transitions, each with:
              timestamp, raised (true=fault appeared, false=fault cleared),
              actual (the observed state at that moment), source

        Results are ordered newest-first by the most recent event for each anomaly.
        No event count limit is applied — all events in the window are returned.
        Data source: local SQLite store (updated every 60 s by background poller).
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available", "hint": "The anomaly store populates ~60 s after server start. Use get_current_anomalies for real-time data in the meantime."}

        blu_list = await resolve_blueprints(ctx.lifespan_context["sessions"], blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'", "hint": "Call get_blueprints to list available blueprints and their labels."}

        if len(blu_list) > 1:
            results = []
            for bp in blu_list:
                r = await get_device_anomaly_history(
                    blueprint_id=bp["id"], device_label=device_label, from_time=from_time,
                    to_time=to_time, time_window=time_window, anomaly_type=anomaly_type,
                    instance_name=instance_name, ctx=ctx,
                )
                r["blueprint_label"] = bp["label"]
                results.append(r)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "results": results}

        blueprint_id = blu_list[0]["id"]

        _window_to_delta = {
            "5min":  timedelta(minutes=5),
            "30min": timedelta(minutes=30),
            "1hr":   timedelta(hours=1),
            "12hr":  timedelta(hours=12),
            "24hr":  timedelta(hours=24),
            "7d":    timedelta(days=7),
        }

        now = datetime.now(timezone.utc)

        if from_time:
            since_iso = from_time
            until_iso = to_time if to_time else now.isoformat()
            window_label = f"{from_time} → {until_iso}"
        else:
            if time_window:
                window = time_window
                since_iso = (now - _window_to_delta[window]).isoformat()
                window_label = window
            else:
                since_iso = (now - timedelta(days=max(1, days_back))).isoformat()
                window_label = f"{max(1, days_back)}d"
            until_iso = now.isoformat()

        # Fetch all events — no limit because caller asked for full history
        events = store.query_events(
            blueprint_id=blueprint_id,
            instance_name=instance_name,
            anomaly_type=anomaly_type,
            device=device_label,
            since=since_iso,
            until=until_iso,
            raised_only=False,
            limit=10_000,
        )

        # Determine which anomaly identities are currently active
        currently_active_set = {
            str(a["identity"])
            for a in store.get_currently_active(blueprint_id, instance_name)
            if a.get("device") == device_label
        }

        # Group events by identity key (JSON string of identity dict)
        import json as _json
        from collections import defaultdict

        grouped: dict[str, dict] = {}
        order: list[str] = []  # insertion order = most-recent-event first

        for ev in events:
            key = _json.dumps(ev["identity"], sort_keys=True)
            if key not in grouped:
                grouped[key] = {
                    "anomaly_type":     ev["anomaly_type"],
                    "role":             ev["role"],
                    "identity":         ev["identity"],
                    "expected":         ev["expected"],
                    "first_detected":   ev["first_detected"],
                    "currently_active": key in currently_active_set or str(ev["identity"]) in currently_active_set,
                    "raise_count":      0,
                    "clear_count":      0,
                    "events":           [],
                }
                order.append(key)
            entry = grouped[key]
            if ev["raised"]:
                entry["raise_count"] += 1
            else:
                entry["clear_count"] += 1
            entry["events"].append({
                "timestamp": ev["timestamp"],
                "raised":    ev["raised"],
                "actual":    ev["actual"],
                "source":    ev["source"],
            })

        # Reverse events within each group so they read oldest → newest (easier to follow)
        anomalies = []
        for key in order:
            entry = grouped[key]
            entry["events"] = list(reversed(entry["events"]))
            anomalies.append(entry)

        return {
            "blueprint_id":    blueprint_id,
            "blueprint_label": blu_list[0]["label"],
            "device":          device_label,
            "time_window":     window_label,
            "since":           since_iso,
            "until":           until_iso,
            "anomaly_count":   len(anomalies),
            "total_events":    len(events),
            "anomalies":      anomalies,
            "_meta": {
                "data_source": "local_anomaly_store",
                "note": "Events grouped by anomaly identity. Each entry shows the full raise/clear lifecycle within the window.",
            },
        }
