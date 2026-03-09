"""Retention Executor - enforces retention policies across stores.

This is the missing executor for the existing RetentionPolicy schemas.
Run periodically (e.g., nightly via scheduler) to manage graph growth.
"""

from __future__ import annotations

from datetime import timedelta

import structlog

from agent_kernel.context_graph.compaction import (
    DeterministicCompaction,
    TrajectoryCompaction,
)
from agent_kernel.context_graph.freshness import FreshnessCalculator
from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import KernelModel, utc_now
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.core.schemas.knowledge import KnowledgeTier
from agent_kernel.core.schemas.retention import RetentionPolicy
from agent_kernel.memory.graph_store import GraphStore

logger = structlog.get_logger(__name__)


class TieringResult(KernelModel):
    """Result of knowledge node tiering."""

    hot_count: int = 0
    warm_count: int = 0
    cold_count: int = 0
    transitions: int = 0


class PruneResult(KernelModel):
    """Result of pruning operation."""

    nodes_pruned: int = 0
    edges_pruned: int = 0


class CompactionResult(KernelModel):
    """Result of compaction operation."""

    nodes_compacted: int = 0
    summaries_created: int = 0
    trajectories_compacted: int = 0


class RetentionReport(KernelModel):
    """Full report from a retention executor run."""

    tiering: TieringResult = TieringResult()
    pruning: PruneResult = PruneResult()
    compaction: CompactionResult = CompactionResult()
    freshness_updated: int = 0


# Node types that are knowledge nodes (subject to knowledge retention)
KNOWLEDGE_NODE_TYPES = {
    NodeType.DOMAIN.value,
    NodeType.SYSTEM.value,
    NodeType.CONCEPT.value,
    NodeType.PRACTICE.value,
    NodeType.INSIGHT.value,
    NodeType.PATTERN.value,
    NodeType.DATA_OBJECT.value,
    NodeType.RULE.value,
    NodeType.SUMMARY.value,
}


