import asyncio
import time

import httpx

from handlers.systems import handle_get_systems
from primitives import live_data_client

# Status strings that mean "still running — keep polling".
_RUNNING_STATUSES = {"inprogress", "in_progress", "in progress", "pending", "queued", "running"}

# Retry configuration for the initial task-creation (submit) calls.
# On failure the helper retries up to this many times with exponential backoff.
_SUBMIT_MAX_RETRIES = 3
_SUBMIT_RETRY_BASE_DELAY = 1.0  # seconds; doubles per attempt

# Hard ceiling on concurrent in-flight systems to avoid overwhelming the
# Apstra API regardless of what the caller passes.
_MAX_CONCURRENT_SYSTEMS = 20


# ---------------------------------------------------------------------------
# Known JunOS syntax corrections
# Maps fragments found in bad commands to the correct form shown to the LLM.
# ---------------------------------------------------------------------------
_JUNOS_CORRECTIONS: list[tuple[str, str]] = [
    ("show bgp neighbors",      "'show bgp neighbor <peer-ip>' or 'show bgp summary'"),
    ("show bgp group",          "'show bgp group <group-name>' (group name is required)"),
    ("show bfd sessions",       "'show bfd session' (JunOS uses singular: 'session')"),
    ("show bfd neighbors",      "'show bfd session'"),
    ("show mpls lsp detail",    "'show mpls lsp detail' with output_format='text'"),
    ("show route summary",      "'show route summary' with output_format='text'"),
    ("show interfaces brief",   "'show interfaces terse'"),
    ("show arp",                "'show arp' or 'show arp hostname <ip>'"),
    ("show mac",                "'show ethernet-switching table' (JunOS L2 MAC table)"),
    ("show ip bgp",             "'show bgp summary' (JunOS uses 'show bgp', not 'show ip bgp')"),
    ("show ip route",           "'show route' (JunOS uses 'show route', not 'show ip route')"),
    ("show ip interface",       "'show interfaces terse' or 'show interfaces <if> detail'"),
    ("show version detail",     "'show version' (no 'detail' keyword in JunOS)"),
]


def _annotate_command_result(result: dict) -> dict:
    """
    Enriches a command_result dict when the device returned a JunOS syntax
    error so the LLM knows not to retry the same command verbatim.

    Adds:
      syntax_error  — True when result == "commandShellError"
      llm_hint      — actionable string telling the LLM what to do next
    """
    if result.get("result") != "commandShellError":
        return result

    cmd_lower = result.get("command", "").lower().strip()
    hint_parts = [
        "The JunOS CLI rejected this command (syntax error — not a connectivity issue). "
        "Do NOT retry the same command text.",
    ]

    correction = next(
        (corr for frag, corr in _JUNOS_CORRECTIONS if frag in cmd_lower),
        None,
    )
    if correction:
        hint_parts.append(f"Suggested correction: {correction}.")
    else:
        hint_parts.append(
            "Revise the command: check argument order, use singular noun forms "
            "(e.g. 'neighbor' not 'neighbors', 'session' not 'sessions'), and "
            "ensure all required arguments are present before retrying."
        )

    return {
        **result,
        "syntax_error": True,
        "llm_hint": " ".join(hint_parts),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _submit_with_retry(submit_fn, skip_codes=()):
    """
    Calls ``submit_fn()`` (a zero-arg async callable) and retries up to
    ``_SUBMIT_MAX_RETRIES`` times if it raises.

    HTTP status codes in ``skip_codes`` are re-raised immediately without
    retrying (used for 404/405 version-detection fall-through).

    Backoff doubles each attempt: 1 s, 2 s, 4 s.
    """
    last_exc = None
    for attempt in range(_SUBMIT_MAX_RETRIES + 1):
        try:
            return await submit_fn()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in skip_codes:
                raise
            last_exc = exc
        except Exception as exc:
            last_exc = exc
        if attempt < _SUBMIT_MAX_RETRIES:
            await asyncio.sleep(_SUBMIT_RETRY_BASE_DELAY * (2 ** attempt))
    raise last_exc


async def _poll_until_done(session, request_id: str, timeout_seconds: int) -> dict:
    """
    Polls GET /api/telemetry/fetchcmd/{request_id} until the job reports a
    terminal status or `timeout_seconds` elapses.

    The Apstra API uses the "result" field to report job state, e.g.
    {"result": "success", "output": "..."}. The older "status" field is
    also checked for compatibility.

    A 404 response during polling means the job record is not yet visible on
    the server (the task was accepted but not yet persisted).  It is treated
    the same as an in-progress status so the poll loop retries transparently.

    Returns the last response dict.  If the timeout is reached before the job
    completes, returns {"result": "timeout", "request_id": request_id}.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            response = await live_data_client.poll_fetchcmd(session, request_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Job not yet visible — treat as pending and keep polling.
                if time.monotonic() >= deadline:
                    return {"result": "timeout", "request_id": request_id}
                await asyncio.sleep(1.5)
                continue
            raise
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
    request_id = await _submit_with_retry(
        lambda: live_data_client.submit_fetchcmd_single(
            session, system_id, command_text, output_format
        )
    )
    poll_result = await _poll_until_done(session, request_id, timeout_seconds)
    await _safe_delete(session, request_id)
    return _annotate_command_result({
        "command": command_text,
        "result": poll_result.get("result", poll_result.get("status", "unknown")),
        "output": poll_result.get("output"),
        "error": poll_result.get("error"),
    })


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
        # Retries up to _SUBMIT_MAX_RETRIES times on transient failures.
        # 404/405 are passed through immediately so the fallback below fires.
        try:
            request_ids_map = await _submit_with_retry(
                lambda: live_data_client.submit_fetchcmd_multiple(
                    session, system_id, commands, output_format
                ),
                skip_codes=(404, 405),
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
            return _annotate_command_result({
                "command": cmd,
                "result": poll_result.get("result", poll_result.get("status", "unknown")),
                "output": poll_result.get("output"),
                "error": poll_result.get("error"),
            })

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
                # All-systems mode — use handle_get_systems as the single
                # source of truth for system discovery so both tools stay in
                # sync.  Filter out any partially-onboarded switches that have
                # no hardware serial yet.
                systems_result = await handle_get_systems(
                    [session], registry, blueprint_id
                )
                systems = [
                    (s["system_id"], s["label"] or s["system_id"])
                    for s in systems_result.get("systems", [])
                    if s.get("system_id")
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

                # Semaphore caps concurrent in-flight systems.  Caller may
                # request a lower limit but the hard ceiling is always applied.
                sem = asyncio.Semaphore(min(max_concurrent_systems, _MAX_CONCURRENT_SYSTEMS))

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
