# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

# Nested sections in the system config context that are omitted by default
# because they can be large. The LLM must request them explicitly via the
# include_sections parameter. Documented so the tool docstring can describe
# each one accurately.
SYSTEM_CONTEXT_SECTIONS = {
    "device_capabilities": (
        "Hardware capability flags: copp_strict, breakout_capable (dict of "
        "breakout-capable port groups), as_seq_num_supported."
    ),
    "dhcp_servers": (
        "DHCP relay config keyed by VRF name. Each entry lists dhcp_servers, "
        "dhcpv6_servers, source IP/interface, and vrf_name."
    ),
    "interface": (
        "Per-interface L2/L3 config keyed by 'IF-<name>' (e.g. IF-ge-0/0/0). "
        "Each entry includes role, switch_port_mode, allowed_vlans, mlag_id, "
        "vrf_name, MTU, operation_state, dot1x, OSPF, and LAG settings. "
        "This section is large — one entry per physical port on the device."
    ),
    "ip": (
        "Per-interface IP configuration keyed by 'IP-<name>' (e.g. IP-ge-0/0/0). "
        "Each entry includes ipv4_address, ipv4_prefixlen, ipv6_address, vrf_name, "
        "subinterfaces, and OSPF settings. One entry per physical port."
    ),
    "portSetting": (
        "Port speed settings keyed by interface name (e.g. ge-0/0/0). Each entry "
        "has global.speed, interface.speed, and state ('active'). Only present for "
        "ports where a non-default speed is configured."
    ),
    "bgpService": (
        "BGP service config for this device: asn, router_id, overlay_protocol "
        "(evpn/static), ipv6_support, max route limits (evpn/mlag/external/fabric), "
        "evpn_uses_mac_vrf, default_fabric_evi_route_target, vtep_addressing."
    ),
    "ospf_services": (
        "OSPF service config keyed by process ID. Typically empty on fabrics "
        "that use pure BGP underlay. Each entry has area, process_id, and "
        "per-interface OSPF parameters."
    ),
    "bgp_sessions": (
        "All BGP sessions on this device keyed by session name "
        "('<src_ip>_<src_asn>-><dst_ip>_<dst_asn>_<vrf>'). Each entry has "
        "source_asn, source_ip, dest_asn, dest_ip, address_families (ipv4/ipv6/evpn), "
        "vrf_name, route_map_in/out, bfd, and session_type."
    ),
    "routing": (
        "Routing policy config: prefix_lists, ipv6_prefix_lists, aspath_lists, "
        "route_maps (keyed by name, with sequence/action/custom_actions), "
        "community_lists, static_routes, bgp_aggregates. Also includes flags: "
        "has_l3edge, has_evpn_gw, has_dynamic_bgp_neighbor."
    ),
    "vlan": (
        "VLAN config keyed by VLAN ID. Typically empty on routed (IP fabric) "
        "designs. Populated on VLAN-mode or hybrid fabrics."
    ),
    "configlets": (
        "User-defined configlets applied to this device, keyed by scope "
        "('system', 'interface', etc.). Each entry is a list of configlet "
        "objects with display_name, templateText (rendered config lines), "
        "negationTemplateText, and renderStyle."
    ),
    "vxlan": (
        "VXLAN config (VNI-to-VLAN mappings). Typically empty at the system "
        "context level — VXLAN is usually expressed through security_zones and "
        "vlan sections."
    ),
    "security_zones": (
        "VRF/routing zone config keyed by vrf_name. Each entry has vlan_id, "
        "vni_id, sz_type (l3_fabric/evpn), import/export route targets, rd, "
        "loopback_intf, loopback IP (IPv4/IPv6), and EVPN IRB mode."
    ),
    "loopbacks": (
        "Loopback interface config keyed by interface name. Each entry has "
        "IP address, VRF, and protocol settings. Usually empty if loopbacks "
        "are expressed inline in security_zones."
    ),
    "access_lists": (
        "ACL definitions keyed by name. Each entry has direction, VRF, and "
        "match/action terms. Typically empty unless explicit ACLs are configured."
    ),
    "dot1x_config": (
        "802.1X authentication config. Typically empty on data centre fabrics "
        "that do not use port-based access control."
    ),
    "aaa_servers": (
        "AAA (RADIUS/TACACS) server config. Typically empty unless centralised "
        "authentication is configured on this device."
    ),
    "fabric_policy": (
        "Fabric-wide policy as applied to this device: overlay_control_protocol, "
        "max route limits, external_router_mtu, default_fabric_evi_route_target, "
        "EVPN type-5 settings, Junos-specific flags (graceful_restart, ex_overlay_ecmp, "
        "evpn_routing_instance_type), default_svi_l3_mtu, default_anycast_gw_mac."
    ),
    "evpn_interconnect": (
        "EVPN interconnect config for multi-pod or multi-DC deployments. "
        "Typically empty in single-pod fabrics."
    ),
    "load_balancing_policy": (
        "ECMP load balancing policy config. Typically empty when the default "
        "fabric load balancing settings are in use."
    ),
    "property_sets": (
        "User-defined property set values applied to this device, keyed by "
        "property name. These are arbitrary key-value pairs injected into "
        "configlet templates (e.g. collector_ip, snmpv2_community)."
    ),
}

def parse_systems(rows: list) -> list:
    """
    Normalises a list of raw Kuzu query rows for switch systems.

    Strips the 'sw.' column prefix returned by Kuzu and returns a clean
    flat list of dicts. Each item contains: id, label, role, system_id,
    system_type, hostname, deploy_mode, management_level, external,
    group_label.
    """
    return [
        {
            "id": row.get("sw.id", "unknown"),
            "label": row.get("sw.label", "unknown"),
            "role": row.get("sw.role", "unknown"),
            "system_id": row.get("sw.system_id"),
            "system_type": row.get("sw.system_type", "unknown"),
            "hostname": row.get("sw.hostname"),
            "deploy_mode": row.get("sw.deploy_mode", "unknown"),
            "management_level": row.get("sw.management_level", "unknown"),
            "external": row.get("sw.external", False),
            "group_label": row.get("sw.group_label"),
        }
        for row in rows
    ]


