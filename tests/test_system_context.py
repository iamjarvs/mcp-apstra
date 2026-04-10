import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from primitives.response_parser import parse_system_context
from handlers.system_context import handle_get_system_context, _select_sessions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session(name="dc-primary"):
    session = MagicMock()
    session.name = name
    return session


# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

# Minimal context JSON (covers all root-level scalar fields) embedded as
# a string in the API response envelope, matching the real API shape.
_CTX_SCALARS = {
    "name": "Spine2",
    "hostname": "Spine2",
    "reference_architecture": "two_stage_l3clos",
    "hcl": "Juniper_vEX",
    "ecmp_limit": 32,
    "deploy_mode": "deploy",
    "port_count": 20,
    "role": "spine",
    "configured_role": "spine",
    "model": "Juniper_VIRTUAL-EX9214",
    "lo0_ipv4_address": "172.16.0.10/32",
    "os": "Junos",
    "dual_re": False,
    "os_selector": ".*",
    "asic": "Trio",
    "device_sn": "5254002D005F",
    "node_id": "wvXOipeAz30CkfrOsw",
    "blueprint_has_esi": True,
    "ipv6_support": False,
    "mac_msb": 2,
    "use_granular_mtu_rendering": True,
    "aos_version": "6.1.0",
    "management_ip": "10.28.173.14",
    "os_version": "23.4I20240726_1207_barunkp",
    "snmpv2_community": "public",
    "collector_ip": "10.28.173.6",
}

_CTX_SECTIONS = {
    "slots": [0],
    "system_tags": [],
    "device_capabilities": {
        "copp_strict": False,
        "breakout_capable": {},
        "as_seq_num_supported": True,
    },
    "bgpService": {
        "asn": "64513",
        "router_id": "172.16.0.10",
        "overlay_protocol": "evpn",
    },
    "bgp_sessions": {
        "192.168.0.6_64513->192.168.0.7_64514_default": {
            "source_asn": "64513",
            "dest_asn": "64514",
            "role": "spine_leaf",
        }
    },
    "routing": {
        "has_l3edge": False,
        "route_maps": {},
    },
    "interface": {
        "IF-ge-0/0/0": {"if_name": "ge-0/0/0", "role": "spine_leaf"},
    },
    "ip": {
        "IP-ge-0/0/0": {"ipv4_address": "192.168.0.6", "ipv4_prefixlen": 31},
    },
    "security_zones": {
        "default": {"vrf_name": "default", "loopback_ip": "172.16.0.10"},
    },
    "configlets": {
        "system": [{"display_name": "Example SNMPv2 configlet"}],
    },
    "fabric_policy": {
        "overlay_control_protocol": "evpn",
        "default_svi_l3_mtu": 9000,
    },
    "property_sets": {
        "collector_ip": "10.28.173.6",
        "snmpv2_community": "public",
    },
}

# Full context dict (scalars + all sections)
_FULL_CTX = {**_CTX_SCALARS, **_CTX_SECTIONS}

# Raw API response envelope
RAW_RESPONSE = {"context": json.dumps(_FULL_CTX)}


# ---------------------------------------------------------------------------
# parse_system_context — default (scalars only)
# ---------------------------------------------------------------------------

