import asyncio
from typing import Annotated, Literal

from fastmcp import Context
from pydantic import Field

from handlers.blueprints import handle_get_blueprint_build_errors, resolve_blueprints
from handlers.system_health import handle_get_system_liveness, handle_get_config_deviations

_BP_DESC = (
    "Apstra blueprint ID, partial label, or null. "
    "Pass null or 'all' for every blueprint. "
    "Pass a partial name (e.g. 'DC1') to match by label. "
    "Pass a full UUID for a specific blueprint."
)

_ACTIVE_ANOMALY_LIMIT = 100  # cap active anomaly rows in incident_snapshot for payload size


def _get_active_anomalies(store, blueprint_id: str, instance_name: str | None) -> dict:
    """
    Reads currently active anomalies from the local store. Zero API calls.
    Returns a compact summary to keep triage payload bounded.
    """
    if store is None:
        return {
            "available": False,
            "hint": "anomaly_store not yet populated (~60 s after server start). "
                    "Use anomaly intent='current_live' for real-time data.",
        }

    try:
        active = store.get_currently_active(blueprint_id, instance_name)
    except Exception as exc:
        return {"available": False, "error": str(exc)}

    from collections import Counter
    by_type: Counter = Counter(a.get("anomaly_type", "unknown") for a in active)

    return {
        "available":    True,
        "active_count": len(active),
        "by_type":      dict(by_type.most_common()),
        "sample":       active[:_ACTIVE_ANOMALY_LIMIT],
        "has_more":     len(active) > _ACTIVE_ANOMALY_LIMIT,
        "_meta": {
            "data_source": "local_anomaly_store",
            "note":        "Use anomaly intent='active' for the full untruncated list.",
        },
    }


