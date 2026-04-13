from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.systems import handle_get_systems
from handlers.system_context import handle_get_system_context


def register(mcp):

    @mcp.tool()
    async def get_systems(
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
        Return all switch systems (leaf, spine, access, superspine) in a blueprint.

        Call this to discover system_id values (hardware chassis serials) required by
        get_interface_list, get_interface_counters, get_rendered_config,
        run_device_commands, get_system_telemetry, and get_system_config_context.
        Always use the system_id field (hardware serial, e.g. "5254002D005F") for those
        tools — not the id field (graph node ID).

        Each system includes: id (graph node ID), label (hostname as shown in Apstra UI),
        role (leaf/spine/access/superspine), system_id (hardware chassis serial),
        hostname, deploy_mode (deploy/drain), management_level, external (bool),
        group_label.
        Data source: graph database (auto-rebuilt when blueprint version changes).
        """
        return await handle_get_systems(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            instance_name,
        )

    @mcp.tool()
    async def get_system_config_context(
        blueprint_id: Annotated[
            str,
            "Apstra blueprint ID. Use get_blueprints to discover valid values.",
        ],
        system_id: Annotated[
            str,
            (
                "Hardware chassis serial (e.g. '5254002D005F'). "
                "Use the system_id field from get_systems — NOT the id field (graph node ID)."
            ),
        ],
        include_sections: Annotated[
            list[str] | None,
            Field(
                default=None,
                description=(
                    "Additional nested sections to include. Valid names: "
                    "device_capabilities, dhcp_servers, interface, ip, portSetting, bgpService, "
                    "ospf_services, bgp_sessions, routing, vlan, configlets, vxlan, "
                    "security_zones, loopbacks, access_lists, dot1x_config, aaa_servers, "
                    "fabric_policy, evpn_interconnect, load_balancing_policy, property_sets. "
                    "Omit for scalar root fields only."
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
        Return the design-time configuration context Apstra uses to render device config.

        Use this to inspect the full data model behind config generation: scalar device
        parameters (hostname, role, ASN, management IP, OS version, deploy_mode), and
        optional nested sections such as BGP sessions, routing policy, security zones,
        and applied configlet values. Call with include_sections omitted first to see
        just the scalar fields — they are returned quickly. Then request specific sections
        only when needed, because interface, ip, and bgp_sessions sections are very large.
        Prefer get_rendered_config when you want the actual JunOS/EOS configuration text.

        Default scalar root fields include: hostname, role, os, management_ip, aos_version,
        lo0_ipv4_address, deploy_mode, model, node_id, device_sn.
        Optional sections (pass key name in include_sections): interface, ip, portSetting,
        bgpService, bgp_sessions, routing, security_zones, configlets, property_sets,
        device_capabilities, dhcp_servers, vlan, vxlan, loopbacks, fabric_policy, aaa_servers.
        Data source: live Apstra API.
        """
        return await handle_get_system_context(
            ctx.lifespan_context["sessions"],
            blueprint_id,
            system_id,
            include_sections,
            instance_name,
        )