class TestParseSystemContextDefaults:

    def test_returns_dict(self):
        result = parse_system_context(RAW_RESPONSE)
        assert isinstance(result, dict)

    def test_scalar_fields_present(self):
        result = parse_system_context(RAW_RESPONSE)
        assert result["name"] == "Spine2"
        assert result["hostname"] == "Spine2"
        assert result["role"] == "spine"
        assert result["deploy_mode"] == "deploy"
        assert result["device_sn"] == "5254002D005F"
        assert result["management_ip"] == "10.28.173.14"
        assert result["aos_version"] == "6.1.0"
        assert result["blueprint_has_esi"] is True
        assert result["ipv6_support"] is False

    def test_nested_dicts_excluded_by_default(self):
        result = parse_system_context(RAW_RESPONSE)
        for key in ("device_capabilities", "bgpService", "bgp_sessions",
                    "routing", "interface", "ip", "security_zones",
                    "configlets", "fabric_policy", "property_sets"):
            assert key not in result, f"Section '{key}' should be excluded by default"

    def test_nested_lists_excluded_by_default(self):
        result = parse_system_context(RAW_RESPONSE)
        assert "slots" not in result
        assert "system_tags" not in result

    def test_empty_context_string_raises(self):
        with pytest.raises(Exception):
            parse_system_context({"context": ""})

    def test_invalid_json_raises(self):
        with pytest.raises(Exception):
            parse_system_context({"context": "not-json"})

    def test_missing_context_key_raises(self):
        with pytest.raises(Exception):
            parse_system_context({})


# ---------------------------------------------------------------------------
# parse_system_context — with include_sections
# ---------------------------------------------------------------------------

class TestParseSystemContextSections:

    def test_single_section_included(self):
        result = parse_system_context(RAW_RESPONSE, include_sections=["bgpService"])
        assert "bgpService" in result
        assert result["bgpService"]["asn"] == "64513"

    def test_multiple_sections_included(self):
        result = parse_system_context(
            RAW_RESPONSE, include_sections=["bgpService", "routing"]
        )
        assert "bgpService" in result
        assert "routing" in result

    def test_scalar_fields_still_present_with_sections(self):
        result = parse_system_context(RAW_RESPONSE, include_sections=["bgpService"])
        assert result["hostname"] == "Spine2"
        assert result["role"] == "spine"

    def test_unknown_section_silently_ignored(self):
        result = parse_system_context(
            RAW_RESPONSE, include_sections=["nonexistent_section"]
        )
        assert "nonexistent_section" not in result

    def test_empty_include_sections_returns_scalars_only(self):
        result_empty = parse_system_context(RAW_RESPONSE, include_sections=[])
        result_none = parse_system_context(RAW_RESPONSE, include_sections=None)
        assert result_empty == result_none

    def test_bgp_sessions_section(self):
        result = parse_system_context(RAW_RESPONSE, include_sections=["bgp_sessions"])
        assert "bgp_sessions" in result
        sessions = result["bgp_sessions"]
        assert isinstance(sessions, dict)
        assert len(sessions) == 1

    def test_security_zones_section(self):
        result = parse_system_context(RAW_RESPONSE, include_sections=["security_zones"])
        assert "security_zones" in result
        assert "default" in result["security_zones"]

    def test_configlets_section(self):
        result = parse_system_context(RAW_RESPONSE, include_sections=["configlets"])
        assert "configlets" in result
        assert isinstance(result["configlets"]["system"], list)

    def test_property_sets_section(self):
        result = parse_system_context(RAW_RESPONSE, include_sections=["property_sets"])
        assert "property_sets" in result
        assert result["property_sets"]["snmpv2_community"] == "public"

    def test_all_documented_sections_retrievable(self):
        from primitives.response_parser import SYSTEM_CONTEXT_SECTIONS
        # All keys in SYSTEM_CONTEXT_SECTIONS that exist in our fixture
        # should be retrievable
        existing = [k for k in SYSTEM_CONTEXT_SECTIONS if k in _FULL_CTX]
        result = parse_system_context(RAW_RESPONSE, include_sections=existing)
        for key in existing:
            assert key in result, f"Section '{key}' should have been included"


# ---------------------------------------------------------------------------
# handle_get_system_context — single instance
# ---------------------------------------------------------------------------

