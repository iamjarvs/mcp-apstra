# apstra-mcp Quickstart

This is the shortest path to run the server locally with one Apstra instance.

## 1. Clone and install

```bash
git clone <your-repo-url>
cd v2_apstra-mcp-server-v2

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 2. Configure one instance

Optional but recommended first pass (preview only):

```bash
apstra-mcp-setup \
  --host https://apstra.example.com \
  --username admin \
  --dry-run
```

Then run for real:

```bash
apstra-mcp-setup --host https://apstra.example.com --username admin
```

The command prompts for your password and writes:

- `config/instances.yaml`
- `.vscode/mcp.json`

It also performs a preflight review and, if it updates an existing file, creates:

- file backups (`*.bak.<timestamp>`)
- rollback script (`.setup-backups/rollback-<timestamp>.sh`)

If you also want Claude Desktop configured automatically:

```bash
apstra-mcp-setup \
  --host https://apstra.example.com \
  --username admin \
  --configure-claude
```

When run interactively, the wizard also **auto-detects installed MCP clients**
(Claude Desktop, Cursor, LM Studio, and the Gemini Antigravity IDE) and offers to
write the server entry into each one it finds, at that client's default config
path. You can also target them explicitly in a non-interactive run:

```bash
apstra-mcp-setup \
  --host https://apstra.example.com \
  --username admin \
  --configure-cursor \
  --configure-lmstudio \
  --configure-antigravity
```

Each client has a matching `--<client>-config-path` flag if you keep its config
in a non-default location. Default paths:

| Client      | Default config path                          | Root key      |
| ----------- | -------------------------------------------- | ------------- |
| VS Code     | `.vscode/mcp.json` (workspace)               | `servers`     |
| Claude      | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) | `mcpServers` |
| Cursor      | `~/.cursor/mcp.json`                         | `mcpServers`  |
| LM Studio   | `~/.lmstudio/mcp.json`                        | `mcpServers`  |
| Antigravity | `~/.gemini/config/mcp_config.json`            | `mcpServers`  |

If you want optional RAG fully configured and embeddings built in the same flow:

```bash
pip install -r knowledge/build/requirements.txt
# Add your PDFs to knowledge/build/source_pdfs
apstra-mcp-setup \
  --host https://apstra.example.com \
  --username admin \
  --enable-rag \
  --build-embeddings
```

Manual fallback (if you do not want to run the setup command):

```bash
cp config/instances.yaml.example config/instances.yaml
```

Then edit `config/instances.yaml` and set:

- `host`: your Apstra controller URL
- `username`: your account
- `password`: your account password
- `ssl_verify`: `false` for self-signed certs, `true` for CA-signed certs

## 3. Validate connectivity/auth and run

```bash
python tests/diagnose_connection.py
python auth_test.py
python server.py
```

Notes:

- `tests/diagnose_connection.py` checks host reachability and login end-to-end.
- Some controllers return HTTP `201` (not `200`) for successful `/api/aaa/login` responses.
  The diagnostic treats both as success when a token is present.

If auth succeeds, the server starts and is ready for MCP clients.

## 4. Verify background data collection

The server does more than answer live queries: at startup it launches two
background pollers that continuously extract data from Apstra into local SQLite
stores under `data/`, so that historical/trend tools have data to work with:

- **Anomaly timeline poller** — backfills ~30 days of anomaly history, then
  samples roughly every **60 seconds** into `data/anomaly_timeseries.db`.
- **Interface counter poller** — samples interface counters roughly every
  **5 minutes** into `data/counter_timeseries.db`.

These pollers only run while `server.py` is running. **Leave the server running
for a few minutes** after first start (the anomaly backfill and the first counter
sample need time), then confirm collection is healthy:

```bash
python tests/verify_data_collection.py
```

You should see recent poll timestamps and non-zero counts for each blueprint /
instance. The script exits non-zero if a store is missing/empty or the newest
sample is older than the freshness threshold, so it is safe to use in CI or a
setup smoke check. Useful options:

```bash
python tests/verify_data_collection.py --max-age-minutes 15   # freshness window
python tests/verify_data_collection.py --json                 # machine-readable
```

If it reports "not healthy", the usual causes are: the server was only just
started (wait a few minutes and rerun), the server is not running, or it cannot
authenticate to Apstra (rerun `python tests/diagnose_connection.py`).

> The store locations honour `MCP_DATA_DIR`, `MCP_ANOMALY_DB_PATH`, and
> `MCP_COUNTER_DB_PATH`; the verify script resolves them the same way the server
> does, so overriding those env vars is picked up automatically.

## 5. Connect from a client

### Claude Desktop

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
        "APSTRA_CONFIG_FILE": "/absolute/path/to/v2_apstra-mcp-server-v2/config/instances.yaml"
      }
    }
  }
}
```

### VS Code Copilot

Create `.vscode/mcp.json`:

```json
{
  "servers": {
    "apstra": {
      "command": "uvx",
      "args": [
        "--from", "/absolute/path/to/v2_apstra-mcp-server-v2",
        "apstra-mcp"
      ],
      "env": {
        "APSTRA_CONFIG_FILE": "/absolute/path/to/v2_apstra-mcp-server-v2/config/instances.yaml"
      }
    }
  }
}
```

### Cursor / LM Studio / Antigravity

These clients use the same `mcpServers` shape as Claude Desktop — the only
difference is the file location (see the table in step 2). Add the same entry to:

- **Cursor** — `~/.cursor/mcp.json`
- **LM Studio** (v0.3.17+) — `~/.lmstudio/mcp.json`
- **Antigravity** (Gemini IDE) — `~/.gemini/config/mcp_config.json` (global) or
  `.agents/mcp_config.json` in a workspace

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
        "APSTRA_CONFIG_FILE": "/absolute/path/to/v2_apstra-mcp-server-v2/config/instances.yaml"
      }
    }
  }
}
```

Restart the client after editing so it picks up the new server.

### A note on Ollama

Ollama is a **model runtime**, not an MCP host — it does not read an MCP config
file to consume this server, so there is no Ollama client entry to write. In this
project Ollama's role is the optional **RAG embedding provider** (the default),
configured via the setup wizard's `--rag-provider ollama` path, not as an MCP
client. To drive this server's tools with an Ollama-served model, use an MCP host
that supports Ollama models (for example LM Studio) and point it at the server as
shown above.

## Security checklist

- Never commit `config/instances.yaml`.
- Prefer per-instance environment variable overrides for secrets when possible.
- Keep `MCP_VERBOSE=0` unless troubleshooting.
- Rotate credentials immediately if they were exposed in logs or history.
