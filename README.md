# mcp-apstra

A [FastMCP](https://github.com/jlowin/fastmcp) server that exposes Juniper Apstra data centre network management capabilities as tools for LLM agents. Connect Claude, Cursor, or any MCP-compatible AI assistant directly to your Apstra fabric — query anomalies, inspect BGP peerings, audit MTU, diff configlets, and run CLI commands across entire fabrics in parallel.

Supports multiple Apstra instances simultaneously. Clean three-layer architecture: MCP tool registration → business logic handlers → shared primitives.

---

## Table of contents

- [What this does](#what-this-does)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Running the server](#running-the-server)
- [Connecting to Claude Desktop](#connecting-to-claude-desktop)
- [Available tools](#available-tools)
- [Architecture](#architecture)
- [Data sources](#data-sources)
- [Testing](#testing)
- [Project structure](#project-structure)

---

## What this does

Apstra is a data centre network management platform that maintains a graph database of network design intent, collects live telemetry from devices, and tracks the difference between staged (planned) and active (deployed) network state. This MCP server makes those data sources available to an LLM as callable tools.

An AI assistant with this server connected can answer questions like:

- *"Are there any active anomalies in the production fabric right now?"*
- *"Show me all BGP peerings from Leaf-01 to external systems"*
- *"Which configlets applied to blueprint dc-prod have drifted from the design catalogue?"*
- *"Run `show version` across every switch in the fabric and tell me if any are running an older OS version"*
- *"Check the MTU configuration and flag any mismatches that would break VXLAN"*

---

## Quick start

```bash
# 1. Clone and enter the repo
git clone git@github.com:iamjarvs/mcp-apstra.git
cd mcp-apstra

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -e ".[dev]"

# 4. Configure your Apstra instance(s)
cp config/instances.yaml.example config/instances.yaml
# Edit config/instances.yaml with your host, username, and password

# 5. Verify auth before starting the server
python auth_test.py

# 6. Run the server
fastmcp run server.py
```

---

## Configuration

Edit `config/instances.yaml`. An example file is provided at `config/instances.yaml.example`:

```yaml
instances:
  - name: dc-primary
    host: https://apstra.example.com
    username: admin
    password: changeme
    ssl_verify: false   # set to true if you have a valid signed cert

  # - name: dc-secondary          # add as many instances as needed
  #   host: https://apstra-dr.example.com
  #   username: admin
  #   password: changeme
  #   ssl_verify: false
```

`instances.yaml` is excluded from git (it contains credentials). Never commit it.

### Environment variable overrides

Credentials can be overridden with environment variables — recommended for production and CI:

```bash
# Variable name: APSTRA_{NAME_UPPERCASED}_USERNAME / _PASSWORD
# Hyphens in the instance name become underscores.
export APSTRA_DC_PRIMARY_USERNAME=admin
export APSTRA_DC_PRIMARY_PASSWORD=secretpassword
```

---

## Running the server

### stdio (default — for Claude Desktop, Cursor, etc.)

```bash
fastmcp run server.py
# or
.venv/bin/fastmcp run server.py
```

### HTTP transport

```bash
MCP_TRANSPORT=http MCP_HOST=0.0.0.0 MCP_PORT=8000 fastmcp run server.py
```

### Debug / verbose mode

Enables per-request logging, timing middleware, and payload inspection:

```bash
MCP_VERBOSE=1 fastmcp run server.py
```

### Smoke-test auth independently

Before running the full server, verify every instance authenticates correctly:

```bash
python auth_test.py
```

Prints session status every 10 seconds — confirm `token_valid: True` and `host_reachable: True` for each instance.

---

## Connecting to Claude Desktop

Add this to your Claude Desktop `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "apstra": {
      "command": "/path/to/mcp-apstra/.venv/bin/fastmcp",
      "args": ["run", "/path/to/mcp-apstra/server.py"]
    }
  }
}
```

Replace `/path/to/mcp-apstra` with the absolute path to this repo. Restart Claude Desktop after saving.

---

## Available tools

22 tools across 7 categories. Every tool response includes a `_meta` block documenting data source, instance, timestamp, and LLM usage hints.

### Anomalies

| Tool | Description |
|------|-------------|
| `get_current_anomalies` | Active anomalies for a blueprint — severity, type, affected device, and description |

### Blueprints & instances

| Tool | Description |
|------|-------------|
| `get_blueprints` | All blueprints across all instances (or a named instance) with status and type |
| `get_blueprint_configlets` | Configlets applied to a blueprint — condition expressions and Jinja2 generators |
| `get_blueprint_property_sets` | Property sets applied to a blueprint — key-value variables injected into configlet templates |

### Systems (switches)

| Tool | Description |
|------|-------------|
| `get_systems` | All switch systems in a blueprint with role, deploy state, and chassis info |
| `get_system_config_context` | Full design-time config context for a switch — the data model Apstra uses to render device config |

### Virtual networks & routing

| Tool | Description |
|------|-------------|
| `get_vn_deployments` | Where each VN is deployed — one row per VN per switch, including local VLAN ID |
| `get_virtual_networks` | VN design list with routing zone membership, VXLAN VNI, and IP gateway config |
| `get_routing_zones` | All routing/security zones (VRFs) in a blueprint with VN count |
| `get_routing_zone_detail` | Per-switch deployment detail for a single routing zone — interfaces, attached VNs |
| `get_virtual_network_detail` | Per-switch deployment detail for a single VN — VLAN, gateway, bound interfaces |

### BGP

| Tool | Description |
|------|-------------|
| `get_external_blueprint_peerings` | BGP sessions between fabric devices and external systems (routers, firewalls, servers) |
| `get_fabric_bgp_peerings` | Intra-fabric eBGP sessions (spine-leaf underlay, ESI peer links) |

### Interfaces & links

| Tool | Description |
|------|-------------|
| `get_interface_list` | All interfaces for a switch — type, description, IP, operational state |
| `get_link_list` | Physical fabric links with both endpoints, link role, type, and speed |

### Config & design

| Tool | Description |
|------|-------------|
| `get_rendered_config` | Full JunOS/EOS rendered config for a switch, parsed into hierarchical sections; supports narrowing to specific sections or subsections |
| `get_design_configlets` | All configlets in the instance-level design catalogue (master copies) |
| `get_design_property_sets` | All property sets in the instance-level design catalogue |
| `get_blueprint_configlet_drift` | Compares blueprint-applied configlets against design catalogue — reports drifted templates |
| `get_blueprint_property_set_drift` | Compares blueprint-applied property sets against design catalogue — reports drifted values |
| `get_fabric_mtu_check` | Audits MTU across the fabric; validates physical/inet symmetry and VXLAN headroom |
| `get_reference_design_context` | Full Apstra Reference Design Guide as structured Markdown |

### Command execution

| Tool | Description |
|------|-------------|
| `run_device_commands` | Runs one or more CLI commands on a single switch or all switches in a blueprint via the Apstra fetchcmd API. Commands run in parallel — all switches in a batch execute concurrently up to `max_concurrent_systems` (default 10) |

**`run_device_commands` parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `blueprint_id` | string | required | Target blueprint |
| `commands` | list[string] | required | CLI commands to run (e.g. `["show version", "show bgp summary"]`) |
| `system_id` | string | optional | Target a single switch (chassis serial). Omit to run on all switches |
| `output_format` | string | `"json"` | `"json"` or `"text"` |
| `timeout_seconds` | int | `30` | Per-command poll timeout |
| `max_concurrent_systems` | int | `10` | Max switches queried simultaneously. Increase up to ~20 for larger fabrics |
| `instance_name` | string | optional | Target a specific Apstra instance |

---

## Architecture

```
MCP client (Claude, Cursor …)
        │
        ▼
  tools/*.py          @mcp.tool() decorators — parameter parsing, ctx extraction
        │
        ▼
  handlers/*.py       Business logic, fan-out across instances, response shaping
        │
        ▼
  primitives/         Shared, MCP-unaware functions and classes
  ├── auth_manager.py        ApstraSession — auth, token refresh, host probe
  ├── graph_client.py        ApstraKuzuGraph + BlueprintGraphRegistry
  ├── live_data_client.py    Async HTTP wrappers for live Apstra REST API
  ├── design_diff_client.py  Staged vs active state comparison
  └── response_parser.py     Normalisation and LLM formatting hints
```

### Authentication and keep-alive

`primitives/auth_manager.py` handles all auth. Tokens are acquired at startup and kept valid by two background asyncio tasks per session:

- **Token refresh loop** — decodes the JWT `exp` claim, re-authenticates 5 minutes before expiry. Retries every 15 seconds on failure.
- **Probe loop** — calls `/api/version` every 30 seconds to confirm reachability. Attempts re-auth if the probe fails (catches server-side token revocation).

Both `token_valid` and `host_reachable` are tracked independently and exposed in every tool response's `_meta` block.

### Graph database caching

Graph-backed tools use a `BlueprintGraphRegistry` that holds one Kuzu in-memory database per blueprint. Before each query the registry checks the Apstra blueprint version endpoint:

- **Version unchanged** → returns the existing graph immediately (fast path)
- **Version incremented** → rebuilds the graph from the full blueprint graph API response

This means the first query for a blueprint is slower (full rebuild), but subsequent queries are fast, and the graph stays automatically consistent with Apstra after every commit.

### Multi-instance support

All handlers receive the full session pool. Each handler decides whether to route to a specific instance (`instance_name` parameter) or fan out across all instances and merge the results. Responses always include a `instance` field in `_meta` so the LLM knows which controller produced the data.

---

## Data sources

| Source | Key | What it contains | Notes |
|--------|-----|------------------|-------|
| Live REST API | `live` | Real-time operational state, anomalies, rendered config | Variable latency; reflects current fault state |
| Blueprint graph | `blueprint_design` | Design intent — graph of devices, links, VNs, policies | Version-cached; rebuilt automatically on commit |
| Design catalogue | `design_catalogue` | Instance-level master copies of configlets, property sets | Queried live on each call |
| Fetchcmd API | `live_fetchcmd` | CLI command output from device telemetry agent | Async poll; respects `timeout_seconds` |

The data source for every tool is documented in its docstring and in the `_meta.data_source` field of every response.

---

## Testing

504 tests, all unit tests — no live Apstra connection required.

```bash
# Run all tests
.venv/bin/pytest tests/ -v

# Run a specific module
.venv/bin/pytest tests/test_run_commands.py -v

# Quiet summary
.venv/bin/pytest tests/ -q
```

Tests mock at the primitive layer (`live_data_client`, `graph_client`, `auth_manager`). Handlers and parsers are tested against real response shapes captured from a live Apstra environment.

---

## Project structure

```
mcp-apstra/
├── server.py                    # FastMCP app — tool registration and lifespan
├── pyproject.toml               # Project metadata and dependencies
├── auth_test.py                 # Standalone auth smoke test
│
├── config/
│   ├── instances.yaml           # Your instance config (git-ignored — contains credentials)
│   ├── instances.yaml.example   # Template to copy
│   └── settings.py              # Reads instances.yaml, builds session pool
│
├── tools/                       # @mcp.tool() wrappers — parameter parsing only
│   ├── anomalies.py             # get_current_anomalies
│   ├── bgp.py                   # get_external_blueprint_peerings, get_fabric_bgp_peerings
│   ├── blueprints.py            # get_blueprints, get_blueprint_configlets, get_blueprint_property_sets
│   ├── config_rendering.py      # get_rendered_config
│   ├── design.py                # get_design_configlets, get_design_property_sets,
│   │                            #   get_blueprint_configlet_drift, get_blueprint_property_set_drift
│   ├── interfaces.py            # get_interface_list
│   ├── links.py                 # get_link_list
│   ├── mtu_check.py             # get_fabric_mtu_check
│   ├── reference.py             # get_reference_design_context
│   ├── run_commands.py          # run_device_commands
│   ├── systems.py               # get_systems, get_system_config_context
│   └── virtual_networks.py      # get_vn_deployments, get_virtual_networks,
│                                #   get_routing_zones, get_routing_zone_detail,
│                                #   get_virtual_network_detail
│
├── handlers/                    # Business logic — no MCP imports
│   ├── anomalies.py
│   ├── bgp.py
│   ├── blueprints.py
│   ├── config_rendering.py
│   ├── design.py
│   ├── interfaces.py
│   ├── links.py
│   ├── mtu_check.py
│   ├── run_commands.py
│   ├── systems.py
│   └── virtual_networks.py
│
├── primitives/                  # Shared, MCP-unaware Python
│   ├── auth_manager.py          # ApstraSession class
│   ├── design_diff_client.py    # Staged vs active state comparison
│   ├── graph_client.py          # ApstraKuzuGraph + BlueprintGraphRegistry
│   ├── live_data_client.py      # Async HTTP wrappers
│   └── response_parser.py       # Normalisation and formatting
│
└── tests/
    ├── test_anomalies.py
    ├── test_bgp.py
    ├── test_blueprints.py
    ├── test_config_rendering.py
    ├── test_design.py
    ├── test_graph_client.py
    ├── test_interfaces.py
    ├── test_links.py
    ├── test_mtu_check.py
    ├── test_run_commands.py
    ├── test_systems.py
    └── test_virtual_networks.py
```

---

## Requirements

- Python 3.10+
- Juniper Apstra 4.x or 5.x
- Apstra user account with API read access

### Python dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `fastmcp` | ≥ 2.0 | MCP server framework |
| `httpx` | ≥ 0.27 | Async HTTP client |
| `pyyaml` | ≥ 6.0 | YAML config parsing |
| `kuzu` | ≥ 0.6 | In-memory graph database |
| `pytest` | ≥ 8.0 | Test runner (dev) |
| `pytest-asyncio` | ≥ 0.23 | Async test support (dev) |

---

## Licence

MIT
