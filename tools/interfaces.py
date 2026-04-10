from fastmcp import Context

from handlers.interfaces import handle_get_interface_list


def register(mcp):

    @mcp.tool()
    async def get_interface_list(
        blueprint_id: str,
        system_id: str,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns all interfaces for a specific switch in a blueprint.

        Traverses the hosted_interfaces graph relationship from the system
        node to its interface nodes. Returns one entry per interface
        regardless of type.

        Data source: graph database (graph_client). Results reflect the
        design intent as stored in the Apstra blueprint graph. The graph is
        automatically rebuilt if the blueprint version has changed since the
        last query.

        Note: interface speed is not stored on the interface node in the
        Apstra graph — speed is a property of the physical link and can be
        seen via the description field (which encodes the connected peer in
        the form "facing_<hostname>:<port>") or via the device profile.

        Use get_blueprints to discover valid blueprint_id values.
        Use get_systems to discover valid system_id values (the system_id
        field, which is the hardware chassis serial — NOT the graph node id).

        Args:
            blueprint_id:  The Apstra blueprint ID to query.
            system_id:     The hardware chassis serial of the switch (e.g.
                           "525400AA7236"). This is the system_id field
                           returned by get_systems, not the graph node id.
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance:     name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - system_id:    hardware serial of the switch queried
              - interfaces:   list of interface objects, each with:
                  id               — graph node ID
                  if_name          — interface name (e.g. ge-0/0/0, ae1,
                                     lo0.0)
                  if_type          — type: ethernet, ip, port_channel,
                                     loopback, svi, subinterface,
                                     unicast_vtep, anycast_vtep,
                                     logical_vtep, global_anycast_vtep
                  description      — connected peer description
                                     (e.g. "facing_spine1:ge-0/0/2", or
                                     "to.pod1-hpe-vme-essentials:eth1").
                                     Null for internal/virtual interfaces.
                  operation_state  — up / admin_down / deduced_down
                  ipv4_addr        — IPv4 address with prefix length
                                     (e.g. "192.168.0.5/31"), or null
                  ipv6_addr        — IPv6 address with prefix, or null
                  l3_mtu           — L3 MTU in bytes, or null
                  lag_mode         — lacp_active / lacp_passive / static_lag
                                     for port-channels, or null
                  port_channel_id  — integer bundle ID (port-channels only),
                                     or null
                  loopback_id      — integer loopback index (loopbacks only),
                                     or null
                  protocols        — routing protocol on this interface
                                     (e.g. "ebgp"), or null
                  mode             — trunk / access, or null
                  vlan_id          — subinterface VLAN ID, or null
              - count: total number of interfaces returned

            When querying all instances:
              - instance: "all"
              - blueprint_id, system_id: as above
              - results: list of per-instance result objects (same shape)
              - total_count: sum of interface counts across all instances
        """
        return await handle_get_interface_list(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            system_id,
            instance_name,
        )
