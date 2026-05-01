from typing import Annotated

from fastmcp import Context
from pydantic import Field

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
                "Omit to run on every onboarded switch in the blueprint (parallel)."
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

        Omit system_id to run across the whole blueprint concurrently (can produce a large
        response on large fabrics — scope to a specific system when possible).

        Returns: systems (list with system_id, system_label, endpoint, status, command_results
        (list with command, status, output, error)), system_count.
        Data source: live Apstra fetchcmd API (real-time device CLI).
        """
        return await handle_run_commands(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            commands,
            system_id,
            instance_name,
            timeout_seconds,
            output_format,
            max_concurrent_systems,
        )
