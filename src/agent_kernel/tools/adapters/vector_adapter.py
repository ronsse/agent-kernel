"""Vector adapter - provides semantic search tools.

These functions are registered as capabilities for agents to call
for ad-hoc semantic searches beyond automatic context assembly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from agent_kernel.memory.vector_store import VectorStore
    from agent_kernel.services.embedding import EmbeddingService

logger = structlog.get_logger(__name__)

# Global store references (set during initialization)
_vector_store: VectorStore | None = None
_embedding_service: EmbeddingService | None = None


def set_vector_store(store: VectorStore) -> None:
    """Set the global vector store for adapter functions.

    Args:
        store: The vector store instance to use.
    """
    global _vector_store
    _vector_store = store
    logger.info("vector_adapter_store_set")


def set_embedding_service(service: EmbeddingService) -> None:
    """Set the embedding service for text-to-vector conversion.

    Args:
        service: The embedding service instance to use.
    """
    global _embedding_service
    _embedding_service = service
    logger.info("vector_adapter_embedding_service_set")


async def vector_search_async(
    query: str | None = None,
    embedding: list[float] | None = None,
    top_k: int = 10,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Search for semantically similar items (async version).

    Args:
        query: Text query to search for (will be embedded).
        embedding: Pre-computed embedding vector.
        top_k: Number of results to return.
        filters: Metadata filters to apply.

    Returns:
        Dict with results list.
    """
    if _vector_store is None:
        return {"error": "Vector store not initialized", "results": []}

    # Get embedding from query if not provided
    search_embedding = embedding
    if search_embedding is None and query is not None:
        if _embedding_service is None:
            return {"error": "Embedding service not initialized", "results": []}
        try:
            search_embedding = await _embedding_service.embed(query)
        except Exception as e:
            logger.error("embedding_failed", error=str(e))
            return {"error": f"Failed to embed query: {e}", "results": []}

    if search_embedding is None:
        return {"error": "Either query or embedding must be provided", "results": []}

    try:
        results = _vector_store.query(
            vector=search_embedding,
            top_k=top_k,
            filters=filters,
        )
        logger.debug(
            "vector_search_executed",
            query_preview=query[:50] if query else "embedding",
            top_k=top_k,
            results_count=len(results),
        )
        return {"results": results, "count": len(results)}
    except Exception as e:
        logger.error("vector_search_failed", error=str(e))
        return {"error": str(e), "results": []}


def vector_search(
    query: str | None = None,
    embedding: list[float] | None = None,
    top_k: int = 10,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Search for semantically similar items (sync wrapper).

    For async context, use vector_search_async directly.

    Args:
        query: Text query to search for (will be embedded).
        embedding: Pre-computed embedding vector.
        top_k: Number of results to return.
        filters: Metadata filters to apply.

    Returns:
        Dict with results list.
    """
    import asyncio

    # If embedding is provided, we can do sync search
    if embedding is not None:
        if _vector_store is None:
            return {"error": "Vector store not initialized", "results": []}

        try:
            results = _vector_store.query(
                vector=embedding,
                top_k=top_k,
                filters=filters,
            )
            return {"results": results, "count": len(results)}
        except Exception as e:
            logger.error("vector_search_failed", error=str(e))
            return {"error": str(e), "results": []}

    # For query-based search, need to run async
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Create a new task in the running loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    vector_search_async(query, embedding, top_k, filters),
                )
                return future.result()
        else:
            return loop.run_until_complete(
                vector_search_async(query, embedding, top_k, filters)
            )
    except Exception as e:
        logger.error("vector_search_sync_wrapper_failed", error=str(e))
        return {"error": str(e), "results": []}
