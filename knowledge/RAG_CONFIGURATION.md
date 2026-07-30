# RAG Configuration Guide

The MCP server supports optional documentation RAG (Retrieval-Augmented Generation) for the `query_apstra_product_docs` tool. You can configure it using either YAML or environment variables.

## Fast path (setup CLI)

You can configure RAG and build embeddings in one setup flow:

```bash
pip install -r knowledge/build/requirements.txt
# Add PDF files into knowledge/build/source_pdfs
apstra-mcp-setup \
  --host https://apstra.example.com \
  --username your-username \
  --enable-rag \
  --build-embeddings
```

This writes the `rag` block to `config/instances.yaml` and builds
`knowledge/index.embeddings.json`.

## Configuration Priority

Configuration is resolved in this order:
1. **YAML rag block** in instances.yaml (if present and `enabled: true`)
2. **Environment variables** (if `APSTRA_RAG_ENABLED` is set)
3. **RAG disabled** (default when both methods are absent)

## Method 1: YAML Configuration (Recommended)

Create or update `config/instances.yaml`:

```yaml
instances:
  - name: apstra-lab
    host: https://apstra.example.com
    username: your-username
    password: your-strong-password
    ssl_verify: false

# Optional RAG configuration
rag:
  enabled: true
  embedding_provider: ollama
  embedding_model: qwen3-embedding
  embedding_url: http://localhost:11434/api/embed
  top_k: 5
```

Then in your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "apstra-local": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/server.py"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "APSTRA_CONFIG_FILE": "/path/to/config/instances.yaml",
        "MCP_RESET_STORES_ON_START": "1"
      }
    }
  }
}
```

## Method 2: Environment Variables Only

Use this when you prefer not to store configuration on disk or want all config in Claude Desktop.

In your Claude Desktop config:

```json
{
  "mcpServers": {
    "apstra-local": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/server.py"],
      "env": {
        "MCP_TRANSPORT": "stdio",
        "APSTRA_HOST": "https://apstra.example.com",
        "APSTRA_USERNAME": "your-username",
        "APSTRA_PASSWORD": "your-strong-password",
        "APSTRA_RAG_ENABLED": "true",
        "APSTRA_RAG_EMBEDDING_PROVIDER": "ollama",
        "APSTRA_RAG_EMBEDDING_MODEL": "qwen3-embedding",
        "APSTRA_RAG_EMBEDDING_URL": "http://localhost:11434/api/embed",
        "APSTRA_RAG_TOP_K": "5",
        "MCP_RESET_STORES_ON_START": "1"
      }
    }
  }
}
```

## Environment Variables Reference

All required when `APSTRA_RAG_ENABLED` is set to enable RAG.

| Variable | Example | Required | Description |
|----------|---------|----------|-------------|
| `APSTRA_RAG_ENABLED` | `true` | Yes | Set to "1", "true", or "yes" to enable RAG |
| `APSTRA_RAG_EMBEDDING_PROVIDER` | `ollama` | Yes* | Provider: `ollama`, `lmstudio`, or `openai_compatible` |
| `APSTRA_RAG_EMBEDDING_MODEL` | `qwen3-embedding` | Yes* | Model name for embeddings |
| `APSTRA_RAG_EMBEDDING_URL` | `http://localhost:11434/api/embed` | Yes* | Embedding service endpoint URL |
| `APSTRA_RAG_TOP_K` | `5` | No | Number of top results to return (default: 5) |

*Required when `APSTRA_RAG_ENABLED` is enabled.

## Building the Knowledge Index

Before using RAG, you must build an embedding index from your PDF documents.

### Step 1: Run the builder UI

```bash
cd knowledge/build
python build_index_ui.py
```

Or use the web fallback:

```bash
python build_index_web.py
# Then open http://127.0.0.1:8765
```

### Step 2: Drag and drop PDFs

- Select your embedding provider and model
- Set the output location (e.g., `/path/to/knowledge/index.embeddings.json`)
- Click **Build**

### Step 3: Configure MCP server

Update `instances.yaml` or environment variables with your RAG settings (see above).

### Step 4: Restart MCP server

Restart Claude Desktop or your MCP server. You should see:

```
[INFO] RAG configuration loaded. Index has N chunks.
```

If you see a warning like:

```
[WARNING] RAG WARNING: Index was built with 'qwen3-embedding' but rag.embedding_model is 'different-model'...
```

The models don't match. Rebuild your index or update your configuration to use the same model.

## Supported Embedding Providers

### Ollama (Default)

Local, open-source embeddings. Requires Ollama running locally.

```yaml
embedding_provider: ollama
embedding_model: qwen3-embedding
embedding_url: http://localhost:11434/api/embed
```

### LM Studio

Alternative local embedding provider.

```yaml
embedding_provider: lmstudio
embedding_model: nomic-embed-text
embedding_url: http://localhost:8000/v1/embeddings
```

### OpenAI-Compatible

Any OpenAI-compatible API (Hugging Face, local servers, etc.).

```yaml
embedding_provider: openai_compatible
embedding_model: text-embedding-3-small
embedding_url: https://api.openai.com/v1/embeddings
```

## Disabling RAG

To disable RAG:

**YAML method:**
- Remove the `rag` block from instances.yaml, or
- Set `enabled: false`

**Environment variable method:**
- Remove `APSTRA_RAG_ENABLED` or set it to `false`

When RAG is disabled, the `query_apstra_product_docs` tool will not be registered.

## Troubleshooting

**Q: query_apstra_product_docs tool not appearing**
- Check MCP server logs for "RAG configuration loaded" message
- Verify all required environment variables or YAML fields are set
- Check that knowledge index exists at the expected path

**Q: Model mismatch warning**
- The knowledge index was built with a different model than currently configured
- Either rebuild the index or update configuration to match
- Use the same model for both building and querying

**Q: Connection refused errors**
- Ensure your embedding service is running (e.g., `ollama serve` for local Ollama)
- Check that the embedding URL is accessible and reachable
- Verify port numbers are correct

**Q: "Top-k must be >= 1"**
- `APSTRA_RAG_TOP_K` or `rag.top_k` must be >= 1 (typically 5-10)
