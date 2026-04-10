from primitives import live_data_client, response_parser


async def handle_get_anomalies(sessions, blueprint_id: str, instance_name: str = None) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            raw = await live_data_client.get_anomalies(session, blueprint_id)
            parsed = response_parser.parse_anomalies(raw)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "anomalies": parsed,
                "count": len(parsed),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
                "anomalies": [],
                "count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    all_anomalies = [a for r in all_results for a in r.get("anomalies", [])]
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
