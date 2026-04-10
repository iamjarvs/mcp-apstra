# LLM Working Instructions

Read this document fully before writing any code. It is the source of truth
for how to work on this project. The DESIGN.md file contains the detailed
patterns for each layer — this document tells you how to use them correctly,
what you must never do, and what process to follow.

---

## What this project is

An MCP server that exposes Juniper Apstra (network automation platform) data
to an LLM. It connects to one or more Apstra instances, keeps authentication
alive continuously in the background, and provides tools the LLM can call to
query live network state and blueprint design intent.

### Domain concepts you must understand

**Instance**: A single Apstra controller (virtual machine). One instance
manages its own independent set of blueprints. This server can connect to
multiple instances simultaneously.

**Blueprint**: A running data centre managed by an Apstra instance. Each
instance can have multiple blueprints. A blueprint represents a complete
deployed fabric — devices, cabling, routing policy, and intent.

**Relationship**: 3 instances × 3 blueprints = 9 blueprints total. Tools that
accept `instance_name` operate at the instance level. Tools that accept
`blueprint_id` operate at the data centre level.

---

## Architecture — the four layers

```
server.py
  └── tools/<n>.py           @mcp.tool() wrapper — calls handler only
        └── handlers/<n>.py   Business logic — calls primitives only
              └── primitives/  HTTP calls + data transformation
                    └── Apstra REST API
```

Each layer has strictly defined responsibilities. Crossing layer boundaries
is the most common mistake — do not do it.

| Layer | File pattern | Responsibility | Must NOT |
|---|---|---|---|
| Server | `server.py` | Init FastMCP, lifespan, register tools | Contain logic, import primitives/handlers |
| Tool | `tools/<n>.py` | Define LLM interface (name, docstring, params) | Contain business logic |
| Handler | `handlers/<n>.py` | Orchestrate primitive calls, shape response | Import MCP, make HTTP calls |
| Primitives | `primitives/*.py` | HTTP calls, data transformation | Contain business logic |

---

## Rules you must follow

### server.py
- The lifespan hook must `yield {"sessions": sessions, "graph_registry": registry}`.
  This is how FastMCP 3.x passes state. Do not use `app.state`.
- Add one import and one `register(mcp)` call per new tool module. Nothing else.
- The `FastMCP()` constructor takes `instructions=`, not `description=`.

### tools/<n>.py
- Import `Context` from `fastmcp` and add `ctx: Context = None` as the last parameter.
- Access sessions as `ctx.lifespan_context["sessions"]`.
- Graph-backed tools also access `ctx.lifespan_context["graph_registry"]`.
- The function body must be a single `return await handle_*()` call.
- The docstring is the LLM's entire understanding of the tool — it must state:
  what the tool returns, which data source it uses (`live` or `blueprint_design`),
  what each parameter means, and the exact shape of the return value including
  the `_meta` block.
- `ctx` must never appear in the docstring — FastMCP hides it from the LLM.

### handlers/<n>.py
- No MCP imports. This file must be testable without a running server.
- First argument is always `sessions` (a list of `ApstraSession` objects).
- Graph-backed handlers take `registry` as their second argument.
- Always support `instance_name=None` to query all instances.
- Use `_select_sessions()` for instance filtering — copy the pattern exactly.
- Catch exceptions per-session. Never let an exception from one session
  propagate and kill results from other sessions.
- Return a flat dict for a single session, an aggregated dict for multiple.
- Always include `"_meta": response_parser.compute_response_meta(...)` in every
  success response. Pass `data_source="blueprint_design"` for graph-backed tools.
- Never make HTTP calls directly.

### primitives/live_data_client.py
- All functions are `async` and accept a session as their first argument.
- Use the private `_request(session, method, path)` helper for all HTTP calls —
  never write httpx boilerplate directly in a public function.
- Return raw JSON only — no transformation here.
- One public function per distinct API endpoint.

