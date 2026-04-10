"""
tests/test_anomaly_timeline.py

Tests for:
  - primitives/anomaly_store.py  (AnomalyStore)
  - handlers/anomaly_poller.py   (_counts_changed, poller logic)
  - tools/anomaly_timeline.py    (MCP tools)
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from primitives.anomaly_store import AnomalyStore, WINDOW_DAYS
from handlers.anomaly_poller import _counts_changed


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_store() -> AnomalyStore:
    """Return an in-process AnomalyStore backed by a temp file."""
    tmp = tempfile.mktemp(suffix=".db")
    return AnomalyStore(db_path=Path(tmp))


BP      = "bp-001"
INST    = "dc-primary"
BGP_A   = {
    "anomaly_type":    "bgp",
    "device_hostname": "Leaf1",
    "role":            "leaf",
    "identity": {
        "anomaly_type": "bgp",
        "system_id":    "AABBCC001122",
        "source_ip":    "10.0.0.1",
        "destination_ip": "10.0.0.2",
    },
    "expected":    {"value": "up"},
    "actual":      {"value": "down"},
    "detected_at": "2026-04-03T12:00:00Z",
}
BGP_B = {
    "anomaly_type":    "bgp",
    "device_hostname": "Leaf2",
    "role":            "leaf",
    "identity": {
        "anomaly_type": "bgp",
        "system_id":    "AABBCC003344",
        "source_ip":    "10.0.0.3",
        "destination_ip": "10.0.0.4",
    },
    "expected":    {"value": "up"},
    "actual":      {"value": "down"},
    "detected_at": "2026-04-04T08:00:00Z",
}


# ════════════════════════════════════════════════════════════════════════════
# AnomalyStore — core write / read
# ════════════════════════════════════════════════════════════════════════════

class TestAnomalyStoreUpsert:
    def test_upsert_returns_id(self):
        store = make_store()
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        assert isinstance(aid, int)
        store.close()

    def test_upsert_idempotent(self):
        store = make_store()
        id1 = store.upsert_anomaly(BP, INST, BGP_A)
        id2 = store.upsert_anomaly(BP, INST, BGP_A)
        assert id1 == id2
        store.close()

    def test_different_identities_get_different_ids(self):
        store = make_store()
        id1 = store.upsert_anomaly(BP, INST, BGP_A)
        id2 = store.upsert_anomaly(BP, INST, BGP_B)
        assert id1 != id2
        store.close()

    def test_same_identity_different_blueprint_is_separate(self):
        store = make_store()
        id1 = store.upsert_anomaly("bp-001", INST, BGP_A)
        id2 = store.upsert_anomaly("bp-002", INST, BGP_A)
        assert id1 != id2
        store.close()


class TestAnomalyStoreEvents:
    def test_insert_event_returns_true_on_new(self):
        store = make_store()
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        wrote = store.insert_event(aid, "2026-04-03T12:00:00Z", raised=True,
                                   actual={"value": "down"}, source="trace")
        assert wrote is True
        store.close()

    def test_insert_event_deduplicates(self):
        store = make_store()
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        store.insert_event(aid, "2026-04-03T12:00:00Z", raised=True,
                           actual=None, source="trace")
        wrote = store.insert_event(aid, "2026-04-03T12:00:00Z", raised=True,
                                   actual=None, source="trace")
        assert wrote is False
        store.close()

    def test_raise_and_clear_both_stored(self):
        store = make_store()
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        store.insert_event(aid, "2026-04-03T12:00:00Z", raised=True,  actual=None, source="test")
        store.insert_event(aid, "2026-04-03T13:00:00Z", raised=False, actual=None, source="test")
        events = store.query_events(BP)
        assert len(events) == 2
        raised_flags = {e["raised"] for e in events}
        assert raised_flags == {True, False}
        store.close()


# ════════════════════════════════════════════════════════════════════════════
# AnomalyStore — query
# ════════════════════════════════════════════════════════════════════════════

class TestAnomalyStoreQuery:
    def _populated_store(self) -> AnomalyStore:
        store = make_store()
        aid_a = store.upsert_anomaly(BP, INST, BGP_A)
        aid_b = store.upsert_anomaly(BP, INST, BGP_B)
        store.insert_event(aid_a, "2026-04-03T12:00:00Z", raised=True,  actual={"value": "down"}, source="t")
        store.insert_event(aid_a, "2026-04-04T12:00:00Z", raised=False, actual=None, source="t")
        store.insert_event(aid_b, "2026-04-04T08:00:00Z", raised=True,  actual={"value": "down"}, source="t")
        return store

    def test_query_returns_all(self):
        store = self._populated_store()
        events = store.query_events(BP)
        assert len(events) == 3
        store.close()

    def test_query_raised_only(self):
        store = self._populated_store()
        events = store.query_events(BP, raised_only=True)
        assert all(e["raised"] for e in events)
        assert len(events) == 2
        store.close()

    def test_query_by_device(self):
        store = self._populated_store()
        events = store.query_events(BP, device="Leaf1")
        assert all(e["device"] == "Leaf1" for e in events)
        store.close()

    def test_query_by_type(self):
        store = self._populated_store()
        events = store.query_events(BP, anomaly_type="bgp")
        assert len(events) == 3
        store.close()

    def test_query_since_filters_by_time(self):
        store = self._populated_store()
        events = store.query_events(BP, since="2026-04-04T00:00:00Z")
        # Only events on Apr 4 or later
        for e in events:
            assert e["timestamp"] >= "2026-04-04"
        store.close()

    def test_query_events_include_expected_and_identity(self):
        store = self._populated_store()
        events = store.query_events(BP, device="Leaf1", raised_only=True)
        assert len(events) == 1
        assert events[0]["expected"] == {"value": "up"}
        assert "system_id" in events[0]["identity"]
        store.close()


# ════════════════════════════════════════════════════════════════════════════
# AnomalyStore — currently active
# ════════════════════════════════════════════════════════════════════════════

class TestAnomalyStoreActive:
    def test_raised_anomaly_is_active(self):
        store = make_store()
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        store.insert_event(aid, "2026-04-03T12:00:00Z", raised=True, actual=None, source="t")
        active = store.get_currently_active(BP)
        assert len(active) == 1
        assert active[0]["anomaly_type"] == "bgp"
        store.close()

    def test_cleared_anomaly_is_not_active(self):
        store = make_store()
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        store.insert_event(aid, "2026-04-03T12:00:00Z", raised=True,  actual=None, source="t")
        store.insert_event(aid, "2026-04-03T13:00:00Z", raised=False, actual=None, source="t")
        active = store.get_currently_active(BP)
        assert len(active) == 0
        store.close()

    def test_multiple_anomalies_mixed_state(self):
        store = make_store()
        aid_a = store.upsert_anomaly(BP, INST, BGP_A)
        aid_b = store.upsert_anomaly(BP, INST, BGP_B)
        store.insert_event(aid_a, "2026-04-03T12:00:00Z", raised=True,  actual=None, source="t")
        store.insert_event(aid_a, "2026-04-03T14:00:00Z", raised=False, actual=None, source="t")  # cleared
        store.insert_event(aid_b, "2026-04-04T08:00:00Z", raised=True,  actual=None, source="t")  # still up
        active = store.get_currently_active(BP)
        assert len(active) == 1
        assert active[0]["device"] == "Leaf2"
        store.close()


# ════════════════════════════════════════════════════════════════════════════
# AnomalyStore — prune
# ════════════════════════════════════════════════════════════════════════════

class TestAnomalyStorePrune:
    def test_prune_removes_old_events(self):
        store = make_store()
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        # Insert an event older than 7 days (way in the past)
        store.insert_event(aid, "2020-01-01T00:00:00Z", raised=True, actual=None, source="t")
        # Insert a recent event
        store.insert_event(aid, "2026-04-09T12:00:00Z", raised=True, actual=None, source="t")
        store.prune()
        events = store.query_events(BP)
        assert all(e["timestamp"] >= "2026" for e in events)
        store.close()

    def test_prune_removes_orphaned_anomaly(self):
        store = make_store()
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        # Only old events
        store.insert_event(aid, "2020-01-01T00:00:00Z", raised=True, actual=None, source="t")
        store.prune()
        # The anomaly row should be cleaned up too
        active = store.get_currently_active(BP)
        assert len(active) == 0
        store.close()


# ════════════════════════════════════════════════════════════════════════════
# AnomalyStore — poll state
# ════════════════════════════════════════════════════════════════════════════

class TestPollState:
    def test_default_state(self):
        store = make_store()
        state = store.get_poll_state(BP, INST)
        assert state["backfill_complete"] is False
        assert state["last_counts"] == {}
        store.close()

    def test_set_and_get_poll_state(self):
        store = make_store()
        store.set_poll_state(BP, INST, {"bgp": 4}, {"key1": {}}, backfill_complete=True)
        state = store.get_poll_state(BP, INST)
        assert state["backfill_complete"] is True
        assert state["last_counts"] == {"bgp": 4}
        store.close()

    def test_update_does_not_lose_backfill_flag(self):
        store = make_store()
        store.set_poll_state(BP, INST, {}, {}, backfill_complete=True)
        # Update without specifying backfill_complete
        store.set_poll_state(BP, INST, {"bgp": 5}, {})
        state = store.get_poll_state(BP, INST)
        assert state["backfill_complete"] is True
        store.close()

    def test_is_ready(self):
        store = make_store()
        assert store.is_ready(BP, INST) is False
        store.set_poll_state(BP, INST, {}, {}, backfill_complete=True)
        assert store.is_ready(BP, INST) is True
        store.close()


# ════════════════════════════════════════════════════════════════════════════
# AnomalyStore — summary
# ════════════════════════════════════════════════════════════════════════════

class TestAnomalyStoreSummary:
    def test_summary_structure(self):
        store = make_store()
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        store.insert_event(aid, "2026-04-08T10:00:00Z", raised=True, actual=None, source="t")
        s = store.get_summary(BP)
        assert s["total_identities"] == 1
        assert s["currently_active"] == 1
        assert s["total_events"] == 1
        assert len(s["by_type"]) == 1
        assert s["by_type"][0]["anomaly_type"] == "bgp"
        store.close()


# ════════════════════════════════════════════════════════════════════════════
# _counts_changed helper
# ════════════════════════════════════════════════════════════════════════════

class TestCountsChanged:
    def test_no_change(self):
        assert _counts_changed({"bgp": 4, "mac": 2}, {"bgp": 4, "mac": 2}) is False

    def test_count_increased(self):
        assert _counts_changed({"bgp": 4}, {"bgp": 5}) is True

    def test_count_decreased(self):
        assert _counts_changed({"bgp": 4}, {"bgp": 3}) is True

    def test_new_type_appeared(self):
        assert _counts_changed({"bgp": 4}, {"bgp": 4, "mac": 1}) is True

    def test_type_disappeared(self):
        assert _counts_changed({"bgp": 4, "mac": 1}, {"bgp": 4}) is True

    def test_empty_both(self):
        assert _counts_changed({}, {}) is False


# ════════════════════════════════════════════════════════════════════════════
# Poller — backfill integration (mocked API)
# ════════════════════════════════════════════════════════════════════════════

class TestBackfill:
    async def test_backfill_populates_store(self):
        from handlers.anomaly_poller import _backfill

        store = make_store()
        session = MagicMock()
        session.name = INST

        snapshot_item = {**BGP_A, "raised": True}
        trace_item = {
            **BGP_A,
            "raised":      True,
            "detected_at": "2026-04-03T12:00:00Z",
            "actual":      {"value": "down"},
        }

        with (
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_snapshot",
                  new_callable=AsyncMock,
                  return_value={"items": [snapshot_item]}),
            patch("handlers.anomaly_poller.live_data_client.get_anomalies",
                  new_callable=AsyncMock,
                  return_value={"items": [snapshot_item]}),
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_trace",
                  new_callable=AsyncMock,
                  return_value={"items": [trace_item]}),
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_counts",
                  new_callable=AsyncMock,
                  return_value={"counts": {"bgp": [{"count": 1, "timestamp": "2026-04-10T00:00:00Z"}]}}),
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_snapshot",
                  new_callable=AsyncMock,
                  return_value={"items": [snapshot_item]}),
        ):
            await _backfill(session, BP, store)

        assert store.is_ready(BP, INST)
        active = store.get_currently_active(BP)
        assert len(active) >= 1
        assert active[0]["anomaly_type"] == "bgp"
        store.close()

    async def test_backfill_skipped_if_already_complete(self):
        from handlers.anomaly_poller import _backfill

        store = make_store()
        store.set_poll_state(BP, INST, {}, {}, backfill_complete=True)
        session = MagicMock()
        session.name = INST

        with patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_snapshot",
                   new_callable=AsyncMock) as mock_snap:
            await _backfill(session, BP, store)
            mock_snap.assert_not_called()

        store.close()

    async def test_persistent_anomaly_gets_synthetic_raise_event(self):
        """BGP/cabling anomalies with detected_at pre-dating the window have no
        trace events within 7 days.  The poller should write a synthetic raise."""
        from handlers.anomaly_poller import _backfill

        store = make_store()
        session = MagicMock()
        session.name = INST

        persistent = {
            **BGP_A,
            "detected_at": "2026-01-29T20:00:24Z",  # 70 days ago — before trace window
        }

        with (
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_snapshot",
                  new_callable=AsyncMock, return_value={"items": [persistent]}),
            patch("handlers.anomaly_poller.live_data_client.get_anomalies",
                  new_callable=AsyncMock, return_value={"items": [persistent]}),
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_trace",
                  new_callable=AsyncMock, return_value={"items": []}),
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_counts",
                  new_callable=AsyncMock,
                  return_value={"counts": {"bgp": [{"count": 4, "timestamp": "2026-04-10T00:00:00Z"}]}}),
        ):
            await _backfill(session, BP, store)

        active = store.get_currently_active(BP)
        assert any(a["anomaly_type"] == "bgp" for a in active), \
            "persistent BGP anomaly should appear as active via synthetic raise event"
        events = store.query_events(BP, anomaly_type="bgp")
        assert any(e.get("source") == "synthetic_raise" for e in events)
        store.close()


# ════════════════════════════════════════════════════════════════════════════
# _norm_ts helper
# ════════════════════════════════════════════════════════════════════════════

class TestNormTs:
    def test_z_suffix_unchanged(self):
        from handlers.anomaly_poller import _norm_ts
        assert _norm_ts("2026-04-10T12:00:00Z") == "2026-04-10T12:00:00Z"

    def test_plus00_replaced_with_z(self):
        from handlers.anomaly_poller import _norm_ts
        assert _norm_ts("2026-04-10T12:00:00+00:00") == "2026-04-10T12:00:00Z"

    def test_other_suffix_unchanged(self):
        from handlers.anomaly_poller import _norm_ts
        result = _norm_ts("2026-04-10T12:00:00.123456Z")
        assert result.endswith("Z")


# ════════════════════════════════════════════════════════════════════════════
# Poller — incremental poll (mocked)
# ════════════════════════════════════════════════════════════════════════════

class TestIncrementalPoll:
    async def test_no_change_skips_snapshot(self):
        from handlers.anomaly_poller import _incremental_poll

        store = make_store()
        store.set_poll_state(BP, INST, {"bgp": 4}, {}, backfill_complete=True)
        session = MagicMock()
        session.name = INST

        with (
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_counts",
                  new_callable=AsyncMock,
                  return_value={"counts": {"bgp": [{"count": 4, "timestamp": "2026-04-10T00:00:00Z"}]}}),
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_snapshot",
                  new_callable=AsyncMock) as mock_snap,
        ):
            await _incremental_poll(session, BP, store)
            mock_snap.assert_not_called()

        store.close()

    async def test_count_change_triggers_snapshot(self):
        from handlers.anomaly_poller import _incremental_poll

        store = make_store()
        # Prev count: bgp=4. New count: bgp=5 (one more anomaly raised)
        store.set_poll_state(BP, INST, {"bgp": 4}, {}, backfill_complete=True)
        session = MagicMock()
        session.name = INST

        new_item = {**BGP_A}

        with (
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_counts",
                  new_callable=AsyncMock,
                  return_value={"counts": {"bgp": [{"count": 5, "timestamp": "2026-04-10T00:00:00Z"}]}}),
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_snapshot",
                  new_callable=AsyncMock,
                  return_value={"items": [new_item]}) as mock_snap,
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_trace",
                  new_callable=AsyncMock,
                  return_value={"items": []}),
        ):
            await _incremental_poll(session, BP, store)
            mock_snap.assert_called_once()

        store.close()

    async def test_new_anomaly_written_to_store(self):
        from handlers.anomaly_poller import _incremental_poll

        store = make_store()
        store.set_poll_state(BP, INST, {"bgp": 4}, {}, backfill_complete=True)
        session = MagicMock()
        session.name = INST

        new_item = {**BGP_A}

        with (
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_counts",
                  new_callable=AsyncMock,
                  return_value={"counts": {"bgp": [{"count": 5, "timestamp": "2026-04-10T00:00:00Z"}]}}),
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_snapshot",
                  new_callable=AsyncMock,
                  return_value={"items": [new_item]}),
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_trace",
                  new_callable=AsyncMock,
                  return_value={"items": []}),
        ):
            await _incremental_poll(session, BP, store)

        active = store.get_currently_active(BP)
        assert len(active) == 1
        assert active[0]["device"] == "Leaf1"
        store.close()

    async def test_cleared_anomaly_removed_from_active(self):
        from handlers.anomaly_poller import _incremental_poll

        store = make_store()
        # Pre-seed: BGP_A is active in the stored snapshot
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        store.insert_event(aid, "2026-04-03T12:00:00Z", raised=True, actual=None, source="trace")

        prev_snap = {json.dumps(BGP_A["identity"], sort_keys=True): BGP_A}
        store.set_poll_state(BP, INST, {"bgp": 4}, prev_snap, backfill_complete=True)
        session = MagicMock()
        session.name = INST

        # New snapshot: BGP_A is gone
        with (
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_counts",
                  new_callable=AsyncMock,
                  return_value={"counts": {"bgp": [{"count": 3, "timestamp": "2026-04-10T00:00:00Z"}]}}),
            patch("handlers.anomaly_poller.live_data_client.get_anomaly_history_snapshot",
                  new_callable=AsyncMock,
                  return_value={"items": []}),
        ):
            await _incremental_poll(session, BP, store)

        active = store.get_currently_active(BP)
        assert len(active) == 0
        store.close()


# ════════════════════════════════════════════════════════════════════════════
# MCP tools (tools/anomaly_timeline.py)
# ════════════════════════════════════════════════════════════════════════════

def make_ctx(store):
    ctx = MagicMock()
    ctx.lifespan_context = {
        "anomaly_store": store,
        "sessions": [MagicMock(name=INST)],
    }
    ctx.lifespan_context["sessions"][0].name = INST
    return ctx


class TestGetAnomalyEventsTool:
    async def test_returns_events(self):
        from tools.anomaly_timeline import register
        from fastmcp import FastMCP

        store = make_store()
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        store.insert_event(aid, "2026-04-09T12:00:00Z", raised=True, actual={"value": "down"}, source="t")

        # Import and call the handler function directly rather than going through MCP
        from tools.anomaly_timeline import register as reg_fn

        # Build a minimal stub MCP to capture the registered function
        captured = {}
        class StubMCP:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        reg_fn(StubMCP())
        ctx = make_ctx(store)
        result = await captured["get_anomaly_events"](
            blueprint_id=BP, hours_back=48, ctx=ctx
        )
        assert result["event_count"] >= 1
        assert result["events"][0]["anomaly_type"] == "bgp"
        store.close()

    async def test_no_store_returns_error(self):
        from tools.anomaly_timeline import register

        captured = {}
        class StubMCP:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        register(StubMCP())
        ctx = MagicMock()
        ctx.lifespan_context = {}
        result = await captured["get_anomaly_events"](blueprint_id=BP, ctx=ctx)
        assert "error" in result

    async def test_raised_only_filter(self):
        from tools.anomaly_timeline import register

        store = make_store()
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        store.insert_event(aid, "2026-04-09T10:00:00Z", raised=True,  actual=None, source="t")
        store.insert_event(aid, "2026-04-09T11:00:00Z", raised=False, actual=None, source="t")

        captured = {}
        class StubMCP:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        register(StubMCP())
        ctx = make_ctx(store)
        result = await captured["get_anomaly_events"](
            blueprint_id=BP, hours_back=48, raised_only=True, ctx=ctx
        )
        assert all(e["raised"] for e in result["events"])
        store.close()


class TestGetActiveTool:
    async def test_returns_active_anomalies(self):
        from tools.anomaly_timeline import register

        store = make_store()
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        store.insert_event(aid, "2026-04-09T10:00:00Z", raised=True, actual=None, source="t")

        captured = {}
        class StubMCP:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        register(StubMCP())
        ctx = make_ctx(store)
        result = await captured["get_active_anomalies_from_store"](blueprint_id=BP, ctx=ctx)
        assert result["active_count"] == 1
        assert result["anomalies"][0]["device"] == "Leaf1"
        store.close()


class TestGetSummaryTool:
    async def test_summary_includes_by_type(self):
        from tools.anomaly_timeline import register

        store = make_store()
        aid = store.upsert_anomaly(BP, INST, BGP_A)
        store.insert_event(aid, "2026-04-09T10:00:00Z", raised=True, actual=None, source="t")
        store.set_poll_state(BP, INST, {}, {}, backfill_complete=True)

        captured = {}
        class StubMCP:
            def tool(self):
                def decorator(fn):
                    captured[fn.__name__] = fn
                    return fn
                return decorator

        register(StubMCP())
        ctx = make_ctx(store)
        result = await captured["get_anomaly_summary"](blueprint_id=BP, ctx=ctx)
        assert result["total_identities"] == 1
        assert result["backfill_ready"] is True
        assert len(result["by_type"]) == 1
        store.close()
