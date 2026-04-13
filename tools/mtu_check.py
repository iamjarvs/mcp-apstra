from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.mtu_check import handle_get_fabric_mtu_check


def register(mcp):

    @mcp.tool()
    async def get_fabric_mtu_check(
        blueprint_id: Annotated[
            str,
            "Apstra blueprint ID. Use get_blueprints to discover valid values.",
        ],
        focus_systems: Annotated[
            list[str] | None,
            Field(
                default=None,
                description=(
                    "Device labels to scope the analysis (e.g. ['Leaf1', 'Leaf2']). "
                    "When provided, adds a path_analysis section showing effective path MTU "
                    "and the bottleneck interface. For a leaf-to-leaf path, include both leaves."
                ),
            ),
        ] = None,
        issue_description: Annotated[
            str | None,
            Field(default=None, description="Free-text description of the suspected problem, included verbatim in the response for context tracing."),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Audit MTU configuration across a fabric and validate it for VXLAN overlay operation.

        Use this when investigating silent packet drops, intermittent throughput degradation,
        or asymmetric performance across ECMP paths — classic symptoms of MTU misconfiguration.
        Checks physical and L3 (inet) MTU symmetry on both ends of every fabric link, flags
        mismatches that cause silent frame drops, verifies fabric minimums (inet ≥ 9000,
        physical ≥ 9050), and computes VXLAN encapsulation headroom. Use focus_systems to
        scope to a specific path; for a leaf-to-leaf analysis include both leaf labels —
        the shared spine links are automatically included.

        Returns: assessment (ok/warning/critical), issues_count (critical/warning/total),
        vxlan_headroom (fabric_inet_mtu, max_inner_ethernet_frame_bytes, max_inner_ip_payload_bytes,
        can_carry_jumbo_9000_inner), fabric_consistency (per-role inet/physical MTU uniformity,
        ECMP risk), link_mtu_checks (per-link physical and inet MTU on each side with issue list),
        per_system_interface_mtu, path_analysis (only when focus_systems given), issues_summary.
        Data sources: graph DB (L3 MTU) + live rendered config API (physical MTU, one call per device).
        """
        return await handle_get_fabric_mtu_check(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            focus_systems,
            issue_description,
            instance_name,
        )
