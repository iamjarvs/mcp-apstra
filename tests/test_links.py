import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from primitives.response_parser import parse_links
from handlers.links import handle_get_link_list, _select_sessions


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
# Fixture data — raw Kuzu RETURN local_intf, link, remote_intf rows
# ---------------------------------------------------------------------------

_INTF_PAYLOAD_SPINE_SIDE = {
    "description": "facing_leaf3:ge-0/0/0",
    "evpn_esi_mac": None,
    "id": "XTE5xJZIjI4taziMug",
    "if_name": "ge-0/0/2",
    "if_type": "ip",
    "ipv4_addr": "192.168.0.4/31",
    "ipv4_addr_type": None,
    "ipv4_enabled": None,
    "ipv6_addr": None,
    "ipv6_addr_type": None,
    "ipv6_enabled": None,
    "l3_mtu": 9170,
    "lag_mode": None,
    "loopback_id": None,
    "mode": None,
    "operation_state": "up",
    "port_channel_id": None,
    "protocols": "ebgp",
    "type": "interface",
}

_INTF_PAYLOAD_LEAF_SIDE = {
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
    "lag_mode": None,
    "loopback_id": None,
    "mode": None,
    "operation_state": "up",
    "port_channel_id": None,
    "protocols": "ebgp",
    "type": "interface",
}

_INTF_PAYLOAD_ACCESS_LOCAL = {
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
    "lag_mode": None,
    "loopback_id": None,
    "mode": None,
    "operation_state": "up",
    "port_channel_id": None,
    "protocols": None,
    "type": "interface",
}

_INTF_PAYLOAD_ACCESS_REMOTE = {
    "description": "facing_leaf3:ge-0/0/4",
    "evpn_esi_mac": None,
    "id": "BvoQ0y7du3ugQ8N8Aw",
    "if_name": "eth1",
    "if_type": "ethernet",
    "ipv4_addr": None,
    "ipv4_addr_type": None,
    "ipv4_enabled": None,
    "ipv6_addr": None,
    "ipv6_addr_type": None,
    "ipv6_enabled": None,
    "l3_mtu": None,
    "lag_mode": None,
    "loopback_id": None,
    "mode": None,
    "operation_state": "up",
    "port_channel_id": None,
    "protocols": None,
    "type": "interface",
}

_INTF_PAYLOAD_LAG_LOCAL = {
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
    "lag_mode": "lacp_active",
    "loopback_id": None,
    "mode": None,
    "operation_state": "up",
    "port_channel_id": 1,
    "protocols": None,
    "type": "interface",
}

_INTF_PAYLOAD_LAG_REMOTE = {
    "description": "facing_leaf3",
    "evpn_esi_mac": None,
    "id": "bLC-nuufFhOw_dlJVw",
    "if_name": None,
    "if_type": "port_channel",
    "ipv4_addr": None,
    "ipv4_addr_type": None,
    "ipv4_enabled": None,
    "ipv6_addr": None,
    "ipv6_addr_type": None,
    "ipv6_enabled": None,
    "l3_mtu": 9170,
    "lag_mode": "lacp_active",
    "loopback_id": None,
    "mode": None,
    "operation_state": "up",
    "port_channel_id": None,
    "protocols": None,
    "type": "interface",
}


def _make_intf_node(payload_dict, **overrides):
    """Builds a Kuzu interface node dict from a payload dict."""
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
        "protocols": payload_dict.get("protocols"),
        "payload": json.dumps(payload_dict),
        "label": "interface",
        "type": "interface",
        **overrides,
    }
    return node


def _make_link_node(
    link_id,
    link_type="ethernet",
    role="spine_leaf",
    speed="10G",
    deploy_mode="deploy",
    group_label=None,
):
    return {
        "id": link_id,
        "link_type": link_type,
        "role": role,
        "speed": speed,
        "deploy_mode": deploy_mode,
        "group_label": group_label,
        "label": "link",
        "type": "link",
    }