### primitives/response_parser.py
- Pure functions only. No HTTP calls, no session access, no side effects.
- Always use `.get()` with safe defaults — never assume a field is present.
- Always inspect the live API response (or live graph query) before writing
  a parser. See the "Inspecting endpoints" section below.
- `severity_label` on individual items is only appropriate when the underlying
  data has a real severity or operational state field (anomaly severity,
  blueprint build error counts, switch deploy_mode). Do not invent severity
  labels for configuration/design data — the LLM decides how to narrate those.
- `compute_response_meta()` returns formatting hints only (no computed severity):
  `data_source`, optional `display_as`, and `section_order`. Call it from every
  handler success path.

### primitives/graph_client.py
- Do not modify `ApstraKuzuGraph` or `BlueprintGraphRegistry` unless changing
  graph build or caching behaviour.
- `_infer_kuzu_type` defaults to STRING for all-null property values so that
  Cypher queries referencing those fields return null rather than failing.
  This is intentional — do not revert it.
- Cypher queries live as module-level constants in the handler, not in the
  graph client.

### primitives/auth_manager.py
- Do not modify this file unless changing authentication behaviour.
- Call `await session.get_token()` to get the current token.
- Use `session.host` for the base URL.
- Use `session._ssl_verify` for the httpx `verify` parameter.

---

## Process for building a new tool

Follow these steps in order every time.

### 1. Inspect the live endpoint first

For **REST API tools**, probe the endpoint before writing any code:

```bash
.venv/bin/python3.13 - <<'EOF'
import asyncio, json
from config.settings import load_sessions

async def probe(path):
    sessions = load_sessions()
    s = sessions[0]
    await s.authenticate()
    import httpx
    token = await s.get_token()
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(f"{s.host}{path}", headers={"AUTHTOKEN": token})
        r.raise_for_status()
        print(json.dumps(r.json(), indent=2))

asyncio.run(probe("/api/your-endpoint-here"))
EOF
```

For **graph-backed tools**, probe the Cypher query against the live graph:

```bash
.venv/bin/python3.13 - <<'EOF'
import asyncio, json
from config.settings import load_sessions
from primitives.graph_client import BlueprintGraphRegistry

BLUEPRINT_ID = "a7c2cd1d-da6e-4c3c-8e34-acbd131d3e76"

async def main():
    sessions = load_sessions()
    session = sessions[0]
    await session.authenticate()
    registry = BlueprintGraphRegistry()
    graph = await registry.get_or_rebuild(session, BLUEPRINT_ID)
    rows = graph.query("YOUR CYPHER HERE")
    print(f"{len(rows)} rows")
    if rows:
        print("Keys:", list(rows[0].keys()))
        print(json.dumps(rows[0], indent=2, default=str))

asyncio.run(main())
EOF
```

Also check the Kuzu schema for a node type to know which properties exist:

```python
rows = graph.query("CALL table_info('virtual_network') RETURN *")
for r in rows: print(r['name'], '-', r['type'])
```

Never write a parser based on assumed field names. Always inspect the real
response first.

### 2. Build in layer order — bottom up

For **REST API tools** (live data):
1. Add the primitive function to `primitives/live_data_client.py`
2. Add the parser to `primitives/response_parser.py`
3. Create `handlers/<n>.py`
4. Create `tools/<n>.py`
5. Add the import and `register(mcp)` call in `server.py`
6. Create `tests/test_<n>.py`

For **graph-backed tools** (blueprint design data):
1. Add the Cypher constant to the handler as a module-level string
2. Add the parser to `primitives/response_parser.py`
3. Create (or add to) `handlers/<n>.py` — takes `sessions, registry, blueprint_id`
4. Create (or add to) `tools/<n>.py` — passes both `sessions` and `graph_registry`
5. Add the import and `register(mcp)` call in `server.py` if it's a new file
6. Add tests to `tests/test_<n>.py`

