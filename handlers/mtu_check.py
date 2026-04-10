from primitives import live_data_client
from primitives.response_parser import (
    parse_mtu_link_rows,
    parse_interface_mtus,
    parse_config_rendering,
)

# ---------------------------------------------------------------------------
# MTU reference constants
# ---------------------------------------------------------------------------
# VXLAN encapsulation adds a fixed overhead on top of the inner Ethernet frame:
#   outer Ethernet header :  14 bytes  (6 dst MAC + 6 src MAC + 2 EtherType)
#   outer IPv4 header     :  20 bytes
#   UDP header            :   8 bytes
#   VXLAN header          :   8 bytes
#   Total                 :  50 bytes
#
# JunOS physical (frame) MTU vs L3 (inet) MTU relationship on Apstra fabrics:
#   Frame MTU = inet MTU + L2 overhead
#   Apstra standard: 9192 = 9170 + 22  (22 = ETH 14 + 802.1Q VLAN 4 + FCS 4)
#
# Minimum viable MTU for a VXLAN fabric carrying jumbo inner traffic (9000):
#   fabric inet MTU  ≥  inner IP (9000) + VXLAN overhead (50) = 9050
#   fabric frame MTU ≥  9050 + L2 overhead (22) = 9072  (9192 provides headroom)
#
# For standard-MTU inner traffic (1500 byte IP):
#   absolute minimum fabric inet MTU = 1500 + 50 = 1550

MTU_CONSTANTS = {
    "vxlan_overhead_bytes": 50,
    "junos_l2_overhead_typical_bytes": 22,
    "junos_l2_overhead_min_bytes": 14,
    "junos_l2_overhead_max_bytes": 24,
    "recommended_fabric_physical_mtu": 9192,
    "recommended_fabric_inet_mtu": 9170,
    "min_fabric_physical_mtu": 9050,
    "min_fabric_inet_mtu": 9000,
    "absolute_min_for_vxlan_with_1500_inner": 1550,
    "explanation": (
        "VXLAN overhead = outer ETH(14) + IP(20) + UDP(8) + VXLAN-header(8) = 50 bytes. "
        "JunOS frame MTU includes L2 overhead (ETH+VLAN+FCS = 22 bytes in Apstra standard), "
        "so physical MTU 9192 = inet MTU 9170 + 22. "
        "With inet MTU 9170: max inner Ethernet = 9170 - 50 = 9120, "
        "max inner IP payload = 9120 - 14 (inner ETH) = 9106 bytes."
    ),
}

_VXLAN_OVERHEAD = 50
_L2_OVERHEAD_TYPICAL = 22
_L2_OVERHEAD_MIN = 14
_L2_OVERHEAD_MAX = 24
_MIN_FABRIC_INET = 9000
_MIN_FABRIC_PHYSICAL = 9050
_INNER_ETH_HEADER = 14

