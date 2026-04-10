import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from primitives.response_parser import parse_anomalies
from handlers.anomalies import handle_get_anomalies, _select_sessions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session(name="dc-primary"):
    session = MagicMock()
    session.name = name
    return session


RAW_RESPONSE = {
    "items": [
        {
            "severity": "critical",
            "anomaly_type": "bgp",
            "description": "BGP session down",
            "system_id": "spine-1",
        }
    ]
}

PARSED_ANOMALIES = [
    {
        "severity": "critical",
        "type": "bgp",
        "description": "BGP session down",
        "affected_node": "spine-1",
    }
]


# ---------------------------------------------------------------------------
# response_parser.parse_anomalies
# ---------------------------------------------------------------------------

class TestParseAnomalies:
    def test_normal_item(self):
        result = parse_anomalies(RAW_RESPONSE)
        assert result == PARSED_ANOMALIES

    def test_missing_fields_use_defaults(self):
        result = parse_anomalies({"items": [{}]})
        assert result[0]["severity"] == "unknown"
        assert result[0]["type"] == "unknown"
        assert result[0]["description"] == ""
        assert result[0]["affected_node"] == "unknown"

    def test_empty_items_list(self):
        assert parse_anomalies({"items": []}) == []

    def test_no_items_key(self):
        assert parse_anomalies({}) == []

    def test_multiple_items(self):
        raw = {
            "items": [
                {"severity": "critical", "anomaly_type": "bgp", "description": "d1", "system_id": "n1"},
                {"severity": "warning", "anomaly_type": "lldp", "description": "d2", "system_id": "n2"},
            ]
        }
        result = parse_anomalies(raw)
        assert len(result) == 2
        assert result[0]["type"] == "bgp"
        assert result[1]["affected_node"] == "n2"


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
# handle_get_anomalies
# ---------------------------------------------------------------------------

class TestHandleGetAnomalies:
    async def test_single_session_success(self):
        session = make_session("dc-primary")
        with patch("primitives.live_data_client.get_anomalies", new=AsyncMock(return_value=RAW_RESPONSE)):
            result = await handle_get_anomalies([session], "bp-001")

        assert result["instance"] == "dc-primary"
        assert result["blueprint_id"] == "bp-001"
        assert result["anomalies"] == PARSED_ANOMALIES
        assert result["count"] == 1

    async def test_single_session_api_error_returns_error_dict(self):
        session = make_session("dc-primary")
        with patch("primitives.live_data_client.get_anomalies", new=AsyncMock(side_effect=Exception("timeout"))):
            result = await handle_get_anomalies([session], "bp-001")

        assert result["instance"] == "dc-primary"
        assert result["error"] == "timeout"
        assert result["anomalies"] == []
        assert result["count"] == 0

    async def test_multiple_sessions_aggregated(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        with patch("primitives.live_data_client.get_anomalies", new=AsyncMock(return_value=RAW_RESPONSE)):
            result = await handle_get_anomalies(sessions, "bp-001")

        assert result["instance"] == "all"
        assert result["blueprint_id"] == "bp-001"
        assert result["total_count"] == 2
        assert len(result["results"]) == 2

    async def test_multiple_sessions_partial_failure(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]

        async def side_effect(session, blueprint_id):
            if session.name == "dc-primary":
                return RAW_RESPONSE
            raise Exception("unreachable")

        with patch("primitives.live_data_client.get_anomalies", side_effect=side_effect):
            result = await handle_get_anomalies(sessions, "bp-001")

        assert result["instance"] == "all"
        assert result["total_count"] == 1
        good = next(r for r in result["results"] if r["instance"] == "dc-primary")
        bad = next(r for r in result["results"] if r["instance"] == "dc-secondary")
        assert good["count"] == 1
        assert "error" in bad

    async def test_instance_name_filter_queries_only_named_session(self):
        sessions = [make_session("dc-primary"), make_session("dc-secondary")]
        mock_get = AsyncMock(return_value=RAW_RESPONSE)
        with patch("primitives.live_data_client.get_anomalies", new=mock_get):
            result = await handle_get_anomalies(sessions, "bp-001", instance_name="dc-secondary")

        assert result["instance"] == "dc-secondary"
        assert mock_get.call_count == 1

    async def test_unknown_instance_name_raises(self):
        sessions = [make_session("dc-primary")]
        with pytest.raises(ValueError, match="No instance named 'nonexistent'"):
            await handle_get_anomalies(sessions, "bp-001", instance_name="nonexistent")