Multiple tools can share one handler file and one tool file when they cover the
same domain (e.g. `handlers/virtual_networks.py` holds both
`handle_get_virtual_networks` and `handle_get_virtual_network_list`).

### 3. Test before declaring done

```bash
.venv/bin/pytest tests/ -v
```

All tests must pass before the work is complete.

### 4. QA the server start

```bash
.venv/bin/fastmcp run server.py &
sleep 6 && kill %1 2>/dev/null; wait %1 2>/dev/null; true
```

The output must show `INFO Starting MCP server 'apstra-mcp'` with no errors.

---

## What exists today

### Tools

| Tool name | File | Data source | What it does |
|---|---|---|---|
| `get_current_anomalies` | `tools/anomalies.py` | live | Active anomalies for a blueprint |
| `get_blueprints` | `tools/blueprints.py` | live | All blueprints across all instances |
| `get_systems` | `tools/systems.py` | blueprint_design | All switches in a blueprint |
| `get_vn_deployments` | `tools/virtual_networks.py` | blueprint_design | VN instances per switch (VLAN/VNI per device) |
| `get_virtual_networks` | `tools/virtual_networks.py` | blueprint_design | All VNs in a blueprint joined to their routing zone |

### Primitives

| Function | File | Notes |
|---|---|---|
| `get_anomalies(session, blueprint_id)` | `live_data_client.py` | `GET /api/blueprints/{id}/anomalies` |
| `get_blueprints(session)` | `live_data_client.py` | `GET /api/blueprints` |
| `get_blueprint_versions(session)` | `live_data_client.py` | `GET /api/blueprints` — cheap version map only |
| `get_blueprint_graph(session, blueprint_id)` | `live_data_client.py` | `GET /api/blueprints/{id}` — full graph payload |
| `parse_anomalies(raw)` | `response_parser.py` | Includes `severity_label` per item |
| `parse_blueprints(raw)` | `response_parser.py` | Includes `severity_label` per item |
| `parse_systems(rows)` | `response_parser.py` | Includes `severity_label` per item (from deploy_mode) |
| `parse_virtual_networks(rows)` | `response_parser.py` | No severity_label (design data) |
| `parse_virtual_network_list(rows)` | `response_parser.py` | No severity_label (design data) |
| `compute_response_meta(display_as, data_source)` | `response_parser.py` | Returns `_meta` hints dict — NOT a severity aggregator |

### Graph registry

`primitives/graph_client.py` contains:
- `ApstraKuzuGraph` — Kuzu in-memory graph for one blueprint. Built from nodes
  and relationships dicts. Queries via `graph.query(cypher, params)`.
- `BlueprintGraphRegistry` — manages one graph per `(instance_name, blueprint_id)`.
  Two-call version check: cheap `get_blueprint_versions()` each access,
  expensive `get_blueprint_graph()` only on cache miss or version change.

The registry lives in the lifespan context:

```python
registry = ctx.lifespan_context["graph_registry"]
graph = await registry.get_or_rebuild(session, blueprint_id)
rows = graph.query("MATCH (n:system) RETURN n.id, n.label")
```

### Handler files

| File | Functions |
|---|---|
| `handlers/anomalies.py` | `handle_get_anomalies` |
| `handlers/blueprints.py` | `handle_get_blueprints` |
| `handlers/systems.py` | `handle_get_systems` |
| `handlers/virtual_networks.py` | `handle_get_virtual_networks`, `handle_get_virtual_network_list` |

### Unused stubs
- `primitives/design_diff_client.py` — for staged vs deployed diff queries (not yet implemented)

---

## The _meta block

Every handler success response must include `_meta`. It is a formatting hint
for the LLM, not a data augmentation. It never includes a computed severity.

```python
# Live data handlers
"_meta": response_parser.compute_response_meta(display_as="anomaly_table")

# Graph-backed handlers
"_meta": response_parser.compute_response_meta(
    display_as="vxlan_table", data_source="blueprint_design"
)
```

