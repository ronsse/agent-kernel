"""Hybrid Search Service (v1.0.3 → v1.0.4).

Combines multiple search strategies for optimal retrieval:
1. Vector search (semantic similarity via embeddings)
2. Keyword search (FTS5 exact/fuzzy matching)
3. Graph expansion (relationship traversal)
4. Reranking (cross-encoder or scoring fusion)

The service implements a hierarchical retrieval flow:
- Stage 1: Search summary embeddings for entity-level relevance
- Stage 2: Expand via graph relationships
- Stage 3: Search chunk embeddings for passage retrieval
- Stage 4: Merge and rerank all results

v1.0.4 additions:
- Entity-based search results (source_id, entity_type, entity_id, view_type)
- Entity-based filters (allowed_sources, allowed_entity_types, view_types, scope)
- Backward-compatible with note-centric queries
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from agent_kernel.memory.document_store import DocumentStore
    from agent_kernel.memory.entity_store import EntityStore
    from agent_kernel.memory.graph_store import GraphStore
    from agent_kernel.memory.vector_store import VectorStore
    from agent_kernel.services.embedding import EmbeddingService

logger = structlog.get_logger(__name__)


class SearchStrategy(str, Enum):
    """Available search strategies."""

    VECTOR_ONLY = "vector_only"
    KEYWORD_ONLY = "keyword_only"
    GRAPH_ONLY = "graph_only"
    HYBRID = "hybrid"  # All combined
    HIERARCHICAL = "hierarchical"  # Summary -> Graph -> Chunks
    ENTITY = "entity"  # Entity-based multi-source search (v1.0.4)


@dataclass
class SearchResult:
    """A single search result.
    
    v1.0.4: Extended with entity model fields.
    """

    item_id: str
    score: float
    source: str  # "vector", "keyword", "graph"

    # v1.0.4: Entity model fields
    source_id: str = "obsidian"  # Source system (obsidian, slack, outlook, etc.)
    entity_type: str = "note"    # Entity type (note, message, email, etc.)
    entity_id: str = ""          # Stable entity ID within source
    view_type: str | None = None  # "summary", "chunk", "thread_summary", etc.

    # Legacy compatibility (deprecated in v1.0.4)
    note_id: str = ""  # Alias for entity_id when source_id=obsidian
    embedding_type: str | None = None  # Alias for view_type

    # Content fields
    text: str = ""
    path: str = ""
    title: str = ""
    uri: str = ""
    canonical_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Ensure backward compatibility with legacy fields."""
        # Sync entity_id with note_id for backward compatibility
        if not self.entity_id and self.note_id:
            self.entity_id = self.note_id
        elif self.entity_id and not self.note_id:
            self.note_id = self.entity_id

        # Sync view_type with embedding_type for backward compatibility
        if not self.view_type and self.embedding_type:
            self.view_type = self.embedding_type
        elif self.view_type and not self.embedding_type:
            self.embedding_type = self.view_type


@dataclass
class HybridSearchResult:
    """Combined results from hybrid search."""

    results: list[SearchResult] = field(default_factory=list)
    strategy: SearchStrategy = SearchStrategy.HYBRID
    duration_ms: int = 0
    vector_count: int = 0
    keyword_count: int = 0
    graph_count: int = 0
    reranked: bool = False

    # v1.0.4: Entity-level stats
    sources_searched: list[str] = field(default_factory=list)
    entity_types_found: list[str] = field(default_factory=list)


