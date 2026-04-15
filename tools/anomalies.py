from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.anomalies import handle_get_anomalies
from handlers.blueprints import resolve_blueprints


def register(mcp):

    @mcp.tool()
    async def get_current_anomalies(
        blueprint_id: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Apstra blueprint ID, partial label, or null. "
                    "Pass null or 'all' to query every blueprint. "
                    "Pass a partial name (e.g. 'DC1') to match by label substring. "
                    "Pass a full UUID for a specific blueprint."
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
        Return active anomalies for one or all blueprints directly from the live Apstra API.

        Use this for a definitive real-time snapshot of current fabric health: which
        anomalies are active right now, their severity, type, and affected node. For
        faster responses with zero API latency, use get_active_anomalies_from_store
        (local cache, updated every 60 s). For historical context and trend analysis
        use get_anomaly_events or get_anomaly_trend.

        Pass blueprint_id=null to check all blueprints at once. Pass a partial label such
        as "DC1" to match any blueprint whose name contains that string.

        Returns: instance, blueprint_id, anomalies (list with severity, type, description,
        affected_node), count.
        Data source: live Apstra API (results reflect current state and may vary between calls).
        """
        sessions = ctx.lifespan_context["sessions"]
        blu_list = await resolve_blueprints(sessions, blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}

        if len(blu_list) > 1:
            results = []
            for bp in blu_list:
                r = await handle_get_anomalies(sessions, bp["id"], instance_name)
                r["blueprint_label"] = bp["label"]
                results.append(r)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "results": results}

        bp = blu_list[0]
        r = await handle_get_anomalies(sessions, bp["id"], instance_name)
        r["blueprint_label"] = bp["label"]
        return r