def parse_blueprints(raw: dict) -> list:
    """
    Normalises a raw Apstra blueprints API response into a flat list of dicts.

    Fields returned per blueprint:
      id, label, design, status, version, last_modified_at,
      has_uncommitted_changes, build_errors_count, build_warnings_count,
      anomaly_counts (dict keyed by anomaly type, plus 'all' total),
      topology (spine_count, leaf_count, rack_count, security_zone_count,
                virtual_network_count, generic_count)
    """
    items = raw.get("items", [])
    return [
        {
            "id": item.get("id", "unknown"),
            "label": item.get("label", "unknown"),
            "design": item.get("design", "unknown"),
            "status": item.get("status", "unknown"),
            "version": item.get("version"),
            "last_modified_at": item.get("last_modified_at"),
            "has_uncommitted_changes": item.get("has_uncommitted_changes", False),
            "build_errors_count": item.get("build_errors_count", 0),
            "build_warnings_count": item.get("build_warnings_count", 0),
            "anomaly_counts": item.get("anomaly_counts", {}),
            "topology": {
                "spine_count": item.get("spine_count", 0),
                "leaf_count": item.get("leaf_count", 0),
                "rack_count": item.get("rack_count", 0),
                "security_zone_count": item.get("security_zone_count", 0),
                "virtual_network_count": item.get("virtual_network_count", 0),
                "generic_count": item.get("generic_count", 0),
            },
        }
        for item in items
    ]


def parse_anomalies(raw: dict) -> list:
    """
    Normalises a raw Apstra anomaly API response into a flat list of dicts.
    Each item contains: severity, type, description, affected_node.
    """
    items = raw.get("items", [])
    return [
        {
            "severity": item.get("severity", "unknown"),
            "type": item.get("anomaly_type", "unknown"),
            "description": item.get("description", ""),
            "affected_node": item.get("system_id", "unknown"),
        }
        for item in items
    ]


def parse_virtual_networks(rows: list) -> list:
    """
    Normalises a list of raw Kuzu query rows for virtual network instances.

    Each row represents one VN instance on one switch. The same VNI (vni_number)
    may appear for multiple switches with a different vlan_id on each — this is
    expected VXLAN behaviour and is the key data point this parser preserves.

    Strips the 'sw.', 'vni.', and 'vn.' column prefixes returned by Kuzu.

    Fields returned per row:
      sw_id, sw_label — switch graph node ID and human-readable label
      vni_id          — VN-instance graph node ID (unique per switch/VN pair)
      vlan_id         — local VLAN assignment on this switch (may differ per switch)
      ipv4_enabled    — per-instance IPv4 flag
      ipv4_mode       — per-instance IPv4 mode (e.g. "enabled")
      dhcp_enabled    — per-instance DHCP relay flag
      vn_id           — virtual_network graph node ID
      vn_label        — human-readable VN name
      vn_type         — network type, typically "vxlan"
      vni_number      — VNI (globally consistent across switches, stored as string)
      reserved_vlan_id — VN-level reserved VLAN (may differ from vlan_id)
      vn_ipv4_enabled — VN-level IPv4 flag
      ipv4_subnet     — VN IPv4 subnet
      ipv6_enabled    — VN IPv6 flag
      virtual_gateway_ipv4 — anycast gateway IP
      l3_mtu          — L3 MTU for this VN
    """
    return [
        {
            "sw_id": row.get("sw.id"),
            "sw_label": row.get("sw.label"),
            "vni_id": row.get("vni.id"),
            "vlan_id": row.get("vni.vlan_id"),
            "ipv4_enabled": row.get("vni.ipv4_enabled", False),
            "ipv4_mode": row.get("vni.ipv4_mode"),
            "dhcp_enabled": row.get("vni.dhcp_enabled", False),
            "vn_id": row.get("vn.id"),
            "vn_label": row.get("vn.label"),
            "vn_type": row.get("vn.vn_type"),
            "vni_number": row.get("vn.vn_id"),
            "reserved_vlan_id": row.get("vn.reserved_vlan_id"),
            "vn_ipv4_enabled": row.get("vn.ipv4_enabled", False),
            "ipv4_subnet": row.get("vn.ipv4_subnet"),
            "ipv6_enabled": row.get("vn.ipv6_enabled", False),
            "virtual_gateway_ipv4": row.get("vn.virtual_gateway_ipv4"),
            "l3_mtu": row.get("vn.l3_mtu"),
        }
        for row in rows
    ]


def parse_virtual_network_list(rows: list) -> list:
    """
    Normalises Kuzu query rows from the virtual_network + security_zone join
    into a flat list of VN configuration objects.

    Each row represents one virtual network as designed in the blueprint,
    joined to its parent routing zone (security zone). Unlike parse_virtual_networks,
    which returns one row per VN-instance per switch (deployment state), this
    returns one row per VN in the design.

    The Cypher query uses OPTIONAL MATCH for the security_zone join so VNs not
    assigned to a routing zone will still be returned with null sz fields.

    Fields returned per row:
      id, label            — VN graph node ID and human-readable name
      vn_type              — "vxlan" or "vlan"
      vni_number           — VNI (string), or None for VLAN-only networks
      reserved_vlan_id     — blueprint-level reserved VLAN
      ipv4_enabled         — VN-level IPv4 flag
      ipv4_subnet          — VN IPv4 subnet, or None
      ipv6_enabled         — VN-level IPv6 flag
      ipv6_subnet          — VN IPv6 subnet (null if not configured or older Apstra)
      virtual_gateway_ipv4         — anycast gateway IPv4
      virtual_gateway_ipv4_enabled — anycast gateway IPv4 enabled flag
      virtual_gateway_ipv6         — anycast gateway IPv6 (null if not configured)
      virtual_gateway_ipv6_enabled — anycast gateway IPv6 enabled flag
      virtual_mac          — virtual MAC address (null if not configured)
      l3_mtu               — L3 MTU
      description          — VN description (null if not configured)
      tags                 — tags (null if not configured)
      routing_zone_label   — parent routing zone name (null if unassigned)
      vrf_name             — VRF name from the routing zone
      routing_zone_type    — routing zone type (e.g. "evpn")

    Note: fields that are null in a given Apstra version (e.g. ipv6_subnet on
    environments without IPv6) will be present in the output with a None value.
    They are included in the query explicitly so that the contract is clear and
    new Apstra versions that populate them will be captured automatically once
    that Apstra environment is connected.
    """
    return [
        {
            "id": row.get("vn.id"),
            "label": row.get("vn.label"),
            "vn_type": row.get("vn.vn_type"),
            "vni_number": row.get("vn.vn_id"),
            "reserved_vlan_id": row.get("vn.reserved_vlan_id"),
            "ipv4_enabled": row.get("vn.ipv4_enabled", False),
            "ipv4_subnet": row.get("vn.ipv4_subnet"),
            "ipv6_enabled": row.get("vn.ipv6_enabled", False),
            "ipv6_subnet": row.get("vn.ipv6_subnet"),
            "virtual_gateway_ipv4": row.get("vn.virtual_gateway_ipv4"),
            "virtual_gateway_ipv4_enabled": row.get("vn.virtual_gateway_ipv4_enabled", False),
            "virtual_gateway_ipv6": row.get("vn.virtual_gateway_ipv6"),
            "virtual_gateway_ipv6_enabled": row.get("vn.virtual_gateway_ipv6_enabled", False),
            "virtual_mac": row.get("vn.virtual_mac"),
            "l3_mtu": row.get("vn.l3_mtu"),
            "description": row.get("vn.description"),
            "tags": row.get("vn.tags"),
            "routing_zone_label": row.get("routing_zone_label"),
            "vrf_name": row.get("vrf_name"),
            "routing_zone_type": row.get("routing_zone_type"),
        }
        for row in rows
    ]