@dataclass
class HybridSearchConfig:
    """Configuration for hybrid search.
    
    v1.0.4: Extended with entity-based filters.
    """

    # Strategy
    strategy: SearchStrategy = SearchStrategy.HIERARCHICAL

    # Limits
    max_results: int = 20
    summary_limit: int = 10  # Top-N summaries in stage 1
    chunk_limit: int = 5  # Chunks per relevant entity in stage 3
    graph_depth: int = 1  # Hops for graph expansion

    # Weights for score fusion
    vector_weight: float = 0.6
    keyword_weight: float = 0.3
    graph_weight: float = 0.1

    # Legacy filters (note-centric, deprecated in v1.0.4)
    embedding_type_filter: str | None = None  # "summary" or "chunk"
    note_ids_filter: list[str] | None = None

    # v1.0.4: Entity-based filters
    allowed_sources: list[str] | None = None  # ["obsidian", "slack", "outlook"]
    allowed_entity_types: list[str] | None = None  # ["note", "message", "email"]
    view_types: list[str] | None = None  # ["summary", "chunk"]
    entity_ids_filter: list[str] | None = None  # Filter to specific entities
    scope: dict[str, Any] | None = None  # project_id, time_range, path_globs, etc.

    # Reranking
    enable_reranking: bool = False
    rerank_model: str | None = None

    def get_vector_filters(self) -> dict[str, Any] | None:
        """Build vector store filters from config."""
        filters: dict[str, Any] = {}

        # Legacy embedding_type filter
        if self.embedding_type_filter:
            filters["embedding_type"] = self.embedding_type_filter

        # v1.0.4: Entity-based filters
        if self.allowed_sources and len(self.allowed_sources) == 1:
            filters["source_id"] = self.allowed_sources[0]
        if self.allowed_entity_types and len(self.allowed_entity_types) == 1:
            filters["entity_type"] = self.allowed_entity_types[0]
        if self.view_types and len(self.view_types) == 1:
            filters["view_type"] = self.view_types[0]

        return filters if filters else None


