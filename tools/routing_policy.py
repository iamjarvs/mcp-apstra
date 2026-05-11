from typing import Annotated, Literal

from fastmcp import Context
from pydantic import Field

from handlers.blueprints import resolve_blueprints
from handlers.routing_policy import handle_routing_policy_intent


def register(mcp):

    @mcp.tool()
    async def routing_policy(
        intent: Annotated[
            Literal[
                "discover",
                "explain_policy",
                "diagnose_hidden_routes",
                "compare_rib",
                "peer_summary",
                "resolve_next_hop",
                "full_audit",
            ],
            Field(
                default="discover",
                description=(
                    "Dispatcher intent. Use discover first to list devices and peers, "
                    "then call a targeted intent."
                ),
            ),
        ] = "discover",
        blueprint_id: Annotated[
            str,
            Field(
                description=(
                    "Required. Apstra blueprint ID or partial label (e.g. 'DC1'). "
                    "Use get_blueprints to list available blueprints and IDs."
                )
            ),
        ] = None,
        device: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional device selector (label, hostname, or system_id). "
                    "Use discover intent if uncertain."
                ),
            ),
        ] = None,
        peer_ip: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional BGP peer IP. Required for most deep intents when a device has multiple peers."
                ),
            ),
        ] = None,
        direction: Annotated[
            Literal["import", "export", "both"],
            Field(default="both", description="Policy direction for explain and audit intents."),
        ] = "both",
        vrf: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional VRF name (e.g. 'blue') or table name (e.g. 'blue.inet.0'). "
                    "Omit for global inet.0."
                ),
            ),
        ] = None,
        prefix: Annotated[
            str | None,
            Field(default=None, description="Optional prefix scope for future policy drill-downs."),
        ] = None,
        next_hop_ip: Annotated[
            str | None,
            Field(default=None, description="Required for resolve_next_hop intent."),
        ] = None,
        limit: Annotated[
            int,
            Field(
                default=20,
                ge=1,
                le=100,
                description="Page size for discover, peer_summary, and hidden-route outputs.",
            ),
        ] = 20,
        cursor: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Pagination cursor returned by previous responses. "
                    "Only valid for single-blueprint calls."
                ),
            ),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Apstra instance name. Leave as None to follow blueprint resolution automatically. "
                    "Set only when user explicitly specifies an instance."
                ),
            ),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Dispatcher tool for JunOS routing-policy troubleshooting in Apstra-managed fabrics.

        Recommended workflow for MCP-to-LLM efficiency:
          1. Call intent='discover' first.
          2. Choose only one targeted intent based on discovered peers/devices.
          3. Use pagination (limit/cursor) for large outputs.

        Intents:
          - discover: list available devices/peers and required fields for next calls
          - peer_summary: BGP peer state and receive/active/send count health checks
          - explain_policy: import/export policy names, terms, and plain-language summary
          - diagnose_hidden_routes: hidden-route reasons with grouping and pagination
          - compare_rib: received vs active/hidden distribution for one peer
          - resolve_next_hop: next-hop reachability and resolution table/interface
          - full_audit: combined snapshot (expensive, use for investigations)
        """
        sessions = ctx.lifespan_context["sessions"]
        registry = ctx.lifespan_context["graph_registry"]

        blu_list = await resolve_blueprints(sessions, blueprint_id)
        if not blu_list:
            return {
                "error": f"No blueprints found matching '{blueprint_id}'",
                "hint": "Call get_blueprints to list available blueprints and labels.",
            }

        if len(blu_list) > 1 and cursor is not None:
            return {
                "error": "cursor_not_supported_for_multi_blueprint",
                "hint": "Set blueprint_id to a single blueprint before using cursor pagination.",
            }

        results = []
        for bp in blu_list:
            effective_instance = instance_name or bp.get("instance_name")
            result = await handle_routing_policy_intent(
                sessions,
                registry,
                blueprint_id=bp["id"],
                intent=intent,
                device=device,
                peer_ip=peer_ip,
                direction=direction,
                vrf=vrf,
                prefix=prefix,
                next_hop_ip=next_hop_ip,
                limit=limit,
                cursor=cursor,
                instance_name=effective_instance,
            )
            result["blueprint_label"] = bp.get("label")
            results.append(result)

        if len(results) == 1:
            return results[0]

        return {
            "blueprint_count": len(results),
            "blueprint_ref": blueprint_id,
            "intent": intent,
            "results": results,
        }
