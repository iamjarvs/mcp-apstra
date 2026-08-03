import difflib
from collections import Counter

from primitives import live_data_client, response_parser
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
# Active system-agent jobs
# ---------------------------------------------------------------------------

async def handle_get_active_system_agent_jobs(
    sessions,
    registry,
    instance_name: str | None = None,
) -> dict:
    """
    Checks whether Apstra currently has active system-agent jobs running on
    devices. Each active job is enriched with system-agent identity and, when
    possible, matched back to blueprint inventory so the device context is clear
    before deeper troubleshooting begins.
    """
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            raw_jobs = await live_data_client.get_active_system_agent_jobs(session)
            job_items = raw_jobs.get("items", [])

            if not job_items:
                all_results.append({
                    "instance": session.name,
                    "has_active_jobs": False,
                    "active_job_count": 0,
                    "active_jobs": [],
                    "summary": {
                        "by_job_type": {},
                        "by_state": {},
                        "impacted_device_count": 0,
                        "impacted_blueprint_count": 0,
                        "impacted_blueprints": [],
                    },
                    "guidance": "No active system-agent jobs detected.",
                })
                continue

            agent_lookup_error = None
            blueprint_lookup_error = None
            agents_by_host_id = {}
            blueprint_index = {"system_id": {}, "hostname": {}}

            try:
                raw_agents = await live_data_client.get_system_agents(session)
                agents_by_host_id = _index_system_agents(raw_agents.get("items", []))
            except Exception as exc:
                agent_lookup_error = str(exc)

            try:
                blueprint_index = await _build_blueprint_system_index(session, registry)
            except Exception as exc:
                blueprint_lookup_error = str(exc)

            active_jobs = _enrich_active_jobs(job_items, agents_by_host_id, blueprint_index)
            result = {
                "instance": session.name,
                "has_active_jobs": len(active_jobs) > 0,
                "active_job_count": len(active_jobs),
                "active_jobs": active_jobs,
                "summary": _summarize_active_jobs(active_jobs),
                "guidance": (
                    "Rule out these in-progress device jobs before deep protocol or CLI "
                    "troubleshooting. Upgrades, reboots, and connectivity checks can "
                    "create transient symptoms."
                ),
            }
            if agent_lookup_error or blueprint_lookup_error:
                result["lookup_warnings"] = {
                    "system_agent_lookup_error": agent_lookup_error,
                    "blueprint_lookup_error": blueprint_lookup_error,
                }
            all_results.append(result)
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "error": str(e),
                "has_active_jobs": None,
                "active_job_count": 0,
                "active_jobs": [],
            })

    if len(all_results) == 1:
        return all_results[0]

    total_active_jobs = sum(r.get("active_job_count", 0) for r in all_results)
    return {
        "instance": "all",
        "results": all_results,
        "total_active_jobs": total_active_jobs,
        "has_active_jobs": total_active_jobs > 0,
    }


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


def _first_non_empty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _job_guidance(job_type: str | None, state: str | None) -> str:
    normalized_job_type = str(job_type or "").strip().lower()
    normalized_state = str(state or "").strip().lower()

    if normalized_job_type == "upgrade":
        return (
            "An upgrade workflow is active on this device. Expect temporary "
            "reachability loss, reconnects, or config churn until it completes."
        )
    if normalized_job_type == "reboot":
        return (
            "A reboot workflow is active on this device. Intermittent loss of "
            "reachability is expected until the device is back online."
        )
    if normalized_state == "inprogress":
        return (
            "A system-agent workflow is still in progress on this device. Rule it "
            "out before attributing symptoms to protocol-specific faults."
        )
    return "An active system-agent workflow may be affecting device behavior."


def _index_system_agents(agent_items: list[dict]) -> dict[str, dict]:
    indexed: dict[str, dict] = {}

    for item in agent_items:
        if not isinstance(item, dict):
            continue
        for candidate in (
            item.get("id"),
            item.get("config", {}).get("id"),
            item.get("running_config", {}).get("id"),
            item.get("last_job_status", {}).get("host_id"),
        ):
            if candidate and candidate not in indexed:
                indexed[str(candidate)] = item

    return indexed


async def _build_blueprint_system_index(session, registry) -> dict[str, dict[str, list[dict]]]:
    raw_blueprints = await live_data_client.get_blueprints(session)
    blueprints = response_parser.parse_blueprints(raw_blueprints)
    index = {"system_id": {}, "hostname": {}}

    for blueprint in blueprints:
        systems_result = await handle_get_systems([session], registry, blueprint["id"], session.name)
        for system in systems_result.get("systems", []):
            match = {
                "blueprint_id": blueprint["id"],
                "blueprint_label": blueprint["label"],
                "system_id": system.get("system_id"),
                "system_label": system.get("label"),
                "hostname": system.get("hostname"),
                "role": system.get("role"),
                "management_level": system.get("management_level"),
                "deploy_mode": system.get("deploy_mode"),
            }
            system_id = system.get("system_id")
            if system_id:
                index["system_id"].setdefault(str(system_id), []).append(match)
            hostname = _first_non_empty(system.get("hostname"), system.get("label"))
            if hostname:
                index["hostname"].setdefault(str(hostname).lower(), []).append(match)

    for values in index.values():
        for matches in values.values():
            matches.sort(key=lambda item: (item.get("blueprint_label") or "", item.get("system_label") or ""))

    return index


