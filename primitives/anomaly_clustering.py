"""
anomaly_clustering.py

Pure-Python logic for grouping anomaly raise events into temporal fault
clusters and enriching each cluster with OSI-layer analysis, structural
de-duplication, and root-cause scoring.

No I/O — all public functions take plain Python dicts and return plain
Python dicts.  This makes the logic unit-testable without a database or
running server.

Design principles
-----------------
- Clustering is *temporal and fabric-wide*, not protocol-specific.
  A cluster forms whenever the fabric emits a burst of new raises within a
  short idle gap.  The algorithm is agnostic to anomaly type.
- De-duplication removes artefacts of how Apstra stores anomalies (ghost
  rows with no device_hostname, bilateral BGP/cabling pairs) before any
  analysis is done.
- OSI-layer tagging adds structure AFTER clustering so that a callers (and
  LLMs) can reason about L1→L2→L4 cascades without the clustering itself
  being biased toward any one layer.
- Root-cause scoring is heuristic and clearly labelled as a *candidate*
  with an explicit confidence and reason — it never silently hides
  conflicting evidence.
"""

import json

# ── OSI layer mapping ─────────────────────────────────────────────────────────

OSI_LAYER: dict[str, int] = {
    "cabling":    1,  # physical — LLDP neighbour mismatch
    "interface":  2,  # link — port up/down
    "lag":        2,  # link aggregation
    "deployment": 3,  # config deployment state
    "liveness":   3,  # device reachability
    "config":     3,  # configuration compliance
    "bgp":        4,  # routing protocol
    "route":      4,  # routing table
    "mac":        5,  # overlay / MAC learning
    "probe":      6,  # derived telemetry (IBA)
}

OSI_LAYER_LABEL: dict[int, str] = {
    1: "L1-physical",
    2: "L2-link",
    3: "L3-network",
    4: "L4-routing",
    5: "L5-overlay",
    6: "L6-telemetry",
}


# ── Identity normalisation ────────────────────────────────────────────────────

def _strip_type_prefix(identity: dict) -> dict:
    """
    Apstra sometimes prepends an ``anomaly_type`` key to the identity dict
    (backfill artefact).  Strip it so de-dup comparisons work correctly.
    """
    return {k: v for k, v in identity.items() if k != "anomaly_type"}


def _identity_key(anomaly: dict) -> str:
    """
    Stable string key for an anomaly identity.

    Includes the row's ``anomaly_type`` so that two different anomaly types
    that happen to share the same identity fields (e.g. a cabling and a bgp
    anomaly on the same interface) are NOT treated as duplicates.
    The ``anomaly_type`` key embedded inside the identity dict itself is still
    stripped — that is the Apstra ghost-row artefact we want to normalise away.
    """
    clean = _strip_type_prefix(anomaly.get("identity", {}))
    atype = anomaly.get("anomaly_type", "")
    return json.dumps({"_type": atype, **clean}, sort_keys=True)


# ── Temporal clustering ───────────────────────────────────────────────────────

def cluster_raises(
    raises: list[dict],
    idle_gap_seconds: int = 60,
    min_size: int = 1,
) -> list[list[dict]]:
    """
    Group raise events into temporal clusters using inter-event gap detection.

    A new cluster starts whenever the consecutive gap between events exceeds
    *idle_gap_seconds*.  This is deliberately protocol-agnostic: it detects
    moments of fabric-wide change without assuming any causal relationship
    between anomaly types.

    Parameters
    ----------
    raises
        List of raise-event dicts, sorted ascending by ``timestamp``.
        Each dict must contain at minimum:
          ``timestamp``, ``anomaly_type``, ``device``, ``identity``,
          ``expected``, ``role``, ``first_detected``, ``source``.
    idle_gap_seconds
        Gap (seconds) between consecutive events that begins a new cluster.
        Default 60 s — coarse enough to absorb Apstra's internal polling lag
        while still separating unrelated faults.
    min_size
        Drop clusters with fewer than this many *raw* raises (suppresses
        isolated single-event noise when set to 2).

    Returns
    -------
    List of clusters, each cluster being a list of raw raise dicts.
    Clusters are ordered by their earliest event timestamp.
    """
    if not raises:
        return []

    # Raises must be in ascending time order for gap detection to work.
    sorted_raises = sorted(raises, key=lambda r: r["timestamp"])

    clusters: list[list[dict]] = []
    current: list[dict] = [sorted_raises[0]]

    for evt in sorted_raises[1:]:
        prev_ts = _parse_iso_seconds(current[-1]["timestamp"])
        this_ts = _parse_iso_seconds(evt["timestamp"])
        gap = this_ts - prev_ts
        if gap > idle_gap_seconds:
            clusters.append(current)
            current = [evt]
        else:
            current.append(evt)

    clusters.append(current)
    return [c for c in clusters if len(c) >= min_size]


