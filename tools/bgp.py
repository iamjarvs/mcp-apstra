from fastmcp import Context

from handlers.bgp import handle_get_external_peerings, handle_get_fabric_peerings


def register(mcp):

    @mcp.tool()
    async def get_external_blueprint_peerings(
        blueprint_id: str,
        device: str = None,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns BGP peerings between Apstra-managed fabric devices and external
        systems — routers, firewalls, servers, or any device that is not owned
        or configured by this Apstra blueprint.

        This is explicitly NOT intra-fabric BGP (e.g. spine-leaf underlay or
        leaf-leaf EVPN sessions). Both Cypher queries filter on
        system.external = true for the remote peer, so only peerings that exit
        the fabric boundary are returned.

        This reflects design intent from the blueprint graph, not live BGP
        neighbour state. Use this to understand what external sessions are
        configured and from which fabric edge devices — not whether they are
        currently established.

        Data source: graph database (graph_client). The graph is automatically
        rebuilt if the blueprint version has changed since the last query.

        Use get_blueprints to discover valid blueprint_id values and
        get_systems to discover valid device hostnames.

        Args:
            blueprint_id:  The Apstra blueprint ID to query.
            device:        Optional. Hostname or label of a specific fabric
                           device to scope the query (e.g. "Leaf3"). If
                           omitted, all external peerings in the blueprint are
                           returned. The local side is always the named fabric
                           device; the remote side is always the external peer.
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - device: the device filter used (or None for all fabric devices)
              - peerings: list of external peering objects, each with:
                  session_id       — protocol_session graph node ID
                  bfd              — BFD enabled flag
                  ipv4_safi        — "enabled" or "disabled"
                  ipv6_safi        — "enabled" or "disabled"
                  ttl              — BGP TTL (2 = eBGP single-hop typical)
                  local            — fabric side: hostname, role, serial,
                                     external (always False), interface,
                                     subinterface, ip_address, vlan_id, local_asn
                  remote           — external peer: hostname, role, serial
                                     (null if unmanaged), external (always True),
                                     interface (null if unmanaged), subinterface
                                     (null if unmanaged), ip_address, vlan_id,
                                     local_asn
              - count: total number of external peerings returned

            When querying all instances:
              - instance: "all"
              - blueprint_id, device: as above
              - results: list of per-instance result objects
              - total_count: sum of peerings across all instances
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
        blueprint_id: str,
        device: str = None,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns intra-fabric eBGP peerings between Apstra-managed devices —
        spine-leaf underlay sessions, ESI peer links, and any other eBGP
        session where both peers are managed by this blueprint.

        This is explicitly NOT external BGP (fabric-to-outside). Both Cypher
        queries filter on system.external = false for both peers, so only
        sessions that stay inside the fabric boundary are returned. Use
        get_external_blueprint_peerings for sessions to external systems.

        Data source: graph database (graph_client). The graph is automatically
        rebuilt if the blueprint version has changed since the last query.

        Use get_blueprints to discover valid blueprint_id values and
        get_systems to discover valid device hostnames.

        Args:
            blueprint_id:  The Apstra blueprint ID to query.
            device:        Optional. Hostname or label of a specific fabric
                           device to anchor the query (e.g. "Leaf2"). If
                           omitted, all intra-fabric peerings are returned
                           deduplicated (each session appears once).
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - device: the device filter used (or None for all peerings)
              - peerings: list of fabric peering objects, each with:
                  link_id    — link graph node ID
                  link_role  — link role (e.g. "spine_leaf", "leaf_peer_link")
                  link_speed — link speed (e.g. "1G", "10G")
                  a_side     — one endpoint: hostname, role, serial, asn,
                               interface, description, ip_address, l3_mtu
                  b_side     — the other endpoint: same fields as a_side
              - count: total number of fabric peerings returned

            When querying all instances:
              - instance: "all"
              - blueprint_id, device: as above
              - results: list of per-instance result objects
              - total_count: sum of peerings across all instances
        """
        return await handle_get_fabric_peerings(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            device,
            instance_name,
        )
