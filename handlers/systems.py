from primitives.response_parser import parse_systems

_SYSTEMS_CYPHER = """
MATCH (sw:system)
WHERE sw.system_type = 'switch'
AND sw.role IN ['leaf', 'spine', 'access', 'superspine']
RETURN sw.deploy_mode,
       sw.role,
       sw.system_id,
       sw.label,
       sw.type,
       sw.external,
       sw.hostname,
       sw.management_level,
       sw.system_type,
       sw.group_label,
       sw.id
"""


async def handle_get_systems(sessions, registry, blueprint_id: str, instance_name: str = None) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            graph = await registry.get_or_rebuild(session, blueprint_id)
            rows = graph.query(_SYSTEMS_CYPHER)
            parsed = parse_systems(rows)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "systems": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
                "systems": [],
                "count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    all_systems = [s for r in all_results for s in r.get("systems", [])]
    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
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
