# Design guide for LLM-assisted implementation

This document is the primary reference for building tools in this MCP server.
Read it fully before writing any code. Every convention here exists for a
reason; deviating from it will make the codebase inconsistent and harder to
extend.

**Installed versions (as of last update):** Python 3.13, FastMCP 3.2.0,
kuzu 0.11.3, httpx 0.27+

---

## Architecture summary

The server has three code layers and two supporting layers. The layers are
strictly one-directional — upper layers call lower layers, never the reverse.

```
server.py
  └── tools/<n>.py             @mcp.tool() wrapper — calls handler
        └── handlers/<n>.py    Business logic — calls primitives
              └── primitives/  Shared functions — calls external APIs
                    ├── live_data_client.py   → Apstra REST API (live telemetry)
                    ├── graph_client.py       → Kuzu in-memory graph (design intent)
                    └── design_diff_client.py → Apstra REST API (staged vs active)
```

Supporting layers:
- `config/` — reads instances.yaml, builds the session pool at startup
- `tests/` — tests handlers and primitives directly, not through MCP

### Two build paths

Tools either call the Apstra REST API directly for live operational data, or
query a Kuzu in-memory graph database that is pre-populated from the Apstra
blueprint graph API. Choose the path based on what the tool needs:

| Path | When to use | Key primitive | Data freshness |
|------|-------------|---------------|----------------|
| **REST / live** | Operational state — anomalies, device health | `live_data_client` | Real-time |
| **Graph / design** | Design intent — VNs, topology, routing zones | `graph_client` | Blueprint version |

Graph-backed tools receive the `BlueprintGraphRegistry` in addition to
`sessions` and call `await registry.get_or_rebuild(session, blueprint_id)` to
get an `ApstraKuzuGraph` instance. The registry tracks the blueprint version
and rebuilds the graph automatically when Apstra reports a new version.

---

## Authentication architecture

Authentication is owned entirely by `primitives/auth_manager.py` and runs as
a permanently active background process. Nothing else in the codebase
authenticates or manages tokens.

### ApstraSession

The only class in the project. One instance per Apstra server. Holds the
token, tracks health state, and owns the two background asyncio tasks.

**Public interface used by primitives:**

```python
token = await session.get_token()   # returns current valid token immediately
status = session.status()           # returns health snapshot dict
```

**Status fields available on every session:**

```python
session.token_valid       # bool — False if auth has failed and not recovered
session.host_reachable    # bool — False if probe is failing
session.last_token_refresh  # datetime of last successful authentication
session.last_probe          # datetime of last successful probe
```

### Startup sequence

Sessions are built by `config/settings.py`, then in `server.py`'s lifespan
hook each session is authenticated and its background tasks are started:

```python
for session in sessions:
    await session.authenticate()       # blocks until first token obtained
    session.start_background_refresh() # launches background tasks, returns immediately
```

`authenticate()` raises on failure so the server fails fast at startup if an
instance is unreachable or credentials are wrong.

### Token refresh loop

Runs forever. Decodes the JWT `exp` claim to determine exactly when the token
expires, sleeps until 5 minutes before that point, then re-authenticates. On
failure it marks `token_valid = False`, logs the error, and retries every 15
seconds until successful.

JWT decoding reads the `exp` claim directly from the token payload without
verifying the signature — no secret key needed, and no network call. If
decoding fails for any reason the loop falls back to a 1-hour TTL.

### Probe loop

Runs forever. Calls `/api/version` every 30 seconds. On failure it attempts
a re-authentication before concluding the host is unreachable — a failed probe
may indicate server-side token revocation, which JWT expiry checking cannot
catch. The decision tree is:

1. Probe fails → attempt re-auth
2. Re-auth succeeds → retry probe with fresh token
3. Retry probe succeeds → token was revoked, now recovered
4. Retry probe fails → host is genuinely unreachable, set `host_reachable = False`
5. Re-auth also fails → same conclusion, set `host_reachable = False`

`token_valid` and `host_reachable` are independent flags. A tool can tell the
LLM exactly which failure mode it is dealing with.

---

## Layer 1: server.py

### Purpose

Initialise the FastMCP app, run the startup lifespan hook, and register all
tool modules. Nothing else.

### Rules

- Import FastMCP and create the `mcp` instance here with `instructions=`
  (not `description=` — that key is not accepted by FastMCP 3.x)
- Use FastMCP's lifespan context manager for startup — authenticate sessions,
  initialise the `BlueprintGraphRegistry`, and start background tasks here
