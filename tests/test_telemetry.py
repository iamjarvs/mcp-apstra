"""
tests/test_telemetry.py

Unit tests for tools/telemetry.py — three MCP tools:
  get_interface_counters
  get_interface_utilisation
  get_system_telemetry

All Apstra API calls are mocked via unittest.mock.AsyncMock so no live
instance is required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.telemetry import register


# ── Helpers ──────────────────────────────────────────────────────────────────

class StubMCP:
    """Captures decorated tool functions by name without @pytest.mark magic."""
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


# ─────────────────────────────────────────────────────────────────────────────
# get_interface_counters
# ─────────────────────────────────────────────────────────────────────────────

COUNTER_ITEM_CLEAN = {
    "system_id": "AABBCC001122",
    "interface_name": "ge-0/0/0",
    "rx_unicast_packets": 1000,
    "rx_error_packets": 0,
    "rx_discard_packets": 0,
    "rx_bytes": 100000,
    "tx_unicast_packets": 800,
    "tx_bytes": 80000,
    "alignment_errors": 0,
    "fcs_errors": 0,
    "symbol_errors": 0,
    "runts": 0,
    "giants": 0,
    "last_fetched_at": "2026-01-01T00:00:00Z",
}

COUNTER_ITEM_ERRORS = {
    "system_id": "AABBCC001122",
    "interface_name": "ge-0/0/1",
    "rx_unicast_packets": 500,
    "rx_error_packets": 12,
    "rx_discard_packets": 0,
    "rx_bytes": 50000,
    "tx_unicast_packets": 400,
    "tx_bytes": 40000,
    "alignment_errors": 0,
    "fcs_errors": 5,
    "symbol_errors": 0,
    "runts": 2,
    "giants": 0,
    "last_fetched_at": "2026-01-01T00:00:00Z",
}

COUNTERS_RAW = {
    "items": [COUNTER_ITEM_CLEAN, COUNTER_ITEM_ERRORS],
    "delta_microseconds": 120000000,
}


@pytest.mark.asyncio
async def test_counter_returns_all_interfaces(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.telemetry.live_data_client.get_interface_counters", new=AsyncMock(return_value=COUNTERS_RAW)):
        result = await tools["get_interface_counters"](system_id="AABBCC001122", ctx=ctx)
    assert result["interface_count"] == 2
    assert result["interfaces_with_errors"] == 1


@pytest.mark.asyncio
async def test_counter_errors_only_filter(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.telemetry.live_data_client.get_interface_counters", new=AsyncMock(return_value=COUNTERS_RAW)):
        result = await tools["get_interface_counters"](
            system_id="AABBCC001122", errors_only=True, ctx=ctx
        )
    assert result["interface_count"] == 1
    assert result["interfaces"][0]["interface_name"] == "ge-0/0/1"


@pytest.mark.asyncio
async def test_counter_interface_name_filter(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.telemetry.live_data_client.get_interface_counters", new=AsyncMock(return_value=COUNTERS_RAW)):
        result = await tools["get_interface_counters"](
            system_id="AABBCC001122", interface="ge-0/0/0", ctx=ctx
        )
    assert result["interface_count"] == 1
    assert result["interfaces"][0]["interface_name"] == "ge-0/0/0"


@pytest.mark.asyncio
async def test_counter_has_errors_flag_set_correctly(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.telemetry.live_data_client.get_interface_counters", new=AsyncMock(return_value=COUNTERS_RAW)):
        result = await tools["get_interface_counters"](system_id="AABBCC001122", ctx=ctx)
    by_name = {i["interface_name"]: i for i in result["interfaces"]}
    assert by_name["ge-0/0/0"]["has_errors"] is False
    assert by_name["ge-0/0/1"]["has_errors"] is True


@pytest.mark.asyncio
async def test_counter_no_matching_session(tools):
    ctx = make_ctx([make_session("prod")])
    with patch("tools.telemetry.live_data_client.get_interface_counters", new=AsyncMock(return_value=COUNTERS_RAW)):
        result = await tools["get_interface_counters"](
            system_id="X", instance_name="nonexistent", ctx=ctx
        )
    assert "error" in result


@pytest.mark.asyncio
async def test_counter_instance_name_filter(tools):
    """instance_name='prod' selects only the 'prod' session."""
    prod = make_session("prod")
    other = make_session("staging")
    ctx = make_ctx([prod, other])
    with patch("tools.telemetry.live_data_client.get_interface_counters", new=AsyncMock(return_value=COUNTERS_RAW)) as mock_api:
        await tools["get_interface_counters"](system_id="X", instance_name="prod", ctx=ctx)
        _, call_kwargs = mock_api.call_args
        assert mock_api.call_count == 1


@pytest.mark.asyncio
async def test_counter_empty_response(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.telemetry.live_data_client.get_interface_counters", new=AsyncMock(return_value={"items": []})):
        result = await tools["get_interface_counters"](system_id="AABBCC001122", ctx=ctx)
    assert result["interface_count"] == 0
    assert result["interfaces_with_errors"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# get_interface_utilisation
# ─────────────────────────────────────────────────────────────────────────────

PROBE_LIST_RAW = {
    "items": [
        {"id": "probe-uuid-1", "label": "Device Traffic", "state": "operational",
         "anomaly_count": 0, "stages": [{"name": "Average Interface Counters"}]},
        {"id": "probe-uuid-2", "label": "BGP Monitoring", "state": "operational",
         "anomaly_count": 3, "stages": [{"name": "BGP Session"}]},
    ]
}

UTILISATION_RAW = {
    "type": "table",
    "total_count": 3,
    "items": [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "tx_utilization_average": 0.5,
            "rx_utilization_average": 0.3,
            "tx_bps_average": 500000000,
            "rx_bps_average": 300000000,
            "tx_error_pps_average": 0.0,
            "rx_error_pps_average": 0.0,
            "tx_discard_pps_average": 0.0,
            "rx_discard_pps_average": 0.0,
            "fcs_errors_per_second_average": 0.0,
            "properties": {"system_id": "SYS001", "interface": "ge-0/0/0",
                           "link_role": "spine_leaf", "speed": 1000000000},
        },
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "tx_utilization_average": 0.1,
            "rx_utilization_average": 0.05,
            "tx_bps_average": 100000000,
            "rx_bps_average": 50000000,
            "tx_error_pps_average": 0.0,
            "rx_error_pps_average": 0.0,
            "tx_discard_pps_average": 0.0,
            "rx_discard_pps_average": 0.0,
            "fcs_errors_per_second_average": 0.0,
            "properties": {"system_id": "SYS002", "interface": "ge-0/0/1",
                           "link_role": "to_generic", "speed": 1000000000},
        },
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "tx_utilization_average": 0.8,
            "rx_utilization_average": 0.9,
            "tx_bps_average": 800000000,
            "rx_bps_average": 900000000,
            "tx_error_pps_average": 0.0,
            "rx_error_pps_average": 0.0,
            "tx_discard_pps_average": 0.0,
            "rx_discard_pps_average": 0.0,
            "fcs_errors_per_second_average": 0.0,
            "properties": {"system_id": "SYS003", "interface": "ge-0/0/2",
                           "link_role": "spine_leaf", "speed": 1000000000},
        },
    ],
}


@pytest.mark.asyncio
async def test_utilisation_sorted_by_max_util(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.telemetry.live_data_client.get_probes", new=AsyncMock(return_value=PROBE_LIST_RAW)), \
         patch("tools.telemetry.live_data_client.query_probe_stage", new=AsyncMock(return_value=UTILISATION_RAW)):
        result = await tools["get_interface_utilisation"](
            blueprint_id="bp-001", top_n=0, ctx=ctx
        )
    ifaces = result["interfaces"]
    # SYS003/ge-0/0/2 has max_util_pct=90 (rx 0.9) → should be first
    assert ifaces[0]["interface"] == "ge-0/0/2"
    # SYS001/ge-0/0/0 has max_util_pct=50 → second
    assert ifaces[1]["interface"] == "ge-0/0/0"


@pytest.mark.asyncio
async def test_utilisation_top_n(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.telemetry.live_data_client.get_probes", new=AsyncMock(return_value=PROBE_LIST_RAW)), \
         patch("tools.telemetry.live_data_client.query_probe_stage", new=AsyncMock(return_value=UTILISATION_RAW)):
        result = await tools["get_interface_utilisation"](
            blueprint_id="bp-001", top_n=2, ctx=ctx
        )
    assert result["interface_count"] == 2


@pytest.mark.asyncio
async def test_utilisation_system_id_filter(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.telemetry.live_data_client.get_probes", new=AsyncMock(return_value=PROBE_LIST_RAW)), \
         patch("tools.telemetry.live_data_client.query_probe_stage", new=AsyncMock(return_value=UTILISATION_RAW)):
        result = await tools["get_interface_utilisation"](
            blueprint_id="bp-001", system_id="SYS001", top_n=0, ctx=ctx
        )
    assert all(i["system_id"] == "SYS001" for i in result["interfaces"])


@pytest.mark.asyncio
async def test_utilisation_percentage_conversion(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.telemetry.live_data_client.get_probes", new=AsyncMock(return_value=PROBE_LIST_RAW)), \
         patch("tools.telemetry.live_data_client.query_probe_stage", new=AsyncMock(return_value=UTILISATION_RAW)):
        result = await tools["get_interface_utilisation"](
            blueprint_id="bp-001", system_id="SYS001", top_n=0, ctx=ctx
        )
    iface = result["interfaces"][0]
    assert iface["tx_util_pct"] == pytest.approx(50.0, rel=1e-3)
    assert iface["rx_util_pct"] == pytest.approx(30.0, rel=1e-3)


@pytest.mark.asyncio
async def test_utilisation_probe_not_found(tools):
    session = make_session()
    ctx = make_ctx([session])
    no_traffic = {"items": [{"id": "x", "label": "BGP Monitoring", "state": "operational",
                             "stages": [{"name": "BGP Session"}]}]}
    with patch("tools.telemetry.live_data_client.get_probes", new=AsyncMock(return_value=no_traffic)):
        result = await tools["get_interface_utilisation"](
            blueprint_id="bp-001", ctx=ctx
        )
    assert "error" in result
    assert "Device Traffic" in result["error"]


@pytest.mark.asyncio
async def test_utilisation_interface_filter(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch("tools.telemetry.live_data_client.get_probes", new=AsyncMock(return_value=PROBE_LIST_RAW)), \
         patch("tools.telemetry.live_data_client.query_probe_stage", new=AsyncMock(return_value=UTILISATION_RAW)):
        result = await tools["get_interface_utilisation"](
            blueprint_id="bp-001", interface="ge-0/0/1", top_n=0, ctx=ctx
        )
    assert result["interface_count"] == 1
    assert result["interfaces"][0]["interface"] == "ge-0/0/1"


# ─────────────────────────────────────────────────────────────────────────────
# get_system_telemetry
# ─────────────────────────────────────────────────────────────────────────────

def _resource_util(system_id, cpu, mem):
    return {
        "items": [
            {
                "system_id": system_id,
                "type": "resource_util",
                "key": "system_cpu_utilization",
                "actual": {"value": str(cpu)},
                "last_fetched_at": "2026-01-01T00:00:00Z",
            },
            {
                "system_id": system_id,
                "type": "resource_util",
                "key": "system_memory_utilization",
                "actual": {"value": str(mem)},
                "last_fetched_at": "2026-01-01T00:00:00Z",
            },
        ]
    }


@pytest.mark.asyncio
async def test_system_telemetry_single_device(tools):
    session = make_session()
    ctx = make_ctx([session])
    with patch(
        "tools.telemetry.live_data_client.get_system_resource_util",
        new=AsyncMock(return_value=_resource_util("SYS001", cpu=15, mem=62)),
    ):
        result = await tools["get_system_telemetry"](system_ids=["SYS001"], ctx=ctx)
    assert result["devices"][0]["cpu_pct"] == 15
    assert result["devices"][0]["memory_pct"] == 62
    assert result["devices"][0]["system_id"] == "SYS001"


@pytest.mark.asyncio
async def test_system_telemetry_sorted_by_cpu(tools):
    session = make_session()
    ctx = make_ctx([session])

    async def mock_resource_util(sess, sid):
        return _resource_util(sid, cpu={"SYS001": 90, "SYS002": 10, "SYS003": 50}[sid], mem=40)

    with patch("tools.telemetry.live_data_client.get_system_resource_util", new=mock_resource_util):
        result = await tools["get_system_telemetry"](
            system_ids=["SYS001", "SYS002", "SYS003"], ctx=ctx
        )
    cpus = [d["cpu_pct"] for d in result["devices"]]
    assert cpus == [90, 50, 10]


@pytest.mark.asyncio
async def test_system_telemetry_partial_error(tools):
    """If one system_id fails, others still succeed."""
    session = make_session()
    ctx = make_ctx([session])
    call_count = 0

    async def mock_resource_util(sess, sid):
        nonlocal call_count
        call_count += 1
        if sid == "BAD":
            raise Exception("HTTP 404")
        return _resource_util(sid, cpu=20, mem=30)

    with patch("tools.telemetry.live_data_client.get_system_resource_util", new=mock_resource_util):
        result = await tools["get_system_telemetry"](
            system_ids=["GOOD", "BAD"], ctx=ctx
        )

    assert any(d["system_id"] == "GOOD" for d in result["devices"])
    assert len(result.get("errors", [])) == 1
    assert result["errors"][0]["system_id"] == "BAD"


@pytest.mark.asyncio
async def test_system_telemetry_no_session(tools):
    ctx = make_ctx([make_session("prod")])
    result = await tools["get_system_telemetry"](
        system_ids=["SYS001"], instance_name="nonexistent", ctx=ctx
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_system_telemetry_multiple_devices_count(tools):
    session = make_session()
    ctx = make_ctx([session])

    async def mock_resource_util(sess, sid):
        return _resource_util(sid, cpu=5, mem=30)

    with patch("tools.telemetry.live_data_client.get_system_resource_util", new=mock_resource_util):
        result = await tools["get_system_telemetry"](
            system_ids=["A", "B", "C"], ctx=ctx
        )
    assert result["device_count"] == 3
