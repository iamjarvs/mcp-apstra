from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.anomalies import handle_get_anomalies


def register(mcp):

    @mcp.tool()
    async def get_current_anomalies(
        blueprint_id: Annotated[
            str,
            "Apstra blueprint ID. Use get_blueprints to discover valid values.",
        ],
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return active anomalies for a blueprint directly from the live Apstra API.

        Use this for a definitive real-time snapshot of current fabric health: which
        anomalies are active right now, their severity, type, and affected node. For
        faster responses with zero API latency, use get_active_anomalies_from_store
        (local cache, updated every 60 s). For historical context and trend analysis
        use get_anomaly_events or get_anomaly_trend.

        Returns: instance, blueprint_id, anomalies (list with severity, type, description,
        affected_node), count.
        Data source: live Apstra API (results reflect current state and may vary between calls).
        """
        return await handle_get_anomalies(
            ctx.lifespan_context["sessions"], blueprint_id, instance_name
        )
