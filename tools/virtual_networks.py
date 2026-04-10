from fastmcp import Context

from handlers.virtual_networks import (
    handle_get_virtual_networks,
    handle_get_virtual_network_list,
    handle_get_routing_zones,
    handle_get_routing_zone_detail,
    handle_get_virtual_network_detail,
)


def register(mcp):

    @mcp.tool()
    async def get_vn_deployments(
        blueprint_id: str,
        system_id: str = None,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Shows where each virtual network is deployed across the fabric —
        one row per VN instance per switch.

        This is distinct from listing virtual networks in the design: it shows
        the actual deployment state, including the local VLAN assigned on each
        switch. Because VXLAN allows VLAN IDs to be assigned locally per-switch,
        the same VNI (vni_number) may have a different vlan_id on each switch —
        this is the key data point to surface when cross-referencing across the
        fabric.

        Data source: graph database (graph_client). The graph is automatically
        rebuilt if the blueprint version has changed since the last query.

        Use get_blueprints to discover valid blueprint_id values and
        get_systems to discover valid system_id values.

        Args:
            blueprint_id:  The Apstra blueprint ID to query.
            system_id:     Optional. Hardware system ID (chassis serial) of a
                           specific switch to scope the query. If omitted, all
                           switches in the blueprint are included.
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - system_id: the system_id filter used (or None for all switches)
              - vn_instances: list of VN instance objects, each with:
                  sw_id, sw_label — switch identity
                  vni_id          — VN-instance graph node ID
                  vlan_id         — local VLAN on this switch (may differ per switch)
                  ipv4_enabled, ipv4_mode, dhcp_enabled — per-instance IPv4/DHCP flags
                  vn_id, vn_label — virtual network identity
                  vn_type         — "vxlan" or "vlan"
                  vni_number      — VNI (globally consistent across all switches)
                  reserved_vlan_id, vn_ipv4_enabled, ipv4_subnet — VN-level IP config
                  ipv6_enabled, virtual_gateway_ipv4, l3_mtu — L3 parameters
              - count: total number of VN instances returned

            When querying all instances:
              - instance: "all"
              - blueprint_id, system_id: as above
              - results: list of per-instance result objects (same shape as above)
              - total_count: sum of VN instances across all instances
        """
        return await handle_get_virtual_networks(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            system_id,
            instance_name,
        )

    @mcp.tool()
    async def get_virtual_networks(
        blueprint_id: str,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns all virtual networks configured in a blueprint, joined with
        their parent routing zone (VRF).

        This describes the design intent — one row per VN regardless of which
        switches it is deployed to. Use get_vn_deployments if you need to know
        which switches carry a VN and what local VLAN ID is assigned on each.

        Data source: graph database (graph_client). The graph is automatically
        rebuilt if the blueprint version has changed since the last query.

        Use get_blueprints to discover valid blueprint_id values.

        Args:
            blueprint_id:  The Apstra blueprint ID to query.
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - virtual_networks: list of VN objects, each with:
                  id, label         — VN identity
                  vn_type           — "vxlan" or "vlan"
                  vni_number        — VNI string (None for VLAN-only)
                  reserved_vlan_id  — blueprint-level reserved VLAN
                  ipv4_enabled, ipv4_subnet — IPv4 configuration
                  ipv6_enabled, ipv6_subnet — IPv6 configuration (null if not configured)
                  virtual_gateway_ipv4, virtual_gateway_ipv4_enabled — anycast GW
                  virtual_gateway_ipv6, virtual_gateway_ipv6_enabled — anycast IPv6 GW
                  virtual_mac       — virtual MAC (null if not configured)
                  l3_mtu            — L3 MTU
                  description, tags — metadata (null if not configured)
                  routing_zone_label, vrf_name, routing_zone_type — parent VRF/SZ
              - count: total number of VNs

            When querying all instances:
              - instance: "all"
              - blueprint_id: the blueprint queried
              - results: list of per-instance result objects (same shape as above)
              - total_count: sum of VNs across all instances

        Note: Fields that are null in a given Apstra version (e.g. ipv6_subnet
        on environments without IPv6) are always present in the output with a
        None value. They are included explicitly so that the contract is stable
        and new Apstra versions that populate them are captured automatically.
        """
        return await handle_get_virtual_network_list(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            instance_name,
        )

    @mcp.tool()
    async def get_routing_zones(
        blueprint_id: str,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns all routing zones (VRFs / security zones) configured in a
        blueprint, along with the number of virtual networks assigned to each.

        In Apstra, every virtual network belongs to exactly one routing zone.
        Routing zones map directly to VRFs on the switches. This tool gives a
        summary view useful for understanding VRF inventory before drilling into
        a specific zone with get_routing_zone_detail.

        Data source: graph database (graph_client). The graph is automatically
        rebuilt if the blueprint version has changed since the last query.

        Use get_blueprints to discover valid blueprint_id values.

        Args:
            blueprint_id:  The Apstra blueprint ID to query.
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - routing_zones: list of routing zone objects, each with:
                  id        — security_zone graph node ID
                  label     — human-readable routing zone name
                  vrf_name  — VRF name used in rendered device config
                  sz_type   — "l3_fabric" (default) or "evpn" (tenant VRF)
                  vn_count  — number of virtual networks in this zone
              - count: total number of routing zones

            When querying all instances:
              - instance: "all"
              - blueprint_id: the blueprint queried
              - results: list of per-instance result objects (same shape as above)
              - total_count: sum of routing zones across all instances
        """
        return await handle_get_routing_zones(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            instance_name,
        )

    @mcp.tool()
    async def get_routing_zone_detail(
        blueprint_id: str,
        routing_zone: str,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns full detail for a single routing zone (VRF / security zone),
        including all member virtual networks and the switches that host them.

        A routing zone corresponds to one VRF on every leaf switch in the
        fabric. This tool surfaces the zone topology: which VNs live in the
        zone, and which switches carry at least one VN from the zone.

        Data source: graph database (graph_client). The graph is automatically
        rebuilt if the blueprint version has changed since the last query.

        Use get_routing_zones to list available routing zones and
        get_blueprints to discover valid blueprint_id values.

        Args:
            blueprint_id:  The Apstra blueprint ID to query.
            routing_zone:  The routing zone label or VRF name to look up.
                           Both are accepted (e.g. "Production" or "prod_vrf").
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - routing_zone: the routing_zone argument passed in
              - detail: routing zone detail object (null if not found), with:
                  id, label, vrf_name, sz_type — zone identity
                  member_virtual_networks — list of VNs with:
                      vn_id, vn_label, vn_type, vni_number,
                      ipv4_enabled, ipv4_subnet, ipv6_enabled, ipv6_subnet,
                      virtual_gateway_ipv4, l3_mtu, description, tags
                  member_systems — list of switches carrying any VN in this zone:
                      sw_id, sw_label, sw_role
                  vn_count    — number of member VNs
                  system_count — number of member switches
              - error: present if the routing zone was not found or a query
                error occurred

            When querying all instances:
              - instance: "all"
              - blueprint_id, routing_zone: as above
              - results: list of per-instance result objects (same shape as above)
        """
        return await handle_get_routing_zone_detail(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            routing_zone,
            instance_name,
        )

    @mcp.tool()
    async def get_virtual_network_detail(
        blueprint_id: str,
        virtual_network: str,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns full configuration detail for a single virtual network,
        including its parent routing zone and the list of switches it is
        deployed on with each switch's local VLAN assignment.

        Use get_virtual_networks to list available VNs by name, and
        get_vn_deployments if you only need the per-switch VLAN/VNI state
        without the design-level L3/gateway configuration.

        Data source: graph database (graph_client). The graph is automatically
        rebuilt if the blueprint version has changed since the last query.

        Args:
            blueprint_id:    The Apstra blueprint ID to query.
            virtual_network: The virtual network label or graph node ID.
                             Both are accepted (e.g. "Hypervisor_Dev").
            instance_name:   Optional. The name of the Apstra instance to query
                             (as defined in instances.yaml). If omitted, all
                             instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - virtual_network: the virtual_network argument passed in
              - detail: VN detail object (null if not found), with:
                  id, label         — VN identity
                  vn_type           — "vxlan" or "vlan"
                  vni_number        — VNI string (None for VLAN-only)
                  reserved_vlan_id  — blueprint-level reserved VLAN
                  ipv4_enabled, ipv4_subnet
                  ipv6_enabled, ipv6_subnet
                  virtual_gateway_ipv4, virtual_gateway_ipv4_enabled
                  virtual_gateway_ipv6, virtual_gateway_ipv6_enabled
                  virtual_mac, l3_mtu, description, tags
                  routing_zone — parent SZ info, or null if unassigned:
                      sz_id, routing_zone_label, vrf_name, routing_zone_type
                  deployed_on — list of switches carrying this VN:
                      sw_id, sw_label, sw_role,
                      vni_id, vlan_id,
                      ipv4_enabled, ipv4_mode, dhcp_enabled
                  deployed_count — number of switches in deployed_on
              - error: present if the virtual network was not found or a query
                error occurred

            When querying all instances:
              - instance: "all"
              - blueprint_id, virtual_network: as above
              - results: list of per-instance result objects (same shape as above)
        """
        return await handle_get_virtual_network_detail(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            virtual_network,
            instance_name,
        )
