import pytest
from unittest.mock import AsyncMock, MagicMock

from primitives.response_parser import (
    parse_routing_zones,
    parse_routing_zone_detail,
    parse_virtual_network_detail,
)
from handlers.virtual_networks import (
    handle_get_routing_zones,
    handle_get_routing_zone_detail,
    handle_get_virtual_network_detail,
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


# ---------------------------------------------------------------------------
# Raw Kuzu rows for routing zone list query
# ---------------------------------------------------------------------------

SZ_LIST_ROWS = [
    {"sz.id": "szA1", "sz.label": "Alpha_VRF", "sz.vrf_name": "alpha_vrf", "sz.sz_type": "evpn", "vn_count": 3},
    {"sz.id": "szB2", "sz.label": "Default", "sz.vrf_name": "default", "sz.sz_type": "l3_fabric", "vn_count": 0},
    {"sz.id": "szC3", "sz.label": "Mgmt_VRF", "sz.vrf_name": "mgmt_vrf", "sz.sz_type": "evpn", "vn_count": 1},
]

PARSED_SZ_LIST = [
    {"id": "szA1", "label": "Alpha_VRF", "vrf_name": "alpha_vrf", "sz_type": "evpn", "vn_count": 3},
    {"id": "szB2", "label": "Default", "vrf_name": "default", "sz_type": "l3_fabric", "vn_count": 0},
    {"id": "szC3", "label": "Mgmt_VRF", "vrf_name": "mgmt_vrf", "sz_type": "evpn", "vn_count": 1},
]

# ---------------------------------------------------------------------------
# Raw rows for routing zone detail query (1 zone, 2 VNs, 2 switches)
# Each row = one (VN, switch) pair
# ---------------------------------------------------------------------------

SZ_DETAIL_ROWS = [
    {
        "sz.id": "szA1",
        "sz.label": "Alpha_VRF",
        "sz.vrf_name": "alpha_vrf",
        "sz.sz_type": "evpn",
        "vn_id": "vn01",
        "vn_label": "App_Net",
        "vn_type": "vxlan",
        "vni_number": "10010",
        "vn_ipv4_enabled": True,
        "vn_ipv4_subnet": "10.1.0.0/24",
        "vn_ipv6_enabled": False,
        "vn_ipv6_subnet": None,
        "vn_gw_ipv4": "10.1.0.1",
        "vn_l3_mtu": 9000,
        "vn_description": "Application network",
        "vn_tags": None,
        "sw_id": "sw01",
        "sw_label": "Leaf1",
        "sw_role": "leaf",
        "vni_vlan_id": 100,
    },
    {
        "sz.id": "szA1",
        "sz.label": "Alpha_VRF",
        "sz.vrf_name": "alpha_vrf",
        "sz.sz_type": "evpn",
        "vn_id": "vn01",
        "vn_label": "App_Net",
        "vn_type": "vxlan",
        "vni_number": "10010",
        "vn_ipv4_enabled": True,
        "vn_ipv4_subnet": "10.1.0.0/24",
        "vn_ipv6_enabled": False,
        "vn_ipv6_subnet": None,
        "vn_gw_ipv4": "10.1.0.1",
        "vn_l3_mtu": 9000,
        "vn_description": "Application network",
        "vn_tags": None,
        "sw_id": "sw02",
        "sw_label": "Leaf2",
        "sw_role": "leaf",
        "vni_vlan_id": 101,
    },
    {
        "sz.id": "szA1",
        "sz.label": "Alpha_VRF",
        "sz.vrf_name": "alpha_vrf",
        "sz.sz_type": "evpn",
        "vn_id": "vn02",
        "vn_label": "DB_Net",
        "vn_type": "vxlan",
        "vni_number": "10020",
        "vn_ipv4_enabled": True,
        "vn_ipv4_subnet": "10.2.0.0/24",
        "vn_ipv6_enabled": False,
        "vn_ipv6_subnet": None,
        "vn_gw_ipv4": "10.2.0.1",
        "vn_l3_mtu": 9000,
        "vn_description": None,
        "vn_tags": None,
        "sw_id": "sw01",
        "sw_label": "Leaf1",
        "sw_role": "leaf",
        "vni_vlan_id": 200,
    },
]

# ---------------------------------------------------------------------------
# Raw rows for VN detail query (1 VN, 2 switches, 1 routing zone)
# ---------------------------------------------------------------------------

VN_DETAIL_ROWS = [
    {
        "vn.id": "vn01",
        "vn.label": "App_Net",
        "vn.vn_type": "vxlan",
        "vn.vn_id": "10010",
        "vn.reserved_vlan_id": 100,
        "vn.ipv4_enabled": True,
        "vn.ipv4_subnet": "10.1.0.0/24",
        "vn.ipv6_enabled": False,
        "vn.ipv6_subnet": None,
        "vn.virtual_gateway_ipv4": "10.1.0.1",
        "vn.virtual_gateway_ipv4_enabled": True,
        "vn.virtual_gateway_ipv6": None,
        "vn.virtual_gateway_ipv6_enabled": False,
        "vn.virtual_mac": None,
        "vn.l3_mtu": 9000,
        "vn.description": "Application network",
        "vn.tags": None,
        "sz_id": "szA1",
        "routing_zone_label": "Alpha_VRF",
        "vrf_name": "alpha_vrf",
        "routing_zone_type": "evpn",
        "sw_id": "sw01",
        "sw_label": "Leaf1",
        "sw_role": "leaf",
        "vni_id": "vni_sw01",
        "vni_vlan_id": 100,
        "vni_ipv4_enabled": True,
        "vni_ipv4_mode": "enabled",
        "vni_dhcp_enabled": False,
    },
    {
        "vn.id": "vn01",
        "vn.label": "App_Net",
        "vn.vn_type": "vxlan",
        "vn.vn_id": "10010",
        "vn.reserved_vlan_id": 100,
        "vn.ipv4_enabled": True,
        "vn.ipv4_subnet": "10.1.0.0/24",
        "vn.ipv6_enabled": False,
        "vn.ipv6_subnet": None,
        "vn.virtual_gateway_ipv4": "10.1.0.1",
        "vn.virtual_gateway_ipv4_enabled": True,
        "vn.virtual_gateway_ipv6": None,
        "vn.virtual_gateway_ipv6_enabled": False,
        "vn.virtual_mac": None,
        "vn.l3_mtu": 9000,
        "vn.description": "Application network",
        "vn.tags": None,
        "sz_id": "szA1",
        "routing_zone_label": "Alpha_VRF",
        "vrf_name": "alpha_vrf",
        "routing_zone_type": "evpn",
        "sw_id": "sw02",
        "sw_label": "Leaf2",
        "sw_role": "leaf",
        "vni_id": "vni_sw02",
        "vni_vlan_id": 101,
        "vni_ipv4_enabled": True,
        "vni_ipv4_mode": "enabled",
        "vni_dhcp_enabled": False,
    },
]


# ===========================================================================
# parse_routing_zones
# ===========================================================================

class TestParseRoutingZones:
    def test_strips_prefixes_and_maps_fields(self):
        result = parse_routing_zones(SZ_LIST_ROWS)
        assert result == PARSED_SZ_LIST

    def test_empty_rows(self):
        assert parse_routing_zones([]) == []

    def test_missing_fields_use_defaults(self):
        result = parse_routing_zones([{}])
        assert result[0]["id"] is None
        assert result[0]["label"] is None
        assert result[0]["vrf_name"] is None
        assert result[0]["sz_type"] is None
        assert result[0]["vn_count"] == 0

    def test_l3_fabric_sz_type(self):
        row = {"sz.id": "x", "sz.label": "Default", "sz.vrf_name": "default",
               "sz.sz_type": "l3_fabric", "vn_count": 0}
        result = parse_routing_zones([row])
        assert result[0]["sz_type"] == "l3_fabric"

    def test_evpn_sz_type(self):
        row = {"sz.id": "y", "sz.label": "Tenant", "sz.vrf_name": "tenant",
               "sz.sz_type": "evpn", "vn_count": 5}
        result = parse_routing_zones([row])
        assert result[0]["sz_type"] == "evpn"
        assert result[0]["vn_count"] == 5


# ===========================================================================
# parse_routing_zone_detail
# ===========================================================================

class TestParseRoutingZoneDetail:
    def test_returns_none_for_empty_rows(self):
        assert parse_routing_zone_detail([]) is None

    def test_zone_metadata(self):
        result = parse_routing_zone_detail(SZ_DETAIL_ROWS)
        assert result["id"] == "szA1"
        assert result["label"] == "Alpha_VRF"
        assert result["vrf_name"] == "alpha_vrf"
        assert result["sz_type"] == "evpn"

    def test_member_vns_deduplicated(self):
        result = parse_routing_zone_detail(SZ_DETAIL_ROWS)
        # vn01 appears in 2 rows (two switches) — should appear only once
        vn_ids = [v["vn_id"] for v in result["member_virtual_networks"]]
        assert len(vn_ids) == 2
        assert "vn01" in vn_ids
        assert "vn02" in vn_ids
        assert vn_ids.count("vn01") == 1

    def test_member_vns_sorted_by_label(self):
        result = parse_routing_zone_detail(SZ_DETAIL_ROWS)
        labels = [v["vn_label"] for v in result["member_virtual_networks"]]
        assert labels == sorted(labels)

    def test_member_systems_deduplicated(self):
        result = parse_routing_zone_detail(SZ_DETAIL_ROWS)
        # sw01 appears in 2 rows (two VNs) — should appear only once
        sw_ids = [s["sw_id"] for s in result["member_systems"]]
        assert len(sw_ids) == 2
        assert "sw01" in sw_ids
        assert "sw02" in sw_ids
        assert sw_ids.count("sw01") == 1

    def test_member_systems_sorted_by_label(self):
        result = parse_routing_zone_detail(SZ_DETAIL_ROWS)
        labels = [s["sw_label"] for s in result["member_systems"]]
        assert labels == sorted(labels)

    def test_vn_count_and_system_count(self):
        result = parse_routing_zone_detail(SZ_DETAIL_ROWS)
        assert result["vn_count"] == 2
        assert result["system_count"] == 2

    def test_vn_fields_populated(self):
        result = parse_routing_zone_detail(SZ_DETAIL_ROWS)
        app_net = next(v for v in result["member_virtual_networks"] if v["vn_id"] == "vn01")
        assert app_net["vn_label"] == "App_Net"
        assert app_net["vni_number"] == "10010"
        assert app_net["ipv4_subnet"] == "10.1.0.0/24"
        assert app_net["virtual_gateway_ipv4"] == "10.1.0.1"
        assert app_net["description"] == "Application network"

    def test_system_fields_populated(self):
        result = parse_routing_zone_detail(SZ_DETAIL_ROWS)
        leaf1 = next(s for s in result["member_systems"] if s["sw_id"] == "sw01")
        assert leaf1["sw_label"] == "Leaf1"
        assert leaf1["sw_role"] == "leaf"

    def test_null_sw_rows_ignored(self):
        """Rows with no switch (VN has no deployments) must not add None entries."""
        rows = [
            {
                "sz.id": "szA1", "sz.label": "Alpha_VRF",
                "sz.vrf_name": "alpha_vrf", "sz.sz_type": "evpn",
                "vn_id": "vn01", "vn_label": "App_Net",
                "vn_type": "vxlan", "vni_number": "10010",
                "vn_ipv4_enabled": True, "vn_ipv4_subnet": None,
                "vn_ipv6_enabled": False, "vn_ipv6_subnet": None,
                "vn_gw_ipv4": None, "vn_l3_mtu": None,
                "vn_description": None, "vn_tags": None,
                "sw_id": None, "sw_label": None, "sw_role": None,
                "vni_vlan_id": None,
            }
        ]
        result = parse_routing_zone_detail(rows)
        assert result["member_systems"] == []
        assert result["system_count"] == 0
        assert result["vn_count"] == 1

    def test_null_vn_rows_ignored(self):
        """Rows with no VN (empty routing zone) must not add None entries."""
        rows = [
            {
                "sz.id": "szA1", "sz.label": "Alpha_VRF",
                "sz.vrf_name": "alpha_vrf", "sz.sz_type": "evpn",
                "vn_id": None, "vn_label": None,
                "vn_type": None, "vni_number": None,
                "vn_ipv4_enabled": None, "vn_ipv4_subnet": None,
                "vn_ipv6_enabled": None, "vn_ipv6_subnet": None,
                "vn_gw_ipv4": None, "vn_l3_mtu": None,
                "vn_description": None, "vn_tags": None,
                "sw_id": None, "sw_label": None, "sw_role": None,
                "vni_vlan_id": None,
            }
        ]
        result = parse_routing_zone_detail(rows)
        assert result["member_virtual_networks"] == []
        assert result["vn_count"] == 0


# ===========================================================================
# parse_virtual_network_detail
# ===========================================================================

class TestParseVirtualNetworkDetail:
    def test_returns_none_for_empty_rows(self):
        assert parse_virtual_network_detail([]) is None

    def test_vn_fields(self):
        result = parse_virtual_network_detail(VN_DETAIL_ROWS)
        assert result["id"] == "vn01"
        assert result["label"] == "App_Net"
        assert result["vn_type"] == "vxlan"
        assert result["vni_number"] == "10010"
        assert result["reserved_vlan_id"] == 100
        assert result["ipv4_enabled"] is True
        assert result["ipv4_subnet"] == "10.1.0.0/24"
        assert result["ipv6_enabled"] is False
        assert result["ipv6_subnet"] is None
        assert result["virtual_gateway_ipv4"] == "10.1.0.1"
        assert result["virtual_gateway_ipv4_enabled"] is True
        assert result["virtual_gateway_ipv6"] is None
        assert result["virtual_gateway_ipv6_enabled"] is False
        assert result["virtual_mac"] is None
        assert result["l3_mtu"] == 9000
        assert result["description"] == "Application network"
        assert result["tags"] is None

    def test_routing_zone_populated(self):
        result = parse_virtual_network_detail(VN_DETAIL_ROWS)
        rz = result["routing_zone"]
        assert rz["sz_id"] == "szA1"
        assert rz["routing_zone_label"] == "Alpha_VRF"
        assert rz["vrf_name"] == "alpha_vrf"
        assert rz["routing_zone_type"] == "evpn"

    def test_routing_zone_null_when_unassigned(self):
        rows = [{**VN_DETAIL_ROWS[0], "sz_id": None,
                 "routing_zone_label": None, "vrf_name": None,
                 "routing_zone_type": None}]
        result = parse_virtual_network_detail(rows)
        assert result["routing_zone"] is None

    def test_deployed_on_deduplicated(self):
        result = parse_virtual_network_detail(VN_DETAIL_ROWS)
        sw_ids = [s["sw_id"] for s in result["deployed_on"]]
        assert len(sw_ids) == 2
        assert sw_ids.count("sw01") == 1
        assert sw_ids.count("sw02") == 1

    def test_deployed_on_sorted_by_label(self):
        result = parse_virtual_network_detail(VN_DETAIL_ROWS)
        labels = [s["sw_label"] for s in result["deployed_on"]]
        assert labels == sorted(labels)

    def test_deployed_count(self):
        result = parse_virtual_network_detail(VN_DETAIL_ROWS)
        assert result["deployed_count"] == 2

    def test_switch_fields_populated(self):
        result = parse_virtual_network_detail(VN_DETAIL_ROWS)
        sw = next(s for s in result["deployed_on"] if s["sw_id"] == "sw01")
        assert sw["sw_label"] == "Leaf1"
        assert sw["sw_role"] == "leaf"
        assert sw["vni_id"] == "vni_sw01"
        assert sw["vlan_id"] == 100
        assert sw["ipv4_enabled"] is True
        assert sw["ipv4_mode"] == "enabled"
        assert sw["dhcp_enabled"] is False

    def test_different_vlan_per_switch(self):
        result = parse_virtual_network_detail(VN_DETAIL_ROWS)
        vlans = {s["vlan_id"] for s in result["deployed_on"]}
        assert vlans == {100, 101}

    def test_null_sw_rows_ignored(self):
        rows = [{**VN_DETAIL_ROWS[0], "sw_id": None, "sw_label": None,
                 "sw_role": None, "vni_id": None, "vni_vlan_id": None,
                 "vni_ipv4_enabled": None, "vni_ipv4_mode": None,
                 "vni_dhcp_enabled": None}]
        result = parse_virtual_network_detail(rows)
        assert result["deployed_on"] == []
        assert result["deployed_count"] == 0

    def test_missing_boolean_fields_default_false(self):
        result = parse_virtual_network_detail([{}])
        assert result["ipv4_enabled"] is False
        assert result["ipv6_enabled"] is False
        assert result["virtual_gateway_ipv4_enabled"] is False
        assert result["virtual_gateway_ipv6_enabled"] is False


# ===========================================================================
# handle_get_routing_zones
# ===========================================================================

class TestHandleGetRoutingZones:
    async def test_single_session_returns_zones(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=SZ_LIST_ROWS)

        result = await handle_get_routing_zones([session], registry, "bp-001")

        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["routing_zones"] == PARSED_SZ_LIST
        assert result["count"] == 3

    async def test_single_session_registry_error(self):
        session = make_session("dc-primary")
        registry = make_registry(error=Exception("graph error"))

        result = await handle_get_routing_zones([session], registry, "bp-001")

        assert result["instance"] == "dc-primary"
        assert "error" in result
        assert result["routing_zones"] == []
        assert result["count"] == 0

    async def test_empty_blueprint(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=[])

        result = await handle_get_routing_zones([session], registry, "bp-001")

        assert result["count"] == 0
        assert result["routing_zones"] == []

    async def test_multiple_sessions_aggregated(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        registry = make_registry(rows=SZ_LIST_ROWS)

        result = await handle_get_routing_zones(sessions, registry, "bp-001")

        assert result["instance"] == "all"
        assert result["blueprint_id"] == "bp-001"
        assert result["total_count"] == 6
        assert len(result["results"]) == 2

    async def test_instance_name_filters_sessions(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        registry = make_registry(rows=SZ_LIST_ROWS)

        result = await handle_get_routing_zones(sessions, registry, "bp-001",
                                                instance_name="dc-secondary")

        assert result["instance"] == "dc-secondary"
        assert result["count"] == 3

    async def test_instance_name_all_treated_as_none(self):
        sessions = [make_session("a"), make_session("b")]
        registry = make_registry(rows=SZ_LIST_ROWS)

        result = await handle_get_routing_zones(sessions, registry, "bp-001",
                                                instance_name="all")

        assert result["instance"] == "all"


# ===========================================================================
# handle_get_routing_zone_detail
# ===========================================================================

class TestHandleGetRoutingZoneDetail:
    async def test_single_session_found(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=SZ_DETAIL_ROWS)

        result = await handle_get_routing_zone_detail(
            [session], registry, "bp-001", "Alpha_VRF"
        )

        assert result["instance"] == "dc-primary"
        assert result["routing_zone"] == "Alpha_VRF"
        assert result["detail"]["label"] == "Alpha_VRF"
        assert result["detail"]["vn_count"] == 2
        assert "error" not in result

    async def test_single_session_not_found(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=[])

        result = await handle_get_routing_zone_detail(
            [session], registry, "bp-001", "NoSuchZone"
        )

        assert result["detail"] is None
        assert "error" in result
        assert "not found" in result["error"]

    async def test_single_session_registry_error(self):
        session = make_session("dc-primary")
        registry = make_registry(error=Exception("graph error"))

        result = await handle_get_routing_zone_detail(
            [session], registry, "bp-001", "Alpha_VRF"
        )

        assert result["detail"] is None
        assert "error" in result

    async def test_query_uses_routing_zone_param(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=SZ_DETAIL_ROWS)

        await handle_get_routing_zone_detail(
            [session], registry, "bp-001", "alpha_vrf"
        )

        graph = registry.get_or_rebuild.return_value
        call_args = graph.query.call_args
        assert call_args[0][1] == {"routing_zone": "alpha_vrf"}

    async def test_multiple_sessions_aggregated(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        registry = make_registry(rows=SZ_DETAIL_ROWS)

        result = await handle_get_routing_zone_detail(
            sessions, registry, "bp-001", "Alpha_VRF"
        )

        assert result["instance"] == "all"
        assert len(result["results"]) == 2


# ===========================================================================
# handle_get_virtual_network_detail
# ===========================================================================

class TestHandleGetVirtualNetworkDetail:
    async def test_single_session_found(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=VN_DETAIL_ROWS)

        result = await handle_get_virtual_network_detail(
            [session], registry, "bp-001", "App_Net"
        )

        assert result["instance"] == "dc-primary"
        assert result["virtual_network"] == "App_Net"
        assert result["detail"]["label"] == "App_Net"
        assert result["detail"]["deployed_count"] == 2
        assert "error" not in result

    async def test_single_session_not_found(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=[])

        result = await handle_get_virtual_network_detail(
            [session], registry, "bp-001", "Ghost_Net"
        )

        assert result["detail"] is None
        assert "error" in result
        assert "not found" in result["error"]

    async def test_single_session_registry_error(self):
        session = make_session("dc-primary")
        registry = make_registry(error=Exception("graph error"))

        result = await handle_get_virtual_network_detail(
            [session], registry, "bp-001", "App_Net"
        )

        assert result["detail"] is None
        assert "error" in result

    async def test_query_uses_virtual_network_param(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=VN_DETAIL_ROWS)

        await handle_get_virtual_network_detail(
            [session], registry, "bp-001", "App_Net"
        )

        graph = registry.get_or_rebuild.return_value
        call_args = graph.query.call_args
        assert call_args[0][1] == {"virtual_network": "App_Net"}

    async def test_multiple_sessions_aggregated(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        registry = make_registry(rows=VN_DETAIL_ROWS)

        result = await handle_get_virtual_network_detail(
            sessions, registry, "bp-001", "App_Net"
        )

        assert result["instance"] == "all"
        assert len(result["results"]) == 2

    async def test_instance_name_filters_sessions(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        registry = make_registry(rows=VN_DETAIL_ROWS)

        result = await handle_get_virtual_network_detail(
            sessions, registry, "bp-001", "App_Net", instance_name="dc-primary"
        )

        assert result["instance"] == "dc-primary"
        assert result["detail"]["deployed_count"] == 2