`display_as` recognised values: `anomaly_table`, `device_state_table`,
`blueprint_table`, `vxlan_table`, `virtual_network_table`.

`data_source`: `"live"` (default) or `"blueprint_design"`.

---

## Severity labels — when to use them

`severity_label` on individual parsed items is appropriate only when the
underlying data field is an explicit operational or fault state:

| Parser | Source field | Label range |
|---|---|---|
| `parse_anomalies` | `severity` string from Apstra | 🔴 Critical / 🟠 Warning / 🟡 Advisory / 🟢 Healthy |
| `parse_blueprints` | `build_errors_count`, `build_warnings_count`, `anomaly_counts.all` | same |
| `parse_systems` | `deploy_mode` | 🟢 Healthy / 🟡 Advisory / 🟠 Warning |

Do **not** add `severity_label` to configuration/design parsers
(`parse_virtual_networks`, `parse_virtual_network_list`, or any future design
parser). The LLM decides how to narrate design data.

---

## What you must never do

- **Never modify `primitives/auth_manager.py`** unless the change is explicitly
  about authentication behaviour.
- **Never make HTTP calls in a handler.** Handlers call primitives only.
- **Never import MCP types in a handler.** Handlers must be testable standalone.
- **Never write a parser without inspecting the live API/graph response first.**
- **Never assume field names.** Apstra API responses often contain more or
  different fields than documented. Always verify with a live probe.
- **Never add business logic to a tool function.** One line: `return await handle_*()`.
- **Never skip tests.** Every tool requires a test file covering the parser,
  session selection, and all handler paths.
- **Never use `app.state`** — FastMCP 3.x uses `ctx.lifespan_context`.
- **Never use `description=`** on `FastMCP()` — use `instructions=`.
- **Never store credentials in `instances.yaml`** beyond local dev.
- **Never add `severity_label` to design/configuration parsers** — only add it
  where the underlying data has an explicit fault or operational state field.
- **Never pass parsed items to `compute_response_meta()`** — the new signature
  is `compute_response_meta(display_as=..., data_source=...)` with no items arg.

---

## Running things

```bash
# Run tests
.venv/bin/pytest tests/ -v

# Run a single test file
.venv/bin/pytest tests/test_anomalies.py -v

# Start the MCP server
.venv/bin/fastmcp run server.py

# Inspect a live REST endpoint
.venv/bin/python3.13 - <<'EOF'
import asyncio, json
from config.settings import load_sessions
async def probe(path):
    sessions = load_sessions()
    s = sessions[0]
    await s.authenticate()
    import httpx
    token = await s.get_token()
    async with httpx.AsyncClient(verify=False) as client:
        r = await client.get(f"{s.host}{path}", headers={"AUTHTOKEN": token})
        r.raise_for_status()
        print(json.dumps(r.json(), indent=2))
asyncio.run(probe("/api/your-endpoint-here"))
EOF

# Inspect a live graph query
.venv/bin/python3.13 - <<'EOF'
import asyncio, json
from config.settings import load_sessions
from primitives.graph_client import BlueprintGraphRegistry
BLUEPRINT_ID = "a7c2cd1d-da6e-4c3c-8e34-acbd131d3e76"
async def main():
    sessions = load_sessions()
    session = sessions[0]
    await session.authenticate()
    registry = BlueprintGraphRegistry()
    graph = await registry.get_or_rebuild(session, BLUEPRINT_ID)
    rows = graph.query("MATCH (n:system) RETURN n.id, n.label LIMIT 5")
    print(json.dumps(rows, indent=2, default=str))
asyncio.run(main())
EOF
```

---

## Key versions

- Python: 3.13
- FastMCP: 3.2.0 (not 2.x — the API is different)
- httpx: 0.27+
- kuzu: 0.11.3
- pytest + pytest-asyncio with `asyncio_mode = "auto"`
- Virtual environment: `.venv/`
- Blueprint ID for testing: `a7c2cd1d-da6e-4c3c-8e34-acbd131d3e76`
