"""Compaction pipeline for context graph growth management.

Compaction strategies for reducing graph size while preserving provenance:
- DeterministicCompaction: Merge knowledge nodes without LLM
- TrajectoryCompaction: Compact old trajectories into summaries
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import structlog

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.core.schemas.knowledge import (
    FreshnessScore,
    KnowledgeSource,
    KnowledgeTier,
    SummaryProperties,
)

logger = structlog.get_logger(__name__)


class CompactionStrategy(ABC):
    """Base class for compaction strategies."""

    @abstractmethod
    async def compact(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compact a set of nodes into a summary.

        Args:
            nodes: List of node dicts to compact.
            edges: List of edge dicts between these nodes.

        Returns:
            Dict with:
            - node_type: Type for the summary node
            - properties: Properties for the summary node
        """


class DeterministicCompaction(CompactionStrategy):
    """Merge knowledge nodes without LLM.

    Strategy:
    - Concatenate descriptions
    - Use most recent title
    - Union tags
    - Average confidence
    - Preserve all source_refs
    """

    async def compact(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Merge nodes deterministically."""
        if not nodes:
            msg = "Cannot compact empty node list"
            raise ValueError(msg)

        # Collect properties from all nodes
        all_titles = []
        all_descriptions = []
        all_tags: set[str] = set()
        all_source_refs: list[str] = []
        confidence_sum = 0.0
        node_ids = []

        for node in nodes:
            props = node.get("properties", {})
            node_ids.append(node["node_id"])

            title = props.get("title", "")
            if title:
                all_titles.append(title)

            desc = props.get("description", "")
            if desc:
                all_descriptions.append(desc)

            tags = props.get("tags", [])
            all_tags.update(tags)

            refs = props.get("source_refs", [])
            all_source_refs.extend(refs)

            confidence_sum += props.get("confidence", 1.0)

        # Use most recent title (last node, assumed sorted by time)
        title = all_titles[-1] if all_titles else "Summary"

        # Concatenate descriptions
        description = " | ".join(all_descriptions) if all_descriptions else None

        # Average confidence
        avg_confidence = confidence_sum / len(nodes) if nodes else 1.0

        now = utc_now()
        summary_props = SummaryProperties(
            title=f"Summary: {title}",
            description=description,
            knowledge_source=KnowledgeSource.COMPACTION,
            source_refs=all_source_refs,
            confidence=avg_confidence,
            freshness=FreshnessScore(
                last_accessed_at=now,
                last_reinforced_at=now,
            ),
            tier=KnowledgeTier.COLD,
            tags=sorted(all_tags),
            summarized_node_ids=node_ids,
            original_count=len(nodes),
        )

        return {
            "node_type": NodeType.SUMMARY.value,
            "properties": summary_props.model_dump(mode="json"),
        }


class TrajectoryCompaction:
    """Compact old trajectories.

    Strategy:
    - Keep the TRAJECTORY node with summary properties
    - Remove individual DECISION_EVENT nodes
    - Preserve CO_OCCURS_WITH edges (they're between entities, not events)
    - Mark trajectory as compacted
    """

    def __init__(self, graph_store: Any) -> None:
        self._graph_store = graph_store

    async def compact_trajectory(self, trajectory_node_id: str) -> bool:
        """Compact a trajectory by removing its decision events.

        Args:
            trajectory_node_id: Node ID of the TRAJECTORY to compact.

        Returns:
            True if compaction was performed, False if already compacted
            or trajectory not found.
        """
        node = self._graph_store.get_node(trajectory_node_id)
        if node is None:
            return False

        props = node.get("properties", {})
        if props.get("compacted", False):
            return False

        # Find all DECISION_EVENT nodes linked via TRAJECTORY_DECIDED
        decided_edges = self._graph_store.get_edges(
            trajectory_node_id,
            direction="outgoing",
            edge_type=EdgeType.TRAJECTORY_DECIDED.value,
        )

        event_node_ids = [edge["target_id"] for edge in decided_edges]

        # Delete decision event nodes (and their edges) in batch
        deleted_count = self._graph_store.delete_nodes_bulk(event_node_ids)

        # Mark trajectory as compacted
        props["compacted"] = True
        props["compacted_at"] = utc_now().isoformat()
        props["events_removed"] = deleted_count
        self._graph_store.upsert_node(
            node_id=trajectory_node_id,
            node_type=NodeType.TRAJECTORY.value,
            properties=props,
        )

        logger.info(
            "trajectory_compacted",
            trajectory_id=trajectory_node_id,
            events_removed=deleted_count,
        )

        return True
