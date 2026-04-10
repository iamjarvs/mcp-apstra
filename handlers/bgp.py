from primitives.response_parser import parse_external_peerings, parse_fabric_peerings


# ---------------------------------------------------------------------------
# Cypher queries
# ---------------------------------------------------------------------------

# All external BGP peerings across the blueprint — sessions between an
# Apstra-managed fabric device (external=false) and an external system
# (external=true) that is not owned or configured by this blueprint.
#
# The external/fabric constraints on the system nodes naturally select one row
# per session (each session has exactly one fabric-side endpoint and one
# external-side endpoint), so no explicit deduplication is needed.
_PEERING_QUERY_ALL = """
MATCH (ps:protocol_session {routing: 'bgp'})
MATCH (ps)-[:instantiates]->(ep_fabric:protocol_endpoint)
MATCH (ps)-[:instantiates]->(ep_external:protocol_endpoint)
WHERE ep_fabric <> ep_external
MATCH (ep_fabric)-[:layered_over]->(sub_fabric:interface)
MATCH (ep_external)-[:layered_over]->(sub_external:interface)
MATCH (phys_fabric:interface)-[:composed_of]->(sub_fabric)
MATCH (phys_external:interface)-[:composed_of]->(sub_external)
MATCH (sys_fabric:system {external: false})-[:hosted_interfaces]->(phys_fabric)
MATCH (sys_external:system {external: true})-[:hosted_interfaces]->(phys_external)
RETURN
  ps.id                    AS session_id,
  ps.bfd                   AS bfd,
  ps.ipv4_safi             AS ipv4_safi,
  ps.ipv6_safi             AS ipv6_safi,
  ps.ttl                   AS ttl,
  sys_fabric.hostname      AS local_hostname,
  sys_fabric.role          AS local_role,
  sys_fabric.system_id     AS local_serial,
  sys_fabric.external      AS local_external,
  phys_fabric.if_name      AS local_interface,
  phys_fabric.description  AS local_intf_description,
  sub_fabric.if_name       AS local_subinterface,
  sub_fabric.ipv4_addr     AS local_ip,
  sub_fabric.vlan_id       AS local_vlan_id,
  ep_fabric.local_asn      AS local_asn,
  sys_external.hostname    AS remote_hostname,
  sys_external.role        AS remote_role,
  sys_external.system_id   AS remote_serial,
  sys_external.external    AS remote_external,
  phys_external.if_name    AS remote_interface,
  phys_external.description AS remote_intf_description,
  sub_external.if_name     AS remote_subinterface,
  sub_external.ipv4_addr   AS remote_ip,
  sub_external.vlan_id     AS remote_vlan_id,
  ep_external.local_asn    AS remote_asn
"""

# External BGP peerings for a specific fabric device, identified by hostname or
# label. The anchor must be a managed device (external=false); only sessions
# where the remote peer is an external system (external=true) are returned.
_PEERING_QUERY_DEVICE = """
MATCH (sw:system)
WHERE (sw.label = $device OR sw.hostname = $device) AND sw.external = false
MATCH (sw)-[:hosted_interfaces]->(phys_intf:interface)
MATCH (phys_intf)-[:composed_of]->(local_sub:interface)
MATCH (ep_local:protocol_endpoint)-[:layered_over]->(local_sub)
MATCH (ps:protocol_session {routing: 'bgp'})-[:instantiates]->(ep_local)
MATCH (ps)-[:instantiates]->(ep_remote:protocol_endpoint)
WHERE ep_local <> ep_remote
MATCH (ep_remote)-[:layered_over]->(remote_sub:interface)
MATCH (remote_phys:interface)-[:composed_of]->(remote_sub)
MATCH (remote_sw:system {external: true})-[:hosted_interfaces]->(remote_phys)
RETURN
  ps.id                   AS session_id,
  ps.bfd                  AS bfd,
  ps.ipv4_safi            AS ipv4_safi,
  ps.ipv6_safi            AS ipv6_safi,
  ps.ttl                  AS ttl,
  sw.hostname             AS local_hostname,
  sw.role                 AS local_role,
  sw.system_id            AS local_serial,
  sw.external             AS local_external,
  phys_intf.if_name       AS local_interface,
  phys_intf.description   AS local_intf_description,
  local_sub.if_name       AS local_subinterface,
  local_sub.ipv4_addr     AS local_ip,
  local_sub.vlan_id       AS local_vlan_id,
  ep_local.local_asn      AS local_asn,
  remote_sw.hostname      AS remote_hostname,
  remote_sw.role          AS remote_role,
  remote_sw.system_id     AS remote_serial,
  remote_sw.external      AS remote_external,
  remote_phys.if_name     AS remote_interface,
  remote_phys.description AS remote_intf_description,
  remote_sub.if_name      AS remote_subinterface,
  remote_sub.ipv4_addr    AS remote_ip,
  remote_sub.vlan_id      AS remote_vlan_id,
  ep_remote.local_asn     AS remote_asn
"""