- The lifespan must `yield` a dict with two keys: `"sessions"` and
  `"graph_registry"`. Tools access both via `ctx.lifespan_context`
- Call `registry.close_all()` after the yield to clean up Kuzu databases on
  shutdown
- Import each tool module and call its registration function, passing `mcp`
- Do not define any tool logic here
- Do not import primitives or handlers directly here

### Pattern

```python
# server.py

from contextlib import asynccontextmanager
from fastmcp import FastMCP
from config.settings import load_sessions
from primitives.graph_client import BlueprintGraphRegistry
from tools import anomalies as anomalies_tool
from tools import blueprints as blueprints_tool
from tools import systems as systems_tool
from tools import virtual_networks as virtual_networks_tool


@asynccontextmanager
async def lifespan(app):
    sessions = load_sessions()
    registry = BlueprintGraphRegistry()
    for session in sessions:
        await session.authenticate()
        session.start_background_refresh()
    yield {"sessions": sessions, "graph_registry": registry}
    registry.close_all()


mcp = FastMCP(
    "apstra-mcp",
    lifespan=lifespan,
    instructions="...",   # LLM-visible server description
)

anomalies_tool.register(mcp)
blueprints_tool.register(mcp)
systems_tool.register(mcp)
virtual_networks_tool.register(mcp)

if __name__ == "__main__":
    mcp.run()
```

---

## Layer 2: tools/<n>.py

### Purpose

Define and register the `@mcp.tool()` decorated function. This is the only
place the LLM-visible tool interface is defined — its name, parameters, return
type, and docstring. The function body must be a single call to the
corresponding handler.

### Rules

- One file per tool group (a file may register multiple related tools via a
  single `register(mcp)` function)
- The function name becomes the tool name the LLM sees — name it clearly
- The docstring is what the LLM reads to understand what the tool does. It
  must include: what the tool returns, which data source it uses (graph / live
  / design diff), and what each parameter means
- Always declare `ctx: Context = None` as the last parameter and import
  `Context` from `fastmcp`
- Access sessions via `ctx.lifespan_context["sessions"]`
- For graph-backed tools, also pass `ctx.lifespan_context["graph_registry"]`
- No business logic — one call only in the function body

### REST-backed tool pattern

```python
# tools/anomalies.py

from fastmcp import Context
from handlers.anomalies import handle_get_anomalies


def register(mcp):

    @mcp.tool()
    async def get_current_anomalies(
        blueprint_id: str,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Returns active anomalies for a given blueprint from the live network.

        Data source: live network (live_data_client). Results reflect the
        current state of the network and may vary between calls.

        Args:
            blueprint_id:  The Apstra blueprint ID to query.
            instance_name: Optional. The Apstra instance to query (as defined
                           in instances.yaml). Omit to query all instances.
        """
        return await handle_get_anomalies(
            ctx.lifespan_context["sessions"],
            blueprint_id,
            instance_name,
        )
```

### Graph-backed tool pattern

```python
# tools/virtual_networks.py

from fastmcp import Context
from handlers.virtual_networks import handle_get_virtual_networks


def register(mcp):

    @mcp.tool()
    async def get_vn_deployments(
        blueprint_id: str,
        system_id: str = None,
        instance_name: str = None,
        ctx: Context = None,
    ) -> dict:
        """
        Shows where each virtual network is deployed across the fabric.

        Data source: graph database (graph_client). The graph is automatically
        rebuilt if the blueprint version has changed since the last query.

        Args:
            blueprint_id:  The Apstra blueprint ID to query.
            system_id:     Optional. Hardware system ID to scope to one switch.
            instance_name: Optional. Apstra instance name. Omit for all instances.
        """
        return await handle_get_virtual_networks(
            ctx.lifespan_context["sessions"],
            ctx.lifespan_context["graph_registry"],
            blueprint_id,
            system_id,
            instance_name,
        )
```

---

## Layer 3: handlers/<n>.py

### Purpose

Contain the business logic for a tool. Decide which sessions to use, call the
appropriate primitive(s), handle errors, and shape the response.

### Rules

- No MCP imports — this file must be testable without a running MCP server
- Receive `sessions` as the first argument (a list of ApstraSession objects)
- Graph-backed handlers receive `registry` (BlueprintGraphRegistry) as the
  second positional argument, before `blueprint_id`
- Decide whether to query one instance or all based on `instance_name`
- Call primitives only — do not make HTTP calls directly
- Return a plain Python dict — no MCP-specific types
- Always include `_meta` in each successful result using `compute_response_meta`
- Catch exceptions from primitives per-session and return structured error
  information rather than letting exceptions propagate to the LLM unhandled

