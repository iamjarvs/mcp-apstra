from typing import Annotated, Literal

from fastmcp import Context
from pydantic import Field

from handlers.audit import (
    AUDIT_PRESETS,
    ALL_AUDIT_EVENT_TYPES,
    handle_get_audit_events,
    handle_get_audit_device_config,
)


def register(mcp, include_legacy: bool = True):

    def _resolve_event_types(
        preset: Literal["all", "blueprint_changes", "device_config", "auth", "system_mode"] | None,
        event_types: list[str] | None,
    ) -> list[str] | None:
        # explicit event_types always wins over preset
        if event_types:
            return event_types
        if preset and preset != "all":
            return AUDIT_PRESETS.get(preset)
        return None

    @mcp.tool()
    async def audit(
        intent: Annotated[
            Literal["events", "device_config"],
            Field(
                description=(
                    "Audit workflow intent. 'events' queries the audit log. "
                    "'device_config' fetches config content from a specific "
                    "DeviceConfigChange/DeviceConfigDeviationAccepted event."
                ),
            ),
        ],
        days_back: Annotated[
            int,
            Field(
                default=1,
                ge=1,
                description=(
                    "How many days back from now to query. Used only for intent='events'."
                ),
            ),
        ] = 1,
        preset: Annotated[
            Literal["all", "blueprint_changes", "device_config", "auth", "system_mode"] | None,
            Field(
                default=None,
                description=(
                    "Convenience event grouping for intent='events'. Ignored when "
                    "event_types is provided."
                ),
            ),
        ] = None,
        event_types: Annotated[
            list[str] | None,
            Field(
                default=None,
                description=(
                    "Explicit list of event types for intent='events'. "
                    "Known types: " + ", ".join(ALL_AUDIT_EVENT_TYPES)
                ),
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                default=200,
                ge=1,
                le=1000,
                description="Maximum events returned for intent='events'.",
            ),
        ] = 200,
        offset: Annotated[
            int,
            Field(
                default=0,
                ge=0,
                description=(
                    "Pagination offset for intent='events'. Start with 0. "
                    "If response has has_more=true, call again with offset=next_offset."
                ),
            ),
        ] = 0,
        device_id: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Required for intent='device_config'. Use the device_id from a "
                    "prior audit event returned by intent='events' with preset='device_config'."
                ),
            ),
        ] = None,
        file_name: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Required for intent='device_config'. Use the device_config field "
                    "from a prior audit event as file_name."
                ),
            ),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Apstra instance name. Leave as None unless user explicitly provides one."
                ),
            ),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Umbrella audit workflow tool.

                Scope:
                    This tool reports Apstra controller/AppStore-server audit activity
                    (API/user actions recorded by Apstra), not switch-local OS audit logs.

        intent='events':
                    Query controller audit events with optional preset/event type filters.
                    Use offset pagination: start at offset=0 and continue with offset=next_offset
                    while has_more is true.

        intent='device_config':
                    Fetch config snapshot content for a specific controller audit event.
          Must provide device_id and file_name from a prior audit event.

        Audit pagination note (`intent='events'`):

                    - Start with `offset=0`.
                    - If response has `has_more=true` and `next_offset` is present, call `audit` again with the same filters/window and `offset=next_offset`.
                    - Repeat until `has_more=false`.
        """
        sessions = ctx.lifespan_context["sessions"]

        if intent == "events":
            resolved_types = _resolve_event_types(preset, event_types)
            return await handle_get_audit_events(
                sessions,
                days_back=days_back,
                event_types=resolved_types,
                instance_name=instance_name,
                limit=limit,
                offset=offset,
            )

        if not device_id or not file_name:
            return {
                "error": (
                    "intent='device_config' requires both device_id and file_name. "
                    "Get these from an earlier audit intent='events' response "
                    "(device_id + device_config)."
                )
            }

        return await handle_get_audit_device_config(
            sessions,
            device_id=device_id,
            file_name=file_name,
            instance_name=instance_name,
        )

    if include_legacy:

        @mcp.tool()
        async def get_audit_log(
        days_back: Annotated[
            int,
            Field(
                default=1,
                ge=1,
                description=(
                    "How many days back from now to query. Default 1 (last 24 hours). "
                    "Common values: 1 (recent activity), 7 (last week), 30 (last month)."
                ),
            ),
        ] = 1,
        preset: Annotated[
            Literal["all", "blueprint_changes", "device_config", "auth", "system_mode"] | None,
            Field(
                default=None,
                description=(
                    "Convenience preset that selects a group of related event types. "
                    "Ignored when event_types is provided.\n"
                    "  all              — no filter, every event type\n"
                    "  blueprint_changes — BlueprintCommit, BlueprintRevert, "
                    "BlueprintRollback, BlueprintDelete, BlueprintLock, BlueprintUnlock\n"
                    "  device_config    — DeviceConfigChange, DeviceConfigDeviationAccepted\n"
                    "  auth             — Login, Logout, UserCreate/Update/Delete, "
                    "AuthAcl* events\n"
                    "  system_mode      — OperationModeChange*, "
                    "SystemChangeApiOperationMode*, MigrationCheckpoint"
                ),
            ),
        ] = None,
        event_types: Annotated[
            list[str] | None,
            Field(
                default=None,
                description=(
                    "Explicit list of Apstra audit event types to filter on. "
                    "Takes priority over preset when provided. "
                    "Pass null or omit to use the preset (or return all types). "
                    "Known types: "
                    + ", ".join(ALL_AUDIT_EVENT_TYPES)
                ),
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                default=200,
                ge=1,
                le=1000,
                description=(
                    "Maximum events to return per instance. Default 200, max 1000. "
                    "Events are ordered newest-first. "
                    "If has_more=true, increase limit or narrow the window/preset."
                ),
            ),
        ] = 200,
        offset: Annotated[
            int,
            Field(
                default=0,
                ge=0,
                description=(
                    "Pagination offset. Start with 0. "
                    "Use next_offset from previous response to continue."
                ),
            ),
        ] = 0,
        instance_name: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Apstra instance name. Do not ask the user for this — leave as None "
                    "to query all instances. Only set if the user explicitly names a "
                    "specific instance."
                ),
            ),
        ] = None,
        ctx: Context = None,
        ) -> dict:
            """
            Query the Apstra controller audit log for operator activity in a time window.

                Scope:
                    Controller/AppStore-server audit only (not switch-local OS audit logs).

        Use this to investigate who changed what on the Apstra controller and when.

        Recommended usage:
          1. Start with preset='blueprint_changes' + days_back=7 to see recent
             blueprint commits, reverts, and rollbacks.
          2. Use preset='device_config' to see which devices had config pushed.
          3. Use preset='auth' to review login activity and user changes.
          4. Use event_types=[...] for a custom cross-category filter.

        Response fields:
          begin_time / end_time  — exact query window (UTC ISO 8601)
          total_count            — total matching events on the server
          returned               — events included in this response
          has_more               — true when total_count > returned
          is_truncated           — true when the server itself capped the result
          summary.by_type        — count per event type (most common first)
          summary.by_user        — count per user
          summary.by_result      — count per result (Success/Failure)
          events                 — newest-first list; each contains timestamp,
                                   user, user_ip, type, result and type-specific
                                   fields (blueprint_id/label, device_id, etc.)

            Data source: live Apstra API (POST /api/audit/events/query). No local cache.
            """
            # Backward-compatible alias for intent='events'.
            return await audit(
                intent="events",
                days_back=days_back,
                preset=preset,
                event_types=event_types,
                limit=limit,
                offset=offset,
                instance_name=instance_name,
                ctx=ctx,
            )

        @mcp.tool()
        async def get_device_audit_config(
        device_id: Annotated[
            str,
            Field(
                description=(
                    "The device system ID from a DeviceConfigChange or "
                    "DeviceConfigDeviationAccepted audit event (the 'device_id' field)."
                ),
            ),
        ],
        file_name: Annotated[
            str,
            Field(
                description=(
                    "The config filename from the same audit event (the 'device_config' "
                    "field). Passed verbatim to the API."
                ),
            ),
        ],
        instance_name: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Apstra instance name. Leave as None unless the user explicitly "
                    "names a specific instance. Device audit records are instance-scoped "
                    "so the first available session is used when None."
                ),
            ),
        ] = None,
        ctx: Context = None,
        ) -> dict:
            """
            Retrieve the device configuration snapshot recorded by a config-change audit event.

                Scope:
                    Snapshot artifacts are attached to controller audit events in Apstra.
                    This is not device-native local audit logging.

        Workflow:
          1. Call get_audit_log with preset='device_config' to find DeviceConfigChange
             or DeviceConfigDeviationAccepted events for the time window of interest.
          2. Pick the event you want to inspect; copy its 'device_id' and
             'device_config' fields.
          3. Call this tool with those values to fetch the full config content.

        Both device_id and file_name come directly from the audit event fields
        returned by get_audit_log — do not construct them manually.

        Response fields:
          instance   — Apstra instance queried
          device_id  — echoed back for reference
          file_name  — echoed back for reference
          config     — the configuration content returned by Apstra

            Data source: live Apstra API (POST /api/audit/events/device-config).
            """
            # Backward-compatible alias for intent='device_config'.
            return await audit(
                intent="device_config",
                device_id=device_id,
                file_name=file_name,
                instance_name=instance_name,
                ctx=ctx,
            )

