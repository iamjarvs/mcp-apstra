import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from primitives.response_parser import parse_configlets, parse_property_sets
from handlers.blueprint_policy import (
    handle_get_configlets,
    handle_get_property_sets,
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
# Fixture data — configlets
# ---------------------------------------------------------------------------

_CONFIGLET_PAYLOAD_1 = {
    "condition": 'role in ["spine", "leaf"]',
    "configlet": {
        "display_name": "Example SNMPv2 configlet for flow-data interface enrichment",
        "generators": [
            {
                "config_style": "junos",
                "filename": "",
                "negation_template_text": "",
                "render_style": "standard",
                "section": "system",
                "section_condition": None,
                "template_text": "snmp {\n    community {{ snmpv2_community }};\n}",
            }
        ],
    },
    "id": "a2tHL8OAq5xtt33fTw",
    "label": "Example SNMPv2 configlet for flow-data interface enrichment",
    "type": "configlet",
}

_CONFIGLET_PAYLOAD_2 = {
    "condition": 'id in ["9VaRIyqtOJwRHDKnpA"]',
    "configlet": {
        "display_name": "JUNOS_Groups",
        "generators": [
            {
                "config_style": "junos",
                "filename": "",
                "negation_template_text": "",
                "render_style": "standard",
                "section": "set_based_system",
                "section_condition": None,
                "template_text": "set groups jei_disable_ports interfaces ge-0/0/4 disable",
            }
        ],
    },
    "id": "9h-aLapPOEibbvfSYA",
    "label": "JUNOS_Groups",
    "type": "configlet",
}

RAW_CONFIGLET_ROWS = [
    {
        "configlet": {
            "id": "a2tHL8OAq5xtt33fTw",
            "condition": 'role in ["spine", "leaf"]',
            "payload": json.dumps(_CONFIGLET_PAYLOAD_1),
            "type": "configlet",
        }
    },
    {
        "configlet": {
            "id": "9h-aLapPOEibbvfSYA",
            "condition": 'id in ["9VaRIyqtOJwRHDKnpA"]',
            "payload": json.dumps(_CONFIGLET_PAYLOAD_2),
            "type": "configlet",
        }
    },
]

PARSED_CONFIGLETS = [
    {
        "id": "a2tHL8OAq5xtt33fTw",
        "display_name": "Example SNMPv2 configlet for flow-data interface enrichment",
        "condition": 'role in ["spine", "leaf"]',
        "generators": _CONFIGLET_PAYLOAD_1["configlet"]["generators"],
    },
    {
        "id": "9h-aLapPOEibbvfSYA",
        "display_name": "JUNOS_Groups",
        "condition": 'id in ["9VaRIyqtOJwRHDKnpA"]',
        "generators": _CONFIGLET_PAYLOAD_2["configlet"]["generators"],
    },
]


# ---------------------------------------------------------------------------
# Fixture data — property sets
# ---------------------------------------------------------------------------

_PROPSET_PAYLOAD_1 = {
    "id": "KUSFB--TMtIw7ajL3g",
    "label": "Flow Data For Optional Flow Analytics",
    "property_set_id": "flow_data",
    "stale": False,
    "type": "property_set",
    "values": {"collector_ip": "10.28.173.6"},
}

_PROPSET_PAYLOAD_2 = {
    "id": "DbIIZEVSDGWRDBagkQ",
    "label": "Example SNMPv2 property-set for flow-data interface name enrichment",
    "property_set_id": "flow_snmpv2",
    "stale": False,
    "type": "property_set",
    "values": {"snmpv2_community": "public"},
}

RAW_PROPSET_ROWS = [
    {
        "propset": {
            "id": "KUSFB--TMtIw7ajL3g",
            "property_set_id": "flow_data",
            "stale": False,
            "payload": json.dumps(_PROPSET_PAYLOAD_1),
            "type": "property_set",
        }
    },
    {
        "propset": {
            "id": "DbIIZEVSDGWRDBagkQ",
            "property_set_id": "flow_snmpv2",
            "stale": False,
            "payload": json.dumps(_PROPSET_PAYLOAD_2),
            "type": "property_set",
        }
    },
]

PARSED_PROPERTY_SETS = [
    {
        "id": "KUSFB--TMtIw7ajL3g",
        "display_name": "Flow Data For Optional Flow Analytics",
        "property_set_id": "flow_data",
        "stale": False,
        "values": {"collector_ip": "10.28.173.6"},
    },
    {
        "id": "DbIIZEVSDGWRDBagkQ",
        "display_name": "Example SNMPv2 property-set for flow-data interface name enrichment",
        "property_set_id": "flow_snmpv2",
        "stale": False,
        "values": {"snmpv2_community": "public"},
    },
]


# ---------------------------------------------------------------------------
# parse_configlets
# ---------------------------------------------------------------------------

class TestParseConfiglets:

    def test_returns_correct_count(self):
        result = parse_configlets(RAW_CONFIGLET_ROWS)
        assert len(result) == 2

    def test_empty_rows_returns_empty_list(self):
        assert parse_configlets([]) == []

    def test_id_and_condition(self):
        result = parse_configlets(RAW_CONFIGLET_ROWS)
        assert result[0]["id"] == "a2tHL8OAq5xtt33fTw"
        assert result[0]["condition"] == 'role in ["spine", "leaf"]'

    def test_display_name_from_payload(self):
        result = parse_configlets(RAW_CONFIGLET_ROWS)
        assert result[0]["display_name"] == (
            "Example SNMPv2 configlet for flow-data interface enrichment"
        )

    def test_generators_present(self):
        result = parse_configlets(RAW_CONFIGLET_ROWS)
        gens = result[0]["generators"]
        assert isinstance(gens, list)
        assert len(gens) == 1
        assert gens[0]["config_style"] == "junos"
        assert gens[0]["section"] == "system"
        assert "template_text" in gens[0]

    def test_set_based_section(self):
        result = parse_configlets(RAW_CONFIGLET_ROWS)
        assert result[1]["generators"][0]["section"] == "set_based_system"

    def test_parsed_output_matches_fixture(self):
        result = parse_configlets(RAW_CONFIGLET_ROWS)
        assert result == PARSED_CONFIGLETS

    def test_null_node_handled_safely(self):
        result = parse_configlets([{"configlet": None}])
        assert result[0]["id"] is None
        assert result[0]["generators"] == []

    def test_invalid_payload_json_produces_empty_generators(self):
        row = {"configlet": {"id": "x", "condition": "true", "payload": "not-json"}}
        result = parse_configlets([row])
        assert result[0]["generators"] == []
        assert result[0]["display_name"] is None


# ---------------------------------------------------------------------------
# parse_property_sets
# ---------------------------------------------------------------------------

class TestParsePropertySets:

    def test_returns_correct_count(self):
        result = parse_property_sets(RAW_PROPSET_ROWS)
        assert len(result) == 2

    def test_empty_rows_returns_empty_list(self):
        assert parse_property_sets([]) == []

    def test_id_and_property_set_id(self):
        result = parse_property_sets(RAW_PROPSET_ROWS)
        assert result[0]["id"] == "KUSFB--TMtIw7ajL3g"
        assert result[0]["property_set_id"] == "flow_data"

    def test_display_name_from_payload_label(self):
        result = parse_property_sets(RAW_PROPSET_ROWS)
        assert result[0]["display_name"] == "Flow Data For Optional Flow Analytics"

    def test_values_dict(self):
        result = parse_property_sets(RAW_PROPSET_ROWS)
        assert result[0]["values"] == {"collector_ip": "10.28.173.6"}
        assert result[1]["values"] == {"snmpv2_community": "public"}

    def test_stale_flag(self):
        result = parse_property_sets(RAW_PROPSET_ROWS)
        assert result[0]["stale"] is False

    def test_parsed_output_matches_fixture(self):
        result = parse_property_sets(RAW_PROPSET_ROWS)
        assert result == PARSED_PROPERTY_SETS

    def test_null_node_handled_safely(self):
        result = parse_property_sets([{"propset": None}])
        assert result[0]["id"] is None
        assert result[0]["values"] == {}

    def test_invalid_payload_json_produces_empty_values(self):
        row = {"propset": {"id": "x", "property_set_id": "y", "payload": "bad"}}
        result = parse_property_sets([row])
        assert result[0]["values"] == {}
        assert result[0]["display_name"] is None


# ---------------------------------------------------------------------------
# handle_get_configlets — single instance
# ---------------------------------------------------------------------------

class TestHandleGetConfigletsSingle:

    async def test_returns_single_instance_result(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=RAW_CONFIGLET_ROWS)
        result = await handle_get_configlets([session], registry, "bp-001")
        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["count"] == 2

    async def test_configlets_are_parsed(self):
        session = make_session()
        registry = make_registry(rows=RAW_CONFIGLET_ROWS)
        result = await handle_get_configlets([session], registry, "bp-001")
        assert result["configlets"] == PARSED_CONFIGLETS

    async def test_empty_blueprint_returns_empty_list(self):
        session = make_session()
        registry = make_registry(rows=[])
        result = await handle_get_configlets([session], registry, "bp-001")
        assert result["configlets"] == []
        assert result["count"] == 0

    async def test_graph_error_returns_error_dict(self):
        session = make_session()
        registry = make_registry(error=RuntimeError("graph failure"))
        result = await handle_get_configlets([session], registry, "bp-001")
        assert result["configlets"] == []
        assert result["count"] == 0
        assert "error" in result

    async def test_error_message_captured(self):
        session = make_session()
        registry = make_registry(error=RuntimeError("kuzu exploded"))
        result = await handle_get_configlets([session], registry, "bp-001")
        assert "kuzu exploded" in result["error"]


# ---------------------------------------------------------------------------
# handle_get_configlets — multiple instances
# ---------------------------------------------------------------------------

class TestHandleGetConfigletsMulti:

    async def test_two_instances_returns_all_wrapper(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        registry = make_registry(rows=RAW_CONFIGLET_ROWS)
        result = await handle_get_configlets([s1, s2], registry, "bp-001")
        assert result["instance"] == "all"
        assert result["total_count"] == 4
        assert len(result["results"]) == 2

    async def test_partial_error_still_returns_other_results(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        graph_ok = MagicMock()
        graph_ok.query = MagicMock(return_value=RAW_CONFIGLET_ROWS)
        registry = MagicMock()
        registry.get_or_rebuild = AsyncMock(
            side_effect=[graph_ok, RuntimeError("dc-secondary unreachable")]
        )
        result = await handle_get_configlets([s1, s2], registry, "bp-001")
        primary = next(r for r in result["results"] if r["instance"] == "dc-primary")
        secondary = next(r for r in result["results"] if r["instance"] == "dc-secondary")
        assert primary["count"] == 2
        assert "error" in secondary


# ---------------------------------------------------------------------------
# handle_get_property_sets — single instance
# ---------------------------------------------------------------------------

class TestHandleGetPropertySetsSingle:

    async def test_returns_single_instance_result(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=RAW_PROPSET_ROWS)
        result = await handle_get_property_sets([session], registry, "bp-001")
        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["count"] == 2

    async def test_property_sets_are_parsed(self):
        session = make_session()
        registry = make_registry(rows=RAW_PROPSET_ROWS)
        result = await handle_get_property_sets([session], registry, "bp-001")
        assert result["property_sets"] == PARSED_PROPERTY_SETS

    async def test_empty_blueprint_returns_empty_list(self):
        session = make_session()
        registry = make_registry(rows=[])
        result = await handle_get_property_sets([session], registry, "bp-001")
        assert result["property_sets"] == []
        assert result["count"] == 0

    async def test_graph_error_returns_error_dict(self):
        session = make_session()
        registry = make_registry(error=RuntimeError("graph failure"))
        result = await handle_get_property_sets([session], registry, "bp-001")
        assert result["property_sets"] == []
        assert "error" in result

    async def test_error_message_captured(self):
        session = make_session()
        registry = make_registry(error=RuntimeError("kuzu exploded"))
        result = await handle_get_property_sets([session], registry, "bp-001")
        assert "kuzu exploded" in result["error"]


# ---------------------------------------------------------------------------
# handle_get_property_sets — multiple instances
# ---------------------------------------------------------------------------

class TestHandleGetPropertySetsMulti:

    async def test_two_instances_returns_all_wrapper(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        registry = make_registry(rows=RAW_PROPSET_ROWS)
        result = await handle_get_property_sets([s1, s2], registry, "bp-001")
        assert result["instance"] == "all"
        assert result["total_count"] == 4
        assert len(result["results"]) == 2

    async def test_partial_error_still_returns_other_results(self):
        s1 = make_session("dc-primary")
        s2 = make_session("dc-secondary")
        graph_ok = MagicMock()
        graph_ok.query = MagicMock(return_value=RAW_PROPSET_ROWS)
        registry = MagicMock()
        registry.get_or_rebuild = AsyncMock(
            side_effect=[graph_ok, RuntimeError("dc-secondary unreachable")]
        )
        result = await handle_get_property_sets([s1, s2], registry, "bp-001")
        primary = next(r for r in result["results"] if r["instance"] == "dc-primary")
        secondary = next(r for r in result["results"] if r["instance"] == "dc-secondary")
        assert primary["count"] == 2
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
