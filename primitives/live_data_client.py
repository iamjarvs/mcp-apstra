import httpx


async def _request(session, method: str, path: str, body: dict = None) -> dict:
    token = await session.get_token()
    url = f"{session.host}{path}"
    async with httpx.AsyncClient(verify=session._ssl_verify, timeout=30.0) as client:
        kwargs: dict = {"headers": {"AUTHTOKEN": token}}
        if body is not None:
            kwargs["json"] = body
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}


async def get_anomalies(session, blueprint_id: str) -> dict:
    """
    Fetches active anomalies for a blueprint from the Apstra REST API.
    Returns the raw JSON response body as a Python dict.
    """
    return await _request(session, "GET", f"/api/blueprints/{blueprint_id}/anomalies")


async def get_blueprints(session) -> dict:
    """
    Fetches all blueprints from an Apstra instance.
    Returns the raw JSON response body as a Python dict.
    """
    return await _request(session, "GET", "/api/blueprints")


async def get_blueprint_versions(session) -> dict[str, int]:
    """
    Returns a mapping of blueprint_id -> version for all blueprints on an
    instance. Uses GET /api/blueprints, which is cheap and returns all
    blueprint metadata in a single call. Used by BlueprintGraphRegistry to
    check staleness without fetching full graph data.
    """
    raw = await _request(session, "GET", "/api/blueprints")
    return {
        item["id"]: item["version"]
        for item in raw.get("items", [])
        if "id" in item and "version" in item
    }


async def get_blueprint_graph(session, blueprint_id: str) -> dict:
    """
    Fetches a single blueprint including its full graph data in one call.
    Returns a dict containing: id, label, version, nodes, relationships.
    Using this endpoint is preferred over separate nodes/relationships calls
    because it returns version, nodes, and relationships in a single round trip.
    """
    return await _request(session, "GET", f"/api/blueprints/{blueprint_id}")


async def get_system_config_context(session, blueprint_id: str, system_id: str) -> dict:
    """
    Fetches the config context for a specific system within a blueprint.
    The context is the full data model Apstra uses to render device configuration.

    `system_id` is the hardware chassis serial number (e.g. "5254002D005F").
    This is the `system_id` field returned by get_systems — NOT the `id` field
    (graph node ID such as "wvXOipeAz30CkfrOsw"). Apstra's config-context
    endpoint is keyed on the hardware serial in the URL path.

    Returns a dict containing a single 'context' key whose value is a
    JSON-encoded string. Call json.loads() on that string to get the full
    structured context object.
    """
    return await _request(
        session, "GET",
        f"/api/blueprints/{blueprint_id}/systems/{system_id}/config-context",
    )


async def get_design_configlets(session) -> dict:
    """
    Fetches all configlets from the instance-level design catalogue.
    Returns the raw JSON response (items list).
    """
    return await _request(session, "GET", "/api/design/configlets")


async def get_design_property_sets(session) -> dict:
    """
    Fetches all property sets from the instance-level design catalogue.
    Returns the raw JSON response (items list).
    """
    return await _request(session, "GET", "/api/property-sets")


async def get_config_rendering(session, blueprint_id: str, system_id: str) -> dict:
    """
    Fetches the rendered device configuration for a specific system within
    a blueprint. Apstra renders the full JunOS (or EOS) configuration from
    its intent model and returns it as a flat config string.

    `system_id` is the hardware chassis serial number (e.g. "5254002D005F").
    This is the `system_id` field returned by get_systems — NOT the `id`
    field (graph node ID). Apstra's config-rendering endpoint is keyed on
    the hardware serial in the URL path.

    Returns a dict containing a single 'config' key whose value is a
    JunOS hierarchical configuration string. The string contains an
    optional '------BEGIN SECTION CONFIGLETS------' boundary that separates
    AOS-managed config from user-defined configlets.
    """
    return await _request(
        session, "GET",
        f"/api/blueprints/{blueprint_id}/systems/{system_id}/config-rendering",
    )


