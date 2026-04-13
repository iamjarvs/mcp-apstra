"""
tools/anomaly_analytics.py

Five MCP tools for deeper analysis of the anomaly time-series store.

  get_anomaly_trend           — raise/clear counts per device over time
  get_correlated_faults       — structural de-duplication of bilateral anomalies
  get_fault_durations         — episode durations (raise→clear pairs)
  get_device_anomaly_heatmap  — device × anomaly-type raise-count matrix
  correlate_anomaly_events    — temporal fault clustering with OSI-layer analysis

All tools read from the local SQLite anomaly store (zero Apstra API calls).
The store is backfilled with 7 days of history at server startup and refreshed
every 60 seconds by the background anomaly poller.
"""

from collections import defaultdict
from datetime import datetime, timezone, timedelta

from fastmcp import Context

from primitives.anomaly_clustering import (
    OSI_LAYER,
    cluster_raises,
    enrich_cluster,
)


def register(mcp):

    # ── Tool 1: get_anomaly_trend ─────────────────────────────────────────────

    @mcp.tool()
    async def get_anomaly_trend(
        blueprint_id: str,
        anomaly_type: str,
        hours_back: int = 24,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Show how the rate of a specific anomaly type has changed over time,
        broken down by device.

        Use this tool when you want to answer questions like:
          - "Is the BGP flapping getting better or worse on Leaf2?"
          - "When did the MAC address anomalies start and are they increasing?"
          - "Which device has the highest sustained anomaly rate this week?"

        The bucket granularity is selected automatically from the time window:
          ≤ 6 hours  → 15-minute buckets
          ≤ 24 hours → 1-hour buckets
          ≤ 72 hours → 4-hour buckets
          > 72 hours → 12-hour buckets

        A fabric-wide total is included alongside per-device rows so you can
        distinguish a single device problem from a widespread event.

        Parameters
        ----------
        blueprint_id  : Blueprint to query.
        anomaly_type  : The anomaly type to trend (e.g. "bgp", "mac",
                        "interface", "cabling", "route", "lag").
        hours_back    : How far back to look (1–168).  Default 24 hours.
        instance_name : Target a specific Apstra instance.

        Data source: local SQLite anomaly store (anomaly_timeseries.db)
        Updated: every 60 seconds
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available"}

        hours_back = max(1, min(hours_back, 168))
        since = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

        if hours_back <= 6:
            bucket_minutes = 15
        elif hours_back <= 24:
            bucket_minutes = 60
        elif hours_back <= 72:
            bucket_minutes = 240
        else:
            bucket_minutes = 720

        rows = store.get_trend_buckets(
            blueprint_id=blueprint_id,
            anomaly_type=anomaly_type,
            since=since,
            bucket_minutes=bucket_minutes,
            instance_name=instance_name,
        )

        # Group by device
        by_device: dict[str, list] = defaultdict(list)
        fabric_totals: dict[str, dict] = {}
        for r in rows:
            dev = r["device"] or "unknown"
            by_device[dev].append({"bucket": r["bucket"], "raises": r["raises"], "clears": r["clears"]})
            bucket = r["bucket"]
            if bucket not in fabric_totals:
                fabric_totals[bucket] = {"bucket": bucket, "raises": 0, "clears": 0}
            fabric_totals[bucket]["raises"] += r["raises"]
            fabric_totals[bucket]["clears"] += r["clears"]

        devices_out = []
        for dev, buckets in sorted(by_device.items()):
            total_raises = sum(b["raises"] for b in buckets)
            devices_out.append({
                "device": dev,
                "total_raises": total_raises,
                "trend": buckets,
            })
        # Sort by total raises descending so the most active device is first
        devices_out.sort(key=lambda d: d["total_raises"], reverse=True)

        fabric_list = sorted(fabric_totals.values(), key=lambda b: b["bucket"])

        return {
            "blueprint_id":  blueprint_id,
            "anomaly_type":  anomaly_type,
            "hours_back":    hours_back,
            "bucket_size":   f"{bucket_minutes}m",
            "device_count":  len(devices_out),
            "devices":       devices_out,
            "fabric_total":  fabric_list,
            "_meta": {
                "data_source": "local_anomaly_store",
                "note":        "Bucket timestamps are rounded to the bucket boundary (UTC). "
                               "Raises = new anomaly detected; clears = anomaly resolved.",
            },
        }

    # ── Tool 2: get_correlated_faults ─────────────────────────────────────────

    @mcp.tool()
    async def get_correlated_faults(
        blueprint_id: str,
        anomaly_type: str,
        hours_back: int = 168,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        De-duplicate anomalies that are structurally the same logical fault
        but appear multiple times in the store due to Apstra's storage model.

        Use this tool when you want to answer questions like:
          - "How many distinct BGP sessions are actually down vs. how many
            raw anomaly rows exist?"
          - "Is the cabling anomaly on Spine1 the same physical cable as
            the one shown on Leaf2?"
          - "How many unique logical faults are there after collapsing mirrors?"

        De-duplication rules applied
        ----------------------------
        1. Ghost-row removal — Apstra creates a second identity row for the
           same anomaly with no device hostname.  These are dropped.
        2. BGP bilateral collapse — each BGP session generates an anomaly on
           both endpoints.  Pairs sharing (frozenset{source_ip, dest_ip},
           addr_family, vrf) are collapsed to one logical session entry.
        3. Cabling bilateral collapse — each physical link mismatch generates
           an alert on both cable ends.  Pairs are matched via
           expected.neighbor_name cross-reference.

        Parameters
        ----------
        blueprint_id  : Blueprint to query.
        anomaly_type  : Anomaly type to de-duplicate
                        ("bgp", "cabling", "interface", etc.).
        hours_back    : Look-back window (1–168).  Default 168 (full 7 days).
        instance_name : Target a specific Apstra instance.

        Data source: local SQLite anomaly store (anomaly_timeseries.db)
        Updated: every 60 seconds
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available"}

        hours_back = max(1, min(hours_back, 168))
        since = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

        raises = store.get_raises_in_window(
            blueprint_id=blueprint_id,
            instance_name=instance_name,
            since=since,
            anomaly_type=anomaly_type,
        )

        if not raises:
            return {
                "blueprint_id": blueprint_id,
                "anomaly_type": anomaly_type,
                "raw_count": 0,
                "logical_count": 0,
                "dedup_ratio": 0.0,
                "logical_faults": [],
                "_meta": {"data_source": "local_anomaly_store"},
            }

        from primitives.anomaly_clustering import deduplicate_cluster, tag_osi_layer
        logical = deduplicate_cluster(raises)
        logical = [tag_osi_layer(a) for a in logical]

        raw_count = len(raises)
        logical_count = len(logical)
        dedup_ratio = round(1.0 - logical_count / raw_count, 3) if raw_count else 0.0

        faults_out = []
        for a in logical:
            devices = list({d for d in ([a.get("device")] + (a.get("bilateral_peers") or [])) if d})
            faults_out.append({
                "anomaly_type":    a["anomaly_type"],
                "osi_layer":       a["osi_layer"],
                "osi_label":       a["osi_label"],
                "devices":         devices,
                "identity":        a.get("identity"),
                "expected":        a.get("expected"),
                "first_detected":  a.get("first_detected"),
                "first_raise_in_window": a.get("timestamp"),
                "bilateral_dedup": a.get("bilateral_dedup", False),
                "raw_count":       a.get("raw_count", 1),
            })

        return {
            "blueprint_id":   blueprint_id,
            "anomaly_type":   anomaly_type,
            "hours_back":     hours_back,
            "raw_count":      raw_count,
            "logical_count":  logical_count,
            "dedup_ratio":    dedup_ratio,
            "logical_faults": faults_out,
            "_meta": {
                "data_source": "local_anomaly_store",
                "note": (
                    "dedup_ratio is the fraction of raw rows removed by de-duplication. "
                    "0.5 means half the rows were mirrors/ghosts."
                ),
            },
        }

    # ── Tool 3: get_fault_durations ───────────────────────────────────────────

    @mcp.tool()
    async def get_fault_durations(
        blueprint_id: str,
        anomaly_type: str = None,
        hours_back: int = 168,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Compute how long each anomaly episode lasted by pairing each raise
        event with its subsequent clear.

        Use this tool when you want to answer questions like:
          - "Are the MAC address anomalies brief flaps (seconds) or sustained
            outages (minutes)?"
          - "What is the longest BGP session outage in the past week?"
          - "Which device has experienced the most total downtime for
            interface anomalies?"
          - "Are anomalies getting cleared quickly or are they accumulating?"

        Open episodes (raised but not yet cleared) are included with
        ``cleared_at: null`` and ``duration_s: null``.

        Per-device statistics returned
        ------------------------------
          episode_count       — total number of raise events
          open_episodes       — raises not yet cleared
          mean_duration_s     — average of closed episodes
          max_duration_s      — longest single episode
          total_downtime_s    — sum of all closed episode durations
          episodes            — individual raise/clear pairs (capped at 100)

        Parameters
        ----------
        blueprint_id  : Blueprint to query.
        anomaly_type  : Optional filter by type.
        hours_back    : Look-back window (1–168).  Default 168 (full 7 days).
        instance_name : Target a specific Apstra instance.

        Data source: local SQLite anomaly store (anomaly_timeseries.db)
        Updated: every 60 seconds
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available"}

        hours_back = max(1, min(hours_back, 168))
        since = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

        episodes = store.get_fault_episodes(
            blueprint_id=blueprint_id,
            instance_name=instance_name,
            anomaly_type=anomaly_type,
            since=since,
        )

        if not episodes:
            return {
                "blueprint_id": blueprint_id,
                "anomaly_type": anomaly_type,
                "hours_back": hours_back,
                "device_count": 0,
                "devices": [],
                "_meta": {"data_source": "local_anomaly_store"},
            }

        from primitives.anomaly_clustering import _parse_iso_seconds

        # Group episodes by (device, identity_json) — one entry per anomaly identity
        from collections import defaultdict
        import json
        grouped: dict[tuple, list] = defaultdict(list)
        for ep in episodes:
            key = (ep["device"] or "unknown", json.dumps(ep["identity"], sort_keys=True))
            grouped[key].append(ep)

        devices_out = []
        for (device, _), eps in sorted(grouped.items()):
            closed = []
            open_eps = []
            for ep in eps:
                if ep["cleared_at"]:
                    dur = _parse_iso_seconds(ep["cleared_at"]) - _parse_iso_seconds(ep["raised_at"])
                    closed.append({"raised_at": ep["raised_at"], "cleared_at": ep["cleared_at"], "duration_s": round(dur, 1)})
                else:
                    open_eps.append({"raised_at": ep["raised_at"], "cleared_at": None, "duration_s": None})

            durations = [e["duration_s"] for e in closed]
            mean_dur = round(sum(durations) / len(durations), 1) if durations else None
            max_dur  = max(durations) if durations else None
            total_dt = round(sum(durations), 1) if durations else 0.0

            all_eps = sorted(closed + open_eps, key=lambda e: e["raised_at"])
            devices_out.append({
                "device":         device,
                "anomaly_type":   eps[0]["anomaly_type"],
                "identity":       eps[0]["identity"],
                "role":           eps[0]["role"],
                "episode_count":  len(eps),
                "open_episodes":  len(open_eps),
                "mean_duration_s": mean_dur,
                "max_duration_s":  max_dur,
                "total_downtime_s": total_dt,
                "episodes": all_eps[:100],
            })

        # Sort by total_downtime_s descending
        devices_out.sort(key=lambda d: d["total_downtime_s"], reverse=True)

        return {
            "blueprint_id": blueprint_id,
            "anomaly_type": anomaly_type,
            "hours_back":   hours_back,
            "device_count": len(devices_out),
            "devices":      devices_out,
            "_meta": {
                "data_source": "local_anomaly_store",
                "note": (
                    "Episodes list is capped at 100 per device. "
                    "open_episodes > 0 means the anomaly is currently raised."
                ),
            },
        }

    # ── Tool 4: get_device_anomaly_heatmap ────────────────────────────────────

    @mcp.tool()
    async def get_device_anomaly_heatmap(
        blueprint_id: str,
        hours_back: int = 168,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Show a device-by-type raise-count matrix to quickly identify which
        devices are generating the most anomalies and of what kind.

        Use this tool when you want to answer questions like:
          - "Which device is the most problematic in this blueprint?"
          - "Do any devices show anomalies across multiple layers simultaneously
            (suggesting a major incident)?"
          - "Is the issue isolated to one device or spread across the fabric?"
          - "What is the overall anomaly profile of this blueprint at a glance?"

        The matrix has one row per device and one column per anomaly type seen
        in the window.  Cell values are raw raise counts (not de-duplicated).
        Cells with zero raises are omitted from the per-device type map.

        A ``summary`` section ranks devices by total raises and identifies
        devices with cross-layer anomalies (anomalies at two or more distinct
        OSI layers), which are a strong indicator of a major fault.

        Parameters
        ----------
        blueprint_id  : Blueprint to query.
        hours_back    : Look-back window (1–168).  Default 168 (full 7 days).
        instance_name : Target a specific Apstra instance.

        Data source: local SQLite anomaly store (anomaly_timeseries.db)
        Updated: every 60 seconds
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available"}

        hours_back = max(1, min(hours_back, 168))
        since = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

        rows = store.get_device_type_matrix(
            blueprint_id=blueprint_id,
            since=since,
            instance_name=instance_name,
        )

        if not rows:
            return {
                "blueprint_id": blueprint_id,
                "hours_back": hours_back,
                "device_count": 0,
                "all_types": [],
                "matrix": [],
                "_meta": {"data_source": "local_anomaly_store"},
            }

        # Build matrix
        device_data: dict[str, dict] = defaultdict(lambda: defaultdict(int))
        all_types: set[str] = set()
        for r in rows:
            dev = r["device"] or "unknown"
            device_data[dev][r["anomaly_type"]] += r["raise_count"]
            all_types.add(r["anomaly_type"])

        # Sort types by OSI layer then alphabetically
        sorted_types = sorted(all_types, key=lambda t: (OSI_LAYER.get(t, 99), t))

        matrix = []
        for device in sorted(device_data.keys()):
            types = dict(device_data[device])
            total = sum(types.values())
            layers_present = {OSI_LAYER.get(t, 99) for t in types}
            matrix.append({
                "device":            device,
                "total_raises":      total,
                "types":             types,
                "distinct_osi_layers": len(layers_present),
                "cross_layer_fault": len(layers_present) > 1,
            })

        matrix.sort(key=lambda d: d["total_raises"], reverse=True)

        # Summary: top offenders and cross-layer devices
        cross_layer = [d for d in matrix if d["cross_layer_fault"]]
        summary = {
            "top_device":       matrix[0]["device"] if matrix else None,
            "top_device_raises": matrix[0]["total_raises"] if matrix else 0,
            "cross_layer_devices": [d["device"] for d in cross_layer],
        }

        return {
            "blueprint_id": blueprint_id,
            "hours_back":   hours_back,
            "device_count": len(matrix),
            "all_types":    sorted_types,
            "summary":      summary,
            "matrix":       matrix,
            "_meta": {
                "data_source": "local_anomaly_store",
                "note": (
                    "cross_layer_fault=true means anomalies were raised at two or more "
                    "OSI layers for this device in the window — a strong signal of a "
                    "significant fault event."
                ),
            },
        }

    # ── Tool 5: correlate_anomaly_events ─────────────────────────────────────

    @mcp.tool()
    async def correlate_anomaly_events(
        blueprint_id: str,
        hours_back: int = 24,
        idle_gap_seconds: int = 60,
        min_cluster_size: int = 2,
        anomaly_type: str = None,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Group anomaly raise events into temporal fault clusters and analyse the
        likely OSI-layer cascade within each cluster.

        Use this tool when you want to answer questions like:
          - "Multiple anomaly types fired at roughly the same time — are they
            all caused by the same underlying event?"
          - "A link went down and now I'm seeing BGP and route anomalies —
            what is the root cause and what is the cascade?"
          - "Were there multiple distinct fault events in the last 24 hours,
            or was it all one incident?"
          - "I see cabling, BGP, and interface anomalies on different devices —
            are these related?"

        How it works
        ------------
        1. Temporal clustering — all raise events are sorted by timestamp.
           A new cluster is started whenever consecutive events are separated
           by more than ``idle_gap_seconds``.  This is fabric-change detection:
           it finds moments where new anomalies burst into the fabric,
           regardless of what type or protocol they are.

        2. Structural de-duplication within each cluster:
           - Ghost rows (Apstra stores some anomalies twice) are removed.
           - BGP bilateral pairs (each session appears on both endpoints) are
             collapsed to one logical session.
           - Cabling bilateral pairs (each cable mismatch appears on both
             ends) are collapsed to one logical cable entry.

        3. OSI-layer tagging — every logical anomaly is tagged with its layer:
             L1-physical (cabling) → L2-link (interface, lag) →
             L3-network (deployment, liveness, config) →
             L4-routing (bgp, route) → L5-overlay (mac) → L6-telemetry (probe)

        4. Root-cause scoring — the anomaly at the lowest OSI layer is
           identified as the root-cause candidate.  Ties are broken by
           structural centrality: an anomaly whose devices are referenced
           by other anomalies in the cluster scores higher.  Confidence is
           reported explicitly (high / medium / low) so you can judge whether
           manual investigation is needed.

        5. Causal chain — the ordered sequence of OSI layers present in the
           cluster, showing how a lower-layer event cascaded upward.

        Important caveats
        -----------------
        - Clustering is *temporal*, not causal.  Two anomalies in the same
          cluster fired close together in time — they may or may not share a
          root cause.  The tool flags uncertainty explicitly.
        - Root-cause scoring is heuristic.  "High confidence" means the
          evidence is consistent with a single root cause; it does not mean
          the cause is definitively proven.
        - The tool only has visibility into anomalies in the local store.
          If an anomaly was cleared before backfill started it will not appear.

        Parameters
        ----------
        blueprint_id       : Blueprint to query.
        hours_back         : How far back to look (1–168).  Default 24 hours.
        idle_gap_seconds   : Gap between consecutive events that starts a new
                             cluster.  Default 60 s.  Increase to 120–300 for
                             noisy environments; lower to 15–30 for precise
                             incident separation.
        min_cluster_size   : Minimum raw raises to include a cluster in the
                             output.  Default 2 (suppresses isolated events).
                             Set to 1 to see all events including singletons.
        anomaly_type       : Optional filter — only show clusters that contain
                             at least one anomaly of this type.
        instance_name      : Target a specific Apstra instance.

        Data source: local SQLite anomaly store (anomaly_timeseries.db)
        Updated: every 60 seconds
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available"}

        hours_back        = max(1, min(hours_back, 168))
        idle_gap_seconds  = max(5, min(idle_gap_seconds, 3600))
        min_cluster_size  = max(1, min_cluster_size)

        since = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

        raises = store.get_raises_in_window(
            blueprint_id=blueprint_id,
            instance_name=instance_name,
            since=since,
        )

        if not raises:
            return {
                "blueprint_id": blueprint_id,
                "hours_back":   hours_back,
                "total_raises": 0,
                "cluster_count": 0,
                "clusters": [],
                "_meta": {"data_source": "local_anomaly_store"},
            }

        raw_clusters = cluster_raises(
            raises,
            idle_gap_seconds=idle_gap_seconds,
            min_size=min_cluster_size,
        )

        # Optional post-filter: only keep clusters containing the requested type
        if anomaly_type:
            raw_clusters = [
                c for c in raw_clusters
                if any(r["anomaly_type"] == anomaly_type for r in c)
            ]

        enriched = [enrich_cluster(c, i) for i, c in enumerate(raw_clusters)]

        # Sort newest cluster first (most operationally relevant)
        enriched.sort(key=lambda c: c["started_at"], reverse=True)

        return {
            "blueprint_id":       blueprint_id,
            "hours_back":         hours_back,
            "idle_gap_seconds":   idle_gap_seconds,
            "min_cluster_size":   min_cluster_size,
            "total_raises":       len(raises),
            "cluster_count":      len(enriched),
            "clusters":           enriched,
            "_meta": {
                "data_source": "local_anomaly_store",
                "note": (
                    "Clusters are ordered newest-first. "
                    "root_cause_candidate is a heuristic — treat 'low' confidence "
                    "results as requiring manual investigation. "
                    "causal_chain shows OSI layers present in the cluster from "
                    "lowest to highest."
                ),
            },
        }
