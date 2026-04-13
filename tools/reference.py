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

    @mcp.tool()
    async def get_junos_show_commands(
        ctx: Context = None,
    ) -> dict:
        """
        Return a categorised reference of Junos operational show commands for use with run_device_commands.

        IMPORTANT: All commands in this reference are for Junos OS only (QFX, EX, MX, PTX
        series). Do not use these commands on non-Juniper platforms.

        Call this before composing a run_device_commands request whenever you need to know
        the correct command syntax. The returned reference is organised by topic
        (routing, BGP, EVPN, VXLAN, MAC/L2, BFD, interfaces, VRF/routing-instances,
        spanning-tree, optical, connectivity, NTP/services, logs, security, system) and each
        entry includes the command string, a description of what it returns, and whether JSON
        output is supported (output_format="json" vs "text"). Placeholders in angle brackets
        (e.g. <prefix>) must be replaced with actual values before calling run_device_commands.

        Returns: platform, categories (list), each with name, description, and commands (list
        of {command, description, json_supported, notes}).
        """
        return {
            "title": "Junos Show Command Reference",
            "platform": "Junos OS only (QFX, EX, MX, PTX series). Not applicable to non-Juniper platforms.",
            "note": (
                "Pass commands verbatim to run_device_commands. "
                "Replace angle-bracket placeholders with real values before calling run_device_commands. "
                "Use output_format='json' only when json_supported=true — "
                "some commands produce malformed JSON; fall back to output_format='text' if the result is empty or unparseable."
            ),
            "categories": [
                {
                    "name": "routing",
                    "description": "Routing table inspection and route verification.",
                    "commands": [
                        {
                            "command": "show route",
                            "description": "All routes in all routing tables.",
                            "json_supported": True,
                            "notes": "Large output on production devices; prefer 'show route table inet.0 summary'.",
                        },
                        {
                            "command": "show route summary",
                            "description": "Route count per protocol per table — fast health check.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route table inet.0",
                            "description": "IPv4 unicast routes only.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route table inet6.0",
                            "description": "IPv6 unicast routes.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route <prefix>",
                            "description": "All routes matching a specific prefix or exact host (e.g. '10.0.0.1/32').",
                            "json_supported": True,
                        },
                        {
                            "command": "show route <prefix> exact",
                            "description": "Only the exact prefix match — suppresses longer-match entries.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route <prefix> detail",
                            "description": "Full attributes for matching routes: next-hop, communities, AS path, local-pref, MED.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route protocol bgp",
                            "description": "Only BGP-learned routes across all tables.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route protocol evpn",
                            "description": "EVPN routes (type-2 MAC/IP, type-3 IMET, type-5 IP prefix).",
                            "json_supported": True,
                        },
                        {
                            "command": "show route protocol static",
                            "description": "Static routes only — useful to confirm manually configured default routes or management routes.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route advertising-protocol bgp <neighbor-ip>",
                            "description": "Routes being advertised to a specific BGP peer.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route receive-protocol bgp <neighbor-ip>",
                            "description": "Routes received from a specific BGP peer before local policy.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route forwarding-table",
                            "description": "Forwarding plane (FIB) routing table — what is actually programmed into the PFE hardware.",
                            "json_supported": True,
                            "notes": "Useful to verify that RIB routes have been correctly installed in hardware. Compare with 'show route' to detect RIB/FIB divergence.",
                        },
                        {
                            "command": "show route forwarding-table destination <prefix>",
                            "description": "Forwarding table entry for one specific destination — shows resolved next-hop and egress interface.",
                            "json_supported": True,
                        },
                    ],
                },
                {
                    "name": "bgp",
                    "description": "BGP session state, prefix exchange, and policy troubleshooting.",
                    "commands": [
                        {
                            "command": "show bgp summary",
                            "description": "All BGP peers with session state, uptime, prefixes sent/received. Primary BGP health check.",
                            "json_supported": True,
                        },
                        {
                            "command": "show bgp neighbor",
                            "description": "Detailed state for all BGP neighbors including timers, capabilities, hold time, and error counters.",
                            "json_supported": True,
                        },
                        {
                            "command": "show bgp neighbor <ip>",
                            "description": "Detailed state for one BGP neighbor (replace <ip> with peer address).",
                            "json_supported": True,
                        },
                        {
                            "command": "show bgp group",
                            "description": "BGP group configuration and per-group session count. Use to map Apstra group names (l3clos-l, l3clos-l-evpn, etc.) to peers.",
                            "json_supported": True,
                        },
                        {
                            "command": "show bgp group <group-name>",
                            "description": "Sessions within one BGP group.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route advertising-protocol bgp <ip> <prefix>",
                            "description": "Check whether a specific prefix is being advertised to a peer and with what attributes.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route receive-protocol bgp <ip> <prefix>",
                            "description": "Check whether a specific prefix was received from a peer.",
                            "json_supported": True,
                        },
                    ],
                },
                {
                    "name": "bfd",
                    "description": "BFD (Bidirectional Forwarding Detection) session state. Apstra enables BFD on all BGP sessions by default — a BFD failure will tear down the BGP session immediately.",
                    "commands": [
                        {
                            "command": "show bfd session",
                            "description": "All BFD sessions with state (Up/Down/Init), local/remote discriminator, interval, and multiplier.",
                            "json_supported": True,
                        },
                        {
                            "command": "show bfd session detail",
                            "description": "Full BFD session detail including adaptive timers, transition history, and client (BGP) that owns each session.",
                            "json_supported": True,
                        },
                        {
                            "command": "show bfd session summary",
                            "description": "Count of BFD sessions by state — fast fabric-wide health check.",
                            "json_supported": True,
                        },
                    ],
                },
                {
                    "name": "evpn",
                    "description": "EVPN overlay state: MAC/IP database, IMET routes, ESI multihoming, and VNI bindings.",
                    "commands": [
                        {
                            "command": "show evpn instance",
                            "description": "All EVPN instances (mac-vrfs) with IRB/interface bindings and route counts.",
                            "json_supported": True,
                        },
                        {
                            "command": "show evpn instance <name> extensive",
                            "description": "Full detail for one EVPN instance: ESI, designated-forwarder state, all members, statistics.",
                            "json_supported": True,
                        },
                        {
                            "command": "show evpn database",
                            "description": "MAC and IP entries learned via EVPN across all instances — shows originating VTEP and route type.",
                            "json_supported": True,
                        },
                        {
                            "command": "show evpn database mac <mac-address>",
                            "description": "EVPN database entries for one specific MAC address.",
                            "json_supported": True,
                        },
                        {
                            "command": "show evpn database extensive",
                            "description": "Full EVPN database including duplicate MAC detection state and move history.",
                            "json_supported": True,
                            "notes": "Large output on busy fabrics.",
                        },
                        {
                            "command": "show evpn l2-domain-id",
                            "description": "VLAN-to-VNI mapping for all EVPN instances.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route table bgp.evpn.0",
                            "description": "All EVPN routes in the BGP EVPN RIB (type-2 MAC/IP, type-3 IMET, type-5 IP prefix).",
                            "json_supported": True,
                        },
                        {
                            "command": "show route table bgp.evpn.0 detail",
                            "description": "Full attributes for EVPN RIB routes including communities, PMSI tunnel, and next-hop.",
                            "json_supported": True,
                        },
                        {
                            "command": "show evpn esi",
                            "description": "ESI (Ethernet Segment Identifier) table — ESI-LAG multihoming state and designated forwarder elections.",
                            "json_supported": True,
                        },
                        {
                            "command": "show evpn neighbor",
                            "description": "EVPN remote VTEP neighbours discovered via BGP EVPN.",
                            "json_supported": True,
                        },
                    ],
                },
                {
                    "name": "vxlan",
                    "description": "VXLAN tunnel, VNI, and VTEP state.",
                    "commands": [
                        {
                            "command": "show interfaces vtep",
                            "description": "VTEP tunnel interfaces — local and remote endpoints.",
                            "json_supported": True,
                        },
                        {
                            "command": "show interfaces vtep detail",
                            "description": "Detailed VTEP state including encap counters.",
                            "json_supported": True,
                        },
                        {
                            "command": "show vxlan tunnel",
                            "description": "Active VXLAN tunnels with remote VTEP IP, VNI, and encapsulation.",
                            "json_supported": True,
                        },
                        {
                            "command": "show vxlan vni-table",
                            "description": "VNI-to-bridge-domain (EVPN instance) mapping table.",
                            "json_supported": True,
                        },
                        {
                            "command": "show vxlan remote-vtep",
                            "description": "Remotely discovered VTEPs via EVPN IMET routes.",
                            "json_supported": True,
                        },
                        {
                            "command": "show vxlan statistics",
                            "description": "VXLAN encap/decap packet and byte counters — useful for detecting tunnelling failures.",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                    ],
                },
                {
                    "name": "mac",
                    "description": "L2 MAC address table, ARP, and IPv6 neighbour discovery.",
                    "commands": [
                        {
                            "command": "show ethernet-switching table",
                            "description": "Local L2 MAC address table — learned MACs, interface, VLAN, and type (local/remote/flood). Use on QFX/EX.",
                            "json_supported": True,
                        },
                        {
                            "command": "show ethernet-switching table mac-address <mac>",
                            "description": "Look up one specific MAC in the L2 table.",
                            "json_supported": True,
                        },
                        {
                            "command": "show ethernet-switching table vlan-id <id>",
                            "description": "All MACs learned in a specific VLAN.",
                            "json_supported": True,
                        },
                        {
                            "command": "show ethernet-switching table instance <instance-name>",
                            "description": "MAC table scoped to one routing instance (VRF) — needed for multi-VRF fabrics.",
                            "json_supported": True,
                        },
                        {
                            "command": "show ethernet-switching statistics",
                            "description": "L2 forwarding statistics including flooded frames, MAC moves, and drops.",
                            "json_supported": True,
                        },
                        {
                            "command": "show arp",
                            "description": "ARP table for all interfaces (IPv4 MAC-to-IP mappings).",
                            "json_supported": True,
                        },
                        {
                            "command": "show arp hostname <ip>",
                            "description": "ARP entry for a specific IP address.",
                            "json_supported": True,
                        },
                        {
                            "command": "show arp interface <name>",
                            "description": "ARP entries for a specific interface or IRB (e.g. 'irb.100').",
                            "json_supported": True,
                        },
                        {
                            "command": "show ipv6 neighbors",
                            "description": "IPv6 Neighbour Discovery table.",
                            "json_supported": True,
                        },
                        {
                            "command": "show ipv6 neighbors <ip>",
                            "description": "ND entry for one specific IPv6 address.",
                            "json_supported": True,
                        },
                    ],
                },
                {
                    "name": "vrf_routing_instances",
                    "description": "VRF and routing-instance state. In Apstra-managed fabrics, each security zone maps to a Junos routing-instance (vrf type). Use these commands to inspect per-VRF routes, ARP, and interface membership.",
                    "commands": [
                        {
                            "command": "show route instance",
                            "description": "All routing instances with type, interface count, and route/next-hop count.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route instance <name>",
                            "description": "Detail for one routing instance — interfaces, RD, RT, and route counts.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route table <instance-name>.inet.0",
                            "description": "IPv4 routes in a specific VRF (replace <instance-name> with the Apstra security zone name).",
                            "json_supported": True,
                        },
                        {
                            "command": "show route table <instance-name>.inet6.0",
                            "description": "IPv6 routes in a specific VRF.",
                            "json_supported": True,
                        },
                        {
                            "command": "show arp routing-instance <name>",
                            "description": "ARP table for a specific VRF.",
                            "json_supported": True,
                        },
                        {
                            "command": "show bgp summary instance <name>",
                            "description": "BGP sessions belonging to a specific routing-instance — useful when external BGP peers are in a VRF.",
                            "json_supported": True,
                        },
                        {
                            "command": "show route forwarding-table table <instance-name>",
                            "description": "Forwarding table (FIB) for a specific VRF.",
                            "json_supported": True,
                        },
                    ],
                },
                {
                    "name": "interfaces",
                    "description": "Physical and logical interface state, counters, errors, and LAG/LLDP.",
                    "commands": [
                        {
                            "command": "show interfaces terse",
                            "description": "All interfaces with up/down state and IP addresses — quick overview.",
                            "json_supported": True,
                        },
                        {
                            "command": "show interfaces <name>",
                            "description": "Full detail for one interface (e.g. 'ge-0/0/1', 'xe-0/0/0', 'et-0/0/0', 'ae0', 'irb.100').",
                            "json_supported": True,
                        },
                        {
                            "command": "show interfaces <name> detail",
                            "description": "Extended counters including input/output errors, discards, CRC errors, flap count, and queue stats.",
                            "json_supported": True,
                        },
                        {
                            "command": "show interfaces statistics",
                            "description": "All interfaces with traffic counters.",
                            "json_supported": True,
                        },
                        {
                            "command": "show interfaces extensive",
                            "description": "Maximum detail for all interfaces including error breakdowns, traffic rates, and CoS queues.",
                            "json_supported": True,
                            "notes": "Very large output; prefer targeting a single interface with 'show interfaces <name> extensive'.",
                        },
                        {
                            "command": "show interfaces irb",
                            "description": "All IRB (Integrated Routing and Bridging) interfaces — the anycast gateway interfaces for VLANs in Apstra.",
                            "json_supported": True,
                        },
                        {
                            "command": "show interfaces irb.<unit>",
                            "description": "Detail for one IRB interface (e.g. 'irb.100') — IP address, MTU, MAC, and traffic counters.",
                            "json_supported": True,
                        },
                        {
                            "command": "show lacp interfaces",
                            "description": "LACP state for all link-aggregation groups (ae interfaces) — actor/partner state, PDU counters.",
                            "json_supported": True,
                        },
                        {
                            "command": "show lacp statistics interfaces <ae>",
                            "description": "LACP PDU send/receive statistics for one ae interface.",
                            "json_supported": True,
                        },
                        {
                            "command": "show lldp neighbors",
                            "description": "LLDP neighbour table — confirms physical cabling matches design intent.",
                            "json_supported": True,
                        },
                        {
                            "command": "show lldp neighbors interface <name>",
                            "description": "LLDP neighbour on one specific interface.",
                            "json_supported": True,
                        },
                        {
                            "command": "show lldp statistics",
                            "description": "LLDP PDU transmit/receive/discarded counters per interface.",
                            "json_supported": True,
                        },
                    ],
                },
                {
                    "name": "optical_diagnostics",
                    "description": "SFP/QSFP optical transceiver signal levels. Use to detect dirty fibres, bad optics, or receiver saturation before pursuing software troubleshooting.",
                    "commands": [
                        {
                            "command": "show interfaces diagnostics optical",
                            "description": "Optical TX and RX power (dBm), laser bias current, and temperature for all transceiver-equipped interfaces. Flags lanes in alarm or warning state.",
                            "json_supported": True,
                            "notes": "Key values: rx-optical-power and tx-optical-power in dBm. Typical acceptable range is -3 to -20 dBm; alarm thresholds are vendor-specific. Values of -40 dBm or below indicate no light / bad fibre.",
                        },
                        {
                            "command": "show interfaces diagnostics optical <interface-name>",
                            "description": "Optical power levels for one specific interface (e.g. 'et-0/0/0' or 'xe-0/0/0').",
                            "json_supported": True,
                        },
                        {
                            "command": "show chassis pic fpc-slot <n> pic-slot <n>",
                            "description": "PIC-level status including transceiver presence and type for all ports on a line card.",
                            "json_supported": True,
                        },
                    ],
                },
                {
                    "name": "spanning_tree",
                    "description": "Spanning tree state (STP/RSTP/MSTP). Relevant for access switch downlinks and edge ports. Apstra fabric uplinks run pure L3 — STP is only relevant at the access/server edge.",
                    "commands": [
                        {
                            "command": "show spanning-tree interface",
                            "description": "STP port state (Forwarding/Blocking/Discarding), role (Root/Designated/Alternate), and cost for all interfaces.",
                            "json_supported": True,
                        },
                        {
                            "command": "show spanning-tree bridge",
                            "description": "Bridge information: root bridge ID, local bridge priority, and topology change count.",
                            "json_supported": True,
                        },
                        {
                            "command": "show spanning-tree mstp",
                            "description": "MSTP instance-to-VLAN mapping and per-instance root bridge state.",
                            "json_supported": True,
                        },
                        {
                            "command": "show spanning-tree statistics interface",
                            "description": "STP BPDU transmit/receive counters per interface — non-zero values on fabric ports indicate unexpected L2 topology.",
                            "json_supported": True,
                        },
                    ],
                },
                {
                    "name": "connectivity_testing",
                    "description": "Active probing commands to verify reachability. These are operational commands, not show commands, but are valid in run_device_commands.",
                    "commands": [
                        {
                            "command": "ping <ip> count 5",
                            "description": "Send 5 ICMP echo requests to a destination from the default routing table.",
                            "json_supported": False,
                            "notes": "Use output_format='text'. Replace <ip> with a destination address.",
                        },
                        {
                            "command": "ping <ip> routing-instance <name> count 5",
                            "description": "Ping from within a specific VRF (Apstra security zone).",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                        {
                            "command": "ping <ip> source <source-ip> count 5",
                            "description": "Ping with a specific source address — useful to test reachability from loopback or IRB.",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                        {
                            "command": "traceroute <ip>",
                            "description": "Trace the forwarding path to a destination.",
                            "json_supported": False,
                            "notes": "Use output_format='text'. Add 'routing-instance <name>' for VRF-aware traceroute.",
                        },
                        {
                            "command": "traceroute mpls ldp <prefix>",
                            "description": "MPLS LSP traceroute for LDP-signalled paths (DCI/OTT scenarios).",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                    ],
                },
                {
                    "name": "ntp_dns_services",
                    "description": "NTP synchronisation, DNS resolution, and management services.",
                    "commands": [
                        {
                            "command": "show ntp associations",
                            "description": "NTP peer and server associations with reach, delay, and jitter. A '*' prefix means the system peer.",
                            "json_supported": True,
                        },
                        {
                            "command": "show ntp status",
                            "description": "NTP synchronisation status, stratum, and reference clock.",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                        {
                            "command": "show system ntp",
                            "description": "Configured NTP servers and current polling state.",
                            "json_supported": True,
                        },
                        {
                            "command": "show host <hostname>",
                            "description": "DNS resolution for a hostname — confirm management plane DNS is working.",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                        {
                            "command": "show snmp statistics",
                            "description": "SNMP request/response counters — confirm Apstra's SNMP polling is reaching the device.",
                            "json_supported": True,
                        },
                    ],
                },
                {
                    "name": "logs_events",
                    "description": "System logs and event history. Pipe filters are supported by run_device_commands and are useful for reducing volume.",
                    "commands": [
                        {
                            "command": "show log messages | last 100",
                            "description": "Last 100 lines of the main system log — first stop for unexplained events.",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                        {
                            "command": "show log messages | match error",
                            "description": "Filter messages log for lines containing 'error'.",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                        {
                            "command": "show log messages | match \"BGP|rpd\"",
                            "description": "Filter for BGP or routing daemon (rpd) log entries.",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                        {
                            "command": "show log messages | match EVPN",
                            "description": "Filter for EVPN-related log events — MAC moves, adjacency changes.",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                        {
                            "command": "show log chassisd | last 50",
                            "description": "Last 50 lines from the chassis daemon log — hardware events, optic alarms, FPC resets.",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                        {
                            "command": "show log dcd | last 50",
                            "description": "Device Control Daemon log — interface flaps, link state changes.",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                        {
                            "command": "show system commit",
                            "description": "Commit history with timestamp, user, and log message — confirms when the last configuration change was made.",
                            "json_supported": True,
                        },
                    ],
                },
                {
                    "name": "security",
                    "description": "Firewall filter hit counters and routing policy statistics (QFX/EX data-plane ACLs — not SRX). These confirm whether Apstra-generated input/output filters are matching traffic as expected.",
                    "commands": [
                        {
                            "command": "show firewall",
                            "description": "All firewall filter counters — packets and bytes matched by each filter term.",
                            "json_supported": True,
                        },
                        {
                            "command": "show firewall filter <name>",
                            "description": "Counters for one specific firewall filter.",
                            "json_supported": True,
                        },
                        {
                            "command": "show firewall filter <name> counter",
                            "description": "Named counters only for a filter — useful when terms use 'count <counter-name>'.",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                        {
                            "command": "show policy",
                            "description": "Routing policy statistics — term names and match counts.",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                        {
                            "command": "show class-of-service interface <name>",
                            "description": "CoS/QoS queue depths and scheduler configuration for an interface.",
                            "json_supported": True,
                        },
                        {
                            "command": "show pfe statistics traffic",
                            "description": "Packet Forwarding Engine traffic statistics — total forwarded, dropped, errored packets at the hardware level.",
                            "json_supported": False,
                            "notes": "Use output_format='text'. Persistent drop counters indicate a hardware-level issue.",
                        },
                        {
                            "command": "show pfe statistics error",
                            "description": "PFE hardware error counters — fabric errors, cell drops, and lookup errors.",
                            "json_supported": False,
                            "notes": "Use output_format='text'. Non-zero values warrant hardware investigation.",
                        },
                    ],
                },
                {
                    "name": "system",
                    "description": "Platform health, resource utilisation, hardware inventory, and general device info.",
                    "commands": [
                        {
                            "command": "show version",
                            "description": "Junos OS version and hardware model — always collect this for bug-report context.",
                            "json_supported": True,
                        },
                        {
                            "command": "show chassis hardware",
                            "description": "Hardware inventory including FPC/PIC, optic modules, and serial numbers.",
                            "json_supported": True,
                        },
                        {
                            "command": "show chassis alarms",
                            "description": "Active hardware alarms — fan, power supply, temperature, FPC.",
                            "json_supported": True,
                        },
                        {
                            "command": "show system alarms",
                            "description": "Software/system-level alarms (license, routing engine failover, boot device).",
                            "json_supported": True,
                        },
                        {
                            "command": "show system uptime",
                            "description": "System and routing engine uptime — correlate resets with anomaly timelines from Apstra.",
                            "json_supported": True,
                        },
                        {
                            "command": "show chassis routing-engine",
                            "description": "Routing engine CPU and memory utilisation — faster than 'show system processes'.",
                            "json_supported": True,
                        },
                        {
                            "command": "show system processes extensive",
                            "description": "All system processes with CPU and memory usage.",
                            "json_supported": False,
                            "notes": "Use output_format='text'. High rpd or chassisd CPU is a common performance degradation signal.",
                        },
                        {
                            "command": "show system storage",
                            "description": "Filesystem disk usage — full /var can prevent commits and log rotation.",
                            "json_supported": True,
                        },
                        {
                            "command": "show system memory",
                            "description": "Kernel memory pool usage — useful when rpd memory is a concern.",
                            "json_supported": False,
                            "notes": "Use output_format='text'.",
                        },
                        {
                            "command": "show chassis environment",
                            "description": "Temperature sensors, fan trays, and power supply status across the chassis.",
                            "json_supported": True,
                        },
                        {
                            "command": "show chassis fpc",
                            "description": "FPC (line card) state — Online/Offline, CPU/memory utilisation per slot.",
                            "json_supported": True,
                        },
                        {
                            "command": "show chassis fpc detail",
                            "description": "Extended FPC statistics including uptime, CPU interrupt, and memory breakdown.",
                            "json_supported": True,
                        },
                    ],
                },
            ],
        }
