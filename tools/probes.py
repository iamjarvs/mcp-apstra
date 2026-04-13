"""
tools/probes.py

MCP tools for Apstra IBA (Intent-Based Analytics) probes.

  get_probe_list    — list all active probes in a blueprint with anomaly counts
  get_probe_detail  — full probe definition and current anomaly state per stage
  get_probe_history — query time-series output from a specific probe stage

IBA probes are how Apstra performs continuous intent verification beyond basic
anomaly checks.  They cover things like ECMP imbalance, hot/cold interface
counters, BGP session flapping, VXLAN flood list validation, device health,
and more.  An LLM with no probe access cannot answer questions about what
Apstra is actively monitoring or what the current computed state of those
checks is.

Data source: live Apstra API.  Every call makes HTTP requests to the Apstra
controller.

Use get_blueprints to discover valid blueprint_id values.
"""

from datetime import datetime, timezone, timedelta

from fastmcp import Context

from primitives import live_data_client


def register(mcp):

    # ── Tool 1: get_probe_list ────────────────────────────────────────────────

    @mcp.tool()
    async def get_probe_list(
        blueprint_id: str,
        anomalous_only: bool = False,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns all IBA probes configured in a blueprint with their current
        operational state and anomaly counts.

        Use this tool when you want to answer questions like:
          - "What is Apstra actively monitoring in this blueprint beyond
            basic anomaly checks?"
          - "Which probes currently have anomalies?"
          - "Is there a probe for ECMP imbalance / BGP flapping / VXLAN
            flood list validation?"
          - "How many anomalies does the Device Traffic probe have?"
          - "Is the BGP Monitoring probe operational?"

        Probe state values
        ------------------
          operational  — probe is running normally
          error        — probe has a configuration or runtime error
          disabled     — probe has been manually disabled

        Fields returned per probe
        -------------------------
          id, label, description, state, probe_state, disabled,
          anomaly_count, predefined_probe (the built-in template name if
          applicable), stage_names (list of queryable stage names),
          updated_at

        To query the actual data or anomaly details for a specific probe,
        use get_probe_detail or get_probe_history with the probe id.

        Parameters
        ----------
        blueprint_id   : Blueprint to query.
        anomalous_only : If True, return only probes with anomaly_count > 0.
        instance_name  : Target a specific Apstra instance.

        Data source: live Apstra API
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'"}

        session = target[0]
        raw = await live_data_client.get_probes(session, blueprint_id)
        items = raw.get("items", [])

        if anomalous_only:
            items = [p for p in items if (p.get("anomaly_count") or 0) > 0]

        probes_out = []
        for p in items:
            probes_out.append({
                "id":               p["id"],
                "label":            p.get("label"),
                "description":      p.get("description") or "",
                "state":            p.get("state"),
                "probe_state":      p.get("probe_state"),
                "disabled":         p.get("disabled", False),
                "anomaly_count":    p.get("anomaly_count", 0),
                "predefined_probe": p.get("predefined_probe"),
                "stage_names":      [st.get("name") for st in p.get("stages", [])],
                "updated_at":       p.get("updated_at"),
            })

        # Sort: probes with anomalies first, then alphabetical
        probes_out.sort(key=lambda p: (-p["anomaly_count"], p["label"] or ""))

        total_anomalies = sum(p["anomaly_count"] for p in probes_out)

        return {
            "blueprint_id":   blueprint_id,
            "instance":       session.name,
            "probe_count":    len(probes_out),
            "total_anomalies": total_anomalies,
            "filters":        {"anomalous_only": anomalous_only},
            "probes":         probes_out,
            "_meta":          {"data_source": "live_apstra_api"},
        }

    # ── Tool 2: get_probe_detail ──────────────────────────────────────────────

    @mcp.tool()
    async def get_probe_detail(
        blueprint_id: str,
        probe_id: str,
        stage: str = None,
        anomalous_only: bool = False,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns the current output and anomaly state of a specific IBA probe,
        optionally filtered to a single stage.

        Use this tool when you want to answer questions like:
          - "What does the BGP Monitoring probe currently show?"
          - "Which BGP sessions does the BGP Monitoring probe flag as flapping?"
          - "What is the ECMP imbalance probe reporting for the spine layer?"
          - "Show me only the anomalous rows from the VXLAN Flood List probe."
          - "What are the stage names I can query for this probe?"

        If no `stage` is specified, the tool queries the first stage of the
        probe.  Use get_probe_list to discover stage names, then call this
        tool again with a specific stage name to get the data you need.

        Items returned per stage row
        ----------------------------
        Each item has:
          timestamp  — when this data point was last computed
          value      — the computed value (type depends on stage)
          properties — dict of grouping dimensions (system_id, interface, etc.)

        Parameters
        ----------
        blueprint_id   : Blueprint to query.
        probe_id       : Probe UUID.  Use get_probe_list to discover probe IDs.
        stage          : Optional stage name to query.  Defaults to the first
                         stage if omitted.  Use get_probe_list to see all
                         stage_names for a probe.
        anomalous_only : If True, return only rows in an anomalous state.
                         Not all probe stages support this filter.
        instance_name  : Target a specific Apstra instance.

        Data source: live Apstra API
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'"}

        session = target[0]

        # Get full probe definition
        probe = await live_data_client.get_probe(session, blueprint_id, probe_id)
        stage_names = [st.get("name") for st in probe.get("stages", [])]

        if not stage_names:
            return {
                "blueprint_id": blueprint_id,
                "probe_id":     probe_id,
                "label":        probe.get("label"),
                "error":        "Probe has no queryable stages.",
            }

        query_stage = stage if stage else stage_names[0]
        if query_stage not in stage_names:
            return {
                "error": f"Stage '{query_stage}' not found.",
                "available_stages": stage_names,
            }

        try:
            result = await live_data_client.query_probe_stage(
                session, blueprint_id, probe_id,
                stage=query_stage,
                anomalous_only=anomalous_only,
            )
        except Exception as exc:
            # Some stages don't support anomalous_only — retry without it
            if anomalous_only:
                result = await live_data_client.query_probe_stage(
                    session, blueprint_id, probe_id,
                    stage=query_stage,
                    anomalous_only=False,
                )
                result["_warning"] = (
                    f"anomalous_only filter not supported by stage '{query_stage}'. "
                    "Returning all rows."
                )
            else:
                raise

        return {
            "blueprint_id":   blueprint_id,
            "instance":       session.name,
            "probe_id":       probe_id,
            "probe_label":    probe.get("label"),
            "probe_state":    probe.get("state"),
            "anomaly_count":  probe.get("anomaly_count", 0),
            "all_stages":     stage_names,
            "queried_stage":  query_stage,
            "stage_type":     result.get("type"),
            "stage_description": result.get("description", ""),
            "item_count":     result.get("total_count", len(result.get("items", []))),
            "items":          result.get("items", []),
            "_meta":          {"data_source": "live_apstra_api"},
        }

    # ── Tool 3: get_probe_history ─────────────────────────────────────────────

    @mcp.tool()
    async def get_probe_history(
        blueprint_id: str,
        probe_id: str,
        stage: str,
        hours_back: int = 1,
        end_time: str = None,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Queries the time-series output of a specific IBA probe stage over a
        historical time window.

        Use this tool when you want to answer questions like:
          - "Has BGP session flapping been getting worse in the last hour?"
          - "When did the ECMP imbalance probe first start reporting an
            anomaly?"
          - "Show me the interface error rate trend for the last 24 hours."
          - "What was the CPU utilisation on Leaf1 over the past 6 hours?"
          - "Was there a spike in VXLAN flood list anomalies last night?"

        The probe must have time-series data available — most operational
        probes retain a configurable history window.  Use get_probe_list to
        find stage names, and get_probe_detail to see the current state before
        looking at history.

        Items returned
        --------------
        Each item has:
          timestamp   — when this data point was recorded
          value       — computed value at that point in time
          properties  — grouping dimensions (system_id, interface, etc.)

        Items are returned newest-first.  The number of items depends on the
        probe sampling period and the requested time window.

        Parameters
        ----------
        blueprint_id  : Blueprint to query.
        probe_id      : Probe UUID.  Use get_probe_list to discover probe IDs.
        stage         : Stage name to query (required).  Use get_probe_list
                        or get_probe_detail to discover available stage names.
        hours_back    : How far back to look (1–168).  Default 1 hour.
        end_time      : Optional ISO-8601 end timestamp.  Defaults to now.
        instance_name : Target a specific Apstra instance.

        Data source: live Apstra API
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'"}

        session = target[0]
        hours_back = max(1, min(hours_back, 168))

        now = datetime.now(timezone.utc)
        begin_dt  = now - timedelta(hours=hours_back)
        begin_iso = begin_dt.isoformat()
        end_iso   = end_time or now.isoformat()

        result = await live_data_client.query_probe_stage(
            session, blueprint_id, probe_id,
            stage=stage,
            begin_time=begin_iso,
            end_time=end_iso,
            per_page=500,
        )

        items = result.get("items", [])
        # Return newest-first
        items = sorted(items, key=lambda i: i.get("timestamp", ""), reverse=True)

        return {
            "blueprint_id":  blueprint_id,
            "instance":      session.name,
            "probe_id":      probe_id,
            "stage":         stage,
            "hours_back":    hours_back,
            "begin_time":    begin_iso,
            "end_time":      end_iso,
            "item_count":    len(items),
            "total_count":   result.get("total_count", len(items)),
            "stage_type":    result.get("type"),
            "items":         items,
            "_meta": {
                "data_source": "live_apstra_api",
                "note": (
                    "Items are returned newest-first. "
                    "total_count may exceed item_count if the server has more data "
                    "than the per_page limit (500). "
                    "Sampling interval depends on the probe's configured period."
                ),
            },
        }
