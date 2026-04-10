import asyncio
import time

import httpx

from primitives import live_data_client

# Status strings that mean "still running — keep polling".
_RUNNING_STATUSES = {"inprogress", "in_progress", "in progress", "pending", "queued", "running"}

# Cypher query to retrieve all hardware system_ids for switches in a blueprint.
# Returns only onboarded systems (system_id is not null/empty).
_SYSTEMS_QUERY = """
MATCH (sw:system)
WHERE sw.system_type = 'switch'
  AND sw.role IN ['leaf', 'spine', 'access', 'superspine']
  AND sw.system_id IS NOT NULL
RETURN sw.system_id, sw.label
ORDER BY sw.label
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _poll_until_done(session, request_id: str, timeout_seconds: int) -> dict:
    """
    Polls GET /api/telemetry/fetchcmd/{request_id} until the job reports a
    terminal status or `timeout_seconds` elapses.

    The Apstra API uses the "result" field to report job state, e.g.
    {"result": "success", "output": "..."}. The older "status" field is
    also checked for compatibility.

    Returns the last response dict.  If the timeout is reached before the job
    completes, returns {"result": "timeout", "request_id": request_id}.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        response = await live_data_client.poll_fetchcmd(session, request_id)
        raw_status = response.get("result", response.get("status", ""))
        if raw_status.lower() not in _RUNNING_STATUSES:
            return response
        if time.monotonic() >= deadline:
            return {"result": "timeout", "request_id": request_id}
        await asyncio.sleep(1.5)


async def _safe_delete(session, request_id: str) -> None:
    """
    Deletes a fetchcmd job, silently swallowing any errors so that cleanup
    failure does not mask a successful result.
    """
    try:
        await live_data_client.delete_fetchcmd(session, request_id)
    except Exception:
        pass


async def _run_single_command(
    session,
    system_id: str,
    command_text: str,
    timeout_seconds: int,
    output_format: str,
) -> dict:
    """
    Runs one command via the single-command endpoint, polls to completion,
    cleans up, and returns a normalised result dict.
    """
    request_id = await live_data_client.submit_fetchcmd_single(
        session, system_id, command_text, output_format
    )
    poll_result = await _poll_until_done(session, request_id, timeout_seconds)
    await _safe_delete(session, request_id)
    return {
        "command": command_text,
        "result": poll_result.get("result", poll_result.get("status", "unknown")),
        "output": poll_result.get("output"),
        "error": poll_result.get("error"),
    }


async def _run_on_system(
    session,
    system_id: str,
    system_label: str,
    commands: list[str],
    timeout_seconds: int,
    output_format: str,
) -> dict:
    """
    Runs the given commands on a single system.

    Tries POST /api/telemetry/fetchcmd/multiple first.  If that endpoint
    returns 404 or 405 (older Apstra version), falls back to running each
    command individually via POST /api/telemetry/fetchcmd.

    Both paths execute all command submissions and polls concurrently using
    asyncio.gather so one slow command does not block the others.

    Returns a dict with system identity, the endpoint used ("multiple" or
    "single"), and command results.
    """
    base = {"system_id": system_id, "system_label": system_label}

    try:
        # -- Attempt batch endpoint first ------------------------------------
        # Returns {"command text": "request_id", ...} — one uuid per command.
        try:
            request_ids_map = await live_data_client.submit_fetchcmd_multiple(
                session, system_id, commands, output_format
            )
            endpoint = "multiple"
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 405):
                # Endpoint not available — run all commands concurrently via
                # the single endpoint (one POST+poll per command, in parallel).
                command_results = await asyncio.gather(*[
                    _run_single_command(
                        session, system_id, cmd, timeout_seconds, output_format
                    )
                    for cmd in commands
                ])
                return {
                    **base,
                    "endpoint": "single",
                    "status": "success",
                    "command_results": list(command_results),
                }
            raise

        # -- Poll & cleanup each command's request_id concurrently ----------
        async def _poll_one(cmd: str, req_id: str) -> dict:
            poll_result = await _poll_until_done(session, req_id, timeout_seconds)
            await _safe_delete(session, req_id)
            return {
                "command": cmd,
                "result": poll_result.get("result", poll_result.get("status", "unknown")),
                "output": poll_result.get("output"),
                "error": poll_result.get("error"),
            }

        command_results = await asyncio.gather(*[
            _poll_one(cmd, req_id)
            for cmd, req_id in request_ids_map.items()
        ])

        return {
            **base,
            "endpoint": endpoint,
            "status": "success",
            "command_results": list(command_results),
        }

    except Exception as exc:
        return {
            **base,
            "status": "error",
            "error": str(exc),
            "command_results": None,
        }


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------

async def handle_run_commands(
    sessions,
    registry,
    blueprint_id: str,
    commands: list[str],
    system_id: str = None,
    instance_name: str = None,
    timeout_seconds: int = 30,
    output_format: str = "json",
    max_concurrent_systems: int = 10,
) -> dict:
    """
    Runs CLI commands on one system or all systems in a blueprint.

    When `system_id` is None, the blueprint graph is queried to discover all
    switch hardware system_ids, then commands are run concurrently on each.
    A semaphore limits simultaneous active systems to `max_concurrent_systems`
    so that large fabrics (20-30 switches) do not overwhelm the Apstra API.

    Each session in the pool is used independently.
    """
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            if system_id:
                # Single-system mode — no graph query needed.
                system_result = await _run_on_system(
                    session, system_id, system_id,
                    commands, timeout_seconds, output_format,
                )
                all_results.append({
                    "instance": session.name,
                    "blueprint_id": blueprint_id,
                    "systems": [system_result],
                    "system_count": 1,
                })
            else:
                # All-systems mode — discover system_ids from the graph.
                graph = await registry.get_or_rebuild(session, blueprint_id)
                rows = graph.query(_SYSTEMS_QUERY)
                systems = [
                    (r["sw.system_id"], r.get("sw.label") or r["sw.system_id"])
                    for r in rows
                    if r.get("sw.system_id")
                ]
                if not systems:
                    all_results.append({
                        "instance": session.name,
                        "blueprint_id": blueprint_id,
                        "systems": [],
                        "system_count": 0,
                        "note": "No onboarded systems found in blueprint",
                    })
                    continue

                # Semaphore caps concurrent in-flight systems so large fabrics
                # don't all hammer the Apstra API at the same instant.
                sem = asyncio.Semaphore(max_concurrent_systems)

                async def _bounded(sid, label):
                    async with sem:
                        return await _run_on_system(
                            session, sid, label,
                            commands, timeout_seconds, output_format,
                        )

                system_results = await asyncio.gather(*[
                    _bounded(sid, label) for sid, label in systems
                ])
                all_results.append({
                    "instance": session.name,
                    "blueprint_id": blueprint_id,
                    "systems": list(system_results),
                    "system_count": len(system_results),
                })

        except Exception as exc:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(exc),
                "systems": [],
                "system_count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "results": all_results,
        "total_system_count": sum(r.get("system_count", 0) for r in all_results),
    }


def _select_sessions(sessions, instance_name):
    if instance_name is None or instance_name == "all":
        return sessions
    matched = [s for s in sessions if s.name == instance_name]
    if not matched:
        raise ValueError(f"No instance named '{instance_name}' in session pool")
    return matched
