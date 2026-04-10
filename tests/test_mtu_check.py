import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from primitives.response_parser import parse_mtu_link_rows, parse_interface_mtus
from handlers.mtu_check import (
    handle_get_fabric_mtu_check,
    _validate_interface_pair,
    _vxlan_headroom,
    _check_fabric_consistency,
    _select_sessions,
    MTU_CONSTANTS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session(name="dc-primary"):
    s = MagicMock()
    s.name = name
    return s


def make_graph(rows):
    g = MagicMock()
    g.query = MagicMock(return_value=rows)
    return g


def make_registry(graph):
    r = MagicMock()
    r.get_or_rebuild = AsyncMock(return_value=graph)
    return r


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

# Minimal Kuzu RETURN * rows for the MTU link query
# Each row has sys_a, intf_a, link, intf_b, sys_b node dicts
_LINK_ROWS_HEALTHY = [
    {
        "sys_a":  {"label": "Spine1",  "hostname": "spine1", "role": "spine", "system_id": "AABBCC001"},
        "intf_a": {"if_name": "ge-0/0/0", "l3_mtu": 9170, "ipv4_addr": "192.168.0.0/31"},
        "link":   {"id": "link1", "role": "spine_leaf", "speed": "1G"},
        "intf_b": {"if_name": "ge-0/0/1", "l3_mtu": 9170, "ipv4_addr": "192.168.0.1/31"},
        "sys_b":  {"label": "Leaf1",   "hostname": "leaf1",  "role": "leaf",  "system_id": "DDEEFF002"},
    },
    {
        "sys_a":  {"label": "Spine1",  "hostname": "spine1", "role": "spine", "system_id": "AABBCC001"},
        "intf_a": {"if_name": "ge-0/0/1", "l3_mtu": 9170, "ipv4_addr": "192.168.0.2/31"},
        "link":   {"id": "link2", "role": "spine_leaf", "speed": "1G"},
        "intf_b": {"if_name": "ge-0/0/1", "l3_mtu": 9170, "ipv4_addr": "192.168.0.3/31"},
        "sys_b":  {"label": "Leaf2",   "hostname": "leaf2",  "role": "leaf",  "system_id": "112233003"},
    },
]

_LINK_ROWS_MISMATCH = [
    {
        "sys_a":  {"label": "Spine1",  "hostname": "spine1", "role": "spine", "system_id": "AABBCC001"},
        "intf_a": {"if_name": "ge-0/0/0", "l3_mtu": 9170, "ipv4_addr": "192.168.0.0/31"},
        "link":   {"id": "link1", "role": "spine_leaf", "speed": "1G"},
        "intf_b": {"if_name": "ge-0/0/1", "l3_mtu": 1500, "ipv4_addr": "192.168.0.1/31"},  # mismatch
        "sys_b":  {"label": "Leaf1",   "hostname": "leaf1",  "role": "leaf",  "system_id": "DDEEFF002"},
    },
]

# Rendered config API response payload (minimal)
_SPINE1_RENDERED = {
    "config": (
        "interfaces {\n"
        "    replace: ge-0/0/0 {\n"
        "        description \"facing_leaf1\";\n"
        "        mtu 9192;\n"
        "        unit 0 {\n"
        "            family inet {\n"
        "                mtu 9170;\n"
        "                address 192.168.0.0/31;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "    replace: ge-0/0/1 {\n"
        "        description \"facing_leaf2\";\n"
        "        mtu 9192;\n"
        "        unit 0 {\n"
        "            family inet {\n"
        "                mtu 9170;\n"
        "                address 192.168.0.2/31;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "    lo0 {\n"
        "        unit 0 {\n"
        "            family inet {\n"
        "                address 172.16.0.10/32;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
        "routing-options {\n"
        "    router-id 172.16.0.10;\n"
        "    autonomous-system 64513;\n"
        "}\n"
    )
}

_LEAF1_RENDERED = {
    "config": (
        "interfaces {\n"
        "    replace: ge-0/0/1 {\n"
        "        description \"facing_spine1\";\n"
        "        mtu 9192;\n"
        "        unit 0 {\n"
        "            family inet {\n"
        "                mtu 9170;\n"
        "                address 192.168.0.1/31;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "    lo0 {\n"
        "        unit 0 {\n"
        "            family inet {\n"
        "                address 172.16.0.0/32;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
    )
}

# Rendered config with a bad inet MTU (gap too large)
_LEAF_BAD_INET_RENDERED = {
    "config": (
        "interfaces {\n"
        "    replace: ge-0/0/1 {\n"
        "        mtu 9192;\n"
        "        unit 0 {\n"
        "            family inet {\n"
        "                mtu 1500;\n"  # <-- inet MTU too low (9192 - 1500 = 7692 overhead!)
        "                address 192.168.0.1/31;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
    )
}


# ---------------------------------------------------------------------------
# TestParseMtuLinkRows
# ---------------------------------------------------------------------------

class TestParseMtuLinkRows:

    def test_extracts_both_sides(self):
        result = parse_mtu_link_rows(_LINK_ROWS_HEALTHY)
        assert len(result) == 2
        assert result[0]["a_side"]["label"] == "Spine1"
        assert result[0]["b_side"]["label"] == "Leaf1"

    def test_extracts_l3_mtu(self):
        result = parse_mtu_link_rows(_LINK_ROWS_HEALTHY)
        assert result[0]["a_side"]["l3_mtu"] == 9170
        assert result[0]["b_side"]["l3_mtu"] == 9170

    def test_extracts_link_fields(self):
        result = parse_mtu_link_rows(_LINK_ROWS_HEALTHY)
        assert result[0]["link_id"] == "link1"
        assert result[0]["link_role"] == "spine_leaf"
        assert result[0]["speed"] == "1G"

    def test_extracts_system_id(self):
        result = parse_mtu_link_rows(_LINK_ROWS_HEALTHY)
        assert result[0]["a_side"]["system_id"] == "AABBCC001"
        assert result[0]["b_side"]["system_id"] == "DDEEFF002"

    def test_none_l3_mtu_when_missing(self):
        rows = [{
            "sys_a":  {"label": "S1", "hostname": "s1", "role": "spine", "system_id": "AAA"},
            "intf_a": {"if_name": "ge-0/0/0"},  # no l3_mtu
            "link":   {"id": "L1", "role": "spine_leaf", "speed": "1G"},
            "intf_b": {"if_name": "ge-0/0/1"},
            "sys_b":  {"label": "L1", "hostname": "l1",  "role": "leaf",  "system_id": "BBB"},
        }]
        result = parse_mtu_link_rows(rows)
        assert result[0]["a_side"]["l3_mtu"] is None

    def test_empty_rows_returns_empty_list(self):
        assert parse_mtu_link_rows([]) == []


# ---------------------------------------------------------------------------
# TestParseInterfaceMtus
# ---------------------------------------------------------------------------

class TestParseInterfaceMtus:

    def test_parses_physical_and_inet_mtu(self):
        result = parse_interface_mtus(_SPINE1_RENDERED["config"].split("routing-options")[0])
        # The above slices off routing-options; we want just the interfaces block
        # Better: use the full rendered and let parse_config_rendering extract it
        from primitives.response_parser import parse_config_rendering
        parsed = parse_config_rendering(_SPINE1_RENDERED, sections=["interfaces"])
        intf_block = parsed["sections"]["interfaces"]
        mtu_data = parse_interface_mtus(intf_block)

        assert mtu_data["ge-0/0/0"]["physical_mtu"] == 9192
        assert mtu_data["ge-0/0/0"]["inet_mtu"] == 9170
        assert mtu_data["ge-0/0/0"]["inet_address"] == "192.168.0.0/31"

    def test_loopback_has_no_physical_or_inet_mtu(self):
        from primitives.response_parser import parse_config_rendering
        parsed = parse_config_rendering(_SPINE1_RENDERED, sections=["interfaces"])
        intf_block = parsed["sections"]["interfaces"]
        mtu_data = parse_interface_mtus(intf_block)

        assert mtu_data["lo0"]["physical_mtu"] is None
        assert mtu_data["lo0"]["inet_mtu"] is None
        assert mtu_data["lo0"]["inet_address"] == "172.16.0.10/32"

    def test_multiple_interfaces_all_parsed(self):
        from primitives.response_parser import parse_config_rendering
        parsed = parse_config_rendering(_SPINE1_RENDERED, sections=["interfaces"])
        intf_block = parsed["sections"]["interfaces"]
        mtu_data = parse_interface_mtus(intf_block)

        assert "ge-0/0/0" in mtu_data
        assert "ge-0/0/1" in mtu_data
        assert "lo0" in mtu_data

    def test_empty_block_returns_empty_dict(self):
        assert parse_interface_mtus("") == {}

    def test_interface_without_inet_mtu(self):
        block = (
            "interfaces {\n"
            "    ge-0/0/5 {\n"
            "        mtu 9192;\n"
            "        unit 0 {\n"
            "            family inet;\n"  # no explicit inet mtu
            "        }\n"
            "    }\n"
            "}\n"
        )
        result = parse_interface_mtus(block)
        assert result["ge-0/0/5"]["physical_mtu"] == 9192
        assert result["ge-0/0/5"]["inet_mtu"] is None

    def test_interface_without_mtu_stanza_at_all(self):
        block = (
            "interfaces {\n"
            "    ge-0/0/3 {\n"
            "        unit 0 {\n"
            "            family inet;\n"
            "        }\n"
            "    }\n"
            "}\n"
        )
        result = parse_interface_mtus(block)
        assert result["ge-0/0/3"]["physical_mtu"] is None
        assert result["ge-0/0/3"]["inet_mtu"] is None


# ---------------------------------------------------------------------------
# TestValidateInterfacePair
# ---------------------------------------------------------------------------

class TestValidateInterfacePair:

    def test_symmetric_healthy_values_produce_no_issues(self):
        issues = _validate_interface_pair(
            "Spine1", "ge-0/0/0", 9192, 9170,
            "Leaf1",  "ge-0/0/1", 9192, 9170,
            "spine_leaf",
        )
        assert issues == []

    def test_physical_mtu_asymmetry_is_critical(self):
        issues = _validate_interface_pair(
            "Spine1", "ge-0/0/0", 9192, 9170,
            "Leaf1",  "ge-0/0/1", 1500, 1500,  # different physical MTU
            "spine_leaf",
        )
        checks = [i["check"] for i in issues]
        assert "physical_mtu_asymmetry" in checks
        critical = [i for i in issues if i["check"] == "physical_mtu_asymmetry"]
        assert critical[0]["severity"] == "critical"

    def test_inet_mtu_asymmetry_is_critical(self):
        issues = _validate_interface_pair(
            "Spine1", "ge-0/0/0", 9192, 9170,
            "Leaf1",  "ge-0/0/1", 9192, 1500,  # inet mtu mismatch
            "spine_leaf",
        )
        checks = [i["check"] for i in issues]
        assert "inet_mtu_asymmetry" in checks

    def test_inet_exceeds_physical_flags_critical(self):
        issues = _validate_interface_pair(
            "Spine1", "ge-0/0/0", 9192, 9180,  # overhead only 12B < 14B min
            "Leaf1",  "ge-0/0/1", 9192, 9180,
            "spine_leaf",
        )
        checks = [i["check"] for i in issues]
        assert "inet_exceeds_physical" in checks

    def test_inet_mtu_too_low_warns_when_gap_large(self):
        # physical=9192, inet=1500 → overhead=7692 >> max 24B
        issues = _validate_interface_pair(
            "Spine1", "ge-0/0/0", 9192, 1500,
            "Leaf1",  "ge-0/0/1", 9192, 1500,
            "spine_leaf",
        )
        checks = [i["check"] for i in issues]
        assert "inet_mtu_too_low" in checks

    def test_low_fabric_inet_mtu_creates_issue(self):
        issues = _validate_interface_pair(
            "Spine1", "ge-0/0/0", None, 8000,  # below 9000
            "Leaf1",  "ge-0/0/1", None, 8000,
            "spine_leaf",
        )
        checks = [i["check"] for i in issues]
        assert "fabric_inet_mtu_too_low" in checks

    def test_non_fabric_link_skips_fabric_minimum_checks(self):
        issues = _validate_interface_pair(
            "Leaf1", "ge-0/0/5", 1500, 1486,  # tiny but correct overhead
            "Server", "eth0", 1500, 1486,
            "leaf_l2_server",  # not a fabric link role
        )
        checks = [i["check"] for i in issues]
        assert "fabric_inet_mtu_too_low" not in checks
        assert "fabric_physical_mtu_too_low" not in checks

    def test_none_values_do_not_cause_errors(self):
        issues = _validate_interface_pair(
            "Spine1", "ge-0/0/0", None, None,
            "Leaf1",  "ge-0/0/1", None, None,
            "spine_leaf",
        )
        assert issues == []


# ---------------------------------------------------------------------------
# TestVxlanHeadroom
# ---------------------------------------------------------------------------

class TestVxlanHeadroom:

    def test_healthy_9170_values(self):
        result = _vxlan_headroom(9170)
        assert result["fabric_inet_mtu"] == 9170
        assert result["vxlan_overhead_bytes"] == 50
        assert result["max_inner_ethernet_frame_bytes"] == 9120  # 9170 - 50
        assert result["max_inner_ip_payload_bytes"] == 9106     # 9120 - 14
        assert result["can_carry_standard_1500_inner"] is True
        assert result["can_carry_jumbo_9000_inner"] is True
        assert result["assessment"] == "ok"

    def test_limited_when_cannot_carry_jumbo(self):
        result = _vxlan_headroom(1600)
        assert result["can_carry_standard_1500_inner"] is True
        assert result["can_carry_jumbo_9000_inner"] is False
        assert result["assessment"] == "limited"

    def test_critical_when_cannot_carry_standard(self):
        result = _vxlan_headroom(1500)
        # 1500 - 50 - 14 = 1436 < 1500
        assert result["can_carry_standard_1500_inner"] is False
        assert result["assessment"] == "critical"

    def test_none_returns_none(self):
        assert _vxlan_headroom(None) is None

    def test_9000_inet_mtu_cannot_carry_jumbo(self):
        result = _vxlan_headroom(9000)
        # 9000 - 50 - 14 = 8936 < 9000 inner IP → cannot carry full 9000 jumbo
        assert result["can_carry_jumbo_9000_inner"] is False


# ---------------------------------------------------------------------------
# TestMtuConstants
# ---------------------------------------------------------------------------

class TestMtuConstants:

    def test_vxlan_overhead_is_50(self):
        assert MTU_CONSTANTS["vxlan_overhead_bytes"] == 50

    def test_recommended_fabric_physical_is_9192(self):
        assert MTU_CONSTANTS["recommended_fabric_physical_mtu"] == 9192

    def test_recommended_fabric_inet_is_9170(self):
        assert MTU_CONSTANTS["recommended_fabric_inet_mtu"] == 9170

    def test_l2_overhead_typical_is_22(self):
        assert MTU_CONSTANTS["junos_l2_overhead_typical_bytes"] == 22


# ---------------------------------------------------------------------------
# TestSelectSessions
# ---------------------------------------------------------------------------

class TestSelectSessions:

    def test_returns_all_when_no_name(self):
        sessions = [make_session("a"), make_session("b")]
        assert _select_sessions(sessions, None) == sessions

    def test_filters_by_name(self):
        sessions = [make_session("a"), make_session("b")]
        result = _select_sessions(sessions, "b")
        assert len(result) == 1 and result[0].name == "b"

    def test_raises_for_unknown(self):
        with pytest.raises(ValueError, match="dc-missing"):
            _select_sessions([make_session("dc-primary")], "dc-missing")


# ---------------------------------------------------------------------------
# TestHandleGetFabricMtuCheck
# ---------------------------------------------------------------------------

class TestHandleGetFabricMtuCheck:

    def _make_rendered_map(self):
        """Returns rendered config by system_id for patching."""
        return {
            "AABBCC001": _SPINE1_RENDERED,
            "DDEEFF002": _LEAF1_RENDERED,
            "112233003": _LEAF1_RENDERED,
        }

    async def test_healthy_fabric_assessment_is_ok(self):
        session = make_session()
        rendered = self._make_rendered_map()

        async def mock_render(sess, bp, system_id):
            return rendered.get(system_id, {"config": ""})

        with (
            patch("primitives.live_data_client.get_config_rendering", side_effect=mock_render),
            patch.object(make_registry(make_graph(_LINK_ROWS_HEALTHY)), "get_or_rebuild",
                         new_callable=lambda: lambda *a, **kw: None),
        ):
            registry = make_registry(make_graph(_LINK_ROWS_HEALTHY))
            result = await handle_get_fabric_mtu_check(
                [session], registry, "bp-001"
            )

        assert result["assessment"] == "ok"
        assert result["issues_count"]["total"] == 0

    async def test_mismatch_detected_as_critical(self):
        session = make_session()

        async def mock_render(sess, bp, system_id):
            # Leaf1 has bad inet mtu in its rendered config
            if system_id == "DDEEFF002":
                return _LEAF_BAD_INET_RENDERED
            return _SPINE1_RENDERED

        registry = make_registry(make_graph(_LINK_ROWS_MISMATCH))
        with patch("primitives.live_data_client.get_config_rendering", side_effect=mock_render):
            result = await handle_get_fabric_mtu_check(
                [session], registry, "bp-001"
            )

        assert result["assessment"] == "critical"
        assert result["issues_count"]["critical"] > 0

    async def test_result_structure_complete(self):
        session = make_session()

        async def mock_render(sess, bp, system_id):
            return _SPINE1_RENDERED

        registry = make_registry(make_graph(_LINK_ROWS_HEALTHY))
        with patch("primitives.live_data_client.get_config_rendering", side_effect=mock_render):
            result = await handle_get_fabric_mtu_check(
                [session], registry, "bp-001"
            )

        for key in ("assessment", "issues_count", "vxlan_headroom",
                    "mtu_reference", "link_mtu_checks", "per_system_interface_mtu",
                    "issues_summary"):
            assert key in result, f"Missing key: {key}"

    async def test_vxlan_headroom_computed(self):
        session = make_session()

        async def mock_render(sess, bp, system_id):
            return _SPINE1_RENDERED

        registry = make_registry(make_graph(_LINK_ROWS_HEALTHY))
        with patch("primitives.live_data_client.get_config_rendering", side_effect=mock_render):
            result = await handle_get_fabric_mtu_check(
                [session], registry, "bp-001"
            )

        vxlan = result["vxlan_headroom"]
        assert vxlan is not None
        assert vxlan["fabric_inet_mtu"] == 9170
        assert vxlan["can_carry_jumbo_9000_inner"] is True

    async def test_focus_systems_filters_links(self):
        session = make_session()

        async def mock_render(sess, bp, system_id):
            return _SPINE1_RENDERED

        registry = make_registry(make_graph(_LINK_ROWS_HEALTHY))
        with patch("primitives.live_data_client.get_config_rendering", side_effect=mock_render):
            result = await handle_get_fabric_mtu_check(
                [session], registry, "bp-001",
                focus_systems=["Leaf1"],
            )

        # Only link1 (Spine1↔Leaf1) should be returned
        assert len(result["link_mtu_checks"]) == 1
        link = result["link_mtu_checks"][0]
        assert "Leaf1" in (link["a_side"]["system"], link["b_side"]["system"])

    async def test_focus_systems_produces_path_analysis(self):
        session = make_session()

        async def mock_render(sess, bp, system_id):
            return _SPINE1_RENDERED

        registry = make_registry(make_graph(_LINK_ROWS_HEALTHY))
        with patch("primitives.live_data_client.get_config_rendering", side_effect=mock_render):
            result = await handle_get_fabric_mtu_check(
                [session], registry, "bp-001",
                focus_systems=["Leaf1", "Leaf2"],
            )

        assert result["path_analysis"] is not None
        pa = result["path_analysis"]
        assert "effective_path_inet_mtu" in pa
        assert "bottleneck_interface" in pa
        assert "max_inner_ip_payload" in pa

    async def test_issue_description_passed_through(self):
        session = make_session()

        async def mock_render(sess, bp, system_id):
            return _SPINE1_RENDERED

        registry = make_registry(make_graph(_LINK_ROWS_HEALTHY))
        with patch("primitives.live_data_client.get_config_rendering", side_effect=mock_render):
            result = await handle_get_fabric_mtu_check(
                [session], registry, "bp-001",
                issue_description="Jumbo frames dropped between Leaf1 and Leaf2",
            )

        assert "Jumbo frames" in result["issue_description"]

    async def test_rendered_config_error_captured(self):
        session = make_session()

        async def mock_render(sess, bp, system_id):
            raise RuntimeError("connection refused")

        registry = make_registry(make_graph(_LINK_ROWS_HEALTHY))
        with patch("primitives.live_data_client.get_config_rendering", side_effect=mock_render):
            result = await handle_get_fabric_mtu_check(
                [session], registry, "bp-001"
            )

        assert result["rendered_config_errors"]
        assert len(result["rendered_config_errors"]) > 0

    async def test_multi_session_wraps_in_results_list(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]

        async def mock_render(sess, bp, system_id):
            return _SPINE1_RENDERED

        registry = make_registry(make_graph(_LINK_ROWS_HEALTHY))
        with patch("primitives.live_data_client.get_config_rendering", side_effect=mock_render):
            result = await handle_get_fabric_mtu_check(
                sessions, registry, "bp-001"
            )

        assert result["instance"] == "all"
        assert len(result["results"]) == 2

    async def test_link_checks_contain_physical_mtu_from_rendered_config(self):
        session = make_session()

        async def mock_render(sess, bp, system_id):
            return _SPINE1_RENDERED

        registry = make_registry(make_graph(_LINK_ROWS_HEALTHY))
        with patch("primitives.live_data_client.get_config_rendering", side_effect=mock_render):
            result = await handle_get_fabric_mtu_check(
                [session], registry, "bp-001"
            )

        # spine1 ge-0/0/0 has physical_mtu 9192 in _SPINE1_RENDERED
        spine_links = [
            lc for lc in result["link_mtu_checks"]
            if lc["a_side"]["system"] == "Spine1" and lc["a_side"]["interface"] == "ge-0/0/0"
        ]
        assert len(spine_links) == 1
        assert spine_links[0]["a_side"]["physical_mtu"] == 9192


# ---------------------------------------------------------------------------
# TestCheckFabricConsistency
# ---------------------------------------------------------------------------

def _make_link_check(role, a_sys, a_if, a_inet, b_sys, b_if, b_inet,
                     a_phys=None, b_phys=None):
    """Helper to build a minimal link_checks entry for consistency tests."""
    return {
        "link_id":   f"{a_sys}-{b_sys}",
        "link_role": role,
        "speed":     "1G",
        "a_side":    {"system": a_sys, "role": "spine", "interface": a_if,
                      "inet_mtu": a_inet, "physical_mtu": a_phys},
        "b_side":    {"system": b_sys, "role": "leaf",  "interface": b_if,
                      "inet_mtu": b_inet, "physical_mtu": b_phys},
        "mtu_ok":    True,
        "issues":    [],
    }


class TestCheckFabricConsistency:

    def test_uniform_fabric_is_consistent(self):
        links = [
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/0", 9170, "Leaf1", "ge-0/0/0", 9170, 9192, 9192),
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/1", 9170, "Leaf2", "ge-0/0/0", 9170, 9192, 9192),
            _make_link_check("spine_leaf", "Spine2", "ge-0/0/0", 9170, "Leaf1", "ge-0/0/1", 9170, 9192, 9192),
        ]
        summary, issues = _check_fabric_consistency(links)
        assert summary["fabric_wide_inet_mtu_consistent"] is True
        assert summary["fabric_wide_physical_mtu_consistent"] is True
        assert summary["ecmp_risk"] == "none"
        assert summary["effective_bottleneck_inet_mtu"] == 9170
        assert issues == []

    def test_inconsistent_inet_mtu_is_critical_issue(self):
        links = [
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/0", 9170, "Leaf1", "ge-0/0/0", 9170),
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/1", 9000, "Leaf2", "ge-0/0/0", 9000),  # different
        ]
        summary, issues = _check_fabric_consistency(links)
        assert summary["fabric_wide_inet_mtu_consistent"] is False
        assert summary["ecmp_risk"] == "critical"
        assert len(issues) >= 1
        checks = [i["check"] for i in issues]
        assert "fabric_inet_mtu_inconsistency" in checks
        critical = [i for i in issues if i["check"] == "fabric_inet_mtu_inconsistency"]
        assert critical[0]["severity"] == "critical"

    def test_inconsistent_physical_mtu_is_critical_issue(self):
        links = [
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/0", 9170, "Leaf1", "ge-0/0/0", 9170, 9192, 9192),
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/1", 9170, "Leaf2", "ge-0/0/0", 9170, 9000, 9000),
        ]
        summary, issues = _check_fabric_consistency(links)
        assert summary["fabric_wide_physical_mtu_consistent"] is False
        checks = [i["check"] for i in issues]
        assert "fabric_physical_mtu_inconsistency" in checks

    def test_bottleneck_is_minimum_inet_mtu(self):
        links = [
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/0", 9170, "Leaf1", "ge-0/0/0", 9170),
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/1", 8000, "Leaf2", "ge-0/0/0", 8000),
        ]
        summary, _ = _check_fabric_consistency(links)
        assert summary["effective_bottleneck_inet_mtu"] == 8000

    def test_non_fabric_links_excluded_from_consistency(self):
        # leaf_l2_server is not a fabric role — should not affect results
        links = [
            _make_link_check("spine_leaf",    "Spine1", "ge-0/0/0", 9170, "Leaf1", "ge-0/0/0", 9170),
            _make_link_check("leaf_l2_server","Leaf1",  "ge-0/0/5", 1500, "Server1", "eth0", 1500),
        ]
        summary, issues = _check_fabric_consistency(links)
        assert summary["fabric_wide_inet_mtu_consistent"] is True
        assert issues == []

    def test_inconsistency_message_includes_min_max(self):
        links = [
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/0", 9170, "Leaf1", "ge-0/0/0", 9170),
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/1", 9000, "Leaf2", "ge-0/0/0", 9000),
        ]
        _, issues = _check_fabric_consistency(links)
        msg = next(i["message"] for i in issues if i["check"] == "fabric_inet_mtu_inconsistency")
        assert "9000" in msg
        assert "9170" in msg

    def test_ecmp_risk_explanation_describes_affected_packet_range(self):
        links = [
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/0", 9170, "Leaf1", "ge-0/0/0", 9170),
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/1", 9000, "Leaf2", "ge-0/0/0", 9000),
        ]
        summary, _ = _check_fabric_consistency(links)
        explanation = summary["ecmp_risk_explanation"]
        assert "9000" in explanation
        assert "9170" in explanation

    def test_by_role_breakdown_populated(self):
        links = [
            _make_link_check("spine_leaf",  "Spine1", "ge-0/0/0", 9170, "Leaf1", "ge-0/0/0", 9170),
            _make_link_check("leaf_peer_link", "Leaf1", "ae0",     9170, "Leaf2", "ae0",      9170),
        ]
        summary, _ = _check_fabric_consistency(links)
        assert "spine_leaf"    in summary["by_role"]
        assert "leaf_peer_link" in summary["by_role"]
        assert summary["by_role"]["spine_leaf"]["link_count"] == 1
        assert summary["by_role"]["spine_leaf"]["inet_mtu_values"] == [9170]

    def test_empty_link_list_returns_consistent_summary(self):
        summary, issues = _check_fabric_consistency([])
        assert summary["fabric_wide_inet_mtu_consistent"] is True
        assert summary["ecmp_risk"] == "none"
        assert summary["effective_bottleneck_inet_mtu"] is None
        assert issues == []

    def test_none_mtu_values_ignored_in_consistency(self):
        links = [
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/0", None, "Leaf1", "ge-0/0/0", None),
            _make_link_check("spine_leaf", "Spine1", "ge-0/0/1", 9170, "Leaf2", "ge-0/0/0", 9170),
        ]
        summary, issues = _check_fabric_consistency(links)
        # None values not in the set — only 9170 observed
        assert summary["fabric_wide_inet_mtu_consistent"] is True
        assert issues == []


class TestHandleFabricConsistencyIntegration:
    """Verify fabric_consistency key is present in handler output."""

    async def test_fabric_consistency_in_result(self):
        session = make_session()

        async def mock_render(sess, bp, system_id):
            return _SPINE1_RENDERED

        registry = make_registry(make_graph(_LINK_ROWS_HEALTHY))
        with patch("primitives.live_data_client.get_config_rendering", side_effect=mock_render):
            result = await handle_get_fabric_mtu_check(
                [session], registry, "bp-001"
            )

        assert "fabric_consistency" in result
        fc = result["fabric_consistency"]
        assert "by_role" in fc
        assert "fabric_wide_inet_mtu_consistent" in fc
        assert "effective_bottleneck_inet_mtu" in fc
        assert "ecmp_risk" in fc
        assert "ecmp_risk_explanation" in fc

    async def test_healthy_fabric_ecmp_risk_is_none(self):
        session = make_session()

        async def mock_render(sess, bp, system_id):
            return _SPINE1_RENDERED

        registry = make_registry(make_graph(_LINK_ROWS_HEALTHY))
        with patch("primitives.live_data_client.get_config_rendering", side_effect=mock_render):
            result = await handle_get_fabric_mtu_check(
                [session], registry, "bp-001"
            )

        assert result["fabric_consistency"]["ecmp_risk"] == "none"
        assert result["fabric_consistency"]["fabric_wide_inet_mtu_consistent"] is True
