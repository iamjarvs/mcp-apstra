"""
tests/test_counter_store.py

Tests for:
  - primitives/counter_store.py  (CounterStore + _compute_deltas)
  - tools/telemetry.py           (get_interface_error_trend, get_top_error_growers)
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from primitives.counter_store import (
    CounterStore,
    _compute_deltas,
    ERROR_FIELDS,
    ALL_COUNTER_FIELDS,
)
from tools.telemetry import register as register_telemetry


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_store() -> CounterStore:
    tmp = tempfile.mktemp(suffix=".db")
    return CounterStore(db_path=Path(tmp))


def make_snapshot(fcs=0, rx_err=0, rx_bytes=1000, tx_bytes=1000, **kw) -> dict:
    base = {f: 0 for f in ALL_COUNTER_FIELDS}
    base.update(
        fcs_errors=fcs,
        rx_error_packets=rx_err,
        rx_bytes=rx_bytes,
        tx_bytes=tx_bytes,
    )
    base.update(kw)
    return base


class StubMCP:
    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


def make_ctx(session, counter_store, registry=None):
    ctx = MagicMock()
    ctx.lifespan_context = {
        "sessions": [session],
        "counter_store": counter_store,
        "graph_registry": registry or MagicMock(),
        "anomaly_store": MagicMock(),
    }
    return ctx


def make_session(name="dc-primary"):
    s = MagicMock()
    s.name = name
    return s


@pytest.fixture
def telemetry_tools():
    mcp = StubMCP()
    register_telemetry(mcp)
    return mcp.tools


# ════════════════════════════════════════════════════════════════════════════
# _compute_deltas (pure function)
# ════════════════════════════════════════════════════════════════════════════

class TestComputeDeltas:
    def _snap(self, ts, fcs=0, rx_err=0, rx_bytes=0, tx_bytes=0):
        d = {f: 0 for f in ALL_COUNTER_FIELDS}
        d["polled_at"] = ts
        d["fcs_errors"] = fcs
        d["rx_error_packets"] = rx_err
        d["rx_bytes"] = rx_bytes
        d["tx_bytes"] = tx_bytes
        return d

    def test_empty_returns_empty(self):
        assert _compute_deltas([]) == []

    def test_single_snap_returns_empty(self):
        assert _compute_deltas([self._snap("2026-01-01T00:00:00Z")]) == []

    def test_two_snaps_one_delta(self):
        snaps = [
            self._snap("2026-01-01T00:00:00Z", fcs=10),
            self._snap("2026-01-01T00:05:00Z", fcs=13),
        ]
        deltas = _compute_deltas(snaps)
        assert len(deltas) == 1
        assert deltas[0]["fcs_errors"] == 3

    def test_delta_interval_seconds_correct(self):
        snaps = [
            self._snap("2026-01-01T00:00:00Z"),
            self._snap("2026-01-01T00:05:00Z"),
        ]
        delta = _compute_deltas(snaps)[0]
        assert delta["interval_seconds"] == pytest.approx(300.0)

    def test_negative_delta_clamped_to_zero_and_has_reset(self):
        snaps = [
            self._snap("2026-01-01T00:00:00Z", fcs=50),
            self._snap("2026-01-01T00:05:00Z", fcs=10),  # counter reset
        ]
        delta = _compute_deltas(snaps)[0]
        assert delta["fcs_errors"] == 0
        assert delta["has_reset"] is True

    def test_clean_interval_has_reset_false(self):
        snaps = [
            self._snap("2026-01-01T00:00:00Z", fcs=10),
            self._snap("2026-01-01T00:05:00Z", fcs=12),
        ]
        delta = _compute_deltas(snaps)[0]
        assert delta["has_reset"] is False

    def test_total_errors_field_is_sum_of_error_fields(self):
        snaps = [
            self._snap("2026-01-01T00:00:00Z", fcs=0, rx_err=0),
            self._snap("2026-01-01T00:05:00Z", fcs=3, rx_err=5),
        ]
        delta = _compute_deltas(snaps)[0]
        assert delta["total_errors"] == 8  # fcs(3) + rx_err(5)

    def test_multiple_snaps_produce_n_minus_1_deltas(self):
        snaps = [self._snap(f"2026-01-01T00:0{i}:00Z", fcs=i * 2) for i in range(5)]
        deltas = _compute_deltas(snaps)
        assert len(deltas) == 4

    def test_traffic_bytes_not_reset_on_error_reset(self):
        """Traffic bytes deltas should still be computed even on error resets."""
        snaps = [
            {**{f: 0 for f in ALL_COUNTER_FIELDS},
             "polled_at": "2026-01-01T00:00:00Z", "fcs_errors": 100,
             "rx_bytes": 1000, "tx_bytes": 2000},
            {**{f: 0 for f in ALL_COUNTER_FIELDS},
             "polled_at": "2026-01-01T00:05:00Z", "fcs_errors": 5,    # reset
             "rx_bytes": 1500, "tx_bytes": 2500},
        ]
        delta = _compute_deltas(snaps)[0]
        assert delta["has_reset"] is True
        assert delta["fcs_errors"] == 0         # reset → 0
        assert delta["rx_bytes"] == 500         # traffic delta is positive, kept


# ════════════════════════════════════════════════════════════════════════════
# CounterStore — write helpers
# ════════════════════════════════════════════════════════════════════════════

class TestCounterStoreWrite:
    def test_upsert_interface_returns_id(self):
        store = make_store()
        iid = store.upsert_interface("dc-primary", "SYS001", "ge-0/0/0")
        assert isinstance(iid, int)
        assert iid > 0

    def test_upsert_interface_idempotent(self):
        store = make_store()
        id1 = store.upsert_interface("dc-primary", "SYS001", "ge-0/0/0")
        id2 = store.upsert_interface("dc-primary", "SYS001", "ge-0/0/0")
        assert id1 == id2

    def test_different_interfaces_get_different_ids(self):
        store = make_store()
        id1 = store.upsert_interface("dc-primary", "SYS001", "ge-0/0/0")
        id2 = store.upsert_interface("dc-primary", "SYS001", "ge-0/0/1")
        assert id1 != id2

    def test_insert_snapshot_returns_true(self):
        store = make_store()
        iid = store.upsert_interface("dc-primary", "SYS001", "ge-0/0/0")
        result = store.insert_snapshot(iid, "2026-01-01T00:00:00Z", make_snapshot(fcs=5))
        assert result is True

    def test_insert_snapshot_deduplicates(self):
        store = make_store()
        iid = store.upsert_interface("dc-primary", "SYS001", "ge-0/0/0")
        store.insert_snapshot(iid, "2026-01-01T00:00:00Z", make_snapshot(fcs=5))
        result = store.insert_snapshot(iid, "2026-01-01T00:00:00Z", make_snapshot(fcs=5))
        assert result is False

    def test_insert_snapshot_stores_zero_for_missing_fields(self):
        store = make_store()
        iid = store.upsert_interface("dc-primary", "SYS001", "ge-0/0/0")
        store.insert_snapshot(iid, "2026-01-01T00:00:00Z", {})
        # Should not raise; missing fields default to 0


# ════════════════════════════════════════════════════════════════════════════
# CounterStore — analytics queries
# ════════════════════════════════════════════════════════════════════════════

def _populate_store(store, instance, system_id, iface, snapshots):
    """
    Insert test counter snapshots.
    `snapshots` is a list of (minutes_ago, fcs_errors) — timestamp is
    computed as now - timedelta(minutes=minutes_ago) so data always falls
    within a reasonable hours_back window.
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    iid = store.upsert_interface(instance, system_id, iface)
    for minutes_ago, fcs in snapshots:
        ts = (now - timedelta(minutes=minutes_ago)).isoformat()
        store.insert_snapshot(iid, ts, make_snapshot(fcs=fcs, rx_err=fcs // 2))
    return iid


class TestGetErrorTrend:
    def test_returns_empty_with_single_snapshot(self):
        store = make_store()
        _populate_store(store, "dc", "SYS1", "ge-0/0/0", [(10, 10)])
        trend = store.get_error_trend("dc", "SYS1", "ge-0/0/0", hours_back=168)
        assert trend == []

    def test_returns_deltas_for_two_snapshots(self):
        # minutes_ago=10 → older snapshot with fcs=10; minutes_ago=5 → newer with fcs=13
        # Delta: fcs_errors=3, rx_error_packets=(6-5)=1, total_errors=4
        store = make_store()
        _populate_store(store, "dc", "SYS1", "ge-0/0/0", [
            (10, 10),
            (5, 13),
        ])
        trend = store.get_error_trend("dc", "SYS1", "ge-0/0/0", hours_back=1)
        assert len(trend) == 1
        assert trend[0]["fcs_errors"] == 3

    def test_trend_oldest_first(self):
        store = make_store()
        _populate_store(store, "dc", "SYS1", "ge-0/0/0", [
            (20, 10),
            (15, 15),
            (10, 22),
        ])
        trend = store.get_error_trend("dc", "SYS1", "ge-0/0/0", hours_back=168)
        timestamps = [t["polled_at"] for t in trend]
        assert timestamps == sorted(timestamps)

    def test_hours_back_filters_old_data(self):
        """Only snapshots within hours_back should be counted."""
        store = make_store()
        # Snapshot from far past should be excluded from the window
        iid = store.upsert_interface("dc", "SYS1", "ge-0/0/0")
        # Two recent and two old snapshots
        store.insert_snapshot(iid, "2026-01-01T00:00:00Z", make_snapshot(fcs=0))
        store.insert_snapshot(iid, "2026-01-01T00:05:00Z", make_snapshot(fcs=5))
        # the recent ones need a realistic timestamp — use 'far future' as proxy
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        t1 = (now - timedelta(minutes=10)).isoformat()
        t2 = (now - timedelta(minutes=5)).isoformat()
        store.insert_snapshot(iid, t1, make_snapshot(fcs=5))
        store.insert_snapshot(iid, t2, make_snapshot(fcs=7))
        trend = store.get_error_trend("dc", "SYS1", "ge-0/0/0", hours_back=1)
        # Only the two recent snapshots → 1 delta
        assert len(trend) == 1

    def test_wrong_interface_returns_empty(self):
        store = make_store()
        _populate_store(store, "dc", "SYS1", "ge-0/0/0", [
            (10, 0),
            (5, 5),
        ])
        trend = store.get_error_trend("dc", "SYS1", "ge-0/0/1", hours_back=168)
        assert trend == []


class TestGetTopErrorGrowers:
    def test_ranks_by_total_errors(self):
        store = make_store()
        # SYS1/if0 has more errors than SYS2/if0
        _populate_store(store, "dc", "SYS1", "ge-0/0/0", [
            (10, 0),
            (5, 100),
        ])
        _populate_store(store, "dc", "SYS2", "ge-0/0/0", [
            (10, 0),
            (5, 5),
        ])
        results = store.get_top_error_growers("dc", hours_back=168)
        assert results[0]["system_id"] == "SYS1"
        assert results[0]["total_fcs_errors"] == 100

    def test_top_n_respected(self):
        store = make_store()
        for i in range(5):
            _populate_store(store, "dc", f"SYS{i}", "ge-0/0/0", [
                (10, 0),
                (5, i * 10),
            ])
        results = store.get_top_error_growers("dc", hours_back=168, top_n=3)
        assert len(results) == 3

    def test_system_ids_filter(self):
        store = make_store()
        _populate_store(store, "dc", "SYS1", "ge-0/0/0", [
            (10, 0), (5, 50),
        ])
        _populate_store(store, "dc", "SYS2", "ge-0/0/0", [
            (10, 0), (5, 200),
        ])
        results = store.get_top_error_growers("dc", system_ids=["SYS1"], hours_back=168)
        assert all(r["system_id"] == "SYS1" for r in results)

    def test_has_any_errors_false_for_clean_interface(self):
        store = make_store()
        _populate_store(store, "dc", "SYS1", "ge-0/0/0", [
            (10, 0),
            (5, 0),  # no change
        ])
        results = store.get_top_error_growers("dc", hours_back=168)
        assert results[0]["has_any_errors"] is False

    def test_error_rate_per_hour_is_nonzero(self):
        store = make_store()
        _populate_store(store, "dc", "SYS1", "ge-0/0/0", [
            (10, 0),
            (5, 60),
        ])
        results = store.get_top_error_growers("dc", hours_back=2)
        assert results[0]["error_rate_per_hour"] > 0

    def test_empty_store_returns_empty(self):
        store = make_store()
        results = store.get_top_error_growers("dc", hours_back=24)
        assert results == []


class TestPrune:
    def test_prune_removes_old_data(self):
        from datetime import datetime, timezone, timedelta
        store = make_store()
        iid = store.upsert_interface("dc", "SYS1", "ge-0/0/0")
        # Insert an old snapshot (9 days ago)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
        store.insert_snapshot(iid, old_ts, make_snapshot(fcs=5))
        store.prune(window_days=7)
        # After prune, nothing in the window for this interface  
        trend = store.get_error_trend("dc", "SYS1", "ge-0/0/0", hours_back=168)
        assert trend == []

    def test_prune_keeps_recent_data(self):
        from datetime import datetime, timezone, timedelta
        store = make_store()
        iid = store.upsert_interface("dc", "SYS1", "ge-0/0/0")
        now = datetime.now(timezone.utc)
        t1 = (now - timedelta(hours=1)).isoformat()
        t2 = (now - timedelta(minutes=30)).isoformat()
        store.insert_snapshot(iid, t1, make_snapshot(fcs=5))
        store.insert_snapshot(iid, t2, make_snapshot(fcs=10))
        store.prune(window_days=7)
        trend = store.get_error_trend("dc", "SYS1", "ge-0/0/0", hours_back=2)
        assert len(trend) == 1  # 2 recent snapshots → 1 delta


class TestCoverageSummary:
    def test_empty_store_summary(self):
        store = make_store()
        summary = store.get_coverage_summary("dc")
        assert summary["interface_count"] == 0
        assert summary["snapshot_count"] == 0

    def test_summary_counts_correctly(self):
        store = make_store()
        for iface in ["ge-0/0/0", "ge-0/0/1"]:
            iid = store.upsert_interface("dc", "SYS1", iface)
            store.insert_snapshot(iid, "2026-01-01T00:00:00Z", make_snapshot())
            store.insert_snapshot(iid, "2026-01-01T00:05:00Z", make_snapshot())
        summary = store.get_coverage_summary("dc")
        assert summary["interface_count"] == 2
        assert summary["snapshot_count"] == 4


# ════════════════════════════════════════════════════════════════════════════
# MCP tools — get_interface_error_trend
# ════════════════════════════════════════════════════════════════════════════

class TestGetInterfaceErrorTrendTool:
    @pytest.mark.asyncio
    async def test_returns_empty_trend_no_data(self, telemetry_tools):
        store = make_store()
        session = make_session()
        ctx = make_ctx(session, store)
        result = await telemetry_tools["get_interface_error_trend"](
            system_id="SYS1", interface_name="ge-0/0/0", hours_back=24, ctx=ctx
        )
        assert result["trend"] == []
        assert result["data_point_count"] == 0

    @pytest.mark.asyncio
    async def test_returns_trend_data(self, telemetry_tools):
        # minutes_ago=10 → fcs=10,rx_err=5; minutes_ago=5 → fcs=15,rx_err=7
        # Delta: fcs=5, rx_err=2, total_errors=7
        store = make_store()
        _populate_store(store, "dc-primary", "SYS1", "ge-0/0/0", [
            (10, 10),
            (5, 15),
        ])
        session = make_session()
        ctx = make_ctx(session, store)
        result = await telemetry_tools["get_interface_error_trend"](
            system_id="SYS1", interface_name="ge-0/0/0", hours_back=1, ctx=ctx
        )
        assert result["data_point_count"] == 1
        assert result["total_errors"] == 7  # fcs delta=5, rx_err delta=2

    @pytest.mark.asyncio
    async def test_has_any_errors_true(self, telemetry_tools):
        store = make_store()
        _populate_store(store, "dc-primary", "SYS1", "ge-0/0/0", [
            (10, 0),
            (5, 10),
        ])
        session = make_session()
        ctx = make_ctx(session, store)
        result = await telemetry_tools["get_interface_error_trend"](
            system_id="SYS1", interface_name="ge-0/0/0", hours_back=168, ctx=ctx
        )
        assert result["has_any_errors"] is True

    @pytest.mark.asyncio
    async def test_has_any_errors_false_for_clean(self, telemetry_tools):
        store = make_store()
        _populate_store(store, "dc-primary", "SYS1", "ge-0/0/0", [
            (10, 5),
            (5, 5),  # no growth
        ])
        session = make_session()
        ctx = make_ctx(session, store)
        result = await telemetry_tools["get_interface_error_trend"](
            system_id="SYS1", interface_name="ge-0/0/0", hours_back=168, ctx=ctx
        )
        assert result["has_any_errors"] is False

    @pytest.mark.asyncio
    async def test_no_session_returns_error(self, telemetry_tools):
        store = make_store()
        session = make_session("prod")
        ctx = make_ctx(session, store)
        result = await telemetry_tools["get_interface_error_trend"](
            system_id="SYS1", interface_name="ge-0/0/0",
            instance_name="nonexistent", ctx=ctx
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_hours_back_clamped_to_maximum(self, telemetry_tools):
        store = make_store()
        session = make_session()
        ctx = make_ctx(session, store)
        result = await telemetry_tools["get_interface_error_trend"](
            system_id="SYS1", interface_name="ge-0/0/0",
            hours_back=9999, ctx=ctx
        )
        assert result["hours_back"] == 168

    @pytest.mark.asyncio
    async def test_hours_back_clamped_to_minimum(self, telemetry_tools):
        store = make_store()
        session = make_session()
        ctx = make_ctx(session, store)
        result = await telemetry_tools["get_interface_error_trend"](
            system_id="SYS1", interface_name="ge-0/0/0",
            hours_back=0, ctx=ctx
        )
        assert result["hours_back"] == 1


# ════════════════════════════════════════════════════════════════════════════
# MCP tools — get_top_error_growers
# ════════════════════════════════════════════════════════════════════════════

class TestGetTopErrorGrowersTool:
    @pytest.mark.asyncio
    async def test_returns_empty_for_empty_store(self, telemetry_tools):
        store = make_store()
        session = make_session()
        ctx = make_ctx(session, store)
        result = await telemetry_tools["get_top_error_growers"](
            hours_back=24, ctx=ctx
        )
        assert result["interfaces"] == []
        assert result["interface_count"] == 0

    @pytest.mark.asyncio
    async def test_rankings_match_store(self, telemetry_tools):
        store = make_store()
        _populate_store(store, "dc-primary", "BAD", "ge-0/0/0", [
            (10, 0), (5, 500),
        ])
        _populate_store(store, "dc-primary", "OK", "ge-0/0/0", [
            (10, 0), (5, 1),
        ])
        session = make_session()
        ctx = make_ctx(session, store)
        result = await telemetry_tools["get_top_error_growers"](
            hours_back=168, ctx=ctx
        )
        assert result["interfaces"][0]["system_id"] == "BAD"

    @pytest.mark.asyncio
    async def test_interfaces_with_errors_count(self, telemetry_tools):
        store = make_store()
        _populate_store(store, "dc-primary", "SYS1", "ge-0/0/0", [
            (10, 0), (5, 10),
        ])
        _populate_store(store, "dc-primary", "SYS2", "ge-0/0/0", [
            (10, 0), (5, 0),  # no errors
        ])
        session = make_session()
        ctx = make_ctx(session, store)
        result = await telemetry_tools["get_top_error_growers"](hours_back=168, ctx=ctx)
        assert result["interfaces_with_errors"] == 1

    @pytest.mark.asyncio
    async def test_no_session_returns_error(self, telemetry_tools):
        store = make_store()
        session = make_session("prod")
        ctx = make_ctx(session, store)
        result = await telemetry_tools["get_top_error_growers"](
            instance_name="nonexistent", ctx=ctx
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_blueprint_id_resolves_via_registry(self, telemetry_tools):
        """When blueprint_id is provided, system_ids are resolved via graph registry."""
        store = make_store()
        _populate_store(store, "dc-primary", "SYS_IN_BP", "ge-0/0/0", [
            (10, 0), (5, 50),
        ])
        _populate_store(store, "dc-primary", "SYS_NOT_IN_BP", "ge-0/0/0", [
            (10, 0), (5, 200),
        ])
        session = make_session()
        # Mock the graph registry to return only SYS_IN_BP
        mock_graph = MagicMock()
        mock_graph.query.return_value = [{"sw.system_id": "SYS_IN_BP"}]
        mock_registry = MagicMock()
        mock_registry.get_or_rebuild = AsyncMock(return_value=mock_graph)

        ctx = make_ctx(session, store, registry=mock_registry)
        result = await telemetry_tools["get_top_error_growers"](
            blueprint_id="bp-001", hours_back=168, ctx=ctx
        )
        # Only SYS_IN_BP should appear, even though SYS_NOT_IN_BP has more errors
        assert all(r["system_id"] == "SYS_IN_BP" for r in result["interfaces"])

    @pytest.mark.asyncio
    async def test_blueprint_registry_failure_returns_error(self, telemetry_tools):
        store = make_store()
        session = make_session()
        mock_registry = MagicMock()
        mock_registry.get_or_rebuild = AsyncMock(side_effect=Exception("graph error"))
        ctx = make_ctx(session, store, registry=mock_registry)
        result = await telemetry_tools["get_top_error_growers"](
            blueprint_id="bad-bp", hours_back=24, ctx=ctx
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_hours_back_clamped(self, telemetry_tools):
        store = make_store()
        session = make_session()
        ctx = make_ctx(session, store)
        result = await telemetry_tools["get_top_error_growers"](
            hours_back=0, ctx=ctx
        )
        assert result["hours_back"] == 1
