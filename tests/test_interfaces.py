import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from primitives.response_parser import parse_interfaces
from handlers.interfaces import handle_get_interface_list, _select_sessions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session(name="dc-primary"):
    session = MagicMock()
    session.name = name
    return session


def make_registry(rows=None, error=None):
    registry = MagicMock()
    if error:
        registry.get_or_rebuild = AsyncMock(side_effect=error)
        return registry
    graph = MagicMock()
    graph.query = MagicMock(return_value=rows or [])
    registry.get_or_rebuild = AsyncMock(return_value=graph)
    return registry


# ---------------------------------------------------------------------------
# Fixture data — raw Kuzu RETURN * rows
# ---------------------------------------------------------------------------

# Ethernet interface (no IP, no LAG, description encodes the peer)
_PAYLOAD_ETHERNET = {
    "description": "to.pod1-hpe-vme-essentials:eth1",
    "evpn_esi_mac": None,
    "id": "bs0wyWRD5Uj0n2qhbQ",
    "if_name": "ge-0/0/4",
    "if_type": "ethernet",
    "ipv4_addr": None,
    "ipv4_addr_type": None,
    "ipv4_enabled": None,
    "ipv6_addr": None,
    "ipv6_addr_type": None,
    "ipv6_enabled": None,
    "l3_mtu": None,
    "label": None,
    "lag_mode": None,
    "loopback_id": None,
    "mlag_id": None,
    "mode": None,
    "operation_state": "up",
    "po_control_protocol": None,
    "port_channel_id": None,
    "property_set": None,
    "protocols": None,
    "ref_count": None,
    "subintf_id": None,
    "tags": None,
    "type": "interface",
    "vlan_id": None,
}

# IP interface (routed, eBGP, l3 MTU, IPv4 address)
_PAYLOAD_IP = {
    "description": "facing_spine1:ge-0/0/2",
    "evpn_esi_mac": None,
    "id": "-n6oBB6CmLqpbPUO4g",
    "if_name": "ge-0/0/0",
    "if_type": "ip",
    "ipv4_addr": "192.168.0.5/31",
    "ipv4_addr_type": None,
    "ipv4_enabled": None,
    "ipv6_addr": None,
    "ipv6_addr_type": None,
    "ipv6_enabled": None,
    "l3_mtu": 9170,
    "label": None,
    "lag_mode": None,
    "loopback_id": None,
    "mlag_id": None,
    "mode": None,
    "operation_state": "up",
    "po_control_protocol": None,
    "port_channel_id": None,
    "property_set": None,
    "protocols": "ebgp",
    "ref_count": None,
    "subintf_id": None,
    "tags": None,
    "type": "interface",
    "vlan_id": None,
}

# Port-channel (LACP active, l3 MTU, description)
_PAYLOAD_LAG = {
    "description": "to.pod1-hpe-vme-essentials",
    "evpn_esi_mac": None,
    "id": "HktB226zrycFLUNpsA",
    "if_name": "ae1",
    "if_type": "port_channel",
    "ipv4_addr": None,
    "ipv4_addr_type": None,
    "ipv4_enabled": None,
    "ipv6_addr": None,
    "ipv6_addr_type": None,
    "ipv6_enabled": None,
    "l3_mtu": 9170,
    "label": None,
    "lag_mode": "lacp_active",
    "loopback_id": None,
    "mlag_id": None,
    "mode": None,
    "operation_state": "up",
    "po_control_protocol": None,
    "port_channel_id": 1,
    "property_set": None,
    "protocols": None,
    "ref_count": None,
    "subintf_id": None,
    "tags": None,
    "type": "interface",
    "vlan_id": None,
}

# Loopback (primary, IPv4 address)
_PAYLOAD_LOOPBACK = {
    "description": None,
    "evpn_esi_mac": None,
    "id": "_single_rack_001_leaf1_loopback",
    "if_name": "lo0.0",
    "if_type": "loopback",
    "ipv4_addr": "172.16.0.2/32",
    "ipv4_addr_type": None,
    "ipv4_enabled": None,
    "ipv6_addr": None,
    "ipv6_addr_type": None,
    "ipv6_enabled": None,
    "l3_mtu": None,
    "label": None,
    "lag_mode": None,
    "loopback_id": 0,
    "mlag_id": None,
    "mode": None,
    "operation_state": "up",
    "po_control_protocol": None,
    "port_channel_id": None,
    "property_set": None,
    "protocols": None,
    "ref_count": None,
    "subintf_id": None,
    "tags": None,
    "type": "interface",
    "vlan_id": None,
}

