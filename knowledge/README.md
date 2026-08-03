# Knowledge index - maintainer guide

This directory stores the optional documentation embedding index used by the
query_docs MCP tool when RAG is enabled.

The index file is local-only and gitignored by default.

## Contents

- knowledge/index.embeddings.json: local output index (optional, not committed)
- knowledge/build/build_index.py: CLI index builder
- knowledge/build/build_index_ui.py: desktop drag-and-drop builder UI
- knowledge/build/chunker.py: structured chunking logic
- knowledge/build/source_pdfs/: local PDF drop location (gitignored)

## Install build dependencies

Run from repository root:

```bash
cd knowledge/build
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Option 1 (priority): desktop UI with drag and drop

Run:

```bash
cd knowledge/build
python build_index_ui.py
```

Capabilities:
- drag and drop one or more PDF files
- choose embedding provider, model, and endpoint URL
- set output JSON path anywhere on disk (not limited to default)
- build and write index in one click

Notes:
- Drag-and-drop requires tkinterdnd2 from requirements.txt.
- If tkinterdnd2 is unavailable, UI still works with Add Files.
- If your interpreter is missing Tk (`ModuleNotFoundError: _tkinter`),
  `build_index_ui.py` automatically falls back to the web UI.

### macOS/Homebrew Tk fix (optional)

If you want the native Tk desktop window, install the matching Tk package for
your Python version (example for 3.13):

```bash
brew install python-tk@3.13
```

Recreate your venv from the same Python binary after install.

## Option 1b: browser UI fallback (no Tk required)

Run:

```bash
cd knowledge/build
python build_index_web.py
```

Then open http://127.0.0.1:8765 in your browser. This UI supports drag/drop
PDFs and custom output paths.

## Option 2: CLI builder

Run:

```bash
cd knowledge/build
python build_index.py \
	--source-dir source_pdfs \
	--output ../index.embeddings.json \
	--apstra-version 6.1 \
	--provider ollama \
	--model qwen3-embedding \
	--url http://localhost:11434/api/embed
```

Provider modes:
- ollama: supports both Ollama /api/embed and /api/embeddings response shapes
- lmstudio: OpenAI-compatible embeddings endpoint (auto-appends /v1/embeddings)
- openai_compatible: generic OpenAI-compatible embeddings endpoint

For Ollama, prefer:

```text
http://localhost:11434/api/embed
```

## Structured chunking behavior

Chunking is section-first and paragraph-aware.

- heading hierarchy creates section boundaries
- prose splits at paragraph boundaries near target size
- table/code/callout blocks are atomic and never split
- oversized atomic block is kept as one chunk
- each chunk is prefixed with heading breadcrumb in embedded text
- metadata includes section_path, page, block_type, token_count

## Model pinning reminder

The runtime RAG tool checks index meta.embedding_model against configured
rag.embedding_model. If they differ, query_docs returns model mismatch errors
until aligned.
