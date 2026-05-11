from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.anomaly_umbrella import register


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
        "current_live": AsyncMock(return_value={"count": 2}),
        "summary": AsyncMock(return_value={"event_count": 10}),
        "events": AsyncMock(return_value={"event_count": 5}),
        "active": AsyncMock(return_value={"active_count": 3}),
        "device_history": AsyncMock(return_value={"history_count": 7}),
        "trend": AsyncMock(return_value={"device_count": 4}),
        "correlated_faults": AsyncMock(return_value={"logical_count": 2}),
        "durations": AsyncMock(return_value={"device_count": 1}),
        "heatmap": AsyncMock(return_value={"device_count": 2}),
        "correlate_events": AsyncMock(return_value={"cluster_count": 1}),
    }

    side_effect = [
        {"get_current_anomalies": mocks["current_live"]},
        {
            "get_anomaly_summary": mocks["summary"],
            "get_anomaly_events": mocks["events"],
            "get_active_anomalies_from_store": mocks["active"],
            "get_device_anomaly_history": mocks["device_history"],
        },
        {
            "get_anomaly_trend": mocks["trend"],
            "get_correlated_faults": mocks["correlated_faults"],
            "get_fault_durations": mocks["durations"],
            "get_device_anomaly_heatmap": mocks["heatmap"],
            "correlate_anomaly_events": mocks["correlate_events"],
        },
    ]

    with patch("tools.anomaly_umbrella._capture_registered_tools", side_effect=side_effect):
        register(mcp)

    return mcp.tools["anomaly"], mocks


@pytest.mark.asyncio
async def test_summary_intent_dispatches_to_summary_tool():
    tool, mocks = _build_tool_and_mocks()
    ctx = MagicMock()

    result = await tool(intent="summary", blueprint_id="bp-1", ctx=ctx)

    assert result["intent"] == "summary"
    mocks["summary"].assert_awaited_once()


@pytest.mark.asyncio
async def test_device_history_requires_device_label():
    tool, mocks = _build_tool_and_mocks()

    result = await tool(intent="device_history", blueprint_id="bp-1", ctx=MagicMock())

    assert result["error"] == "missing_required_parameters"
    assert result["missing"] == ["device_label"]
    mocks["device_history"].assert_not_called()


@pytest.mark.asyncio
async def test_current_live_intent_dispatches_to_live_tool():
    tool, mocks = _build_tool_and_mocks()
    ctx = MagicMock()

    result = await tool(intent="current_live", blueprint_id="bp-1", ctx=ctx)

    assert result["intent"] == "current_live"
    mocks["current_live"].assert_awaited_once_with(
        blueprint_id="bp-1",
        instance_name=None,
        ctx=ctx,
    )