# Three representative link rows
ROW_SPINE_LEAF = {
    "local_intf": _make_intf_node(_INTF_PAYLOAD_SPINE_SIDE),
    "link": _make_link_node(
        "spine1<->_single_rack_001_leaf1[1]",
        link_type="ethernet",
        role="spine_leaf",
        speed="10G",
    ),
    "remote_intf": _make_intf_node(_INTF_PAYLOAD_LEAF_SIDE),
}

ROW_ACCESS_ETHERNET = {
    "local_intf": _make_intf_node(_INTF_PAYLOAD_ACCESS_LOCAL),
    "link": _make_link_node(
        "_single_rack_001_leaf1<->_single_rack_001_sys001(generic1_leaf1_c1010)[1]",
        link_type="ethernet",
        role="to_generic",
        speed="1G",
    ),
    "remote_intf": _make_intf_node(_INTF_PAYLOAD_ACCESS_REMOTE),
}

ROW_AGGREGATE = {
    "local_intf": _make_intf_node(_INTF_PAYLOAD_LAG_LOCAL),
    "link": _make_link_node(
        "_single_rack_001_leaf1<->_single_rack_001_sys001(generic1_leaf1_c1010)",
        link_type="aggregate_link",
        role="to_generic",
        speed=None,
    ),
    "remote_intf": _make_intf_node(_INTF_PAYLOAD_LAG_REMOTE),
}

RAW_ROWS = [ROW_SPINE_LEAF, ROW_ACCESS_ETHERNET, ROW_AGGREGATE]


# ---------------------------------------------------------------------------
# parse_links
# ---------------------------------------------------------------------------

