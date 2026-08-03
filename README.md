# apstra-mcp

A FastMCP server for Juniper Apstra network operations and troubleshooting.

This server is read-only. It lets MCP clients (Claude Desktop, VS Code Copilot, Cursor, and others) query live Apstra state, inspect design intent, run JunOS show commands through Apstra, analyze anomaly timelines, and troubleshoot commit blockers.

Current codebase scope (May 2026): compact-by-default MCP tool surface with umbrella dispatchers for anomaly, telemetry, VN/VRF, and probes. Set `MCP_TOOL_SURFACE=full` to expose all granular legacy tools.

## Table of contents

- [5-minute quickstart](#5-minute-quickstart)
- [Automated setup CLI](#automated-setup-cli)
- [What changed recently](#what-changed-recently)
- [Quick install](#quick-install)
- [Local development quick start](#local-development-quick-start)
- [Configuration](#configuration)
- [Running the server](#running-the-server)
- [Recommended troubleshooting flow](#recommended-troubleshooting-flow)
- [Security](#security)
- [Tool catalog (full surface)](#tool-catalog-full-surface)
- [Architecture](#architecture)
- [Data sources and freshness](#data-sources-and-freshness)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Requirements](#requirements)

## What changed recently

- Added optional RAG (Retrieval-Augmented Generation) for product documentation search:
  - `query_apstra_product_docs` tool for semantic search over Apstra docs using vector embeddings.
  - Supports Ollama, LM Studio, and OpenAI-compatible embedding providers.
  - Includes desktop and web UI for building custom knowledge indexes from PDFs.
- Expanded from a smaller core toolset to 48 tools.
- Added anomaly timeline and analytics workflows backed by local stores.
- Added probe tooling: `get_probe_list`, `get_probe_detail`, `get_probe_history`.
- Added telemetry trend tooling: `get_interface_error_trend`, `get_top_error_growers`.
- Added reference-guide token optimization flow:
  - `get_reference_design_overview`
  - `get_reference_design_section`
  - `get_junos_command_categories`
  - `get_junos_show_commands`
- Added blueprint commit troubleshooting:
  - `get_blueprint_build_errors` uses digest-first checks and only fetches full error payloads when needed.
- Added discover-first routing policy diagnostics:
  - `routing_policy` dispatcher (`discover`, `peer_summary`, `explain_policy`, `diagnose_hidden_routes`, `compare_rib`, `resolve_next_hop`, `full_audit`)
- Added compact umbrella dispatchers:
  - `anomaly`, `telemetry`, `virtual_networks`, `probes`
  - Use `MCP_TOOL_SURFACE=full` for legacy per-function tool exposure.
- Added chart rendering tool:
  - `generate_chart` renders line/bar/stacked_bar/heatmap/scatter charts and returns PNG images for visual trend analysis.

## 5-minute quickstart

If you just want to get up and running quickly, follow this path.
For the complete guide, see [QUICKSTART.md](QUICKSTART.md).

```bash
git clone <your-repo-url>
cd v2_apstra-mcp-server-v2

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

apstra-mcp-setup --host https://apstra.example.com --username admin

python auth_test.py
python server.py
```

The setup command prompts for password securely and writes:

- `config/instances.yaml`
- `.vscode/mcp.json`

Optional one-shot RAG + embedding build:

```bash
pip install -r knowledge/build/requirements.txt
apstra-mcp-setup \
  --host https://apstra.example.com \
  --username admin \
  --enable-rag \
  --build-embeddings
```

Optional (run as an MCP server without entering a venv):

```bash
uvx --from /absolute/path/to/v2_apstra-mcp-server-v2 apstra-mcp
```

Security notes for quickstart:

- Never commit `config/instances.yaml`.
- Use strong credentials or per-instance environment variable overrides.
- Keep `MCP_VERBOSE=0` unless actively troubleshooting.

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting and hardening guidance.

## Automated setup CLI

Use `apstra-mcp-setup` to auto-configure local files and MCP config.

Basic usage (interactive password prompt):

```bash
apstra-mcp-setup --host https://apstra.example.com --username admin
```

Preview everything first (no writes, no embedding build):

```bash
apstra-mcp-setup \
  --host https://apstra.example.com \
  --username admin \
  --dry-run
```

Also update Claude Desktop config automatically:

```bash
apstra-mcp-setup \
  --host https://apstra.example.com \
  --username admin \
  --configure-claude
```

Common flags:

- `--dry-run`: show planned actions without modifying files
- `--check-endpoint`: run a TCP reachability check during preflight
- `--ui auto|plain|rich`: choose terminal rendering mode
- `--overwrite`: replace existing `config/instances.yaml`
- `--overwrite-mcp-server`: replace existing `apstra` MCP entry
- `--server-name`: set MCP server key (default: `apstra`)
- `--skip-vscode`: only write instance config (skip `.vscode/mcp.json`)
- `--non-interactive --password <value>`: CI/automation mode
- `--enable-rag`: write the optional `rag` block in `config/instances.yaml`
- `--build-embeddings`: build `knowledge/index.embeddings.json` from PDFs
- `--rag-source-dir`: defaults to `knowledge/build/source_pdfs`

Backup and rollback behavior:

- Existing files are backed up before write: `*.bak.<timestamp>`
- A rollback script is generated at `.setup-backups/rollback-<timestamp>.sh`
- In `--dry-run`, backup and rollback paths are previewed but not created

## Quick install

### Claude Desktop

#### Option 1: single Apstra instance

```json
{
  "mcpServers": {
    "apstra": {
      "command": "uvx",
      "args": ["apstra-mcp"],
      "env": {
        "APSTRA_HOST": "https://apstra.example.com",
        "APSTRA_USERNAME": "admin",
        "APSTRA_PASSWORD": "your-strong-password"
      }
    }
  }
}
```

#### Option 2: multiple Apstra instances via YAML

`~/.apstra/instances.yaml`:

```yaml
instances:
  - name: dc-primary
    host: https://apstra-prod.example.com
    username: admin
    password: your-strong-password
    ssl_verify: false

  - name: dc-dr
    host: https://apstra-dr.example.com
    username: admin
    password: your-strong-password
    ssl_verify: false
```

Claude config:

```json
{
  "mcpServers": {
    "apstra": {
      "command": "uvx",
      "args": ["apstra-mcp"],
      "env": {
        "APSTRA_CONFIG_FILE": "/Users/yourname/.apstra/instances.yaml"
      }
    }
  }
}
```

Per-instance credential overrides are supported:

```bash
APSTRA_DC_PRIMARY_USERNAME=admin
APSTRA_DC_PRIMARY_PASSWORD=your-strong-password
```

`dc-primary` becomes `APSTRA_DC_PRIMARY_*` (uppercase, hyphens converted to underscores).

#### Claude config location

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

### VS Code + GitHub Copilot

Create `.vscode/mcp.json`:

```json
{
  "servers": {
    "apstra": {
      "command": "uvx",
      "args": ["apstra-mcp"],
      "env": {
        "APSTRA_HOST": "https://apstra.example.com",
        "APSTRA_USERNAME": "admin",
        "APSTRA_PASSWORD": "your-strong-password"
      }
    }
  }
}
```

For multi-instance:

```json
{
  "servers": {
    "apstra": {
      "command": "uvx",
      "args": ["apstra-mcp"],
      "env": {
        "APSTRA_CONFIG_FILE": "/Users/yourname/.apstra/instances.yaml"
      }
    }
  }
}
```

### Run from local source with uvx

```json
{
  "mcpServers": {
    "apstra": {
      "command": "uvx",
      "args": [
        "--from", "/absolute/path/to/v2_apstra-mcp-server-v2",
        "apstra-mcp"
      ],
      "env": {
        "APSTRA_HOST": "https://apstra.example.com",
        "APSTRA_USERNAME": "admin",
        "APSTRA_PASSWORD": "your-strong-password"
      }
    }
  }
}
```

After code changes:

```bash
uvx --from /absolute/path/to/v2_apstra-mcp-server-v2 --reinstall apstra-mcp
```

## Local development quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp config/instances.yaml.example config/instances.yaml
# edit config/instances.yaml

python auth_test.py
python server.py
```

For stdio MCP clients, `fastmcp run server.py` also works.
If you want the shortest path, use the `5-minute quickstart` section above.

## Configuration

Configuration resolution order in code:

1. `APSTRA_CONFIG_FILE` (explicit YAML path)
2. `config/instances.yaml`
3. Single-instance environment variables (`APSTRA_HOST`, `APSTRA_USERNAME`, `APSTRA_PASSWORD`)

### YAML format

```yaml
instances:
  - name: dc-primary
    host: https://apstra.example.com
    username: admin
    password: changeme
    ssl_verify: false
```

### Apstra configuration environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `APSTRA_CONFIG_FILE` | no | unset | Absolute path to instances YAML |
| `APSTRA_HOST` | yes (single-instance mode) | unset | Controller URL |
| `APSTRA_USERNAME` | yes (single-instance mode) | unset | Login username |
| `APSTRA_PASSWORD` | yes (single-instance mode) | unset | Login password |
| `APSTRA_SSL_VERIFY` | no | `false` | TLS cert validation |
| `APSTRA_INSTANCE_NAME` | no | `default` | Friendly instance label |
| `APSTRA_<NAME>_USERNAME` | no | unset | Per-instance username override |
| `APSTRA_<NAME>_PASSWORD` | no | unset | Per-instance password override |

### Server runtime environment variables

| Variable | Default | Notes |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio`, `http`, or `sse` |
| `MCP_HOST` | `0.0.0.0` | Used for `http` and `sse` |
| `MCP_PORT` | `8000` | Used for `http` and `sse` |
| `MCP_VERBOSE` | `0` | `1` = operational logging, `2` = full debug with payload logging |
| `MCP_TOOL_SURFACE` | `compact` | `compact` exposes umbrella tools; `full` exposes umbrella + granular legacy tools |
| `MCP_RESET_STORES_ON_START` | `0` | If `1`, deletes local anomaly/counter store DB files on startup |
| `MCP_DATA_DIR` | package-local `data/` | Base path for local SQLite stores |
| `MCP_ANOMALY_DB_PATH` | `<MCP_DATA_DIR>/anomaly_timeseries.db` | Full path override |
| `MCP_COUNTER_DB_PATH` | `<MCP_DATA_DIR>/counter_timeseries.db` | Full path override |
| `APSTRA_CHART_PUBLISH_ENABLED` | `false` | Enable optional chart URL publishing for `generate_chart` |
| `APSTRA_CHART_PUBLISH_PROVIDER` | `catbox` | `catbox`, `postimages`, or `freeimage` |
| `APSTRA_CHART_PUBLISH_TIMEOUT_SECONDS` | `20` | HTTP timeout for upload requests |
| `APSTRA_CHART_PUBLISH_STRICT` | `false` | If `true`, upload failures return tool error instead of falling back to image-only |
| `APSTRA_CHART_PUBLISH_INSECURE_SKIP_VERIFY` | `false` | If `true`, disables TLS certificate verification for chart upload HTTP calls (testing only) |
| `APSTRA_CHART_CATBOX_USERHASH` | unset | Optional Catbox user hash for account-linked uploads |
| `APSTRA_CHART_POSTIMAGES_API_URL` | unset | Required when provider is `postimages`; upload endpoint URL |
| `APSTRA_CHART_POSTIMAGES_API_KEY` | unset | Optional API key for `postimages` endpoint |
| `APSTRA_CHART_FREEIMAGE_API_URL` | `https://freeimage.host/api/1/upload` | Freeimage upload endpoint override |
| `APSTRA_CHART_FREEIMAGE_API_KEY` | unset | Required when provider is `freeimage` |

### Optional: Chart URL publishing for inline markdown

`generate_chart` always returns MCP image content. Some clients (including Claude desktop)
show that image only in expanded tool output.

For quick testing, you can optionally publish chart PNGs to a temporary host and get
a markdown-ready URL in tool output.

Catbox example:

```bash
APSTRA_CHART_PUBLISH_ENABLED=true
APSTRA_CHART_PUBLISH_PROVIDER=catbox
```

Postimages example (endpoint varies by account/workflow):

```bash
APSTRA_CHART_PUBLISH_ENABLED=true
APSTRA_CHART_PUBLISH_PROVIDER=postimages
APSTRA_CHART_POSTIMAGES_API_URL=https://<your-postimages-upload-endpoint>
APSTRA_CHART_POSTIMAGES_API_KEY=<optional-key>
```

Freeimage example:

```bash
APSTRA_CHART_PUBLISH_ENABLED=true
APSTRA_CHART_PUBLISH_PROVIDER=freeimage
APSTRA_CHART_FREEIMAGE_API_KEY=<your-freeimage-api-key>
```

When publishing is enabled and upload succeeds, `generate_chart` returns both:
1. MCP image content (for tool viewers)
2. `chart_url` and markdown string (`![title](url)`) for inline chat rendering

If upload fails and `APSTRA_CHART_PUBLISH_STRICT` is not set, the tool falls back
to image-only output and includes `chart_publish_error` metadata instead of failing.

If you see certificate validation failures in local environments, you can temporarily set
`APSTRA_CHART_PUBLISH_INSECURE_SKIP_VERIFY=true` for testing. Do not use this in production.

### Optional: RAG (product documentation search)

The MCP server optionally supports semantic search over Apstra product documentation via the `query_apstra_product_docs` tool. This uses vector embeddings and requires:

1. **Built knowledge index** — PDFs embedded into a JSON index file
2. **Embedding provider** — local (Ollama, LM Studio) or remote (OpenAI-compatible API)

**Quick setup** (Ollama + local docs):

```bash
# 1. Run Ollama locally
ollama serve

# 2. In another terminal, build the knowledge index
cd knowledge/build
python build_index_ui.py
# → Drag/drop PDFs, set model=qwen3-embedding, output to /path/to/index.embeddings.json

# 3. Configure RAG via environment variables
APSTRA_RAG_ENABLED="true"
APSTRA_RAG_EMBEDDING_PROVIDER="ollama"
APSTRA_RAG_EMBEDDING_MODEL="qwen3-embedding"
APSTRA_RAG_EMBEDDING_URL="http://localhost:11434/api/embed"
APSTRA_RAG_TOP_K="5"
```

**Or via YAML** (`config/instances.yaml`):

```yaml
instances:
  - name: apstra-lab
    host: https://apstra.example.com
    username: admin
    password: your-strong-password

rag:
  enabled: true
  embedding_provider: ollama
  embedding_model: qwen3-embedding
  embedding_url: http://localhost:11434/api/embed
  top_k: 5
```

**RAG environment variables** (optional, fallback when YAML is absent):

| Variable | Default | Notes |
|---|---|---|
| `APSTRA_RAG_ENABLED` | unset | Set to `"true"`, `"1"`, or `"yes"` to enable |
| `APSTRA_RAG_EMBEDDING_PROVIDER` | unset | `ollama`, `lmstudio`, or `openai_compatible` |
| `APSTRA_RAG_EMBEDDING_MODEL` | unset | Model name (e.g., `qwen3-embedding`) |
| `APSTRA_RAG_EMBEDDING_URL` | unset | Embedding service endpoint |
| `APSTRA_RAG_TOP_K` | `5` | Number of results to return |

When RAG is enabled and properly configured, the `query_apstra_product_docs` tool becomes available for answering "how do I..." and "what is..." questions about Apstra.

**Full RAG setup guide**: See [RAG_CONFIGURATION.md](RAG_CONFIGURATION.md).

## Running the server

### stdio (default)

```bash
python server.py
```

or

```bash
fastmcp run server.py
```

### Streamable HTTP

```bash
MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=8000 python server.py
```

Endpoint:

```text
http://127.0.0.1:8000/mcp
```

### SSE transport

```bash
MCP_TRANSPORT=sse MCP_HOST=127.0.0.1 MCP_PORT=8001 python server.py
```

### Verbose logging

```bash
# Operational logs
MCP_VERBOSE=1 python server.py

# Full debug logs and payloads
MCP_VERBOSE=2 python server.py
```

### Connectivity pre-check

```bash
python tests/diagnose_connection.py
```

Note: some Apstra/controller variants return HTTP `201` for successful
`/api/aaa/login` responses. The diagnostic treats HTTP `200` and `201` as
success when a token is present.

### Auth pre-check

```bash
python auth_test.py
```

## Recommended troubleshooting flow

When a user reports fabric problems:

1. Call `get_system_liveness` first.
2. Call `get_config_deviations` next.
3. If commit is blocked or suspected, call `get_blueprint_build_errors`.
4. In compact mode, use `anomaly` with `intent="current_live"` and `intent="summary"` first, then a single targeted intent (`events`, `trend`, `correlate_events`, or `device_history`).
5. In full mode, you can also call granular anomaly timeline/analytics tools directly.
6. Use `get_junos_command_categories` + `get_junos_show_commands` before `run_device_commands` when syntax is uncertain.
7. For route-policy, hidden-route, or next-hop resolution issues, call `routing_policy` with `intent="discover"` first, then run only one targeted intent.
8. Use reference guide tools in this order for token efficiency:
   - `get_reference_design_overview`
   - `get_reference_design_section`
   - `get_reference_design_context` only when full-guide context is explicitly needed

## Tool catalog (full surface)

Compact mode exposes umbrella tools (`anomaly`, `telemetry`, `virtual_networks`, `probes`) plus core standalone tools. The catalog below lists the full surface available when `MCP_TOOL_SURFACE=full`.

### Discovery and design state

| Tool | Purpose |
|---|---|
| `get_blueprints` | List blueprints and high-level metadata |
| `get_blueprint_build_errors` | Digest-first commit-blocking error/warning analysis |
| `get_blueprint_configlets` | Blueprint-applied configlets |
| `get_blueprint_property_sets` | Blueprint-applied property sets |
| `get_design_configlets` | Design catalog configlets |
| `get_design_property_sets` | Design catalog property sets |
| `get_blueprint_configlet_drift` | Drift between blueprint and catalog configlets |
| `get_blueprint_property_set_drift` | Drift between blueprint and catalog property sets |

### Fabric health triage

| Tool | Purpose |
|---|---|
| `get_active_system_agent_jobs` | Detect in-flight device jobs before deeper troubleshooting |
| `get_system_liveness` | Detect unreachable systems before deeper troubleshooting |
| `get_config_deviations` | Diff intended vs actual system config |
| `get_current_anomalies` | Current active anomalies |
| `get_active_anomalies_from_store` | Fast anomaly snapshot from local store |

### Inventory, topology, and intent graph

| Tool | Purpose |
|---|---|
| `get_systems` | Discover switches and required `system_id` values |
| `get_system_config_context` | Raw context model used for config rendering |
| `get_interface_list` | Interface inventory and intent-side attributes |
| `get_link_list` | Physical link topology and endpoint data |
| `get_vn_deployments` | VN deployment by switch |
| `get_virtual_networks` | VN inventory and attributes |
| `get_routing_zones` | Routing zone (VRF) inventory |
| `get_routing_zone_detail` | Per-zone detailed deployment |
| `get_virtual_network_detail` | Per-VN detailed deployment |
| `get_fabric_bgp_peerings` | Intra-fabric BGP sessions |
| `get_external_blueprint_peerings` | Fabric-to-external BGP sessions |
| `get_fabric_mtu_check` | MTU consistency and VXLAN headroom checks |

### CLI, rendering, and references

| Tool | Purpose |
|---|---|
| `run_device_commands` | Run JunOS show commands via Apstra fetchcmd |
| `get_rendered_config` | Rendered config by sections/subsections |
| `routing_policy` | Discover-first routing-policy dispatcher for peer health, policy explanation, hidden routes, RIB comparison, and next-hop resolution |
| `generate_chart` | Render chart PNGs (line/bar/stacked/heatmap/scatter) from structured series data |
| `get_reference_design_overview` | Compact index of reference sections |
| `get_reference_design_section` | Fetch one guide section |
| `get_reference_design_context` | Full guide content |
| `get_junos_command_categories` | Command taxonomy for scoped lookup |
| `get_junos_show_commands` | JunOS command reference with optional category filters |

### Product documentation

| Tool | Purpose |
|---|---|
| `query_apstra_product_docs` | Semantic search over Apstra product docs (admin guides, how-tos, best practices). Optional; requires RAG configuration. |

### Telemetry and probe workflows

| Tool | Purpose |
|---|---|
| `get_interface_counters` | Live cumulative interface counters |
| `get_interface_utilisation` | Probe-based utilization and error/discard rates |
| `get_system_telemetry` | Live per-system telemetry metrics |
| `get_interface_error_trend` | Time-series growth for one interface's counters |
| `get_top_error_growers` | Fastest-growing interface errors from local store |
| `get_probe_list` | List probes and stage names |
| `get_probe_detail` | Probe stage details for current state |
| `get_probe_history` | Probe stage history over time |

### Anomaly timeline and analytics

| Tool | Purpose |
|---|---|
| `get_anomaly_events` | Event-level anomaly timeline queries |
| `get_anomaly_summary` | Compact anomaly volume summary |
| `get_device_anomaly_history` | Device-scoped anomaly history |
| `get_anomaly_trend` | Aggregated trend views |
| `get_correlated_faults` | Correlated anomalies around a fault window |
| `get_fault_durations` | Fault duration metrics |
| `get_device_anomaly_heatmap` | Device/time anomaly concentration |
| `correlate_anomaly_events` | Cross-signal correlation from anomaly events |

## Architecture

```text
MCP client (Claude, Copilot, Cursor, etc.)
        |
        v
tools/*.py
        |
        v
handlers/*.py
        |
        v
primitives/*.py
```

Layer responsibilities:

- `tools/`: MCP decorators, parameter schemas, context handling.
- `handlers/`: business logic, blueprint/session resolution, response shaping.
- `primitives/`: shared clients/parsers/stores with no MCP dependency.

Key runtime components:

- `ApstraSession` auth manager with background refresh/probe loops.
- `BlueprintGraphRegistry` with version-aware rebuilds.
- `AnomalyStore` and `CounterStore` SQLite stores.
- Background pollers (`anomaly_poller`, `counter_poller`) that maintain local history.

## Data sources and freshness

| Source | Typical usage |
|---|---|
| Live Apstra REST API | Real-time blueprint/system/anomaly/config data |
| Live fetchcmd API | Device CLI execution via Apstra |
| Blueprint graph cache (Kuzu) | Fast design-intent topology queries |
| Local anomaly store | Historical anomaly event/timeline analytics |
| Local counter store | Error growth and telemetry trend analytics |

Notes:

- Graph data is rebuilt when blueprint version changes.
- Store-backed analytics are near-real-time, based on poll cadence.
- Many telemetry and anomaly tools include a `_meta` section; not every tool response does.

## Testing

Current repository test footprint (May 2026):

- 25 test modules under `tests/`
- 700+ test functions

Run all tests:

```bash
.venv/bin/python -m pytest tests -q
```

Run focused suites:

```bash
.venv/bin/python -m pytest -q tests/test_blueprint_build_errors.py
.venv/bin/python -m pytest -q tests/test_reference.py tests/test_server_instructions.py
```

## Project structure

```text
.
|- server.py
|- setup_cli.py
|- instructions.md
|- pyproject.toml
|- config/
|  |- instances.yaml.example
|  `- settings.py
|- tools/
|  |- anomalies.py
|  |- anomaly_analytics.py
|  |- anomaly_timeline.py
|  |- bgp.py
|  |- blueprints.py
|  |- config_rendering.py
|  |- design.py
|  |- interfaces.py
|  |- links.py
|  |- mtu_check.py
|  |- probes.py
|  |- reference.py
|  |- routing_policy.py
|  |- run_commands.py
|  |- system_health.py
|  |- systems.py
|  |- telemetry.py
|  `- virtual_networks.py
|- handlers/
|- primitives/
|- tests/
`- _ref_arch/
```

## Requirements

- Python >= 3.10
- Juniper Apstra environment with API access

Core dependencies from `pyproject.toml`:

- `fastmcp>=3.2.0`
- `httpx>=0.28.1`
- `pyyaml>=6.0.3`
- `kuzu>=0.11.3`

Optional RAG (product documentation) dependencies:

- `pypdf>=5.5.0` (for PDF chunking in knowledge builder)
- `flask` (for web UI fallback when Tk unavailable)
- `ollama` or `lmstudio` or compatible embedding provider (local or remote)

Dev dependencies:

- `pytest>=9.0.3`
- `pytest-asyncio>=1.3.0`

## License

This project is source-available under the license in [LICENSE](LICENSE).

Commercial product use is not permitted without prior written permission from
the copyright holder.