def parse_external_peerings(rows: list) -> list:
    """
    Normalises Kuzu query rows for external BGP peerings into a flat list of
    peering dicts. External peerings are sessions between an Apstra-managed
    fabric device (system.external = false) and a system outside the blueprint
    (system.external = true), such as a router, firewall, server, or any device
    not owned or configured by this Apstra blueprint.

    This parser is NOT suitable for intra-fabric BGP sessions (e.g. spine-leaf
    underlay or iBGP). A separate parser will be added when that tool is built.

    Works with both the global (_PEERING_QUERY_ALL) and per-device
    (_PEERING_QUERY_DEVICE) Cypher queries in handlers/bgp.py — both use
    identical AS column aliases so one parser handles both.

    Each row uses explicit AS aliases (e.g. 'ps.id AS session_id') so there are
    no dotted key prefixes. This is distinct from parsers like parse_virtual_networks
    that consume un-aliased Kuzu column names such as 'sw.id'.

    Fields returned per peering:
      session_id                   — protocol_session graph node ID
      bfd                          — BFD enabled flag
      ipv4_safi                    — "enabled" or "disabled"
      ipv6_safi                    — "enabled" or "disabled"
      ttl                          — BGP TTL (typically 2 for eBGP single-hop)
      local.hostname               — fabric device hostname (always managed)
      local.role                   — leaf / spine / generic
      local.serial                 — hardware system_id
      local.external               — always False (fabric side)
      local.interface              — physical interface name (e.g. ge-0/0/2)
      local.interface_description  — interface description
      local.subinterface           — subinterface name (e.g. ge-0/0/2.50)
      local.ip_address             — IP address on the subinterface (CIDR)
      local.vlan_id                — VLAN tag on the subinterface
      local.local_asn              — locally configured ASN override (usually null)
      remote.hostname              — external peer hostname
      remote.role                  — role as modelled in blueprint (e.g. "generic")
      remote.serial                — null (unmanaged systems have no system_id)
      remote.external              — always True (external peer)
      remote.interface             — null (unmanaged systems have no if_name)
      remote.interface_description — description on the fabric-side phys interface
      remote.subinterface          — null (unmanaged)
      remote.ip_address            — peer IP address on its subinterface (CIDR)
      remote.vlan_id               — VLAN tag on the remote subinterface
      remote.local_asn             — locally configured ASN on the remote endpoint
    """
    return [
        {
            "session_id": row.get("session_id"),
            "bfd": row.get("bfd"),
            "ipv4_safi": row.get("ipv4_safi"),
            "ipv6_safi": row.get("ipv6_safi"),
            "ttl": row.get("ttl"),
            "local": {
                "hostname": row.get("local_hostname"),
                "role": row.get("local_role"),
                "serial": row.get("local_serial"),
                "external": row.get("local_external"),
                "interface": row.get("local_interface"),
                "interface_description": row.get("local_intf_description"),
                "subinterface": row.get("local_subinterface"),
                "ip_address": row.get("local_ip"),
                "vlan_id": row.get("local_vlan_id"),
                "local_asn": row.get("local_asn"),
            },
            "remote": {
                "hostname": row.get("remote_hostname"),
                "role": row.get("remote_role"),
                "serial": row.get("remote_serial"),
                "external": row.get("remote_external"),
                "interface": row.get("remote_interface"),
                "interface_description": row.get("remote_intf_description"),
                "subinterface": row.get("remote_subinterface"),
                "ip_address": row.get("remote_ip"),
                "vlan_id": row.get("remote_vlan_id"),
                "local_asn": row.get("remote_asn"),
            },
        }
        for row in rows
    ]


