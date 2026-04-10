import pytest
from unittest.mock import AsyncMock, MagicMock

from primitives.response_parser import parse_external_peerings, parse_fabric_peerings
from handlers.bgp import handle_get_external_peerings, handle_get_fabric_peerings, _select_sessions


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
# Fixture data
# ---------------------------------------------------------------------------

# Raw Kuzu rows as returned by graph.query() when using explicit AS aliases.
# Keys are the alias names (no dotted prefix). Modelled on the example return
# data from the blueprint graph — two sessions on Leaf3.
RAW_ROWS = [
    {
        "session_id": "eGvaG_sDPPPoZZVACA",
        "bfd": False,
        "ipv4_safi": "enabled",
        "ipv6_safi": "disabled",
        "ttl": 2,
        "local_hostname": "Leaf3",
        "local_role": "leaf",
        "local_serial": "525400AA7236",
        "local_external": False,
        "local_interface": "ge-0/0/2",
        "local_intf_description": "to.dc2-leaf6",
        "local_subinterface": "ge-0/0/2.50",
        "local_ip": "172.16.100.1/24",
        "local_vlan_id": 50,
        "local_asn": None,
        "remote_hostname": "DC2-Leaf6",
        "remote_role": "generic",
        "remote_serial": None,
        "remote_external": True,
        "remote_interface": None,
        "remote_intf_description": "facing_leaf3:ge-0/0/2",
        "remote_subinterface": None,
        "remote_ip": "172.16.100.2/24",
        "remote_vlan_id": 50,
        "remote_asn": None,
    },
    {
        "session_id": "BWajMIKXOOlqxBgFlg",
        "bfd": False,
        "ipv4_safi": "enabled",
        "ipv6_safi": "disabled",
        "ttl": 2,
        "local_hostname": "Leaf3",
        "local_role": "leaf",
        "local_serial": "525400AA7236",
        "local_external": False,
        "local_interface": "ge-0/0/3",
        "local_intf_description": "to.dc2-leaf6",
        "local_subinterface": "ge-0/0/3.50",
        "local_ip": "172.16.200.1/24",
        "local_vlan_id": 50,
        "local_asn": None,
        "remote_hostname": "DC2-Leaf6",
        "remote_role": "generic",
        "remote_serial": None,
        "remote_external": True,
        "remote_interface": None,
        "remote_intf_description": "facing_leaf3:ge-0/0/3",
        "remote_subinterface": None,
        "remote_ip": "172.16.200.2/24",
        "remote_vlan_id": 50,
        "remote_asn": None,
    },
]

PARSED_SESSIONS = [
    {
        "session_id": "eGvaG_sDPPPoZZVACA",
        "bfd": False,
        "ipv4_safi": "enabled",
        "ipv6_safi": "disabled",
        "ttl": 2,
        "local": {
            "hostname": "Leaf3",
            "role": "leaf",
            "serial": "525400AA7236",
            "external": False,
            "interface": "ge-0/0/2",
            "interface_description": "to.dc2-leaf6",
            "subinterface": "ge-0/0/2.50",
            "ip_address": "172.16.100.1/24",
            "vlan_id": 50,
            "local_asn": None,
        },
        "remote": {
            "hostname": "DC2-Leaf6",
            "role": "generic",
            "serial": None,
            "external": True,
            "interface": None,
            "interface_description": "facing_leaf3:ge-0/0/2",
            "subinterface": None,
            "ip_address": "172.16.100.2/24",
            "vlan_id": 50,
            "local_asn": None,
        },
    },
    {
        "session_id": "BWajMIKXOOlqxBgFlg",
        "bfd": False,
        "ipv4_safi": "enabled",
        "ipv6_safi": "disabled",
        "ttl": 2,
        "local": {
            "hostname": "Leaf3",
            "role": "leaf",
            "serial": "525400AA7236",
            "external": False,
            "interface": "ge-0/0/3",
            "interface_description": "to.dc2-leaf6",
            "subinterface": "ge-0/0/3.50",
            "ip_address": "172.16.200.1/24",
            "vlan_id": 50,
            "local_asn": None,
        },
        "remote": {
            "hostname": "DC2-Leaf6",
            "role": "generic",
            "serial": None,
            "external": True,
            "interface": None,
            "interface_description": "facing_leaf3:ge-0/0/3",
            "subinterface": None,
            "ip_address": "172.16.200.2/24",
            "vlan_id": 50,
            "local_asn": None,
        },
    },
]


