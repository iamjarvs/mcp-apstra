from typing import Annotated, Literal

from fastmcp import Context
from pydantic import Field

from tools import virtual_networks as virtual_networks_tool


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
    legacy_tools = _capture_registered_tools(virtual_networks_tool.register)

    @mcp.tool()
    async def virtual_networks(
        intent: Annotated[
            Literal[
                "deployments",
                "list",
                "routing_zones",
                "routing_zone_detail",
                "virtual_network_detail",
            ],
            Field(default="list", description="Dispatcher intent for VN and routing-zone workflows."),
        ] = "list",
        blueprint_id: Annotated[
            str | None,
            Field(default=None, description="Required for all virtual_networks intents."),
        ] = None,
        system_id: Annotated[
            str | None,
            Field(default=None, description="Optional hardware system_id for deployments intent."),
        ] = None,
        vn_label: Annotated[
            str | None,
            Field(default=None, description="Optional VN label filter for deployments intent."),
        ] = None,
        routing_zone: Annotated[
            str | None,
            Field(default=None, description="Required for routing_zone_detail intent."),
        ] = None,
        virtual_network: Annotated[
            str | None,
            Field(default=None, description="Required for virtual_network_detail intent."),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Optional Apstra instance name; leave null unless user explicitly specifies it."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Umbrella dispatcher for virtual network and routing-zone workflows.

        Intent mapping:
          - deployments -> get_vn_deployments
          - list -> get_virtual_networks
          - routing_zones -> get_routing_zones
          - routing_zone_detail -> get_routing_zone_detail
          - virtual_network_detail -> get_virtual_network_detail
        """
        missing = _missing_args({"blueprint_id": blueprint_id})
        if missing:
            return {
                "error": "missing_required_parameters",
                "intent": intent,
                "missing": missing,
                "hint": "Set blueprint_id for virtual_networks intents.",
            }

        if intent == "deployments":
            result = await legacy_tools["get_vn_deployments"](
                blueprint_id=blueprint_id,
                system_id=system_id,
                vn_label=vn_label,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "list":
            result = await legacy_tools["get_virtual_networks"](
                blueprint_id=blueprint_id,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "routing_zones":
            result = await legacy_tools["get_routing_zones"](
                blueprint_id=blueprint_id,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        if intent == "routing_zone_detail":
            missing = _missing_args({"routing_zone": routing_zone})
            if missing:
                return {
                    "error": "missing_required_parameters",
                    "intent": intent,
                    "missing": missing,
                    "hint": "Set routing_zone for routing_zone_detail intent.",
                }
            result = await legacy_tools["get_routing_zone_detail"](
                blueprint_id=blueprint_id,
                routing_zone=routing_zone,
                instance_name=instance_name,
                ctx=ctx,
            )
            result.setdefault("intent", intent)
            return result

        missing = _missing_args({"virtual_network": virtual_network})
        if missing:
            return {
                "error": "missing_required_parameters",
                "intent": intent,
                "missing": missing,
                "hint": "Set virtual_network for virtual_network_detail intent.",
            }

        result = await legacy_tools["get_virtual_network_detail"](
            blueprint_id=blueprint_id,
            virtual_network=virtual_network,
            instance_name=instance_name,
            ctx=ctx,
        )
        result.setdefault("intent", intent)
        return result
