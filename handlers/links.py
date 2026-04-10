from primitives.response_parser import parse_links

# Scoped to a single system — deduplicates by canonicalising endpoint order
# using the interface id string comparison, then DISTINCT on the link node.
_LINK_QUERY_BY_SYSTEM = """
MATCH (sw_a:system {system_id: $system_id})
  -[:hosted_interfaces]->(intf_a:interface)
  -[:link__rel]->(link:link)
  <-[:link__rel]-(intf_b:interface)
WHERE intf_a.id <> intf_b.id
WITH link,
     CASE WHEN intf_a.id < intf_b.id
          THEN intf_a ELSE intf_b END AS local_intf,
     CASE WHEN intf_a.id < intf_b.id
          THEN intf_b ELSE intf_a END AS remote_intf
WITH DISTINCT link, local_intf, remote_intf
RETURN local_intf, link, remote_intf
"""

# Fabric-wide — all physical links in the blueprint.
# WHERE intf_a.id < intf_b.id ensures each link appears exactly once.
_LINK_QUERY_ALL = """
MATCH (intf_a:interface)
  -[:link__rel]->(link:link)
  <-[:link__rel]-(intf_b:interface)
WHERE intf_a.id < intf_b.id
WITH DISTINCT link, intf_a AS local_intf, intf_b AS remote_intf
RETURN local_intf, link, remote_intf
"""


async def handle_get_link_list(
    sessions,
    registry,
    blueprint_id: str,
    system_id: str = None,
    instance_name: str = None,
) -> dict:
    """
    Returns physical links for a blueprint, optionally filtered to a single
    system. When system_id is supplied the query traverses hosted_interfaces
    from that system; when omitted every link in the fabric is returned.
    """
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            graph = await registry.get_or_rebuild(session, blueprint_id)
            if system_id:
                rows = graph.query(_LINK_QUERY_BY_SYSTEM, {"system_id": system_id})
            else:
                rows = graph.query(_LINK_QUERY_ALL)
            parsed = parse_links(rows)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "system_id": system_id,
                "links": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "system_id": system_id,
                "error": str(e),
                "links": [],
                "count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "system_id": system_id,
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