class TestParseLinks:

    def test_returns_correct_count(self):
        result = parse_links(RAW_ROWS)
        assert len(result) == 3

    def test_empty_rows_returns_empty(self):
        assert parse_links([]) == []

    def test_link_id_from_link_node(self):
        result = parse_links([ROW_SPINE_LEAF])
        assert result[0]["link_id"] == "spine1<->_single_rack_001_leaf1[1]"

    def test_link_type(self):
        result = parse_links(RAW_ROWS)
        assert result[0]["link_type"] == "ethernet"
        assert result[2]["link_type"] == "aggregate_link"

    def test_role(self):
        result = parse_links([ROW_SPINE_LEAF])
        assert result[0]["role"] == "spine_leaf"

    def test_speed(self):
        result = parse_links(RAW_ROWS)
        assert result[0]["speed"] == "10G"
        assert result[1]["speed"] == "1G"

    def test_null_speed(self):
        result = parse_links([ROW_AGGREGATE])
        assert result[0]["speed"] is None

    def test_deploy_mode(self):
        result = parse_links([ROW_SPINE_LEAF])
        assert result[0]["deploy_mode"] == "deploy"

    def test_group_label_null_default(self):
        result = parse_links([ROW_SPINE_LEAF])
        assert result[0]["group_label"] is None

    def test_group_label_populated(self):
        row = {
            "local_intf": _make_intf_node(_INTF_PAYLOAD_ACCESS_LOCAL),
            "link": _make_link_node("some-link", group_label="server-rack-1"),
            "remote_intf": _make_intf_node(_INTF_PAYLOAD_ACCESS_REMOTE),
        }
        result = parse_links([row])
        assert result[0]["group_label"] == "server-rack-1"

    def test_local_interface_fields(self):
        result = parse_links([ROW_SPINE_LEAF])
        li = result[0]["local_interface"]
        assert li["id"] == "XTE5xJZIjI4taziMug"
        assert li["if_name"] == "ge-0/0/2"
        assert li["if_type"] == "ip"
        assert li["ipv4_addr"] == "192.168.0.4/31"
        assert li["description"] == "facing_leaf3:ge-0/0/0"
        assert li["operation_state"] == "up"

    def test_remote_interface_fields(self):
        result = parse_links([ROW_SPINE_LEAF])
        ri = result[0]["remote_interface"]
        assert ri["id"] == "-n6oBB6CmLqpbPUO4g"
        assert ri["if_name"] == "ge-0/0/0"
        assert ri["ipv4_addr"] == "192.168.0.5/31"

    def test_lag_interface_fields(self):
        result = parse_links([ROW_AGGREGATE])
        li = result[0]["local_interface"]
        assert li["if_type"] == "port_channel"
        assert li["lag_mode"] == "lacp_active"
        assert li["port_channel_id"] == 1

    def test_remote_lag_no_if_name(self):
        result = parse_links([ROW_AGGREGATE])
        ri = result[0]["remote_interface"]
        assert ri["if_name"] is None
        assert ri["lag_mode"] == "lacp_active"

    def test_null_link_node_handled_safely(self):
        row = {
            "local_intf": _make_intf_node(_INTF_PAYLOAD_ACCESS_LOCAL),
            "link": None,
            "remote_intf": _make_intf_node(_INTF_PAYLOAD_ACCESS_REMOTE),
        }
        result = parse_links([row])
        assert result[0]["link_id"] is None
        assert result[0]["link_type"] is None
        assert result[0]["speed"] is None

    def test_null_intf_nodes_handled_safely(self):
        row = {
            "local_intf": None,
            "link": _make_link_node("x"),
            "remote_intf": None,
        }
        result = parse_links([row])
        assert result[0]["local_interface"]["id"] is None
        assert result[0]["remote_interface"]["id"] is None

    def test_payload_fallback_for_interface_fields(self):
        # ipv6_addr is only in payload, not promoted to top-level node
        payload = {**_INTF_PAYLOAD_LEAF_SIDE, "ipv6_addr": "2001:db8::1/128"}
        # _make_intf_node never promotes ipv6_addr to top-level
        node = _make_intf_node(payload)
        assert "ipv6_addr" not in node  # confirm not promoted
        row = {
            "local_intf": node,
            "link": _make_link_node("test-link"),
            "remote_intf": _make_intf_node(_INTF_PAYLOAD_SPINE_SIDE),
        }
        result = parse_links([row])
        assert result[0]["local_interface"]["ipv6_addr"] == "2001:db8::1/128"

    def test_invalid_intf_payload_json_falls_back_gracefully(self):
        node = {**_make_intf_node(_INTF_PAYLOAD_LEAF_SIDE), "payload": "bad-json"}
        row = {
            "local_intf": node,
            "link": _make_link_node("test-link"),
            "remote_intf": _make_intf_node(_INTF_PAYLOAD_SPINE_SIDE),
        }
        result = parse_links([row])
        # Top-level fields still accessible
        assert result[0]["local_interface"]["id"] == "-n6oBB6CmLqpbPUO4g"
        assert result[0]["local_interface"]["ipv6_addr"] is None


# ---------------------------------------------------------------------------
# handle_get_link_list — single instance, per-system
# ---------------------------------------------------------------------------

class TestHandleGetLinkListBySystem:

    async def test_returns_flat_result_single_session(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=RAW_ROWS)
        result = await handle_get_link_list(
            [session], registry, "bp-001", "525400AA7236"
        )
        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["system_id"] == "525400AA7236"
        assert result["count"] == 3

    async def test_links_fully_parsed(self):
        session = make_session()
        registry = make_registry(rows=RAW_ROWS)
        result = await handle_get_link_list(
            [session], registry, "bp-001", "525400AA7236"
        )
        roles = {l["role"] for l in result["links"]}
        assert "spine_leaf" in roles
        assert "to_generic" in roles

    async def test_system_id_passed_as_query_param(self):
        session = make_session()
        registry = make_registry(rows=[])
        await handle_get_link_list([session], registry, "bp-001", "MYSERIAL")
        graph = registry.get_or_rebuild.return_value
        graph.query.assert_called_once()
        call_args = graph.query.call_args
        params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("params")
        assert params == {"system_id": "MYSERIAL"}

    async def test_empty_result_returns_zero_count(self):
        session = make_session()
        registry = make_registry(rows=[])
        result = await handle_get_link_list(
            [session], registry, "bp-001", "SERIAL"
        )
        assert result["count"] == 0
        assert result["links"] == []

    async def test_registry_error_returns_error_key(self):
        session = make_session()
        registry = make_registry(error=RuntimeError("graph down"))
        result = await handle_get_link_list(
            [session], registry, "bp-001", "SERIAL"
        )
        assert "error" in result
        assert result["links"] == []
        assert result["count"] == 0

    async def test_unknown_instance_name_raises(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=[])
        with pytest.raises(ValueError, match="No instance named"):
            await handle_get_link_list(
                [session], registry, "bp-001", "SERIAL", "nonexistent"
            )


