from primitives import live_data_client
from primitives.response_parser import parse_system_context


async def handle_get_system_context(
    sessions,
    blueprint_id: str,
    system_id: str,
    include_sections: list[str] | None = None,
    instance_name: str | None = None,
) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            raw = await live_data_client.get_system_config_context(
                session, blueprint_id, system_id
            )
            parsed = parse_system_context(raw, include_sections)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "system_id": system_id,
                "context": parsed,
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "system_id": system_id,
                "error": str(e),
                "context": {},
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "system_id": system_id,
        "results": all_results,
    }


def _select_sessions(sessions, instance_name):
    if instance_name is None or instance_name == "all":
        return sessions
    matched = [s for s in sessions if s.name == instance_name]
    if not matched:
        raise ValueError(f"No instance named '{instance_name}' in session pool")
    return matched
