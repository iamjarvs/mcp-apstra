import pytest
from unittest.mock import AsyncMock, MagicMock

from primitives.response_parser import parse_virtual_networks, parse_virtual_network_list
from handlers.virtual_networks import (
    handle_get_virtual_networks,
    handle_get_virtual_network_list,
    _select_sessions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session(name="dc-primary"):
    session = MagicMock()
    session.name = name
    return session


def make_registry(rows=None, error=None):
    registry = MagicMock()
    graph = MagicMock()
    if error:
        registry.get_or_rebuild = AsyncMock(side_effect=error)
    else:
        graph.query = MagicMock(return_value=rows or [])
        registry.get_or_rebuild = AsyncMock(return_value=graph)
    return registry


# Raw Kuzu rows as returned by graph.query() — column names include prefixes
RAW_ROWS = [
    {
        "sw.id": "nWePWChh0aYcsYOvNw",
        "sw.label": "Leaf1",
        "vni.id": "AvZUiXvwUp8Tkfzv7Q",
        "vni.vlan_id": 10,
        "vni.ipv4_enabled": True,
        "vni.ipv4_mode": "enabled",
        "vni.dhcp_enabled": False,
        "vn.id": "J3d9vF5uXRp_N8ebXQ",
        "vn.label": "Hypervisor_Dev",
        "vn.vn_type": "vxlan",
        "vn.vn_id": "10050",
        "vn.reserved_vlan_id": 10,
        "vn.ipv4_enabled": True,
        "vn.ipv4_subnet": "10.80.0.0/18",
        "vn.ipv6_enabled": False,
        "vn.virtual_gateway_ipv4": "10.80.0.1",
        "vn.l3_mtu": 9000,
    },
    {
        "sw.id": "nWePWChh0aYcsYOvXx",
        "sw.label": "Leaf2",
        "vni.id": "BqAViYwxVf9Ulgzw8R",
        "vni.vlan_id": 11,  # same VNI, different VLAN on Leaf2
        "vni.ipv4_enabled": True,
        "vni.ipv4_mode": "enabled",
        "vni.dhcp_enabled": False,
        "vn.id": "J3d9vF5uXRp_N8ebXQ",
        "vn.label": "Hypervisor_Dev",
        "vn.vn_type": "vxlan",
        "vn.vn_id": "10050",
        "vn.reserved_vlan_id": 10,
        "vn.ipv4_enabled": True,
        "vn.ipv4_subnet": "10.80.0.0/18",
        "vn.ipv6_enabled": False,
        "vn.virtual_gateway_ipv4": "10.80.0.1",
        "vn.l3_mtu": 9000,
    },
]

PARSED_VNS = [
    {
        "sw_id": "nWePWChh0aYcsYOvNw",
        "sw_label": "Leaf1",
        "vni_id": "AvZUiXvwUp8Tkfzv7Q",
        "vlan_id": 10,
        "ipv4_enabled": True,
        "ipv4_mode": "enabled",
        "dhcp_enabled": False,
        "vn_id": "J3d9vF5uXRp_N8ebXQ",
        "vn_label": "Hypervisor_Dev",
        "vn_type": "vxlan",
        "vni_number": "10050",
        "reserved_vlan_id": 10,
        "vn_ipv4_enabled": True,
        "ipv4_subnet": "10.80.0.0/18",
        "ipv6_enabled": False,
        "virtual_gateway_ipv4": "10.80.0.1",
        "l3_mtu": 9000,
    },
    {
        "sw_id": "nWePWChh0aYcsYOvXx",
        "sw_label": "Leaf2",
        "vni_id": "BqAViYwxVf9Ulgzw8R",
        "vlan_id": 11,
        "ipv4_enabled": True,
        "ipv4_mode": "enabled",
        "dhcp_enabled": False,
        "vn_id": "J3d9vF5uXRp_N8ebXQ",
        "vn_label": "Hypervisor_Dev",
        "vn_type": "vxlan",
        "vni_number": "10050",
        "reserved_vlan_id": 10,
        "vn_ipv4_enabled": True,
        "ipv4_subnet": "10.80.0.0/18",
        "ipv6_enabled": False,
        "virtual_gateway_ipv4": "10.80.0.1",
        "l3_mtu": 9000,
    },
]


# ---------------------------------------------------------------------------
# response_parser.parse_virtual_networks
# ---------------------------------------------------------------------------

class TestParseVirtualNetworks:
    def test_strips_prefixes_and_maps_fields(self):
        assert parse_virtual_networks(RAW_ROWS) == PARSED_VNS

    def test_same_vni_different_vlan_per_switch(self):
        result = parse_virtual_networks(RAW_ROWS)
        vni_numbers = {r["vni_number"] for r in result}
        vlan_ids = {r["vlan_id"] for r in result}
        assert vni_numbers == {"10050"}  # same VNI
        assert vlan_ids == {10, 11}       # different VLANs

    def test_empty_rows(self):
        assert parse_virtual_networks([]) == []

    def test_missing_fields_use_defaults(self):
        result = parse_virtual_networks([{}])
        assert result[0]["sw_id"] is None
        assert result[0]["vlan_id"] is None
        assert result[0]["ipv4_enabled"] is False
        assert result[0]["dhcp_enabled"] is False
        assert result[0]["vn_ipv4_enabled"] is False
        assert result[0]["ipv6_enabled"] is False

    def test_vn_type_vxlan(self):
        result = parse_virtual_networks(RAW_ROWS)
        assert result[0]["vn_type"] == "vxlan"

    def test_vni_number_is_string(self):
        result = parse_virtual_networks(RAW_ROWS)
        assert isinstance(result[0]["vni_number"], str)


# ---------------------------------------------------------------------------
# _select_sessions
# ---------------------------------------------------------------------------

class TestSelectSessions:
    def test_none_returns_all(self):
        sessions = [make_session("a"), make_session("b")]
        assert _select_sessions(sessions, None) == sessions

    def test_named_returns_matching_session(self):
        sessions = [make_session("a"), make_session("b")]
        result = _select_sessions(sessions, "b")
        assert len(result) == 1
        assert result[0].name == "b"

    def test_unknown_name_raises_value_error(self):
        sessions = [make_session("a")]
        with pytest.raises(ValueError, match="No instance named 'x'"):
            _select_sessions(sessions, "x")


# ---------------------------------------------------------------------------
# handle_get_virtual_networks
# ---------------------------------------------------------------------------

class TestHandleGetVirtualNetworks:
    async def test_single_session_all_switches(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=RAW_ROWS)

        result = await handle_get_virtual_networks([session], registry, "bp-001")

        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["system_id"] is None
        assert result["vn_instances"] == PARSED_VNS
        assert result["count"] == 2

    async def test_single_session_filtered_by_system_id(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=[RAW_ROWS[0]])

        result = await handle_get_virtual_networks(
            [session], registry, "bp-001", system_id="525400AFC169"
        )

        assert result["system_id"] == "525400AFC169"
        assert result["count"] == 1
        # Verify the correct Cypher (with params) was called
        graph = registry.get_or_rebuild.return_value
        call_args = graph.query.call_args
        assert call_args[0][1] == {"system_id": "525400AFC169"}

    async def test_single_session_registry_error_returns_error_dict(self):
        session = make_session("dc-primary")
        registry = make_registry(error=Exception("graph error"))

        result = await handle_get_virtual_networks([session], registry, "bp-001")

        assert result["instance"] == "dc-primary"
        assert result["error"] == "graph error"
        assert result["vn_instances"] == []
        assert result["count"] == 0

    async def test_empty_blueprint_returns_empty_vn_instances(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=[])

        result = await handle_get_virtual_networks([session], registry, "bp-001")

        assert result["count"] == 0
        assert result["vn_instances"] == []

    async def test_multiple_sessions_aggregated(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        registry = make_registry(rows=RAW_ROWS)

        result = await handle_get_virtual_networks(sessions, registry, "bp-001")

        assert result["instance"] == "all"
        assert result["blueprint_id"] == "bp-001"
        assert result["total_count"] == 4
        assert len(result["results"]) == 2

    async def test_multiple_sessions_partial_failure(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]

        async def registry_side_effect(session, blueprint_id):
            if session.name == "dc-secondary":
                raise Exception("unreachable")
            graph = MagicMock()
            graph.query = MagicMock(return_value=RAW_ROWS)
            return graph

        registry = MagicMock()
        registry.get_or_rebuild = AsyncMock(side_effect=registry_side_effect)

        result = await handle_get_virtual_networks(sessions, registry, "bp-001")

        assert result["instance"] == "all"
        assert result["total_count"] == 2
        good = next(r for r in result["results"] if r["instance"] == "dc-primary")
        bad = next(r for r in result["results"] if r["instance"] == "dc-secondary")
        assert good["count"] == 2
        assert "error" in bad

    async def test_instance_name_filter_queries_only_named_session(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        registry = make_registry(rows=RAW_ROWS)

        result = await handle_get_virtual_networks(
            sessions, registry, "bp-001", instance_name="dc-secondary"
        )

        assert result["instance"] == "dc-secondary"
        assert registry.get_or_rebuild.call_count == 1

    async def test_unknown_instance_name_raises(self):
        sessions = [make_session("dc-primary")]
        registry = make_registry(rows=[])

        with pytest.raises(ValueError, match="No instance named 'nonexistent'"):
            await handle_get_virtual_networks(
                sessions, registry, "bp-001", instance_name="nonexistent"
            )


# ---------------------------------------------------------------------------
# response_parser.parse_virtual_network_list
# ---------------------------------------------------------------------------

RAW_VN_LIST_ROWS = [
    {
        "vn.id": "J3d9vF5uXRp_N8ebXQ",
        "vn.label": "Hypervisor_Dev",
        "vn.vn_type": "vxlan",
        "vn.vn_id": "10050",
        "vn.reserved_vlan_id": 10,
        "vn.ipv4_enabled": True,
        "vn.ipv4_subnet": "10.80.0.0/18",
        "vn.ipv6_enabled": False,
        "vn.ipv6_subnet": None,
        "vn.virtual_gateway_ipv4": "10.80.0.1",
        "vn.virtual_gateway_ipv4_enabled": True,
        "vn.virtual_gateway_ipv6": None,
        "vn.virtual_gateway_ipv6_enabled": False,
        "vn.virtual_mac": None,
        "vn.l3_mtu": 9000,
        "vn.description": None,
        "vn.tags": None,
        "routing_zone_label": "Prod_VRF",
        "vrf_name": "Prod_VRF",
        "routing_zone_type": "evpn",
    },
    {
        "vn.id": "GEmKGZz6-YfpHuBYaw",
        "vn.label": "Web_Prod",
        "vn.vn_type": "vxlan",
        "vn.vn_id": "10000",
        "vn.reserved_vlan_id": 101,
        "vn.ipv4_enabled": True,
        "vn.ipv4_subnet": "10.80.101.0/24",
        "vn.ipv6_enabled": False,
        "vn.ipv6_subnet": None,
        "vn.virtual_gateway_ipv4": "10.80.101.1",
        "vn.virtual_gateway_ipv4_enabled": True,
        "vn.virtual_gateway_ipv6": None,
        "vn.virtual_gateway_ipv6_enabled": False,
        "vn.virtual_mac": None,
        "vn.l3_mtu": 9000,
        "vn.description": None,
        "vn.tags": None,
        "routing_zone_label": None,   # not yet assigned to a routing zone
        "vrf_name": None,
        "routing_zone_type": None,
    },
]

PARSED_VN_LIST = [
    {
        "id": "J3d9vF5uXRp_N8ebXQ",
        "label": "Hypervisor_Dev",
        "vn_type": "vxlan",
        "vni_number": "10050",
        "reserved_vlan_id": 10,
        "ipv4_enabled": True,
        "ipv4_subnet": "10.80.0.0/18",
        "ipv6_enabled": False,
        "ipv6_subnet": None,
        "virtual_gateway_ipv4": "10.80.0.1",
        "virtual_gateway_ipv4_enabled": True,
        "virtual_gateway_ipv6": None,
        "virtual_gateway_ipv6_enabled": False,
        "virtual_mac": None,
        "l3_mtu": 9000,
        "description": None,
        "tags": None,
        "routing_zone_label": "Prod_VRF",
        "vrf_name": "Prod_VRF",
        "routing_zone_type": "evpn",
    },
    {
        "id": "GEmKGZz6-YfpHuBYaw",
        "label": "Web_Prod",
        "vn_type": "vxlan",
        "vni_number": "10000",
        "reserved_vlan_id": 101,
        "ipv4_enabled": True,
        "ipv4_subnet": "10.80.101.0/24",
        "ipv6_enabled": False,
        "ipv6_subnet": None,
        "virtual_gateway_ipv4": "10.80.101.1",
        "virtual_gateway_ipv4_enabled": True,
        "virtual_gateway_ipv6": None,
        "virtual_gateway_ipv6_enabled": False,
        "virtual_mac": None,
        "l3_mtu": 9000,
        "description": None,
        "tags": None,
        "routing_zone_label": None,
        "vrf_name": None,
        "routing_zone_type": None,
    },
]


class TestParseVirtualNetworkList:
    def test_strips_prefixes_and_maps_all_fields(self):
        assert parse_virtual_network_list(RAW_VN_LIST_ROWS) == PARSED_VN_LIST

    def test_null_optional_fields_present_as_none(self):
        result = parse_virtual_network_list(RAW_VN_LIST_ROWS)
        assert result[0]["ipv6_subnet"] is None
        assert result[0]["virtual_gateway_ipv6"] is None
        assert result[0]["virtual_mac"] is None
        assert result[0]["description"] is None
        assert result[0]["tags"] is None

    def test_optional_match_null_routing_zone(self):
        # VN not yet assigned to a routing zone — routing_zone_label stays None
        result = parse_virtual_network_list(RAW_VN_LIST_ROWS)
        assert result[1]["routing_zone_label"] is None
        assert result[1]["vrf_name"] is None

    def test_severity_label_not_in_items(self):
        result = parse_virtual_network_list(RAW_VN_LIST_ROWS)
        assert all("severity_label" not in r for r in result)

    def test_empty_rows(self):
        assert parse_virtual_network_list([]) == []

    def test_missing_fields_use_defaults(self):
        result = parse_virtual_network_list([{}])
        assert result[0]["id"] is None
        assert result[0]["ipv4_enabled"] is False
        assert result[0]["ipv6_enabled"] is False
        assert result[0]["virtual_gateway_ipv4_enabled"] is False
        assert result[0]["virtual_gateway_ipv6_enabled"] is False


# ---------------------------------------------------------------------------
# handle_get_virtual_network_list
# ---------------------------------------------------------------------------

class TestHandleGetVirtualNetworkList:
    async def test_single_session_success(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=RAW_VN_LIST_ROWS)

        result = await handle_get_virtual_network_list([session], registry, "bp-001")

        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["virtual_networks"] == PARSED_VN_LIST
        assert result["count"] == 2

    async def test_single_session_registry_error_returns_error_dict(self):
        session = make_session("dc-primary")
        registry = make_registry(error=Exception("graph error"))

        result = await handle_get_virtual_network_list([session], registry, "bp-001")

        assert result["instance"] == "dc-primary"
        assert result["error"] == "graph error"
        assert result["virtual_networks"] == []
        assert result["count"] == 0

    async def test_empty_blueprint_returns_empty_list(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=[])

        result = await handle_get_virtual_network_list([session], registry, "bp-001")

        assert result["count"] == 0
        assert result["virtual_networks"] == []

    async def test_multiple_sessions_aggregated(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        registry = make_registry(rows=RAW_VN_LIST_ROWS)

        result = await handle_get_virtual_network_list(sessions, registry, "bp-001")

        assert result["instance"] == "all"
        assert result["blueprint_id"] == "bp-001"
        assert result["total_count"] == 4
        assert len(result["results"]) == 2

    async def test_instance_name_filter_queries_only_named_session(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        registry = make_registry(rows=RAW_VN_LIST_ROWS)

        result = await handle_get_virtual_network_list(
            sessions, registry, "bp-001", instance_name="dc-secondary"
        )

        assert result["instance"] == "dc-secondary"
        assert registry.get_or_rebuild.call_count == 1

    async def test_unknown_instance_name_raises(self):
        sessions = [make_session("dc-primary")]
        registry = make_registry(rows=[])

        with pytest.raises(ValueError, match="No instance named 'nonexistent'"):
            await handle_get_virtual_network_list(
                sessions, registry, "bp-001", instance_name="nonexistent"
            )