# ---------------------------------------------------------------------------
# Cypher: fabric-wide links with both system nodes (for MTU analysis)
# ---------------------------------------------------------------------------
_MTU_LINK_QUERY = """
MATCH (sys_a:system)-[:hosted_interfaces]->(intf_a:interface)
  -[:link__rel]->(link:link)
  <-[:link__rel]-(intf_b:interface)<-[:hosted_interfaces]-(sys_b:system)
WHERE intf_a.id < intf_b.id
RETURN *
"""


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_interface_pair(
    a_label, a_if, a_phys, a_inet,
    b_label, b_if, b_phys, b_inet,
    link_role,
):
    """
    Runs MTU validation for one link endpoint pair.

    Returns a list of issue dicts, each with:
      check    — machine-readable check name
      severity — "critical" or "warning"
      message  — human-readable description
    """
    issues = []
    is_fabric = link_role in ("spine_leaf", "leaf_peer_link", "spine_superspine")

    # — Physical MTU symmetry across the link
    if a_phys is not None and b_phys is not None and a_phys != b_phys:
        issues.append({
            "check": "physical_mtu_asymmetry",
            "severity": "critical",
            "message": (
                f"Physical MTU mismatch across link: "
                f"{a_label}:{a_if} = {a_phys}B, "
                f"{b_label}:{b_if} = {b_phys}B. "
                "Asymmetric physical MTU will cause unpredictable frame-size drops."
            ),
        })

    # — L3 MTU symmetry across the link
    if a_inet is not None and b_inet is not None and a_inet != b_inet:
        issues.append({
            "check": "inet_mtu_asymmetry",
            "severity": "critical",
            "message": (
                f"L3 (inet) MTU mismatch across link: "
                f"{a_label}:{a_if} = {a_inet}B, "
                f"{b_label}:{b_if} = {b_inet}B. "
                "BGP sessions may stay up but oversized packets will be silently dropped."
            ),
        })

    # — Per-side checks (physical vs inet relationship, fabric minimums)
    for label, if_name, phys, inet in (
        (a_label, a_if, a_phys, a_inet),
        (b_label, b_if, b_phys, b_inet),
    ):
        if phys is not None and inet is not None:
            overhead = phys - inet
            if overhead < _L2_OVERHEAD_MIN:
                issues.append({
                    "check": "inet_exceeds_physical",
                    "severity": "critical",
                    "message": (
                        f"{label}:{if_name}: inet MTU {inet}B is only {overhead}B below "
                        f"physical MTU {phys}B. The Ethernet header alone is 14B — "
                        "this configuration cannot work and packets will be dropped."
                    ),
                })
            elif overhead > _L2_OVERHEAD_MAX:
                issues.append({
                    "check": "inet_mtu_too_low",
                    "severity": "warning",
                    "message": (
                        f"{label}:{if_name}: inet MTU {inet}B is {overhead}B below "
                        f"physical MTU {phys}B. Expected overhead is {_L2_OVERHEAD_MIN}–"
                        f"{_L2_OVERHEAD_MAX}B (JunOS standard 22B = ETH+VLAN+FCS). "
                        "The virtual/inet MTU appears to be set too low."
                    ),
                })

        if is_fabric:
            if inet is not None and inet < _MIN_FABRIC_INET:
                issues.append({
                    "check": "fabric_inet_mtu_too_low",
                    "severity": "warning" if inet >= 1550 else "critical",
                    "message": (
                        f"{label}:{if_name}: inet MTU {inet}B is below the "
                        f"recommended minimum {_MIN_FABRIC_INET}B for a VXLAN overlay "
                        "fabric. VXLAN overhead is 50B; insufficient headroom will "
                        "cause fragmentation or drops for jumbo inner frames."
                    ),
                })
            if phys is not None and phys < _MIN_FABRIC_PHYSICAL:
                issues.append({
                    "check": "fabric_physical_mtu_too_low",
                    "severity": "warning" if phys >= 1564 else "critical",
                    "message": (
                        f"{label}:{if_name}: physical MTU {phys}B is below the "
                        f"recommended minimum {_MIN_FABRIC_PHYSICAL}B for a VXLAN "
                        "underlay fabric."
                    ),
                })

    return issues


_FABRIC_ROLES = frozenset(("spine_leaf", "leaf_peer_link", "spine_superspine"))


