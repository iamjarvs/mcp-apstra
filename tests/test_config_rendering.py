import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from primitives.response_parser import (
    _parse_junos_root_sections,
    _extract_inner_content,
    parse_config_rendering,
)
from handlers.config_rendering import handle_get_rendered_config, _select_sessions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session(name="dc-primary"):
    session = MagicMock()
    session.name = name
    return session


# ---------------------------------------------------------------------------
# Fixture: realistic rendered config matching the Apstra API response shape
# ---------------------------------------------------------------------------

_SAMPLE_CONFIG_TEXT = (
    "system {\n"
    "    host-name Spine2;\n"
    "}\n"
    "interfaces {\n"
    "    replace: ge-0/0/0 {\n"
    "        description \"facing_leaf1:ge-0/0/1\";\n"
    "        mtu 9192;\n"
    "        unit 0 {\n"
    "            family inet {\n"
    "                address 192.168.0.6/31;\n"
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
    "    graceful-restart;\n"
    "    forwarding-table {\n"
    "        export PFE-LB;\n"
    "        ecmp-fast-reroute;\n"
    "    }\n"
    "}\n"
    "protocols {\n"
    "    bgp {\n"
    "        group l3clos-s {\n"
    "            type external;\n"
    "            neighbor 192.168.0.7 {\n"
    "                peer-as 64514;\n"
    "            }\n"
    "        }\n"
    "    }\n"
    "    lldp {\n"
    "        interface all;\n"
    "    }\n"
    "    replace: rstp {\n"
    "        disable;\n"
    "    }\n"
    "}\n"
    "policy-options {\n"
    "    community DEFAULT_DIRECT_V4 {\n"
    "        members [ 2:20007 21001:26000 ];\n"
    "    }\n"
    "    policy-statement BGP-AOS-Policy {\n"
    "        term BGP-AOS-Policy-10 {\n"
    "            then accept;\n"
    "        }\n"
    "    }\n"
    "    policy-statement PFE-LB {\n"
    "        then {\n"
    "            load-balance per-packet;\n"
    "        }\n"
    "    }\n"
    "}\n"
    "------BEGIN SECTION CONFIGLETS------\n"
    "snmp {\n"
    "    community public;\n"
    "}\n"
    "routing-options {\n"
    "    static {\n"
    "        route 10.28.173.6/32 next-table mgmt_junos.inet.0;\n"
    "    }\n"
    "}\n"
    "protocols {\n"
    "    sflow {\n"
    "        polling-interval 10;\n"
    "    }\n"
    "}\n"
)

_SAMPLE_RAW = {"config": _SAMPLE_CONFIG_TEXT}


# ---------------------------------------------------------------------------
# TestParseJunosRootSections
# ---------------------------------------------------------------------------

class TestParseJunosRootSections:

    def test_finds_all_main_sections(self):
        main_text = _SAMPLE_CONFIG_TEXT.split("------BEGIN SECTION CONFIGLETS------")[0]
        sections = _parse_junos_root_sections(main_text)
        assert set(sections.keys()) == {"system", "interfaces", "routing-options", "protocols", "policy-options"}

    def test_section_text_starts_with_section_name(self):
        main_text = _SAMPLE_CONFIG_TEXT.split("------BEGIN SECTION CONFIGLETS------")[0]
        sections = _parse_junos_root_sections(main_text)
        assert sections["system"].startswith("system {")
        assert sections["interfaces"].startswith("interfaces {")
        assert sections["routing-options"].startswith("routing-options {")
        assert sections["protocols"].startswith("protocols {")
        assert sections["policy-options"].startswith("policy-options {")

    def test_nested_braces_do_not_close_section_early(self):
        # forwarding-table {} is nested inside routing-options — must be captured
        main_text = _SAMPLE_CONFIG_TEXT.split("------BEGIN SECTION CONFIGLETS------")[0]
        sections = _parse_junos_root_sections(main_text)
        assert "forwarding-table" in sections["routing-options"]
        assert "ecmp-fast-reroute" in sections["routing-options"]

    def test_deeply_nested_bgp_neighbour_captured(self):
        main_text = _SAMPLE_CONFIG_TEXT.split("------BEGIN SECTION CONFIGLETS------")[0]
        sections = _parse_junos_root_sections(main_text)
        assert "peer-as 64514" in sections["protocols"]
        assert "l3clos-s" in sections["protocols"]

    def test_replace_qualifier_stripped_from_section_name(self):
        # "replace: rstp {" inside protocols is depth 1 — not a root section.
        # At root level, "replace: interfaces {" would be normalised to "interfaces".
        text = "replace: interfaces {\n    ge-0/0/0;\n}\n"
        sections = _parse_junos_root_sections(text)
        assert "interfaces" in sections
        assert "replace: interfaces" not in sections

    def test_configlet_sections_parsed(self):
        configlet_text = _SAMPLE_CONFIG_TEXT.split("------BEGIN SECTION CONFIGLETS------")[1]
        sections = _parse_junos_root_sections(configlet_text)
        assert "snmp" in sections
        assert "routing-options" in sections
        assert "protocols" in sections

    def test_empty_text_returns_empty_dict(self):
        assert _parse_junos_root_sections("") == {}

    def test_section_text_ends_with_closing_brace(self):
        main_text = _SAMPLE_CONFIG_TEXT.split("------BEGIN SECTION CONFIGLETS------")[0]
        sections = _parse_junos_root_sections(main_text)
        for name, text in sections.items():
            assert text.rstrip().endswith("}"), f"Section '{name}' does not end with }}"


