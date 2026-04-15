from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.system_health import handle_get_system_liveness, handle_get_config_deviations
from handlers.blueprints import resolve_blueprints

_BP_DESC = (
    "Apstra blueprint ID, partial label, or null. "
    "Pass null or 'all' for every blueprint. "
    "Pass a partial name (e.g. 'DC1') to match by label. "
    "Pass a full UUID for a specific blueprint."
)


def register(mcp):

    @mcp.tool()
    async def get_system_liveness(
        blueprint_id: Annotated[str | None, Field(default=None, description=_BP_DESC)] = None,
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
        Check which systems in one or all blueprints are unreachable according to Apstra liveness anomalies.

        CALL THIS TOOL FIRST before any per-device troubleshooting.

        A device that appears here has failed Apstra's liveness check — one or more of its
        management or telemetry agents are not responding. This means:
          - CLI commands (run_device_commands) will fail or time out for that device
          - Interface counters may be stale
          - BGP/BFD anomalies on that device are EXPECTED — the root cause is likely the
            device itself being unreachable, not a protocol-level problem
          - Config rendering and system context data may be outdated

        If any device is unreachable, present this fact to the user before starting any
        other investigation. Troubleshooting BGP or interfaces on a device that Apstra
        cannot reach will give misleading results.

        Pass blueprint_id=null to check all blueprints at once.

        Returns:
          all_systems_reachable (bool): True only when zero liveness anomalies are present
          unreachable_count (int): Number of systems with liveness anomalies
          liveness_anomalies (list): Per-device details including role, severity,
            expected_agent_count, responding_agent_count, non_responding_agents,
            all_agents_alive

        Data source: live Apstra API (real-time, no cache).
        """
        sessions = ctx.lifespan_context["sessions"]
        blu_list = await resolve_blueprints(sessions, blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}

        if len(blu_list) > 1:
            results = []
            for bp in blu_list:
                r = await handle_get_system_liveness(sessions, bp["id"], instance_name)
                r["blueprint_label"] = bp["label"]
                results.append(r)
            total_unreachable = sum(r.get("unreachable_count", 0) for r in results)
            return {
                "blueprint_count": len(results),
                "blueprint_ref": blueprint_id,
                "total_unreachable": total_unreachable,
                "all_systems_reachable": total_unreachable == 0,
                "results": results,
            }

        bp = blu_list[0]
        r = await handle_get_system_liveness(sessions, bp["id"], instance_name)
        r["blueprint_label"] = bp["label"]
        return r

    @mcp.tool()
    async def get_config_deviations(
        blueprint_id: Annotated[str | None, Field(default=None, description=_BP_DESC)] = None,
        system_id: Annotated[
            str | list[str] | None,
            Field(
                default=None,
                description=(
                    "Hardware chassis serial(s) to check (e.g. '5254002D005F'). "
                    "Use the system_id field from get_systems — NOT the graph node id. "
                    "Pass None to check every system in the blueprint."
                ),
            ),
        ] = None,
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
        Check for configuration drift between Apstra's intent (expected) and what is actually
        running on each device (actual). Flags any system where deploy_state is "deviated".

        CALL THIS TOOL EARLY in any troubleshooting workflow, alongside get_system_liveness.

        A deviated system means the device's live configuration no longer matches what Apstra
        has deployed. This happens when:
          - Someone has logged into the device and made a manual change outside Apstra
          - A commit script or ephemeral config has injected lines not present in Apstra intent
          - A previous Apstra push failed partway through, leaving the device in a mixed state

        The diff returned uses unified-diff format:
          Lines starting with '-' are in Apstra's INTENT but MISSING from the device
            → something was removed from the device outside of Apstra
          Lines starting with '+' are on the DEVICE but NOT in Apstra's intent
            → something was manually added to the device
          Context lines (no prefix) show surrounding config for location reference

        Pass blueprint_id=null to scan all blueprints at once.

        Parameters:
          blueprint_id: target blueprint(s) — null = all, partial name, or full UUID
          system_id: one serial, a list of serials, or None to scan the entire blueprint

        Returns:
          all_compliant (bool), deviated_count (int), total_checked (int),
          deviations (list with system_id, hostname, deploy_state, diff),
          compliant_systems (list)

        Data source: live Apstra API — reads actual device config in real time.
        """
        sessions = ctx.lifespan_context["sessions"]
        registry = ctx.lifespan_context["graph_registry"]
        blu_list = await resolve_blueprints(sessions, blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}

        if len(blu_list) > 1:
            results = []
            for bp in blu_list:
                r = await handle_get_config_deviations(sessions, registry, bp["id"], system_id, instance_name)
                r["blueprint_label"] = bp["label"]
                results.append(r)
            total_deviated = sum(r.get("deviated_count", 0) for r in results)
            return {
                "blueprint_count": len(results),
                "blueprint_ref": blueprint_id,
                "total_deviated": total_deviated,
                "all_compliant": total_deviated == 0,
                "results": results,
            }

        bp = blu_list[0]
        r = await handle_get_config_deviations(sessions, registry, bp["id"], system_id, instance_name)
        r["blueprint_label"] = bp["label"]
        return r


    @mcp.tool()
    async def get_system_liveness(
        blueprint_id: Annotated[
            str,
            "Apstra blueprint ID. Use get_blueprints to discover valid values.",
        ],
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
        Check which systems in a blueprint are unreachable according to Apstra liveness anomalies.

        CALL THIS TOOL FIRST before any per-device troubleshooting.

        A device that appears here has failed Apstra's liveness check — one or more of its
        management or telemetry agents are not responding. This means:
          - CLI commands (run_device_commands) will fail or time out for that device
          - Interface counters may be stale
          - BGP/BFD anomalies on that device are EXPECTED — the root cause is likely the
            device itself being unreachable, not a protocol-level problem
          - Config rendering and system context data may be outdated

        If any device is unreachable, present this fact to the user before starting any
        other investigation. Troubleshooting BGP or interfaces on a device that Apstra
        cannot reach will give misleading results.

        Returns:
          all_systems_reachable (bool): True only when zero liveness anomalies are present
          unreachable_count (int): Number of systems with liveness anomalies
          liveness_anomalies (list): Per-device details including:
            - role: leaf/spine/etc
            - identity: system identifiers as stored in Apstra
            - severity: typically "critical"
            - last_modified_at: when the anomaly was last updated
            - expected_agent_count: how many agents Apstra expects to hear from
            - responding_agent_count: how many are actually responding
            - non_responding_agents: list of agent IDs not currently responding
            - all_agents_alive: whether Apstra considers the device alive at protocol level

        An empty liveness_anomalies list confirms all devices in the blueprint are reachable.

        Data source: live Apstra API (real-time, no cache).
        """
        return await handle_get_system_liveness(
            ctx.lifespan_context["sessions"],
            blueprint_id,
            instance_name,
        )

    @mcp.tool()
    async def get_config_deviations(
        blueprint_id: Annotated[
            str,
            "Apstra blueprint ID. Use get_blueprints to discover valid values.",
        ],
        system_id: Annotated[
            str | list[str] | None,
            Field(
                default=None,
                description=(
                    "Hardware chassis serial(s) to check (e.g. '5254002D005F'). "
                    "Use the system_id field from get_systems — NOT the graph node id. "
                    "Pass None to check every system in the blueprint."
                ),
            ),
        ] = None,
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
        Check for configuration drift between Apstra's intent (expected) and what is actually
        running on each device (actual). Flags any system where deplopy_state is "deviated".

        CALL THIS TOOL EARLY in any troubleshooting workflow, alongside get_system_liveness.

        A deviated system means the device's live configuration no longer matches what Apstra
        has deployed. This happens when:
          - Someone has logged into the device and made a manual change outside Apstra
          - A commit script or ephemeral config has injected lines not present in Apstra intent
          - A previous Apstra push failed partway through, leaving the device in a mixed state

        The diff returned uses unified-diff format:
          Lines starting with '-' are in Apstra's INTENT but MISSING from the device
            → something was removed from the device outside of Apstra
          Lines starting with '+' are on the DEVICE but NOT in Apstra's intent
            → something was manually added to the device
          Context lines (no prefix) show surrounding config for location reference

        If a deviated device also shows BGP failures, interface anomalies, or other issues,
        investigate the deviation first — the manual config change may be the root cause.

        Parameters:
          blueprint_id: target blueprint (required — use get_blueprints to discover)
          system_id: one serial, a list of serials, or None to scan the entire blueprint

        Returns:
          all_compliant (bool): True only when zero deviations found
          deviated_count (int): Number of systems with config drift
          total_checked (int): Number of systems examined
          deviations (list): Per-device entry with:
            - system_id, hostname, deploy_state, deviated
            - diff (unified diff string — only present when deviated=True)
            - error_message (Apstra error if it could not read the live config)
            - contiguous_failures (number of consecutive failed config reads)
          compliant_systems (list): Systems checked and found in compliance

        Data source: live Apstra API — reads actual device config in real time.
        """
        return await handle_get_config_deviations(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            system_id,
            instance_name,
        )