class HybridSearchService:
    """Service for hybrid search across vector, keyword, and graph stores.

    Implements the hierarchical retrieval pattern:
    1. Summary search → Find relevant entities (notes, messages, etc.)
    2. Graph expansion → Get related entities
    3. Chunk search → Get specific passages
    4. Merge & rerank → Combine and score

    v1.0.4: Extended to support entity-based search across multiple sources.
    """

    def __init__(
        self,
        document_store: DocumentStore | None = None,
        vector_store: VectorStore | None = None,
        graph_store: GraphStore | None = None,
        embedding_service: EmbeddingService | None = None,
        entity_store: EntityStore | None = None,  # v1.0.4
    ) -> None:
        """Initialize the hybrid search service."""
        self._document_store = document_store
        self._vector_store = vector_store
        self._graph_store = graph_store
        self._embedding_service = embedding_service
        self._entity_store = entity_store  # v1.0.4

        logger.info(
            "hybrid_search_service_initialized",
            has_document_store=document_store is not None,
            has_vector_store=vector_store is not None,
            has_graph_store=graph_store is not None,
            has_embedding_service=embedding_service is not None,
            has_entity_store=entity_store is not None,
        )

    async def search(
        self,
        query: str,
        config: HybridSearchConfig | None = None,
    ) -> HybridSearchResult:
        """Execute hybrid search.

        Args:
            query: The search query.
            config: Search configuration.

        Returns:
            Combined search results.
        """
        config = config or HybridSearchConfig()
        start_time = time.time()

        if config.strategy == SearchStrategy.HIERARCHICAL:
            result = await self._hierarchical_search(query, config)
        elif config.strategy == SearchStrategy.HYBRID:
            result = await self._hybrid_search(query, config)
        elif config.strategy == SearchStrategy.VECTOR_ONLY:
            result = await self._vector_search(query, config)
        elif config.strategy == SearchStrategy.KEYWORD_ONLY:
            result = await self._keyword_search(query, config)
        elif config.strategy == SearchStrategy.GRAPH_ONLY:
            result = await self._graph_search(query, config)
        elif config.strategy == SearchStrategy.ENTITY:
            result = await self._entity_search(query, config)
        else:
            result = await self._hybrid_search(query, config)

        result.duration_ms = int((time.time() - start_time) * 1000)
        result.strategy = config.strategy

        # v1.0.4: Track entity types and sources found
        sources_seen: set[str] = set()
        entity_types_seen: set[str] = set()
        for r in result.results:
            sources_seen.add(r.source_id)
            entity_types_seen.add(r.entity_type)
        result.sources_searched = list(sources_seen)
        result.entity_types_found = list(entity_types_seen)

        logger.info(
            "hybrid_search_completed",
            query_preview=query[:50],
            strategy=config.strategy.value,
            result_count=len(result.results),
            duration_ms=result.duration_ms,
            sources=result.sources_searched,
            entity_types=result.entity_types_found,
        )

        return result

    async def _hierarchical_search(
        self,
        query: str,
        config: HybridSearchConfig,
    ) -> HybridSearchResult:
        """Hierarchical search: Summary → Graph → Chunks.

        This is the recommended strategy for knowledge graph search.
        v1.0.4: Updated to use entity-based fields while maintaining backward compatibility.
        """
        all_results: list[SearchResult] = []
        vector_count = 0
        keyword_count = 0
        graph_count = 0

        # Stage 1: Search summary embeddings for entity-level relevance
        if self._vector_store and self._embedding_service:
            query_embedding = await self._embedding_service.embed(query)

            # Build filters for summary search
            summary_filters: dict[str, Any] = {"embedding_type": "summary"}
            # v1.0.4: Add entity-based filters if specified
            if config.allowed_sources and len(config.allowed_sources) == 1:
                summary_filters["source_id"] = config.allowed_sources[0]
            if config.allowed_entity_types and len(config.allowed_entity_types) == 1:
                summary_filters["entity_type"] = config.allowed_entity_types[0]

            summary_results = self._vector_store.query(
                query_embedding,
                top_k=config.summary_limit,
                filters=summary_filters,
            )

            relevant_entity_ids: set[str] = set()
            for result in summary_results:
                metadata = result.get("metadata", {})
                # v1.0.4: Use entity fields with fallback to legacy fields
                entity_id = metadata.get("entity_id") or metadata.get("note_id", "")
                source_id = metadata.get("source_id", "obsidian")
                entity_type = metadata.get("entity_type", "note")
                view_type = metadata.get("view_type") or metadata.get("embedding_type", "summary")

                relevant_entity_ids.add(entity_id)

                search_result = SearchResult(
                    item_id=result["item_id"],
                    score=result.get("score", 0.0),
                    source="vector",
                    source_id=source_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    view_type=view_type,
                    note_id=entity_id,  # Legacy compatibility
                    embedding_type=view_type,  # Legacy compatibility
                    text=metadata.get("text", ""),
                    path=metadata.get("path", ""),
                    title=metadata.get("title", ""),
                    uri=metadata.get("uri", ""),
                    canonical_id=metadata.get("canonical_id"),
                    metadata=metadata,
                )
                all_results.append(search_result)
                vector_count += 1

            logger.debug(
                "hierarchical_stage1_summaries",
                summary_count=len(summary_results),
                relevant_entities=len(relevant_entity_ids),
            )

            # Stage 2: Expand via graph relationships
            if self._graph_store and relevant_entity_ids:
                seed_node_ids = [f"note:{eid}" for eid in relevant_entity_ids]
                subgraph = self._graph_store.get_subgraph(
                    seed_node_ids,
                    depth=config.graph_depth,
                )

                # Add related entities from graph
                for node in subgraph.get("nodes", []):
                    node_id = node.get("node_id", "")
                    if node_id.startswith("note:"):
                        entity_id = node_id.replace("note:", "")
                        if entity_id not in relevant_entity_ids:
                            relevant_entity_ids.add(entity_id)
                            props = node.get("properties", {})
                            search_result = SearchResult(
                                item_id=node_id,
                                score=0.5,  # Lower score for graph-expanded
                                source="graph",
                                source_id=props.get("source_id", "obsidian"),
                                entity_type=props.get("entity_type", "note"),
                                entity_id=entity_id,
                                note_id=entity_id,
                                text=props.get("title", ""),
                                path=props.get("path", ""),
                                title=props.get("title", ""),
                                uri=props.get("uri", ""),
                                metadata=props,
                            )
                            all_results.append(search_result)
                            graph_count += 1

                logger.debug(
                    "hierarchical_stage2_graph",
                    expanded_entities=graph_count,
                )

            # Stage 3: Search chunk embeddings for relevant entities
            if relevant_entity_ids:
                for entity_id in list(relevant_entity_ids)[:config.summary_limit]:
                    chunk_filters: dict[str, Any] = {
                        "embedding_type": "chunk",
                        "note_id": entity_id,
                    }
                    chunk_results = self._vector_store.query(
                        query_embedding,
                        top_k=config.chunk_limit,
                        filters=chunk_filters,
                    )

                    for result in chunk_results:
                        metadata = result.get("metadata", {})
                        result_entity_id = metadata.get("entity_id") or metadata.get("note_id", "")
                        search_result = SearchResult(
                            item_id=result["item_id"],
                            score=result.get("score", 0.0),
                            source="vector",
                            source_id=metadata.get("source_id", "obsidian"),
                            entity_type=metadata.get("entity_type", "note"),
                            entity_id=result_entity_id,
                            view_type="chunk",
                            note_id=result_entity_id,
                            embedding_type="chunk",
                            text=metadata.get("text", ""),
                            path=metadata.get("path", ""),
                            title=metadata.get("title", ""),
                            uri=metadata.get("uri", ""),
                            canonical_id=metadata.get("canonical_id"),
                            metadata=metadata,
                        )
                        all_results.append(search_result)
                        vector_count += 1

                logger.debug(
                    "hierarchical_stage3_chunks",
                    chunk_count=vector_count - len(summary_results),
                )

        # Stage 4: Add keyword search results
        if self._document_store:
            keyword_results = self._document_store.search(
                query,
                limit=config.max_results,
            )

            for doc in keyword_results:
                doc_id = doc.get("doc_id", "")
                # Parse entity info from doc_id (format: source:entity_id)
                if ":" in doc_id:
                    source_id, entity_id = doc_id.split(":", 1)
                else:
                    source_id = "obsidian"
                    entity_id = doc_id
                metadata = doc.get("metadata", {})

                search_result = SearchResult(
                    item_id=doc_id,
                    score=0.7,  # Default score for keyword match
                    source="keyword",
                    source_id=source_id,
                    entity_type=metadata.get("entity_type", "note"),
                    entity_id=entity_id,
                    note_id=entity_id,
                    text=doc.get("content", "")[:200],
                    path=metadata.get("path", ""),
                    title=metadata.get("title", ""),
                    uri=metadata.get("uri", ""),
                    metadata=metadata,
                )
                all_results.append(search_result)
                keyword_count += 1

        # Deduplicate and sort by score
        seen: set[str] = set()
        unique_results: list[SearchResult] = []
        for result in sorted(all_results, key=lambda r: r.score, reverse=True):
            # Deduplicate by entity_id for summary/graph results
            if result.view_type == "chunk":
                key = result.item_id  # Keep all chunks
            else:
                key = f"{result.source_id}:{result.entity_id}"

            if key not in seen:
                seen.add(key)
                unique_results.append(result)

        # Apply final limit
        final_results = unique_results[:config.max_results]

        return HybridSearchResult(
            results=final_results,
            vector_count=vector_count,
            keyword_count=keyword_count,
            graph_count=graph_count,
        )

    async def _entity_search(
        self,
        query: str,
        config: HybridSearchConfig,
    ) -> HybridSearchResult:
        """Entity-based multi-source search (v1.0.4).
        
        Searches across multiple sources and entity types using the entity model.
        This is the recommended strategy for cross-source retrieval.
        """
        all_results: list[SearchResult] = []
        vector_count = 0

        if not self._vector_store or not self._embedding_service:
            logger.warning("entity_search_missing_dependencies")
            return HybridSearchResult(results=[])

        query_embedding = await self._embedding_service.embed(query)

        # Build entity-based filters
        filters = config.get_vector_filters()

        # Stage 1: Search entity summaries
        if config.view_types is None or "summary" in config.view_types:
            summary_filters = dict(filters) if filters else {}
            summary_filters["view_type"] = "summary"

            summary_results = self._vector_store.query(
                query_embedding,
                top_k=config.summary_limit,
                filters=summary_filters,
            )

            for result in summary_results:
                metadata = result.get("metadata", {})
                search_result = SearchResult(
                    item_id=result["item_id"],
                    score=result.get("score", 0.0),
                    source="vector",
                    source_id=metadata.get("source_id", "unknown"),
                    entity_type=metadata.get("entity_type", "unknown"),
                    entity_id=metadata.get("entity_id", ""),
                    view_type="summary",
                    canonical_id=metadata.get("canonical_id"),
                    text=metadata.get("text", ""),
                    uri=metadata.get("uri", ""),
                    title=metadata.get("title", ""),
                    path=metadata.get("path", ""),
                    metadata=metadata,
                )
                all_results.append(search_result)
                vector_count += 1

        # Stage 2: Search entity chunks if needed
        if config.view_types is None or "chunk" in config.view_types:
            chunk_filters = dict(filters) if filters else {}
            chunk_filters["view_type"] = "chunk"

            chunk_results = self._vector_store.query(
                query_embedding,
                top_k=config.chunk_limit * config.summary_limit,
                filters=chunk_filters,
            )

            for result in chunk_results:
                metadata = result.get("metadata", {})
                search_result = SearchResult(
                    item_id=result["item_id"],
                    score=result.get("score", 0.0),
                    source="vector",
                    source_id=metadata.get("source_id", "unknown"),
                    entity_type=metadata.get("entity_type", "unknown"),
                    entity_id=metadata.get("entity_id", ""),
                    view_type="chunk",
                    canonical_id=metadata.get("canonical_id"),
                    text=metadata.get("text", ""),
                    uri=metadata.get("uri", ""),
                    title=metadata.get("title", ""),
                    path=metadata.get("path", ""),
                    metadata=metadata,
                )
                all_results.append(search_result)
                vector_count += 1

        # Sort and limit
        all_results.sort(key=lambda r: r.score, reverse=True)
        final_results = all_results[:config.max_results]

        return HybridSearchResult(
            results=final_results,
            vector_count=vector_count,
        )

    async def _hybrid_search(
        self,
        query: str,
        config: HybridSearchConfig,
    ) -> HybridSearchResult:
        """Simple hybrid search: All sources in parallel, score fusion."""
        all_results: list[SearchResult] = []
        vector_count = 0
        keyword_count = 0

        # Vector search
        if self._vector_store and self._embedding_service:
            query_embedding = await self._embedding_service.embed(query)
            vector_results = self._vector_store.query(
                query_embedding,
                top_k=config.max_results,
                filters={"embedding_type": config.embedding_type_filter}
                if config.embedding_type_filter
                else None,
            )

            for result in vector_results:
                metadata = result.get("metadata", {})
                search_result = SearchResult(
                    item_id=result["item_id"],
                    note_id=metadata.get("note_id", ""),
                    score=result.get("score", 0.0) * config.vector_weight,
                    source="vector",
                    embedding_type=metadata.get("embedding_type"),
                    text=metadata.get("text", ""),
                    path=metadata.get("path", ""),
                    title=metadata.get("title", ""),
                    metadata=metadata,
                )
                all_results.append(search_result)
                vector_count += 1

        # Keyword search
        if self._document_store:
            keyword_results = self._document_store.search(
                query,
                limit=config.max_results,
            )

            for doc in keyword_results:
                doc_id = doc.get("doc_id", "")
                note_id = doc_id.replace("obsidian:", "")
                metadata = doc.get("metadata", {})

                search_result = SearchResult(
                    item_id=doc_id,
                    note_id=note_id,
                    score=config.keyword_weight,  # Flat score for keyword
                    source="keyword",
                    text=doc.get("content", "")[:200],
                    path=metadata.get("path", ""),
                    title=metadata.get("title", ""),
                    metadata=metadata,
                )
                all_results.append(search_result)
                keyword_count += 1

        # Score fusion: boost items that appear in multiple sources
        note_scores: dict[str, float] = {}
        for result in all_results:
            if result.note_id in note_scores:
                note_scores[result.note_id] += result.score
            else:
                note_scores[result.note_id] = result.score

        # Update scores with fusion bonus
        for result in all_results:
            result.score = note_scores.get(result.note_id, result.score)

        # Deduplicate and sort
        seen: set[str] = set()
        unique_results: list[SearchResult] = []
        for result in sorted(all_results, key=lambda r: r.score, reverse=True):
            if result.note_id not in seen:
                seen.add(result.note_id)
                unique_results.append(result)

        return HybridSearchResult(
            results=unique_results[:config.max_results],
            vector_count=vector_count,
            keyword_count=keyword_count,
            graph_count=0,
        )

    async def _vector_search(
        self,
        query: str,
        config: HybridSearchConfig,
    ) -> HybridSearchResult:
        """Vector-only search."""
        results: list[SearchResult] = []

        if self._vector_store and self._embedding_service:
            query_embedding = await self._embedding_service.embed(query)
            filters = config.get_vector_filters()
            vector_results = self._vector_store.query(
                query_embedding,
                top_k=config.max_results,
                filters=filters,
            )

            for result in vector_results:
                metadata = result.get("metadata", {})
                entity_id = metadata.get("entity_id") or metadata.get("note_id", "")
                view_type = metadata.get("view_type") or metadata.get("embedding_type")
                search_result = SearchResult(
                    item_id=result["item_id"],
                    score=result.get("score", 0.0),
                    source="vector",
                    source_id=metadata.get("source_id", "obsidian"),
                    entity_type=metadata.get("entity_type", "note"),
                    entity_id=entity_id,
                    view_type=view_type,
                    note_id=entity_id,
                    embedding_type=view_type,
                    text=metadata.get("text", ""),
                    path=metadata.get("path", ""),
                    title=metadata.get("title", ""),
                    uri=metadata.get("uri", ""),
                    canonical_id=metadata.get("canonical_id"),
                    metadata=metadata,
                )
                results.append(search_result)

        return HybridSearchResult(
            results=results,
            vector_count=len(results),
        )

    async def _keyword_search(
        self,
        query: str,
        config: HybridSearchConfig,
    ) -> HybridSearchResult:
        """Keyword-only search (FTS5)."""
        results: list[SearchResult] = []

        if self._document_store:
            keyword_results = self._document_store.search(
                query,
                limit=config.max_results,
            )

            for doc in keyword_results:
                doc_id = doc.get("doc_id", "")
                note_id = doc_id.replace("obsidian:", "")
                metadata = doc.get("metadata", {})

                search_result = SearchResult(
                    item_id=doc_id,
                    note_id=note_id,
                    score=1.0,
                    source="keyword",
                    text=doc.get("content", "")[:200],
                    path=metadata.get("path", ""),
                    title=metadata.get("title", ""),
                    metadata=metadata,
                )
                results.append(search_result)

        return HybridSearchResult(
            results=results,
            keyword_count=len(results),
        )

    async def _graph_search(
        self,
        query: str,
        config: HybridSearchConfig,
    ) -> HybridSearchResult:
        """Graph-only search (find by relationships)."""
        results: list[SearchResult] = []

        # For graph search, we need seed nodes
        # Use keyword search to find initial seeds
        seed_note_ids: list[str] = []
        if self._document_store:
            keyword_results = self._document_store.search(query, limit=5)
            for doc in keyword_results:
                doc_id = doc.get("doc_id", "")
                note_id = doc_id.replace("obsidian:", "")
                seed_note_ids.append(note_id)

        if self._graph_store and seed_note_ids:
            seed_node_ids = [f"note:{nid}" for nid in seed_note_ids]
            subgraph = self._graph_store.get_subgraph(
                seed_node_ids,
                depth=config.graph_depth,
            )

            for node in subgraph.get("nodes", []):
                node_id = node.get("node_id", "")
                if node_id.startswith("note:"):
                    note_id = node_id.replace("note:", "")
                    props = node.get("properties", {})
                    search_result = SearchResult(
                        item_id=node_id,
                        note_id=note_id,
                        score=1.0 if note_id in seed_note_ids else 0.5,
                        source="graph",
                        text=props.get("title", ""),
                        path=props.get("path", ""),
                        title=props.get("title", ""),
                        metadata=props,
                    )
                    results.append(search_result)

        return HybridSearchResult(
            results=results,
            graph_count=len(results),
        )

    async def search_similar_notes(
        self,
        note_id: str,
        limit: int = 10,
    ) -> HybridSearchResult:
        """Find notes similar to a given note.

        Uses the note's summary embedding for similarity search.
        """
        results: list[SearchResult] = []

        if self._vector_store:
            # Get the note's summary embedding
            summary_id = f"{note_id}:summary"
            vectors = self._vector_store.list_vectors(
                prefix=summary_id,
                limit=1,
            )

            if vectors:
                # Query similar summaries
                note_vector = vectors[0].get("vector")
                if note_vector:
                    similar = self._vector_store.query(
                        note_vector,
                        top_k=limit + 1,  # +1 to exclude self
                        filters={"embedding_type": "summary"},
                    )

                    for result in similar:
                        metadata = result.get("metadata", {})
                        result_note_id = metadata.get("note_id", "")
                        if result_note_id != note_id:  # Exclude self
                            search_result = SearchResult(
                                item_id=result["item_id"],
                                note_id=result_note_id,
                                score=result.get("score", 0.0),
                                source="vector",
                                embedding_type="summary",
                                text=metadata.get("text", ""),
                                path=metadata.get("path", ""),
                                title=metadata.get("title", ""),
                                metadata=metadata,
                            )
                            results.append(search_result)

        return HybridSearchResult(
            results=results[:limit],
            vector_count=len(results),
        )