def _check_fabric_consistency(link_checks: list) -> tuple[dict, list]:
    """
    Checks that every fabric link of the same role uses the same MTU values.

    In an ECMP fabric, traffic from a single host is load-balanced across
    multiple equal-cost paths. If those paths have different inet MTU values,
    large packets sent on a high-MTU path succeed while identical packets
    that happen to be hashed to a lower-MTU path are silently dropped. This
    produces sporadic, hard-to-diagnose partial connectivity failures.

    Operates on the already-built link_checks list (after per-link validation).

    Returns:
        consistency_summary  — dict with structure:
            {
              "by_role": {
                "<role>": {
                  "link_count": N,
                  "inet_mtu_values": [sorted distinct values],
                  "physical_mtu_values": [sorted distinct values],
                  "inet_mtu_consistent": bool,
                  "physical_mtu_consistent": bool,
                }
              },
              "fabric_wide_inet_mtu_consistent": bool,
              "fabric_wide_physical_mtu_consistent": bool,
              "effective_bottleneck_inet_mtu": int | None,
            }
        issues  — list of issue dicts (same shape as _validate_interface_pair
                  output) for any inconsistencies found
    """
    from collections import defaultdict

    # Collect MTU values per role from both sides of every fabric link.
    role_inet: dict[str, set] = defaultdict(set)
    role_phys: dict[str, set] = defaultdict(set)

    for lc in link_checks:
        role = lc.get("link_role")
        if role not in _FABRIC_ROLES:
            continue
        for side in (lc["a_side"], lc["b_side"]):
            if side.get("inet_mtu") is not None:
                role_inet[role].add(side["inet_mtu"])
            if side.get("physical_mtu") is not None:
                role_phys[role].add(side["physical_mtu"])

    issues = []
    by_role = {}

    all_fabric_inet: set = set()
    all_fabric_phys: set = set()

    for role in sorted(role_inet.keys() | role_phys.keys()):
        inet_vals = sorted(role_inet.get(role, set()))
        phys_vals = sorted(role_phys.get(role, set()))
        link_count = sum(
            1 for lc in link_checks if lc.get("link_role") == role
        )
        inet_ok = len(inet_vals) <= 1
        phys_ok = len(phys_vals) <= 1

        by_role[role] = {
            "link_count":              link_count,
            "inet_mtu_values":         inet_vals,
            "physical_mtu_values":     phys_vals,
            "inet_mtu_consistent":     inet_ok,
            "physical_mtu_consistent": phys_ok,
        }

        if not inet_ok:
            affected = ", ".join(
                f"{lc['a_side']['system']}:{lc['a_side']['interface']} "
                f"({lc['a_side']['inet_mtu']}B) ↔ "
                f"{lc['b_side']['system']}:{lc['b_side']['interface']} "
                f"({lc['b_side']['inet_mtu']}B)"
                for lc in link_checks
                if lc.get("link_role") == role
                and lc["a_side"].get("inet_mtu") != lc["b_side"].get("inet_mtu") or (
                    lc.get("link_role") == role
                    and (lc["a_side"].get("inet_mtu") not in [inet_vals[0]] if inet_vals else True)
                )
            )
            issues.append({
                "check":    "fabric_inet_mtu_inconsistency",
                "severity": "critical",
                "message": (
                    f"Fabric-wide inet MTU inconsistency on {role} links: "
                    f"found {len(inet_vals)} distinct values {inet_vals}. "
                    "In an ECMP fabric, flows hashed to a lower-MTU path will be "
                    "silently dropped while flows on higher-MTU paths succeed — "
                    "causing intermittent partial connectivity failures that are "
                    "very difficult to diagnose. All fabric links of the same role "
                    f"must use the same inet MTU. Min observed: {min(inet_vals)}B, "
                    f"Max observed: {max(inet_vals)}B, "
                    f"Difference: {max(inet_vals) - min(inet_vals)}B."
                ),
            })

        if not phys_ok:
            issues.append({
                "check":    "fabric_physical_mtu_inconsistency",
                "severity": "critical",
                "message": (
                    f"Fabric-wide physical MTU inconsistency on {role} links: "
                    f"found {len(phys_vals)} distinct values {phys_vals}. "
                    "Inconsistent physical MTU values across ECMP paths cause "
                    "unpredictable frame-size drops depending on which path a "
                    f"flow takes. All {role} links must use the same physical MTU. "
                    f"Min observed: {min(phys_vals)}B, Max observed: {max(phys_vals)}B."
                ),
            })

        all_fabric_inet.update(inet_vals)
        all_fabric_phys.update(phys_vals)

    # Compute the bottleneck — the lowest inet MTU anywhere in the fabric.
    # Any packet larger than this value cannot traverse every possible path.
    all_inet_flat = sorted(all_fabric_inet)
    bottleneck = min(all_inet_flat) if all_inet_flat else None

    consistency_summary = {
        "by_role":                           by_role,
        "fabric_wide_inet_mtu_consistent":   len(all_fabric_inet) <= 1,
        "fabric_wide_physical_mtu_consistent": len(all_fabric_phys) <= 1,
        "effective_bottleneck_inet_mtu":     bottleneck,
        "ecmp_risk": (
            "none"     if len(all_fabric_inet) <= 1 else
            "critical"
        ),
        "ecmp_risk_explanation": (
            "All fabric links have the same inet MTU — ECMP path selection "
            "does not affect effective MTU."
        ) if len(all_fabric_inet) <= 1 else (
            f"Fabric contains {len(all_fabric_inet)} distinct inet MTU values "
            f"({sorted(all_fabric_inet)}). ECMP path selection can send a packet "
            f"via a {min(all_fabric_inet)}B-inet-MTU link or a "
            f"{max(all_fabric_inet)}B-inet-MTU link. Any packet between "
            f"{min(all_fabric_inet) + 1}B and {max(all_fabric_inet)}B will "
            "succeed on some paths and be silently dropped on others."
        ),
    }

    return consistency_summary, issues