# ---------------------------------------------------------------------------
# TestParseConfigRendering
# ---------------------------------------------------------------------------

class TestParseConfigRendering:

    def test_available_sections_correct(self):
        result = parse_config_rendering(_SAMPLE_RAW)
        assert result["available_sections"] == [
            "interfaces", "policy-options", "protocols", "routing-options", "system"
        ]

    def test_available_configlet_sections_correct(self):
        result = parse_config_rendering(_SAMPLE_RAW)
        assert result["available_configlet_sections"] == [
            "protocols", "routing-options", "snmp"
        ]

    def test_all_sections_returned_when_no_filter(self):
        result = parse_config_rendering(_SAMPLE_RAW)
        assert set(result["sections"].keys()) == {
            "system", "interfaces", "routing-options", "protocols", "policy-options"
        }
        assert set(result["configlets"].keys()) == {"snmp", "routing-options", "protocols"}

    def test_section_filter_limits_main_sections(self):
        result = parse_config_rendering(_SAMPLE_RAW, sections=["routing-options", "protocols"])
        assert set(result["sections"].keys()) == {"routing-options", "protocols"}

    def test_section_filter_limits_configlet_sections(self):
        result = parse_config_rendering(_SAMPLE_RAW, sections=["routing-options"])
        assert set(result["configlets"].keys()) == {"routing-options"}

    def test_section_filter_unknown_name_returns_empty(self):
        result = parse_config_rendering(_SAMPLE_RAW, sections=["nonexistent"])
        assert result["sections"] == {}
        assert result["configlets"] == {}

    def test_no_configlet_separator_produces_empty_configlets(self):
        raw = {"config": "system {\n    host-name R1;\n}\n"}
        result = parse_config_rendering(raw)
        assert result["configlets"] == {}
        assert result["available_configlet_sections"] == []
        assert "system" in result["sections"]

    def test_empty_config_returns_empty_result(self):
        result = parse_config_rendering({"config": ""})
        assert result["sections"] == {}
        assert result["configlets"] == {}
        assert result["available_sections"] == []
        assert result["available_configlet_sections"] == []

    def test_routing_options_content_correct(self):
        result = parse_config_rendering(_SAMPLE_RAW)
        ro = result["sections"]["routing-options"]
        assert "router-id 172.16.0.10" in ro
        assert "autonomous-system 64513" in ro

    def test_policy_options_contains_community_and_policy(self):
        result = parse_config_rendering(_SAMPLE_RAW)
        po = result["sections"]["policy-options"]
        assert "DEFAULT_DIRECT_V4" in po
        assert "BGP-AOS-Policy" in po
        assert "PFE-LB" in po

    def test_configlet_snmp_section_content(self):
        result = parse_config_rendering(_SAMPLE_RAW)
        assert "community public" in result["configlets"]["snmp"]

    def test_return_keys_present(self):
        result = parse_config_rendering(_SAMPLE_RAW)
        assert "available_sections" in result
        assert "available_configlet_sections" in result
        assert "available_subsections" in result
        assert "sections" in result
        assert "subsection_detail" in result
        assert "configlets" in result

    def test_subsection_detail_empty_when_no_subsections_param(self):
        result = parse_config_rendering(_SAMPLE_RAW)
        assert result["subsection_detail"] == {}


# ---------------------------------------------------------------------------
# TestSelectSessions (handler helper)
# ---------------------------------------------------------------------------