def parse_fabric_peerings(rows: list) -> list:
    """
    Normalises Kuzu query rows for intra-fabric BGP peerings into a flat list
    of link dicts.

    Intra-fabric peerings are eBGP sessions between two Apstra-managed devices
    (both system.external = false) — the spine-leaf underlay is the canonical
    example. Sessions are returned with both sides identified symmetrically as
    a_side and b_side.

    This parser consumes rows from RETURN * queries where each column is a full
    node object (sy_a, sy_b, int_a, int_b, link, asn_a, asn_b). This is
    distinct from parse_external_peerings which consumes rows with explicit AS
    aliases producing flat keys.

    Fields returned per peering:
      link_id             — link graph node ID
      link_role           — link role (e.g. "spine_leaf", "leaf_peer_link")
      link_speed          — link speed string (e.g. "1G", "10G")
      a_side.hostname     — fabric device hostname
      a_side.role         — device role (leaf, spine, etc.)
      a_side.serial       — hardware system_id (chassis serial)
      a_side.asn          — ASN number string from attached domain node
      a_side.interface    — physical interface name (e.g. "ge-0/0/0")
      a_side.description  — interface description
      a_side.ip_address   — IPv4 address with prefix (e.g. "192.168.0.3/31")
      a_side.l3_mtu       — L3 MTU on the interface
      b_side.*            — same fields for the remote side
    """
    result = []
    for row in rows:
        sy_a = row.get("sy_a") or {}
        sy_b = row.get("sy_b") or {}
        int_a = row.get("int_a") or {}
        int_b = row.get("int_b") or {}
        link = row.get("link") or {}
        asn_a = row.get("asn_a") or {}
        asn_b = row.get("asn_b") or {}
        result.append({
            "link_id": link.get("id"),
            "link_role": link.get("role"),
            "link_speed": link.get("speed"),
            "a_side": {
                "hostname": sy_a.get("hostname"),
                "role": sy_a.get("role"),
                "serial": sy_a.get("system_id"),
                "asn": asn_a.get("domain_id"),
                "interface": int_a.get("if_name"),
                "description": int_a.get("description"),
                "ip_address": int_a.get("ipv4_addr"),
                "l3_mtu": int_a.get("l3_mtu"),
            },
            "b_side": {
                "hostname": sy_b.get("hostname"),
                "role": sy_b.get("role"),
                "serial": sy_b.get("system_id"),
                "asn": asn_b.get("domain_id"),
                "interface": int_b.get("if_name"),
                "description": int_b.get("description"),
                "ip_address": int_b.get("ipv4_addr"),
                "l3_mtu": int_b.get("l3_mtu"),
            },
        })
    return result


def parse_system_context(raw: dict, include_sections: list[str] | None = None) -> dict:
    """
    Parses the Apstra system config-context API response.

    The API returns a single 'context' key whose value is a JSON-encoded
    string. This function decodes that string and returns a filtered view.

    By default only scalar root-level fields are returned (strings, numbers,
    booleans, None). Nested dicts and lists are large and omitted unless
    explicitly requested via include_sections.

    Args:
        raw:              The raw API response dict containing a 'context' key.
        include_sections: Optional list of top-level section names to include
                          in addition to the default scalar fields. Must be
                          keys from SYSTEM_CONTEXT_SECTIONS.

    Returns a flat dict of scalar fields, plus any explicitly requested
    sections appended as additional keys.

    Raises ValueError (via json.JSONDecodeError) if the 'context' string
    cannot be parsed as JSON.
    """
    import json as _json
    context_str = raw.get("context", "")
    ctx = _json.loads(context_str)

    # Include all root-level scalars (str, int, float, bool, None) by default.
    result = {
        k: v for k, v in ctx.items()
        if not isinstance(v, (dict, list))
    }

    if include_sections:
        for section in include_sections:
            if section in ctx:
                result[section] = ctx[section]

    return result


def parse_configlets(rows: list) -> list:
    """
    Normalises Kuzu RETURN * rows for configlet nodes.

    Each row has a single 'configlet' key whose value is the full node dict.
    The payload field is a JSON-encoded string containing the full configlet
    definition including the generators array (template text, config_style,
    render_style, section).

    Fields returned per item:
      id           — configlet graph node ID
      display_name — human-readable name (from decoded payload)
      condition    — Jinja2 device match expression (e.g. 'role in ["spine"]')
      generators   — list of generator objects, each with config_style,
                     render_style, section, template_text,
                     negation_template_text
    """
    import json as _json
    result = []
    for row in rows:
        node = row.get("configlet") or {}
        try:
            payload = _json.loads(node.get("payload") or "{}")
        except (ValueError, TypeError):
            payload = {}
        configlet_def = payload.get("configlet") or {}
        result.append({
            "id": node.get("id"),
            "display_name": configlet_def.get("display_name"),
            "condition": node.get("condition"),
            "generators": configlet_def.get("generators", []),
        })
    return result


def parse_property_sets(rows: list) -> list:
    """
    Normalises Kuzu RETURN * rows for property_set nodes.

    Each row has a single 'propset' key whose value is the full node dict.
    The payload field is a JSON-encoded string containing the property set
    definition including the values dict.

    Fields returned per item:
      id               — property set graph node ID
      display_name     — human-readable name (from decoded payload label)
      property_set_id  — short machine identifier (e.g. 'flow_data')
      stale            — whether the property set is stale (bool)
      values           — dict of key-value pairs defined in this property set
    """
    import json as _json
    result = []
    for row in rows:
        node = row.get("propset") or {}
        try:
            payload = _json.loads(node.get("payload") or "{}")
        except (ValueError, TypeError):
            payload = {}
        result.append({
            "id": node.get("id"),
            "display_name": payload.get("label"),
            "property_set_id": node.get("property_set_id"),
            "stale": node.get("stale", False),
            "values": payload.get("values", {}),
        })
    return result


def parse_design_configlets(raw: dict) -> list:
    """
    Normalises a raw GET /api/design/configlets API response.

    The design catalogue holds the authoritative (master) copies of configlets.
    When a configlet is applied to a blueprint, Apstra takes a copy —
    the blueprint copy can drift from the catalogue over time.

    Fields returned per item:
      id               — design catalogue ID (e.g. "flow_snmpv2")
      display_name     — human-readable configlet name
      ref_archs        — list of reference architectures this configlet targets
      generators       — list of generator objects, each with config_style,
                         section, template_text, negation_template_text,
                         render_style, filename
      created_at       — ISO 8601 creation timestamp
      last_modified_at — ISO 8601 last modified timestamp
    """
    return [
        {
            "id": item.get("id"),
            "display_name": item.get("display_name"),
            "ref_archs": item.get("ref_archs", []),
            "generators": item.get("generators", []),
            "created_at": item.get("created_at"),
            "last_modified_at": item.get("last_modified_at"),
        }
        for item in raw.get("items", [])
    ]