# ---------------------------------------------------------------------------
# parse_external_peerings
# ---------------------------------------------------------------------------

class TestParseExternalPeerings:

    def test_returns_correct_session_count(self):
        result = parse_external_peerings(RAW_ROWS)
        assert len(result) == 2

    def test_empty_rows_returns_empty_list(self):
        assert parse_external_peerings([]) == []

    def test_session_top_level_fields(self):
        result = parse_external_peerings(RAW_ROWS)
        s = result[0]
        assert s["session_id"] == "eGvaG_sDPPPoZZVACA"
        assert s["bfd"] is False
        assert s["ipv4_safi"] == "enabled"
        assert s["ipv6_safi"] == "disabled"
        assert s["ttl"] == 2

    def test_local_peer_fields(self):
        result = parse_external_peerings(RAW_ROWS)
        local = result[0]["local"]
        assert local["hostname"] == "Leaf3"
        assert local["role"] == "leaf"
        assert local["serial"] == "525400AA7236"
        assert local["external"] is False
        assert local["interface"] == "ge-0/0/2"
        assert local["interface_description"] == "to.dc2-leaf6"
        assert local["subinterface"] == "ge-0/0/2.50"
        assert local["ip_address"] == "172.16.100.1/24"
        assert local["vlan_id"] == 50
        assert local["local_asn"] is None

    def test_remote_peer_fields(self):
        result = parse_external_peerings(RAW_ROWS)
        remote = result[0]["remote"]
        assert remote["hostname"] == "DC2-Leaf6"
        assert remote["role"] == "generic"
        assert remote["serial"] is None
        assert remote["external"] is True
        assert remote["interface"] is None  # unmanaged peer has no if_name
        assert remote["interface_description"] == "facing_leaf3:ge-0/0/2"
        assert remote["subinterface"] is None
        assert remote["ip_address"] == "172.16.100.2/24"

    def test_parsed_output_matches_fixture(self):
        result = parse_external_peerings(RAW_ROWS)
        assert result == PARSED_SESSIONS

    def test_null_fields_preserved_as_none(self):
        row = {k: None for k in RAW_ROWS[0]}
        result = parse_external_peerings([row])
        assert result[0]["session_id"] is None
        assert result[0]["local"]["hostname"] is None
        assert result[0]["remote"]["hostname"] is None


# ---------------------------------------------------------------------------
# handle_get_bgp_sessions — single instance
# ---------------------------------------------------------------------------

class TestHandleGetExternalPeeringsSingle:

    async def test_returns_single_instance_result(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=RAW_ROWS)
        result = await handle_get_external_peerings([session], registry, "bp-001")
        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["device"] is None
        assert result["count"] == 2

    async def test_peerings_are_parsed(self):
        session = make_session()
        registry = make_registry(rows=RAW_ROWS)
        result = await handle_get_external_peerings([session], registry, "bp-001")
        assert result["peerings"] == PARSED_SESSIONS

    async def test_device_filter_passed_to_query(self):
        session = make_session()
        graph = MagicMock()
        graph.query = MagicMock(return_value=RAW_ROWS)
        registry = MagicMock()
        registry.get_or_rebuild = AsyncMock(return_value=graph)

        await handle_get_external_peerings([session], registry, "bp-001", device="Leaf3")

        graph.query.assert_called_once()
        call_args = graph.query.call_args
        assert call_args[0][1] == {"device": "Leaf3"}

    async def test_no_device_filter_uses_all_query(self):
        session = make_session()
        graph = MagicMock()
        graph.query = MagicMock(return_value=[])
        registry = MagicMock()
        registry.get_or_rebuild = AsyncMock(return_value=graph)

        await handle_get_external_peerings([session], registry, "bp-001")

        graph.query.assert_called_once()
        call_args = graph.query.call_args
        # All-peerings query takes no parameters
        assert len(call_args[0]) == 1

    async def test_empty_results_on_graph_error(self):
        session = make_session()
        registry = make_registry(error=RuntimeError("graph failure"))
        result = await handle_get_external_peerings([session], registry, "bp-001")
        assert result["peerings"] == []
        assert result["count"] == 0
        assert "error" in result

    async def test_error_message_captured(self):
        session = make_session()
        registry = make_registry(error=RuntimeError("kuzu exploded"))
        result = await handle_get_external_peerings([session], registry, "bp-001")
        assert "kuzu exploded" in result["error"]