def _vxlan_headroom(inet_mtu):
    """Computes VXLAN headroom for a given fabric inet MTU."""
    if inet_mtu is None:
        return None
    max_inner_eth = inet_mtu - _VXLAN_OVERHEAD
    max_inner_ip = max_inner_eth - _INNER_ETH_HEADER
    return {
        "fabric_inet_mtu": inet_mtu,
        "vxlan_overhead_bytes": _VXLAN_OVERHEAD,
        "max_inner_ethernet_frame_bytes": max_inner_eth,
        "max_inner_ip_payload_bytes": max_inner_ip,
        "can_carry_standard_1500_inner": max_inner_ip >= 1500,
        "can_carry_jumbo_9000_inner": max_inner_ip >= 9000,
        "assessment": (
            "ok"       if max_inner_ip >= 9000   else
            "limited"  if max_inner_ip >= 1500   else
            "critical"
        ),
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def handle_get_fabric_mtu_check(
    sessions,
    registry,
    blueprint_id: str,
    focus_systems: list[str] | None = None,
    issue_description: str | None = None,
    instance_name: str | None = None,
) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            result = await _check_instance(
                session, registry, blueprint_id, focus_systems, issue_description
            )
            all_results.append(result)
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "results": all_results,
    }


async def _check_instance(session, registry, blueprint_id, focus_systems, issue_description):
    # 1. Fetch topology + graph l3_mtu
    graph = await registry.get_or_rebuild(session, blueprint_id)
    rows = graph.query(_MTU_LINK_QUERY)
    link_rows = parse_mtu_link_rows(rows)

    # 2. Apply focus_systems filter (keep links where at least one endpoint matches)
    focus_set = set(focus_systems) if focus_systems else None
    if focus_set:
        link_rows = [
            r for r in link_rows
            if r["a_side"]["label"] in focus_set or r["b_side"]["label"] in focus_set
        ]

    # 3. Collect unique systems that need rendered config
    systems_to_fetch: dict[str, str] = {}  # system_id → label
    for link in link_rows:
        for side in (link["a_side"], link["b_side"]):
            sid = side.get("system_id")
            lbl = side.get("label")
            if sid and lbl:
                systems_to_fetch[sid] = lbl

    # 4. Fetch rendered config and extract physical + inet MTU per interface
    #    per_system_mtu: system_id → {if_name → {physical_mtu, inet_mtu, inet_address}}
    per_system_mtu: dict[str, dict] = {}
    rendered_config_errors: list[str] = []

    for system_id, system_label in systems_to_fetch.items():
        try:
            raw = await live_data_client.get_config_rendering(
                session, blueprint_id, system_id
            )
            parsed = parse_config_rendering(raw, sections=["interfaces"])
            intf_block = parsed["sections"].get("interfaces", "")
            per_system_mtu[system_id] = parse_interface_mtus(intf_block) if intf_block else {}
        except Exception as e:
            per_system_mtu[system_id] = {}
            rendered_config_errors.append(f"{system_label} ({system_id}): {e}")

    # 5. Build per-link MTU table with validation
    link_checks = []
    all_issues = []

    for link in link_rows:
        a = link["a_side"]
        b = link["b_side"]

        # Physical MTU from rendered config; inet MTU: prefer rendered, fall back to graph
        a_intf_data = per_system_mtu.get(a["system_id"], {}).get(a["if_name"], {})
        b_intf_data = per_system_mtu.get(b["system_id"], {}).get(b["if_name"], {})

        a_phys = a_intf_data.get("physical_mtu")
        a_inet = a_intf_data.get("inet_mtu") or a["l3_mtu"]
        a_addr = a_intf_data.get("inet_address") or a.get("ipv4_addr")

        b_phys = b_intf_data.get("physical_mtu")
        b_inet = b_intf_data.get("inet_mtu") or b["l3_mtu"]
        b_addr = b_intf_data.get("inet_address") or b.get("ipv4_addr")

        link_issues = _validate_interface_pair(
            a["label"], a["if_name"], a_phys, a_inet,
            b["label"], b["if_name"], b_phys, b_inet,
            link["link_role"],
        )
        all_issues.extend(link_issues)

        link_checks.append({
            "link_id":   link["link_id"],
            "link_role": link["link_role"],
            "speed":     link["speed"],
            "a_side": {
                "system":       a["label"],
                "role":         a["role"],
                "interface":    a["if_name"],
                "ip_address":   a_addr,
                "physical_mtu": a_phys,
                "inet_mtu":     a_inet,
            },
            "b_side": {
                "system":       b["label"],
                "role":         b["role"],
                "interface":    b["if_name"],
                "ip_address":   b_addr,
                "physical_mtu": b_phys,
                "inet_mtu":     b_inet,
            },
            "mtu_ok": len(link_issues) == 0,
            "issues": link_issues,
        })

    # 6. Fabric-wide MTU consistency (ECMP path risk)
    fabric_consistency, consistency_issues = _check_fabric_consistency(link_checks)
    all_issues.extend(consistency_issues)

    # 7. VXLAN headroom — use the bottleneck (lowest) fabric inet MTU so
    #    the headroom figure reflects the worst-case path in the fabric.
    bottleneck_inet = fabric_consistency.get("effective_bottleneck_inet_mtu")
    vxlan = _vxlan_headroom(bottleneck_inet)

    # 8. Per-system physical MTU summary (for quick scan)
    per_system_summary = {}
    for system_id, label in systems_to_fetch.items():
        intf_map = per_system_mtu.get(system_id, {})
        per_system_summary[label] = {
            intf: {
                "physical_mtu": data["physical_mtu"],
                "inet_mtu":     data["inet_mtu"],
                "inet_address": data["inet_address"],
            }
            for intf, data in intf_map.items()
            if data.get("physical_mtu") is not None or data.get("inet_mtu") is not None
        }

    # 9. Path analysis (if focus_systems given)
    path_analysis = None
    if focus_set and len(focus_set) >= 2:
        all_inet = [
            (f"{lc['a_side']['system']}:{lc['a_side']['interface']}", lc["a_side"]["inet_mtu"])
            for lc in link_checks if lc["a_side"]["inet_mtu"] is not None
        ] + [
            (f"{lc['b_side']['system']}:{lc['b_side']['interface']}", lc["b_side"]["inet_mtu"])
            for lc in link_checks if lc["b_side"]["inet_mtu"] is not None
        ]
        if all_inet:
            bottleneck_intf, min_inet = min(all_inet, key=lambda x: x[1])
            path_analysis = {
                "focus_systems":      sorted(focus_set),
                "links_analysed":     len(link_checks),
                "effective_path_inet_mtu": min_inet,
                "bottleneck_interface": bottleneck_intf,
                "max_inner_ip_payload": min_inet - _VXLAN_OVERHEAD - _INNER_ETH_HEADER,
                "note": (
                    "Effective path MTU = minimum L3 (inet) MTU across all links "
                    "in the focused subset. In an ECMP fabric, the best available "
                    "path may be higher if the bottleneck link is not always used."
                ),
            }

    # 10. Overall assessment
    critical = sum(1 for i in all_issues if i["severity"] == "critical")
    warning  = sum(1 for i in all_issues if i["severity"] == "warning")
    assessment = "ok" if not all_issues else ("critical" if critical else "warning")

    return {
        "instance":          session.name,
        "blueprint_id":      blueprint_id,
        "focus_systems":     sorted(focus_set) if focus_set else None,
        "issue_description": issue_description,
        "assessment":        assessment,
        "issues_count": {
            "critical": critical,
            "warning":  warning,
            "total":    len(all_issues),
        },
        "vxlan_headroom":       vxlan,
        "mtu_reference":        MTU_CONSTANTS,
        "fabric_consistency":   fabric_consistency,
        "link_mtu_checks":      link_checks,
        "per_system_interface_mtu": per_system_summary,
        "path_analysis":        path_analysis,
        "rendered_config_errors": rendered_config_errors if rendered_config_errors else None,
        "issues_summary":       all_issues,
    }


def _select_sessions(sessions, instance_name):
    if instance_name is None or instance_name == "all":
        return sessions
    matched = [s for s in sessions if s.name == instance_name]
    if not matched:
        raise ValueError(f"No instance named '{instance_name}' in session pool")
    return matched