def parse_design_property_sets(raw: dict) -> list:
    """
    Normalises a raw GET /api/property-sets API response.

    The design catalogue holds the authoritative (master) copies of property
    sets. When a property set is applied to a blueprint, Apstra takes a copy
    — the blueprint copy can drift from the catalogue over time.

    Fields returned per item:
      id         — property set ID / machine identifier (e.g. "flow_data")
      label      — human-readable name
      values     — dict of key-value pairs defined in this property set
      created_at — ISO 8601 creation timestamp
      updated_at — ISO 8601 last updated timestamp
    """
    return [
        {
            "id": item.get("id"),
            "label": item.get("label"),
            "values": item.get("values", {}),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        }
        for item in raw.get("items", [])
    ]


def parse_interfaces(rows: list) -> list:
    """
    Normalises Kuzu RETURN * rows for interface nodes from the query:

        MATCH (sw:system {system_id: $system_id})
        -[:hosted_interfaces]->(intf:interface)
        RETURN *

    The interface node has most fields at the top level; the payload field
    is a JSON-encoded string that fills any gaps. Top-level fields take
    precedence over payload for performance (avoid redundant json.loads when
    the field is already promoted).

    Fields returned per item:
      id                — graph node ID
      if_name           — interface name (e.g. ge-0/0/0, ae1, lo0.0)
      if_type           — type enum: ethernet, ip, port_channel, loopback,
                          svi, subinterface, unicast_vtep, anycast_vtep,
                          logical_vtep, global_anycast_vtep
      description       — peer description (e.g. "facing_spine1:ge-0/0/2")
      operation_state   — up / admin_down / deduced_down
      ipv4_addr         — IPv4 address with prefix (e.g. "192.168.0.5/31"),
                          or null
      ipv6_addr         — IPv6 address with prefix, or null
      l3_mtu            — L3 MTU in bytes, or null
      lag_mode          — lacp_active / lacp_passive / static_lag (port
                          channels only), or null
      port_channel_id   — integer bundle ID for port-channels, or null
      loopback_id       — integer loopback index (loopbacks only), or null
      protocols         — routing protocol on this interface (e.g. "ebgp"),
                          or null
      mode              — trunk / access (access-facing interfaces), or null
      vlan_id           — subinterface VLAN ID, or null
    """
    import json as _json
    result = []
    for row in rows:
        node = row.get("intf") or {}
        try:
            payload = _json.loads(node.get("payload") or "{}")
        except (ValueError, TypeError):
            payload = {}

        def _get(field):
            # top-level node property first, fall back to decoded payload
            v = node.get(field)
            return v if v is not None else payload.get(field)

        result.append({
            "id":               _get("id"),
            "if_name":          _get("if_name"),
            "if_type":          _get("if_type"),
            "description":      _get("description"),
            "operation_state":  _get("operation_state"),
            "ipv4_addr":        _get("ipv4_addr"),
            "ipv6_addr":        _get("ipv6_addr"),
            "l3_mtu":           _get("l3_mtu"),
            "lag_mode":         _get("lag_mode"),
            "port_channel_id":  _get("port_channel_id"),
            "loopback_id":      _get("loopback_id"),
            "protocols":        _get("protocols"),
            "mode":             _get("mode"),
            "vlan_id":          _get("vlan_id"),
        })
    return result


def parse_links(rows: list) -> list:
    """
    Normalises Kuzu rows for link queries of the form:

        RETURN local_intf, link, remote_intf

    Each row has three keys:
      - local_intf  — interface node dict for the local endpoint
      - link        — link node dict (speed, link_type, role, deploy_mode)
      - remote_intf — interface node dict for the remote endpoint

    The interface sub-objects use the same field extraction as
    parse_interfaces: top-level node properties take precedence over the
    JSON-encoded payload field.

    Fields returned per link:
      link_id          — Apstra link graph node ID (encodes endpoint labels,
                         e.g. "spine1<->_single_rack_001_leaf1[1]")
      link_type        — ethernet / aggregate_link / logical_link
      role             — fabric role: spine_leaf, leaf_l2_server,
                         to_generic, leaf_access, etc.
      speed            — link speed string: "10G", "100G", "25G", etc.
                         or null when not set in the blueprint
      deploy_mode      — deploy (normal) or drain (maintenance)
      group_label      — optional link group label, or null
      local_interface  — interface object for the local (queried) side:
                           id, if_name, if_type, description,
                           operation_state, ipv4_addr, lag_mode,
                           port_channel_id
      remote_interface — interface object for the far-end side (same shape)
    """
    import json as _json

    def _extract_intf(node):
        node = node or {}
        try:
            payload = _json.loads(node.get("payload") or "{}")
        except (ValueError, TypeError):
            payload = {}

        def _get(field):
            v = node.get(field)
            return v if v is not None else payload.get(field)

        return {
            "id":              _get("id"),
            "if_name":         _get("if_name"),
            "if_type":         _get("if_type"),
            "description":     _get("description"),
            "operation_state": _get("operation_state"),
            "ipv4_addr":       _get("ipv4_addr"),
            "ipv6_addr":       _get("ipv6_addr"),
            "lag_mode":        _get("lag_mode"),
            "port_channel_id": _get("port_channel_id"),
        }

    result = []
    for row in rows:
        link_node = row.get("link") or {}
        result.append({
            "link_id":          link_node.get("id"),
            "link_type":        link_node.get("link_type"),
            "role":             link_node.get("role"),
            "speed":            link_node.get("speed"),
            "deploy_mode":      link_node.get("deploy_mode"),
            "group_label":      link_node.get("group_label"),
            "local_interface":  _extract_intf(row.get("local_intf")),
            "remote_interface": _extract_intf(row.get("remote_intf")),
        })
    return result


# ---------------------------------------------------------------------------
# Config rendering parser
# ---------------------------------------------------------------------------

_CONFIGLET_SEPARATOR = "------BEGIN SECTION CONFIGLETS------"


import re as _re

_JUNOS_DIRECTIVE_RE = _re.compile(
    r'^(replace|delete|inactive|protect|apply-groups):\s*', _re.IGNORECASE
)


