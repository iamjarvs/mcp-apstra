# 8. Configuration reference

> **Why a whole section on env vars?** Everything about *how* the server connects, *which*
> tools it shows, *where* it stores data, and *whether* it searches docs is controlled by
> environment variables. This is your one-page lookup for all of them.

---

## 8.1 Credential sources

The server resolves its Apstra connection in this order (first usable one wins):

1. `APSTRA_CONFIG_FILE` → an explicit path to an instances YAML.
2. `config/instances.yaml` → the default file in the repo.
3. Single-instance env vars → `APSTRA_HOST` / `APSTRA_USERNAME` / `APSTRA_PASSWORD`.

### Apstra connection variables

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `APSTRA_CONFIG_FILE` | no | unset | Absolute path to an instances YAML. |
| `APSTRA_HOST` | yes *(single-instance)* | unset | Controller URL, e.g. `https://apstra.example.com`. |
| `APSTRA_USERNAME` | yes *(single-instance)* | unset | Login username. |
| `APSTRA_PASSWORD` | yes *(single-instance)* | unset | Login password. |
| `APSTRA_SSL_VERIFY` | no | `false` | TLS certificate validation. |
| `APSTRA_INSTANCE_NAME` | no | `default` | Friendly label for the single instance. |
| `APSTRA_<NAME>_USERNAME` | no | unset | Per-instance username override (see below). |
| `APSTRA_<NAME>_PASSWORD` | no | unset | Per-instance password override (see below). |

### Per-instance overrides — keep secrets out of YAML

For each instance in `instances.yaml`, you can override its credentials with environment
variables and leave the password out of the file entirely. The variable name is the
instance name **uppercased, hyphens → underscores, prefixed with `APSTRA_`**:

```yaml
# config/instances.yaml
instances:
  - name: dc-primary
    host: https://apstra.example.com
    username: admin
    # no password here
    ssl_verify: false
```

```bash
# Supply the secret via env instead — takes precedence over the file
export APSTRA_DC_PRIMARY_USERNAME=admin
export APSTRA_DC_PRIMARY_PASSWORD='your-strong-password'
```

This is the recommended pattern for production: the YAML holds only non-secret topology,
and secrets live in the environment (or a secrets manager that injects them).

---

## 8.2 Server runtime variables

| Variable | Default | Notes |
|----------|---------|-------|
| `MCP_TRANSPORT` | `stdio` | `stdio`, `http`, or `sse`. |
| `MCP_HOST` | `0.0.0.0` | Bind address for `http`/`sse`. |
| `MCP_PORT` | `8000` | Port for `http`/`sse`. |
| `MCP_VERBOSE` | `0` | `1` = operational logging, `2` = full debug (logs payloads). |
| `MCP_TOOL_SURFACE` | `compact` | `compact` = umbrella tools (33); `full` = umbrella + granular (58). |
| `MCP_RESET_STORES_ON_START` | `0` | `1` deletes the local anomaly/counter DBs on startup. |
| `MCP_DATA_DIR` | package `data/` | Base directory for the local SQLite stores. |
| `MCP_ANOMALY_DB_PATH` | `<MCP_DATA_DIR>/anomaly_timeseries.db` | Full path override for the anomaly store. |
| `MCP_COUNTER_DB_PATH` | `<MCP_DATA_DIR>/counter_timeseries.db` | Full path override for the counter store. |

> **Security tip:** Keep `MCP_VERBOSE=0` in normal operation — level `2` logs request
> payloads, which can include sensitive data.

### Local stores

Two background pollers maintain rolling-window SQLite databases used by the `anomaly` and
`telemetry` tools: an **anomaly timeline** (sampled ~every 60s) and a **counter/telemetry**
store (sampled ~every 5 min). They're created automatically under `data/` while the server
runs. Point them elsewhere with `MCP_DATA_DIR` (or the per-file overrides), and wipe them
on boot with `MCP_RESET_STORES_ON_START=1` if they ever get into a bad state.

