from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.links import handle_get_link_list
from handlers.blueprints import resolve_blueprints


def register(mcp):

    @mcp.tool()
    async def get_link_list(
        blueprint_id: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Apstra blueprint ID, partial label, or null. "
                    "Pass null or 'all' for every blueprint. "
                    "Pass a partial name (e.g. 'DC1') to match by label. "
                    "Pass a full UUID for a specific blueprint."
                ),
            ),
        ] = None,
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
        Return physical links in one or all blueprints with both endpoints, link role, and speed.

        Use this to understand the fabric cabling: which devices are connected, over what
        speeds, in what fabric role. When system_id is omitted, all fabric links are
        returned — useful for capacity planning, redundancy verification, or finding
        asymmetric link speeds. When system_id is provided, only links with one endpoint
        on that switch are returned.

        Pass blueprint_id=null to query all blueprints at once.

        Each link includes: link_id, link_type, role, speed, deploy_mode,
        local_interface and remote_interface (each with if_name, if_type, description,
        operation_state, ipv4_addr, lag_mode).
        Data source: graph database (auto-rebuilt when blueprint version changes).
        """
        sessions = ctx.lifespan_context["sessions"]
        registry = ctx.lifespan_context["graph_registry"]
        blu_list = await resolve_blueprints(sessions, blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}

        if len(blu_list) > 1:
            results = []
            for bp in blu_list:
                r = await handle_get_link_list(sessions, registry, bp["id"], system_id, instance_name)
                r["blueprint_label"] = bp["label"]
                results.append(r)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "results": results}

        bp = blu_list[0]
        r = await handle_get_link_list(sessions, registry, bp["id"], system_id, instance_name)
        r["blueprint_label"] = bp["label"]
        return r


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
