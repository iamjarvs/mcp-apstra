"""
tests/test_anomaly_analytics.py

Tests for:
  - primitives/anomaly_clustering.py
  - primitives/anomaly_store.py  (new analytics query methods)
  - tools/anomaly_analytics.py   (MCP tools)
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from primitives.anomaly_store import AnomalyStore
from primitives.anomaly_clustering import (
    OSI_LAYER,
    _parse_iso_seconds,
    _strip_type_prefix,
    _identity_key,
    cluster_raises,
    deduplicate_cluster,
    tag_osi_layer,
    score_root_cause,
    build_causal_chain,
    enrich_cluster,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_store() -> AnomalyStore:
    tmp = tempfile.mktemp(suffix=".db")
    return AnomalyStore(db_path=Path(tmp))


BP   = "bp-001"
INST = "dc-primary"


def _raise(atype, device, ts, identity=None, expected=None, role=None):
    """Build a minimal raise-event dict as returned by get_raises_in_window."""
    return {
        "event_id":     1,
        "anomaly_id":   1,
        "timestamp":    ts,
        "raised":       True,
        "source":       "test",
        "actual":       None,
        "anomaly_type": atype,
        "device":       device,
        "role":         role,
        "identity":     identity or {"system_id": f"SYS_{device}_{atype}", "interface": "ge-0/0/0"},
        "expected":     expected or {"value": "up"},
        "first_detected": ts,
        "instance":     INST,
    }


def _make_anomaly(atype, device, detected_at="2026-04-01T10:00:00Z"):
    # Include atype in system_id so each (type, device) pair has a unique
    # identity_json in the store, avoiding UNIQUE constraint collisions.
    return {
        "anomaly_type":    atype,
        "device_hostname": device,
        "role":            "spine_leaf",
        "identity": {"system_id": f"SYS_{device}_{atype}", "interface": "ge-0/0/0"},
        "expected":    {"value": "up"},
        "actual":      {"value": "down"},
        "detected_at": detected_at,
    }


# ── StubMCP ──────────────────────────────────────────────────────────────────

class StubMCP:
    """Captures decorated tool functions by name."""
    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


def make_ctx(store):
    ctx = MagicMock()
    ctx.lifespan_context = {
        "anomaly_store": store,
        "sessions": [MagicMock()],
    }
    ctx.lifespan_context["sessions"][0].name = INST
    return ctx


# ════════════════════════════════════════════════════════════════════════════
# anomaly_clustering — utility helpers
# ════════════════════════════════════════════════════════════════════════════

class TestParseIsoSeconds:
    def test_z_suffix(self):
        t = _parse_iso_seconds("2026-04-10T12:00:00Z")
        assert t > 0

    def test_plus00_suffix(self):
        t1 = _parse_iso_seconds("2026-04-10T12:00:00Z")
        t2 = _parse_iso_seconds("2026-04-10T12:00:00+00:00")
        assert t1 == t2

    def test_with_microseconds(self):
        t = _parse_iso_seconds("2026-04-10T12:00:00.123456Z")
        assert t > 0

    def test_invalid_returns_zero(self):
        assert _parse_iso_seconds("not-a-timestamp") == 0.0

    def test_none_returns_zero(self):
        assert _parse_iso_seconds(None) == 0.0

    def test_ordering_correct(self):
        earlier = _parse_iso_seconds("2026-04-10T11:00:00Z")
        later   = _parse_iso_seconds("2026-04-10T12:00:00Z")
        assert later - earlier == pytest.approx(3600.0)


class TestStripTypePrefix:
    def test_removes_anomaly_type_key(self):
        d = {"anomaly_type": "bgp", "system_id": "ABC", "source_ip": "1.2.3.4"}
        assert "anomaly_type" not in _strip_type_prefix(d)
        assert "system_id" in _strip_type_prefix(d)

    def test_no_key_to_remove(self):
        d = {"system_id": "ABC"}
        assert _strip_type_prefix(d) == d

    def test_does_not_mutate_original(self):
        d = {"anomaly_type": "bgp", "x": 1}
        _ = _strip_type_prefix(d)
        assert "anomaly_type" in d


class TestIdentityKey:
    def test_strips_anomaly_type(self):
        a = {"identity": {"anomaly_type": "bgp", "system_id": "X"}}
        b = {"identity": {"system_id": "X"}}
        assert _identity_key(a) == _identity_key(b)

    def test_different_identities_differ(self):
        a = {"identity": {"system_id": "X"}}
        b = {"identity": {"system_id": "Y"}}
        assert _identity_key(a) != _identity_key(b)


# ════════════════════════════════════════════════════════════════════════════
# anomaly_clustering — cluster_raises
# ════════════════════════════════════════════════════════════════════════════

class TestClusterRaises:
    def test_empty_input(self):
        assert cluster_raises([]) == []

    def test_single_event_above_min_size(self):
        r = [_raise("bgp", "Leaf1", "2026-04-10T12:00:00Z")]
        clusters = cluster_raises(r, idle_gap_seconds=60, min_size=1)
        assert len(clusters) == 1

    def test_single_event_below_min_size(self):
        r = [_raise("bgp", "Leaf1", "2026-04-10T12:00:00Z")]
        clusters = cluster_raises(r, idle_gap_seconds=60, min_size=2)
        assert len(clusters) == 0

    def test_two_close_events_form_one_cluster(self):
        raises = [
            _raise("bgp",      "Leaf1", "2026-04-10T12:00:00Z"),
            _raise("cabling",  "Leaf1", "2026-04-10T12:00:05Z"),
        ]
        clusters = cluster_raises(raises, idle_gap_seconds=60, min_size=1)
        assert len(clusters) == 1
        assert len(clusters[0]) == 2

    def test_gap_splits_into_two_clusters(self):
        raises = [
            _raise("bgp",     "Leaf1", "2026-04-10T12:00:00Z"),
            _raise("cabling", "Leaf1", "2026-04-10T13:00:00Z"),  # 1 hour later
        ]
        clusters = cluster_raises(raises, idle_gap_seconds=60, min_size=1)
        assert len(clusters) == 2

    def test_three_burst_two_isolated(self):
        raises = [
            _raise("bgp",       "Leaf1", "2026-04-10T12:00:00Z"),
            _raise("cabling",   "Leaf1", "2026-04-10T12:00:02Z"),
            _raise("interface", "Leaf1", "2026-04-10T12:00:04Z"),
            _raise("route",     "Leaf1", "2026-04-10T14:00:00Z"),  # isolated
        ]
        clusters = cluster_raises(raises, idle_gap_seconds=60, min_size=1)
        assert len(clusters) == 2
        assert len(clusters[0]) == 3

    def test_unsorted_input_is_handled(self):
        raises = [
            _raise("bgp",     "Leaf1", "2026-04-10T12:00:05Z"),
            _raise("cabling", "Leaf1", "2026-04-10T12:00:00Z"),  # earlier, listed second
        ]
        clusters = cluster_raises(raises, idle_gap_seconds=60, min_size=1)
        assert len(clusters) == 1
        # Should be sorted: cabling (T+0) then bgp (T+5)
        assert clusters[0][0]["timestamp"] < clusters[0][1]["timestamp"]

    def test_exact_gap_boundary(self):
        """An event exactly at idle_gap_seconds boundary starts a new cluster."""
        raises = [
            _raise("bgp",     "Leaf1", "2026-04-10T12:00:00Z"),
            _raise("cabling", "Leaf1", "2026-04-10T12:01:01Z"),  # 61s later > 60s gap
        ]
        clusters = cluster_raises(raises, idle_gap_seconds=60, min_size=1)
        assert len(clusters) == 2


# ════════════════════════════════════════════════════════════════════════════
# anomaly_clustering — deduplicate_cluster
# ════════════════════════════════════════════════════════════════════════════

class TestDeduplicateCluster:
    def test_removes_ghost_row(self):
        """A row with anomaly_type prefix in identity but no device is a ghost."""
        canonical = _raise("bgp", "Leaf1", "2026-04-10T12:00:00Z",
                           identity={"system_id": "X", "source_ip": "10.0.0.1", "destination_ip": "10.0.0.2"})
        ghost = _raise("bgp", None, "2026-04-10T12:00:00Z",
                       identity={"anomaly_type": "bgp", "system_id": "X",
                                 "source_ip": "10.0.0.1", "destination_ip": "10.0.0.2"})
        result = deduplicate_cluster([canonical, ghost])
        assert len(result) == 1
        assert result[0]["device"] == "Leaf1"

    def test_unique_events_preserved(self):
        r1 = _raise("bgp",       "Leaf1", "2026-04-10T12:00:00Z")
        r2 = _raise("interface", "Leaf2", "2026-04-10T12:00:01Z")
        result = deduplicate_cluster([r1, r2])
        assert len(result) == 2

    def test_bgp_bilateral_collapsed(self):
        """Leaf1→Spine1 and Spine1→Leaf1 on the same session collapse to one."""
        leaf_bgp = _raise("bgp", "Leaf1", "2026-04-10T12:00:00Z",
                          identity={"source_ip": "10.0.0.1", "destination_ip": "10.0.0.2",
                                    "addr_family": "ipv4", "vrf_name": "default"})
        spine_bgp = _raise("bgp", "Spine1", "2026-04-10T12:00:01Z",
                           identity={"source_ip": "10.0.0.2", "destination_ip": "10.0.0.1",
                                     "addr_family": "ipv4", "vrf_name": "default"})
        result = deduplicate_cluster([leaf_bgp, spine_bgp])
        assert len(result) == 1
        assert result[0]["bilateral_dedup"] is True
        assert result[0]["raw_count"] == 2
        assert "Spine1" in (result[0]["bilateral_peers"] or [])

    def test_bgp_different_families_not_collapsed(self):
        """ipv4 and evpn sessions on the same peer pair are different sessions."""
        ipv4 = _raise("bgp", "Leaf1", "2026-04-10T12:00:00Z",
                      identity={"source_ip": "10.0.0.1", "destination_ip": "10.0.0.2",
                                "addr_family": "ipv4", "vrf_name": "default"})
        evpn = _raise("bgp", "Leaf1", "2026-04-10T12:00:01Z",
                      identity={"source_ip": "172.16.0.1", "destination_ip": "172.16.0.2",
                                "addr_family": "evpn", "vrf_name": "default"})
        result = deduplicate_cluster([ipv4, evpn])
        assert len(result) == 2

    def test_cabling_bilateral_collapsed(self):
        leaf = _raise("cabling", "Leaf1", "2026-04-10T12:00:00Z",
                      identity={"interface": "ge-0/0/0", "system_id": "X"},
                      expected={"neighbor_name": "Spine1", "neighbor_interface": "ge-0/0/5"})
        spine = _raise("cabling", "Spine1", "2026-04-10T12:00:01Z",
                       identity={"interface": "ge-0/0/5", "system_id": "Y"},
                       expected={"neighbor_name": "Leaf1", "neighbor_interface": "ge-0/0/0"})
        result = deduplicate_cluster([leaf, spine])
        assert len(result) == 1
        assert result[0]["bilateral_dedup"] is True
        assert "Spine1" in (result[0]["bilateral_peers"] or [])

    def test_cabling_no_collapse_when_different_cables(self):
        a = _raise("cabling", "Leaf1", "2026-04-10T12:00:00Z",
                   identity={"interface": "ge-0/0/0", "system_id": "X"},
                   expected={"neighbor_name": "Spine1"})
        b = _raise("cabling", "Leaf2", "2026-04-10T12:00:01Z",
                   identity={"interface": "ge-0/0/0", "system_id": "Z"},
                   expected={"neighbor_name": "Spine2"})
        result = deduplicate_cluster([a, b])
        assert len(result) == 2


# ════════════════════════════════════════════════════════════════════════════
# anomaly_clustering — tag_osi_layer
# ════════════════════════════════════════════════════════════════════════════

class TestTagOsiLayer:
    @pytest.mark.parametrize("atype,expected_layer", [
        ("cabling",   1),
        ("interface", 2),
        ("lag",       2),
        ("bgp",       4),
        ("route",     4),
        ("mac",       5),
        ("probe",     6),
    ])
    def test_known_types(self, atype, expected_layer):
        result = tag_osi_layer({"anomaly_type": atype})
        assert result["osi_layer"] == expected_layer
        assert "osi_label" in result

    def test_unknown_type_gets_99(self):
        result = tag_osi_layer({"anomaly_type": "unknown_thing"})
        assert result["osi_layer"] == 99

    def test_does_not_mutate_original(self):
        d = {"anomaly_type": "bgp"}
        _ = tag_osi_layer(d)
        assert "osi_layer" not in d


# ════════════════════════════════════════════════════════════════════════════
# anomaly_clustering — score_root_cause
# ════════════════════════════════════════════════════════════════════════════

class TestScoreRootCause:
    def test_empty_returns_none(self):
        assert score_root_cause([]) is None

    def test_single_item_is_root(self):
        a = _raise("bgp", "Leaf1", "2026-04-10T12:00:00Z")
        result = score_root_cause([a])
        assert result["anomaly_type"] == "bgp"
        assert result["rc_confidence"] in ("medium", "low")

    def test_lower_layer_wins(self):
        cabling = _raise("cabling", "Leaf1", "2026-04-10T12:00:00Z")
        bgp     = _raise("bgp",     "Leaf1", "2026-04-10T12:00:01Z")
        result  = score_root_cause([bgp, cabling])
        assert result["anomaly_type"] == "cabling"

    def test_high_confidence_when_multi_layer(self):
        cabling = _raise("cabling",   "Leaf1", "2026-04-10T12:00:00Z")
        bgp     = _raise("bgp",       "Leaf1", "2026-04-10T12:00:01Z")
        route   = _raise("route",     "Leaf1", "2026-04-10T12:00:02Z")
        result  = score_root_cause([cabling, bgp, route])
        assert result["rc_confidence"] == "high"
        assert result["anomaly_type"] == "cabling"

    def test_rc_reason_is_a_string(self):
        a = _raise("bgp", "Leaf1", "2026-04-10T12:00:00Z")
        result = score_root_cause([a])
        assert isinstance(result["rc_reason"], str)
        assert len(result["rc_reason"]) > 0


# ════════════════════════════════════════════════════════════════════════════
# anomaly_clustering — build_causal_chain
# ════════════════════════════════════════════════════════════════════════════

class TestBuildCausalChain:
    def test_single_layer(self):
        chain = build_causal_chain([_raise("bgp", "Leaf1", "2026-04-10T12:00:00Z")])
        assert len(chain) == 1
        assert "bgp" in chain[0]

    def test_multi_layer_ordered(self):
        anomalies = [
            _raise("bgp",     "Leaf1", "2026-04-10T12:00:01Z"),
            _raise("cabling", "Leaf1", "2026-04-10T12:00:00Z"),
        ]
        chain = build_causal_chain(anomalies)
        # L1 (cabling) must come before L4 (bgp) in the chain
        assert chain.index(next(c for c in chain if "cabling" in c)) < \
               chain.index(next(c for c in chain if "bgp" in c))

    def test_same_layer_types_grouped(self):
        anomalies = [
            _raise("bgp",   "Leaf1", "2026-04-10T12:00:00Z"),
            _raise("route", "Leaf1", "2026-04-10T12:00:01Z"),
        ]
        chain = build_causal_chain(anomalies)
        # Both are L4 — should appear in the same chain entry
        assert len(chain) == 1
        assert "bgp" in chain[0] and "route" in chain[0]


# ════════════════════════════════════════════════════════════════════════════
# anomaly_clustering — enrich_cluster
# ════════════════════════════════════════════════════════════════════════════

class TestEnrichCluster:
    def test_structure_keys_present(self):
        raises = [
            _raise("cabling", "Leaf1", "2026-04-10T12:00:00Z",
                   identity={"interface": "ge-0/0/0", "system_id": "X"},
                   expected={"neighbor_name": "Spine1"}),
            _raise("bgp", "Leaf1", "2026-04-10T12:00:02Z",
                   identity={"source_ip": "10.0.0.1", "destination_ip": "10.0.0.2",
                              "addr_family": "ipv4", "vrf_name": "default"}),
        ]
        result = enrich_cluster(raises, cluster_index=0)
        assert result["cluster_id"] == 1
        assert "started_at" in result
        assert "span_seconds" in result
        assert "raw_raise_count" in result
        assert "logical_anomaly_count" in result
        assert "affected_devices" in result
        assert "causal_chain" in result
        assert "root_cause_candidate" in result
        assert "logical_anomalies" in result

    def test_offsets_computed(self):
        raises = [
            _raise("cabling", "Leaf1", "2026-04-10T12:00:00Z"),
            _raise("bgp",     "Leaf1", "2026-04-10T12:00:10Z"),
        ]
        result = enrich_cluster(raises, cluster_index=0)
        offsets = [a["offset_from_start_s"] for a in result["logical_anomalies"]]
        assert offsets[0] == 0.0
        assert offsets[1] == pytest.approx(10.0)

    def test_span_seconds_correct(self):
        raises = [
            _raise("cabling", "Leaf1", "2026-04-10T12:00:00Z"),
            _raise("bgp",     "Leaf1", "2026-04-10T12:00:30Z"),
        ]
        result = enrich_cluster(raises, cluster_index=0)
        assert result["span_seconds"] == pytest.approx(30.0)

    def test_logical_count_less_than_raw_when_bilateral(self):
        """Bilateral pair collapses to 1 logical anomaly from 2 raw raises."""
        raises = [
            _raise("bgp", "Leaf1", "2026-04-10T12:00:00Z",
                   identity={"source_ip": "10.0.0.1", "destination_ip": "10.0.0.2",
                              "addr_family": "ipv4", "vrf_name": "default"}),
            _raise("bgp", "Spine1", "2026-04-10T12:00:01Z",
                   identity={"source_ip": "10.0.0.2", "destination_ip": "10.0.0.1",
                              "addr_family": "ipv4", "vrf_name": "default"}),
        ]
        result = enrich_cluster(raises, cluster_index=0)
        assert result["raw_raise_count"] == 2
        assert result["logical_anomaly_count"] == 1


# ════════════════════════════════════════════════════════════════════════════
# AnomalyStore — new analytics query methods
# ════════════════════════════════════════════════════════════════════════════

class TestGetRaisesInWindow:
    def _store_with_events(self):
        store = make_store()
        a1 = _make_anomaly("bgp", "Leaf1", "2026-04-08T10:00:00Z")
        a2 = _make_anomaly("cabling", "Spine1", "2026-04-09T10:00:00Z")
        id1 = store.upsert_anomaly(BP, INST, a1)
        id2 = store.upsert_anomaly(BP, INST, a2)
        store.insert_event(id1, "2026-04-08T10:00:00Z", raised=True,  actual=None, source="t")
        store.insert_event(id1, "2026-04-08T11:00:00Z", raised=False, actual=None, source="t")
        store.insert_event(id2, "2026-04-09T10:00:00Z", raised=True,  actual=None, source="t")
        return store

    def test_returns_only_raises(self):
        store = self._store_with_events()
        rows = store.get_raises_in_window(BP)
        assert all(r["raised"] for r in rows)
        store.close()

    def test_since_filter(self):
        store = self._store_with_events()
        rows = store.get_raises_in_window(BP, since="2026-04-09T00:00:00Z")
        assert len(rows) == 1
        assert rows[0]["anomaly_type"] == "cabling"
        store.close()

    def test_ascending_order(self):
        store = self._store_with_events()
        rows = store.get_raises_in_window(BP)
        timestamps = [r["timestamp"] for r in rows]
        assert timestamps == sorted(timestamps)
        store.close()

    def test_type_filter(self):
        store = self._store_with_events()
        rows = store.get_raises_in_window(BP, anomaly_type="bgp")
        assert all(r["anomaly_type"] == "bgp" for r in rows)
        store.close()


class TestGetTrendBuckets:
    def test_returns_buckets(self):
        store = make_store()
        a = _make_anomaly("mac", "Leaf1")
        aid = store.upsert_anomaly(BP, INST, a)
        store.insert_event(aid, "2026-04-10T12:00:00Z", raised=True,  actual=None, source="t")
        store.insert_event(aid, "2026-04-10T12:30:00Z", raised=False, actual=None, source="t")
        store.insert_event(aid, "2026-04-10T13:00:00Z", raised=True,  actual=None, source="t")
        rows = store.get_trend_buckets(BP, "mac", "2026-04-10T00:00:00Z", bucket_minutes=60)
        assert len(rows) >= 1
        assert all("bucket" in r for r in rows)
        assert all("raises" in r for r in rows)
        store.close()

    def test_no_data_returns_empty(self):
        store = make_store()
        rows = store.get_trend_buckets(BP, "bgp", "2026-04-10T00:00:00Z", bucket_minutes=60)
        assert rows == []
        store.close()


class TestGetFaultEpisodes:
    def test_closed_episode(self):
        store = make_store()
        a = _make_anomaly("bgp", "Leaf1")
        aid = store.upsert_anomaly(BP, INST, a)
        store.insert_event(aid, "2026-04-10T10:00:00Z", raised=True,  actual=None, source="t")
        store.insert_event(aid, "2026-04-10T10:05:00Z", raised=False, actual=None, source="t")
        eps = store.get_fault_episodes(BP)
        assert len(eps) == 1
        assert eps[0]["raised_at"]  == "2026-04-10T10:00:00Z"
        assert eps[0]["cleared_at"] == "2026-04-10T10:05:00Z"
        store.close()

    def test_open_episode(self):
        store = make_store()
        a = _make_anomaly("bgp", "Leaf1")
        aid = store.upsert_anomaly(BP, INST, a)
        store.insert_event(aid, "2026-04-10T10:00:00Z", raised=True, actual=None, source="t")
        eps = store.get_fault_episodes(BP)
        assert len(eps) == 1
        assert eps[0]["cleared_at"] is None
        store.close()

    def test_multiple_episodes_same_anomaly(self):
        store = make_store()
        a = _make_anomaly("mac", "Leaf1")
        aid = store.upsert_anomaly(BP, INST, a)
        for i in range(3):
            store.insert_event(aid, f"2026-04-10T{10+i*2:02d}:00:00Z", raised=True,  actual=None, source="t")
            store.insert_event(aid, f"2026-04-10T{11+i*2:02d}:00:00Z", raised=False, actual=None, source="t")
        eps = store.get_fault_episodes(BP, anomaly_type="mac")
        assert len(eps) == 3
        store.close()

    def test_type_filter(self):
        store = make_store()
        a_bgp = _make_anomaly("bgp",      "Leaf1")
        a_mac = _make_anomaly("mac",      "Leaf1")
        id1 = store.upsert_anomaly(BP, INST, a_bgp)
        id2 = store.upsert_anomaly(BP, INST, a_mac)
        store.insert_event(id1, "2026-04-10T10:00:00Z", raised=True, actual=None, source="t")
        store.insert_event(id2, "2026-04-10T10:01:00Z", raised=True, actual=None, source="t")
        eps = store.get_fault_episodes(BP, anomaly_type="bgp")
        assert all(e["anomaly_type"] == "bgp" for e in eps)
        store.close()


class TestGetDeviceTypeMatrix:
    def test_returns_matrix_rows(self):
        store = make_store()
        a = _make_anomaly("bgp", "Leaf1")
        aid = store.upsert_anomaly(BP, INST, a)
        store.insert_event(aid, "2026-04-10T10:00:00Z", raised=True, actual=None, source="t")
        rows = store.get_device_type_matrix(BP, since="2026-04-01T00:00:00Z")
        assert len(rows) == 1
        assert rows[0]["device"] == "Leaf1"
        assert rows[0]["anomaly_type"] == "bgp"
        assert rows[0]["raise_count"] == 1
        store.close()

    def test_multiple_types_per_device(self):
        store = make_store()
        a1 = _make_anomaly("bgp",  "Leaf1")
        a2 = _make_anomaly("mac",  "Leaf1")
        id1 = store.upsert_anomaly(BP, INST, a1)
        id2 = store.upsert_anomaly(BP, INST, a2)
        store.insert_event(id1, "2026-04-10T10:00:00Z", raised=True, actual=None, source="t")
        store.insert_event(id2, "2026-04-10T10:01:00Z", raised=True, actual=None, source="t")
        rows = store.get_device_type_matrix(BP, since="2026-04-01T00:00:00Z")
        types = {r["anomaly_type"] for r in rows}
        assert "bgp" in types
        assert "mac" in types
        store.close()

    def test_since_filter_excludes_old_events(self):
        store = make_store()
        a = _make_anomaly("bgp", "Leaf1")
        aid = store.upsert_anomaly(BP, INST, a)
        store.insert_event(aid, "2020-01-01T10:00:00Z", raised=True, actual=None, source="t")
        rows = store.get_device_type_matrix(BP, since="2026-04-01T00:00:00Z")
        assert rows == []
        store.close()


# ════════════════════════════════════════════════════════════════════════════
# MCP tools (tools/anomaly_analytics.py)
# ════════════════════════════════════════════════════════════════════════════

class TestGetAnomalyTrendTool:
    async def test_returns_trend_structure(self):
        from tools.anomaly_analytics import register
        store = make_store()
        a = _make_anomaly("mac", "Leaf1")
        aid = store.upsert_anomaly(BP, INST, a)
        store.insert_event(aid, "2026-04-10T12:00:00Z", raised=True, actual=None, source="t")

        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["get_anomaly_trend"](
            blueprint_id=BP, anomaly_type="mac", hours_back=24, ctx=ctx
        )
        assert result["anomaly_type"] == "mac"
        assert "devices" in result
        assert "fabric_total" in result
        assert "bucket_size" in result
        store.close()

    async def test_no_data_returns_empty(self):
        from tools.anomaly_analytics import register
        store = make_store()
        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["get_anomaly_trend"](
            blueprint_id=BP, anomaly_type="bgp", hours_back=24, ctx=ctx
        )
        assert result["device_count"] == 0
        store.close()

    async def test_hours_back_clamped(self):
        from tools.anomaly_analytics import register
        store = make_store()
        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["get_anomaly_trend"](
            blueprint_id=BP, anomaly_type="mac", hours_back=9999, ctx=ctx
        )
        assert result["hours_back"] == 168
        store.close()

    async def test_bucket_size_scales_with_window(self):
        from tools.anomaly_analytics import register
        store = make_store()
        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        for hours, expected in [(4, "15m"), (12, "60m"), (48, "240m"), (100, "720m")]:
            result = await stub.tools["get_anomaly_trend"](
                blueprint_id=BP, anomaly_type="mac", hours_back=hours, ctx=ctx
            )
            assert result["bucket_size"] == expected
        store.close()


class TestGetCorrelatedFaultsTool:
    async def test_bilateral_bgp_dedup(self):
        from tools.anomaly_analytics import register

        store = make_store()
        leaf_a = {
            "anomaly_type": "bgp", "device_hostname": "Leaf1", "role": "spine_leaf",
            "identity": {"source_ip": "10.0.0.1", "destination_ip": "10.0.0.2",
                         "addr_family": "ipv4", "vrf_name": "default"},
            "expected": {"value": "up"}, "actual": None, "detected_at": "2026-04-08T10:00:00Z",
        }
        spine_a = {
            "anomaly_type": "bgp", "device_hostname": "Spine1", "role": "spine_leaf",
            "identity": {"source_ip": "10.0.0.2", "destination_ip": "10.0.0.1",
                         "addr_family": "ipv4", "vrf_name": "default"},
            "expected": {"value": "up"}, "actual": None, "detected_at": "2026-04-08T10:00:01Z",
        }
        id1 = store.upsert_anomaly(BP, INST, leaf_a)
        id2 = store.upsert_anomaly(BP, INST, spine_a)
        store.insert_event(id1, "2026-04-10T10:00:00Z", raised=True, actual=None, source="t")
        store.insert_event(id2, "2026-04-10T10:00:01Z", raised=True, actual=None, source="t")

        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["get_correlated_faults"](
            blueprint_id=BP, anomaly_type="bgp", ctx=ctx
        )
        assert result["raw_count"] == 2
        assert result["logical_count"] == 1
        assert result["dedup_ratio"] == 0.5
        store.close()

    async def test_no_anomalies(self):
        from tools.anomaly_analytics import register
        store = make_store()
        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["get_correlated_faults"](
            blueprint_id=BP, anomaly_type="bgp", ctx=ctx
        )
        assert result["raw_count"] == 0
        assert result["logical_count"] == 0
        store.close()


class TestGetFaultDurationsTool:
    async def test_computes_episode_duration(self):
        from tools.anomaly_analytics import register
        store = make_store()
        a = _make_anomaly("bgp", "Leaf1")
        aid = store.upsert_anomaly(BP, INST, a)
        store.insert_event(aid, "2026-04-10T10:00:00Z", raised=True,  actual=None, source="t")
        store.insert_event(aid, "2026-04-10T10:05:00Z", raised=False, actual=None, source="t")

        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["get_fault_durations"](
            blueprint_id=BP, anomaly_type="bgp", ctx=ctx
        )
        assert result["device_count"] == 1
        device = result["devices"][0]
        assert device["episode_count"] == 1
        assert device["mean_duration_s"] == pytest.approx(300.0)
        assert device["total_downtime_s"] == pytest.approx(300.0)
        store.close()

    async def test_open_episode_counted(self):
        from tools.anomaly_analytics import register
        store = make_store()
        a = _make_anomaly("bgp", "Leaf1")
        aid = store.upsert_anomaly(BP, INST, a)
        store.insert_event(aid, "2026-04-10T10:00:00Z", raised=True, actual=None, source="t")

        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["get_fault_durations"](
            blueprint_id=BP, anomaly_type="bgp", ctx=ctx
        )
        device = result["devices"][0]
        assert device["open_episodes"] == 1
        assert device["mean_duration_s"] is None  # no closed episodes
        store.close()

    async def test_sorted_by_total_downtime(self):
        from tools.anomaly_analytics import register
        store = make_store()
        # Leaf1: short outage; Leaf2: long outage
        a1 = {**_make_anomaly("bgp", "Leaf1"), "identity": {"system_id": "SYS1"}}
        a2 = {**_make_anomaly("bgp", "Leaf2"), "identity": {"system_id": "SYS2"}}
        id1 = store.upsert_anomaly(BP, INST, a1)
        id2 = store.upsert_anomaly(BP, INST, a2)
        store.insert_event(id1, "2026-04-10T10:00:00Z", raised=True,  actual=None, source="t")
        store.insert_event(id1, "2026-04-10T10:01:00Z", raised=False, actual=None, source="t")  # 60s
        store.insert_event(id2, "2026-04-10T10:00:00Z", raised=True,  actual=None, source="t")
        store.insert_event(id2, "2026-04-10T10:30:00Z", raised=False, actual=None, source="t")  # 1800s

        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["get_fault_durations"](
            blueprint_id=BP, anomaly_type="bgp", ctx=ctx
        )
        assert result["devices"][0]["device"] == "Leaf2"
        store.close()


class TestGetDeviceAnomalyHeatmapTool:
    async def test_heatmap_structure(self):
        from tools.anomaly_analytics import register
        store = make_store()
        a1 = _make_anomaly("bgp",  "Leaf1")
        a2 = _make_anomaly("mac",  "Leaf1")
        a3 = _make_anomaly("cabling", "Spine1")
        id1 = store.upsert_anomaly(BP, INST, a1)
        id2 = store.upsert_anomaly(BP, INST, a2)
        id3 = store.upsert_anomaly(BP, INST, a3)
        for aid, ts in [(id1, "2026-04-10T10:00:00Z"),
                        (id2, "2026-04-10T10:01:00Z"),
                        (id3, "2026-04-10T10:02:00Z")]:
            store.insert_event(aid, ts, raised=True, actual=None, source="t")

        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["get_device_anomaly_heatmap"](
            blueprint_id=BP, hours_back=168, ctx=ctx
        )
        assert result["device_count"] == 2
        assert "all_types" in result
        assert "matrix" in result
        assert "summary" in result
        store.close()

    async def test_cross_layer_flag(self):
        from tools.anomaly_analytics import register
        store = make_store()
        # Leaf1 has cabling (L1) and bgp (L4) → cross-layer
        a1 = _make_anomaly("cabling", "Leaf1")
        a2 = _make_anomaly("bgp",     "Leaf1")
        id1 = store.upsert_anomaly(BP, INST, a1)
        id2 = store.upsert_anomaly(BP, INST, a2)
        store.insert_event(id1, "2026-04-10T10:00:00Z", raised=True, actual=None, source="t")
        store.insert_event(id2, "2026-04-10T10:00:01Z", raised=True, actual=None, source="t")

        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["get_device_anomaly_heatmap"](
            blueprint_id=BP, hours_back=168, ctx=ctx
        )
        leaf1 = next(d for d in result["matrix"] if d["device"] == "Leaf1")
        assert leaf1["cross_layer_fault"] is True
        assert "Leaf1" in result["summary"]["cross_layer_devices"]
        store.close()

    async def test_no_data_returns_empty(self):
        from tools.anomaly_analytics import register
        store = make_store()
        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["get_device_anomaly_heatmap"](
            blueprint_id=BP, hours_back=168, ctx=ctx
        )
        assert result["device_count"] == 0
        store.close()


class TestCorrelateAnomalyEventsTool:
    async def test_single_cluster_detected(self):
        from tools.anomaly_analytics import register
        store = make_store()
        a1 = _make_anomaly("cabling",   "Leaf1", "2026-04-10T10:00:00Z")
        a2 = _make_anomaly("bgp",       "Leaf1", "2026-04-10T10:00:02Z")
        a3 = _make_anomaly("interface", "Leaf1", "2026-04-10T10:00:04Z")
        # Distant event — should be a separate cluster
        a4 = _make_anomaly("mac",       "Leaf2", "2026-04-10T14:00:00Z")
        for a, ts in [(a1, "2026-04-10T10:00:00Z"),
                      (a2, "2026-04-10T10:00:02Z"),
                      (a3, "2026-04-10T10:00:04Z"),
                      (a4, "2026-04-10T14:00:00Z")]:
            aid = store.upsert_anomaly(BP, INST, a)
            store.insert_event(aid, ts, raised=True, actual=None, source="t")

        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["correlate_anomaly_events"](
            blueprint_id=BP, hours_back=168, idle_gap_seconds=60, min_cluster_size=1, ctx=ctx
        )
        assert result["cluster_count"] == 2
        # First cluster (newest first) should be mac
        # Second cluster (older) should be cabling+bgp+interface
        types_in_older = {a["anomaly_type"] for a in result["clusters"][-1]["logical_anomalies"]}
        assert "cabling" in types_in_older

        store.close()

    async def test_root_cause_is_lowest_layer(self):
        from tools.anomaly_analytics import register
        store = make_store()
        a1 = _make_anomaly("cabling", "Leaf1", "2026-04-10T10:00:00Z")
        a2 = _make_anomaly("bgp",     "Leaf1", "2026-04-10T10:00:02Z")
        id1 = store.upsert_anomaly(BP, INST, a1)
        id2 = store.upsert_anomaly(BP, INST, a2)
        store.insert_event(id1, "2026-04-10T10:00:00Z", raised=True, actual=None, source="t")
        store.insert_event(id2, "2026-04-10T10:00:02Z", raised=True, actual=None, source="t")

        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["correlate_anomaly_events"](
            blueprint_id=BP, hours_back=168, idle_gap_seconds=60, min_cluster_size=1, ctx=ctx
        )
        cluster = result["clusters"][0]
        assert cluster["root_cause_candidate"]["anomaly_type"] == "cabling"
        assert cluster["root_cause_candidate"]["confidence"] == "high"
        store.close()

    async def test_type_filter_excludes_unmatched_clusters(self):
        from tools.anomaly_analytics import register
        store = make_store()
        a1 = _make_anomaly("mac", "Leaf1")
        id1 = store.upsert_anomaly(BP, INST, a1)
        store.insert_event(id1, "2026-04-10T10:00:00Z", raised=True, actual=None, source="t")

        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["correlate_anomaly_events"](
            blueprint_id=BP, hours_back=168, anomaly_type="bgp",
            min_cluster_size=1, ctx=ctx
        )
        # No BGP anomalies — should return zero clusters
        assert result["cluster_count"] == 0
        store.close()

    async def test_min_cluster_size_filters_singletons(self):
        from tools.anomaly_analytics import register
        store = make_store()
        # Each anomaly is an hour apart — all singletons
        for i, atype in enumerate(["bgp", "mac", "route"]):
            a = _make_anomaly(atype, "Leaf1")
            aid = store.upsert_anomaly(BP, INST, a)
            store.insert_event(aid, f"2026-04-10T{10+i:02d}:00:00Z", raised=True,
                               actual=None, source="t")

        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["correlate_anomaly_events"](
            blueprint_id=BP, hours_back=168, idle_gap_seconds=60, min_cluster_size=2, ctx=ctx
        )
        assert result["cluster_count"] == 0
        store.close()

    async def test_causal_chain_present(self):
        from tools.anomaly_analytics import register
        store = make_store()
        a1 = _make_anomaly("cabling",  "Leaf1")
        a2 = _make_anomaly("bgp",      "Leaf1")
        id1 = store.upsert_anomaly(BP, INST, a1)
        id2 = store.upsert_anomaly(BP, INST, a2)
        store.insert_event(id1, "2026-04-10T10:00:00Z", raised=True, actual=None, source="t")
        store.insert_event(id2, "2026-04-10T10:00:05Z", raised=True, actual=None, source="t")

        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["correlate_anomaly_events"](
            blueprint_id=BP, hours_back=168, min_cluster_size=1, ctx=ctx
        )
        cluster = result["clusters"][0]
        chain = cluster["causal_chain"]
        assert len(chain) >= 2  # L1 and L4 at minimum
        assert any("cabling" in c for c in chain)
        assert any("bgp" in c for c in chain)
        store.close()

    async def test_no_raises_returns_empty(self):
        from tools.anomaly_analytics import register
        store = make_store()
        stub = StubMCP()
        register(stub)
        ctx = make_ctx(store)
        result = await stub.tools["correlate_anomaly_events"](
            blueprint_id=BP, hours_back=24, ctx=ctx
        )
        assert result["total_raises"] == 0
        assert result["cluster_count"] == 0
        store.close()
