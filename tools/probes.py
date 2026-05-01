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
from typing import Annotated

from fastmcp import Context
from pydantic import Field

from primitives import live_data_client


def register(mcp):

    _BP_DESC_REQ = (
        "Required. Apstra blueprint ID or partial label (e.g. 'DC1'). "
        "Use get_blueprints to list available blueprints and their IDs. "
        "Pass a full UUID for a specific blueprint."
    )
    _INST_DESC = (
        "Apstra instance name. Do not ask the user for this — leave as None to query all instances. "
        "Only set if the user explicitly names a specific instance."
    )

    # ── Tool 1: get_probe_list ────────────────────────────────────────────────

    @mcp.tool()
    async def get_probe_list(
        blueprint_id: Annotated[str, Field(description=_BP_DESC_REQ)],
        anomalous_only: Annotated[
            bool,
            Field(default=False, description="If True, return only probes with anomaly_count > 0."),
        ] = False,
        instance_name: Annotated[str | None, Field(default=None, description=_INST_DESC)] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return all IBA probes in a blueprint with their operational state and current anomaly counts.

        Use this to discover what Apstra is actively monitoring (ECMP imbalance, BGP flapping,
        VXLAN flood lists, device health, etc.) and which probes currently have anomalies.
        Use get_probe_detail to see anomaly details for a specific probe, or get_probe_history
        for time-series data. stage_names in each probe tells you what data you can query.

        Returns: probes (list with id, label, description, state, probe_state, disabled,
        anomaly_count, predefined_probe, stage_names, updated_at), probe_count, total_anomalies.
        Sorted: probes with anomalies first, then alphabetical.
        Data source: live Apstra API.
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'", "hint": "Do not set instance_name — leave as None to query all instances automatically."}

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
            "blueprint_id":    blueprint_id,
            "instance":        session.name,
            "probe_count":     len(probes_out),
            "count":           len(probes_out),
            "total_anomalies": total_anomalies,
            "filters":         {"anomalous_only": anomalous_only},
            "probes":          probes_out,
            "_meta":           {"data_source": "live_apstra_api"},
        }

    # ── Tool 2: get_probe_detail ──────────────────────────────────────────────

    @mcp.tool()
    async def get_probe_detail(
        blueprint_id: Annotated[str, Field(description=_BP_DESC_REQ)],
        probe_id: Annotated[
            str,
            Field(description="Probe UUID. Use get_probe_list to discover probe IDs and their stage_names."),
        ],
        stage: Annotated[
            str | None,
            Field(default=None, description=(
                "Stage name to query. Defaults to the first stage if omitted. "
                "Use get_probe_list → stage_names to see available stages for a probe."
            )),
        ] = None,
        anomalous_only: Annotated[
            bool,
            Field(default=False, description="If True, return only rows in an anomalous state (not all stages support this)."),
        ] = False,
        instance_name: Annotated[str | None, Field(default=None, description=_INST_DESC)] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return the current output and anomaly state for a specific IBA probe stage.

        Use this to see what a probe is currently computing — e.g. which BGP sessions are flagged
        as flapping, what the ECMP imbalance values are, or which VXLAN flood list entries are
        anomalous. If no stage is given, the first stage is queried. Call get_probe_list first to
        find probe IDs and stage names.

        Returns: probe_label, probe_state, anomaly_count, all_stages, queried_stage, stage_type,
        stage_description, items (list with timestamp, value, properties), item_count.
        Data source: live Apstra API.
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'", "hint": "Do not set instance_name — leave as None to query all instances automatically."}

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
        blueprint_id: Annotated[str, Field(description=_BP_DESC_REQ)],
        probe_id: Annotated[
            str,
            Field(description="Probe UUID. Use get_probe_list to discover probe IDs."),
        ],
        stage: Annotated[
            str,
            Field(description=(
                "REQUIRED. Stage name to query. "
                "You MUST call get_probe_list first to get stage_names for this probe — "
                "do not guess the stage name. "
                "Alternatively, call get_probe_detail to see all_stages."
            )),
        ],
        hours_back: Annotated[
            int,
            Field(default=1, description="How far back to look (1–168 hours). Default 1.", ge=1, le=168),
        ] = 1,
        end_time: Annotated[
            str | None,
            Field(default=None, description="ISO-8601 end timestamp. Defaults to now."),
        ] = None,
        instance_name: Annotated[str | None, Field(default=None, description=_INST_DESC)] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Query the time-series output of a specific IBA probe stage over a historical time window.

        Use this to detect trends — e.g. whether BGP flapping is getting worse, when an ECMP
        imbalance anomaly first appeared, or what CPU utilisation looked like over the past few
        hours. Call get_probe_list first to find stage names, and get_probe_detail for the
        current state before looking at history.

        Items are returned newest-first. Number of items depends on probe sampling period and
        the requested window.

        Returns: items (list with timestamp, value, properties), item_count, total_count,
        stage_type, begin_time, end_time.
        Data source: live Apstra API.
        """
        sessions = ctx.lifespan_context["sessions"]
        target = [s for s in sessions if instance_name is None or s.name == instance_name]
        if not target:
            return {"error": f"No session found for instance '{instance_name}'", "hint": "Do not set instance_name — leave as None to query all instances automatically."}

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