async def submit_fetchcmd_multiple(
    session,
    system_id: str,
    commands: list[str],
    output_format: str = "json",
) -> dict:
    """
    Submits multiple CLI commands to a managed system using the batch fetchcmd
    endpoint available in newer Apstra versions.

    `system_id` is the hardware chassis serial number (e.g. "5254002D005F"),
    not the graph node ID.

    Returns a dict mapping each command text to its own request_id, e.g.
    {"show version": "680d7c55-..."}. Each request_id must be polled and
    deleted independently via poll_fetchcmd() and delete_fetchcmd().

    Raises httpx.HTTPStatusError if the request fails. A 404 or 405 status
    indicates this Apstra version does not support the batch endpoint — the
    caller should fall back to submit_fetchcmd_single().
    """
    body = {
        "system_id": system_id,
        "commands": [{"format": output_format, "text": cmd} for cmd in commands],
    }
    raw = await _request(session, "POST", "/api/telemetry/fetchcmd/multiple", body=body)
    return raw["request_ids"]  # {"command text": "uuid", ...}


async def submit_fetchcmd_single(
    session,
    system_id: str,
    command_text: str,
    output_format: str = "json",
) -> str:
    """
    Submits a single CLI command to a managed system using the single-command
    fetchcmd endpoint supported across all Apstra versions.

    `system_id` is the hardware chassis serial number.

    Returns the `request_id` string to pass to poll_fetchcmd() and
    delete_fetchcmd().
    """
    body = {
        "system_id": system_id,
        "command_text": command_text,
        "output_format": output_format,
    }
    raw = await _request(session, "POST", "/api/telemetry/fetchcmd", body=body)
    return raw["request_id"]


async def poll_fetchcmd(session, request_id: str) -> dict:
    """
    Checks the status of a pending fetchcmd job.

    Returns the raw API response dict. When the `status` field is "inprogress"
    (or a variant), the caller should wait and call again. When the status is
    any terminal value — or the field is absent — the job is complete and
    the output is present in the response.
    """
    return await _request(session, "GET", f"/api/telemetry/fetchcmd/{request_id}")


async def delete_fetchcmd(session, request_id: str) -> None:
    """
    Deletes a fetchcmd job from the Apstra server, freeing server-side
    resources. Should always be called after polling is complete.

    A 404 response means the job was already cleaned up and is silently
    ignored by the caller.
    """
    await _request(session, "DELETE", f"/api/telemetry/fetchcmd/{request_id}")


# ── Anomaly history / time-series APIs ────────────────────────────────────────

async def get_anomaly_history_counts(
    session,
    blueprint_id: str,
    begin_time: str = "-7:0",
) -> dict:
    """
    Returns a count-change timeseries for each anomaly type over the
    requested window.

    `begin_time` uses Apstra's relative format: "-<days>:<seconds>", e.g.
    "-7:0" for seven days ago. The window always ends at the current time.

    Response shape: {"counts": {"bgp": [{"count": N, "timestamp": "..."}], ...}}
    Each list entry represents a moment when the count for that type changed.
    """
    body: dict = {}
    if begin_time:
        body["begin_time"] = begin_time
    return await _request(
        session, "POST",
        f"/api/blueprints/{blueprint_id}/anomalies-history/counts",
        body=body,
    )


async def get_anomaly_history_snapshot(
    session,
    blueprint_id: str,
    timestamp: str,
) -> dict:
    """
    Returns the full set of anomalies that were active at the given
    point-in-time timestamp (ISO-8601 UTC string).

    Response shape: {"items": [{anomaly}], "request": {...}}
    Each item includes: identity, expected, actual, detected_at, raised,
    anomaly_type, device_hostname, role.
    """
    return await _request(
        session, "POST",
        f"/api/blueprints/{blueprint_id}/anomalies-history",
        body={"timestamp": timestamp},
    )


async def get_anomaly_trace(
    session,
    blueprint_id: str,
    anomaly_type: str,
    identity: dict,
    begin_time: str = "-7:0",
) -> dict:
    """
    Returns the full raise/clear event log for a single specific anomaly
    identity over the requested window.

    `identity` must be the complete identity dict as returned by the anomaly
    API — it uniquely identifies one anomaly (e.g. a specific BGP session).

    Response shape: {"items": [{raised, detected_at, actual, ...}]}
    """
    return await _request(
        session, "POST",
        f"/api/blueprints/{blueprint_id}/anomalies-history/trace",
        body={
            "begin_time": begin_time,
            "anomaly_type": anomaly_type,
            "identity": identity,
        },
    )


