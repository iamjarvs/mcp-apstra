from fastmcp import Context

from handlers.links import handle_get_link_list


def register(mcp):

    @mcp.tool()
    async def get_link_list(
        blueprint_id: str,
        system_id: str = None,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns physical links in a blueprint, with endpoints, link type,
        role, and speed.

        When system_id is supplied, only links connected to that system are
        returned. When omitted, all links in the fabric are returned — useful
        for capacity planning and verifying redundancy across the whole fabric.

        Each link object contains a local_interface and remote_interface
        describing both endpoints. The description field on each interface
        encodes the connected peer in the form "facing_<hostname>:<port>"
        (fabric links) or "to.<peer>" (server/host-facing links).

        Data source: graph database (graph_client).

        Use get_blueprints to discover valid blueprint_id values.
        Use get_systems to discover valid system_id values (the system_id
        field — hardware chassis serial — NOT the graph node id).

        Args:
            blueprint_id:  The Apstra blueprint ID to query.
            system_id:     Optional. Hardware chassis serial of a specific
                           switch (e.g. "525400AA7236"). When provided, only
                           links with one endpoint on this system are returned.
                           When omitted, all fabric links are returned.
            instance_name: Optional. The name of the Apstra instance to query
                           (as defined in instances.yaml). If omitted, all
                           instances are queried and results are merged.

        Returns:
            When querying a single instance:
              - instance:     name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - system_id:    hardware serial filter applied (or null)
              - links: list of link objects, each with:
                  link_id          — Apstra link ID encoding both endpoint
                                     labels (e.g.
                                     "spine1<->_single_rack_001_leaf1[1]")
                  link_type        — ethernet / aggregate_link / logical_link
                  role             — fabric role of the link:
                                     spine_leaf, spine_superspine,
                                     leaf_l2_server, leaf_l3_server,
                                     to_generic, leaf_access, to_external_router,
                                     leaf_leaf, leaf_peer_link, etc.
                  speed            — link speed string (e.g. "10G", "100G",
                                     "25G") or null if not set
                  deploy_mode      — deploy (normal) / drain (maintenance)
                  group_label      — link group label, or null
                  local_interface  — interface on the queried system side
                                     (or lower-id side for fabric-wide queries):
                                       id, if_name, if_type, description,
                                       operation_state, ipv4_addr, ipv6_addr,
                                       lag_mode, port_channel_id
                  remote_interface — interface on the far-end side (same shape)
              - count: total number of links returned

            When querying all instances:
              - instance: "all"
              - blueprint_id, system_id: as above
              - results: list of per-instance result objects (same shape)
              - total_count: sum of link counts across all instances
        """
        return await handle_get_link_list(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            system_id,
            instance_name,
        )