# ---------------------------------------------------------------------------
# Fabric (intra-fabric) BGP peering queries
# ---------------------------------------------------------------------------

# All intra-fabric eBGP peerings — sessions between two Apstra-managed devices
# (both external=false). The WHERE sy_a.id < sy_b.id predicate deduplicates:
# without it the undirected link match returns A->B and B->A as separate rows.
_FABRIC_PEERING_QUERY_ALL = """
MATCH (sy_a:system {external: false})-[:hosted_interfaces]->(int_a:interface {if_type: 'ip', protocols: 'ebgp'})
-[:link__rel]->(link:link)
<-[:link__rel]-(int_b:interface {if_type: 'ip', protocols: 'ebgp'})
<-[:hosted_interfaces]-(sy_b:system {external: false})
WHERE sy_a.id < sy_b.id
MATCH (asn_a:domain)-[:composed_of_systems]->(sy_a)
MATCH (asn_b:domain)-[:composed_of_systems]->(sy_b)
RETURN *
"""

# Intra-fabric eBGP peerings for a specific device, identified by hostname or
# label. The anchor (sy_a) is always the named device; sy_b is always its peer.
# No deduplication is needed because the anchor fixes one side of the session.
_FABRIC_PEERING_QUERY_DEVICE = """
MATCH (sy_a:system)
WHERE (sy_a.label = $device OR sy_a.hostname = $device) AND sy_a.external = false
MATCH (sy_a)-[:hosted_interfaces]->(int_a:interface {if_type: 'ip', protocols: 'ebgp'})
-[:link__rel]->(link:link)
<-[:link__rel]-(int_b:interface {if_type: 'ip', protocols: 'ebgp'})
<-[:hosted_interfaces]-(sy_b:system {external: false})
MATCH (asn_a:domain)-[:composed_of_systems]->(sy_a)
MATCH (asn_b:domain)-[:composed_of_systems]->(sy_b)
RETURN *
"""


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def handle_get_external_peerings(
    sessions,
    registry,
    blueprint_id: str,
    device: str = None,
    instance_name: str = None,
) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            graph = await registry.get_or_rebuild(session, blueprint_id)
            if device:
                rows = graph.query(_PEERING_QUERY_DEVICE, {"device": device})
            else:
                rows = graph.query(_PEERING_QUERY_ALL)
            parsed = parse_external_peerings(rows)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "device": device,
                "peerings": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "device": device,
                "error": str(e),
                "peerings": [],
                "count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "device": device,
        "results": all_results,
        "total_count": sum(r.get("count", 0) for r in all_results),
    }


async def handle_get_fabric_peerings(
    sessions,
    registry,
    blueprint_id: str,
    device: str = None,
    instance_name: str = None,
) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            graph = await registry.get_or_rebuild(session, blueprint_id)
            if device:
                rows = graph.query(_FABRIC_PEERING_QUERY_DEVICE, {"device": device})
            else:
                rows = graph.query(_FABRIC_PEERING_QUERY_ALL)
            parsed = parse_fabric_peerings(rows)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "device": device,
                "peerings": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "device": device,
                "error": str(e),
                "peerings": [],
                "count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "device": device,
        "results": all_results,
        "total_count": sum(r.get("count", 0) for r in all_results),
    }


def _select_sessions(sessions, instance_name):
    if instance_name is None or instance_name == "all":
        return sessions
    matched = [s for s in sessions if s.name == instance_name]
    if not matched:
        raise ValueError(f"No instance named '{instance_name}' in session pool")
    return matched
