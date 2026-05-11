from collections import Counter
from datetime import datetime, timezone, timedelta

from primitives import live_data_client


# ---------------------------------------------------------------------------
# Preset event-type groups
# ---------------------------------------------------------------------------

AUDIT_PRESETS: dict[str, list[str] | None] = {
    "blueprint_changes": [
        "BlueprintCommit",
        "BlueprintRevert",
        "BlueprintRollback",
        "BlueprintDelete",
        "BlueprintLock",
        "BlueprintUnlock",
    ],
    "device_config": [
        "DeviceConfigChange",
        "DeviceConfigDeviationAccepted",
    ],
    "auth": [
        "Login",
        "Logout",
        "UserCreate",
        "UserUpdate",
        "UserDelete",
        "AuthAclRuleAdd",
        "AuthAclRuleDelete",
        "AuthAclRuleUpdate",
        "AuthAclEnable",
        "AuthAclDisable",
    ],
    "system_mode": [
        "OperationModeChangeToNormal",
        "OperationModeChangeToReadOnly",
        "OperationModeChangeToMaintenance",
        "SystemChangeApiOperationModeToNormal",
        "SystemChangeApiOperationModeToMaintenance",
        "MigrationCheckpoint",
    ],
    "all": None,  # no filter
}

# Full catalogue returned by /api/audit/event-types for reference
ALL_AUDIT_EVENT_TYPES: list[str] = [
    "BlueprintCommit",
    "BlueprintDelete",
    "BlueprintLock",
    "BlueprintRevert",
    "BlueprintRollback",
    "BlueprintUnlock",
    "DeviceConfigChange",
    "DeviceConfigDeviationAccepted",
    "Login",
    "Logout",
    "UserCreate",
    "UserDelete",
    "UserUpdate",
    "AuthAclDisable",
    "AuthAclEnable",
    "AuthAclRuleAdd",
    "AuthAclRuleDelete",
    "AuthAclRuleUpdate",
    "OperationModeChangeToMaintenance",
    "OperationModeChangeToNormal",
    "OperationModeChangeToReadOnly",
    "SystemChangeApiOperationModeToMaintenance",
    "SystemChangeApiOperationModeToNormal",
    "MigrationCheckpoint",
    "RatelimitClear",
    "RatelimitExceptionAdd",
    "RatelimitExceptionDelete",
    "SyslogCreate",
    "SyslogDelete",
    "SyslogUpdate",
]


def _build_filter_expr(event_types: list[str] | None) -> str | None:
    """
    Build the Apstra filter expression for a list of event types.
    Returns None when no filter should be applied (all types).
    """
    if not event_types:
        return None
    escaped = ", ".join(f'"{t}"' for t in event_types)
    return f"type in [{escaped}]"


def _select_sessions(sessions, instance_name):
    if instance_name is None or instance_name == "all":
        return sessions
    matched = [s for s in sessions if s.name == instance_name]
    if not matched:
        raise ValueError(f"No instance named '{instance_name}' in session pool")
    return matched


def _summarise_events(events: list[dict]) -> dict:
    """
    Builds a compact summary of audit events for LLM consumption.

    Uses the 'type' and 'result' fields that Apstra actually returns
    (not HTTP method/status, which are not present in audit records).
    """
    by_user: Counter = Counter()
    by_type: Counter = Counter()
    by_result: Counter = Counter()

    for ev in events:
        user = ev.get("user") or "unknown"
        ev_type = ev.get("type") or "unknown"
        result = ev.get("result") or "unknown"
        by_user[user] += 1
        by_type[ev_type] += 1
        by_result[result] += 1

    return {
        "by_user":   dict(by_user.most_common()),
        "by_type":   dict(by_type.most_common()),
        "by_result": dict(by_result.most_common()),
    }