### REST-backed handler pattern

```python
# handlers/anomalies.py

from primitives import live_data_client, response_parser


async def handle_get_anomalies(sessions, blueprint_id: str, instance_name: str = None) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            raw = await live_data_client.get_anomalies(session, blueprint_id)
            parsed = response_parser.parse_anomalies(raw)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "anomalies": parsed,
                "count": len(parsed),
                "_meta": response_parser.compute_response_meta(
                    display_as="anomaly_table"
                ),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
                "anomalies": [],
                "count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "results": all_results,
        "total_count": sum(r.get("count", 0) for r in all_results),
        "_meta": response_parser.compute_response_meta(display_as="anomaly_table"),
    }


def _select_sessions(sessions, instance_name):
    if instance_name is None:
        return sessions
    matched = [s for s in sessions if s.name == instance_name]
    if not matched:
        raise ValueError(f"No instance named '{instance_name}' in session pool")
    return matched
```

### Graph-backed handler pattern

```python
# handlers/virtual_networks.py

from primitives import response_parser
from primitives.response_parser import parse_virtual_networks

_CYPHER_QUERY = """
MATCH (sw:system)-[:hosted_vn_instances]->(vni:vn_instance)
<-[:instantiated_by]-(vn:virtual_network)
RETURN sw.id, sw.label, vni.id, vni.vlan_id, vn.id, vn.label, vn.vn_type
"""


async def handle_get_virtual_networks(
    sessions,
    registry,
    blueprint_id: str,
    system_id: str = None,
    instance_name: str = None,
) -> dict:
    target_sessions = _select_sessions(sessions, instance_name)
    all_results = []

    for session in target_sessions:
        try:
            graph = await registry.get_or_rebuild(session, blueprint_id)
            rows = graph.query(_CYPHER_QUERY)
            parsed = parse_virtual_networks(rows)
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "vn_instances": parsed,
                "count": len(parsed),
                "_meta": response_parser.compute_response_meta(
                    display_as="vxlan_table",
                    data_source="blueprint_design",
                ),
            })
        except Exception as e:
            all_results.append({
                "instance": session.name,
                "blueprint_id": blueprint_id,
                "error": str(e),
                "vn_instances": [],
                "count": 0,
            })

    if len(all_results) == 1:
        return all_results[0]

    return {
        "instance": "all",
        "blueprint_id": blueprint_id,
        "results": all_results,
        "total_count": sum(r.get("count", 0) for r in all_results),
        "_meta": response_parser.compute_response_meta(
            display_as="vxlan_table", data_source="blueprint_design"
        ),
    }
```

---

## Layer 4: primitives/

### auth_manager.py

The `ApstraSession` class is fully implemented. Do not modify it unless
changing authentication behaviour. See the authentication architecture section
above for how it works.

Key facts for primitive authors:

- Call `await session.get_token()` to get the current token — it returns
  immediately from memory, no network call
- Use `session.host` for the base URL
- Use `session._ssl_verify` for the httpx `verify` parameter
- Check `session.token_valid` and `session.host_reachable` before making calls
  if you want to return a clean error rather than letting httpx raise

### live_data_client.py

Functions that call the Apstra REST API for live network state. All functions
are async, accept an `ApstraSession` as their first argument, and return raw
parsed JSON. No transformation — that happens in `response_parser.py`.

All HTTP calls go through the private `_request()` helper, which handles token
injection and SSL config. Add new API endpoints by following the same pattern:

```python
# primitives/live_data_client.py

import httpx


async def _request(session, method: str, path: str, **kwargs):
    token = await session.get_token()
    url = f"{session.host}{path}"
    async with httpx.AsyncClient(verify=session._ssl_verify, timeout=30.0) as client:
        response = await client.request(
            method, url, headers={"AUTHTOKEN": token}, **kwargs
        )
        response.raise_for_status()
        return response.json()


async def get_anomalies(session, blueprint_id: str) -> dict:
    return await _request(session, "GET", f"/api/blueprints/{blueprint_id}/anomalies")


async def get_blueprint_graph(session, blueprint_id: str) -> dict:
    """Returns the full node/relationship graph for a blueprint."""
    return await _request(session, "GET", f"/api/blueprints/{blueprint_id}")


async def get_blueprint_versions(session, blueprint_id: str) -> dict:
    """Returns version metadata used by BlueprintGraphRegistry for cache invalidation."""
    return await _request(session, "GET", f"/api/blueprints/{blueprint_id}/version")
```