def _parse_junos_root_sections(text: str) -> dict[str, str]:
    """
    Parses a JunOS hierarchical config string into a dict mapping
    root-level section name → full block text (including braces).

    A root-level section is identified by a line at brace depth 0 that
    starts with a non-whitespace character and contains a '{'. Brace depth
    is tracked by counting '{' and '}' characters on each line to locate
    the matching closing brace.

    Section names are lowercased. Leading JunOS config directives
    (replace:, delete:, inactive:, protect:, apply-groups:) are stripped
    from section names (e.g. 'replace: interfaces {' → 'interfaces').

    Note: brace counting does not handle '{' or '}' inside quoted strings.
    This is safe for Apstra-generated configs, which do not embed literal
    braces in quoted values.
    """
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    depth = 0

    for line in text.splitlines():
        stripped = line.rstrip()
        open_ct = stripped.count('{')
        close_ct = stripped.count('}')

        if depth == 0:
            if stripped and '{' in stripped and not stripped[0].isspace():
                brace_idx = stripped.index('{')
                raw_name = _JUNOS_DIRECTIVE_RE.sub('', stripped[:brace_idx].strip().lower())
                current_name = raw_name
                current_lines = [stripped]
                depth += open_ct - close_ct
                if depth == 0:
                    sections[current_name] = "\n".join(current_lines)
                    current_name = None
                    current_lines = []
        else:
            current_lines.append(stripped)
            depth += open_ct - close_ct
            if depth == 0:
                sections[current_name] = "\n".join(current_lines)
                current_name = None
                current_lines = []

    if current_name is not None and current_lines:
        sections[current_name] = "\n".join(current_lines)

    return sections


def _extract_inner_content(block_text: str) -> str:
    """
    Strips the outer section wrapper from a JunOS block and dedents.

    Given a block like::

        interfaces {
            ge-0/0/0 { ... }
            lo0 { ... }
        }

    Returns the inner content with common leading whitespace removed::

        ge-0/0/0 { ... }
        lo0 { ... }

    Returns an empty string if the block has fewer than 3 lines or the
    inner content is blank.
    """
    lines = block_text.splitlines()
    if len(lines) < 3:
        return ""
    inner_lines = lines[1:-1]
    non_empty = [l for l in inner_lines if l.strip()]
    if not non_empty:
        return ""
    min_indent = min(len(l) - len(l.lstrip()) for l in non_empty)
    return "\n".join(l[min_indent:] if l.strip() else "" for l in inner_lines)


def parse_config_rendering(
    raw: dict,
    sections: list[str] | None = None,
    subsections: dict[str, list[str]] | None = None,
) -> dict:
    """
    Parses the rendered JunOS configuration from the Apstra config-rendering
    API response.

    The API returns a dict with a single 'config' key whose value is a flat
    JunOS hierarchical configuration string. This function:

      1. Splits the string on the '------BEGIN SECTION CONFIGLETS------'
         boundary to separate the AOS-managed config from user-defined
         configlets.
      2. Parses each half into a dict of root-level section name → text.
      3. Optionally filters both halves to only the requested section names.
      4. Optionally narrows specific sections to named child blocks.

    Args:
        raw:         The raw API response dict (must contain a 'config' key).
        sections:    Optional list of section names to include (e.g.
                     ["routing-options", "protocols"]). Applied to both the
                     main config and configlet parts. Pass None to return all.
        subsections: Optional dict mapping root section name → list of child
                     names to extract from within that section (e.g.
                     {"interfaces": ["ge-0/0/0", "lo0"],
                      "protocols": ["bgp"]}).
                     When a section is narrowed by this parameter it is
                     removed from 'sections' and its filtered child blocks
                     appear instead in 'subsection_detail'. Child names match
                     the keys in available_subsections.

    Returns a dict with:
        available_sections           — sorted list of all section names in
                                       the AOS-managed part of the config.
        available_configlet_sections — sorted list of all section names in
                                       the configlet part.
        available_subsections        — dict mapping each returned root section
                                       name to its sorted list of first-level
                                       child block names. Always populated so
                                       the caller can see what sub-elements
                                       exist before deciding to narrow.
        sections                     — {name: full_block_text} for root sections
                                       that were NOT narrowed via subsections.
        subsection_detail            — {section_name: {child_name: block_text}}
                                       for sections that WERE narrowed via the
                                       subsections parameter. Empty dict if no
                                       subsections filtering was requested.
        configlets                   — {name: full_block_text} for configlet
                                       config, filtered to sections if given.
    """
    config_text = raw.get("config", "")

    if _CONFIGLET_SEPARATOR in config_text:
        main_text, configlet_text = config_text.split(_CONFIGLET_SEPARATOR, 1)
    else:
        main_text = config_text
        configlet_text = ""

    main_sections = _parse_junos_root_sections(main_text)
    configlet_sections = _parse_junos_root_sections(configlet_text)

    available = sorted(main_sections)
    available_configlets = sorted(configlet_sections)

    if sections is not None:
        want = set(sections)
        filtered_main = {k: v for k, v in main_sections.items() if k in want}
        filtered_configlets = {k: v for k, v in configlet_sections.items() if k in want}
    else:
        filtered_main = main_sections
        filtered_configlets = configlet_sections

    # Always compute one-level-deep child names for every returned section.
    available_sub: dict[str, list[str]] = {}
    for name, text in filtered_main.items():
        children = _parse_junos_root_sections(_extract_inner_content(text))
        if children:
            available_sub[name] = sorted(children)

    # Apply subsections narrowing: move narrowed sections out of `sections`
    # and into `subsection_detail` so the caller only receives what it asked for.
    final_sections: dict[str, str] = {}
    subsection_detail: dict[str, dict[str, str]] = {}

    if subsections:
        narrow_keys = set(subsections)
        for name, text in filtered_main.items():
            if name in narrow_keys:
                children = _parse_junos_root_sections(_extract_inner_content(text))
                want_children = set(subsections[name])
                subsection_detail[name] = {
                    k: v for k, v in children.items() if k in want_children
                }
            else:
                final_sections[name] = text
    else:
        final_sections = filtered_main

    return {
        "available_sections": available,
        "available_configlet_sections": available_configlets,
        "available_subsections": available_sub,
        "sections": final_sections,
        "subsection_detail": subsection_detail,
        "configlets": filtered_configlets,
    }


