from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Annotated

from fastmcp import Context
from pydantic import Field

from handlers.blueprints import resolve_blueprints
from primitives.anomaly_clustering import (
    OSI_LAYER,
    cluster_raises,
    enrich_cluster,
)

_BP_DESC = (
    "Apstra blueprint ID, partial label, or null. "
    "Pass null or 'all' for every blueprint. "
    "Pass a partial name (e.g. 'DC1') to match by label. "
    "Pass a full UUID for a specific blueprint."
)


def register(mcp):

    # ── Tool 1: get_anomaly_trend ─────────────────────────────────────────────

    @mcp.tool()
    async def get_anomaly_trend(
        blueprint_id: Annotated[str | None, Field(default=None, description=_BP_DESC)] = None,
        anomaly_type: Annotated[
            str,
            "Anomaly type to trend (e.g. 'bgp', 'mac', 'interface', 'cabling', 'route', 'lag').",
        ] = None,
        hours_back: Annotated[
            int,
            Field(default=24, description="Look-back window (1–168 hours). Default 24.", ge=1, le=168),
        ] = 24,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Show raise/clear rates for one anomaly type over time, broken down by device.

        Use this to determine whether a fault is getting better or worse, find when it
        started, and identify which devices are driving it. Bucket granularity is selected
        automatically from the time window (≤64 h: 15 min; ≤24 h: 1 hr; ≤72 h: 4 hr; >72 h:
        12 hr). A fabric-wide total is included alongside per-device rows so you can
        distinguish a single-device problem from a fabric-wide event.

        Returns: devices (list with device hostname, total_raises, trend list of
        {bucket, raises, clears}) sorted by total_raises descending; fabric_total
        (same buckets aggregated across all devices); bucket_size.
        Data source: local SQLite anomaly store (updated every 60 s).
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available"}

        blu_list = await resolve_blueprints(ctx.lifespan_context["sessions"], blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}
        if len(blu_list) > 1:
            results = []
            for bp in blu_list:
                r = await get_anomaly_trend(
                    blueprint_id=bp["id"], anomaly_type=anomaly_type,
                    hours_back=hours_back, instance_name=instance_name, ctx=ctx,
                )
                r["blueprint_label"] = bp["label"]
                results.append(r)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "results": results}
        blueprint_id = blu_list[0]["id"]

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
        blueprint_id: Annotated[str | None, Field(default=None, description=_BP_DESC)] = None,
        anomaly_type: Annotated[
            str,
            "Anomaly type to de-duplicate ('bgp', 'cabling', 'interface', 'mac', etc.).",
        ] = None,
        hours_back: Annotated[
            int,
            Field(default=168, description="Look-back window (1–168 hours). Default 168 (full 7 days).", ge=1, le=168),
        ] = 168,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        De-duplicate anomalies that appear multiple times due to Apstra's bilateral storage model.

        Use this when the raw raise count looks inflated and you want to know how many distinct
        logical faults actually exist. Applies three rules: (1) ghost-row removal — Apstra stores
        some anomalies twice with no device hostname, these are dropped; (2) BGP bilateral
        collapse — each BGP session generates an anomaly on both endpoints, pairs sharing the
        same session IPs are collapsed to one entry; (3) cabling bilateral collapse — each cable
        mismatch fires on both ends, pairs are matched via expected.neighbor_name.

        Returns: raw_count, logical_count, dedup_ratio (fraction of rows removed), logical_faults
        (list with anomaly_type, osi_layer, osi_label, devices, identity, first_detected,
        bilateral_dedup bool, raw_count). Data source: local SQLite anomaly store.
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available"}

        blu_list = await resolve_blueprints(ctx.lifespan_context["sessions"], blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}
        if len(blu_list) > 1:
            results = []
            for bp in blu_list:
                r = await get_correlated_faults(
                    blueprint_id=bp["id"], anomaly_type=anomaly_type,
                    hours_back=hours_back, instance_name=instance_name, ctx=ctx,
                )
                r["blueprint_label"] = bp["label"]
                results.append(r)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "results": results}
        blueprint_id = blu_list[0]["id"]

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
        blueprint_id: Annotated[str | None, Field(default=None, description=_BP_DESC)] = None,
        anomaly_type: Annotated[
            str | None,
            Field(default=None, description="Filter by anomaly type (e.g. 'bgp', 'interface'). Omit for all types."),
        ] = None,
        hours_back: Annotated[
            int,
            Field(default=168, description="Look-back window (1–168 hours). Default 168 (full 7 days).", ge=1, le=168),
        ] = 168,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Compute how long each anomaly episode lasted by pairing each raise with its subsequent clear.

        Use this to distinguish brief flaps from sustained outages, find the longest outage in
        a window, measure total downtime per device, and check whether anomalies are being resolved
        promptly. Open episodes (raised but not yet cleared) are included with duration_s null.

        Returns one entry per unique (device, anomaly identity) pair with: episode_count,
        open_episodes, mean_duration_s, max_duration_s, total_downtime_s, and individual
        episodes list (capped at 100 per device, each with raised_at, cleared_at, duration_s).
        Sorted by total_downtime_s descending. Data source: local SQLite anomaly store.
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available"}

        blu_list = await resolve_blueprints(ctx.lifespan_context["sessions"], blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}
        if len(blu_list) > 1:
            results = []
            for bp in blu_list:
                r = await get_fault_durations(
                    blueprint_id=bp["id"], anomaly_type=anomaly_type,
                    hours_back=hours_back, instance_name=instance_name, ctx=ctx,
                )
                r["blueprint_label"] = bp["label"]
                results.append(r)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "results": results}
        blueprint_id = blu_list[0]["id"]

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
        blueprint_id: Annotated[str | None, Field(default=None, description=_BP_DESC)] = None,
        hours_back: Annotated[
            int,
            Field(default=168, description="Look-back window (1–168 hours). Default 168 (full 7 days).", ge=1, le=168),
        ] = 168,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Return a device × anomaly-type raise-count matrix for rapid fabric-wide triage.

        Use this as a first-pass view to identify the most problematic devices and
        anomaly types in a blueprint. Devices with anomalies spanning two or more OSI
        layers simultaneously are flagged as cross_layer_fault — a strong indicator of a
        major incident (e.g. a physical link failure cascading to BGP and MAC anomalies).
        Follow up with correlate_anomaly_events to identify the probable root cause.

        Returns: matrix (per-device row with total_raises, types dict of anomaly_type →
        raise count, distinct_osi_layers, cross_layer_fault bool) sorted by total_raises
        descending; summary (top device, cross_layer_devices list); all_types (sorted by
        OSI layer). Data source: local SQLite anomaly store.
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available"}

        blu_list = await resolve_blueprints(ctx.lifespan_context["sessions"], blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}
        if len(blu_list) > 1:
            results = []
            for bp in blu_list:
                r = await get_device_anomaly_heatmap(
                    blueprint_id=bp["id"], hours_back=hours_back,
                    instance_name=instance_name, ctx=ctx,
                )
                r["blueprint_label"] = bp["label"]
                results.append(r)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "results": results}
        blueprint_id = blu_list[0]["id"]

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
        blueprint_id: Annotated[str | None, Field(default=None, description=_BP_DESC)] = None,
        hours_back: Annotated[
            int,
            Field(default=24, description="Look-back window (1–168 hours). Default 24.", ge=1, le=168),
        ] = 24,
        idle_gap_seconds: Annotated[
            int,
            Field(
                default=60,
                description=(
                    "Gap in seconds between consecutive events that starts a new cluster. "
                    "Increase to 120–300 for noisy environments; lower to 15–30 for precise "
                    "incident separation. Default 60 s."
                ),
            ),
        ] = 60,
        min_cluster_size: Annotated[
            int,
            Field(default=2, description="Minimum raise events to include a cluster. Default 2; set to 1 to include singletons."),
        ] = 2,
        anomaly_type: Annotated[
            str | None,
            Field(default=None, description="Only return clusters containing at least one event of this anomaly type."),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(default=None, description="Apstra instance name. Do not ask the user for this — leave as None to query all instances. Only set if the user explicitly names a specific instance."),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Cluster temporally related anomaly raise events and identify the probable root-cause OSI layer.

        Use this during incident investigation when multiple anomaly types fire around the same
        time and you want to know whether they share a common root cause. Groups raise events
        into bursts separated by idle_gap_seconds, de-duplicates bilateral anomalies within
        each cluster, tags each anomaly with its OSI layer (L1-physical through L6-telemetry),
        identifies the lowest-layer anomaly as the root-cause candidate, and reports confidence
        (high/medium/low) based on structural consistency.

        Each cluster includes: cluster_id, started_at, ended_at, duration_s, event_count,
        devices (list), anomaly_types (list), osi_layers (list), probable_root_cause_layer,
        root_cause_confidence, causal_chain (ordered OSI layer sequence), and events (list).
        Clusters returned newest-first. Data source: local SQLite anomaly store.
        """
        store = ctx.lifespan_context.get("anomaly_store")
        if store is None:
            return {"error": "anomaly_store not available"}

        blu_list = await resolve_blueprints(ctx.lifespan_context["sessions"], blueprint_id)
        if not blu_list:
            return {"error": f"No blueprints found matching '{blueprint_id}'"}
        if len(blu_list) > 1:
            results = []
            for bp in blu_list:
                r = await correlate_anomaly_events(
                    blueprint_id=bp["id"], hours_back=hours_back,
                    idle_gap_seconds=idle_gap_seconds, min_cluster_size=min_cluster_size,
                    anomaly_type=anomaly_type, instance_name=instance_name, ctx=ctx,
                )
                r["blueprint_label"] = bp["label"]
                results.append(r)
            return {"blueprint_count": len(results), "blueprint_ref": blueprint_id, "results": results}
        blueprint_id = blu_list[0]["id"]

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
