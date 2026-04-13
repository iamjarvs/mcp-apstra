from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.interfaces import handle_get_interface_list


def register(mcp):

    @mcp.tool()
    async def get_interface_list(
        blueprint_id: Annotated[
            str,
            "Apstra blueprint ID. Use get_blueprints to discover valid values.",
        ],
        system_id: Annotated[
            str,
            (
                "Hardware chassis serial (e.g. '525400AA7236'). "
                "Use the system_id field from get_systems — NOT the id field (graph node ID)."
            ),
        ],
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return all interfaces for a specific switch in a blueprint.

        Use this to discover interface names before calling get_interface_counters or
        get_interface_error_trend, to verify IP address assignments match design intent,
        to check operation_state (up/admin_down/deduced_down) across all ports, or to
        inspect LAG membership, MTU, and routing protocol assignments from the graph model.

        Each interface includes: if_name (e.g. ge-0/0/0, ae1, lo0), if_type
        (ethernet/loopback/port_channel/svi/unicast_vtep/anycast_vtep), description
        (connected peer, e.g. "facing_spine1:ge-0/0/2" — null for virtual interfaces),
        operation_state, ipv4_addr (with prefix length), ipv6_addr, l3_mtu, lag_mode
        (lacp_active/lacp_passive/static_lag), protocols (e.g. "ebgp").
        Data source: graph database (auto-rebuilt when blueprint version changes).
        """
        return await handle_get_interface_list(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            system_id,
            instance_name,
        )
