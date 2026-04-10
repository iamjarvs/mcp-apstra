from fastmcp import Context

from handlers.systems import handle_get_systems
from handlers.system_context import handle_get_system_context


def register(mcp):

    @mcp.tool()
    async def get_systems(blueprint_id: str, instance_name: str = None, ctx: Context = None) -> dict:
        """
        Returns all switch systems (leaf, spine, access, superspine) in a
        given blueprint.

        Data source: graph database (graph_client). Results reflect the
        design intent as stored in the Apstra blueprint graph. The graph is
        automatically rebuilt if the blueprint version has changed since the
        last query.

        Use get_blueprints first to discover valid blueprint_id values.

        Args:
            blueprint_id: The Apstra blueprint ID to query systems for.
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - systems: list of system objects, each with:
                  id, label, role (leaf/spine/access/superspine),
                  system_id (hardware chassis ID), system_type, hostname,
                  deploy_mode, management_level, external, and group_label
              - count: total number of systems returned

            When querying all instances:
              - instance: "all"
              - blueprint_id: the blueprint queried
              - results: list of per-instance result objects (same shape as above)
              - total_count: sum of all systems across all instances
        """
        return await handle_get_systems(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            instance_name,
        )

    @mcp.tool()
    async def get_system_config_context(
        blueprint_id: str,
        system_id: str,
        include_sections: list[str] | None = None,
        instance_name: str | None = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns the design-time configuration context for a specific switch
        within a blueprint — the full data model Apstra uses to render device
        config (Junos, EOS, etc.).

        The context is fetched from the live API, decoded from its JSON-encoded
        string form, and filtered before being returned.

        By default, only scalar root-level fields are returned. The nested
        sections listed below are intentionally omitted by default because they
        can be very large (dozens of interfaces and hundreds of BGP sessions).
        Request them explicitly when you need the detail.

        Data source: live network (live_data_client). Results reflect the
        current committed design intent for this device.

        Use get_blueprints to discover valid blueprint_id values.
        Use get_systems to discover valid system_id values — it is the
        `system_id` field on each system object (e.g. "5254002D005F").
        Do NOT use the `id` field, which is the graph node ID.

        Args:
            blueprint_id:      The Apstra blueprint ID.
            system_id:         Hardware chassis serial number of the target
                               switch (e.g. "5254002D005F"). This is the
                               `system_id` field in get_systems output —
                               NOT the `id` field (graph node ID).
            include_sections:  Optional list of additional nested sections to
                               include in the response. Each name must match
                               exactly one of the section keys documented below.
                               Omit or pass null to receive scalar fields only.
            instance_name:     Optional. The Apstra instance name (from
                               instances.yaml). If omitted, all instances are
                               queried and results are merged.

        Default fields returned (scalar root-level values):
            name, hostname, reference_architecture, hcl, ecmp_limit,
            deploy_mode, port_count, role, configured_role, model,
            lo0_ipv4_address, os, dual_re, os_selector, asic,
            blueprint_has_esi, device_sn, node_id, mac_msb, ipv6_support,
            use_granular_mtu_rendering, aos_version, management_ip,
            os_version and any other
            scalar fields present in the context.

        Optional sections (pass the key name in include_sections):
            device_capabilities — Hardware capability flags: copp_strict,
                breakout_capable (dict of breakout-capable port groups),
                as_seq_num_supported.
            dhcp_servers — DHCP relay config per VRF: dhcp_servers,
                dhcpv6_servers, source IP/interface.
            interface — Per-interface L2/L3 config keyed by 'IF-<name>'
                (e.g. IF-ge-0/0/0). Includes role, switch_port_mode,
                MTU, dot1x, OSPF, and LAG settings. Large — one entry
                per physical port.
            ip — Per-interface IP config keyed by 'IP-<name>'. Includes
                ipv4_address, ipv4_prefixlen, ipv6_address, vrf_name.
                Large — one entry per physical port.
            portSetting — Port speed settings keyed by interface name.
                Each entry has global.speed, interface.speed, and state.
            bgpService — BGP service config: asn, router_id,
                overlay_protocol, EVPN flags, max route limits.
            ospf_services — OSPF process config. Typically empty on
                pure-BGP fabrics.
            bgp_sessions — All BGP sessions keyed by session name
                ('<src_ip>_<src_asn>-><dst_ip>_<dst_asn>_<vrf>'). Each
                entry has source/dest ASN and IP, address_families
                (ipv4/ipv6/evpn), vrf_name, route maps, BFD.
            routing — Routing policy: prefix_lists, route_maps (with
                sequence/action/custom_actions), community_lists,
                static_routes, bgp_aggregates.
            vlan — VLAN config keyed by VLAN ID. Typically empty on
                routed fabrics.
            configlets — User-defined configlets applied to this device,
                keyed by scope. Each entry has display_name and
                rendered templateText lines.
            vxlan — VXLAN VNI config. Typically empty at this level.
            security_zones — VRF/routing zone config keyed by vrf_name.
                Includes vni_id, import/export route targets, loopback
                IP, and EVPN IRB mode.
            loopbacks — Loopback interface config keyed by name.
            access_lists — ACL definitions. Empty unless explicit ACLs
                are configured.
            dot1x_config — 802.1X config. Typically empty on DC fabrics.
            aaa_servers — AAA/RADIUS/TACACS config.
            fabric_policy — Fabric-wide policy: EVPN route targets, MTU,
                Junos EVPN flags, default anycast GW MAC.
            evpn_interconnect — EVPN interconnect for multi-pod/multi-DC.
                Typically empty in single-pod deployments.
            load_balancing_policy — ECMP load balancing policy.
            property_sets — User-defined property set values applied to
                this device (arbitrary key-value pairs used in configlets).

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - system_id: the system_id queried
              - context: filtered context dict (scalars + any requested sections)

            When querying all instances:
              - instance: "all"
              - blueprint_id, system_id: as above
              - results: list of per-instance result objects (same shape as above)
        """
        return await handle_get_system_context(
            ctx.lifespan_context["sessions"],
            blueprint_id,
            system_id,
            include_sections,
            instance_name,
        )
