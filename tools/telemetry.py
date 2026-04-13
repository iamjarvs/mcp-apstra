"""
tools/telemetry.py

MCP tools for device telemetry data queried directly from the Apstra API.

  get_interface_counters    — raw error/traffic counters per interface
  get_interface_utilisation — utilisation % and error rates from IBA probe
  get_system_telemetry      — CPU and memory per device

Data source: live Apstra API.  Every call makes one or more HTTP requests to
the Apstra controller, which in turn has cached the values from the streaming
telemetry it receives from each managed device.  Results are typically 30–120
seconds behind real-time depending on the probe sampling period.

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