# ── Telemetry APIs ─────────────────────────────────────────────────────────────

async def get_interface_counters(session, system_id: str) -> dict:
    """
    Returns the latest raw interface counters for every interface on a system.

    Response shape:
      {
        "items": [
          {
            "system_id": "...",
            "interface_name": "ge-0/0/0",
            "rx_unicast_packets": int,
            "rx_broadcast_packets": int,
            "rx_multicast_packets": int,
            "rx_error_packets": int,
            "rx_discard_packets": int,
            "rx_bytes": int,
            "tx_unicast_packets": int,
            "tx_broadcast_packets": int,
            "tx_multicast_packets": int,
            "tx_error_packets": int,
            "tx_discard_packets": int,
            "tx_bytes": int,
            "alignment_errors": int,
            "fcs_errors": int,
            "symbol_errors": int,
            "runts": int,
            "giants": int,
            "last_fetched_at": "ISO-8601"
          }, ...
        ],
        "delta_microseconds": int
      }

    `system_id` is the hardware chassis serial number (e.g. "5254002D005F").
    """
    return await _request(session, "GET", f"/api/systems/{system_id}/counters")


async def get_system_resource_util(session, system_id: str) -> dict:
    """
    Returns the latest CPU and memory utilisation for a system.

    Response shape:
      {
        "items": [
          {
            "system_id": "...",
            "type": "resource_util",
            "key": "system_cpu_utilization",   # or "system_memory_utilization"
            "actual": {"value": "2"},            # percentage as a string
            "last_fetched_at": "ISO-8601"
          }, ...
        ]
      }

    `system_id` is the hardware chassis serial number (e.g. "5254002D005F").
    """
    return await _request(
        session, "GET",
        f"/api/systems/{system_id}/services/resource_util/data",
    )


# ── IBA Probe APIs ─────────────────────────────────────────────────────────────

async def get_probes(session, blueprint_id: str) -> dict:
    """
    Returns the list of all IBA probes configured in a blueprint.

    Response shape:
      {
        "items": [
          {
            "id": "uuid",
            "label": "Device Traffic",
            "description": "...",
            "disabled": false,
            "state": "operational",
            "probe_state": "...",
            "anomaly_count": int,
            "stages": [ {"name": "...", ...}, ... ],
            "predefined_probe": "...",
            "updated_at": "ISO-8601"
          }, ...
        ],
        "total_count": int
      }
    """
    return await _request(session, "GET", f"/api/blueprints/{blueprint_id}/probes")


async def get_probe(session, blueprint_id: str, probe_id: str) -> dict:
    """
    Returns full detail for a single IBA probe including all processor and
    stage definitions.

    Response shape: same fields as items[] in get_probes(), plus full
    "processors" list with graph_query, properties, and stage metadata.
    """
    return await _request(
        session, "GET",
        f"/api/blueprints/{blueprint_id}/probes/{probe_id}",
    )


async def query_probe_stage(
    session,
    blueprint_id: str,
    probe_id: str,
    stage: str,
    begin_time: str | None = None,
    end_time: str | None = None,
    anomalous_only: bool = False,
    per_page: int = 100,
) -> dict:
    """
    Queries the time-series output of a specific stage within an IBA probe.

    Response shape:
      {
        "type": "table",
        "description": "...",
        "items": [
          {
            "timestamp": "ISO-8601",
            "value": <depends on stage>,
            "id": int,
            "properties": { ... stage-specific properties ... }
          }, ...
        ],
        "total_count": int
      }

    The items[] schema varies by probe and stage — the properties dict always
    contains system_id and any grouping keys defined by the probe's graph_query.

    `stage` must be the exact stage name string as returned by get_probe() in
    the stages[].name list.
    """
    body: dict = {"stage": stage, "per_page": per_page}
    if begin_time:
        body["begin_time"] = begin_time
    if end_time:
        body["end_time"] = end_time
    if anomalous_only:
        body["anomalous_only"] = anomalous_only
    return await _request(
        session, "POST",
        f"/api/blueprints/{blueprint_id}/probes/{probe_id}/query",
        body=body,
    )
