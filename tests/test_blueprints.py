import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from primitives.response_parser import parse_blueprints
from handlers.blueprints import handle_get_blueprints, _select_sessions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session(name="dc-primary"):
    session = MagicMock()
    session.name = name
    return session


RAW_ITEM = {
    "id": "bp-001",
    "label": "prod-dc-east",
    "status": "created",
    "design": "two_stage_l3clos",
    "version": 478,
    "last_modified_at": "2026-04-07T21:56:18.150811Z",
    "has_uncommitted_changes": True,
    "build_errors_count": 0,
    "build_warnings_count": 2,
    "anomaly_counts": {"bgp": 4, "cabling": 2, "all": 33},
    "spine_count": 2,
    "leaf_count": 3,
    "rack_count": 2,
    "security_zone_count": 4,
    "virtual_network_count": 12,
    "generic_count": 9,
}

RAW_RESPONSE = {"items": [RAW_ITEM, {**RAW_ITEM, "id": "bp-002", "label": "prod-dc-west"}]}

PARSED_ITEM = {
    "id": "bp-001",
    "label": "prod-dc-east",
    "design": "two_stage_l3clos",
    "status": "created",
    "version": 478,
    "last_modified_at": "2026-04-07T21:56:18.150811Z",
    "has_uncommitted_changes": True,
    "build_errors_count": 0,
    "build_warnings_count": 2,
    "anomaly_counts": {"bgp": 4, "cabling": 2, "all": 33},
    "topology": {
        "spine_count": 2,
        "leaf_count": 3,
        "rack_count": 2,
        "security_zone_count": 4,
        "virtual_network_count": 12,
        "generic_count": 9,
    },
}

PARSED_BLUEPRINTS = [
    PARSED_ITEM,
    {**PARSED_ITEM, "id": "bp-002", "label": "prod-dc-west"},
]


# ---------------------------------------------------------------------------
# response_parser.parse_blueprints
# ---------------------------------------------------------------------------

class TestParseBlueprints:
    def test_normal_items(self):
        assert parse_blueprints(RAW_RESPONSE) == PARSED_BLUEPRINTS

    def test_missing_fields_use_defaults(self):
        result = parse_blueprints({"items": [{}]})
        assert result[0]["id"] == "unknown"
        assert result[0]["has_uncommitted_changes"] is False
        assert result[0]["build_errors_count"] == 0
        assert result[0]["anomaly_counts"] == {}
        assert result[0]["topology"] == {
            "spine_count": 0, "leaf_count": 0, "rack_count": 0,
            "security_zone_count": 0, "virtual_network_count": 0, "generic_count": 0,
        }

    def test_empty_items_list(self):
        assert parse_blueprints({"items": []}) == []

    def test_no_items_key(self):
        assert parse_blueprints({}) == []

    def test_anomaly_counts_passed_through(self):
        result = parse_blueprints({"items": [RAW_ITEM]})
        assert result[0]["anomaly_counts"]["bgp"] == 4
        assert result[0]["anomaly_counts"]["all"] == 33

    def test_topology_extracted(self):
        result = parse_blueprints({"items": [RAW_ITEM]})
        assert result[0]["topology"]["spine_count"] == 2
        assert result[0]["topology"]["leaf_count"] == 3
        assert result[0]["topology"]["virtual_network_count"] == 12

    def test_uncommitted_changes_flag(self):
        result = parse_blueprints({"items": [RAW_ITEM]})
        assert result[0]["has_uncommitted_changes"] is True


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
# handle_get_blueprints
# ---------------------------------------------------------------------------

class TestHandleGetBlueprints:
    async def test_single_session_success(self):
        session = make_session("dc-primary")
        with patch("primitives.live_data_client.get_blueprints", new=AsyncMock(return_value=RAW_RESPONSE)):
            result = await handle_get_blueprints([session])

        assert result["instance"] == "dc-primary"
        assert result["blueprints"] == PARSED_BLUEPRINTS
        assert result["count"] == 2

    async def test_single_session_api_error_returns_error_dict(self):
        session = make_session("dc-primary")
        with patch("primitives.live_data_client.get_blueprints", new=AsyncMock(side_effect=Exception("timeout"))):
            result = await handle_get_blueprints([session])

        assert result["instance"] == "dc-primary"
        assert result["error"] == "timeout"
        assert result["blueprints"] == []
        assert result["count"] == 0

    async def test_multiple_sessions_aggregated(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        with patch("primitives.live_data_client.get_blueprints", new=AsyncMock(return_value=RAW_RESPONSE)):
            result = await handle_get_blueprints(sessions)

        assert result["instance"] == "all"
        assert result["total_count"] == 4
        assert len(result["results"]) == 2

    async def test_multiple_sessions_partial_failure(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]

        async def side_effect(session):
            if session.name == "dc-primary":
                return RAW_RESPONSE
            raise Exception("unreachable")

        with patch("primitives.live_data_client.get_blueprints", side_effect=side_effect):
            result = await handle_get_blueprints(sessions)

        assert result["instance"] == "all"
        assert result["total_count"] == 2
        good = next(r for r in result["results"] if r["instance"] == "dc-primary")
        bad = next(r for r in result["results"] if r["instance"] == "dc-secondary")
        assert good["count"] == 2
        assert "error" in bad

    async def test_instance_name_filter_queries_only_named_session(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        mock_get = AsyncMock(return_value=RAW_RESPONSE)
        with patch("primitives.live_data_client.get_blueprints", new=mock_get):
            result = await handle_get_blueprints(sessions, instance_name="dc-secondary")

        assert result["instance"] == "dc-secondary"
        assert mock_get.call_count == 1

    async def test_unknown_instance_name_raises(self):
        sessions = [make_session("dc-primary")]
        with pytest.raises(ValueError, match="No instance named 'nonexistent'"):
            await handle_get_blueprints(sessions, instance_name="nonexistent")

    async def test_empty_instance_returns_empty_blueprints(self):
        session = make_session("dc-primary")
        with patch("primitives.live_data_client.get_blueprints", new=AsyncMock(return_value={"items": []})):
            result = await handle_get_blueprints([session])

        assert result["blueprints"] == []
        assert result["count"] == 0
