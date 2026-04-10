from primitives import live_data_client, response_parser


async def handle_get_blueprints(sessions, instance_name: str = None) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            raw = await live_data_client.get_blueprints(session)
            parsed = response_parser.parse_blueprints(raw)
            all_results.append({
                "instance": session.name,
                "blueprints": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "error": str(e),
                "blueprints": [],
                "count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    all_blueprints = [b for r in all_results for b in r.get("blueprints", [])]
    return {
        "instance": "all",
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
