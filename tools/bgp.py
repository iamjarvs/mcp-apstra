from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.bgp import handle_get_external_peerings, handle_get_fabric_peerings
from handlers.blueprints import resolve_blueprints

_BP_DESC = (
    "Apstra blueprint ID, partial label, or null. "
    "Pass null or 'all' for every blueprint. "
    "Pass a partial name (e.g. 'DC1') to match by label. "
    "Pass a full UUID for a specific blueprint."
)


def register(mcp):

    @mcp.tool()
    async def get_external_blueprint_peerings(
        blueprint_id: Annotated[str | None, Field(default=None, description=_BP_DESC)] = None,
        device: Annotated[
            str | None,
            Field(
                default=None,
                description="Hostname of a fabric edge device to scope the query (e.g. 'Leaf3'). Omit for all external peerings.",
            ),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return BGP peerings between fabric devices and external systems (routers, firewalls, servers).

        Use this to map the fabric edge: which leaf or spine devices have external sessions,
        what ASNs and address families (IPv4/IPv6 SAFI) are configured, and over which
        interfaces. Only returns sessions where the remote peer is outside the blueprint
        (system.external = true). For intra-fabric underlay sessions between managed devices
        use get_fabric_bgp_peerings instead. Reflects design intent from the blueprint graph,
        not live BGP neighbour state — use run_device_commands with "show bgp summary" to
        verify live session state.

        Pass blueprint_id=null to check all blueprints at once.

        Each peering includes: session_id, bfd (bool), ipv4_safi, ipv6_safi, ttl,
        local (hostname, role, interface, ip_address, local_asn) and remote (hostname,
        ip_address, local_asn; interface and serial are null for unmanaged external peers).
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
                r = await handle_get_external_peerings(sessions, registry, bp["id"], device, instance_name)
                r["blueprint_label"] = bp["label"]
                results.append(r)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "results": results}

        bp = blu_list[0]
        r = await handle_get_external_peerings(sessions, registry, bp["id"], device, instance_name)
        r["blueprint_label"] = bp["label"]
        return r

    @mcp.tool()
    async def get_fabric_bgp_peerings(
        blueprint_id: Annotated[str | None, Field(default=None, description=_BP_DESC)] = None,
        device: Annotated[
            str | None,
            Field(
                default=None,
                description="Device hostname to anchor the query (e.g. 'Leaf2'). Omit to return all intra-fabric peerings deduplicated.",
            ),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return intra-fabric eBGP peerings between Apstra-managed devices.

        Use this to trace the underlay BGP topology: spine-leaf sessions, ESI peer links,
        what ASNs are assigned to each device, what interface IPs are used on each link,
        and what MTU is configured. Only returns sessions where both peers are inside the
        blueprint (system.external = false for both ends). For peerings to external systems
        use get_external_blueprint_peerings. Optionally scope to one device to see only its
        sessions. Reflects design intent — not live BGP session state.

        Pass blueprint_id=null to check all blueprints at once.

        Each peering includes: link_role (spine_leaf/spine_superspine/leaf_peer_link/etc.),
        link_speed, a_side and b_side (each with hostname, role, serial, asn, interface
        name, interface description, ip_address, l3_mtu).
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
                r = await handle_get_fabric_peerings(sessions, registry, bp["id"], device, instance_name)
                r["blueprint_label"] = bp["label"]
                results.append(r)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "results": results}

        bp = blu_list[0]
        r = await handle_get_fabric_peerings(sessions, registry, bp["id"], device, instance_name)
        r["blueprint_label"] = bp["label"]
        return r


    @mcp.tool()
    async def get_external_blueprint_peerings(
        blueprint_id: Annotated[
            str,
            "Apstra blueprint ID. Use get_blueprints to discover valid values.",
        ],
        device: Annotated[
            str | None,
            Field(
                default=None,
                description="Hostname of a fabric edge device to scope the query (e.g. 'Leaf3'). Omit for all external peerings.",
            ),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return BGP peerings between fabric devices and external systems (routers, firewalls, servers).

        Use this to map the fabric edge: which leaf or spine devices have external sessions,
        what ASNs and address families (IPv4/IPv6 SAFI) are configured, and over which
        interfaces. Only returns sessions where the remote peer is outside the blueprint
        (system.external = true). For intra-fabric underlay sessions between managed devices
        use get_fabric_bgp_peerings instead. Reflects design intent from the blueprint graph,
        not live BGP neighbour state — use run_device_commands with "show bgp summary" to
        verify live session state.

        Each peering includes: session_id, bfd (bool), ipv4_safi, ipv6_safi, ttl,
        local (hostname, role, interface, ip_address, local_asn) and remote (hostname,
        ip_address, local_asn; interface and serial are null for unmanaged external peers).
        Data source: graph database (auto-rebuilt when blueprint version changes).
        """
        return await handle_get_external_peerings(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            device,
            instance_name,
        )

    @mcp.tool()
    async def get_fabric_bgp_peerings(
        blueprint_id: Annotated[
            str,
            "Apstra blueprint ID. Use get_blueprints to discover valid values.",
        ],
        device: Annotated[
            str | None,
            Field(
                default=None,
                description="Device hostname to anchor the query (e.g. 'Leaf2'). Omit to return all intra-fabric peerings deduplicated.",
            ),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return intra-fabric eBGP peerings between Apstra-managed devices.

        Use this to trace the underlay BGP topology: spine-leaf sessions, ESI peer links,
        what ASNs are assigned to each device, what interface IPs are used on each link,
        and what MTU is configured. Only returns sessions where both peers are inside the
        blueprint (system.external = false for both ends). For peerings to external systems
        use get_external_blueprint_peerings. Optionally scope to one device to see only its
        sessions. Reflects design intent — not live BGP session state.

        Each peering includes: link_role (spine_leaf/spine_superspine/leaf_peer_link/etc.),
        link_speed, a_side and b_side (each with hostname, role, serial, asn, interface
        name, interface description, ip_address, l3_mtu).
        Data source: graph database (auto-rebuilt when blueprint version changes).
        """
        return await handle_get_fabric_peerings(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            device,
            instance_name,
        )
