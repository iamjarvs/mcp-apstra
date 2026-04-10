from primitives.response_parser import parse_configlets, parse_property_sets


_CONFIGLET_QUERY = "MATCH (configlet:configlet) RETURN *"

_PROPERTY_SET_QUERY = "MATCH (propset:property_set) RETURN *"


async def handle_get_configlets(
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
            rows = graph.query(_CONFIGLET_QUERY)
            parsed = parse_configlets(rows)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "configlets": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
                "configlets": [],
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


async def handle_get_property_sets(
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
            rows = graph.query(_PROPERTY_SET_QUERY)
            parsed = parse_property_sets(rows)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "property_sets": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
                "property_sets": [],
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


def _select_sessions(sessions, instance_name):
    if instance_name is None or instance_name == "all":
        return sessions
    matched = [s for s in sessions if s.name == instance_name]
    if not matched:
        raise ValueError(f"No instance named '{instance_name}' in session pool")
    return matched
