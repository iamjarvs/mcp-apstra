import asyncio
import json

from config.settings import RagConfig
from primitives import rag_client
from primitives.rag_client import RagModelMismatchError


def _run(coro):
    return asyncio.run(coro)


def test_query_docs_returns_top_k_by_similarity(tmp_path, monkeypatch):
    index = {
        "meta": {
            "embedding_model": "nomic-embed-text",
        },
        "chunks": [
            {
                "text": "blueprint lifecycle",
                "embedding": [1.0, 0.0],
                "metadata": {"source": "guide", "page": 10},
            },
            {
                "text": "routing policy communities",
                "embedding": [0.9, 0.1],
                "metadata": {"source": "guide", "page": 88},
            },
            {
                "text": "evpn overlay",
                "embedding": [0.0, 1.0],
                "metadata": {"source": "api", "page": 21},
            },
        ],
    }

    index_path = tmp_path / "index.embeddings.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    monkeypatch.setattr(rag_client, "_INDEX_PATH", index_path)
    monkeypatch.setattr(rag_client, "_loaded_index", None)

    async def fake_embed(_query: str, _config: RagConfig) -> list[float]:
        return [1.0, 0.0]

    monkeypatch.setattr(rag_client, "_embed_query", fake_embed)

    cfg = RagConfig(
        enabled=True,
        embedding_provider="ollama",
        embedding_model="nomic-embed-text",
        embedding_url="http://localhost:11434/api/embeddings",
        top_k=2,
    )

    chunks = _run(rag_client.query_docs("what is a blueprint", cfg))

    assert len(chunks) == 2
    assert chunks[0].text == "blueprint lifecycle"
    assert chunks[1].text == "routing policy communities"


def test_query_docs_raises_on_model_mismatch(tmp_path, monkeypatch):
    index = {
        "meta": {
            "embedding_model": "mxbai-embed-large",
        },
        "chunks": [],
    }

    index_path = tmp_path / "index.embeddings.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    monkeypatch.setattr(rag_client, "_INDEX_PATH", index_path)
    monkeypatch.setattr(rag_client, "_loaded_index", None)

    cfg = RagConfig(
        enabled=True,
        embedding_provider="ollama",
        embedding_model="nomic-embed-text",
        embedding_url="http://localhost:11434/api/embeddings",
        top_k=5,
    )

    try:
        _run(rag_client.query_docs("blueprint", cfg))
    except RagModelMismatchError:
        return
    raise AssertionError("Expected RagModelMismatchError")


def test_vector_from_response_supports_ollama_embed_shape():
    payload = {
        "model": "qwen3-embedding",
        "embeddings": [[0.1, -0.2, 0.3]],
    }
    vector = rag_client._vector_from_response(payload)
    assert vector == [0.1, -0.2, 0.3]