# ---------------------------------------------------------------------------
# MTU parsers (used by handlers/mtu_check.py)
# ---------------------------------------------------------------------------

def parse_mtu_link_rows(rows: list) -> list:
    """
    Normalises rows from the MTU topology query:

        MATCH (sys_a:system)-[:hosted_interfaces]->(intf_a:interface)
          -[:link__rel]->(link:link)
          <-[:link__rel]-(intf_b:interface)<-[:hosted_interfaces]-(sys_b:system)
        WHERE intf_a.id < intf_b.id
        RETURN *

    Each row has five node-dict keys: sys_a, intf_a, link, intf_b, sys_b.

    Returns a list of link dicts, each with:
      link_id    — Apstra link graph node ID
      link_role  — spine_leaf / leaf_peer_link / to_generic / etc.
      speed      — link speed string (e.g. "1G", "10G") or null
      a_side     — {label, hostname, role, system_id, if_name, l3_mtu, ipv4_addr}
      b_side     — same shape for the far-end system and interface

    l3_mtu is the L3 (inet) MTU from the Apstra graph model. The physical
    frame MTU is NOT stored in the graph and must be obtained from the
    rendered JunOS config (parse_interface_mtus).
    """
    result = []
    for row in rows:
        sys_a = row.get("sys_a") or {}
        intf_a = row.get("intf_a") or {}
        link = row.get("link") or {}
        intf_b = row.get("intf_b") or {}
        sys_b = row.get("sys_b") or {}
        result.append({
            "link_id":   link.get("id"),
            "link_role": link.get("role"),
            "speed":     link.get("speed"),
            "a_side": {
                "label":     sys_a.get("label"),
                "hostname":  sys_a.get("hostname"),
                "role":      sys_a.get("role"),
                "system_id": sys_a.get("system_id"),
                "if_name":   intf_a.get("if_name"),
                "l3_mtu":    intf_a.get("l3_mtu"),
                "ipv4_addr": intf_a.get("ipv4_addr"),
            },
            "b_side": {
                "label":     sys_b.get("label"),
                "hostname":  sys_b.get("hostname"),
                "role":      sys_b.get("role"),
                "system_id": sys_b.get("system_id"),
                "if_name":   intf_b.get("if_name"),
                "l3_mtu":    intf_b.get("l3_mtu"),
                "ipv4_addr": intf_b.get("ipv4_addr"),
            },
        })
    return result


def parse_interface_mtus(interfaces_block: str) -> dict[str, dict]:
    """
    Extracts per-interface MTU values from a JunOS interfaces { ... } block.

    Uses _extract_inner_content to strip the outer wrapper then
    _parse_junos_root_sections to split into per-interface blocks. Within
    each block:

      - Physical (frame) MTU: the ``mtu N;`` statement that appears directly in
        the interface stanza BEFORE the first ``unit { }`` block. This is the
        L2 frame MTU; on JunOS it includes the Ethernet header overhead.
        Apstra standard: 9192 on fabric-facing physical interfaces.

      - Inet (L3) MTU: the ``mtu N;`` statement inside ``family inet { ... }``.
        This is the IP packet MTU. On JunOS, inet MTU + L2 overhead
        = physical MTU; Apstra standard: 9170 = 9192 - 22 (ETH+VLAN+FCS).

    Returns a dict keyed by interface name (after JunOS directive stripping,
    e.g. ``ge-0/0/0`` not ``replace: ge-0/0/0``). Values are:
      physical_mtu  — int or None
      inet_mtu      — int or None
      inet_address  — first IPv4 address found in family inet, or None
    """
    inner = _extract_inner_content(interfaces_block)
    if not inner:
        return {}

    child_blocks = _parse_junos_root_sections(inner)
    result = {}

    for intf_name, block in child_blocks.items():
        # Physical MTU: appears before the first "unit " stanza at depth 1.
        # We split the block at the first unit line to isolate the interface
        # header where physical mtu would appear.
        unit_m = _re.search(r'\n\s+unit\s+', block)
        header = block[:unit_m.start()] if unit_m else block

        phys_m = _re.search(r'^\s+mtu\s+(\d+)\s*;', header, _re.MULTILINE)
        physical_mtu = int(phys_m.group(1)) if phys_m else None

        # Inet MTU: inside "family inet { ... }".
        # family inet blocks on physical interfaces are simple (no nested {}),
        # so [^}]* is safe.
        inet_m = _re.search(r'family\s+inet\s*\{([^}]*)\}', block, _re.DOTALL)
        inet_mtu = None
        inet_address = None
        if inet_m:
            inet_content = inet_m.group(1)
            mtu_m = _re.search(r'\s+mtu\s+(\d+)\s*;', inet_content)
            if mtu_m:
                inet_mtu = int(mtu_m.group(1))
            addr_m = _re.search(r'address\s+([\d./]+)', inet_content)
            if addr_m:
                inet_address = addr_m.group(1)

        result[intf_name] = {
            "physical_mtu": physical_mtu,
            "inet_mtu":     inet_mtu,
            "inet_address": inet_address,
        }

    return result


def parse_routing_zones(rows: list) -> list:
    """
    Normalises Kuzu query rows for security_zone nodes into a flat list.

    Each row represents one routing zone (VRF/security zone) in the blueprint,
    with a count of how many virtual networks are members of that zone.

    Fields returned per row:
      id         — security_zone graph node ID
      label      — human-readable routing zone name
      vrf_name   — VRF name used in the rendered device config
      sz_type    — zone type: "l3_fabric" (default VRF) or "evpn" (tenant VRF)
      vn_count   — number of virtual networks assigned to this routing zone

    Note: The default L3 fabric zone (sz_type = "l3_fabric") holds the
    fabric-facing links and is present even when no tenant VRFs are configured.
    """
    return [
        {
            "id": row.get("sz.id"),
            "label": row.get("sz.label"),
            "vrf_name": row.get("sz.vrf_name"),
            "sz_type": row.get("sz.sz_type"),
            "vn_count": row.get("vn_count", 0),
        }
        for row in rows
    ]