# ---------------------------------------------------------------------------
# handle_get_bgp_sessions — multiple instances
# ---------------------------------------------------------------------------

class TestHandleGetExternalPeeringsMulti:

    async def test_two_instances_returns_all_wrapper(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        registry = make_registry(rows=RAW_ROWS)
        result = await handle_get_external_peerings([s1, s2], registry, "bp-001")
        assert result["instance"] == "all"
        assert result["total_count"] == 4
        assert len(result["results"]) == 2

    async def test_total_count_sums_across_instances(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        registry = make_registry(rows=RAW_ROWS)
        result = await handle_get_external_peerings([s1, s2], registry, "bp-001")
        assert result["total_count"] == sum(
            r["count"] for r in result["results"]
        )

    async def test_partial_error_still_returns_other_results(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")

        graph_ok = MagicMock()
        graph_ok.query = MagicMock(return_value=RAW_ROWS)

        registry = MagicMock()
        registry.get_or_rebuild = AsyncMock(
            side_effect=[graph_ok, RuntimeError("dc-secondary unreachable")]
        )

        result = await handle_get_external_peerings([s1, s2], registry, "bp-001")
        assert result["instance"] == "all"
        primary = next(r for r in result["results"] if r["instance"] == "dc-primary")
        secondary = next(r for r in result["results"] if r["instance"] == "dc-secondary")
        assert primary["count"] == 2
        assert secondary["count"] == 0
        assert "error" in secondary


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


# ---------------------------------------------------------------------------
# Fixture data — fabric peerings
# ---------------------------------------------------------------------------

# Raw Kuzu rows as returned by graph.query() for RETURN * queries.
# Each value is a node object dict (variable name is the column key).
# Based on the sample response for the Leaf2 ↔ Spine1/Spine2 fabric links.
RAW_FABRIC_ROWS = [
    {
        "sy_a": {
            "hostname": "Leaf2",
            "role": "leaf",
            "system_id": "525400708DCE",
            "external": False,
        },
        "int_a": {
            "if_name": "ge-0/0/0",
            "ipv4_addr": "192.168.0.3/31",
            "description": "facing_spine1:ge-0/0/5",
            "l3_mtu": 9170,
        },
        "link": {
            "id": "spine1<->_esi_rack_001_leaf2[1]",
            "role": "spine_leaf",
            "speed": "1G",
        },
        "int_b": {
            "if_name": "ge-0/0/5",
            "ipv4_addr": "192.168.0.2/31",
            "description": "facing_leaf2:ge-0/0/0",
            "l3_mtu": 9170,
        },
        "sy_b": {
            "hostname": "Spine1",
            "role": "spine",
            "system_id": "525400F8CE53",
            "external": False,
        },
        "asn_a": {"domain_id": "64515"},
        "asn_b": {"domain_id": "64512"},
    },
    {
        "sy_a": {
            "hostname": "Leaf2",
            "role": "leaf",
            "system_id": "525400708DCE",
            "external": False,
        },
        "int_a": {
            "if_name": "ge-0/0/1",
            "ipv4_addr": "192.168.0.9/31",
            "description": "facing_spine2:ge-0/0/1",
            "l3_mtu": 9170,
        },
        "link": {
            "id": "spine2<->_esi_rack_001_leaf2[1]",
            "role": "spine_leaf",
            "speed": "1G",
        },
        "int_b": {
            "if_name": "ge-0/0/1",
            "ipv4_addr": "192.168.0.8/31",
            "description": "facing_leaf2:ge-0/0/1",
            "l3_mtu": 9170,
        },
        "sy_b": {
            "hostname": "Spine2",
            "role": "spine",
            "system_id": "5254002D005F",
            "external": False,
        },
        "asn_a": {"domain_id": "64515"},
        "asn_b": {"domain_id": "64513"},
    },
]

PARSED_FABRIC_PEERINGS = [
    {
        "link_id": "spine1<->_esi_rack_001_leaf2[1]",
        "link_role": "spine_leaf",
        "link_speed": "1G",
        "a_side": {
            "hostname": "Leaf2",
            "role": "leaf",
            "serial": "525400708DCE",
            "asn": "64515",
            "interface": "ge-0/0/0",
            "description": "facing_spine1:ge-0/0/5",
            "ip_address": "192.168.0.3/31",
            "l3_mtu": 9170,
        },
        "b_side": {
            "hostname": "Spine1",
            "role": "spine",
            "serial": "525400F8CE53",
            "asn": "64512",
            "interface": "ge-0/0/5",
            "description": "facing_leaf2:ge-0/0/0",
            "ip_address": "192.168.0.2/31",
            "l3_mtu": 9170,
        },
    },
    {
        "link_id": "spine2<->_esi_rack_001_leaf2[1]",
        "link_role": "spine_leaf",
        "link_speed": "1G",
        "a_side": {
            "hostname": "Leaf2",
            "role": "leaf",
            "serial": "525400708DCE",
            "asn": "64515",
            "interface": "ge-0/0/1",
            "description": "facing_spine2:ge-0/0/1",
            "ip_address": "192.168.0.9/31",
            "l3_mtu": 9170,
        },
        "b_side": {
            "hostname": "Spine2",
            "role": "spine",
            "serial": "5254002D005F",
            "asn": "64513",
            "interface": "ge-0/0/1",
            "description": "facing_leaf2:ge-0/0/1",
            "ip_address": "192.168.0.8/31",
            "l3_mtu": 9170,
        },
    },
]


# ---------------------------------------------------------------------------
# parse_fabric_peerings
# ---------------------------------------------------------------------------

class TestParseFabricPeerings:

    def test_returns_correct_count(self):
        result = parse_fabric_peerings(RAW_FABRIC_ROWS)
        assert len(result) == 2

    def test_empty_rows_returns_empty_list(self):
        assert parse_fabric_peerings([]) == []

    def test_link_fields(self):
        result = parse_fabric_peerings(RAW_FABRIC_ROWS)
        p = result[0]
        assert p["link_id"] == "spine1<->_esi_rack_001_leaf2[1]"
        assert p["link_role"] == "spine_leaf"
        assert p["link_speed"] == "1G"

    def test_a_side_fields(self):
        result = parse_fabric_peerings(RAW_FABRIC_ROWS)
        a = result[0]["a_side"]
        assert a["hostname"] == "Leaf2"
        assert a["role"] == "leaf"
        assert a["serial"] == "525400708DCE"
        assert a["asn"] == "64515"
        assert a["interface"] == "ge-0/0/0"
        assert a["description"] == "facing_spine1:ge-0/0/5"
        assert a["ip_address"] == "192.168.0.3/31"
        assert a["l3_mtu"] == 9170

    def test_b_side_fields(self):
        result = parse_fabric_peerings(RAW_FABRIC_ROWS)
        b = result[0]["b_side"]
        assert b["hostname"] == "Spine1"
        assert b["role"] == "spine"
        assert b["serial"] == "525400F8CE53"
        assert b["asn"] == "64512"
        assert b["interface"] == "ge-0/0/5"
        assert b["description"] == "facing_leaf2:ge-0/0/0"
        assert b["ip_address"] == "192.168.0.2/31"
        assert b["l3_mtu"] == 9170

    def test_parsed_output_matches_fixture(self):
        result = parse_fabric_peerings(RAW_FABRIC_ROWS)
        assert result == PARSED_FABRIC_PEERINGS

    def test_null_node_dicts_handled_safely(self):
        row = {"sy_a": None, "sy_b": None, "int_a": None, "int_b": None,
               "link": None, "asn_a": None, "asn_b": None}
        result = parse_fabric_peerings([row])
        assert result[0]["link_id"] is None
        assert result[0]["a_side"]["hostname"] is None
        assert result[0]["b_side"]["hostname"] is None


# ---------------------------------------------------------------------------
# handle_get_fabric_peerings — single instance
# ---------------------------------------------------------------------------

class TestHandleGetFabricPeeringsSingle:

    async def test_returns_single_instance_result(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=RAW_FABRIC_ROWS)
        result = await handle_get_fabric_peerings([session], registry, "bp-001")
        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["device"] is None
        assert result["count"] == 2

    async def test_peerings_are_parsed(self):
        session = make_session()
        registry = make_registry(rows=RAW_FABRIC_ROWS)
        result = await handle_get_fabric_peerings([session], registry, "bp-001")
        assert result["peerings"] == PARSED_FABRIC_PEERINGS

    async def test_device_filter_passed_to_query(self):
        session = make_session()
        graph = MagicMock()
        graph.query = MagicMock(return_value=RAW_FABRIC_ROWS)
        registry = MagicMock()
        registry.get_or_rebuild = AsyncMock(return_value=graph)

        await handle_get_fabric_peerings([session], registry, "bp-001", device="Leaf2")

        graph.query.assert_called_once()
        call_args = graph.query.call_args
        assert call_args[0][1] == {"device": "Leaf2"}

    async def test_no_device_filter_uses_all_query(self):
        session = make_session()
        graph = MagicMock()
        graph.query = MagicMock(return_value=[])
        registry = MagicMock()
        registry.get_or_rebuild = AsyncMock(return_value=graph)

        await handle_get_fabric_peerings([session], registry, "bp-001")

        graph.query.assert_called_once()
        call_args = graph.query.call_args
        assert len(call_args[0]) == 1

    async def test_empty_results_on_graph_error(self):
        session = make_session()
        registry = make_registry(error=RuntimeError("graph failure"))
        result = await handle_get_fabric_peerings([session], registry, "bp-001")
        assert result["peerings"] == []
        assert result["count"] == 0
        assert "error" in result

    async def test_error_message_captured(self):
        session = make_session()
        registry = make_registry(error=RuntimeError("kuzu exploded"))
        result = await handle_get_fabric_peerings([session], registry, "bp-001")
        assert "kuzu exploded" in result["error"]


# ---------------------------------------------------------------------------
# handle_get_fabric_peerings — multiple instances
# ---------------------------------------------------------------------------

class TestHandleGetFabricPeeringsMulti:

    async def test_two_instances_returns_all_wrapper(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        registry = make_registry(rows=RAW_FABRIC_ROWS)
        result = await handle_get_fabric_peerings([s1, s2], registry, "bp-001")
        assert result["instance"] == "all"
        assert result["total_count"] == 4
        assert len(result["results"]) == 2

    async def test_total_count_sums_across_instances(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        registry = make_registry(rows=RAW_FABRIC_ROWS)
        result = await handle_get_fabric_peerings([s1, s2], registry, "bp-001")
        assert result["total_count"] == sum(
            r["count"] for r in result["results"]
        )

    async def test_partial_error_still_returns_other_results(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")

        graph_ok = MagicMock()
        graph_ok.query = MagicMock(return_value=RAW_FABRIC_ROWS)

        registry = MagicMock()
        registry.get_or_rebuild = AsyncMock(
            side_effect=[graph_ok, RuntimeError("dc-secondary unreachable")]
        )

        result = await handle_get_fabric_peerings([s1, s2], registry, "bp-001")
        assert result["instance"] == "all"
        primary = next(r for r in result["results"] if r["instance"] == "dc-primary")
        secondary = next(r for r in result["results"] if r["instance"] == "dc-secondary")
        assert primary["count"] == 2
        assert secondary["count"] == 0
        assert "error" in secondary
