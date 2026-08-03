from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from config.settings import RagConfig

logger = logging.getLogger(__name__)

_INDEX_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "index.embeddings.json"


class RagError(Exception):
    """Base class for RAG-related errors."""


class RagProviderError(RagError):
    """Raised when the embedding provider call fails."""


class RagModelMismatchError(RagError):
    """Raised when index and runtime embedding models differ."""


class RagIndexMissingError(RagError):
    """Raised when the embedding index file is unavailable."""


@dataclass(frozen=True)
class DocChunk:
    text: str
    score: float
    metadata: dict[str, Any]


@dataclass
class _LoadedIndex:
    model: str
    chunks: list[dict[str, Any]]
    model_mismatch_message: str | None = None


_loaded_index: _LoadedIndex | None = None


def _vector_from_response(data: dict[str, Any]) -> list[float] | None:
    vector = data.get("embedding")
    if isinstance(vector, list) and vector and all(isinstance(x, (int, float)) for x in vector):
        return [float(x) for x in vector]

    embeddings = data.get("embeddings")
    if isinstance(embeddings, list) and embeddings:
        first = embeddings[0]
        if isinstance(first, list):
            return [float(x) for x in first]
        if all(isinstance(x, (int, float)) for x in embeddings):
            return [float(x) for x in embeddings]

    items = data.get("data")
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict):
            embedded = first.get("embedding")
            if isinstance(embedded, list):
                return [float(x) for x in embedded]

    return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a == 0.0 or norm_b == 0.0:
        return -1.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _load_index(config: RagConfig) -> _LoadedIndex:
    global _loaded_index

    if _loaded_index is not None:
        return _loaded_index

    if not _INDEX_PATH.exists():
        raise RagIndexMissingError(
            f"Knowledge index not found at '{_INDEX_PATH}'. "
            "RAG docs retrieval is optional. Build a local index at "
            "knowledge/index.embeddings.json (for example with "
            "apstra-mcp-setup --enable-rag --build-embeddings), or disable "
            "RAG by removing rag.enabled / APSTRA_RAG_ENABLED."
        )

    with open(_INDEX_PATH, encoding="utf-8") as f:
        payload = json.load(f)

    meta = payload.get("meta") or {}
    chunks = payload.get("chunks") or []
    index_model = str(meta.get("embedding_model") or "").strip()

    mismatch_message = None
    if index_model and index_model != config.embedding_model:
        mismatch_message = (
            "Index was built with "
            f"'{index_model}' but rag.embedding_model is '{config.embedding_model}'. "
            "Update rag.embedding_model to match the index, or rebuild the index."
        )
        logger.warning("[RAG] WARNING: %s", mismatch_message)
        logger.warning("[RAG] WARNING: query_docs will return errors until this is resolved.")

    _loaded_index = _LoadedIndex(
        model=index_model,
        chunks=chunks,
        model_mismatch_message=mismatch_message,
    )
    return _loaded_index


async def _embed_query(query: str, config: RagConfig) -> list[float]:
    provider = config.embedding_provider.lower().strip()

    if provider != "ollama":
        raise RagProviderError(
            "Unsupported embedding provider for this lightweight build: "
            f"'{config.embedding_provider}'. Use 'ollama'."
        )

    payload = {
        "model": config.embedding_model,
        "input": query,
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(config.embedding_url, json=payload)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        raise RagProviderError(
            "Failed to embed query using Ollama. "
            f"endpoint='{config.embedding_url}', model='{config.embedding_model}', "
            f"error='{type(exc).__name__}: {exc}'"
        ) from exc

    vector = _vector_from_response(data)
    if vector is not None:
        return vector

    # Legacy fallback for endpoints that still require `prompt`.
    legacy_payload = {
        "model": config.embedding_model,
        "prompt": query,
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            legacy_response = await client.post(config.embedding_url, json=legacy_payload)
            legacy_response.raise_for_status()
            legacy_data = legacy_response.json()
    except Exception:
        legacy_data = {}

    legacy_vector = _vector_from_response(legacy_data)
    if legacy_vector is not None:
        return legacy_vector

    raise RagProviderError(
        "Embedding response did not include an embedding vector in supported "
        "fields (embedding, embeddings, data[0].embedding)."
    )


async def query_docs(query: str, config: RagConfig) -> list[DocChunk]:
    loaded = _load_index(config)

    if loaded.model_mismatch_message:
        raise RagModelMismatchError(loaded.model_mismatch_message)

    query_embedding = await _embed_query(query, config)

    scored: list[DocChunk] = []
    for chunk in loaded.chunks:
        text = str(chunk.get("text") or "")
        embedding = chunk.get("embedding") or []
        metadata = chunk.get("metadata") or {}

        if not isinstance(embedding, list):
            continue

        score = _cosine_similarity(
            query_embedding,
            [float(x) for x in embedding],
        )
        scored.append(
            DocChunk(
                text=text,
                score=score,
                metadata=metadata,
            )
        )

    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[: config.top_k]


def preflight_rag(config: RagConfig) -> None:
    """
    Best-effort startup validation for index presence and model pinning.

    This logs warnings but does not raise, so non-RAG server features remain
    available even when docs retrieval is misconfigured.
    """
    try:
        _load_index(config)
    except RagIndexMissingError as exc:
        logger.warning("[RAG] WARNING: %s", exc)