class TestSelectSessions:

    def test_returns_all_sessions_when_no_instance_name(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        result = _select_sessions(sessions, None)
        assert result == sessions

    def test_filters_by_instance_name(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        result = _select_sessions(sessions, "dc-secondary")
        assert len(result) == 1
        assert result[0].name == "dc-secondary"

    def test_raises_for_unknown_instance(self):
        sessions = [make_session("dc-primary")]
        with pytest.raises(ValueError, match="dc-missing"):
            _select_sessions(sessions, "dc-missing")


# ---------------------------------------------------------------------------
# TestHandleGetRenderedConfig
# ---------------------------------------------------------------------------

class TestHandleGetRenderedConfig:

    @pytest.fixture
    def mock_api_response(self):
        return _SAMPLE_RAW

    async def test_single_session_returns_flat_result(self, mock_api_response):
        session = make_session()
        with patch(
            "primitives.live_data_client.get_config_rendering",
            new=AsyncMock(return_value=mock_api_response),
        ):
            result = await handle_get_rendered_config(
                [session], "bp-001", "5254002D005F"
            )

        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["system_id"] == "5254002D005F"
        assert "sections" in result
        assert "subsection_detail" in result
        assert "available_subsections" in result
        assert "configlets" in result
        assert "system" in result["sections"]

    async def test_multi_session_returns_results_list(self, mock_api_response):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        with patch(
            "primitives.live_data_client.get_config_rendering",
            new=AsyncMock(return_value=mock_api_response),
        ):
            result = await handle_get_rendered_config(
                sessions, "bp-001", "5254002D005F"
            )

        assert result["instance"] == "all"
        assert len(result["results"]) == 2
        assert result["results"][0]["instance"] == "dc-primary"
        assert result["results"][1]["instance"] == "dc-secondary"

    async def test_section_filter_forwarded_to_parser(self, mock_api_response):
        session = make_session()
        with patch(
            "primitives.live_data_client.get_config_rendering",
            new=AsyncMock(return_value=mock_api_response),
        ):
            result = await handle_get_rendered_config(
                [session], "bp-001", "5254002D005F",
                sections=["routing-options"],
            )

        assert set(result["sections"].keys()) == {"routing-options"}

    async def test_api_error_captured_in_result(self):
        session = make_session()
        with patch(
            "primitives.live_data_client.get_config_rendering",
            new=AsyncMock(side_effect=RuntimeError("API timeout")),
        ):
            result = await handle_get_rendered_config(
                [session], "bp-001", "5254002D005F"
            )

        assert "error" in result
        assert "API timeout" in result["error"]
        assert result["sections"] == {}

    async def test_instance_name_selects_correct_session(self, mock_api_response):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        with patch(
            "primitives.live_data_client.get_config_rendering",
            new=AsyncMock(return_value=mock_api_response),
        ) as mock_call:
            result = await handle_get_rendered_config(
                sessions, "bp-001", "5254002D005F",
                instance_name="dc-secondary",
            )

        assert result["instance"] == "dc-secondary"
        assert mock_call.call_count == 1


# ---------------------------------------------------------------------------
# TestExtractInnerContent
# ---------------------------------------------------------------------------

class TestExtractInnerContent:

    def test_strips_outer_wrapper(self):
        block = "interfaces {\n    ge-0/0/0 {\n        mtu 9192;\n    }\n}"
        inner = _extract_inner_content(block)
        assert inner.strip().startswith("ge-0/0/0 {")

    def test_dedents_inner_lines(self):
        block = "interfaces {\n    ge-0/0/0 {\n        mtu 9192;\n    }\n}"
        inner = _extract_inner_content(block)
        # Common 4-space indent removed; first char should not be a space
        assert not inner.splitlines()[0].startswith(" ")

    def test_short_block_returns_empty(self):
        assert _extract_inner_content("section {\n}") == ""
        assert _extract_inner_content("") == ""

    def test_blank_inner_returns_empty(self):
        assert _extract_inner_content("section {\n    \n}") == ""


# ---------------------------------------------------------------------------
# TestAvailableSubsections
# ---------------------------------------------------------------------------

class TestAvailableSubsections:

    def test_interfaces_subsections_listed(self):
        result = parse_config_rendering(_SAMPLE_RAW)
        assert "interfaces" in result["available_subsections"]
        ifaces = result["available_subsections"]["interfaces"]
        assert "ge-0/0/0" in ifaces
        assert "lo0" in ifaces

    def test_protocols_subsections_listed(self):
        result = parse_config_rendering(_SAMPLE_RAW)
        protos = result["available_subsections"].get("protocols", [])
        assert "bgp" in protos
        assert "lldp" in protos

    def test_policy_options_subsections_listed(self):
        result = parse_config_rendering(_SAMPLE_RAW)
        po = result["available_subsections"].get("policy-options", [])
        # community and policy-statement names include the keyword prefix
        assert any("default_direct_v4" in name for name in po)
        assert any("bgp-aos-policy" in name for name in po)
        assert any("pfe-lb" in name for name in po)

    def test_available_subsections_sorted(self):
        result = parse_config_rendering(_SAMPLE_RAW)
        for section, children in result["available_subsections"].items():
            assert children == sorted(children), f"{section} children not sorted"

    def test_scalar_only_section_not_in_available_subsections(self):
        # routing-options has a forwarding-table child block so it WILL appear.
        # 'system' has no child blocks in our sample, so it should NOT appear.
        result = parse_config_rendering(_SAMPLE_RAW)
        assert "system" not in result["available_subsections"]

    def test_available_subsections_respects_sections_filter(self):
        # Only request protocols; available_subsections should not contain others
        result = parse_config_rendering(_SAMPLE_RAW, sections=["protocols"])
        assert "protocols" in result["available_subsections"]
        assert "interfaces" not in result["available_subsections"]


# ---------------------------------------------------------------------------
# TestSubsectionsFiltering
# ---------------------------------------------------------------------------

class TestSubsectionsFiltering:

    def test_interface_subsection_moves_to_subsection_detail(self):
        result = parse_config_rendering(
            _SAMPLE_RAW, subsections={"interfaces": ["ge-0/0/0"]}
        )
        assert "interfaces" not in result["sections"]
        assert "ge-0/0/0" in result["subsection_detail"]["interfaces"]

    def test_interface_subsection_content_correct(self):
        result = parse_config_rendering(
            _SAMPLE_RAW, subsections={"interfaces": ["ge-0/0/0"]}
        )
        intf_text = result["subsection_detail"]["interfaces"]["ge-0/0/0"]
        assert "192.168.0.6/31" in intf_text
        assert "mtu 9192" in intf_text

    def test_non_narrowed_sections_remain_in_sections(self):
        result = parse_config_rendering(
            _SAMPLE_RAW, subsections={"interfaces": ["ge-0/0/0"]}
        )
        assert "routing-options" in result["sections"]
        assert "protocols" in result["sections"]
        assert "policy-options" in result["sections"]

    def test_protocols_bgp_subsection(self):
        result = parse_config_rendering(
            _SAMPLE_RAW, subsections={"protocols": ["bgp"]}
        )
        assert "protocols" not in result["sections"]
        bgp_text = result["subsection_detail"]["protocols"]["bgp"]
        assert "l3clos-s" in bgp_text
        assert "peer-as 64514" in bgp_text

    def test_unknown_subsection_child_returns_empty_dict_for_section(self):
        result = parse_config_rendering(
            _SAMPLE_RAW, subsections={"interfaces": ["nonexistent-intf"]}
        )
        assert result["subsection_detail"]["interfaces"] == {}

    def test_multiple_subsections_filtered_together(self):
        result = parse_config_rendering(
            _SAMPLE_RAW,
            subsections={"interfaces": ["lo0"], "protocols": ["lldp"]},
        )
        assert "interfaces" not in result["sections"]
        assert "protocols" not in result["sections"]
        assert "lo0" in result["subsection_detail"]["interfaces"]
        assert "lldp" in result["subsection_detail"]["protocols"]

    def test_subsection_combined_with_sections_filter(self):
        result = parse_config_rendering(
            _SAMPLE_RAW,
            sections=["interfaces", "routing-options"],
            subsections={"interfaces": ["ge-0/0/0"]},
        )
        assert set(result["sections"].keys()) == {"routing-options"}
        assert "ge-0/0/0" in result["subsection_detail"]["interfaces"]

    def test_lo0_subsection_content(self):
        result = parse_config_rendering(
            _SAMPLE_RAW, subsections={"interfaces": ["lo0"]}
        )
        lo0_text = result["subsection_detail"]["interfaces"]["lo0"]
        assert "172.16.0.10/32" in lo0_text

    def test_available_subsections_still_populated_when_subsections_filter_used(self):
        # available_subsections lists all children even when a filter is applied
        result = parse_config_rendering(
            _SAMPLE_RAW,
            sections=["interfaces"],
            subsections={"interfaces": ["ge-0/0/0"]},
        )
        ifaces = result["available_subsections"].get("interfaces", [])
        assert "ge-0/0/0" in ifaces
        assert "lo0" in ifaces  # lo0 listed even though not in subsections filter


# ---------------------------------------------------------------------------
# TestHandleSubsectionsForwarded
# ---------------------------------------------------------------------------

class TestHandleSubsectionsForwarded:

    async def test_subsections_passed_through_to_parser(self):
        session = make_session()
        with patch(
            "primitives.live_data_client.get_config_rendering",
            new=AsyncMock(return_value=_SAMPLE_RAW),
        ):
            result = await handle_get_rendered_config(
                [session], "bp-001", "5254002D005F",
                subsections={"interfaces": ["ge-0/0/0"]},
            )

        assert "subsection_detail" in result
        assert "ge-0/0/0" in result["subsection_detail"]["interfaces"]
        assert "interfaces" not in result["sections"]
