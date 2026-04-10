import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from primitives.response_parser import parse_systems
from handlers.systems import handle_get_systems, _select_sessions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session(name="dc-primary"):
    session = MagicMock()
    session.name = name
    return session


def make_registry(rows=None, error=None):
    """Returns a mock registry whose get_or_rebuild yields a graph that returns rows."""
    registry = MagicMock()
    graph = MagicMock()
    if error:
        registry.get_or_rebuild = AsyncMock(side_effect=error)
    else:
        graph.query = MagicMock(return_value=rows or [])
        registry.get_or_rebuild = AsyncMock(return_value=graph)
    return registry


# Raw Kuzu rows as returned by graph.query() — column names include the sw. prefix
RAW_ROWS = [
    {
        "sw.id": "node-001",
        "sw.label": "spine-1",
        "sw.role": "spine",
        "sw.system_id": "525400ABC001",
        "sw.type": "system",
        "sw.external": False,
        "sw.hostname": "spine-1",
        "sw.management_level": "full_control",
        "sw.system_type": "switch",
        "sw.group_label": "spine1",
        "sw.deploy_mode": "deploy",
    },
    {
        "sw.id": "node-002",
        "sw.label": "leaf-1",
        "sw.role": "leaf",
        "sw.system_id": "525400ABC002",
        "sw.type": "system",
        "sw.external": False,
        "sw.hostname": "leaf-1",
        "sw.management_level": "full_control",
        "sw.system_type": "switch",
        "sw.group_label": "leaf1",
        "sw.deploy_mode": "deploy",
    },
]

PARSED_SYSTEMS = [
    {
        "id": "node-001",
        "label": "spine-1",
        "role": "spine",
        "system_id": "525400ABC001",
        "system_type": "switch",
        "hostname": "spine-1",
        "deploy_mode": "deploy",
        "management_level": "full_control",
        "external": False,
        "group_label": "spine1",
    },
    {
        "id": "node-002",
        "label": "leaf-1",
        "role": "leaf",
        "system_id": "525400ABC002",
        "system_type": "switch",
        "hostname": "leaf-1",
        "deploy_mode": "deploy",
        "management_level": "full_control",
        "external": False,
        "group_label": "leaf1",
    },
]


# ---------------------------------------------------------------------------
# response_parser.parse_systems
# ---------------------------------------------------------------------------

class TestParseSystems:
    def test_strips_sw_prefix_and_maps_fields(self):
        assert parse_systems(RAW_ROWS) == PARSED_SYSTEMS

    def test_empty_rows(self):
        assert parse_systems([]) == []

    def test_missing_fields_use_defaults(self):
        result = parse_systems([{}])
        assert result[0]["id"] == "unknown"
        assert result[0]["label"] == "unknown"
        assert result[0]["role"] == "unknown"
        assert result[0]["system_id"] is None
        assert result[0]["external"] is False
        assert result[0]["group_label"] is None

    def test_external_system_flag(self):
        row = {**RAW_ROWS[0], "sw.external": True}
        result = parse_systems([row])
        assert result[0]["external"] is True


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
# handle_get_systems
# ---------------------------------------------------------------------------

class TestHandleGetSystems:
    async def test_single_session_success(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=RAW_ROWS)

        result = await handle_get_systems([session], registry, "bp-001")

        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["systems"] == PARSED_SYSTEMS
        assert result["count"] == 2

    async def test_single_session_registry_error_returns_error_dict(self):
        session = make_session("dc-primary")
        registry = make_registry(error=Exception("graph build failed"))

        result = await handle_get_systems([session], registry, "bp-001")

        assert result["instance"] == "dc-primary"
        assert "error" in result
        assert result["systems"] == []
        assert result["count"] == 0

    async def test_empty_blueprint_returns_empty_systems(self):
        session = make_session("dc-primary")
        registry = make_registry(rows=[])

        result = await handle_get_systems([session], registry, "bp-001")

        assert result["systems"] == []
        assert result["count"] == 0

    async def test_multiple_sessions_aggregated(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        registry = make_registry(rows=RAW_ROWS)

        result = await handle_get_systems(sessions, registry, "bp-001")

        assert result["instance"] == "all"
        assert result["blueprint_id"] == "bp-001"
        assert result["total_count"] == 4
        assert len(result["results"]) == 2

    async def test_multiple_sessions_partial_failure(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]

        async def side_effect(session, blueprint_id):
            if session.name == "dc-primary":
                graph = MagicMock()
                graph.query = MagicMock(return_value=RAW_ROWS)
                return graph
            raise Exception("unreachable")

        registry = MagicMock()
        registry.get_or_rebuild = side_effect

        result = await handle_get_systems(sessions, registry, "bp-001")

        assert result["instance"] == "all"
        assert result["total_count"] == 2
        good = next(r for r in result["results"] if r["instance"] == "dc-primary")
        bad = next(r for r in result["results"] if r["instance"] == "dc-secondary")
        assert good["count"] == 2
        assert "error" in bad

    async def test_instance_name_filter_queries_only_named_session(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        registry = make_registry(rows=RAW_ROWS)

        result = await handle_get_systems(sessions, registry, "bp-001", instance_name="dc-secondary")

        assert result["instance"] == "dc-secondary"
        assert registry.get_or_rebuild.call_count == 1

    async def test_unknown_instance_name_raises(self):
        sessions = [make_session("dc-primary")]
        registry = make_registry(rows=RAW_ROWS)

        with pytest.raises(ValueError, match="No instance named 'nonexistent'"):
            await handle_get_systems(sessions, registry, "bp-001", instance_name="nonexistent")
