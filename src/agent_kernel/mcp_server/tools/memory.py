"""Memory tools (Layer 3) - DocumentStore + VectorStore operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from mcp.server import FastMCP

    from agent_kernel.mcp_server.server import StoreBundle

logger = structlog.get_logger(__name__)


def register_memory_tools(mcp: FastMCP, stores: StoreBundle) -> None:
    """Register memory tools with the MCP server."""

    @mcp.tool(
        name="memory_search",
        description=(
            "Search the agent's document memory. Supports keyword (FTS), "
            "semantic (vector), or hybrid search modes. Returns matching "
            "documents with content excerpts and relevance scores."
        ),
    )
    def memory_search(
        query: str,
        mode: str = "keyword",
        limit: int = 10,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Search memory stores.

        Args:
            query: Search query string.
            mode: Search mode - "keyword" (FTS), "semantic", or "hybrid".
            limit: Maximum results to return.
            project_id: Optional project scope filter.
        """
        filters: dict[str, Any] | None = None
        if project_id:
            filters = {"project_id": project_id}

        results: list[dict[str, Any]] = []

        if mode in ("keyword", "hybrid"):
            doc_store = stores.document_store
            if doc_store:
                docs = doc_store.search(query, limit=limit, filters=filters)
                results.extend(
                    {
                        "doc_id": doc.get("doc_id", ""),
                        "content": doc.get("content", "")[:500],
                        "score": doc.get("rank", 0),
                        "metadata": doc.get("metadata", {}),
                        "source": "keyword",
                    }
                    for doc in docs
                )

        if mode in ("semantic", "hybrid"):
            vec_store = stores.vector_store
            if vec_store:
                # Semantic search requires embeddings - return note about this
                results.append({
                    "doc_id": "",
                    "content": (
                        "Semantic search requires an embedding service. "
                        "Use keyword mode or configure an embedding provider."
                    ),
                    "score": 0,
                    "metadata": {},
                    "source": "semantic_note",
                })

        return {"results": results[:limit], "total": len(results), "mode": mode}

    @mcp.tool(
        name="memory_store",
        description=(
            "Store a document in the agent's memory. Documents are indexed "
            "for keyword search and can include metadata for filtering."
        ),
    )
    def memory_store(
        doc_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store a document in memory.

        Args:
            doc_id: Unique document identifier.
            content: Document content (text).
            metadata: Optional metadata dict (tags, project_id, etc).
        """
        doc_store = stores.document_store
        if not doc_store:
            return {"stored": False, "error": "Document store not available"}

        doc_store.put(doc_id, content, metadata=metadata or {})
        return {"stored": True, "doc_id": doc_id}

    @mcp.tool(
        name="memory_delete",
        description="Delete a document from the agent's memory by ID.",
    )
    def memory_delete(doc_id: str) -> dict[str, Any]:
        """Delete a document from memory.

        Args:
            doc_id: Document identifier to delete.
        """
        doc_store = stores.document_store
        if not doc_store:
            return {"deleted": False, "error": "Document store not available"}

        deleted = doc_store.delete(doc_id)
        return {"deleted": deleted, "doc_id": doc_id}