### graph_client.py

Manages per-blueprint Kuzu in-memory graph databases. An `ApstraKuzuGraph`
holds one Kuzu database; a `BlueprintGraphRegistry` holds one graph per
blueprint across all sessions, rebuilding automatically when the blueprint
version increments.

#### BlueprintGraphRegistry

```python
registry = BlueprintGraphRegistry()

# Obtain (or rebuild) the graph for a blueprint. The registry calls
# get_blueprint_versions() first; if the version matches the cached version it
# returns the existing graph immediately without a rebuild.
graph = await registry.get_or_rebuild(session, blueprint_id)

# Call on server shutdown to close all Kuzu connections cleanly.
registry.close_all()
```

The registry is created once in `server.py`'s lifespan and shared across all
requests via `ctx.lifespan_context["graph_registry"]`. It is safe to call
`get_or_rebuild` concurrently — an asyncio lock per `(instance, blueprint_id)`
pair prevents duplicate builds.

#### ApstraKuzuGraph

```python
graph = await registry.get_or_rebuild(session, blueprint_id)

# Run any Cypher query. Returns a list of dicts keyed by column name.
rows = graph.query("MATCH (n:system) RETURN n.id, n.label LIMIT 10")

# Parameterised queries (always use params rather than f-strings for values).
rows = graph.query(
    "MATCH (sw:system {system_id: $sid}) RETURN sw.id",
    {"sid": system_id},
)
```

The graph schema is derived automatically from the blueprint's nodes and
relationships. Properties that are entirely null in the current dataset are
stored as `STRING` type so Cypher references to them return `null` instead of
raising "Cannot find property".

To inspect the schema during development or testing:

```python
rows = graph.query("CALL table_info('system') RETURN *")
# Returns column names and types for the 'system' node table.
```

#### Version tracking

`get_or_rebuild` calls `get_blueprint_versions()` on every invocation. The
version endpoint is cheap — it returns only metadata, not the full graph.
A rebuild only happens when the version number has changed, which keeps latency
low on repeated calls.

### design_diff_client.py

Functions that retrieve the difference between staged (uncommitted) and active
(deployed) network state. Apstra tracks both internally. Same async pattern
and session interface as `live_data_client.py`. Not yet used by any tool.

### response_parser.py

Pure transformation functions. Take raw API response dicts or Kuzu query row
lists, return clean consistently structured dicts. No HTTP calls, no session
access, no side effects.

#### compute_response_meta

Returns formatting and context hints for the LLM. Always call this from a
handler and include the result under the key `"_meta"`.

```python
def compute_response_meta(
    display_as: str | None = None,
    data_source: str = "live",
) -> dict:
```

**Never pass items or severity data to `compute_response_meta`.** It does not
scan items, aggregate severity, or compute any summary judgement. The LLM
decides severity narratives from the data directly.

Parameters:
- `display_as`: table format hint. Recognised values: `"anomaly_table"`,
  `"device_state_table"`, `"blueprint_table"`, `"vxlan_table"`,
  `"virtual_network_table"`.
- `data_source`: `"live"` (default) for REST API / operational data;
  `"blueprint_design"` for graph data representing configured intent.

Returned `_meta` structure:

```python
{
    "data_source": "live",            # or "blueprint_design"
    "display_as": "anomaly_table",    # omitted if display_as is None
    "section_order": [
        "Summary",
        "Affected Devices",
        "Findings",
        "Likely Cause",
        "Recommended Actions",
        "Validation",
    ],
}
```

#### severity_label on parsed items

`severity_label` is added to individual items by the parser only when the raw
API data contains an explicit operational or fault field that maps to a
severity — never for design-intent data.

| Parser | severity_label on items? | Basis |
|--------|--------------------------|-------|
| `parse_anomalies` | Yes | `item["severity"]` field from API |
| `parse_blueprints` | Yes | Blueprint `status` field |
| `parse_systems` | Yes | `deploy_mode` + `status` fields |
| `parse_virtual_networks` | **No** | Graph design data — no fault state |
| `parse_virtual_network_list` | **No** | Graph design data — no fault state |

Do not add `severity_label` to parsers for design-intent data. Design intent
represents what is *configured*, not what is *functioning*.

---

## config/instances.yaml

```yaml
instances:
  - name: dc-primary
    host: https://apstra-primary.example.com
    username: admin
    password: changeme
    ssl_verify: false
```

`ssl_verify` defaults to `false` — most Apstra deployments use self-signed
certificates.

