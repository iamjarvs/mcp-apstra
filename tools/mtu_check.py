from fastmcp import Context

from handlers.mtu_check import handle_get_fabric_mtu_check


def register(mcp):

    @mcp.tool()
    async def get_fabric_mtu_check(
        blueprint_id: str,
        focus_systems: list[str] | None = None,
        issue_description: str | None = None,
        instance_name: str | None = None,
        ctx: Context = None,
    ) -> dict:
        """
        Audits MTU configuration across a fabric and validates it is correct
        for VXLAN overlay operation.

        Two data sources are combined:
          1. Apstra graph (fast): L3/inet MTU per interface from the model.
          2. Rendered JunOS config (live API, one call per system): physical
             (frame) MTU per interface — the value not stored in the graph.

        Checks performed on every fabric link:
          — Physical MTU symmetry: both ends of every link must match. A
            mismatch causes unpredictable frame-size drops because the lower
            side silently discards oversized frames.
          — L3 (inet) MTU symmetry: both ends must match. BGP sessions stay
            up but oversized IP packets are dropped without ICMP Frag-Needed
            messages in many deployments.
          — Physical vs inet relationship: inet MTU should be physical MTU
            minus L2 overhead (14–24 bytes). The Apstra standard is 22 bytes
            (ETH 14 + 802.1Q VLAN 4 + FCS 4). If the gap is larger than 24
            bytes, the virtual/inet MTU is probably set too low — this is the
            classic fabric MTU misconfiguration where the physical interface
            looks correct but IP traffic suffers degraded throughput or
            fragmentation.
          — Fabric minimum: fabric-facing interfaces should have inet MTU ≥
            9000 and physical MTU ≥ 9050 to support VXLAN-encapsulated jumbo
            inner traffic.

        VXLAN overhead analysis:
          VXLAN adds 50 bytes of encapsulation overhead to every inner frame
          (outer ETH 14 + IP 20 + UDP 8 + VXLAN header 8). With fabric inet
          MTU 9170, the maximum inner Ethernet frame is 9120 and the maximum
          inner IP payload is 9106 bytes — well above the 9000-byte jumbo
          threshold. The tool reports these numbers explicitly so you can
          evaluate whether end-to-end jumbo inner traffic is possible.

        Data-source notes:
          Physical MTU is read from the rendered JunOS config
          (GET /api/blueprints/{id}/systems/{serial}/config-rendering).
          L3 MTU is read from the Apstra graph model. Both should reflect the
          same intended design; discrepancies between them indicate a bug in
          Apstra's config generation or an out-of-band manual change.

        Data source for L3 MTU: graph database.
        Data source for physical MTU: live network (rendered config API).

        Use get_blueprints to discover valid blueprint_id values.
        Use get_systems if you need to verify system labels.

        Args:
            blueprint_id:       The Apstra blueprint ID to audit.
            focus_systems:      Optional list of system labels (e.g.
                                ["Leaf1", "Leaf2"]) to scope the analysis
                                to specific devices. When provided:
                                  - Only links where at least one endpoint
                                    matches are included.
                                  - A path_analysis section is returned
                                    showing effective path MTU, the
                                    bottleneck interface, and the maximum
                                    inner IP payload available through the
                                    focused set.
                                Use this when investigating a reported MTU
                                problem on a specific path. For a leaf-to-
                                leaf path analysis, include both leaves (the
                                shared spine links appear automatically).
            issue_description:  Optional free-text description of the
                                suspected problem. Included verbatim in the
                                response for context tracing.
            instance_name:      Optional. The Apstra instance name (from
                                instances.yaml). If omitted, all instances
                                are queried and results are merged.

        Returns:
            instance:               Apstra instance name
            blueprint_id:           Blueprint queried
            focus_systems:          Filtered system labels (or null)
            issue_description:      The description provided (or null)
            assessment:             "ok" / "warning" / "critical" — overall
                                    fabric MTU health
            issues_count:           {critical, warning, total}
            vxlan_headroom:         {fabric_inet_mtu, vxlan_overhead_bytes,
                                     max_inner_ethernet_frame_bytes,
                                     max_inner_ip_payload_bytes,
                                     can_carry_standard_1500_inner,
                                     can_carry_jumbo_9000_inner, assessment}
                                    Uses the LOWEST inet MTU seen across the
                                    fabric so the figure reflects the worst-
                                    case ECMP path.
            mtu_reference:          Reference constants used for validation
                                    (vxlan_overhead_bytes,
                                    recommended_fabric_physical_mtu, etc.)
            fabric_consistency:     Fabric-wide MTU uniformity analysis.
                                    Key fields:
                                      by_role — per link-role breakdown:
                                        link_count
                                        inet_mtu_values (sorted distinct)
                                        physical_mtu_values (sorted distinct)
                                        inet_mtu_consistent (bool)
                                        physical_mtu_consistent (bool)
                                      fabric_wide_inet_mtu_consistent (bool)
                                      fabric_wide_physical_mtu_consistent (bool)
                                      effective_bottleneck_inet_mtu — lowest
                                        inet MTU seen on any fabric link
                                      ecmp_risk — "none" or "critical"
                                      ecmp_risk_explanation — prose explanation
                                        of what happens to packets between the
                                        min and max observed MTU values
            link_mtu_checks:        List of per-link results. Each item has:
                                      link_id, link_role, speed,
                                      a_side / b_side:
                                        {system, role, interface, ip_address,
                                         physical_mtu, inet_mtu},
                                      mtu_ok (bool),
                                      issues (list)
            per_system_interface_mtu: Dict of system_label → {if_name →
                                      {physical_mtu, inet_mtu, inet_address}}
                                      for quick per-device scan.
            path_analysis:          (only when focus_systems given)
                                      {focus_systems, links_analysed,
                                       effective_path_inet_mtu,
                                       bottleneck_interface,
                                       max_inner_ip_payload, note}
            rendered_config_errors: List of systems where rendered config
                                    could not be fetched (or null).
            issues_summary:         Flat list of all issues found, each with
                                    {check, severity, message}.

        Issue check names:
            physical_mtu_asymmetry         — physical MTU differs across a link
            inet_mtu_asymmetry             — L3 MTU differs across a link
            inet_exceeds_physical          — inet MTU ≥ physical MTU
            inet_mtu_too_low               — inet MTU unusually far below
                                             physical MTU (virtual MTU too small)
            fabric_inet_mtu_too_low        — inet MTU below 9000 on fabric link
            fabric_physical_mtu_too_low    — physical MTU below 9050 on
                                             fabric link
            fabric_inet_mtu_inconsistency  — different fabric links of the same
                                             role have different inet MTU values;
                                             ECMP will route some flows through
                                             lower-MTU paths causing intermittent
                                             silent drops
            fabric_physical_mtu_inconsistency — same as above for physical MTU

        Typical healthy Apstra VXLAN fabric values:
            physical MTU  : 9192 on spine/leaf-facing and peer-link ports
            inet MTU      : 9170 (= 9192 − 22)
            VXLAN headroom: 9170 − 50 = 9120 inner Ethernet
                            9120 − 14 = 9106 inner IP payload
        """
        return await handle_get_fabric_mtu_check(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            focus_systems,
            issue_description,
            instance_name,
        )
