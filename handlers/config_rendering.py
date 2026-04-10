from primitives import live_data_client
from primitives.response_parser import parse_config_rendering


async def handle_get_rendered_config(
    sessions,
    blueprint_id: str,
    system_id: str,
    sections: list[str] | None = None,
    subsections: dict[str, list[str]] | None = None,
    instance_name: str | None = None,
) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            raw = await live_data_client.get_config_rendering(
                session, blueprint_id, system_id
            )
            parsed = parse_config_rendering(raw, sections, subsections)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "system_id": system_id,
                **parsed,
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "system_id": system_id,
                "error": str(e),
                "available_sections": [],
                "available_configlet_sections": [],
                "available_subsections": {},
                "sections": {},
                "subsection_detail": {},
                "configlets": {},
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
