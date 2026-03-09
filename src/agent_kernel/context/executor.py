"""Retrieval Executor for v1.0.2 flexible context retrieval.

The RetrievalExecutor validates and executes retrieval directives
against underlying stores/connectors.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.context.source_registry import SourceRegistry
from agent_kernel.core.schemas import (
    ContextItem,
    ContextRef,
    QueryRecord,
    RefType,
)
from agent_kernel.core.schemas.retrieval import (
    RetrievalDirective,
    RetrievalPlan,
)

if TYPE_CHECKING:
    from agent_kernel.memory.document_store import DocumentStore
    from agent_kernel.memory.graph_store import GraphStore
    from agent_kernel.memory.vector_store import VectorStore
    from agent_kernel.services.embedding import EmbeddingService

logger = structlog.get_logger(__name__)


class DirectiveResult:
    """Result of executing a single retrieval directive."""

    def __init__(
        self,
        directive_id: str,
        items: list[ContextItem],
        query_record: QueryRecord,
        error: str | None = None,
    ) -> None:
        self.directive_id = directive_id
        self.items = items
        self.query_record = query_record
        self.error = error

    @property
    def success(self) -> bool:
        return self.error is None


class ExecutionResult:
    """Result of executing a full retrieval plan."""

    def __init__(
        self,
        plan_id: str,
        directive_results: list[DirectiveResult],
    ) -> None:
        self.plan_id = plan_id
        self.directive_results = directive_results

    @property
    def all_items(self) -> list[ContextItem]:
        """Get all items from all directives."""
        items = []
        for result in self.directive_results:
            items.extend(result.items)
        return items

    @property
    def all_query_records(self) -> list[QueryRecord]:
        """Get all query records."""
        return [r.query_record for r in self.directive_results]

    @property
    def total_items(self) -> int:
        return len(self.all_items)

    @property
    def successful_directives(self) -> int:
        return sum(1 for r in self.directive_results if r.success)

    @property
    def failed_directives(self) -> int:
        return sum(1 for r in self.directive_results if not r.success)


class RetrievalExecutor:
    """Executes retrieval directives against underlying stores.

    The executor:
    1. Validates each directive against SourceRegistry
    2. Routes to appropriate store/connector
    3. Normalizes results to ContextItems
    4. Emits QueryRecords for observability
    """

    def __init__(
        self,
        source_registry: SourceRegistry,
        document_store: DocumentStore | None = None,
        vector_store: VectorStore | None = None,
        graph_store: GraphStore | None = None,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        """Initialize the executor.

        Args:
            source_registry: Registry for directive validation.
            document_store: Store for document retrieval.
            vector_store: Store for semantic search.
            graph_store: Store for graph queries.
            embedding_service: Service for generating query embeddings.
        """
        self._source_registry = source_registry
        self._document_store = document_store
        self._vector_store = vector_store
        self._graph_store = graph_store
        self._embedding_service = embedding_service
        logger.info("retrieval_executor_initialized")

    async def execute(self, plan: RetrievalPlan) -> ExecutionResult:
        """Execute a retrieval plan.

        Args:
            plan: The validated retrieval plan.

        Returns:
            ExecutionResult with items and query records.
        """
        directive_results: list[DirectiveResult] = []

        for directive in plan.directives:
            result = await self._execute_directive(directive)
            directive_results.append(result)

        execution_result = ExecutionResult(
            plan_id=plan.retrieval_plan_id,
            directive_results=directive_results,
        )

        logger.debug(
            "plan_executed",
            plan_id=plan.retrieval_plan_id,
            total_items=execution_result.total_items,
            successful=execution_result.successful_directives,
            failed=execution_result.failed_directives,
        )

        return execution_result

    async def _execute_directive(
        self,
        directive: RetrievalDirective,
    ) -> DirectiveResult:
        """Execute a single retrieval directive."""
        start_time = time.time()

        # Validate directive
        if not self._source_registry.has_source(directive.source_id):
            return DirectiveResult(
                directive_id=directive.directive_id,
                items=[],
                query_record=self._make_query_record(
                    directive, 0, int((time.time() - start_time) * 1000)
                ),
                error=f"Unknown source: {directive.source_id}",
            )

        try:
            # Route to appropriate handler
            if directive.source_id == "obsidian":
                items = await self._execute_obsidian_directive(directive)
            elif directive.source_id == "tasks":
                items = await self._execute_tasks_directive(directive)
            elif directive.source_id == "graph":
                items = await self._execute_graph_directive(directive)
            elif directive.source_id == "calendar":
                items = await self._execute_calendar_directive(directive)
            else:
                # Generic document store fallback
                items = await self._execute_document_directive(directive)

            duration_ms = int((time.time() - start_time) * 1000)
            return DirectiveResult(
                directive_id=directive.directive_id,
                items=items,
                query_record=self._make_query_record(directive, len(items), duration_ms),
            )

        except Exception as e:
            logger.warning(
                "directive_execution_failed",
                directive_id=directive.directive_id,
                source=directive.source_id,
                error=str(e),
            )
            duration_ms = int((time.time() - start_time) * 1000)
            return DirectiveResult(
                directive_id=directive.directive_id,
                items=[],
                query_record=self._make_query_record(directive, 0, duration_ms),
                error=str(e),
            )

    async def _execute_obsidian_directive(
        self,
        directive: RetrievalDirective,
    ) -> list[ContextItem]:
        """Execute directive against Obsidian notes.

        Uses vector store for semantic search, document store for filtering.
        """
        items: list[ContextItem] = []

        # If we have a query and vector store, do semantic search
        if directive.query and self._vector_store and self._embedding_service:
            embedding = await self._embedding_service.embed(directive.query)

            # Build filter dict from directive filters
            filters = self._build_filter_dict(directive.filters)

            results = self._vector_store.query(
                embedding,
                top_k=directive.top_k,
                filters=filters if filters else None,
                min_score=directive.min_score,
            )

            for result in results:
                metadata = result.get("metadata", {})
                item = ContextItem(
                    ref=ContextRef(
                        ref_type=RefType.NOTE,
                        ref_id=result.get("item_id", ""),
                        uri=metadata.get("path"),
                        hash=metadata.get("content_hash"),
                        metadata=metadata,
                    ),
                    excerpt=metadata.get("excerpt", ""),
                    relevance_score=result.get("score", 0.0),
                    included_reason=directive.reason or "semantic_search",
                )
                items.append(item)

        # If we have document store, can also do filtered retrieval
        elif self._document_store:
            filters = self._build_filter_dict(directive.filters)

            results = self._document_store.search(
                directive.query or "",
                limit=directive.top_k,
                filters=filters if filters else None,
            )

            for doc in results:
                item = ContextItem(
                    ref=ContextRef(
                        ref_type=RefType.NOTE,
                        ref_id=doc.get("doc_id", ""),
                        uri=doc.get("path"),
                        hash=doc.get("content_hash"),
                        metadata=doc.get("metadata", {}),
                    ),
                    excerpt=doc.get("content", "")[:500],
                    relevance_score=abs(doc.get("rank", 0.0)),
                    included_reason=directive.reason or "keyword_search",
                )
                items.append(item)

        return items

    async def _execute_tasks_directive(
        self,
        directive: RetrievalDirective,
    ) -> list[ContextItem]:
        """Execute directive against task entities.

        Tasks are stored in the graph store as Task nodes.
        """
        items: list[ContextItem] = []

        if not self._graph_store:
            return items

        # Query graph for task nodes
        # Build Cypher-style query (simplified for now)
        filters = self._build_filter_dict(directive.filters)
        node_filters = {k: v for k, v in filters.items() if "__" not in k}
        node_type = node_filters.pop("node_type", None)
        node_filters = {k: v for k, v in filters.items() if "__" not in k}

        # Use graph store to find task nodes
        task_results = self._graph_store.query(
            node_type="task",
            properties=node_filters or None,
            limit=directive.top_k,
        )

        for node in task_results:
            props = node.get("properties", {})
            item = ContextItem(
                ref=ContextRef(
                    ref_type=RefType.TASK,
                    ref_id=node.get("node_id", ""),
                    metadata=props,
                ),
                excerpt=props.get("text", ""),
                relevance_score=0.7,  # Default relevance for tasks
                included_reason=directive.reason or "task_query",
            )
            items.append(item)

        return items

    async def _execute_graph_directive(
        self,
        directive: RetrievalDirective,
    ) -> list[ContextItem]:
        """Execute directive against graph store."""
        items: list[ContextItem] = []

        if not self._graph_store:
            return items

        filters = self._build_filter_dict(directive.filters)

        # Get nodes or edges based on entity_type
        if directive.entity_type == "graph_edge":
            results = self._graph_store.get_edges(
                limit=directive.top_k,
                filters=filters,
            )
            for edge in results:
                item = ContextItem(
                    ref=ContextRef(
                        ref_type=RefType.GRAPH_EDGE,
                        ref_id=edge.get("edge_id", ""),
                        metadata=edge.get("properties", {}),
                    ),
                    excerpt=f"{edge.get('source_id')} -> {edge.get('target_id')}",
                    relevance_score=0.5,
                    included_reason=directive.reason or "graph_edge",
                )
                items.append(item)
        else:
            results = self._graph_store.query(
                node_type=node_type,
                properties=node_filters or None,
                limit=directive.top_k,
            )
            for node in results:
                props = node.get("properties", {})
                item = ContextItem(
                    ref=ContextRef(
                        ref_type=RefType.GRAPH_NODE,
                        ref_id=node.get("node_id", ""),
                        metadata=props,
                    ),
                    excerpt=props.get("title", props.get("name", "")),
                    relevance_score=0.5,
                    included_reason=directive.reason or "graph_node",
                )
                items.append(item)

        return items

    async def _execute_calendar_directive(
        self,
        directive: RetrievalDirective,
    ) -> list[ContextItem]:
        """Execute directive against calendar events.

        Calendar events are fetched on-demand (live fetch).
        For now, we check if events are in the graph store.
        """
        items: list[ContextItem] = []

        if not self._graph_store:
            return items

        filters = self._build_filter_dict(directive.filters)
        node_filters = {k: v for k, v in filters.items() if "__" not in k}

        # Look for calendar_event nodes in graph
        results = self._graph_store.query(
            node_type="calendar_event",
            properties=node_filters or None,
            limit=directive.top_k,
        )

        for node in results:
            props = node.get("properties", {})
            item = ContextItem(
                ref=ContextRef(
                    ref_type=RefType.EVENT,
                    ref_id=node.get("node_id", ""),
                    metadata=props,
                ),
                excerpt=props.get("title", ""),
                relevance_score=0.8,
                included_reason=directive.reason or "calendar_event",
            )
            items.append(item)

        return items

    async def _execute_document_directive(
        self,
        directive: RetrievalDirective,
    ) -> list[ContextItem]:
        """Generic document store execution."""
        items: list[ContextItem] = []

        if not self._document_store:
            return items

        filters = self._build_filter_dict(directive.filters)

        results = self._document_store.search(
            directive.query or "",
            limit=directive.top_k,
            filters=filters if filters else None,
        )

        for doc in results:
            item = ContextItem(
                ref=ContextRef(
                    ref_type=RefType.DOCUMENT,
                    ref_id=doc.get("doc_id", ""),
                    metadata=doc.get("metadata", {}),
                ),
                excerpt=doc.get("content", "")[:500],
                relevance_score=abs(doc.get("rank", 0.0)),
                included_reason=directive.reason or "document_search",
            )
            items.append(item)

        return items

    def _build_filter_dict(
        self,
        filters: list,
    ) -> dict[str, Any]:
        """Convert RetrievalFilters to store-compatible filter dict."""
        result = {}
        for f in filters:
            # Simple mapping - stores may need more complex handling
            if f.op == "eq":
                result[f.field] = f.value
            elif f.op == "neq":
                result[f"{f.field}__ne"] = f.value
            elif f.op == "gt":
                result[f"{f.field}__gt"] = f.value
            elif f.op == "gte":
                result[f"{f.field}__gte"] = f.value
            elif f.op == "lt":
                result[f"{f.field}__lt"] = f.value
            elif f.op == "lte":
                result[f"{f.field}__lte"] = f.value
            elif f.op == "in":
                result[f"{f.field}__in"] = f.value
            elif f.op == "contains":
                result[f"{f.field}__contains"] = f.value
            elif f.op == "prefix":
                result[f"{f.field}__prefix"] = f.value
            elif f.op == "any_in":
                result[f"{f.field}__any_in"] = f.value
        return result

    def _make_query_record(
        self,
        directive: RetrievalDirective,
        count: int,
        duration_ms: int,
    ) -> QueryRecord:
        """Create a QueryRecord for a directive."""
        query_str = directive.query or f"filters={len(directive.filters)}"
        return QueryRecord(
            source=directive.source_id,
            query=query_str[:100],  # Truncate for logging
            results_count=count,
            duration_ms=duration_ms,
        )
