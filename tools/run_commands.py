from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.blueprints import resolve_blueprints
from handlers.run_commands import handle_run_commands


def register(mcp):

    @mcp.tool()
    async def run_device_commands(
        blueprint_id: Annotated[
            str,
            Field(description=(
                "Required. Apstra blueprint ID or partial label (e.g. 'DC1'). "
                "Use get_blueprints to list available blueprints and their IDs. "
                "Pass a full UUID for a specific blueprint."
            )),
        ],
        commands: Annotated[
            list[str],
            Field(description=(
                "JunOS CLI commands to run (e.g. ['show bgp summary', 'show interfaces terse']). "
                "If unsure of exact syntax, call get_junos_show_commands first."
            )),
        ],
        system_id: Annotated[
            str | None,
            Field(default=None, description=(
                "Hardware chassis serial of the target switch (e.g. '5254002D005F'). "
                "Use the system_id field from get_systems — NOT the id field. "
                "Prefer hostname if you know it; omit both to run on every switch."
            )),
        ] = None,
        hostname: Annotated[
            str | None,
            Field(default=None, description=(
                "Device hostname/label as shown in Apstra (e.g. 'Leaf2', 'Spine1'). "
                "Automatically resolved to system_id — use this instead of looking up "
                "the hardware serial manually. Ignored if system_id is also provided."
            )),
        ] = None,
        output_format: Annotated[
            str,
            Field(default="json", description="'json' for structured output (default). Use 'text' for commands that don't support JSON."),
        ] = "json",
        timeout_seconds: Annotated[
            int,
            Field(default=30, description="Per-system command timeout in seconds. Increase for slow commands like 'show route' on large tables. Default 30."),
        ] = 30,
        max_concurrent_systems: Annotated[
            int,
            Field(default=10, description="Max switches queried in parallel when system_id is omitted. Default 10, hard-capped at 20."),
        ] = 10,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Run one or more JunOS CLI commands on a switch (or all switches) in a blueprint via Apstra.

        Use this to inspect live device state — routing tables, BGP sessions, BFD, LLDP,
        interface detail, logs — whenever Apstra's graph data is insufficient or you need to
        verify the actual running state.

        IMPORTANT — JunOS syntax: Call get_junos_show_commands first if you are unsure of the
        exact command. Common differences from IOS/EOS:
          - "show bgp summary" / "show bgp neighbor <ip>"  (NOT "show bgp neighbors")
          - "show bfd session"  (NOT "show bfd sessions")
          - "show route"  (NOT "show ip route")
          - "show interfaces terse"  (NOT "show interfaces brief")
        If a result has result="commandShellError", read the llm_hint field, look up the correct
        syntax with get_junos_show_commands, and retry with corrected commands.

        Omit system_id and hostname to run across the whole blueprint concurrently (can produce
        a large response on large fabrics — scope to a specific system when possible).

        Returns: systems (list with system_id, system_label, endpoint, status, command_results
        (list with command, status, output, error)), system_count.
        Data source: live Apstra fetchcmd API (real-time device CLI).
        """
        sessions = ctx.lifespan_context["sessions"]
        blu_list = await resolve_blueprints(sessions, blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'", "hint": "Call get_blueprints to list available blueprints and their labels."}

        # Resolve hostname → system_id if hostname provided and system_id is not
        resolved_system_id = system_id
        if hostname and not system_id:
            try:
                from handlers.systems import handle_get_systems
                registry = ctx.lifespan_context["graph_registry"]
                sys_result = await handle_get_systems(sessions, registry, blu_list[0]["id"], instance_name)
                systems = sys_result.get("systems", [])
                match = next((s for s in systems if s.get("label", "").lower() == hostname.lower()), None)
                if not match:
                    available = [s.get("label") for s in systems]
                    return {"error": f"No device with hostname '{hostname}' found in blueprint.", "available_hostnames": available}
                resolved_system_id = match["system_id"]
            except Exception as exc:
                return {"error": f"Failed to resolve hostname '{hostname}' to system_id: {exc}"}

        results = []
        for bp in blu_list:
            r = await handle_run_commands(
                sessions,
                ctx.lifespan_context["graph_registry"],
                bp["id"],
                commands,
                system_id=resolved_system_id,
                instance_name=instance_name,
                timeout_seconds=timeout_seconds,
                output_format=output_format,
                max_concurrent_systems=max_concurrent_systems,
            )
            results.append(r)

        if len(results) == 1:
            return results[0]
        return {"blueprint_count": len(results), "results": results}