def _resolve_blueprint_matches(
    system_id: str | None,
    hostname: str | None,
    blueprint_index: dict[str, dict[str, list[dict]]],
) -> list[dict]:
    matches: list[dict] = []
    seen: set[tuple[str | None, str | None]] = set()

    if system_id:
        for match in blueprint_index.get("system_id", {}).get(str(system_id), []):
            signature = (match.get("blueprint_id"), match.get("system_id"))
            if signature in seen:
                continue
            seen.add(signature)
            matches.append(match)

    if not matches and hostname:
        for match in blueprint_index.get("hostname", {}).get(str(hostname).lower(), []):
            signature = (match.get("blueprint_id"), match.get("system_id"))
            if signature in seen:
                continue
            seen.add(signature)
            matches.append(match)

    return matches


def _enrich_active_jobs(
    job_items: list[dict],
    agents_by_host_id: dict[str, dict],
    blueprint_index: dict[str, dict[str, list[dict]]],
) -> list[dict]:
    active_jobs = []

    for job in job_items:
        host_id = str(job.get("host_id") or "")
        agent = agents_by_host_id.get(host_id, {})

        system_id = _first_non_empty(
            agent.get("status", {}).get("system_id"),
            agent.get("platform_status", {}).get("system_id"),
        )
        hostname = _first_non_empty(
            agent.get("device_facts", {}).get("hostname"),
            agent.get("config", {}).get("label"),
            agent.get("running_config", {}).get("label"),
        )
        blueprint_matches = _resolve_blueprint_matches(system_id, hostname, blueprint_index)

        active_jobs.append({
            "job_id": job.get("job_id"),
            "job_type": job.get("job_type"),
            "state": job.get("state"),
            "current_task": _first_non_empty(
                job.get("current_task"),
                agent.get("platform_status", {}).get("current_task"),
                agent.get("status", {}).get("current_task"),
            ),
            "started": job.get("started"),
            "created": job.get("created"),
            "agent_type": job.get("agent_type"),
            "is_log_available": job.get("is_log_available"),
            "error": job.get("error"),
            "device_identified": bool(system_id or hostname or host_id),
            "device": {
                "host_id": host_id or None,
                "system_id": system_id,
                "hostname": hostname,
                "management_ip": _first_non_empty(
                    agent.get("config", {}).get("management_ip"),
                    agent.get("running_config", {}).get("management_ip"),
                ),
                "connection_state": agent.get("status", {}).get("connection_state"),
                "platform": _first_non_empty(
                    agent.get("status", {}).get("platform"),
                    agent.get("platform_status", {}).get("platform"),
                ),
                "platform_version": _first_non_empty(
                    agent.get("status", {}).get("platform_version"),
                    agent.get("platform_status", {}).get("platform_version"),
                ),
                "device_os_version": agent.get("device_facts", {}).get("device_os_version"),
            },
            "blueprint_match_count": len(blueprint_matches),
            "blueprint_matches": blueprint_matches,
            "troubleshooting_note": _job_guidance(job.get("job_type"), job.get("state")),
        })

    active_jobs.sort(
        key=lambda item: (
            item.get("started") or "",
            item.get("job_type") or "",
            item.get("device", {}).get("hostname") or "",
        )
    )
    return active_jobs


def _summarize_active_jobs(active_jobs: list[dict]) -> dict:
    by_job_type = Counter(str(job.get("job_type") or "unknown") for job in active_jobs)
    by_state = Counter(str(job.get("state") or "unknown") for job in active_jobs)
    impacted_devices = {
        job.get("device", {}).get("system_id")
        or job.get("device", {}).get("hostname")
        or job.get("device", {}).get("host_id")
        for job in active_jobs
    }
    impacted_devices.discard(None)
    impacted_blueprints = sorted({
        match.get("blueprint_label")
        for job in active_jobs
        for match in job.get("blueprint_matches", [])
        if match.get("blueprint_label")
    })

    return {
        "by_job_type": dict(by_job_type.most_common()),
        "by_state": dict(by_state.most_common()),
        "impacted_device_count": len(impacted_devices),
        "impacted_blueprint_count": len(impacted_blueprints),
        "impacted_blueprints": impacted_blueprints,
    }


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
