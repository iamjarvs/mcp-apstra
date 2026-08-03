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

## 4. Connect from a client

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

## Security checklist

- Never commit `config/instances.yaml`.
- Prefer per-instance environment variable overrides for secrets when possible.
- Keep `MCP_VERBOSE=0` unless troubleshooting.
- Rotate credentials immediately if they were exposed in logs or history.