def parse_routing_zone_detail(rows: list):
    """
    Normalises Kuzu query rows for a single security_zone detail query into a
    structured object, or returns None if the query returned no rows.

    The Cypher query returns one row per (VN, switch) combination so that
    vni_vlan_id can be captured. This parser aggregates those rows into:
      - Security zone metadata (id, label, vrf_name, sz_type)
      - member_virtual_networks  — list of unique VNs in this zone, each with:
          vn_id, vn_label, vn_type, vni_number, vn_ipv4_enabled, vn_ipv4_subnet,
          vn_ipv6_enabled, vn_ipv6_subnet, vn_gw_ipv4, vn_l3_mtu,
          vn_description, vn_tags
      - member_systems — list of unique switches that host at least one VN in
          this zone, each with: sw_id, sw_label, sw_role
      - vn_count     — number of unique VNs
      - system_count — number of unique switches

    Returns None if rows is empty (routing zone not found).
    """
    if not rows:
        return None

    first = rows[0]
    detail = {
        "id": first.get("sz.id"),
        "label": first.get("sz.label"),
        "vrf_name": first.get("sz.vrf_name"),
        "sz_type": first.get("sz.sz_type"),
    }

    seen_vns = {}    # vn_id → dict
    seen_sws = {}    # sw_id → dict

    for row in rows:
        vn_id = row.get("vn_id")
        if vn_id and vn_id not in seen_vns:
            seen_vns[vn_id] = {
                "vn_id": vn_id,
                "vn_label": row.get("vn_label"),
                "vn_type": row.get("vn_type"),
                "vni_number": row.get("vni_number"),
                "ipv4_enabled": row.get("vn_ipv4_enabled", False),
                "ipv4_subnet": row.get("vn_ipv4_subnet"),
                "ipv6_enabled": row.get("vn_ipv6_enabled", False),
                "ipv6_subnet": row.get("vn_ipv6_subnet"),
                "virtual_gateway_ipv4": row.get("vn_gw_ipv4"),
                "l3_mtu": row.get("vn_l3_mtu"),
                "description": row.get("vn_description"),
                "tags": row.get("vn_tags"),
            }

        sw_id = row.get("sw_id")
        if sw_id and sw_id not in seen_sws:
            seen_sws[sw_id] = {
                "sw_id": sw_id,
                "sw_label": row.get("sw_label"),
                "sw_role": row.get("sw_role"),
            }

    vns = sorted(seen_vns.values(), key=lambda v: v["vn_label"] or "")
    sws = sorted(seen_sws.values(), key=lambda s: s["sw_label"] or "")

    detail["member_virtual_networks"] = vns
    detail["member_systems"] = sws
    detail["vn_count"] = len(vns)
    detail["system_count"] = len(sws)
    return detail


def parse_virtual_network_detail(rows: list):
    """
    Normalises Kuzu query rows for a single virtual_network detail query into
    a structured object, or returns None if the query returned no rows.

    The Cypher query returns one row per switch that hosts the VN (via
    vn_instance nodes). This parser aggregates those rows into:
      - Full VN configuration (id, label, vn_type, vni_number, reserved_vlan_id,
          ipv4_enabled, ipv4_subnet, ipv6_enabled, ipv6_subnet,
          virtual_gateway_ipv4, virtual_gateway_ipv4_enabled,
          virtual_gateway_ipv6, virtual_gateway_ipv6_enabled,
          virtual_mac, l3_mtu, description, tags)
      - routing_zone — parent security zone info, or None if unassigned
          (sz_id, routing_zone_label, vrf_name, routing_zone_type)
      - deployed_on — list of switches carrying this VN, each with:
          sw_id, sw_label, sw_role, vni_id, vlan_id, ipv4_enabled,
          ipv4_mode, dhcp_enabled
      - deployed_count — number of switches in deployed_on

    Returns None if rows is empty (virtual network not found).
    """
    if not rows:
        return None

    first = rows[0]
    detail = {
        "id": first.get("vn.id"),
        "label": first.get("vn.label"),
        "vn_type": first.get("vn.vn_type"),
        "vni_number": first.get("vn.vn_id"),
        "reserved_vlan_id": first.get("vn.reserved_vlan_id"),
        "ipv4_enabled": first.get("vn.ipv4_enabled", False),
        "ipv4_subnet": first.get("vn.ipv4_subnet"),
        "ipv6_enabled": first.get("vn.ipv6_enabled", False),
        "ipv6_subnet": first.get("vn.ipv6_subnet"),
        "virtual_gateway_ipv4": first.get("vn.virtual_gateway_ipv4"),
        "virtual_gateway_ipv4_enabled": first.get("vn.virtual_gateway_ipv4_enabled", False),
        "virtual_gateway_ipv6": first.get("vn.virtual_gateway_ipv6"),
        "virtual_gateway_ipv6_enabled": first.get("vn.virtual_gateway_ipv6_enabled", False),
        "virtual_mac": first.get("vn.virtual_mac"),
        "l3_mtu": first.get("vn.l3_mtu"),
        "description": first.get("vn.description"),
        "tags": first.get("vn.tags"),
    }

    sz_id = first.get("sz_id")
    if sz_id:
        detail["routing_zone"] = {
            "sz_id": sz_id,
            "routing_zone_label": first.get("routing_zone_label"),
            "vrf_name": first.get("vrf_name"),
            "routing_zone_type": first.get("routing_zone_type"),
        }
    else:
        detail["routing_zone"] = None

    seen_sws = {}  # sw_id → dict

    for row in rows:
        sw_id = row.get("sw_id")
        if sw_id and sw_id not in seen_sws:
            seen_sws[sw_id] = {
                "sw_id": sw_id,
                "sw_label": row.get("sw_label"),
                "sw_role": row.get("sw_role"),
                "vni_id": row.get("vni_id"),
                "vlan_id": row.get("vni_vlan_id"),
                "ipv4_enabled": row.get("vni_ipv4_enabled", False),
                "ipv4_mode": row.get("vni_ipv4_mode"),
                "dhcp_enabled": row.get("vni_dhcp_enabled", False),
            }

    sws = sorted(seen_sws.values(), key=lambda s: s["sw_label"] or "")
    detail["deployed_on"] = sws
    detail["deployed_count"] = len(sws)
    return detail