To confirm the pollers are actively collecting (present and recent data), run the bundled
check a few minutes after starting the server — it resolves the same `MCP_DATA_DIR` /
`MCP_ANOMALY_DB_PATH` / `MCP_COUNTER_DB_PATH` paths and exits non-zero if a store is
missing, empty, or stale:

```bash
python tests/verify_data_collection.py            # human-readable report
python tests/verify_data_collection.py --json      # machine-readable
```


---

## 8.3 RAG documentation search

The server can optionally answer questions from Apstra documentation using retrieval
(RAG). It's **disabled by default** and entirely local — you bring your own embedding
provider (Ollama, LM Studio, or an OpenAI-compatible endpoint).

### Option A — YAML block (recommended)

Add a `rag:` block to `config/instances.yaml`:

```yaml
rag:
  enabled: true
  embedding_provider: ollama                       # ollama | lmstudio | openai_compatible
  embedding_model: qwen3-embedding
  embedding_url: http://localhost:11434/api/embed
  top_k: 5
```

### Option B — environment variables

If there's no YAML `rag:` block (or it's disabled), these are checked instead:

| Variable | Notes |
|----------|-------|
| `APSTRA_RAG_ENABLED` | `1`/`true`/`yes` to enable. |
| `APSTRA_RAG_EMBEDDING_PROVIDER` | `ollama`, `lmstudio`, or `openai_compatible`. |
| `APSTRA_RAG_EMBEDDING_MODEL` | Embedding model name. |
| `APSTRA_RAG_EMBEDDING_URL` | Embedding service endpoint. |
| `APSTRA_RAG_TOP_K` | Results per query (default `5`). |

### Building the index

You can let the setup wizard do it in one shot:

```bash
apstra-mcp-setup \
  --host https://apstra.example.com --username admin \
  --enable-rag --build-embeddings \
  --rag-provider ollama \
  --rag-model qwen3-embedding \
  --rag-url http://localhost:11434/api/embed \
  --rag-source-dir /path/to/apstra-pdfs
```

This writes the `rag:` block and builds `knowledge/index.embeddings.json` from the PDFs in
`--rag-source-dir`. See the full walkthrough in
[knowledge/RAG_CONFIGURATION.md](../knowledge/RAG_CONFIGURATION.md).

---

## 8.4 Chart publishing (optional)

`generate_chart` always returns a PNG as MCP image content. Some clients only show that in
expanded tool output. If you want an inline **URL** as well, enable publishing to a
temporary image host:

| Variable | Default | Notes |
|----------|---------|-------|
| `APSTRA_CHART_PUBLISH_ENABLED` | `false` | Turn on URL publishing. |
| `APSTRA_CHART_PUBLISH_PROVIDER` | `catbox` | `catbox`, `postimages`, or `freeimage`. |
| `APSTRA_CHART_PUBLISH_TIMEOUT_SECONDS` | `20` | Upload HTTP timeout. |
| `APSTRA_CHART_PUBLISH_STRICT` | `false` | If `true`, upload failure is a tool error (no image-only fallback). |
| `APSTRA_CHART_PUBLISH_INSECURE_SKIP_VERIFY` | `false` | Disable TLS verify for uploads (testing only). |
| `APSTRA_CHART_CATBOX_USERHASH` | unset | Optional Catbox account hash. |
| `APSTRA_CHART_POSTIMAGES_API_URL` / `_API_KEY` | unset | Required/optional for `postimages`. |
| `APSTRA_CHART_FREEIMAGE_API_URL` | `https://freeimage.host/api/1/upload` | Freeimage endpoint. |
| `APSTRA_CHART_FREEIMAGE_API_KEY` | unset | Required for `freeimage`. |

> **Privacy note:** Publishing uploads your chart image to a third-party host. Leave it
> disabled unless you specifically need inline URLs, and never enable it for charts that
> could contain sensitive data.

---

**Next:** [9. Troubleshooting →](09-troubleshooting.md)