# ---------------------------------------------------------------------------
# handle_get_link_list — fabric-wide (no system_id)
# ---------------------------------------------------------------------------

class TestHandleGetLinkListFabric:

    async def test_no_system_id_calls_all_query_without_params(self):
        session = make_session()
        registry = make_registry(rows=RAW_ROWS)
        result = await handle_get_link_list([session], registry, "bp-001")
        assert result["system_id"] is None
        assert result["count"] == 3
        # Query should be called with no params (or None)
        graph = registry.get_or_rebuild.return_value
        graph.query.assert_called_once()
        call_args = graph.query.call_args
        # No second positional arg and no params keyword
        assert len(call_args[0]) == 1 or (len(call_args[0]) > 1 and call_args[0][1] is None)

    async def test_fabric_wide_result_has_correct_instance(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=[])
        result = await handle_get_link_list([session], registry, "bp-001")
        assert result["instance"] == "dc-primary"


# ---------------------------------------------------------------------------
# handle_get_link_list — multi instance
# ---------------------------------------------------------------------------

class TestHandleGetLinkListMulti:

    async def test_multi_instance_returns_all_wrapper(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        registry = make_registry(rows=RAW_ROWS)
        result = await handle_get_link_list(
            [s1, s2], registry, "bp-001", "525400AA7236"
        )
        assert result["instance"] == "all"
        assert len(result["results"]) == 2
        assert result["total_count"] == 6  # 3 + 3

    async def test_multi_instance_preserves_blueprint_and_system_id(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        registry = make_registry(rows=[])
        result = await handle_get_link_list(
            [s1, s2], registry, "bp-xyz", "MYSERIAL"
        )
        assert result["blueprint_id"] == "bp-xyz"
        assert result["system_id"] == "MYSERIAL"

    async def test_multi_instance_partial_error(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")

        registry = MagicMock()
        graph_ok = MagicMock()
        graph_ok.query = MagicMock(return_value=RAW_ROWS)
        call_count = 0

        async def get_or_rebuild(session, bp_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return graph_ok
            raise RuntimeError("second instance unavailable")

        registry.get_or_rebuild = get_or_rebuild
        result = await handle_get_link_list(
            [s1, s2], registry, "bp-001", "SERIAL"
        )
        assert result["instance"] == "all"
        ok = [r for r in result["results"] if "error" not in r]
        err = [r for r in result["results"] if "error" in r]
        assert len(ok) == 1
        assert len(err) == 1
        assert ok[0]["count"] == 3


# ---------------------------------------------------------------------------
# _select_sessions
# ---------------------------------------------------------------------------

class TestLinksSelectSessions:

    def test_none_returns_all(self):
        sessions = [make_session("a"), make_session("b")]
        assert _select_sessions(sessions, None) == sessions

    def test_named_returns_matching(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        assert _select_sessions([s1, s2], "dc-secondary") == [s2]

    def test_unknown_name_raises(self):
        with pytest.raises(ValueError, match="No instance named 'nope'"):
            _select_sessions([make_session()], "nope")
