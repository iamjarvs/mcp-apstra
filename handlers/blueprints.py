import re

from primitives import live_data_client, response_parser

# --------------------------------------------------------------------------
# Blueprint resolution
# --------------------------------------------------------------------------

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


async def resolve_blueprints(sessions, blueprint_ref: str | None) -> list[dict]:
    """
    Resolve a blueprint reference to a list of {id, label, instance_name} dicts.

    Rules:
      None / "all"   → all blueprints from all sessions
      UUID string    → [{id: ref, label: None, instance_name: None}] without API call
      partial label  → case-insensitive substring match (e.g. "DC1" matches "DC1 - SE Demo")

    Returns an empty list if a partial label matches nothing.
    """
    if blueprint_ref and _UUID_RE.match(blueprint_ref):
        return [{"id": blueprint_ref, "label": None, "instance_name": None}]

    all_bps: list[dict] = []
    for session in sessions:
        try:
            raw = await live_data_client.get_blueprints(session)
            for item in response_parser.parse_blueprints(raw):
                all_bps.append({
                    "id": item["id"],
                    "label": item["label"],
                    "instance_name": session.name,
                })
        except Exception:
            pass

    ref = (blueprint_ref or "").strip().lower()
    if not ref or ref == "all":
        return all_bps

    if not all_bps:
        # Could not reach any instance — treat as literal ID (best-effort fallback)
        return [{"id": blueprint_ref, "label": blueprint_ref, "instance_name": None}]

    matched = [bp for bp in all_bps if ref in bp["label"].lower()]
    return matched



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