class TestHandleGetSystemContextSingle:

    async def test_returns_single_instance_result(self):
        session = make_session("dc-primary")
        session.get_token = AsyncMock(return_value="tok")

        import primitives.live_data_client as ldc
        original = ldc.get_system_config_context
        ldc.get_system_config_context = AsyncMock(return_value=RAW_RESPONSE)

        result = await handle_get_system_context(
            [session], "bp-001", "5254002D005F"
        )

        ldc.get_system_config_context = original

        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["system_id"] == "5254002D005F"
        assert "context" in result

    async def test_context_contains_scalar_fields(self):
        session = make_session()

        import primitives.live_data_client as ldc
        original = ldc.get_system_config_context
        ldc.get_system_config_context = AsyncMock(return_value=RAW_RESPONSE)

        result = await handle_get_system_context([session], "bp-001", "5254002D005F")

        ldc.get_system_config_context = original

        assert result["context"]["hostname"] == "Spine2"
        assert "bgpService" not in result["context"]

    async def test_include_sections_forwarded(self):
        session = make_session()

        import primitives.live_data_client as ldc
        original = ldc.get_system_config_context
        ldc.get_system_config_context = AsyncMock(return_value=RAW_RESPONSE)

        result = await handle_get_system_context(
            [session], "bp-001", "5254002D005F",
            include_sections=["bgpService"]
        )

        ldc.get_system_config_context = original

        assert "bgpService" in result["context"]

    async def test_api_error_returns_error_dict(self):
        session = make_session()

        import primitives.live_data_client as ldc
        original = ldc.get_system_config_context
        ldc.get_system_config_context = AsyncMock(
            side_effect=RuntimeError("connection refused")
        )

        result = await handle_get_system_context([session], "bp-001", "5254002D005F")

        ldc.get_system_config_context = original

        assert "error" in result
        assert "connection refused" in result["error"]
        assert result["context"] == {}

    async def test_error_preserves_blueprint_and_system_id(self):
        session = make_session()

        import primitives.live_data_client as ldc
        original = ldc.get_system_config_context
        ldc.get_system_config_context = AsyncMock(side_effect=Exception("oops"))

        result = await handle_get_system_context([session], "bp-999", "SERIAL123")

        ldc.get_system_config_context = original

        assert result["blueprint_id"] == "bp-999"
        assert result["system_id"] == "SERIAL123"


# ---------------------------------------------------------------------------
# handle_get_system_context — multiple instances
# ---------------------------------------------------------------------------

class TestHandleGetSystemContextMulti:

    async def test_two_instances_returns_all_wrapper(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")

        import primitives.live_data_client as ldc
        original = ldc.get_system_config_context
        ldc.get_system_config_context = AsyncMock(return_value=RAW_RESPONSE)

        result = await handle_get_system_context(
            [s1, s2], "bp-001", "5254002D005F"
        )

        ldc.get_system_config_context = original

        assert result["instance"] == "all"
        assert len(result["results"]) == 2

    async def test_partial_error_still_returns_other(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")

        import primitives.live_data_client as ldc
        original = ldc.get_system_config_context
        ldc.get_system_config_context = AsyncMock(
            side_effect=[RAW_RESPONSE, RuntimeError("dc-secondary unreachable")]
        )

        result = await handle_get_system_context(
            [s1, s2], "bp-001", "5254002D005F"
        )

        ldc.get_system_config_context = original

        assert result["instance"] == "all"
        primary = next(r for r in result["results"] if r["instance"] == "dc-primary")
        secondary = next(r for r in result["results"] if r["instance"] == "dc-secondary")
        assert primary["context"]["hostname"] == "Spine2"
        assert "error" in secondary
        assert secondary["context"] == {}


# ---------------------------------------------------------------------------
# _select_sessions
# ---------------------------------------------------------------------------

class TestSelectSessions:

    def test_none_returns_all(self):
        sessions = [make_session("a"), make_session("b")]
        assert _select_sessions(sessions, None) == sessions

    def test_name_filters_correctly(self):
        a = make_session("alpha")
        b = make_session("beta")
        assert _select_sessions([a, b], "alpha") == [a]

    def test_unknown_name_raises(self):
        sessions = [make_session("dc-primary")]
        with pytest.raises(ValueError, match="No instance named"):
            _select_sessions(sessions, "nonexistent")