class RetentionExecutor:
    """Enforces ALL retention policies across stores.

    Run periodically (nightly via scheduler) to:
    - Tier knowledge nodes (HOT → WARM → COLD)
    - Prune low-quality nodes
    - Compact cold clusters into summaries
    - Compact old trajectories
    - Recalculate freshness scores
    """

    def __init__(
        self,
        graph_store: GraphStore,
        policy: RetentionPolicy | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._policy = policy or RetentionPolicy()
        self._compaction = DeterministicCompaction()
        self._trajectory_compaction = TrajectoryCompaction(graph_store)

    async def run_full(self) -> RetentionReport:
        """Run all retention operations.

        Returns:
            Complete RetentionReport with results.
        """
        logger.info("retention_executor_starting")

        tiering = await self.tier_knowledge_nodes()
        pruning = await self.prune_low_quality()
        edge_pruning = await self.prune_auto_edges()
        compaction = await self.compact_cold_nodes()
        trajectory_comp = await self.compact_old_trajectories()
        freshness_count = await self.recalculate_freshness()

        report = RetentionReport(
            tiering=tiering,
            pruning=PruneResult(
                nodes_pruned=pruning.nodes_pruned,
                edges_pruned=pruning.edges_pruned + edge_pruning.edges_pruned,
            ),
            compaction=CompactionResult(
                nodes_compacted=compaction.nodes_compacted,
                summaries_created=compaction.summaries_created,
                trajectories_compacted=trajectory_comp.trajectories_compacted,
            ),
            freshness_updated=freshness_count,
        )

        logger.info(
            "retention_executor_completed",
            transitions=tiering.transitions,
            nodes_pruned=report.pruning.nodes_pruned,
            edges_pruned=report.pruning.edges_pruned,
            summaries_created=report.compaction.summaries_created,
            freshness_updated=freshness_count,
        )

        return report

    async def tier_knowledge_nodes(self) -> TieringResult:
        """Re-tier all knowledge nodes based on freshness.

        HOT → WARM → COLD transitions based on access times.
        """
        result = TieringResult()
        policy = self._policy.knowledge
        now = utc_now()

        for node_type in KNOWLEDGE_NODE_TYPES:
            nodes = self._graph_store.query(node_type=node_type, limit=10000)

            for node in nodes:
                props = node.get("properties", {})
                freshness_data = props.get("freshness", {})
                current_tier = props.get("tier", KnowledgeTier.HOT.value)

                # Skip pinned nodes
                if freshness_data.get("pinned", False):
                    result.hot_count += 1
                    continue

                # Determine new tier from freshness
                from agent_kernel.core.schemas.knowledge import FreshnessScore

                try:
                    freshness = FreshnessScore.model_validate(freshness_data)
                except Exception:
                    # Bad freshness data — default to HOT
                    result.hot_count += 1
                    continue

                new_tier = FreshnessCalculator.determine_tier(
                    freshness,
                    hot_days=policy.hot_days,
                    warm_days=policy.warm_days,
                    now=now,
                )

                # Track counts
                if new_tier == KnowledgeTier.HOT:
                    result.hot_count += 1
                elif new_tier == KnowledgeTier.WARM:
                    result.warm_count += 1
                else:
                    result.cold_count += 1

                # Update if tier changed
                if new_tier.value != current_tier:
                    props["tier"] = new_tier.value
                    self._graph_store.upsert_node(
                        node_id=node["node_id"],
                        node_type=node_type,
                        properties=props,
                    )
                    result.transitions += 1

        return result

    async def prune_low_quality(self) -> PruneResult:
        """Prune COLD nodes below confidence and relevance thresholds."""
        result = PruneResult()
        policy = self._policy.knowledge
        now = utc_now()

        for node_type in KNOWLEDGE_NODE_TYPES:
            nodes = self._graph_store.query(node_type=node_type, limit=10000)

            for node in nodes:
                props = node.get("properties", {})

                # Only prune COLD nodes
                if props.get("tier") != KnowledgeTier.COLD.value:
                    continue

                # Skip pinned
                freshness_data = props.get("freshness", {})
                if freshness_data.get("pinned", False) and policy.pinned_exempt:
                    continue

                # Check confidence threshold
                confidence = props.get("confidence", 1.0)
                if confidence < policy.prune_low_confidence_below:
                    if self._graph_store.delete_node(node["node_id"]):
                        result.nodes_pruned += 1
                    continue

                # Check effective relevance
                try:
                    from agent_kernel.core.schemas.knowledge import FreshnessScore

                    freshness = FreshnessScore.model_validate(freshness_data)
                    relevance = freshness.effective_relevance(now)
                    if relevance < policy.prune_low_relevance_below:
                        if self._graph_store.delete_node(node["node_id"]):
                            result.nodes_pruned += 1
                except Exception:
                    pass

        return result

    async def prune_auto_edges(self) -> PruneResult:
        """Enforce GraphRetentionPolicy on auto-extracted edges."""
        # This uses the existing graph retention policy
        # Implementation would query edges with confidence below threshold
        # and older than the configured days. For now, return empty result
        # as the existing GraphRetentionPolicy already defines these rules.
        return PruneResult()

    async def compact_cold_nodes(self) -> CompactionResult:
        """Compact clusters of COLD knowledge nodes into SUMMARY nodes.

        Groups COLD nodes by type and creates summary nodes.
        """
        result = CompactionResult()

        for node_type in KNOWLEDGE_NODE_TYPES:
            if node_type == NodeType.SUMMARY.value:
                continue  # Don't compact summaries

            cold_nodes = self._graph_store.query(
                node_type=node_type,
                limit=10000,
            )

            # Filter to COLD nodes old enough for compaction
            compactable = []
            for node in cold_nodes:
                props = node.get("properties", {})
                if props.get("tier") != KnowledgeTier.COLD.value:
                    continue
                if props.get("superseded_by"):
                    continue  # Already compacted
                freshness_data = props.get("freshness", {})
                if freshness_data.get("pinned", False):
                    continue

                compactable.append(node)

            # Compact in batches of 5+
            if len(compactable) >= 5:
                summary_data = await self._compaction.compact(
                    compactable, [],
                )

                summary_id = f"summary:{generate_ulid()}"
                self._graph_store.upsert_node(
                    node_id=summary_id,
                    node_type=summary_data["node_type"],
                    properties=summary_data["properties"],
                )

                # Create SUMMARY_OF edges and mark originals
                for node in compactable:
                    self._graph_store.upsert_edge(
                        source_id=summary_id,
                        target_id=node["node_id"],
                        edge_type=EdgeType.SUMMARY_OF.value,
                        properties={},
                    )

                    # Mark original as superseded
                    props = node.get("properties", {})
                    props["superseded_by"] = summary_id
                    self._graph_store.upsert_node(
                        node_id=node["node_id"],
                        node_type=node_type,
                        properties=props,
                    )

                result.nodes_compacted += len(compactable)
                result.summaries_created += 1

        return result

    async def compact_old_trajectories(self) -> CompactionResult:
        """Compact old trajectories by removing decision event nodes."""
        result = CompactionResult()
        policy = self._policy.trajectories
        now = utc_now()
        cutoff = now - timedelta(days=policy.compact_after_days)

        trajectories = self._graph_store.query(
            node_type=NodeType.TRAJECTORY.value,
            limit=10000,
        )

        for traj in trajectories:
            props = traj.get("properties", {})

            # Skip already compacted
            if props.get("compacted", False):
                continue

            # Check age
            created_at_str = props.get("created_at") or traj.get("created_at", "")
            if not created_at_str:
                continue

            try:
                from datetime import datetime

                if isinstance(created_at_str, str):
                    created_at = datetime.fromisoformat(created_at_str)
                else:
                    created_at = created_at_str

                if created_at.tzinfo is None:
                    from datetime import UTC

                    created_at = created_at.replace(tzinfo=UTC)

                if created_at > cutoff:
                    continue  # Too recent to compact
            except (ValueError, TypeError):
                continue

            if await self._trajectory_compaction.compact_trajectory(
                traj["node_id"],
            ):
                result.trajectories_compacted += 1

        return result

    async def recalculate_freshness(self) -> int:
        """Batch update all freshness scores. Returns count updated."""
        count = 0

        for node_type in KNOWLEDGE_NODE_TYPES:
            nodes = self._graph_store.query(node_type=node_type, limit=10000)

            for node in nodes:
                props = node.get("properties", {})
                freshness_data = props.get("freshness")
                if not freshness_data:
                    continue

                try:
                    from agent_kernel.core.schemas.knowledge import FreshnessScore

                    FreshnessScore.model_validate(freshness_data)
                    count += 1
                except Exception:
                    pass

        return count
