from fastmcp import Context

from handlers.run_commands import handle_run_commands


def register(mcp):

    @mcp.tool()
    async def run_device_commands(
        blueprint_id: str,
        commands: list[str],
        system_id: str = None,
        output_format: str = "json",
        timeout_seconds: int = 30,
        max_concurrent_systems: int = 10,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Runs one or more CLI commands on a specific switch, or on every switch
        in a blueprint, via the Apstra telemetry fetchcmd API.

        This is the primary tool for inspecting live device state — routing
        tables, interface counters, BGP sessions, LLDP neighbours, logs, etc.
        Use it whenever you need data that Apstra's graph does not model, or
        to verify the actual running state against the design intent.

        Apstra version compatibility
        ----------------------------
        Newer Apstra versions support a batch endpoint that accepts multiple
        commands in a single request.  Older versions are automatically detected
        and a per-command fallback is used transparently — the output shape is
        the same either way, though the "endpoint" field in the result will show
        "multiple" or "single" so you can tell which was used.

        Command format
        --------------
        Pass standard JunOS CLI commands exactly as you would type them on the
        device.  Examples:
          - "show version"
          - "show bgp summary"
          - "show interfaces ge-0/0/0 detail"
          - "show route table inet.0 summary"

        For JSON-structured output on commands that support it, set
        output_format="json".  Most operational commands support JSON; use
        output_format="text" (the default) for commands that do not.

        All-systems mode
        ----------------
        Omit system_id to run the same commands on every onboarded switch in
        the blueprint concurrently.  This is fast (parallel execution) but
        produces a large response for large fabrics.  Be specific about which
        commands you actually need rather than running broad commands on all
        systems simultaneously.

        Use get_systems to discover valid system_id values (hardware chassis
        serial numbers such as "5254002D005F").

        Args:
            blueprint_id:    The Apstra blueprint the target system(s) belong to.
            commands:        List of CLI commands to run. Each command is run as
                             a separate request in fallback mode, or batched in
                             newer Apstra versions.
            system_id:       Optional. Hardware chassis serial of the target
                             switch. If omitted, commands run on all onboarded
                             switches in the blueprint.
            output_format:   "text" (default) for raw CLI output string, or
                             "json" for structured Junos JSON output (only works
                             for commands that support display json).
            timeout_seconds: How long to wait for each system's commands to
                             complete before returning a "timeout" status.
                             Default 30 seconds. Increase for slow commands such
                             as "show route" on large tables.
            max_concurrent_systems: Maximum number of switches to query at the
                             same time. Default 10. Increase (up to ~20) for
                             larger fabrics where you want faster completion;
                             decrease if the Apstra instance is under load.
                             Only applies when system_id is omitted.
            instance_name:   Optional. The Apstra instance to query (as defined
                             in instances.yaml). If omitted, all instances are
                             queried and results are merged.

        Returns:
            When querying a single instance:
              - instance: name of the Apstra instance queried
              - blueprint_id: the blueprint queried
              - systems: list of per-system result objects, each with:
                  system_id    — hardware chassis serial
                  system_label — human-readable device name
                  endpoint     — "multiple" or "single" (which API was used)
                  status       — "success", "error", or "timeout"
                  command_results — list of command outputs (batch mode), or
                                    structured per-command results (single mode)
                                    each with: command, status, output, error
                  raw          — raw API response (batch mode only)
                  error        — error message if status is "error"
              - system_count: number of systems in the results list

            When querying all instances:
              - instance: "all"
              - blueprint_id: the blueprint queried
              - results: list of per-instance result objects (same shape)
              - total_system_count: sum of systems across all instances

        Note: "all systems" runs commands concurrently across all switches and
        can generate a very large response on large fabrics.  Prefer scoping to
        a specific system_id when possible, or filter to only the commands you
        actually need.
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
