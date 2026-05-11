from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.blueprints import resolve_blueprints
from handlers.virtual_networks import (
    handle_get_virtual_networks,
    handle_get_virtual_network_list,
    handle_get_routing_zones,
    handle_get_routing_zone_detail,
    handle_get_virtual_network_detail,
)


def register(mcp):

    _BP_DESC_REQ = (
        "Required. Apstra blueprint ID or partial label (e.g. 'DC1'). "
        "Use get_blueprints to list available blueprints and their IDs. "
        "Pass a full UUID for a specific blueprint."
    )
    _SYS_DESC = (
        "Hardware chassis serial (e.g. '5254002D005F'). "
        "Use the system_id field from get_systems — NOT the id field. "
        "Omit to include all switches."
    )
    _INST_DESC = (
        "Apstra instance name. Do not ask the user for this — leave as None to query all instances. "
        "Only set if the user explicitly names a specific instance."
    )

    @mcp.tool()
    async def get_vn_deployments(
        blueprint_id: Annotated[str, Field(description=_BP_DESC_REQ)],
        system_id: Annotated[str | None, Field(default=None, description=_SYS_DESC)] = None,
        vn_label: Annotated[
            str | None,
            Field(default=None, description=(
                "Filter to a specific virtual network by label (e.g. 'Web_Prod'). "
                "Case-insensitive substring match. Omit to return all VN deployments."
            )),
        ] = None,
        instance_name: Annotated[str | None, Field(default=None, description=_INST_DESC)] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Show where each virtual network is deployed across the fabric — one row per VN per switch.

        Use this to find which switches carry a specific VN and what local VLAN ID is assigned
        on each. VLAN IDs are allocated per-switch, so the same VNI can have a different vlan_id
        on Leaf1 vs Leaf2. For the full VN design intent (IP config, anycast gateway) use
        get_virtual_network_detail instead.

        Pass system_id to scope to one switch. Pass vn_label to filter to a specific VN.
        Pass blueprint_id=null for all blueprints.

        Returns: vn_instances (list with sw_id, sw_label, vlan_id, vni_id, ipv4_enabled,
        ipv4_mode, dhcp_enabled, vn_id, vn_label, vn_type, vni_number, ipv4_subnet), count.
        Data source: graph database (auto-rebuilt when blueprint version changes).
        """
        sessions = ctx.lifespan_context["sessions"]
        blu_list = await resolve_blueprints(sessions, blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}
        results = []
        for bp in blu_list:
            r = await handle_get_virtual_networks(
                sessions, ctx.lifespan_context["graph_registry"],
                bp["id"], system_id, instance_name,
            )
            # Apply vn_label filter if requested
            if vn_label and "vn_instances" in r:
                vn_label_lower = vn_label.lower()
                r["vn_instances"] = [
                    inst for inst in r["vn_instances"]
                    if vn_label_lower in (inst.get("vn_label") or "").lower()
                ]
                r["count"] = len(r["vn_instances"])
                r["filter_vn_label"] = vn_label
            results.append(r)
        return results[0] if len(results) == 1 else {"blueprint_count": len(results), "results": results}

    @mcp.tool()
    async def get_virtual_networks(
        blueprint_id: Annotated[str, Field(description=_BP_DESC_REQ)],
        instance_name: Annotated[str | None, Field(default=None, description=_INST_DESC)] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return all virtual networks in a blueprint with L3/gateway design intent and parent routing zone.

        Lists every VN regardless of which switches carry it. For per-switch deployment state
        and local VLAN assignments use get_vn_deployments. For one VN in full detail use
        get_virtual_network_detail.

        Returns: virtual_networks (list with id, label, vn_type, vni_number, ipv4_enabled,
        ipv4_subnet, ipv6_enabled, ipv6_subnet, virtual_gateway_ipv4,
        virtual_gateway_ipv4_enabled, virtual_mac, l3_mtu, description, tags,
        routing_zone_label, vrf_name, routing_zone_type), count.
        Data source: graph database (auto-rebuilt when blueprint version changes).
        """
        sessions = ctx.lifespan_context["sessions"]
        blu_list = await resolve_blueprints(sessions, blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}
        results = []
        for bp in blu_list:
            r = await handle_get_virtual_network_list(
                sessions, ctx.lifespan_context["graph_registry"],
                bp["id"], instance_name,
            )
            results.append(r)
        return results[0] if len(results) == 1 else {"blueprint_count": len(results), "results": results}

    @mcp.tool()
    async def get_routing_zones(
        blueprint_id: Annotated[str, Field(description=_BP_DESC_REQ)],
        instance_name: Annotated[str | None, Field(default=None, description=_INST_DESC)] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return all routing zones (VRFs) in a blueprint with the count of virtual networks in each.

        In Apstra every VN belongs to exactly one routing zone, which maps to a VRF on leaf
        switches. Use this for a VRF inventory overview. Use get_routing_zone_detail to see
        member VNs and which switches carry them for a specific zone.

        Returns: routing_zones (list with id, label, vrf_name, sz_type
        (l3_fabric / evpn), vn_count), count.
        Data source: graph database (auto-rebuilt when blueprint version changes).
        """
        sessions = ctx.lifespan_context["sessions"]
        blu_list = await resolve_blueprints(sessions, blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}
        results = []
        for bp in blu_list:
            r = await handle_get_routing_zones(
                sessions, ctx.lifespan_context["graph_registry"],
                bp["id"], instance_name,
            )
            results.append(r)
        return results[0] if len(results) == 1 else {"blueprint_count": len(results), "results": results}

    @mcp.tool()
    async def get_routing_zone_detail(
        blueprint_id: Annotated[str, Field(description=_BP_DESC_REQ)],
        routing_zone: Annotated[
            str,
            Field(description=(
                "Routing zone label or VRF name (both accepted). "
                "Use get_routing_zones to list available zones."
            )),
        ],
        instance_name: Annotated[str | None, Field(default=None, description=_INST_DESC)] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return full detail for a single routing zone (VRF), including member VNs and which
        switches carry at least one VN from the zone.

        Use this to map which devices participate in a given VRF and which virtual networks
        live in it. Accepts both the zone label and the vrf_name.

        Returns: detail with id, label, vrf_name, sz_type, member_virtual_networks (list with
        vn_id, vn_label, vn_type, vni_number, ipv4_enabled, ipv4_subnet, ipv6_enabled,
        ipv6_subnet, virtual_gateway_ipv4, l3_mtu, description, tags), member_systems
        (list with sw_id, sw_label, sw_role), vn_count, system_count.
        Data source: graph database (auto-rebuilt when blueprint version changes).
        """
        sessions = ctx.lifespan_context["sessions"]
        blu_list = await resolve_blueprints(sessions, blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}
        results = []
        for bp in blu_list:
            r = await handle_get_routing_zone_detail(
                sessions, ctx.lifespan_context["graph_registry"],
                bp["id"], routing_zone, instance_name,
            )
            results.append(r)
        return results[0] if len(results) == 1 else {"blueprint_count": len(results), "results": results}

    @mcp.tool()
    async def get_virtual_network_detail(
        blueprint_id: Annotated[str, Field(description=_BP_DESC_REQ)],
        virtual_network: Annotated[
            str,
            Field(description=(
                "VN label or graph node ID (both accepted). "
                "Use get_virtual_networks to list available VNs."
            )),
        ],
        instance_name: Annotated[str | None, Field(default=None, description=_INST_DESC)] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return full configuration detail for a single virtual network — design intent, gateway
        config, parent routing zone, and per-switch deployment state.

        Use this when you need the complete picture for one VN: IP subnet, anycast gateway,
        which switches carry it, and the local VLAN on each switch. For a listing of all VNs
        use get_virtual_networks. For switch-level deployment across all VNs use get_vn_deployments.

        Returns: detail with id, label, vn_type, vni_number, ipv4_enabled, ipv4_subnet,
        virtual_gateway_ipv4, virtual_gateway_ipv4_enabled, virtual_mac, l3_mtu,
        routing_zone (sz_id, routing_zone_label, vrf_name, routing_zone_type),
        deployed_on (list with sw_id, sw_label, sw_role, vlan_id, ipv4_enabled, ipv4_mode,
        dhcp_enabled), deployed_count.
        Data source: graph database (auto-rebuilt when blueprint version changes).
        """
        sessions = ctx.lifespan_context["sessions"]
        blu_list = await resolve_blueprints(sessions, blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}
        results = []
        for bp in blu_list:
            r = await handle_get_virtual_network_detail(
                sessions, ctx.lifespan_context["graph_registry"],
                bp["id"], virtual_network, instance_name,
            )
            results.append(r)
        return results[0] if len(results) == 1 else {"blueprint_count": len(results), "results": results}
