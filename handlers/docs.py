from config.settings import RagConfig
from primitives.rag_client import (
    RagIndexMissingError,
    RagModelMismatchError,
    RagProviderError,
    query_docs,
)


async def handle_query_apstra_product_docs(query: str, rag_config: RagConfig | None) -> dict:
    if not rag_config or not rag_config.enabled:
        return {
            "error": "RAG is not enabled. Add rag.enabled: true in config/instances.yaml.",
            "error_type": "rag_disabled",
        }

    cleaned = (query or "").strip()
    if not cleaned:
        return {
            "error": "query is required and must be non-empty.",
            "error_type": "validation",
        }

    try:
        chunks = await query_docs(cleaned, rag_config)
    except RagModelMismatchError as exc:
        return {
            "error": str(exc),
            "error_type": "model_mismatch",
        }
    except RagProviderError as exc:
        return {
            "error": str(exc),
            "error_type": "provider_unreachable",
        }
    except RagIndexMissingError as exc:
        return {
            "error": str(exc),
            "error_type": "missing_index",
        }

    results = []
    for chunk in chunks:
        metadata = chunk.metadata or {}
        results.append(
            {
                "score": round(chunk.score, 6),
                "text": chunk.text,
                "source": metadata.get("source"),
                "apstra_version": metadata.get("apstra_version"),
                "page": metadata.get("page"),
                "section_path": metadata.get("section_path") or metadata.get("section"),
                "block_type": metadata.get("block_type"),
                "metadata": metadata,
            }
        )

    return {
        "query": cleaned,
        "top_k": rag_config.top_k,
        "result_count": len(results),
        "results": results,
    }
