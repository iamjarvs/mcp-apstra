"""
tools/telemetry.py

MCP tools for device telemetry data queried directly from the Apstra API.

  get_interface_counters    — raw error/traffic counters per interface (live)
  get_interface_utilisation — utilisation % and error rates from IBA probe (live)
  get_system_telemetry      — CPU and memory per device (live)
  get_interface_error_trend — time-series of error growth for one interface (db)
  get_top_error_growers     — which interfaces are accumulating errors fastest (db)

Live tools query the Apstra controller on every call.
Trend tools query the local CounterStore, which the counter_poller populates
every 5 minutes with snapshots from every managed system.

Use get_systems to discover valid system_id values — it is the `system_id`
field on each system object (e.g. "5254002D005F"), NOT the graph node `id`.
"""

from typing import Annotated

import httpx
from fastmcp import Context
from pydantic import Field

from primitives import live_data_client


def register(mcp):

    # ── Tool 1: get_interface_counters ────────────────────────────────────────

    @mcp.tool()
    async def get_interface_counters(
        system_id: Annotated[
            str,
            "Hardware chassis serial (e.g. '5254002D005F'). Use the system_id field from get_systems — NOT the id field.",
        ],
        interface_name: Annotated[
            str | None,
            Field(default=None, description="Filter to one interface name (e.g. 'ge-0/0/1'). Omit to return all interfaces."),
        ] = None,
        errors_only: Annotated[
            bool,
            Field(default=False, description="If True, return only interfaces with at least one non-zero error counter (fcs_errors, alignment_errors, symbol_errors, rx/tx_error_packets, runts, giants)."),
        ] = False,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return the latest raw cumulative interface counters polled from a device.

        NOTE: This tool takes system_id (hardware serial) only — it does NOT take a
        blueprint_id parameter. The system_id uniquely identifies the device across all
        blueprints. Use get_systems to discover the system_id for a named device.

        Use this to check whether an interface has error activity at all (set errors_only=True
        for a fast scan across all ports), inspect exact byte and packet counts, or confirm
        there are no FCS, alignment, or discard errors. These are cumulative totals since
        last device reset, not per-second rates — use get_interface_utilisation for rates
        and utilisation percentages. Use get_interface_error_trend to see how errors have
        grown over time.

        Each interface includes: interface_name, rx/tx_bytes, rx/tx unicast/broadcast/
        multicast packets, fcs_errors, alignment_errors, symbol_errors, runts, giants,
        rx/tx_error_packets, rx/tx_discard_packets, has_errors (bool), last_fetched_at.
        Data source: live Apstra API → device streaming telemetry (30–120 s behind real-time).
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'", "hint": "Do not set instance_name — leave as None to query all instances automatically."}

        session = target[0]
        raw = await live_data_client.get_interface_counters(session, system_id)
        items = raw.get("items", [])

        if interface_name:
            items = [i for i in items if i.get("interface_name") == interface_name]

        ERROR_FIELDS = {
            "fcs_errors", "alignment_errors", "symbol_errors",
            "rx_error_packets", "tx_error_packets", "runts", "giants",
        }
        if errors_only:
            items = [
                i for i in items
                if any(i.get(f, 0) > 0 for f in ERROR_FIELDS)
            ]

        # Tag each item with a has_errors flag for easy LLM scanning
        result_items = []
        for i in items:
            has_errors = any(i.get(f, 0) > 0 for f in ERROR_FIELDS)
            result_items.append({**i, "has_errors": has_errors})

        error_count = sum(1 for i in result_items if i["has_errors"])

        return {
            "system_id":      system_id,
            "instance":       session.name,
            "interface_count": len(result_items),
            "interfaces_with_errors": error_count,
            "delta_microseconds": raw.get("delta_microseconds"),
            "filters": {"interface": interface_name, "errors_only": errors_only},
            "interfaces": result_items,
            "_meta": {
                "data_source": "live_apstra_api",
                "note": (
                    "Counters are cumulative since last device reset, not per-second rates. "
                    "Use get_interface_utilisation for rates and utilisation percentages."
                ),
            },
        }

    # ── Tool 2: get_interface_utilisation ─────────────────────────────────────

    @mcp.tool()
    async def get_interface_utilisation(
        blueprint_id: Annotated[
            str,
            Field(description=(
                "Required. Apstra blueprint ID or partial label (e.g. 'DC1'). "
                "IBA probes are per-blueprint — a blueprint_id is required. "
                "Use get_blueprints to list available blueprints and their IDs."
            )),
        ],
        system_id: Annotated[
            str | None,
            Field(default=None, description="Hardware chassis serial (e.g. '5254002D005F'). Use system_id from get_systems. Omit for all devices."),
        ] = None,
        interface_name: Annotated[
            str | None,
            Field(default=None, description="Filter to one interface name (e.g. 'ge-0/0/1'). Omit for all interfaces."),
        ] = None,
        top_n: Annotated[
            int,
            Field(default=10, description="Return only the top N busiest interfaces by utilisation. Set to 0 for all. Default 10."),
        ] = 10,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return interface utilisation percentages and per-second error rates from the
        Apstra IBA 'Device Traffic' probe.

        Use this for throughput and utilisation questions: which ports are busiest, are uplinks
        balanced, are there persistent discard or error rates. Values are rolling averages
        (~120 s window), not cumulative totals — use get_interface_counters for raw totals and
        get_interface_error_trend for historical error growth.

        Results sorted by max(tx_util, rx_util) descending so busiest ports appear first.

        Returns: interfaces (list with system_id, interface, role, speed_bps, tx_util_pct,
        rx_util_pct, max_util_pct, tx_bps, rx_bps, tx_error_pps, rx_error_pps,
        tx_discard_pps, rx_discard_pps, fcs_errors_pps), interface_count.
        Data source: Apstra IBA probe 'Device Traffic' / stage 'Average Interface Counters' (live).
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'", "hint": "Do not set instance_name — leave as None to query all instances automatically."}

        session = target[0]

        # Find the "Device Traffic" probe by label
        try:
            probes_raw = await live_data_client.get_probes(session, blueprint_id)
        except (httpx.RemoteProtocolError, httpx.ConnectError,
                httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            return {
                "error": "Apstra API connection failed while fetching probe list.",
                "detail": str(exc),
                "blueprint_id": blueprint_id,
                "instance": session.name,
            }
        probes = probes_raw.get("items", [])
        traffic_probe = next(
            (p for p in probes if p.get("label") == "Device Traffic"),
            None,
        )
        if traffic_probe is None:
            return {
                "error": "IBA probe 'Device Traffic' not found in this blueprint.",
                "available_probes": [p.get("label") for p in probes],
            }

        probe_id = traffic_probe["id"]
        try:
            raw = await live_data_client.query_probe_stage(
                session, blueprint_id, probe_id,
                stage="Average Interface Counters",
            )
        except (httpx.RemoteProtocolError, httpx.ConnectError,
                httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            return {
                "error": "Apstra API connection failed while querying IBA probe data.",
                "detail": str(exc),
                "blueprint_id": blueprint_id,
                "instance": session.name,
            }
        items = raw.get("items", [])

        # Filter
        if system_id:
            items = [i for i in items if i.get("properties", {}).get("system_id") == system_id]
        if interface_name:
            items = [i for i in items if i.get("properties", {}).get("interface") == interface_name]

        # Flatten and annotate
        result_items = []
        for i in items:
            props = i.get("properties", {})
            tx_util = i.get("tx_utilization_average", 0.0) or 0.0
            rx_util = i.get("rx_utilization_average", 0.0) or 0.0
            result_items.append({
                "system_id":   props.get("system_id"),
                "interface":   props.get("interface"),
                "role":        props.get("link_role") or props.get("role"),
                "speed_bps":   props.get("speed"),
                "tx_util_pct": round(tx_util * 100, 4),
                "rx_util_pct": round(rx_util * 100, 4),
                "max_util_pct": round(max(tx_util, rx_util) * 100, 4),
                "tx_bps":      i.get("tx_bps_average", 0),
                "rx_bps":      i.get("rx_bps_average", 0),
                "tx_error_pps":   i.get("tx_error_pps_average", 0),
                "rx_error_pps":   i.get("rx_error_pps_average", 0),
                "tx_discard_pps": i.get("tx_discard_pps_average", 0),
                "rx_discard_pps": i.get("rx_discard_pps_average", 0),
                "fcs_errors_pps": i.get("fcs_errors_per_second_average", 0),
                "timestamp":   i.get("timestamp"),
            })

        # Sort by max utilisation descending
        result_items.sort(key=lambda x: x["max_util_pct"], reverse=True)

        if top_n and top_n > 0:
            result_items = result_items[:top_n]

        return {
            "blueprint_id":  blueprint_id,
            "instance":      session.name,
            "probe":         "Device Traffic / Average Interface Counters",
            "interface_count": len(result_items),
            "filters": {
                "system_id":     system_id,
                "interface_name": interface_name,
                "top_n":         top_n,
            },
            "interfaces": result_items,
            "_meta": {
                "data_source": "live_apstra_iba_probe",
                "note": (
                    "Values are rolling averages over the probe sampling period (~120 s). "
                    "tx_util_pct / rx_util_pct are percentages of link capacity (0–100). "
                    "Results sorted by max(tx, rx) utilisation descending."
                ),
            },
        }

    # ── Tool 3: get_system_telemetry ──────────────────────────────────────────

    @mcp.tool()
    async def get_system_telemetry(
        system_ids: Annotated[
            list[str],
            Field(description=(
                "One or more hardware chassis serials (e.g. ['5254002D005F', '525400F8CE53']). "
                "Use the system_id field from get_systems — NOT the id field."
            )),
        ],
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return the latest CPU and memory utilisation for one or more devices from Apstra streaming telemetry.

        Use this when asked about device resource health — whether a device is under CPU or memory
        pressure, or to compare utilisation across a set of devices (e.g. all spines). Use
        get_systems to discover system_id values for devices by hostname.

        Returns: devices (list with system_id, cpu_pct, memory_pct, last_fetched_at), sorted by
        cpu_pct descending. device_count, errors (list of failed lookups).
        Data source: live Apstra API → device streaming telemetry (30–120 s behind real-time).
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'", "hint": "Do not set instance_name — leave as None to query all instances automatically."}

        session = target[0]

        results = []
        errors = []
        for sid in system_ids:
            try:
                raw = await live_data_client.get_system_resource_util(session, sid)
                items = raw.get("items", [])
                cpu = next(
                    (int(i["actual"]["value"]) for i in items
                     if i.get("key") == "system_cpu_utilization"),
                    None,
                )
                mem = next(
                    (int(i["actual"]["value"]) for i in items
                     if i.get("key") == "system_memory_utilization"),
                    None,
                )
                last_fetched = next(
                    (i.get("last_fetched_at") for i in items if i.get("last_fetched_at")),
                    None,
                )
                results.append({
                    "system_id":      sid,
                    "cpu_pct":        cpu,
                    "memory_pct":     mem,
                    "last_fetched_at": last_fetched,
                })
            except Exception as exc:
                errors.append({"system_id": sid, "error": str(exc)})

        # Sort by cpu_pct descending (None last) so highest-load devices appear first
        results.sort(key=lambda r: (r["cpu_pct"] is None, -(r["cpu_pct"] or 0)))

        return {
            "instance":      session.name,
            "device_count":  len(results),
            "devices":       results,
            "errors":        errors,
            "_meta": {
                "data_source": "live_apstra_api",
                "note": "Values are polled from device streaming telemetry, typically 30–120 s behind real-time.",
            },
        }

    # ── Tool 4: get_interface_error_trend ─────────────────────────────────────

    @mcp.tool()
    async def get_interface_error_trend(
        system_id: Annotated[
            str,
            Field(description="Hardware chassis serial (e.g. '5254002D005F'). Use the system_id field from get_systems."),
        ],
        interface_name: Annotated[
            str,
            Field(description="Exact interface name (e.g. 'ge-0/0/1'). Use get_interface_counters to list available interfaces for a device."),
        ],
        hours_back: Annotated[
            int,
            Field(default=24, description="Look-back window in hours (1–168). Default 24.", ge=1, le=168),
        ] = 24,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return a time-series of error counter growth for a single interface, sampled every 5 minutes.

        Use this to detect creeping physical-layer degradation — an interface where FCS or CRC
        errors are slowly accumulating over hours, indicating a failing cable or SFP. Each data
        point is the *delta* between consecutive snapshots (not a cumulative total). has_reset=True
        flags counter wraps or device reboots. Use get_top_error_growers first to find which
        interfaces warrant investigation.

        Returns: trend (list with polled_at, interval_seconds, fcs_errors, alignment_errors,
        symbol_errors, rx/tx_error_packets, runts, giants, rx/tx_discard_packets, rx/tx_bytes,
        total_errors, has_reset), total_errors, max_errors_in_interval, has_any_errors,
        data_point_count.
        Data source: local counter_store (5-min snapshots; needs ≥2 snapshots to compute deltas).
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'", "hint": "Do not set instance_name — leave as None to query all instances automatically."}

        session = target[0]
        counter_store = ctx.lifespan_context["counter_store"]
        hours_back = max(1, min(hours_back, 168))

        trend = counter_store.get_error_trend(
            session.name, system_id, interface_name, hours_back=hours_back
        )

        coverage = counter_store.get_coverage_summary(session.name)
        total_errors = sum(row["total_errors"] for row in trend)
        max_interval_errors = max((row["total_errors"] for row in trend), default=0)

        return {
            "system_id":              system_id,
            "interface_name":         interface_name,
            "instance":               session.name,
            "hours_back":             hours_back,
            "data_point_count":       len(trend),
            "total_errors":           total_errors,
            "max_errors_in_interval": max_interval_errors,
            "has_any_errors":         total_errors > 0,
            "trend":                  trend,
            "_meta": {
                "data_source": "local_counter_store",
                "poll_interval_seconds": 300,
                "oldest_data": coverage.get("oldest_snapshot"),
                "newest_data": coverage.get("newest_snapshot"),
                "note": (
                    "Values are deltas between consecutive 5-min snapshots. "
                    "Empty trend means insufficient data — the counter poller "
                    "needs at least 2 snapshots (~10 min after server start)."
                ),
            },
        }

    # ── Tool 5: get_top_error_growers ─────────────────────────────────────────

    @mcp.tool()
    async def get_top_error_growers(
        hours_back: Annotated[
            int,
            Field(default=24, description="Look-back window (1–168 hours). Default 24.", ge=1, le=168),
        ] = 24,
        top_n: Annotated[
            int,
            Field(default=20, description="Maximum interfaces to return, ranked by total_errors. Default 20."),
        ] = 20,
        blueprint_id: Annotated[
            str | None,
            Field(default=None, description=(
                "Restrict results to switches in this blueprint. "
                "Pass a partial label (e.g. 'DC1'), full UUID, or null for all systems."
            )),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return the interfaces accumulating the most error counter growth over a time window, ranked worst-first.

        Use this as a first-pass scan to find interfaces with degrading physical layer health — FCS,
        alignment, symbol errors, or discards growing over time. Only interfaces with at least one
        error are returned. Drill into a specific interface with get_interface_error_trend to see the
        per-interval breakdown.

        Returns: interfaces (list with system_id, interface_name, snapshot_count, total_fcs_errors,
        total_alignment_errors, total_symbol_errors, total_rx/tx_error_packets, total_runts,
        total_giants, total_discards, total_errors, error_rate_per_hour, reset_count, has_any_errors),
        sorted by total_errors descending.
        Data source: local counter_store (5-min snapshots by counter_poller).
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'", "hint": "Do not set instance_name — leave as None to query all instances automatically."}

        session = target[0]
        counter_store = ctx.lifespan_context["counter_store"]
        hours_back = max(1, min(hours_back, 168))

        # Optionally resolve blueprint → system_ids via graph registry
        system_ids: list[str] | None = None
        if blueprint_id:
            try:
                registry = ctx.lifespan_context["graph_registry"]
                _SYSTEMS_CYPHER = (
                    "MATCH (sw:system) "
                    "WHERE sw.system_type = 'switch' "
                    "RETURN sw.system_id"
                )
                graph = await registry.get_or_rebuild(session, blueprint_id)
                rows = graph.query(_SYSTEMS_CYPHER)
                system_ids = [
                    r["sw.system_id"] for r in rows
                    if r.get("sw.system_id")
                ]
            except Exception as exc:
                return {
                    "error": f"Failed to resolve systems for blueprint '{blueprint_id}': {exc}",
                    "hint": "Use get_blueprints to verify the blueprint_id is valid.",
                }

        results = counter_store.get_top_error_growers(
            instance_name=session.name,
            system_ids=system_ids,
            hours_back=hours_back,
            top_n=top_n,
        )

        coverage = counter_store.get_coverage_summary(session.name)
        error_count = sum(1 for r in results if r["has_any_errors"])

        return {
            "instance":               session.name,
            "blueprint_id":           blueprint_id,
            "hours_back":             hours_back,
            "top_n":                  top_n,
            "interface_count":        len(results),
            "interfaces_with_errors": error_count,
            "interfaces":             results,
            "_meta": {
                "data_source": "local_counter_store",
                "poll_interval_seconds": 300,
                "oldest_data": coverage.get("oldest_snapshot"),
                "newest_data": coverage.get("newest_snapshot"),
                "coverage_note": (
                    f"Store has {coverage.get('snapshot_count', 0)} snapshots "
                    f"across {coverage.get('interface_count', 0)} interfaces."
                ),
            },
        }
