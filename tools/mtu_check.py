from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.mtu_check import handle_get_fabric_mtu_check
from handlers.blueprints import resolve_blueprints


def register(mcp):

    @mcp.tool()
    async def get_fabric_mtu_check(
        blueprint_id: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Apstra blueprint ID, partial label, or null. "
                    "Pass null or 'all' for every blueprint. "
                    "Pass a partial name (e.g. 'DC1') to match by label. "
                    "Pass a full UUID for a specific blueprint."
                ),
            ),
        ] = None,
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
        Audit MTU configuration across one or all fabrics and validate it for VXLAN overlay operation.

        Use this when investigating silent packet drops, intermittent throughput degradation,
        or asymmetric performance across ECMP paths — classic symptoms of MTU misconfiguration.
        Checks physical and L3 (inet) MTU symmetry on both ends of every fabric link, flags
        mismatches that cause silent frame drops, verifies fabric minimums (inet ≥ 9000,
        physical ≥ 9050), and computes VXLAN encapsulation headroom. Use focus_systems to
        scope to a specific path; for a leaf-to-leaf analysis include both leaf labels —
        the shared spine links are automatically included.

        Pass blueprint_id=null to audit all blueprints at once.

        Returns: assessment (ok/warning/critical), issues_count (critical/warning/total),
        vxlan_headroom, fabric_consistency, link_mtu_checks, per_system_interface_mtu,
        path_analysis (only when focus_systems given), issues_summary.
        Data sources: graph DB (L3 MTU) + live rendered config API (physical MTU, one call per device).
        """
        sessions = ctx.lifespan_context["sessions"]
        registry = ctx.lifespan_context["graph_registry"]
        blu_list = await resolve_blueprints(sessions, blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}

        if len(blu_list) > 1:
            results = []
            for bp in blu_list:
                r = await handle_get_fabric_mtu_check(sessions, registry, bp["id"], focus_systems, issue_description, instance_name)
                r["blueprint_label"] = bp["label"]
                results.append(r)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "results": results}

        bp = blu_list[0]
        r = await handle_get_fabric_mtu_check(sessions, registry, bp["id"], focus_systems, issue_description, instance_name)
        r["blueprint_label"] = bp["label"]
        return r
