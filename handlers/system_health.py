import difflib

from primitives import live_data_client
from handlers.systems import handle_get_systems


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------

async def handle_get_system_liveness(
    sessions,
    blueprint_id: str,
    instance_name: str | None = None,
) -> dict:
    """
    Checks which systems in a blueprint are unreachable according to Apstra's
    liveness anomaly feed. Returns a structured summary per instance.

    A device appearing in the liveness anomaly list means one or more of its
    management/telemetry agents are not responding. This is a critical signal —
    any further troubleshooting against that device (BGP, interfaces, CLI) may
    fail or return stale data until reachability is restored.
    """
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            raw = await live_data_client.get_liveness_anomalies(session, blueprint_id)
            parsed = _parse_liveness(raw)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "all_systems_reachable": len(parsed) == 0,
                "unreachable_count": len(parsed),
                "liveness_anomalies": parsed,
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
                "all_systems_reachable": None,
                "unreachable_count": 0,
                "liveness_anomalies": [],
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "results": all_results,
        "total_unreachable": sum(r.get("unreachable_count", 0) for r in all_results),
        "all_systems_reachable": all(r.get("all_systems_reachable", False) for r in all_results),
    }


def _parse_liveness(raw: dict) -> list[dict]:
    """
    Parses the raw liveness anomaly response into a summarised list.
    Determines which agents are expected but not responding by comparing
    the expected and actual agent lists.
    """
    results = []
    for item in raw.get("items", []):
        expected_agents = item.get("expected", {}).get("agents", [])
        actual_agents = item.get("actual", {}).get("agents", [])

        # Determine agents that are expected but not currently responding
        actual_set = set(actual_agents)
        missing_agents = [a for a in expected_agents if a not in actual_set]

        results.append({
            "anomaly_id": item.get("id"),
            "role": item.get("role"),
            "identity": item.get("identity", {}),
            "severity": item.get("severity"),
            "last_modified_at": item.get("last_modified_at"),
            "expected_agent_count": len(expected_agents),
            "responding_agent_count": len(actual_agents),
            "non_responding_agents": missing_agents,
            "all_agents_alive": item.get("actual", {}).get("alive", False),
        })
    return results


# ---------------------------------------------------------------------------
# Config deviation
# ---------------------------------------------------------------------------

async def handle_get_config_deviations(
    sessions,
    registry,
    blueprint_id: str,
    system_id: str | list[str] | None = None,
    instance_name: str | None = None,
) -> dict:
    """
    Checks the live vs intended configuration for one or more systems. When
    `system_id` is None all systems in the blueprint are checked.

    A `deploy_state` of "deviated" means someone (or something) has changed
    the device config outside of Apstra, or Apstra has not yet pushed a
    pending change. The diff shows exactly what differs:

      Lines marked (+) are present on the device but NOT in Apstra's intent
      — these were manually added or injected outside of Apstra management.

      Lines marked (-) are in Apstra's intent but NOT on the device
      — these were removed from the device without going through Apstra.
    """
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            # Resolve which system_ids to check
            if system_id is None:
                sys_result = await handle_get_systems(
                    [session], registry, blueprint_id, session.name
                )
                systems = sys_result.get("systems", [])
                device_keys = [
                    s["system_id"] for s in systems if s.get("system_id")
                ]
                hostname_map = {s["system_id"]: s.get("label") or s.get("hostname") for s in systems}
            else:
                ids = [system_id] if isinstance(system_id, str) else system_id
                device_keys = ids
                # No graph lookup needed for a specific request
                sys_result = await handle_get_systems(
                    [session], registry, blueprint_id, session.name
                )
                hostname_map = {
                    s["system_id"]: s.get("label") or s.get("hostname")
                    for s in sys_result.get("systems", [])
                }

            deviations = []
            compliant = []

            for dk in device_keys:
                try:
                    cfg = await live_data_client.get_system_configuration(session, dk)
                except Exception as fetch_err:
                    deviations.append({
                        "system_id": dk,
                        "hostname": hostname_map.get(dk),
                        "error": str(fetch_err),
                        "deploy_state": "unknown",
                        "deviated": None,
                        "diff": None,
                    })
                    continue

                deploy_state = cfg.get("deploy_state", "unknown")
                deviated = cfg.get("deviated", False)

                if deviated:
                    expected_cfg = cfg.get("expected", {}).get("config", "")
                    actual_cfg = cfg.get("actual", {}).get("config", "")
                    diff_text = _compute_diff(expected_cfg, actual_cfg, dk)
                    deviations.append({
                        "system_id": dk,
                        "hostname": hostname_map.get(dk),
                        "deploy_state": deploy_state,
                        "deviated": True,
                        "diff": diff_text,
                        "error_message": cfg.get("error_message") or None,
                        "contiguous_failures": cfg.get("contiguous_failures", 0),
                    })
                else:
                    compliant.append({
                        "system_id": dk,
                        "hostname": hostname_map.get(dk),
                        "deploy_state": deploy_state,
                    })

            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "total_checked": len(device_keys),
                "deviated_count": len(deviations),
                "all_compliant": len(deviations) == 0,
                "deviations": deviations,
                "compliant_systems": compliant,
            })

        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
                "total_checked": 0,
                "deviated_count": 0,
                "all_compliant": None,
                "deviations": [],
                "compliant_systems": [],
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "results": all_results,
        "total_deviated": sum(r.get("deviated_count", 0) for r in all_results),
        "all_compliant": all(r.get("all_compliant", False) for r in all_results),
    }


def _compute_diff(expected: str, actual: str, label: str) -> str:
    """
    Produces a unified diff between the expected (Apstra intent) and actual
    (device live config) strings. Returns only the diff hunks — not the full
    config — so the result is compact and focused on what changed.

    Lines starting with '-' are in intent but missing from device.
    Lines starting with '+' are on device but absent from intent.
    Context lines (no prefix) show surrounding unchanged config for location.
    """
    expected_lines = expected.splitlines(keepends=True)
    actual_lines = actual.splitlines(keepends=True)

    diff_lines = list(difflib.unified_diff(
        expected_lines,
        actual_lines,
        fromfile=f"{label} — Apstra intent (expected)",
        tofile=f"{label} — device live config (actual)",
        n=3,
    ))

    if not diff_lines:
        return "(no textual differences detected)"

    return "".join(diff_lines)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _select_sessions(sessions, instance_name):
    if instance_name is None or instance_name == "all":
        return sessions
    matched = [s for s in sessions if s.name == instance_name]
    if not matched:
        raise ValueError(f"No instance named '{instance_name}' in session pool")
    return matched
