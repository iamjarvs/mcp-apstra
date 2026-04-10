from primitives.response_parser import (
    parse_virtual_networks,
    parse_virtual_network_list,
    parse_routing_zones,
    parse_routing_zone_detail,
    parse_virtual_network_detail,
)

# Query scoped to a single switch identified by hardware system_id.
_VN_QUERY_SINGLE = """
MATCH (sw:system {system_id: $system_id})
-[:hosted_vn_instances]->(vni:vn_instance)
<-[:instantiated_by]-(vn:virtual_network)
RETURN
  sw.id, sw.label,
  vni.id, vni.vlan_id,
  vni.ipv4_enabled, vni.ipv4_mode, vni.dhcp_enabled,
  vn.id, vn.label, vn.vn_type, vn.vn_id,
  vn.reserved_vlan_id, vn.ipv4_enabled, vn.ipv4_subnet,
  vn.ipv6_enabled,
  vn.virtual_gateway_ipv4,
  vn.l3_mtu
"""

# Query across all switches in the blueprint.
_VN_QUERY_ALL = """
MATCH (sw:system)
-[:hosted_vn_instances]->(vni:vn_instance)
<-[:instantiated_by]-(vn:virtual_network)
RETURN
  sw.id, sw.label,
  vni.id, vni.vlan_id,
  vni.ipv4_enabled, vni.ipv4_mode, vni.dhcp_enabled,
  vn.id, vn.label, vn.vn_type, vn.vn_id,
  vn.reserved_vlan_id, vn.ipv4_enabled, vn.ipv4_subnet,
  vn.ipv6_enabled,
  vn.virtual_gateway_ipv4,
  vn.l3_mtu
"""


async def handle_get_virtual_networks(
    sessions,
    registry,
    blueprint_id: str,
    system_id: str = None,
    instance_name: str = None,
) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            graph = await registry.get_or_rebuild(session, blueprint_id)
            if system_id:
                rows = graph.query(_VN_QUERY_SINGLE, {"system_id": system_id})
            else:
                rows = graph.query(_VN_QUERY_ALL)
            parsed = parse_virtual_networks(rows)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "system_id": system_id,
                "vn_instances": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "system_id": system_id,
                "error": str(e),
                "vn_instances": [],
                "count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    all_vnis = [v for r in all_results for v in r.get("vn_instances", [])]
    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "system_id": system_id,
        "results": all_results,
        "total_count": sum(r.get("count", 0) for r in all_results),
    }


_VN_LIST_QUERY = """
MATCH (vn:virtual_network)
OPTIONAL MATCH (sz:security_zone)-[:member_vns]->(vn)
RETURN
  vn.id, vn.label, vn.vn_type, vn.vn_id,
  vn.reserved_vlan_id, vn.ipv4_enabled, vn.ipv4_subnet,
  vn.ipv6_enabled, vn.ipv6_subnet,
  vn.virtual_gateway_ipv4, vn.virtual_gateway_ipv4_enabled,
  vn.virtual_gateway_ipv6, vn.virtual_gateway_ipv6_enabled,
  vn.virtual_mac, vn.l3_mtu, vn.description, vn.tags,
  sz.label       AS routing_zone_label,
  sz.vrf_name    AS vrf_name,
  sz.sz_type     AS routing_zone_type
ORDER BY vn.label
"""


async def handle_get_virtual_network_list(
    sessions,
    registry,
    blueprint_id: str,
    instance_name: str = None,
) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            graph = await registry.get_or_rebuild(session, blueprint_id)
            rows = graph.query(_VN_LIST_QUERY)
            parsed = parse_virtual_network_list(rows)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "virtual_networks": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
                "virtual_networks": [],
                "count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    all_vns = [v for r in all_results for v in r.get("virtual_networks", [])]
    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "results": all_results,
        "total_count": sum(r.get("count", 0) for r in all_results),
    }


_SZ_LIST_QUERY = """
MATCH (sz:security_zone)
OPTIONAL MATCH (sz)-[:member_vns]->(vn:virtual_network)
WITH sz, count(vn) AS vn_count
RETURN
  sz.id, sz.label, sz.vrf_name, sz.sz_type,
  vn_count
ORDER BY sz.label
"""