def register(mcp):

    @mcp.tool()
    async def triage(
        intent: Annotated[
            Literal[
                "baseline",
                "commit_blockers",
                "drift",
                "active_anomalies",
                "incident_snapshot",
            ],
            Field(
                default="baseline",
                description=(
                    "Triage intent. "
                    "baseline — device reachability (always run first). "
                    "commit_blockers — blueprint build errors that prevent commit. "
                    "drift — expected vs actual config deviation per device. "
                    "active_anomalies — currently raised anomalies from local store (zero API calls). "
                    "incident_snapshot — parallel baseline + active_anomalies + commit_blockers "
                    "for a rapid fabric state snapshot."
                ),
            ),
        ] = "baseline",
        blueprint_id: Annotated[
            str | None,
            Field(default=None, description=_BP_DESC),
        ] = None,
        system_id: Annotated[
            str | list[str] | None,
            Field(
                default=None,
                description=(
                    "Hardware chassis serial(s) to scope drift checks. "
                    "Pass None to check all systems in the blueprint (slower). "
                    "Only used for the drift intent."
                ),
            ),
        ] = None,
        instance_name: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Apstra instance name. Do not ask the user for this — leave as None "
                    "to query all instances. Only set if the user explicitly names a "
                    "specific instance."
                ),
            ),
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """
        Umbrella dispatcher for fabric triage — always start here before any
        targeted investigation.

        Recommended call sequence:
          1) triage intent='baseline'       — verify device reachability
          2) triage intent='active_anomalies' — snapshot of what Apstra has flagged
          3) triage intent='commit_blockers' — check for anything blocking commits
          4) triage intent='drift'          — detect out-of-band config changes
             (heavier: makes per-device API calls)

        Or use intent='incident_snapshot' to run baseline + active_anomalies +
        commit_blockers in parallel for a rapid combined picture (skip drift unless
        you specifically need config deviation detail).

        Intent mapping:
          baseline          → get_system_liveness
          commit_blockers   → get_blueprint_build_errors
          drift             → get_config_deviations
          active_anomalies  → anomaly_store.get_currently_active (no live API call)
          incident_snapshot → baseline + active_anomalies + commit_blockers (parallel)

        IMPORTANT:
          - If baseline returns unreachable devices, do NOT run CLI commands against
            those devices — they will fail or return stale data.
          - If drift shows a deviated device AND it has anomalies, investigate the drift
            first — manual changes outside Apstra are the most common root cause.
          - active_anomalies reads the local store (populated by background poller every
            60 s). On a freshly started server the store may be empty for the first minute.
        """
        sessions = ctx.lifespan_context["sessions"]
        registry = ctx.lifespan_context["graph_registry"]
        anomaly_store = ctx.lifespan_context.get("anomaly_store")

        blu_list = await resolve_blueprints(sessions, blueprint_id)
        if not blu_list:
            return {
                "error": f"No blueprints found matching '{blueprint_id}'",
                "hint":  "Call get_blueprints to list available blueprints.",
            }

        # ── baseline ──────────────────────────────────────────────────────────
        if intent == "baseline":
            if len(blu_list) > 1:
                results = []
                for bp in blu_list:
                    r = await handle_get_system_liveness(sessions, bp["id"], instance_name)
                    r["blueprint_label"] = bp["label"]
                    results.append(r)
                total_unreachable = sum(r.get("unreachable_count", 0) for r in results)
                return {
                    "intent":               "baseline",
                    "blueprint_count":      len(results),
                    "blueprint_ref":        blueprint_id,
                    "total_unreachable":    total_unreachable,
                    "all_systems_reachable": total_unreachable == 0,
                    "results":              results,
                }
            bp = blu_list[0]
            r = await handle_get_system_liveness(sessions, bp["id"], instance_name)
            r["intent"] = "baseline"
            r["blueprint_label"] = bp["label"]
            return r

        # ── commit_blockers ───────────────────────────────────────────────────
        if intent == "commit_blockers":
            if len(blu_list) > 1:
                results = []
                for bp in blu_list:
                    r = await handle_get_blueprint_build_errors(
                        sessions, bp["id"], instance_name
                    )
                    r["blueprint_label"] = bp["label"]
                    results.append(r)
                total_errors = sum(r.get("errors_count", 0) for r in results)
                total_warnings = sum(r.get("warnings_count", 0) for r in results)
                return {
                    "intent":                   "commit_blockers",
                    "blueprint_count":          len(results),
                    "blueprint_ref":            blueprint_id,
                    "total_errors_count":       total_errors,
                    "total_warnings_count":     total_warnings,
                    "blocking_blueprint_count": sum(
                        1 for r in results if r.get("has_blocking_errors")
                    ),
                    "results": results,
                }
            bp = blu_list[0]
            r = await handle_get_blueprint_build_errors(sessions, bp["id"], instance_name)
            r["intent"] = "commit_blockers"
            r["blueprint_label"] = bp["label"]
            return r

        # ── drift ─────────────────────────────────────────────────────────────
        if intent == "drift":
            if len(blu_list) > 1:
                results = []
                for bp in blu_list:
                    r = await handle_get_config_deviations(
                        sessions, registry, bp["id"],
                        system_id=system_id,
                        instance_name=instance_name,
                    )
                    r["blueprint_label"] = bp["label"]
                    results.append(r)
                total_deviated = sum(r.get("deviated_count", 0) for r in results)
                return {
                    "intent":           "drift",
                    "blueprint_count":  len(results),
                    "blueprint_ref":    blueprint_id,
                    "total_deviated":   total_deviated,
                    "results":          results,
                }
            bp = blu_list[0]
            r = await handle_get_config_deviations(
                sessions, registry, bp["id"],
                system_id=system_id,
                instance_name=instance_name,
            )
            r["intent"] = "drift"
            r["blueprint_label"] = bp["label"]
            return r

        # ── active_anomalies ──────────────────────────────────────────────────
        if intent == "active_anomalies":
            if len(blu_list) > 1:
                results = []
                for bp in blu_list:
                    r = _get_active_anomalies(anomaly_store, bp["id"], instance_name)
                    r["blueprint_id"] = bp["id"]
                    r["blueprint_label"] = bp["label"]
                    results.append(r)
                total_active = sum(r.get("active_count", 0) for r in results)
                return {
                    "intent":           "active_anomalies",
                    "blueprint_count":  len(results),
                    "total_active":     total_active,
                    "results":          results,
                }
            bp = blu_list[0]
            r = _get_active_anomalies(anomaly_store, bp["id"], instance_name)
            r["intent"] = "active_anomalies"
            r["blueprint_id"] = bp["id"]
            r["blueprint_label"] = bp["label"]
            return r

        # ── incident_snapshot ─────────────────────────────────────────────────
        # Run baseline, active_anomalies, and commit_blockers in parallel.
        # Drift is excluded here — it makes per-device API calls and is slow.
        async def _snapshot_for_blueprint(bp: dict) -> dict:
            bp_id = bp["id"]
            bp_label = bp["label"]

            liveness_task = asyncio.create_task(
                handle_get_system_liveness(sessions, bp_id, instance_name)
            )
            build_errors_task = asyncio.create_task(
                handle_get_blueprint_build_errors(sessions, bp_id, instance_name)
            )
            # active anomalies is sync (store read) — run after gather
            liveness_r, build_r = await asyncio.gather(
                liveness_task, build_errors_task, return_exceptions=True
            )

            def _safe(result, default_key: str) -> dict:
                if isinstance(result, Exception):
                    return {"error": str(result)}
                return result

            anomaly_r = _get_active_anomalies(anomaly_store, bp_id, instance_name)

            unreachable = (
                _safe(liveness_r, "liveness").get("unreachable_count", 0)
                if not isinstance(liveness_r, Exception) else None
            )
            blocking_errors = (
                _safe(build_r, "build").get("has_blocking_errors", False)
                if not isinstance(build_r, Exception) else None
            )
            active_count = anomaly_r.get("active_count", 0)

            return {
                "blueprint_id":      bp_id,
                "blueprint_label":   bp_label,
                "unreachable_count": unreachable,
                "active_anomaly_count": active_count,
                "has_blocking_errors": blocking_errors,
                "needs_attention":   bool(
                    (unreachable or 0) > 0
                    or active_count > 0
                    or blocking_errors
                ),
                "liveness":          _safe(liveness_r, "liveness"),
                "active_anomalies":  anomaly_r,
                "commit_blockers":   _safe(build_r, "build"),
            }

        snapshot_tasks = [_snapshot_for_blueprint(bp) for bp in blu_list]
        snapshots = await asyncio.gather(*snapshot_tasks, return_exceptions=True)
        results = [
            s if not isinstance(s, Exception)
            else {"error": str(s)}
            for s in snapshots
        ]

        attention_count = sum(1 for r in results if r.get("needs_attention"))
        return {
            "intent":             "incident_snapshot",
            "blueprint_count":    len(results),
            "blueprints_needing_attention": attention_count,
            "results":            results,
            "_meta": {
                "note": (
                    "drift (config deviations) is excluded from incident_snapshot "
                    "because it makes per-device API calls. Run triage intent='drift' "
                    "separately if config deviation is suspected."
                ),
            },
        }
