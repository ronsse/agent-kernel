"""Experience Bridge - one-way sync from ExperienceStore to Context Graph.

Lessons become INSIGHT nodes. Playbooks become PRACTICE nodes.
Recurring case patterns become PATTERN nodes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.context_graph.ingestion import ContextGraphIngestion
from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.core.schemas.knowledge import (
    FreshnessScore,
    KnowledgeSource,
    KnowledgeTier,
    PatternProperties,
)

if TYPE_CHECKING:
    from agent_kernel.core.schemas.experience import LessonLearned, Playbook
    from agent_kernel.memory.event_log import EventLog
    from agent_kernel.memory.experience_store import ExperienceStore
    from agent_kernel.memory.graph_store import GraphStore

logger = structlog.get_logger(__name__)


class ExperienceBridge:
    """One-way sync: ExperienceStore -> Context Graph.

    Lessons become INSIGHT nodes. Playbooks become PRACTICE nodes.
    Recurring case patterns become PATTERN nodes.
    """

    def __init__(
        self,
        graph_store: GraphStore,
        experience_store: ExperienceStore | None = None,
        event_log: EventLog | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._experience_store = experience_store
        self._event_log = event_log
        self._ingestion = ContextGraphIngestion(
            graph_store=graph_store,
            event_log=event_log,
        )

    async def sync_lesson(self, lesson: LessonLearned) -> str:
        """Sync a lesson to an INSIGHT node.

        Args:
            lesson: The lesson to sync.

        Returns:
            The INSIGHT node ID.
        """
        return await self._ingestion.ingest_lesson(lesson)

    async def sync_playbook(self, playbook: Playbook) -> str:
        """Sync a playbook to a PRACTICE node.

        Args:
            playbook: The playbook to sync.

        Returns:
            The PRACTICE node ID.
        """
        return await self._ingestion.ingest_playbook(playbook)

    async def detect_patterns(
        self,
        min_occurrences: int = 3,
    ) -> list[str]:
        """Detect recurring patterns from trajectory co-occurrences.

        Looks at CO_OCCURS_WITH edges with weight >= min_occurrences
        and creates PATTERN nodes for significant co-occurrence clusters.

        Uses batch operations to avoid N+1 query problems.

        Args:
            min_occurrences: Minimum co-occurrence weight to consider.

        Returns:
            List of created PATTERN node IDs.
        """
        pattern_ids: list[str] = []

        all_knowledge_types = [
            NodeType.DOMAIN.value,
            NodeType.SYSTEM.value,
            NodeType.CONCEPT.value,
            NodeType.DATA_OBJECT.value,
        ]

        # Single query for all knowledge types instead of 4 separate queries
        nodes = self._graph_store.query(
            node_type=all_knowledge_types,
            limit=4000,
        )

        if not nodes:
            return pattern_ids

        # Batch fetch outgoing CO_OCCURS_WITH edges for all nodes
        node_ids = [node["node_id"] for node in nodes]
        edges_by_node = self._graph_store.get_edges_for_nodes(
            node_ids,
            direction="outgoing",
            edge_type=EdgeType.CO_OCCURS_WITH.value,
        )

        # Pre-fetch all existing PATTERN nodes to avoid per-edge queries
        existing_patterns = self._graph_store.query(
            node_type=NodeType.PATTERN.value,
            limit=10000,
        )
        existing_pattern_keys: dict[str, dict[str, Any]] = {
            p["properties"].get("pattern_key"): p
            for p in existing_patterns
            if p.get("properties", {}).get("pattern_key")
        }

        # Collect all node IDs that need title resolution
        title_needed_ids: set[str] = set()
        candidate_edges: list[dict[str, Any]] = []

        for node_id in node_ids:
            for edge in edges_by_node.get(node_id, []):
                weight = edge.get("properties", {}).get("weight", 0)
                if weight < min_occurrences:
                    continue

                source_id = edge["source_id"]
                target_id = edge["target_id"]
                pattern_key = f"{source_id}::{target_id}"

                if pattern_key in existing_pattern_keys:
                    # Update occurrence count
                    pattern = existing_pattern_keys[pattern_key]
                    props = pattern.get("properties", {})
                    props["occurrence_count"] = weight
                    self._graph_store.upsert_node(
                        node_id=pattern["node_id"],
                        node_type=NodeType.PATTERN.value,
                        properties=props,
                    )
                    continue

                title_needed_ids.add(source_id)
                title_needed_ids.add(target_id)
                candidate_edges.append(edge)

        # Batch fetch nodes for title resolution
        title_nodes = self._graph_store.get_nodes_bulk(list(title_needed_ids))
        title_map = {
            n["node_id"]: n.get("properties", {}).get("title", n["node_id"])
            for n in title_nodes
        }

        # Create new pattern nodes
        seen_keys: set[str] = set()
        for edge in candidate_edges:
            source_id = edge["source_id"]
            target_id = edge["target_id"]
            pattern_key = f"{source_id}::{target_id}"

            if pattern_key in seen_keys:
                continue
            seen_keys.add(pattern_key)

            weight = edge.get("properties", {}).get("weight", 0)
            source_title = title_map.get(source_id, source_id)
            target_title = title_map.get(target_id, target_id)

            now = utc_now()
            props = PatternProperties(
                title=f"Co-occurrence: {source_title} + {target_title}",
                description=(
                    f"Entities {source_title} and {target_title} "
                    f"appear together in {weight} trajectories"
                ),
                knowledge_source=KnowledgeSource.INFERENCE,
                confidence=min(weight / 10.0, 1.0),
                freshness=FreshnessScore(
                    last_accessed_at=now,
                    last_reinforced_at=now,
                ),
                tier=KnowledgeTier.WARM,
                occurrence_count=weight,
                trajectory_ids=[],
            )

            pattern_id = f"pattern:{generate_ulid()}"
            all_props = props.model_dump(mode="json")
            all_props["pattern_key"] = pattern_key

            self._graph_store.upsert_node(
                node_id=pattern_id,
                node_type=NodeType.PATTERN.value,
                properties=all_props,
            )

            # Link pattern to source entities
            self._graph_store.upsert_edge(
                source_id=pattern_id,
                target_id=source_id,
                edge_type=EdgeType.PATTERN_OBSERVED_IN.value,
                properties={},
            )
            self._graph_store.upsert_edge(
                source_id=pattern_id,
                target_id=target_id,
                edge_type=EdgeType.PATTERN_OBSERVED_IN.value,
                properties={},
            )

            pattern_ids.append(pattern_id)

            logger.info(
                "pattern_detected",
                pattern_id=pattern_id,
                source=source_title,
                target=target_title,
                weight=weight,
            )

        return pattern_ids
