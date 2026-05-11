from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.virtual_networks_dispatch import register


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
        "deployments": AsyncMock(return_value={"count": 2}),
        "list": AsyncMock(return_value={"count": 3}),
        "zones": AsyncMock(return_value={"count": 2}),
        "zone_detail": AsyncMock(return_value={"vn_count": 1}),
        "vn_detail": AsyncMock(return_value={"deployed_count": 2}),
    }

    side_effect = [
        {
            "get_vn_deployments": mocks["deployments"],
            "get_virtual_networks": mocks["list"],
            "get_routing_zones": mocks["zones"],
            "get_routing_zone_detail": mocks["zone_detail"],
            "get_virtual_network_detail": mocks["vn_detail"],
        }
    ]

    with patch("tools.virtual_networks_dispatch._capture_registered_tools", side_effect=side_effect):
        register(mcp)

    return mcp.tools["virtual_networks"], mocks


@pytest.mark.asyncio
async def test_virtual_networks_requires_blueprint_id():
    tool, mocks = _build_tool_and_mocks()

    result = await tool(intent="list", ctx=MagicMock())

    assert result["error"] == "missing_required_parameters"
    assert result["missing"] == ["blueprint_id"]
    mocks["list"].assert_not_called()


@pytest.mark.asyncio
async def test_routing_zone_detail_requires_routing_zone():
    tool, mocks = _build_tool_and_mocks()

    result = await tool(intent="routing_zone_detail", blueprint_id="bp-1", ctx=MagicMock())

    assert result["error"] == "missing_required_parameters"
    assert result["missing"] == ["routing_zone"]
    mocks["zone_detail"].assert_not_called()


@pytest.mark.asyncio
async def test_list_intent_dispatches_to_legacy_tool():
    tool, mocks = _build_tool_and_mocks()

    result = await tool(intent="list", blueprint_id="bp-1", ctx=MagicMock())

    assert result["intent"] == "list"
    mocks["list"].assert_awaited_once()