async def handle_get_audit_events(
    sessions,
    days_back: int = 1,
    event_types: list[str] | None = None,
    instance_name: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """
    Queries the Apstra audit event log across one or all instances.

    days_back controls the look-back window from now.
    event_types filters to specific Apstra audit event types; pass None
    (or the "all" preset) to return every type.

        The response includes:
      - summary: breakdown by user, event type, and result
      - total_count: total matching events on the server
      - is_truncated: true when the server capped the result set
            - events: a single page of events from `offset` with size `limit`
            - has_more: true when more pages are available
            - next_offset: offset to use for the next call when has_more is true
    """
    now = datetime.now(timezone.utc)
    end_time = now.isoformat()
    begin_time = (now - timedelta(days=max(1, days_back))).isoformat()
    filter_expr = _build_filter_expr(event_types)

    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            raw = await live_data_client.get_audit_events(
                session,
                begin_time,
                end_time,
                filter_expr=filter_expr,
                per_page=limit,
                offset=max(0, offset),
            )

            # Normalise across both top-level and nested status shapes
            status_block = raw.get("status", {})
            server_total = (
                raw.get("total_count")
                or status_block.get("total_count")
                or len(raw.get("items", []))
            )
            is_truncated = status_block.get("is_truncated", False)
            result_code = status_block.get("result_code", "unknown")
            response_offset = raw.get("offset", max(0, offset))
            response_per_page = raw.get("per_page", limit)
            response_page = raw.get("page")

            events = raw.get("items", [])

            # Sort newest-first (API may already sort, but be defensive)
            events.sort(
                key=lambda e: e.get("timestamp") or "",
                reverse=True,
            )

            returned = events
            returned_count = len(returned)
            next_offset = response_offset + returned_count
            has_more = next_offset < server_total or is_truncated

            all_results.append({
                "instance":     session.name,
                "begin_time":   begin_time,
                "end_time":     end_time,
                "days_back":    days_back,
                "event_types":  event_types,
                "total_count":  server_total,
                "returned":     returned_count,
                "has_more":     has_more,
                "next_offset":  next_offset if has_more else None,
                "offset":       response_offset,
                "page":         response_page,
                "per_page":     response_per_page,
                "is_truncated": is_truncated,
                "result_code":  result_code,
                "limit":        limit,
                "summary":      _summarise_events(events),
                "events":       returned,
            })
        except Exception as exc:
            all_results.append({
                "instance":    session.name,
                "begin_time":  begin_time,
                "end_time":    end_time,
                "days_back":   days_back,
                "event_types": event_types,
                "error":       str(exc),
                "total_count": 0,
                "returned":    0,
                "has_more":    False,
                "next_offset": None,
                "offset":      max(0, offset),
                "events":      [],
            })

    if len(all_results) == 1:
        return all_results[0]

    total = sum(r.get("total_count", 0) for r in all_results)
    return {
        "instance_count": len(all_results),
        "begin_time":     begin_time,
        "end_time":       end_time,
        "days_back":      days_back,
        "event_types":    event_types,
        "offset":         max(0, offset),
        "limit":          limit,
        "total_events":   total,
        "results":        all_results,
    }


async def handle_get_audit_device_config(
    sessions,
    device_id: str,
    file_name: str,
    instance_name: str | None = None,
) -> dict:
    """
    Retrieves the configuration snapshot recorded by a DeviceConfigChange or
    DeviceConfigDeviationAccepted audit event.

    device_id and file_name come directly from the audit event fields of the
    same name (file_name maps to the 'device_config' field in the event).

    Queries a single instance; if instance_name is None and multiple sessions
    exist the first session is used (device audit records are instance-scoped).
    """
    target = _select_sessions(sessions, instance_name)
    session = target[0]
    try:
        result = await live_data_client.get_audit_device_config(
            session, device_id=device_id, file_name=file_name
        )
        return {
            "instance":  session.name,
            "device_id": device_id,
            "file_name": file_name,
            "config":    result,
        }
    except Exception as exc:
        return {
            "instance":  session.name,
            "device_id": device_id,
            "file_name": file_name,
            "error":     str(exc),
        }

