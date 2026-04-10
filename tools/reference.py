from pathlib import Path

from fastmcp import Context

# Resolved once at import time — path is relative to this file's location
# (tools/ → workspace root → _ref_arch/)
_GUIDE_PATH = Path(__file__).parent.parent / "_ref_arch" / "APSTRA-REFERENCE-DESIGN-GUIDE.md"


def register(mcp):

    @mcp.resource("apstra://reference-design-guide")
    def apstra_reference_design_guide() -> str:
        """
        Apstra Reference Design Guide.

        Comprehensive documentation covering all Apstra reference
        architectures and their JunOS configuration patterns. Attach this
        resource to a conversation to give the assistant full Apstra
        architectural context before asking questions.

        Covers:
          - How to read Apstra-generated JunOS configurations (replace:
            directive, configuration hierarchy)
          - Common building blocks: loopbacks, fabric /31s, IRB anycast
            gateways, ESI LAG, BGP underlay/overlay, mac-vrf, VRF routing
            instances, route policy framework
          - 3-Stage Clos fabric — spine/leaf roles, EVPN route reflection,
            inter-device data flow, loop prevention
          - 5-Stage Clos fabric — super-spine tier, pod-spine, multi-pod
            scaling rationale
          - Collapsed Fabric — combined spine+leaf role, WAN connectivity,
            EVPN gateway peering to remote DCs
          - Access switches — ESI uplinks, EVPN without direct BGP to leaf
          - DCI Over The Top (OTT) — EVPN tunnels across existing IP transport
          - DCI Stitching — multi-domain EVPN, VNI translation, interconnect
            feature, firewall chaining
          - BGP community architecture (0:12–0:15 tier tagging, loop
            prevention trace end-to-end)
          - JunOS route policy processing model: && chaining, first-match
            logic, shadow terms, implicit default reject
          - Common policy failure modes and diagnosis
          - MTU standards, BFD timers, ECMP, RSTP, graceful restart, LLDP
          - Configuration quick reference: interface naming, BGP group
            dictionary, routing-instance types
        """
        return _GUIDE_PATH.read_text(encoding="utf-8")

    @mcp.tool()
    async def get_reference_design_context(
        ctx: Context = None,
    ) -> dict:
        """
        Returns the full Apstra Reference Design Guide as structured
        documentation. Use this tool to add Apstra-specific architectural
        context to responses, or to answer questions about how Apstra works.

        WHEN TO CALL THIS TOOL:
        - A user asks how Apstra works, what a reference design is, or asks
          about any architectural concept: Clos fabric, EVPN, VXLAN, DCI,
          ESI LAG, BGP underlay/overlay, IRB, VRF, anycast gateway, etc.
        - Adding context to other tool output — for example:
            * After get_fabric_bgp_peerings: explain the l3clos-l /
              l3clos-l-evpn group naming and what each session does
            * After get_blueprint_configlets: explain what configlets are
              and where they sit in the Apstra config model
            * After get_systems: explain the spine/leaf/access roles and
              what each tier does in the fabric
            * After get_virtual_networks: explain VNI mapping, mac-vrf,
              and how VLANs map to VXLAN segments in Apstra
            * After get_current_anomalies: explain what a BGP or EVPN
              anomaly means in the context of the Apstra fabric model
        - A user asks why a JunOS configuration looks a certain way (replace:
          directive, community tags, multihop EVPN, no-nexthop-change, etc.)
        - A user asks about MTU values, BFD timers, loop prevention, or
          community tagging (0:14, 0:15) in the fabric
        - A user asks about the difference between OTT and stitching DCI
        - A user asks how EVPN loop prevention works across fabric tiers

        WHAT IT RETURNS:
        The complete guide in Markdown, structured into 10 sections with
        subsections. The guide is written specifically for LLM interpretation
        of Apstra-managed JunOS configurations and Apstra design concepts.

        Returns:
            - title:   "Apstra Reference Design Guide"
            - format:  "markdown"
            - content: Full guide text. Extract and cite the relevant
                       sections when composing your response to the user.
        """
        content = _GUIDE_PATH.read_text(encoding="utf-8")
        return {
            "title": "Apstra Reference Design Guide",
            "format": "markdown",
            "content": content,
        }