# VTEP (virtual, no if_name, no description)
_PAYLOAD_VTEP = {
    "description": None,
    "evpn_esi_mac": None,
    "id": "5dtCOujYwC8pDUBwKg",
    "if_name": None,
    "if_type": "unicast_vtep",
    "ipv4_addr": None,
    "ipv4_addr_type": None,
    "ipv4_enabled": None,
    "ipv6_addr": None,
    "ipv6_addr_type": None,
    "ipv6_enabled": None,
    "l3_mtu": None,
    "label": None,
    "lag_mode": None,
    "loopback_id": None,
    "mlag_id": None,
    "mode": None,
    "operation_state": "up",
    "po_control_protocol": None,
    "port_channel_id": None,
    "property_set": None,
    "protocols": None,
    "ref_count": None,
    "subintf_id": None,
    "tags": None,
    "type": "interface",
    "vlan_id": None,
}


def _make_row(payload_dict, **overrides):
    """
    Builds a raw Kuzu RETURN * row. Top-level node fields that Kuzu promotes
    are mirrored from the payload; overrides allow patching individual fields.
    """
    node = {
        "id": payload_dict["id"],
        "if_name": payload_dict.get("if_name"),
        "if_type": payload_dict["if_type"],
        "description": payload_dict.get("description"),
        "operation_state": payload_dict.get("operation_state", "up"),
        "ipv4_addr": payload_dict.get("ipv4_addr"),
        "l3_mtu": payload_dict.get("l3_mtu"),
        "lag_mode": payload_dict.get("lag_mode"),
        "port_channel_id": payload_dict.get("port_channel_id"),
        "loopback_id": payload_dict.get("loopback_id"),
        "protocols": payload_dict.get("protocols"),
        "payload": json.dumps(payload_dict),
        "label": "interface",
        "type": "interface",
        **overrides,
    }
    return {"intf": node}


RAW_ROWS = [
    _make_row(_PAYLOAD_ETHERNET),
    _make_row(_PAYLOAD_IP),
    _make_row(_PAYLOAD_LAG),
    _make_row(_PAYLOAD_LOOPBACK),
    _make_row(_PAYLOAD_VTEP),
]


# ---------------------------------------------------------------------------
# parse_interfaces
# ---------------------------------------------------------------------------

