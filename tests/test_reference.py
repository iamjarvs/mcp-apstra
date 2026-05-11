import asyncio
import re

import pytest

from tools.reference import _GUIDE_PATH, register


SECTION_NAME_TO_NUMBER = {
    "building_blocks": 2,
    "three_stage_clos": 3,
    "five_stage_clos": 4,
    "collapsed_fabric": 5,
    "access_switches": 6,
    "dci_ott": 7,
    "dci_stitching": 8,
    "routing_policy": 9,
    "config_reading": 1,
    "quick_reference": 10,
}

EXPECTED_JUNOS_CATEGORIES = {
    "routing",
    "bgp",
    "bfd",
    "evpn",
    "vxlan",
    "mac",
    "vrf_routing_instances",
    "interfaces",
    "optical_diagnostics",
    "spanning_tree",
    "connectivity_testing",
    "ntp_dns_services",
    "logs_events",
    "security",
    "system",
}


class StubMCP:
    def __init__(self):
        self.tools = {}
        self.resources = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator

    def resource(self, uri):
        def decorator(fn):
            self.resources[uri] = fn
            return fn

        return decorator


@pytest.fixture
def tools():
    mcp = StubMCP()
    register(mcp)
    return mcp.tools


def _run(coro):
    return asyncio.run(coro)


def _extract_numbered_sections(content: str) -> dict[int, str]:
    matches = list(re.finditer(r"^##\s+(\d+)\.\s+.+$", content, flags=re.MULTILINE))
    appendix = re.search(r"^##\s+Appendix:", content, flags=re.MULTILINE)
    fallback_end = appendix.start() if appendix else len(content)

    out = {}
    for idx, match in enumerate(matches):
        number = int(match.group(1))
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else fallback_end
        out[number] = content[start:end]
    return out


class TestGuideFile:
    def test_guide_file_exists(self):
        assert _GUIDE_PATH.exists(), f"Reference guide not found at {_GUIDE_PATH}"

    def test_guide_is_non_empty(self):
        content = _GUIDE_PATH.read_text(encoding="utf-8")
        assert len(content) > 5000

    def test_guide_is_valid_utf8(self):
        _GUIDE_PATH.read_text(encoding="utf-8")


class TestReferenceTools:
    def test_overview_returns_all_10_sections(self, tools):
        result = _run(tools["get_reference_design_overview"]())
        assert "sections" in result
        assert len(result["sections"]) == 10
        returned = [s["name"] for s in result["sections"]]
        assert set(returned) == set(SECTION_NAME_TO_NUMBER.keys())
        assert all(s.get("description") for s in result["sections"])

    def test_section_routing_policy_matches_guide_section_9(self, tools):
        guide = _GUIDE_PATH.read_text(encoding="utf-8")
        expected = _extract_numbered_sections(guide)[9]

        result = _run(tools["get_reference_design_section"](section="routing_policy"))
        assert result["section"] == "routing_policy"
        assert result["title"] == "9. Cross-Cutting Patterns and Policies"
        assert result["content"] == expected

    def test_section_unknown_returns_structured_error(self, tools):
        result = _run(tools["get_reference_design_section"](section="unknown"))
        assert result["error"] == "unknown_section"
        assert "valid_sections" in result
        assert set(result["valid_sections"]) == set(SECTION_NAME_TO_NUMBER.keys())

    def test_full_context_still_returns_complete_guide_unchanged(self, tools):
        guide = _GUIDE_PATH.read_text(encoding="utf-8")
        result = _run(tools["get_reference_design_context"]())
        assert result["title"] == "Apstra Reference Design Guide"
        assert result["format"] == "markdown"
        assert result["content"] == guide

    def test_all_section_payloads_are_byte_identical_to_source(self, tools):
        guide = _GUIDE_PATH.read_text(encoding="utf-8")
        numbered = _extract_numbered_sections(guide)

        for section_name, section_number in SECTION_NAME_TO_NUMBER.items():
            result = _run(tools["get_reference_design_section"](section=section_name))
            assert result["content"] == numbered[section_number]


class TestJunosCommandTools:
    def test_get_junos_command_categories_returns_all_15(self, tools):
        result = _run(tools["get_junos_command_categories"]())
        assert "categories" in result
        assert len(result["categories"]) == 15
        names = {entry["name"] for entry in result["categories"]}
        assert names == EXPECTED_JUNOS_CATEGORIES
        assert all(entry.get("description") for entry in result["categories"])

    def test_show_commands_filter_returns_only_requested_categories(self, tools):
        result = _run(tools["get_junos_show_commands"](categories=["bgp", "bfd"]))
        names = [entry["name"] for entry in result["categories"]]
        assert set(names) == {"bgp", "bfd"}

    def test_show_commands_without_categories_returns_all(self, tools):
        result = _run(tools["get_junos_show_commands"]())
        names = {entry["name"] for entry in result["categories"]}
        assert names == EXPECTED_JUNOS_CATEGORIES

    def test_show_commands_unknown_category_returns_partial_plus_warning(self, tools):
        result = _run(
            tools["get_junos_show_commands"](categories=["bgp", "not_a_real_category"])
        )
        names = [entry["name"] for entry in result["categories"]]

        assert names == ["bgp"]
        assert result["warnings"]
        assert (
            result["warnings"][0]
            == "Unknown category ignored: 'not_a_real_category'. Valid categories: "
            "routing, bgp, bfd, evpn, vxlan, mac, vrf_routing_instances, "
            "interfaces, optical_diagnostics, spanning_tree, connectivity_testing, "
            "ntp_dns_services, logs_events, security, system"
        )
        assert result["error"] == "unknown_category"
        assert "unknown_categories" not in result
        assert "valid_categories" not in result

    def test_user_style_workflow_scoped_reference_and_scoped_cli(self, tools):
        overview = _run(tools["get_reference_design_overview"]())
        assert any(s["name"] == "routing_policy" for s in overview["sections"])

        section = _run(tools["get_reference_design_section"](section="routing_policy"))
        assert "BGP Community Architecture" in section["content"]

        cat_index = _run(tools["get_junos_command_categories"]())
        assert any(c["name"] == "bgp" for c in cat_index["categories"])

        commands = _run(
            tools["get_junos_show_commands"](categories=["bgp", "bfd", "routing"])
        )
        command_categories = {entry["name"] for entry in commands["categories"]}
        assert command_categories == {"bgp", "bfd", "routing"}

        bgp_bucket = next(entry for entry in commands["categories"] if entry["name"] == "bgp")
        assert any(cmd["command"] == "show bgp summary" for cmd in bgp_bucket["commands"])
