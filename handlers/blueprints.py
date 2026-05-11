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
      UUID string    → exact match by ID (label populated from API)
      partial label  → case-insensitive substring match (e.g. "DC1" matches "DC1 - SE Demo")

    Returns an empty list if a partial label matches nothing.
    """
    is_uuid = blueprint_ref and _UUID_RE.match(blueprint_ref)

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

    if not all_bps:
        # Could not reach any instance — treat as literal ID (best-effort fallback)
        return [{"id": blueprint_ref, "label": blueprint_ref, "instance_name": None}]

    if is_uuid:
        # Exact UUID match with label populated from API data
        matched = [bp for bp in all_bps if bp["id"].lower() == blueprint_ref.lower()]
        if matched:
            return matched
        # UUID not found in any instance — return with ID as label (best-effort)
        return [{"id": blueprint_ref, "label": blueprint_ref, "instance_name": None}]

    ref = (blueprint_ref or "").strip().lower()
    if not ref or ref == "all":
        return all_bps

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


def _as_non_negative_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _normalize_resolutions(raw_resolutions) -> list[dict]:
    seen: set[tuple[str | None, str | None, str | None]] = set()
    normalized: list[dict] = []

    for resolution in raw_resolutions or []:
        if not isinstance(resolution, dict):
            continue
        item = {}
        for key in ("category", "entity_id", "hint"):
            value = resolution.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                item[key] = text
        if not item:
            continue

        signature = (
            item.get("category"),
            item.get("entity_id"),
            item.get("hint"),
        )
        if signature in seen:
            continue
        seen.add(signature)
        normalized.append(item)

    normalized.sort(
        key=lambda item: (
            item.get("category", ""),
            item.get("entity_id", ""),
            item.get("hint", ""),
        )
    )
    return normalized


def _dedupe_build_issues(raw_full: dict) -> list[dict]:
    deduped: dict[tuple, dict] = {}

    for source_key, affected_key in (
        ("nodes", "affected_nodes"),
        ("relationships", "affected_relationships"),
    ):
        source_map = raw_full.get(source_key) or {}
        if not isinstance(source_map, dict):
            continue

        for source_id, entries in source_map.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue

                severity = str(entry.get("severity") or "unknown").strip().lower()
                display_category = entry.get("display_category")
                message = entry.get("message")
                error_type = entry.get("error_type")
                entity_type = entry.get("entity_type")
                rank = entry.get("rank")
                resolutions = _normalize_resolutions(entry.get("resolutions"))
                resolution_signature = tuple(
                    (r.get("category"), r.get("entity_id"), r.get("hint"))
                    for r in resolutions
                )

                dedupe_key = (
                    severity,
                    display_category,
                    message,
                    error_type,
                    entity_type,
                    rank,
                    resolution_signature,
                )

                if dedupe_key not in deduped:
                    deduped[dedupe_key] = {
                        "severity": severity,
                        "display_category": display_category,
                        "message": message,
                        "error_type": error_type,
                        "entity_type": entity_type,
                        "rank": rank,
                        "resolutions": resolutions,
                        "affected_nodes": [],
                        "affected_relationships": [],
                        "occurrences": 0,
                    }

                issue = deduped[dedupe_key]
                issue["occurrences"] += 1
                source_text = str(source_id)
                if source_text not in issue[affected_key]:
                    issue[affected_key].append(source_text)

    severity_order = {"error": 0, "warning": 1}
    issues = list(deduped.values())
    for issue in issues:
        issue["affected_nodes"].sort()
        issue["affected_relationships"].sort()

    issues.sort(
        key=lambda issue: (
            severity_order.get(issue.get("severity", ""), 99),
            -(issue.get("rank") or 0),
            issue.get("display_category") or "",
            issue.get("message") or "",
        )
    )
    return issues


async def handle_get_blueprint_build_errors(
    sessions,
    blueprint_id: str,
    instance_name: str = None,
) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            digest = await live_data_client.get_blueprint_build_errors(
                session,
                blueprint_id,
                mode="digest",
            )
            errors_count = _as_non_negative_int(digest.get("errors_count"))
            warnings_count = _as_non_negative_int(digest.get("warnings_count"))
            result = {
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "version": digest.get("version"),
                "errors_count": errors_count,
                "warnings_count": warnings_count,
                "has_blocking_errors": errors_count > 0,
            }

            if errors_count == 0 and warnings_count == 0:
                result["details_fetched"] = False
                result["issue_count"] = 0
                result["issues"] = []
                result["note"] = "No build errors or warnings detected."
            else:
                full = await live_data_client.get_blueprint_build_errors(
                    session,
                    blueprint_id,
                    mode="full",
                )
                issues = _dedupe_build_issues(full)
                result["details_fetched"] = True
                result["issue_count"] = len(issues)
                result["issues"] = issues

            all_results.append(result)
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
                "details_fetched": False,
                "issue_count": 0,
                "issues": [],
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "results": all_results,
        "total_errors_count": sum(r.get("errors_count", 0) for r in all_results),
        "total_warnings_count": sum(r.get("warnings_count", 0) for r in all_results),
        "blocking_instance_count": sum(
            1 for r in all_results if _as_non_negative_int(r.get("errors_count")) > 0
        ),
    }


def _select_sessions(sessions, instance_name):
    if instance_name is None or instance_name == "all":
        return sessions
    matched = [s for s in sessions if s.name == instance_name]
    if not matched:
        raise ValueError(f"No instance named '{instance_name}' in session pool")
    return matched