class TestParseInterfaces:

    def test_returns_correct_count(self):
        result = parse_interfaces(RAW_ROWS)
        assert len(result) == 5

    def test_empty_rows_returns_empty(self):
        assert parse_interfaces([]) == []

    def test_ethernet_interface_fields(self):
        result = parse_interfaces([_make_row(_PAYLOAD_ETHERNET)])
        r = result[0]
        assert r["id"] == "bs0wyWRD5Uj0n2qhbQ"
        assert r["if_name"] == "ge-0/0/4"
        assert r["if_type"] == "ethernet"
        assert r["description"] == "to.pod1-hpe-vme-essentials:eth1"
        assert r["operation_state"] == "up"
        assert r["ipv4_addr"] is None
        assert r["ipv6_addr"] is None
        assert r["l3_mtu"] is None
        assert r["lag_mode"] is None
        assert r["port_channel_id"] is None
        assert r["loopback_id"] is None
        assert r["protocols"] is None
        assert r["mode"] is None
        assert r["vlan_id"] is None

    def test_ip_interface_has_ipv4_and_protocol(self):
        result = parse_interfaces([_make_row(_PAYLOAD_IP)])
        r = result[0]
        assert r["if_name"] == "ge-0/0/0"
        assert r["if_type"] == "ip"
        assert r["ipv4_addr"] == "192.168.0.5/31"
        assert r["l3_mtu"] == 9170
        assert r["protocols"] == "ebgp"
        assert r["description"] == "facing_spine1:ge-0/0/2"

    def test_port_channel_lag_fields(self):
        result = parse_interfaces([_make_row(_PAYLOAD_LAG)])
        r = result[0]
        assert r["if_name"] == "ae1"
        assert r["if_type"] == "port_channel"
        assert r["lag_mode"] == "lacp_active"
        assert r["port_channel_id"] == 1
        assert r["l3_mtu"] == 9170

    def test_loopback_fields(self):
        result = parse_interfaces([_make_row(_PAYLOAD_LOOPBACK)])
        r = result[0]
        assert r["if_name"] == "lo0.0"
        assert r["if_type"] == "loopback"
        assert r["ipv4_addr"] == "172.16.0.2/32"
        assert r["loopback_id"] == 0
        assert r["description"] is None

    def test_vtep_has_no_if_name(self):
        result = parse_interfaces([_make_row(_PAYLOAD_VTEP)])
        r = result[0]
        assert r["if_name"] is None
        assert r["if_type"] == "unicast_vtep"
        assert r["description"] is None

    def test_payload_fallback_for_missing_top_level_field(self):
        # Build a row where ipv6_addr is ONLY in the payload (not promoted to top-level)
        payload = {**_PAYLOAD_IP, "ipv6_addr": "2001:db8::1/128"}
        row = _make_row(payload)
        # Ensure ipv6_addr is not in the top-level node (it won't be — _make_row
        # doesn't promote ipv6_addr to top-level by default, matching real Kuzu output)
        assert "ipv6_addr" not in row["intf"]
        result = parse_interfaces([row])
        # Should fall back to payload
        assert result[0]["ipv6_addr"] == "2001:db8::1/128"

    def test_top_level_takes_precedence_over_payload(self):
        # top-level says "up", payload says "admin_down"
        payload = {**_PAYLOAD_ETHERNET, "operation_state": "admin_down"}
        row = _make_row(payload, operation_state="up")
        result = parse_interfaces([row])
        assert result[0]["operation_state"] == "up"

    def test_null_node_handled_safely(self):
        result = parse_interfaces([{"intf": None}])
        assert result[0]["id"] is None
        assert result[0]["if_type"] is None

    def test_invalid_payload_json_falls_back_to_empty(self):
        row = {"intf": {"id": "x", "if_type": "ethernet", "payload": "bad-json"}}
        result = parse_interfaces([row])
        assert result[0]["id"] == "x"
        assert result[0]["if_type"] == "ethernet"
        assert result[0]["ipv4_addr"] is None

    def test_admin_down_operation_state(self):
        payload = {**_PAYLOAD_ETHERNET, "operation_state": "admin_down"}
        result = parse_interfaces([_make_row(payload)])
        assert result[0]["operation_state"] == "admin_down"

    def test_deduced_down_operation_state(self):
        payload = {**_PAYLOAD_ETHERNET, "operation_state": "deduced_down"}
        result = parse_interfaces([_make_row(payload)])
        assert result[0]["operation_state"] == "deduced_down"

    def test_vlan_id_extracted(self):
        payload = {**_PAYLOAD_ETHERNET, "if_type": "subinterface", "vlan_id": 100, "subintf_id": 1}
        result = parse_interfaces([_make_row(payload)])
        assert result[0]["vlan_id"] == 100

    def test_trunk_mode_extracted(self):
        payload = {**_PAYLOAD_ETHERNET, "mode": "trunk"}
        result = parse_interfaces([_make_row(payload)])
        assert result[0]["mode"] == "trunk"


# ---------------------------------------------------------------------------
# handle_get_interface_list — single instance
# ---------------------------------------------------------------------------

