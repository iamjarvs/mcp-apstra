import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from handlers.blueprints import handle_get_blueprint_build_errors
from tools.blueprints import register


class StubMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _run(coro):
    return asyncio.run(coro)


def _make_session(name="dc-primary"):
    session = MagicMock()
    session.name = name
    return session


def _make_ctx(sessions):
    ctx = MagicMock()
    ctx.lifespan_context = {
        "sessions": sessions,
        "graph_registry": MagicMock(),
        "anomaly_store": MagicMock(),
        "counter_store": MagicMock(),
    }
    return ctx


def test_handle_build_errors_digest_only_when_no_issues():
    session = _make_session()
    call_modes = []

    async def fake_get_build_errors(_session, _blueprint_id, mode="digest"):
        call_modes.append(mode)
        return {"version": 396, "errors_count": 0, "warnings_count": 0}

    with patch(
        "handlers.blueprints.live_data_client.get_blueprint_build_errors",
        side_effect=fake_get_build_errors,
    ):
        result = _run(handle_get_blueprint_build_errors([session], "bp-001"))

    assert call_modes == ["digest"]
    assert result["version"] == 396
    assert result["errors_count"] == 0
    assert result["warnings_count"] == 0
    assert result["details_fetched"] is False
    assert result["issue_count"] == 0
    assert result["issues"] == []


def test_handle_build_errors_fetches_full_and_deduplicates():
    session = _make_session()
    call_modes = []

    source_issue = {
        "severity": "error",
        "display_category": "security-policies",
        "resolutions": [
            {
                "category": "security-policy",
                "entity_id": "node-1",
                "hint": "Update Security Policy",
            }
        ],
        "message": (
            "Policy \"test1\" source application point is resolved "
            "to empty IPv6 enabled object set"
        ),
        "error_type": "EMPTY_IPV6_APP_POINT_SOURCE",
        "entity_type": "policy",
        "rank": 50,
    }

    destination_issue = {
        "severity": "error",
        "display_category": "security-policies",
        "resolutions": [
            {
                "category": "security-policy",
                "entity_id": "node-1",
                "hint": "Update Security Policy",
            }
        ],
        "message": (
            "Policy \"test1\" destination application point is resolved "
            "to empty IPv6 enabled object set"
        ),
        "error_type": "EMPTY_IPV6_APP_POINT_DESTINATION",
        "entity_type": "policy",
        "rank": 50,
    }

    async def fake_get_build_errors(_session, _blueprint_id, mode="digest"):
        call_modes.append(mode)
        if mode == "digest":
            return {"version": 396, "errors_count": 2, "warnings_count": 0}
        return {
            "version": 396,
            "errors_count": 2,
            "warnings_count": 0,
            "nodes": {
                "node-1": [source_issue, source_issue],
                "node-2": [source_issue, destination_issue],
            },
            "relationships": {
                "rel-9": [destination_issue],
            },
        }

    with patch(
        "handlers.blueprints.live_data_client.get_blueprint_build_errors",
        side_effect=fake_get_build_errors,
    ):
        result = _run(handle_get_blueprint_build_errors([session], "bp-001"))

    assert call_modes == ["digest", "full"]
    assert result["errors_count"] == 2
    assert result["details_fetched"] is True
    assert result["issue_count"] == 2

    source = next(i for i in result["issues"] if i["error_type"] == "EMPTY_IPV6_APP_POINT_SOURCE")
    assert source["occurrences"] == 3
    assert source["affected_nodes"] == ["node-1", "node-2"]
    assert source["affected_relationships"] == []

    destination = next(
        i
        for i in result["issues"]
        if i["error_type"] == "EMPTY_IPV6_APP_POINT_DESTINATION"
    )
    assert destination["occurrences"] == 2
    assert destination["affected_nodes"] == ["node-2"]
    assert destination["affected_relationships"] == ["rel-9"]


def test_tool_rollup_multiple_blueprints():
    mcp = StubMCP()
    register(mcp)
    tool = mcp.tools["get_blueprint_build_errors"]
    sessions = [_make_session("dc-a"), _make_session("dc-b")]
    ctx = _make_ctx(sessions)

    with patch(
        "tools.blueprints.resolve_blueprints",
        new=AsyncMock(
            return_value=[
                {"id": "bp-1", "label": "DC1", "instance_name": "dc-a"},
                {"id": "bp-2", "label": "DC2", "instance_name": "dc-b"},
            ]
        ),
    ), patch(
        "tools.blueprints.handle_get_blueprint_build_errors",
        new=AsyncMock(
            side_effect=[
                {
                    "instance": "dc-a",
                    "blueprint_id": "bp-1",
                    "errors_count": 2,
                    "warnings_count": 1,
                    "issues": [{"message": "foo"}],
                },
                {
                    "instance": "dc-b",
                    "blueprint_id": "bp-2",
                    "errors_count": 0,
                    "warnings_count": 3,
                    "issues": [{"message": "bar"}],
                },
            ]
        ),
    ):
        result = _run(tool(blueprint_id="all", ctx=ctx))

    assert result["blueprint_count"] == 2
    assert result["total_errors_count"] == 2
    assert result["total_warnings_count"] == 4
    assert result["blocking_blueprint_count"] == 1
    assert result["results"][0]["blueprint_label"] == "DC1"
    assert result["results"][1]["blueprint_label"] == "DC2"


def test_tool_returns_error_when_no_blueprints_match():
    mcp = StubMCP()
    register(mcp)
    tool = mcp.tools["get_blueprint_build_errors"]
    ctx = _make_ctx([_make_session("dc-primary")])

    with patch("tools.blueprints.resolve_blueprints", new=AsyncMock(return_value=[])):
        result = _run(tool(blueprint_id="missing", ctx=ctx))

    assert "error" in result
    assert "No blueprints found" in result["error"]
