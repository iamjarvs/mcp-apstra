"""
tests/test_verify_data_collection.py

Tests for the data-collection health surface added for setup verification:
  - AnomalyStore.get_collection_status()
  - CounterStore.list_instances()
  - verify_data_collection freshness helpers + per-store checks
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from primitives.anomaly_store import AnomalyStore
from primitives.counter_store import CounterStore, ALL_COUNTER_FIELDS
from tests import verify_data_collection as vdc


def make_anomaly_store() -> AnomalyStore:
    return AnomalyStore(db_path=Path(tempfile.mktemp(suffix=".db")))


def make_counter_store() -> CounterStore:
    return CounterStore(db_path=Path(tempfile.mktemp(suffix=".db")))


def zero_snapshot() -> dict:
    return {field: 0 for field in ALL_COUNTER_FIELDS}


def seed_anomaly(store: AnomalyStore) -> None:
    aid = store.upsert_anomaly(
        "bp1",
        "dc-primary",
        {
            "identity": {"system_id": "SYS1"},
            "anomaly_type": "bgp",
            "device_hostname": "leaf1",
            "detected_at": "2026-01-01T00:00:00+00:00",
        },
    )
    store.insert_event(aid, "2026-01-01T00:00:00+00:00", True, None, "test")
    store.set_poll_state("bp1", "dc-primary", {}, {}, backfill_complete=True)


class TestAnomalyCollectionStatus:
    def test_empty_store(self):
        store = make_anomaly_store()
        status = store.get_collection_status()
        assert status["poll_states"] == []
        assert status["anomaly_count"] == 0
        assert status["event_count"] == 0
        assert status["newest_event"] is None
        store.close()

    def test_populated_store(self):
        store = make_anomaly_store()
        seed_anomaly(store)
        status = store.get_collection_status()
        assert status["anomaly_count"] == 1
        assert status["event_count"] == 1
        assert status["newest_event"] == "2026-01-01T00:00:00+00:00"
        assert len(status["poll_states"]) == 1
        ps = status["poll_states"][0]
        assert ps["blueprint_id"] == "bp1"
        assert ps["instance_name"] == "dc-primary"
        assert ps["backfill_complete"] is True
        assert ps["last_poll_at"] is not None
        store.close()


class TestCounterListInstances:
    def test_empty(self):
        store = make_counter_store()
        assert store.list_instances() == []
        store.close()

    def test_distinct_sorted(self):
        store = make_counter_store()
        for inst in ["dc-b", "dc-a", "dc-a"]:
            iid = store.upsert_interface(inst, "SYS1", "ge-0/0/0")
            store.insert_snapshot(iid, "2026-01-01T00:00:00Z", zero_snapshot())
        assert store.list_instances() == ["dc-a", "dc-b"]
        store.close()


class TestFreshnessHelpers:
    def test_parse_iso_variants(self):
        assert vdc._parse_iso(None) is None
        assert vdc._parse_iso("not-a-date") is None
        parsed = vdc._parse_iso("2026-01-01T00:00:00Z")
        assert parsed is not None and parsed.tzinfo is not None

    def test_age_minutes(self):
        now = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
        assert vdc._age_minutes("2026-01-01T00:30:00+00:00", now) == pytest.approx(30.0)
        assert vdc._age_minutes(None, now) is None


class TestCollectionChecks:
    def test_missing_store_is_unhealthy(self):
        now = datetime.now(timezone.utc)
        missing = Path(tempfile.mktemp(suffix=".db"))
        ok, status, _lines = vdc.check_anomaly_collection(missing, 15.0, now)
        assert ok is False
        assert status["present"] is False

    def test_fresh_anomaly_is_healthy(self):
        store = make_anomaly_store()
        path = Path(store._path)
        seed_anomaly(store)
        store.close()
        # set_poll_state stamps last_poll_at with the current time.
        ok, _status, _lines = vdc.check_anomaly_collection(path, 15.0, datetime.now(timezone.utc))
        assert ok is True

    def test_stale_anomaly_is_unhealthy(self):
        store = make_anomaly_store()
        path = Path(store._path)
        seed_anomaly(store)
        store.close()
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        ok, _status, _lines = vdc.check_anomaly_collection(path, 15.0, future)
        assert ok is False

    def test_counter_coverage_fresh(self):
        store = make_counter_store()
        path = Path(store._path)
        iid = store.upsert_interface("dc", "SYS1", "ge-0/0/0")
        recent = datetime.now(timezone.utc).isoformat()
        store.insert_snapshot(iid, recent, zero_snapshot())
        store.close()
        ok, _status, _lines = vdc.check_counter_collection(path, 15.0, datetime.now(timezone.utc))
        assert ok is True