_SZ_DETAIL_QUERY = """
MATCH (sz:security_zone)
WHERE sz.label = $routing_zone OR sz.vrf_name = $routing_zone
OPTIONAL MATCH (sz)-[:member_vns]->(vn:virtual_network)
OPTIONAL MATCH (sw:system)-[:hosted_vn_instances]->(vni:vn_instance)
  -[:instantiated_by]->(vn)
RETURN
  sz.id, sz.label, sz.vrf_name, sz.sz_type,
  vn.id    AS vn_id,
  vn.label AS vn_label,
  vn.vn_type AS vn_type,
  vn.vn_id   AS vni_number,
  vn.ipv4_enabled AS vn_ipv4_enabled,
  vn.ipv4_subnet  AS vn_ipv4_subnet,
  vn.ipv6_enabled AS vn_ipv6_enabled,
  vn.ipv6_subnet  AS vn_ipv6_subnet,
  vn.virtual_gateway_ipv4 AS vn_gw_ipv4,
  vn.l3_mtu AS vn_l3_mtu,
  vn.description  AS vn_description,
  vn.tags         AS vn_tags,
  sw.id    AS sw_id,
  sw.label AS sw_label,
  sw.role  AS sw_role,
  vni.vlan_id AS vni_vlan_id
ORDER BY vn.label, sw.label
"""

_VN_DETAIL_QUERY = """
MATCH (vn:virtual_network)
WHERE vn.label = $virtual_network OR vn.id = $virtual_network
OPTIONAL MATCH (sz:security_zone)-[:member_vns]->(vn)
OPTIONAL MATCH (sw:system)-[:hosted_vn_instances]->(vni:vn_instance)
  -[:instantiated_by]->(vn)
RETURN
  vn.id, vn.label, vn.vn_type, vn.vn_id,
  vn.reserved_vlan_id, vn.ipv4_enabled, vn.ipv4_subnet,
  vn.ipv6_enabled, vn.ipv6_subnet,
  vn.virtual_gateway_ipv4, vn.virtual_gateway_ipv4_enabled,
  vn.virtual_gateway_ipv6, vn.virtual_gateway_ipv6_enabled,
  vn.virtual_mac, vn.l3_mtu, vn.description, vn.tags,
  sz.id    AS sz_id,
  sz.label AS routing_zone_label,
  sz.vrf_name AS vrf_name,
  sz.sz_type  AS routing_zone_type,
  sw.id    AS sw_id,
  sw.label AS sw_label,
  sw.role  AS sw_role,
  vni.id      AS vni_id,
  vni.vlan_id AS vni_vlan_id,
  vni.ipv4_enabled AS vni_ipv4_enabled,
  vni.ipv4_mode    AS vni_ipv4_mode,
  vni.dhcp_enabled AS vni_dhcp_enabled
ORDER BY sw.label
"""


async def handle_get_routing_zones(
    sessions,
    registry,
    blueprint_id: str,
    instance_name: str = None,
) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            graph = await registry.get_or_rebuild(session, blueprint_id)
            rows = graph.query(_SZ_LIST_QUERY)
            parsed = parse_routing_zones(rows)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "routing_zones": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
                "routing_zones": [],
                "count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "results": all_results,
        "total_count": sum(r.get("count", 0) for r in all_results),
    }


async def handle_get_routing_zone_detail(
    sessions,
    registry,
    blueprint_id: str,
    routing_zone: str,
    instance_name: str = None,
) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            graph = await registry.get_or_rebuild(session, blueprint_id)
            rows = graph.query(_SZ_DETAIL_QUERY, {"routing_zone": routing_zone})
            parsed = parse_routing_zone_detail(rows)
            result = {
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "routing_zone": routing_zone,
            }
            if parsed is None:
                result["error"] = f"Routing zone '{routing_zone}' not found"
                result["detail"] = None
            else:
                result["detail"] = parsed
            all_results.append(result)
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "routing_zone": routing_zone,
                "error": str(e),
                "detail": None,
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "routing_zone": routing_zone,
        "results": all_results,
    }


async def handle_get_virtual_network_detail(
    sessions,
    registry,
    blueprint_id: str,
    virtual_network: str,
    instance_name: str = None,
) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            graph = await registry.get_or_rebuild(session, blueprint_id)
            rows = graph.query(_VN_DETAIL_QUERY, {"virtual_network": virtual_network})
            parsed = parse_virtual_network_detail(rows)
            result = {
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "virtual_network": virtual_network,
            }
            if parsed is None:
                result["error"] = f"Virtual network '{virtual_network}' not found"
                result["detail"] = None
            else:
                result["detail"] = parsed
            all_results.append(result)
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "virtual_network": virtual_network,
                "error": str(e),
                "detail": None,
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "virtual_network": virtual_network,
        "results": all_results,
    }


def _select_sessions(sessions, instance_name):
    if instance_name is None or instance_name == "all":
        return sessions
    matched = [s for s in sessions if s.name == instance_name]
    if not matched:
        raise ValueError(f"No instance named '{instance_name}' in session pool")
    return matched
