from primitives.response_parser import parse_interfaces

_INTERFACE_QUERY = """
MATCH (sw:system {system_id: $system_id})
-[:hosted_interfaces]->(intf:interface)
RETURN *
"""


async def handle_get_interface_list(
    sessions,
    registry,
    blueprint_id: str,
    system_id: str,
    instance_name: str = None,
) -> dict:
    """
    Returns all interfaces for a given system within a blueprint.

    Matches by hardware chassis serial (system_id), traverses the
    hosted_interfaces relationship, and returns interface details
    normalised by parse_interfaces.
    """
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            graph = await registry.get_or_rebuild(session, blueprint_id)
            rows = graph.query(_INTERFACE_QUERY, {"system_id": system_id})
            parsed = parse_interfaces(rows)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "system_id": system_id,
                "interfaces": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "system_id": system_id,
                "error": str(e),
                "interfaces": [],
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
