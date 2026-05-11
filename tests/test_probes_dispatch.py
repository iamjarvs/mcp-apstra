from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.probes_dispatch import register


class StubMCP:
    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _build_tool_and_mocks():
    mcp = StubMCP()
    mocks = {
        "list": AsyncMock(return_value={"probe_count": 2}),
        "detail": AsyncMock(return_value={"item_count": 4}),
        "history": AsyncMock(return_value={"item_count": 8}),
    }

    side_effect = [
        {
            "get_probe_list": mocks["list"],
            "get_probe_detail": mocks["detail"],
            "get_probe_history": mocks["history"],
        }
    ]

    with patch("tools.probes_dispatch._capture_registered_tools", side_effect=side_effect):
        register(mcp)

    return mcp.tools["probes"], mocks


@pytest.mark.asyncio
async def test_probes_requires_blueprint_id():
    tool, mocks = _build_tool_and_mocks()

    result = await tool(intent="list", ctx=MagicMock())

    assert result["error"] == "missing_required_parameters"
    assert result["missing"] == ["blueprint_id"]
    mocks["list"].assert_not_called()


@pytest.mark.asyncio
async def test_detail_requires_probe_id():
    tool, mocks = _build_tool_and_mocks()

    result = await tool(intent="detail", blueprint_id="bp-1", ctx=MagicMock())

    assert result["error"] == "missing_required_parameters"
    assert result["missing"] == ["probe_id"]
    mocks["detail"].assert_not_called()


@pytest.mark.asyncio
async def test_history_requires_probe_id_and_stage():
    tool, mocks = _build_tool_and_mocks()

    result = await tool(intent="history", blueprint_id="bp-1", probe_id="p-1", ctx=MagicMock())

    assert result["error"] == "missing_required_parameters"
    assert result["missing"] == ["stage"]
    mocks["history"].assert_not_called()


@pytest.mark.asyncio
async def test_list_intent_dispatches_to_legacy_tool():
    tool, mocks = _build_tool_and_mocks()

    result = await tool(intent="list", blueprint_id="bp-1", anomalous_only=True, ctx=MagicMock())

    assert result["intent"] == "list"
    mocks["list"].assert_awaited_once()
