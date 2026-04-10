from fastmcp import Context

from handlers.anomalies import handle_get_anomalies


def register(mcp):

    @mcp.tool()
    async def get_current_anomalies(blueprint_id: str, instance_name: str = None, ctx: Context = None) -> dict:
        """
        Returns active anomalies for a given blueprint from the live network.

        Data source: live network (live_data_client). Results reflect the
        current state of the network and may vary between calls if conditions
        change.

        Args:
            blueprint_id: The Apstra blueprint ID to query anomalies for.
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            A dict containing:
              - instance: name of the Apstra instance queried, or "all" if
                          multiple instances were queried
              - blueprint_id: the blueprint queried
              - anomalies: list of anomaly objects, each with severity,
                           type, description, and affected_node
              - count: total number of anomalies returned
        """
        return await handle_get_anomalies(
            ctx.lifespan_context["sessions"], blueprint_id, instance_name
        )
