from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.telemetry_dispatch import register


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
        "counters": AsyncMock(return_value={"interface_count": 1}),
        "util": AsyncMock(return_value={"interface_count": 2}),
        "system": AsyncMock(return_value={"device_count": 1}),
        "trend": AsyncMock(return_value={"data_point_count": 5}),
        "growers": AsyncMock(return_value={"interface_count": 3}),
    }

    side_effect = [
        {
            "get_interface_counters": mocks["counters"],
            "get_interface_utilisation": mocks["util"],
            "get_system_telemetry": mocks["system"],
            "get_interface_error_trend": mocks["trend"],
            "get_top_error_growers": mocks["growers"],
        }
    ]

    with patch("tools.telemetry_dispatch._capture_registered_tools", side_effect=side_effect):
        register(mcp)

    return mcp.tools["telemetry"], mocks


@pytest.mark.asyncio
async def test_interface_counters_requires_system_id():
    tool, mocks = _build_tool_and_mocks()

    result = await tool(intent="interface_counters", ctx=MagicMock())

    assert result["error"] == "missing_required_parameters"
    assert result["missing"] == ["system_id"]
    mocks["counters"].assert_not_called()


@pytest.mark.asyncio
async def test_interface_counters_dispatches_when_required_fields_present():
    tool, mocks = _build_tool_and_mocks()
    ctx = MagicMock()

    result = await tool(intent="interface_counters", system_id="SYS1", errors_only=True, ctx=ctx)

    assert result["intent"] == "interface_counters"
    mocks["counters"].assert_awaited_once_with(
        system_id="SYS1",
        interface_name=None,
        errors_only=True,
        instance_name=None,
        ctx=ctx,
    )


@pytest.mark.asyncio
async def test_system_telemetry_requires_system_ids_or_blueprint():
    tool, mocks = _build_tool_and_mocks()

    result = await tool(intent="system_telemetry", ctx=MagicMock())

    assert result["error"] == "missing_required_parameters"
    assert result["missing"] == ["system_ids_or_blueprint_id"]
    mocks["system"].assert_not_called()


@pytest.mark.asyncio
async def test_top_error_growers_dispatches():
    tool, mocks = _build_tool_and_mocks()

    result = await tool(
        intent="top_error_growers",
        hours_back=12,
        top_n=5,
        blueprint_id="bp-1",
        ctx=MagicMock(),
    )

    assert result["intent"] == "top_error_growers"
    mocks["growers"].assert_awaited_once()