def _parse_iso_seconds(ts: str) -> float:
    """
    Convert an ISO-8601 UTC timestamp string to a float (seconds since epoch).
    Handles both 'Z' and '+00:00' suffixes.  Returns 0.0 on parse failure
    so a malformed timestamp never crashes the clustering loop.
    """
    from datetime import datetime, timezone
    if not ts:
        return 0.0
    ts = ts.replace("+00:00", "Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    return 0.0


# ── Structural de-duplication ─────────────────────────────────────────────────

def deduplicate_cluster(raises: list[dict]) -> list[dict]:
    """
    Remove duplicate raise entries within a single cluster.

    Three passes are applied in order:

    1. **Identity-ghost removal** — Apstra sometimes stores the same anomaly
       twice: once with ``device_hostname`` set, once without (ghost row where
       ``identity`` has an extra ``anomaly_type`` prefix key).  The ghost is
       dropped; the canonical row (with device) is kept.

    2. **BGP bilateral collapse** — A BGP session anomaly appears on both
       endpoints (e.g. Leaf2→Spine1 and Spine1→Leaf2).  Two rows that share
       ``frozenset({source_ip, destination_ip})`` + ``addr_family`` + ``vrf_name``
       are collapsed into one logical entry.  The canonical row is the one with
       a non-None device; ``bilateral_peers`` lists both devices.

    3. **Cabling bilateral collapse** — A cabling anomaly appears on both ends
       of a cable.  Two rows where ``expected.neighbor_name`` of row A matches
       ``device`` of row B (and vice versa) are collapsed into one logical
       cable entry.

    Returns a new list of de-duplicated dicts.  Each dict gains extra keys:
      ``bilateral_dedup`` (bool), ``raw_count`` (int ≥ 1),
      ``bilateral_peers`` (list[str] | None).
    """
    # Pass 1: identity-ghost removal
    seen_keys: set[str] = set()
    deduped: list[dict] = []
    # Prefer rows that have a device hostname (canonical) over ghost rows.
    # Sort so device!=None comes first.
    ordered = sorted(raises, key=lambda r: (r.get("device") is None, r["timestamp"]))
    for row in ordered:
        key = _identity_key(row)
        if key not in seen_keys:
            seen_keys.add(key)
            row = dict(row)
            row.setdefault("bilateral_dedup", False)
            row.setdefault("raw_count", 1)
            row.setdefault("bilateral_peers", None)
            deduped.append(row)

    # Pass 2: BGP bilateral collapse
    deduped = _collapse_bgp(deduped)

    # Pass 3: Cabling bilateral collapse
    deduped = _collapse_cabling(deduped)

    return deduped


def _bgp_session_key(row: dict) -> tuple | None:
    """
    Return a canonical (frozenset_of_ips, addr_family, vrf) tuple for a BGP
    row, or None if the row is not a BGP anomaly.
    """
    if row.get("anomaly_type") != "bgp":
        return None
    ident = _strip_type_prefix(row.get("identity", {}))
    src = ident.get("source_ip", "")
    dst = ident.get("destination_ip", "")
    family = ident.get("addr_family", "")
    vrf = ident.get("vrf_name", "default")
    if not (src and dst):
        return None
    return (frozenset({src, dst}), family, vrf)


def _collapse_bgp(rows: list[dict]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    result: list[dict] = []
    for row in rows:
        key = _bgp_session_key(row)
        if key is None:
            result.append(row)
            continue
        if key in seen:
            canonical = seen[key]
            canonical["raw_count"] = canonical.get("raw_count", 1) + row.get("raw_count", 1)
            canonical["bilateral_dedup"] = True
            peers = canonical.get("bilateral_peers") or []
            if row.get("device") and row["device"] not in peers:
                peers.append(row["device"])
            canonical["bilateral_peers"] = peers
        else:
            row = dict(row)
            row["bilateral_peers"] = [row["device"]] if row.get("device") else []
            seen[key] = row
            result.append(row)
    return result


def _collapse_cabling(rows: list[dict]) -> list[dict]:
    """
    Collapse bilateral cabling anomaly pairs.

    Row A: device=X, expected.neighbor_name=Y
    Row B: device=Y, expected.neighbor_name=X
    → keep A, attach Y to A.bilateral_peers, drop B.
    """
    dropped: set[int] = set()
    result: list[dict] = []
    for i, row in enumerate(rows):
        if i in dropped:
            continue  # this row was consumed as the second half of a bilateral pair
        if row.get("anomaly_type") != "cabling":
            result.append(row)
            continue
        exp = row.get("expected") or {}
        neighbor_name = exp.get("neighbor_name")
        if not neighbor_name:
            result.append(row)
            continue
        # Find the mirror row
        for j, other in enumerate(rows):
            if j <= i or j in dropped:
                continue
            if other.get("anomaly_type") != "cabling":
                continue
            other_exp = other.get("expected") or {}
            if other_exp.get("neighbor_name") == row.get("device"):
                # Confirmed bilateral pair
                row = dict(row)
                row["bilateral_dedup"] = True
                row["raw_count"] = row.get("raw_count", 1) + other.get("raw_count", 1)
                peers = row.get("bilateral_peers") or [row.get("device")]
                if other.get("device") and other["device"] not in peers:
                    peers.append(other["device"])
                row["bilateral_peers"] = peers
                dropped.add(j)
                break
        result.append(row)
    return result


# ── OSI tagging ───────────────────────────────────────────────────────────────

def tag_osi_layer(anomaly: dict) -> dict:
    """Add ``osi_layer`` and ``osi_label`` keys to an anomaly dict."""
    layer = OSI_LAYER.get(anomaly.get("anomaly_type", ""), 99)
    anomaly = dict(anomaly)
    anomaly["osi_layer"] = layer
    anomaly["osi_label"] = OSI_LAYER_LABEL.get(layer, "L?-unknown")
    return anomaly


# ── Root cause scoring ────────────────────────────────────────────────────────

def score_root_cause(anomalies: list[dict]) -> dict | None:
    """
    Heuristically identify the most likely root-cause anomaly in a cluster.

    Scoring is two-tier:
      Primary   — lowest OSI layer wins (physical before logical)
      Tiebreak  — among equal-layer candidates, prefer the one whose
                  devices are *referenced by name* in other anomalies'
                  identity or expected fields.  A device that is named in
                  another anomaly's expected-neighbor is more structurally
                  central to the cluster.

    Returns the winning anomaly dict with additional keys:
      ``rc_confidence``  — "high" | "medium" | "low"
      ``rc_reason``      — human-readable explanation

    Returns None if the list is empty.
    """
    if not anomalies:
        return None

    tagged = [tag_osi_layer(a) for a in anomalies]
    min_layer = min(a["osi_layer"] for a in tagged)
    candidates = [a for a in tagged if a["osi_layer"] == min_layer]

    # Build a set of all device names referenced anywhere in identity/expected
    # across the entire cluster (not just the candidates).
    referenced_devices: set[str] = set()
    for a in tagged:
        ident = _strip_type_prefix(a.get("identity", {}))
        for v in ident.values():
            if isinstance(v, str):
                referenced_devices.add(v)
        exp = a.get("expected") or {}
        for v in exp.values():
            if isinstance(v, str):
                referenced_devices.add(v)

    # Score candidates: +1 if any of their bilateral_peers or device name
    # appears in referenced_devices of *other* anomalies.
    def centrality(a: dict) -> int:
        devices = set(a.get("bilateral_peers") or [])
        if a.get("device"):
            devices.add(a["device"])
        # Don't count self-references — filter devices that only appear in
        # this anomaly's own identity.
        own_refs: set[str] = set()
        ident = _strip_type_prefix(a.get("identity", {}))
        for v in ident.values():
            if isinstance(v, str):
                own_refs.add(v)
        cross_refs = referenced_devices - own_refs
        return sum(1 for d in devices if d in cross_refs)

    candidates.sort(key=centrality, reverse=True)
    winner = candidates[0]
    winner = dict(winner)

    n_layers = len({a["osi_layer"] for a in tagged})
    if min_layer <= 2 and n_layers > 1:
        confidence = "high"
        reason = (
            f"Lowest OSI layer in cluster ({winner['osi_label']}); "
            f"higher-layer anomalies are likely cascading effects."
        )
    elif len(candidates) == 1:
        confidence = "medium"
        reason = (
            f"Only anomaly at the lowest layer ({winner['osi_label']}) in this cluster."
        )
    else:
        confidence = "low"
        reason = (
            f"Multiple anomalies share the lowest OSI layer ({winner['osi_label']}); "
            f"root cause is ambiguous — manual investigation recommended."
        )

    winner["rc_confidence"] = confidence
    winner["rc_reason"] = reason
    return winner


# ── Causal chain inference ────────────────────────────────────────────────────

def build_causal_chain(anomalies: list[dict]) -> list[str]:
    """
    Return an ordered list of OSI layer labels representing the cascade
    from lowest to highest layer seen in this cluster.

    Example: ["L1-physical (cabling)", "L4-routing (bgp)"]
    """
    tagged = [tag_osi_layer(a) for a in anomalies]
    # Group by layer, collect types
    layer_types: dict[int, set[str]] = {}
    for a in tagged:
        layer = a["osi_layer"]
        layer_types.setdefault(layer, set()).add(a["anomaly_type"])
    chain = []
    for layer in sorted(layer_types):
        label = OSI_LAYER_LABEL.get(layer, f"L{layer}-unknown")
        types_str = ", ".join(sorted(layer_types[layer]))
        chain.append(f"{label} ({types_str})")
    return chain


# ── Cluster enrichment (public entry-point) ───────────────────────────────────

def enrich_cluster(raises: list[dict], cluster_index: int) -> dict:
    """
    Take a raw list of raise events (one temporal cluster) and return a
    fully-enriched cluster dict suitable for direct serialisation as an MCP
    tool response.

    Steps
    -----
    1. Structural de-duplication (ghost removal + bilateral collapse)
    2. OSI-layer tagging on every logical anomaly
    3. Root-cause scoring
    4. Causal chain construction
    5. Offset timestamps (seconds from first event in cluster)
    """
    t0 = _parse_iso_seconds(raises[0]["timestamp"]) if raises else 0.0
    t_last = _parse_iso_seconds(raises[-1]["timestamp"]) if raises else 0.0

    logical = deduplicate_cluster(raises)
    logical = [tag_osi_layer(a) for a in logical]

    # Compute per-anomaly offset from cluster start
    for a in logical:
        a["offset_from_start_s"] = round(
            _parse_iso_seconds(a["timestamp"]) - t0, 1
        )

    root_cause = score_root_cause(logical)
    chain = build_causal_chain(logical)

    affected_devices: list[str] = []
    for a in logical:
        peers = a.get("bilateral_peers") or []
        for d in ([a.get("device")] + peers):
            if d and d not in affected_devices:
                affected_devices.append(d)

    return {
        "cluster_id":          cluster_index + 1,
        "started_at":          raises[0]["timestamp"],
        "span_seconds":        round(t_last - t0, 1),
        "raw_raise_count":     len(raises),
        "logical_anomaly_count": len(logical),
        "affected_devices":    affected_devices,
        "causal_chain":        chain,
        "root_cause_candidate": {
            "anomaly_type": root_cause["anomaly_type"] if root_cause else None,
            "osi_label":    root_cause["osi_label"]    if root_cause else None,
            "device":       root_cause.get("device")   if root_cause else None,
            "bilateral_peers": root_cause.get("bilateral_peers") if root_cause else None,
            "confidence":   root_cause["rc_confidence"] if root_cause else None,
            "reason":       root_cause["rc_reason"]     if root_cause else None,
        } if root_cause else None,
        "logical_anomalies": logical,
    }