---

## config/settings.py

Reads `instances.yaml` and returns a list of `ApstraSession` objects.
Does not make any network calls. Supports per-instance credential override
via environment variables — see README for the naming convention.

---

## pyproject.toml

```toml
[project]
name = "apstra-mcp"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "fastmcp>=3.0",
    "httpx>=0.27",
    "pyyaml>=6.0",
    "kuzu>=0.6",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

---

## fastmcp.json

```json
{
  "source": {
    "type": "filesystem",
    "path": "server.py"
  }
}
```

---

## Installation and environment setup

Dependencies must be installed into a virtual environment before running
anything. The system Python will not have `httpx`, `pyyaml`, `fastmcp`, or
`kuzu`.

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install all dependencies (including dev tools)
pip install -e ".[dev]"

# Or install manually
pip install fastmcp httpx pyyaml kuzu pytest pytest-asyncio

# Or with uv
uv sync
```

Always run scripts and the server from within the activated environment, or
prefix commands with `.venv/bin/` explicitly:

```bash
.venv/bin/python auth_test.py
.venv/bin/pytest tests/ -v
.venv/bin/fastmcp run server.py
```

---

## Smoke testing auth

Before building tools, verify the auth layer works against your instances:

```bash
python auth_test.py
```

Watch the output for `token_valid: True` and `host_reachable: True` per
instance. The probe loop fires every 30 seconds and the token refresh loop
fires 5 minutes before expiry. Press Ctrl+C to stop.

```python
# auth_test.py

import asyncio
import logging
from config.settings import load_sessions

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s"
)

async def main():
    sessions = load_sessions()

    for session in sessions:
        await session.authenticate()
        session.start_background_refresh()

    print("\nAll sessions authenticated. Watching background tasks...")
    print("Press Ctrl+C to stop.\n")

    while True:
        for session in sessions:
            print(session.status())
        await asyncio.sleep(10)

asyncio.run(main())
```

---

## Testing convention

Tests call handler functions directly — not through the MCP layer. Use
`unittest.mock` to mock primitive calls; do not make real HTTP calls in tests.
The `ApstraSession` constructor accepts `ssl_verify` as a fourth argument,
defaulting to `False`.

For graph-backed tools, mock the `BlueprintGraphRegistry` or pass a real
`ApstraKuzuGraph` built from fixture data. The graph handler receives `registry`
as a positional argument, so it is straightforward to inject a mock.

```python
# tests/test_anomalies.py

import pytest
from unittest.mock import AsyncMock, patch
from handlers.anomalies import handle_get_anomalies
from primitives.auth_manager import ApstraSession


@pytest.fixture
def mock_session():
    return ApstraSession("test", "https://apstra.test", "admin", "pass")


async def test_get_anomalies_returns_parsed_results(mock_session):
    raw_response = {
        "items": [
            {
                "severity": "critical",
                "anomaly_type": "bgp_session_down",
                "description": "BGP session to peer 10.0.0.1 is down",
                "system_id": "spine1",
            }
        ]
    }

    with patch(
        "handlers.anomalies.live_data_client.get_anomalies",
        new=AsyncMock(return_value=raw_response),
    ):
        result = await handle_get_anomalies([mock_session], "bp-001")

    assert result["instance"] == "test"
    assert result["count"] == 1
    assert result["anomalies"][0]["severity_label"] == "🔴 Critical"
    assert result["_meta"]["data_source"] == "live"
    assert result["_meta"]["display_as"] == "anomaly_table"
    assert "severity_label" not in result["_meta"]
```

Run all tests:

```bash
.venv/bin/pytest tests/ -v
```

---

## Conventions checklist

When implementing any new tool, verify:

- [ ] Virtual environment is active before running anything
- [ ] Tool docstring states the data source (graph / live / design diff)
- [ ] Tool docstring describes every parameter and return field
- [ ] Primitive uses `session._ssl_verify` for the httpx `verify` parameter
- [ ] Primitive uses `AUTHTOKEN` as the header name (not `AuthToken`)
- [ ] Handler catches exceptions per-session and returns structured errors
- [ ] Handler does not import from `fastmcp` or `tools/`
- [ ] Primitives do not import from `handlers/` or `tools/`
- [ ] `response_parser` functions are pure — no I/O, no session access
- [ ] Tests mock at the primitive level, not the HTTP level
- [ ] `compute_response_meta` is called with `display_as` and `data_source` only — never pass items to it
- [ ] `severity_label` is only added to parsed items where the underlying API data has an explicit fault/operational state field