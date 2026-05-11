from typing import Annotated, Literal

from fastmcp import Context
from pydantic import Field

from tools import probes as probes_tool


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
    legacy_tools = _capture_registered_tools(probes_tool.register)

    @mcp.tool()
    async def probes(
        intent: Annotated[
            Literal["list", "detail", "history"],
            Field(default="list", description="Dispatcher intent for IBA probe workflows."),
        ] = "list",
        blueprint_id: Annotated[
            str | None,
            Field(default=None, description="Required blueprint ID or partial label."),
        ] = None,
        probe_id: Annotated[
            str | None,
            Field(default=None, description="Required for detail and history intents."),
        ] = None,
        stage: Annotated[
            str | None,
            Field(default=None, description="Optional for detail; required for history intent."),
        ] = None,
        anomalous_only: Annotated[
            bool,
            Field(default=False, description="For list/detail intents: return anomalous rows only when supported."),
        ] = False,
        hours_back: Annotated[
            int,
            Field(default=1, ge=1, le=168, description="History look-back window in hours."),
        ] = 1,
        end_time: Annotated[
            str | None,
            Field(default=None, description="Optional ISO-8601 UTC end time for history intent."),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Optional Apstra instance name; leave null unless user explicitly specifies it."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Umbrella dispatcher for IBA probe workflows.

        Intent mapping:
          - list -> get_probe_list
          - detail -> get_probe_detail
          - history -> get_probe_history
        """
        missing = _missing_args({"blueprint_id": blueprint_id})
        if missing:
            return {
                "error": "missing_required_parameters",
                "intent": intent,
                "missing": missing,
                "hint": "Set blueprint_id for probes intents.",
            }

        if intent == "list":
            result = await legacy_tools["get_probe_list"](
                blueprint_id=blueprint_id,
                anomalous_only=anomalous_only,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "detail":
            missing = _missing_args({"probe_id": probe_id})
            if missing:
                return {
                    "error": "missing_required_parameters",
                    "intent": intent,
                    "missing": missing,
                    "hint": "Set probe_id for detail intent.",
                }
            result = await legacy_tools["get_probe_detail"](
                blueprint_id=blueprint_id,
                probe_id=probe_id,
                stage=stage,
                anomalous_only=anomalous_only,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        missing = _missing_args({"probe_id": probe_id, "stage": stage})
        if missing:
            return {
                "error": "missing_required_parameters",
                "intent": intent,
                "missing": missing,
                "hint": "Set probe_id and stage for history intent.",
            }

        result = await legacy_tools["get_probe_history"](
            blueprint_id=blueprint_id,
            probe_id=probe_id,
            stage=stage,
            hours_back=hours_back,
            end_time=end_time,
            instance_name=instance_name,
            ctx=ctx,
        )
        result.setdefault("intent", intent)
        return result
