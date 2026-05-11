from typing import Annotated, Literal

from fastmcp import Context
from pydantic import Field

from tools import telemetry as telemetry_tool


class _CaptureMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _capture_registered_tools(register_fn):
    stub = _CaptureMCP()
    register_fn(stub)
    return stub.tools


def _missing_args(required: dict[str, object]) -> list[str]:
    return [key for key, value in required.items() if value is None]


def register(mcp):
    legacy_tools = _capture_registered_tools(telemetry_tool.register)

    @mcp.tool()
    async def telemetry(
        intent: Annotated[
            Literal[
                "interface_counters",
                "interface_utilisation",
                "system_telemetry",
                "interface_error_trend",
                "top_error_growers",
            ],
            Field(
                default="interface_counters",
                description=(
                    "Dispatcher intent for telemetry workflows. Choose one intent per call."
                ),
            ),
        ] = "interface_counters",
        blueprint_id: Annotated[
            str | None,
            Field(default=None, description="Optional blueprint ID or partial label."),
        ] = None,
        system_id: Annotated[
            str | None,
            Field(default=None, description="Optional hardware system_id for system-scoped intents."),
        ] = None,
        system_ids: Annotated[
            list[str] | None,
            Field(default=None, description="Optional list of system_ids for system_telemetry intent."),
        ] = None,
        interface_name: Annotated[
            str | None,
            Field(default=None, description="Optional interface name for interface-scoped intents."),
        ] = None,
        errors_only: Annotated[
            bool,
            Field(default=False, description="For interface_counters: return only interfaces with errors."),
        ] = False,
        top_n: Annotated[
            int,
            Field(default=20, ge=0, description="For ranking intents: number of rows to return."),
        ] = 20,
        hours_back: Annotated[
            int,
            Field(default=24, ge=1, le=168, description="Look-back window in hours for trend intents."),
        ] = 24,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Optional Apstra instance name; leave null unless user explicitly specifies it."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Umbrella dispatcher for telemetry workflows.

        Intent mapping:
          - interface_counters -> get_interface_counters
          - interface_utilisation -> get_interface_utilisation
          - system_telemetry -> get_system_telemetry
          - interface_error_trend -> get_interface_error_trend
          - top_error_growers -> get_top_error_growers
        """
        if intent == "interface_counters":
            missing = _missing_args({"system_id": system_id})
            if missing:
                return {
                    "error": "missing_required_parameters",
                    "intent": intent,
                    "missing": missing,
                    "hint": "Set system_id for interface_counters intent.",
                }
            result = await legacy_tools["get_interface_counters"](
                system_id=system_id,
                interface_name=interface_name,
                errors_only=errors_only,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "interface_utilisation":
            missing = _missing_args({"blueprint_id": blueprint_id})
            if missing:
                return {
                    "error": "missing_required_parameters",
                    "intent": intent,
                    "missing": missing,
                    "hint": "Set blueprint_id for interface_utilisation intent.",
                }
            result = await legacy_tools["get_interface_utilisation"](
                blueprint_id=blueprint_id,
                system_id=system_id,
                interface_name=interface_name,
                top_n=top_n,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "system_telemetry":
            if not system_ids and blueprint_id is None:
                return {
                    "error": "missing_required_parameters",
                    "intent": intent,
                    "missing": ["system_ids_or_blueprint_id"],
                    "hint": "Provide either system_ids or blueprint_id for system_telemetry intent.",
                }
            result = await legacy_tools["get_system_telemetry"](
                system_ids=system_ids,
                blueprint_id=blueprint_id,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "interface_error_trend":
            missing = _missing_args({"system_id": system_id, "interface_name": interface_name})
            if missing:
                return {
                    "error": "missing_required_parameters",
                    "intent": intent,
                    "missing": missing,
                    "hint": "Set system_id and interface_name for interface_error_trend intent.",
                }
            result = await legacy_tools["get_interface_error_trend"](
                system_id=system_id,
                interface_name=interface_name,
                hours_back=hours_back,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        result = await legacy_tools["get_top_error_growers"](
            hours_back=hours_back,
            top_n=top_n,
            blueprint_id=blueprint_id,
            instance_name=instance_name,
            ctx=ctx,
        )
        result.setdefault("intent", intent)
        return result
