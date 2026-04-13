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

from fastmcp import Context

from primitives import live_data_client


def register(mcp):

    # ── Tool 1: get_interface_counters ────────────────────────────────────────

    @mcp.tool()
    async def get_interface_counters(
        system_id: str,
        interface: str = None,
        errors_only: bool = False,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns the latest raw interface counters polled from a specific device
        by Apstra's streaming telemetry infrastructure.

        Use this tool when you want to answer questions like:
          - "Are there any CRC or FCS errors on Leaf3?"
          - "What is the raw packet and byte throughput on ge-0/0/1?"
          - "Are there any rx_discard or tx_discard counts on Spine1's uplinks?"
          - "Is this interface dropping or erroring at all?"

        Counter fields returned per interface
        -------------------------------------
          rx_unicast_packets, rx_broadcast_packets, rx_multicast_packets
          rx_error_packets, rx_discard_packets, rx_bytes
          tx_unicast_packets, tx_broadcast_packets, tx_multicast_packets
          tx_error_packets, tx_discard_packets, tx_bytes
          alignment_errors, fcs_errors, symbol_errors, runts, giants
          last_fetched_at — when Apstra last polled this counter

        These are cumulative counters since the last device reset, not rates.
        For per-second rates and utilisation percentages, use
        get_interface_utilisation instead.

        Parameters
        ----------
        system_id      : Hardware chassis serial number (e.g. "5254002D005F").
                         Use get_systems to discover valid values — it is the
                         `system_id` field, NOT the `id` field.
        interface      : Optional filter — return only this interface name
                         (e.g. "ge-0/0/1").  Returns all interfaces if omitted.
        errors_only    : If True, return only interfaces that have at least one
                         non-zero error counter (fcs_errors, alignment_errors,
                         rx_error_packets, tx_error_packets, runts, giants,
                         symbol_errors).  Useful for quickly finding problem ports.
        instance_name  : Target a specific Apstra instance.

        Data source: live Apstra API → device streaming telemetry
        Latency: counters are typically 30–120 s behind real-time
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'"}

        session = target[0]
        raw = await live_data_client.get_interface_counters(session, system_id)
        items = raw.get("items", [])

        if interface:
            items = [i for i in items if i.get("interface_name") == interface]

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
            "filters": {"interface": interface, "errors_only": errors_only},
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
        blueprint_id: str,
        system_id: str = None,
        interface: str = None,
        top_n: int = 10,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns interface utilisation percentages and per-second error/discard
        rates as computed by the Apstra IBA "Device Traffic" probe.

        Unlike get_interface_counters (which returns raw cumulative totals),
        this tool returns computed averages over the probe's sampling period
        (typically 120 s), making it directly useful for answering throughput
        and utilisation questions.

        Use this tool when you want to answer questions like:
          - "What is the utilisation on the uplinks to Spine2?"
          - "Which interfaces are the most heavily loaded in this blueprint?"
          - "Are there any persistent discard or error rates on fabric ports?"
          - "Is the bandwidth utilisation on Leaf1's spine-facing ports balanced?"

        Fields returned per interface
        -----------------------------
          tx_utilization_average    — TX utilisation as a fraction (0.0–1.0)
          rx_utilization_average    — RX utilisation as a fraction (0.0–1.0)
          tx_bps_average            — TX bits per second (average)
          rx_bps_average            — RX bits per second (average)
          tx_error_pps_average      — TX error packets per second
          rx_error_pps_average      — RX error packets per second
          tx_discard_pps_average    — TX discard packets per second
          rx_discard_pps_average    — RX discard packets per second
          fcs_errors_per_second_average
          speed                     — link speed in bits per second
          role                      — interface role (spine_leaf, to_generic, etc.)
          system_id, interface, label

        Results are sorted by max(tx_utilization, rx_utilization) descending so
        the busiest ports always appear first.

        Parameters
        ----------
        blueprint_id   : Blueprint to query (IBA probes are per-blueprint).
        system_id      : Optional filter — return only interfaces on this device.
                         Use the hardware serial (e.g. "5254002D005F").
        interface      : Optional filter — return only this interface name.
        top_n          : Return only the top N busiest interfaces (default 10).
                         Set to 0 to return all.
        instance_name  : Target a specific Apstra instance.

        Data source: Apstra IBA probe "Device Traffic" → stage "Average Interface Counters"
        Sampling period: typically 120 seconds
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'"}

        session = target[0]

        # Find the "Device Traffic" probe by label
        probes_raw = await live_data_client.get_probes(session, blueprint_id)
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
        raw = await live_data_client.query_probe_stage(
            session, blueprint_id, probe_id,
            stage="Average Interface Counters",
        )
        items = raw.get("items", [])

        # Filter
        if system_id:
            items = [i for i in items if i.get("properties", {}).get("system_id") == system_id]
        if interface:
            items = [i for i in items if i.get("properties", {}).get("interface") == interface]

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
                "system_id": system_id,
                "interface": interface,
                "top_n":     top_n,
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
        system_ids: list[str],
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns the latest CPU and memory utilisation for one or more devices
        as polled by Apstra's streaming telemetry.

        Use this tool when you want to answer questions like:
          - "Is Spine1 under CPU or memory pressure?"
          - "What are the resource utilisation levels across all spines?"
          - "Which device has the highest memory consumption?"
          - "Are any devices showing elevated CPU (suggesting a routing issue)?"

        Parameters
        ----------
        system_ids    : One or more hardware chassis serial numbers
                        (e.g. ["5254002D005F", "525400F8CE53"]).
                        Use get_systems to discover valid values — use the
                        `system_id` field, NOT the `id` field.
        instance_name : Target a specific Apstra instance.

        Fields returned per device
        --------------------------
          system_id
          cpu_pct    — current CPU utilisation percentage (integer)
          memory_pct — current memory utilisation percentage (integer)
          last_fetched_at — when Apstra last polled this device

        Data source: live Apstra API → device streaming telemetry
        Latency: values are typically 30–120 s behind real-time
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'"}

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
        system_id: str,
        interface_name: str,
        hours_back: int = 24,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns a time-series of error counter *growth* for a single interface,
        using locally stored counter snapshots collected every 5 minutes.

        This is the primary tool for detecting creeping errors — e.g. an
        interface where FCS errors are slowly increasing over hours or days,
        indicating a physical layer degradation before it causes an outage.

        Use this tool when you want to answer questions like:
          - "Is ge-0/0/1 on Leaf3 accumulating CRC/FCS errors over time?"
          - "When did errors start appearing on this interface?"
          - "Are the errors getting worse, improving, or stable?"
          - "Did the error rate change after the maintenance window?"
          - "Are there any counter resets (device reboots) in the window?"

        Each row in the `trend` list represents the change in counter values
        between two consecutive 5-minute poll snapshots:

          polled_at         — timestamp when the later snapshot was taken
          interval_seconds  — seconds between the two snapshots
          fcs_errors        — new FCS/CRC errors in this interval
          alignment_errors  — new alignment errors
          symbol_errors     — new symbol errors
          rx_error_packets  — new RX error packets
          tx_error_packets  — new TX error packets
          runts             — new undersized frames
          giants            — new oversized frames
          rx_discard_packets / tx_discard_packets  — new discards
          rx_bytes / tx_bytes  — traffic volume in this interval (context)
          total_errors      — sum of all error counter deltas in this interval
          has_reset         — True if a counter decreased (device reboot/wrap);
                              error deltas are set to 0 for that interval

        Parameters
        ----------
        system_id      : Hardware chassis serial (e.g. "5254002D005F").
        interface_name : Exact interface name as returned by the API
                         (e.g. "ge-0/0/1").
        hours_back     : How many hours of history to return (1–168).
                         Default 24 hours.
        instance_name  : Target a specific Apstra instance.

        Data source: local counter_store (populated every 5 min by counter_poller)
        Coverage    : Available from first poll after server startup.
        Note        : Returns empty trend list if fewer than 2 snapshots exist
                      in the requested window (not enough data to compute deltas).
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'"}

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
        hours_back: int = 24,
        top_n: int = 20,
        blueprint_id: str = None,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns the interfaces that have accumulated the most error counter
        growth over the specified time window, ranked worst-first.

        Use this tool when you want to answer questions like:
          - "Which interfaces are accumulating errors most rapidly right now?"
          - "Are there any error trends I should be concerned about after
            yesterday's change window?"
          - "Does any interface have a pattern of increasing FCS errors
            that could indicate a degrading cable or SFP?"
          - "Show me the health of all fabric uplinks over the past week."

        The tool queries the local counter time-series database populated by
        the counter_poller.  For each interface, it computes the cumulative
        error growth over the window and returns a ranked summary.

        Fields returned per interface
        -----------------------------
          system_id           — hardware chassis serial
          interface_name
          snapshot_count      — number of 5-min polls available in the window
          total_fcs_errors    — total new FCS/CRC errors over the window
          total_alignment_errors
          total_symbol_errors
          total_rx_error_packets / total_tx_error_packets
          total_runts / total_giants
          total_discards      — rx_discard + tx_discard totals
          total_errors        — sum of all error counter growth
          error_rate_per_hour — total_errors / hours_back
          reset_count         — number of intervals with a counter reset
          has_any_errors      — True if any error counter grew

        Results are sorted by total_errors descending.

        Parameters
        ----------
        hours_back    : Look-back window (1–168 hours).  Default 24 hours.
        top_n         : Maximum number of interfaces to return.  Default 20.
        blueprint_id  : Optional.  If provided, restrict results to systems
                        in this blueprint (resolved via the graph registry).
                        If omitted, all systems on the instance are included.
        instance_name : Target a specific Apstra instance.

        Data source: local counter_store (populated every 5 min by counter_poller)
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'"}

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