class TestHandleGetInterfaceListSingle:

    async def test_returns_flat_result_for_single_session(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=RAW_ROWS)
        result = await handle_get_interface_list(
            [session], registry, "bp-001", "525400AA7236"
        )
        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["system_id"] == "525400AA7236"
        assert result["count"] == 5

    async def test_interfaces_list_is_parsed(self):
        session = make_session()
        registry = make_registry(rows=RAW_ROWS)
        result = await handle_get_interface_list(
            [session], registry, "bp-001", "525400AA7236"
        )
        intf_types = {i["if_type"] for i in result["interfaces"]}
        assert "ethernet" in intf_types
        assert "ip" in intf_types
        assert "port_channel" in intf_types
        assert "loopback" in intf_types

    async def test_graph_queried_with_system_id_param(self):
        session = make_session()
        registry = make_registry(rows=[])
        await handle_get_interface_list([session], registry, "bp-001", "MYSERIAL")
        registry.get_or_rebuild.assert_called_once()
        # The graph object returned by get_or_rebuild had query called once
        graph = registry.get_or_rebuild.return_value
        graph.query.assert_called_once()
        # First positional arg to query is the Cypher string; second is params dict
        call_args = graph.query.call_args
        params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("params")
        assert params == {"system_id": "MYSERIAL"}

    async def test_empty_result_returns_zero_count(self):
        session = make_session()
        registry = make_registry(rows=[])
        result = await handle_get_interface_list(
            [session], registry, "bp-001", "SERIAL"
        )
        assert result["count"] == 0
        assert result["interfaces"] == []

    async def test_registry_error_returns_error_key(self):
        session = make_session()
        registry = make_registry(error=RuntimeError("graph unavailable"))
        result = await handle_get_interface_list(
            [session], registry, "bp-001", "SERIAL"
        )
        assert "error" in result
        assert result["interfaces"] == []
        assert result["count"] == 0

    async def test_instance_name_filter_selects_correct_session(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        registry = make_registry(rows=[])
        result = await handle_get_interface_list(
            [s1, s2], registry, "bp-001", "SERIAL", "dc-secondary"
        )
        assert result["instance"] == "dc-secondary"

    async def test_unknown_instance_name_raises(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=[])
        with pytest.raises(ValueError, match="No instance named"):
            await handle_get_interface_list(
                [session], registry, "bp-001", "SERIAL", "nonexistent"
            )


# ---------------------------------------------------------------------------
# handle_get_interface_list — multi instance
# ---------------------------------------------------------------------------

class TestHandleGetInterfaceListMulti:

    async def test_multi_instance_returns_all_wrapper(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        registry = make_registry(rows=RAW_ROWS)
        result = await handle_get_interface_list(
            [s1, s2], registry, "bp-001", "525400AA7236"
        )
        assert result["instance"] == "all"
        assert len(result["results"]) == 2
        assert result["total_count"] == 10  # 5 + 5

    async def test_multi_instance_preserves_blueprint_and_system_id(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        registry = make_registry(rows=[])
        result = await handle_get_interface_list(
            [s1, s2], registry, "bp-xyz", "MYSERIAL"
        )
        assert result["blueprint_id"] == "bp-xyz"
        assert result["system_id"] == "MYSERIAL"

    async def test_multi_instance_partial_error_still_returns_results(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")

        # First call succeeds, second call fails
        registry = MagicMock()
        graph_ok = MagicMock()
        graph_ok.query = MagicMock(return_value=RAW_ROWS)

        call_count = 0
        async def get_or_rebuild(session, bp_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return graph_ok
            raise RuntimeError("second instance failed")

        registry.get_or_rebuild = get_or_rebuild

        result = await handle_get_interface_list(
            [s1, s2], registry, "bp-001", "SERIAL"
        )
        assert result["instance"] == "all"
        ok_results = [r for r in result["results"] if "error" not in r]
        err_results = [r for r in result["results"] if "error" in r]
        assert len(ok_results) == 1
        assert len(err_results) == 1
        assert ok_results[0]["count"] == 5


# ---------------------------------------------------------------------------
# _select_sessions
# ---------------------------------------------------------------------------

class TestInterfaceSelectSessions:

    def test_none_returns_all(self):
        sessions = [make_session("a"), make_session("b")]
        assert _select_sessions(sessions, None) == sessions

    def test_named_returns_matching_session(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        assert _select_sessions([s1, s2], "dc-secondary") == [s2]

    def test_unknown_name_raises(self):
        sessions = [make_session("dc-primary")]
        with pytest.raises(ValueError, match="No instance named 'unknown'"):
            _select_sessions(sessions, "unknown")
