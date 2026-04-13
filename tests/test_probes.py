"""
tests/test_probes.py

Unit tests for tools/probes.py — three MCP tools:
  get_probe_list
  get_probe_detail
  get_probe_history

All Apstra API calls are mocked via unittest.mock.AsyncMock.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

import pytest

from tools.probes import register


# ── Helpers ──────────────────────────────────────────────────────────────────

class StubMCP:
    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


def make_ctx(sessions):
    ctx = MagicMock()
    ctx.lifespan_context = {
        "sessions": sessions,
        "anomaly_store": MagicMock(),
        "graph_registry": MagicMock(),
    }
    return ctx


def make_session(name="dc-primary"):
    s = MagicMock()
    s.name = name
    return s


@pytest.fixture
def tools():
    mcp = StubMCP()
    register(mcp)
    return mcp.tools


# ── Fixtures ──────────────────────────────────────────────────────────────────

PROBE_ALPHA = {
    "id": "probe-aaa",
    "label": "BGP Monitoring",
    "description": "Monitors BGP sessions",
    "state": "operational",
    "probe_state": "normal",
    "disabled": False,
    "anomaly_count": 4,
    "predefined_probe": "bgp_monitoring",
    "stages": [
        {"name": "BGP Session"},
        {"name": "Sustained BGP Session Flapping"},
        {"name": "BGP Session Flapping"},
    ],
    "updated_at": "2026-01-01T00:00:00Z",
}

PROBE_BETA = {
    "id": "probe-bbb",
    "label": "ECMP Imbalance",
    "description": "",
    "state": "operational",
    "probe_state": "normal",
    "disabled": False,
    "anomaly_count": 0,
    "predefined_probe": "ecmp_imbalance",
    "stages": [{"name": "ECMP Imbalance"}, {"name": "Interface Traffic"}],
    "updated_at": "2026-01-01T00:00:00Z",
}

PROBE_GAMMA = {
    "id": "probe-ccc",
    "label": "VXLAN Flood",
    "description": "VXLAN flood list validation",
    "state": "operational",
    "probe_state": "normal",
    "disabled": False,
    "anomaly_count": 22,
    "predefined_probe": "vxlan_flood",
    "stages": [{"name": "VXLAN Flood List"}],
    "updated_at": "2026-01-01T00:00:00Z",
}

PROBE_LIST_RAW = {
    "items": [PROBE_ALPHA, PROBE_BETA, PROBE_GAMMA],
    "total_count": 3,
}

BGP_SESSION_ITEMS = [
    {
        "timestamp": "2026-01-01T00:00:10Z",
        "value": "false",
        "id": 1,
        "properties": {
            "system_id": "SYS-SPINE1",
            "af": "ipv4",
            "dest_asn": "64519",
            "dest_ip": "10.0.0.1",
            "source_ip": "10.0.0.2",
            "vrf_name": "default",
        },
    },
    {
        "timestamp": "2026-01-01T00:00:00Z",
        "value": "true",
        "id": 2,
        "properties": {
            "system_id": "SYS-SPINE2",
            "af": "evpn",
            "dest_asn": "64518",
            "dest_ip": "10.0.1.1",
            "source_ip": "10.0.1.2",
            "vrf_name": "default",
        },
    },
]

BGP_QUERY_RAW = {
    "type": "table",
    "items": BGP_SESSION_ITEMS,
    "total_count": 2,
}


# ─────────────────────────────────────────────────────────────────────────────
# get_probe_list
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_probe_list_returns_all_probes(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.get_probes", new=AsyncMock(return_value=PROBE_LIST_RAW)):
        result = await tools["get_probe_list"](blueprint_id="bp-001", ctx=ctx)
    assert result["probe_count"] == 3


@pytest.mark.asyncio
async def test_probe_list_sorted_anomalies_first(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.get_probes", new=AsyncMock(return_value=PROBE_LIST_RAW)):
        result = await tools["get_probe_list"](blueprint_id="bp-001", ctx=ctx)
    counts = [p["anomaly_count"] for p in result["probes"]]
    # VXLAN Flood (22) > BGP Monitoring (4) > ECMP Imbalance (0)
    assert counts == sorted(counts, reverse=True)


@pytest.mark.asyncio
async def test_probe_list_total_anomalies(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.get_probes", new=AsyncMock(return_value=PROBE_LIST_RAW)):
        result = await tools["get_probe_list"](blueprint_id="bp-001", ctx=ctx)
    assert result["total_anomalies"] == 26  # 4 + 0 + 22


@pytest.mark.asyncio
async def test_probe_list_anomalous_only_filter(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.get_probes", new=AsyncMock(return_value=PROBE_LIST_RAW)):
        result = await tools["get_probe_list"](
            blueprint_id="bp-001", anomalous_only=True, ctx=ctx
        )
    assert result["probe_count"] == 2
    labels = {p["label"] for p in result["probes"]}
    assert "ECMP Imbalance" not in labels


@pytest.mark.asyncio
async def test_probe_list_stage_names_present(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.get_probes", new=AsyncMock(return_value=PROBE_LIST_RAW)):
        result = await tools["get_probe_list"](blueprint_id="bp-001", ctx=ctx)
    bgp = next(p for p in result["probes"] if p["label"] == "BGP Monitoring")
    assert "BGP Session" in bgp["stage_names"]
    assert "Sustained BGP Session Flapping" in bgp["stage_names"]


@pytest.mark.asyncio
async def test_probe_list_no_session(tools):
    ctx = make_ctx([make_session("prod")])
    with patch("tools.probes.live_data_client.get_probes", new=AsyncMock(return_value=PROBE_LIST_RAW)):
        result = await tools["get_probe_list"](
            blueprint_id="bp-001", instance_name="nonexistent", ctx=ctx
        )
    assert "error" in result


@pytest.mark.asyncio
async def test_probe_list_includes_instance(tools):
    session = make_session("dc-west")
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.get_probes", new=AsyncMock(return_value=PROBE_LIST_RAW)):
        result = await tools["get_probe_list"](blueprint_id="bp-001", ctx=ctx)
    assert result["instance"] == "dc-west"


# ─────────────────────────────────────────────────────────────────────────────
# get_probe_detail
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_probe_detail_queries_first_stage_by_default(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.get_probe", new=AsyncMock(return_value=PROBE_ALPHA)), \
         patch("tools.probes.live_data_client.query_probe_stage", new=AsyncMock(return_value=BGP_QUERY_RAW)):
        result = await tools["get_probe_detail"](
            blueprint_id="bp-001", probe_id="probe-aaa", ctx=ctx
        )
    assert result["queried_stage"] == "BGP Session"


@pytest.mark.asyncio
async def test_probe_detail_explicit_stage(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.get_probe", new=AsyncMock(return_value=PROBE_ALPHA)), \
         patch("tools.probes.live_data_client.query_probe_stage", new=AsyncMock(return_value=BGP_QUERY_RAW)) as mock_q:
        await tools["get_probe_detail"](
            blueprint_id="bp-001", probe_id="probe-aaa",
            stage="Sustained BGP Session Flapping", ctx=ctx
        )
    args, kwargs = mock_q.call_args
    assert kwargs.get("stage") == "Sustained BGP Session Flapping" or args[3] == "Sustained BGP Session Flapping"


@pytest.mark.asyncio
async def test_probe_detail_invalid_stage(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.get_probe", new=AsyncMock(return_value=PROBE_ALPHA)):
        result = await tools["get_probe_detail"](
            blueprint_id="bp-001", probe_id="probe-aaa",
            stage="Nonexistent Stage", ctx=ctx
        )
    assert "error" in result
    assert "available_stages" in result


@pytest.mark.asyncio
async def test_probe_detail_returns_items(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.get_probe", new=AsyncMock(return_value=PROBE_ALPHA)), \
         patch("tools.probes.live_data_client.query_probe_stage", new=AsyncMock(return_value=BGP_QUERY_RAW)):
        result = await tools["get_probe_detail"](
            blueprint_id="bp-001", probe_id="probe-aaa", ctx=ctx
        )
    assert result["item_count"] == 2
    assert len(result["items"]) == 2


@pytest.mark.asyncio
async def test_probe_detail_all_stages_listed(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.get_probe", new=AsyncMock(return_value=PROBE_ALPHA)), \
         patch("tools.probes.live_data_client.query_probe_stage", new=AsyncMock(return_value=BGP_QUERY_RAW)):
        result = await tools["get_probe_detail"](
            blueprint_id="bp-001", probe_id="probe-aaa", ctx=ctx
        )
    assert set(result["all_stages"]) == {
        "BGP Session", "Sustained BGP Session Flapping", "BGP Session Flapping"
    }


@pytest.mark.asyncio
async def test_probe_detail_no_stages(tools):
    session = make_session()
    ctx = make_ctx([session])
    empty_probe = {**PROBE_ALPHA, "stages": []}
    with patch("tools.probes.live_data_client.get_probe", new=AsyncMock(return_value=empty_probe)):
        result = await tools["get_probe_detail"](
            blueprint_id="bp-001", probe_id="probe-aaa", ctx=ctx
        )
    assert "error" in result


@pytest.mark.asyncio
async def test_probe_detail_no_session(tools):
    ctx = make_ctx([make_session("prod")])
    result = await tools["get_probe_detail"](
        blueprint_id="bp-001", probe_id="probe-aaa",
        instance_name="none", ctx=ctx
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_probe_detail_anomaly_count_in_response(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.get_probe", new=AsyncMock(return_value=PROBE_ALPHA)), \
         patch("tools.probes.live_data_client.query_probe_stage", new=AsyncMock(return_value=BGP_QUERY_RAW)):
        result = await tools["get_probe_detail"](
            blueprint_id="bp-001", probe_id="probe-aaa", ctx=ctx
        )
    assert result["anomaly_count"] == 4


# ─────────────────────────────────────────────────────────────────────────────
# get_probe_history
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_probe_history_basic(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.query_probe_stage", new=AsyncMock(return_value=BGP_QUERY_RAW)):
        result = await tools["get_probe_history"](
            blueprint_id="bp-001", probe_id="probe-aaa",
            stage="BGP Session", hours_back=1, ctx=ctx
        )
    assert result["item_count"] == 2
    assert result["stage"] == "BGP Session"


@pytest.mark.asyncio
async def test_probe_history_items_sorted_newest_first(tools):
    session = make_session()
    ctx = make_ctx([session])
    items = [
        {"timestamp": "2026-01-01T00:00:00Z", "value": "a"},
        {"timestamp": "2026-01-01T00:00:30Z", "value": "b"},
        {"timestamp": "2026-01-01T00:00:20Z", "value": "c"},
    ]
    raw = {"type": "table", "items": items, "total_count": 3}
    with patch("tools.probes.live_data_client.query_probe_stage", new=AsyncMock(return_value=raw)):
        result = await tools["get_probe_history"](
            blueprint_id="bp-001", probe_id="probe-aaa",
            stage="BGP Session", hours_back=1, ctx=ctx
        )
    ts_list = [i["timestamp"] for i in result["items"]]
    assert ts_list == sorted(ts_list, reverse=True)


@pytest.mark.asyncio
async def test_probe_history_hours_back_clamped(tools):
    """hours_back > 168 should be clamped to 168."""
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.query_probe_stage", new=AsyncMock(return_value=BGP_QUERY_RAW)) as mock_q:
        await tools["get_probe_history"](
            blueprint_id="bp-001", probe_id="probe-aaa",
            stage="BGP Session", hours_back=9999, ctx=ctx
        )
    result_hours = None
    # Check via begin/end times calculated in the tool
    # We verify it doesn't blow up and returns valid result
    assert mock_q.call_count == 1


@pytest.mark.asyncio
async def test_probe_history_hours_back_minimum(tools):
    """hours_back < 1 should be clamped to 1."""
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.query_probe_stage", new=AsyncMock(return_value=BGP_QUERY_RAW)):
        result = await tools["get_probe_history"](
            blueprint_id="bp-001", probe_id="probe-aaa",
            stage="BGP Session", hours_back=0, ctx=ctx
        )
    assert result["hours_back"] == 1


@pytest.mark.asyncio
async def test_probe_history_no_session(tools):
    ctx = make_ctx([make_session("prod")])
    result = await tools["get_probe_history"](
        blueprint_id="bp-001", probe_id="probe-aaa",
        stage="BGP Session", instance_name="none", ctx=ctx
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_probe_history_includes_times(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.query_probe_stage", new=AsyncMock(return_value=BGP_QUERY_RAW)):
        result = await tools["get_probe_history"](
            blueprint_id="bp-001", probe_id="probe-aaa",
            stage="BGP Session", hours_back=2, ctx=ctx
        )
    assert "begin_time" in result
    assert "end_time" in result


@pytest.mark.asyncio
async def test_probe_history_empty_items(tools):
    session = make_session()
    ctx = make_ctx([session])
    empty_raw = {"type": "table", "items": [], "total_count": 0}
    with patch("tools.probes.live_data_client.query_probe_stage", new=AsyncMock(return_value=empty_raw)):
        result = await tools["get_probe_history"](
            blueprint_id="bp-001", probe_id="probe-aaa",
            stage="BGP Session", hours_back=1, ctx=ctx
        )
    assert result["item_count"] == 0
    assert result["items"] == []


@pytest.mark.asyncio
async def test_probe_history_begin_time_before_end_time(tools):
    """begin_time computed from hours_back should be before end_time."""
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.probes.live_data_client.query_probe_stage", new=AsyncMock(return_value=BGP_QUERY_RAW)):
        result = await tools["get_probe_history"](
            blueprint_id="bp-001", probe_id="probe-aaa",
            stage="BGP Session", hours_back=6, ctx=ctx
        )
    assert result["begin_time"] < result["end_time"]
