"""Multi-source ingestion orchestrator for the context graph.

Accepts knowledge from multiple sources and routes to the appropriate
graph creation logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.context_graph.decomposer import TraceDecomposer
from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.core.schemas.knowledge import (
    DecompositionResult,
    FreshnessScore,
    InsightProperties,
    KnowledgeNodeProperties,
    KnowledgeSource,
    KnowledgeTier,
)
from agent_kernel.core.schemas.trace import DecisionTrace

if TYPE_CHECKING:
    from agent_kernel.context_graph.types import TypeRegistry
    from agent_kernel.core.schemas.experience import LessonLearned, Playbook
    from agent_kernel.memory.event_log import EventLog
    from agent_kernel.memory.graph_store import GraphStore

logger = structlog.get_logger(__name__)


class ContextGraphIngestion:
    """Orchestrates ingestion into the context graph from multiple sources.

    Sources:
    - Traces → decomposed into TRAJECTORY + DECISION_EVENT nodes
    - Manual knowledge → DOMAIN, SYSTEM, CONCEPT, etc. nodes
    - Lessons → INSIGHT nodes
    - Playbooks → PRACTICE nodes
    """

    def __init__(
        self,
        graph_store: GraphStore,
        event_log: EventLog | None = None,
        type_registry: TypeRegistry | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._event_log = event_log
        self._type_registry = type_registry
        self._decomposer = TraceDecomposer(
            graph_store=graph_store,
            event_log=event_log,
            type_registry=type_registry,
        )

    async def ingest_trace(self, trace: DecisionTrace) -> DecompositionResult:
        """Decompose trace into graph structure (event clock).

        This is the PRIMARY ingestion path. Every completed trace
        creates a TRAJECTORY node linked to entities it touched
        and decisions it made.
        """
        return await self._decomposer.decompose(trace)

    async def ingest_manual(
        self,
        node_type: str,
        properties: dict[str, Any],
        edges: list[dict[str, Any]] | None = None,
    ) -> str:
        """Create a manual knowledge node (semantic memory).

        Args:
            node_type: Type of node to create (e.g., "domain", "concept").
            properties: Node properties (should conform to KnowledgeNodeProperties).
            edges: Optional edges to create.
                   Each dict should have: target_id, edge_type, properties (optional).

        Returns:
            The created node ID.
        """
        node_id = f"{node_type}:{generate_ulid()}"

        # Ensure freshness score exists in properties
        if "freshness" not in properties:
            now = utc_now()
            properties["freshness"] = FreshnessScore(
                last_accessed_at=now,
                last_reinforced_at=now,
            ).model_dump(mode="json")

        # Ensure knowledge_source is set
        if "knowledge_source" not in properties:
            properties["knowledge_source"] = KnowledgeSource.MANUAL.value

        # Ensure tier is set
        if "tier" not in properties:
            properties["tier"] = KnowledgeTier.HOT.value

        self._graph_store.upsert_node(
            node_id=node_id,
            node_type=node_type,
            properties=properties,
        )

        # Create edges if provided
        if edges:
            for edge_spec in edges:
                self._graph_store.upsert_edge(
                    source_id=node_id,
                    target_id=edge_spec["target_id"],
                    edge_type=edge_spec["edge_type"],
                    properties=edge_spec.get("properties", {}),
                )

        # Record type usage
        if self._type_registry:
            await self._type_registry.record_type_usage(
                node_type, "node", properties,
            )

        # Emit event
        if self._event_log:
            from agent_kernel.memory.event_log import EventType

            self._event_log.emit(
                event_type=EventType.KNOWLEDGE_CREATED,
                source="context_graph.ingestion",
                entity_id=node_id,
                entity_type=node_type,
                payload={
                    "title": properties.get("title", ""),
                    "knowledge_source": properties.get("knowledge_source", "manual"),
                },
            )

        logger.info(
            "knowledge_node_created",
            node_id=node_id,
            node_type=node_type,
            title=properties.get("title", ""),
        )

        return node_id

    async def ingest_lesson(self, lesson: LessonLearned) -> str:
        """Sync a LessonLearned to an INSIGHT node in the graph.

        Args:
            lesson: The lesson to sync.

        Returns:
            The created/updated INSIGHT node ID.
        """
        node_id = f"insight:{lesson.lesson_id}"

        now = utc_now()
        props = InsightProperties(
            title=lesson.title,
            description=lesson.lesson_text,
            knowledge_source=KnowledgeSource.TRACE,
            source_refs=lesson.source_trace_ids,
            confidence=lesson.confidence,
            freshness=FreshnessScore(
                last_accessed_at=now,
                last_reinforced_at=now,
            ),
            tier=KnowledgeTier.HOT,
            tags=[],
            created_by=None,
            insight_type="lesson",
            applicable_contexts=(
                [lesson.scope.workflow_id]
                if lesson.scope and lesson.scope.workflow_id
                else []
            ),
        )

        self._graph_store.upsert_node(
            node_id=node_id,
            node_type=NodeType.INSIGHT.value,
            properties=props.model_dump(mode="json"),
        )

        # Link insight to source traces
        for trace_id in lesson.source_trace_ids:
            trajectory_id = f"trajectory:{trace_id}"
            existing = self._graph_store.get_node(trajectory_id)
            if existing:
                self._graph_store.upsert_edge(
                    source_id=node_id,
                    target_id=trajectory_id,
                    edge_type=EdgeType.INSIGHT_DERIVED_FROM.value,
                    properties={},
                )

        logger.info(
            "lesson_synced_to_insight",
            node_id=node_id,
            lesson_id=lesson.lesson_id,
            title=lesson.title,
        )

        return node_id

    async def ingest_playbook(self, playbook: Playbook) -> str:
        """Sync a Playbook to a PRACTICE node in the graph.

        Args:
            playbook: The playbook to sync.

        Returns:
            The created/updated PRACTICE node ID.
        """
        node_id = f"practice:{playbook.playbook_id}"

        now = utc_now()
        props = KnowledgeNodeProperties(
            title=playbook.name,
            description=playbook.description,
            knowledge_source=KnowledgeSource.TRACE,
            source_refs=playbook.derived_from_lessons,
            confidence=1.0,
            freshness=FreshnessScore(
                last_accessed_at=now,
                last_reinforced_at=now,
            ),
            tier=KnowledgeTier.HOT,
            tags=[],
            created_by=None,
        )

        self._graph_store.upsert_node(
            node_id=node_id,
            node_type=NodeType.PRACTICE.value,
            properties=props.model_dump(mode="json"),
        )

        # Link practice to derived lessons
        for lesson_id in playbook.derived_from_lessons:
            insight_id = f"insight:{lesson_id}"
            existing = self._graph_store.get_node(insight_id)
            if existing:
                self._graph_store.upsert_edge(
                    source_id=node_id,
                    target_id=insight_id,
                    edge_type=EdgeType.INSIGHT_ABOUT.value,
                    properties={},
                )

        logger.info(
            "playbook_synced_to_practice",
            node_id=node_id,
            playbook_id=playbook.playbook_id,
            name=playbook.name,
        )

        return node_id
