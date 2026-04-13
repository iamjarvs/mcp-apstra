from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.links import handle_get_link_list


def register(mcp):

    @mcp.tool()
    async def get_link_list(
        blueprint_id: Annotated[
            str,
            "Apstra blueprint ID. Use get_blueprints to discover valid values.",
        ],
        system_id: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Hardware chassis serial to filter to links on that switch "
                    "(e.g. '525400AA7236'). Omit to return all fabric links."
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
        Return physical links in a blueprint with both endpoints, link role, and speed.

        Use this to understand the fabric cabling: which devices are connected, over what
        speeds, in what fabric role. When system_id is omitted, all fabric links are
        returned — useful for capacity planning, redundancy verification, or finding
        asymmetric link speeds. When system_id is provided, only links with one endpoint
        on that switch are returned, which is useful for device-focused investigation.

        Each link includes: link_id, link_type (ethernet/aggregate_link), role
        (spine_leaf/spine_superspine/leaf_l2_server/leaf_peer_link/to_external_router/etc.),
        speed (e.g. "10G", "100G"), deploy_mode, local_interface and remote_interface
        (each with if_name, if_type, description, operation_state, ipv4_addr, lag_mode).
        Data source: graph database (auto-rebuilt when blueprint version changes).
        """
        return await handle_get_link_list(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            system_id,
            instance_name,
        )
