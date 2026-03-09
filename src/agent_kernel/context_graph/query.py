"""Context Graph Query Service - relevance-weighted retrieval.

Provides relevance-weighted queries over the context graph for:
- Semantic memory: relevant knowledge nodes (concepts, insights, systems)
- Episodic memory: similar past trajectories
- Entity history: event clock for specific entities
- Domain context: knowledge within a domain subgraph
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from agent_kernel.context_graph.freshness import FreshnessCalculator
from agent_kernel.core.schemas.base import KernelModel, utc_now
from agent_kernel.core.schemas.graph import EdgeType, NodeType, TypedGraphSlice
from agent_kernel.core.schemas.knowledge import FreshnessScore, KnowledgeTier
from agent_kernel.memory.graph_store import GraphStore

logger = structlog.get_logger(__name__)

# Knowledge node types for semantic memory queries
KNOWLEDGE_NODE_TYPES = {
    NodeType.DOMAIN.value,
    NodeType.SYSTEM.value,
    NodeType.CONCEPT.value,
    NodeType.PRACTICE.value,
    NodeType.INSIGHT.value,
    NodeType.PATTERN.value,
    NodeType.DATA_OBJECT.value,
    NodeType.RULE.value,
}


class ContextGraphQuery(KernelModel):
    """Query against the context graph with relevance scoring."""

    node_types: list[str] | None = None
    keywords: list[str] | None = None
    tags: list[str] | None = None
    min_confidence: float = 0.0
    min_freshness: float = 0.0
    include_cold: bool = False
    domain_id: str | None = None
    related_to: str | None = None
    limit: int = 50


class ScoredNode(KernelModel):
    """A graph node with relevance scoring."""

    node_id: str
    node_type: str
    properties: dict[str, Any]
    relevance_score: float  # Combined: freshness x confidence x co-occurrence
    freshness_score: float
    confidence: float


class ContextGraphQueryResult(KernelModel):
    """Result of a context graph query."""

    nodes: list[ScoredNode]
    total_candidates: int
    query_time_ms: int = 0


class ContextGraphQueryService:
    """Relevance-weighted queries over the context graph."""

    def __init__(self, graph_store: GraphStore) -> None:
        self._graph_store = graph_store

    async def query(self, q: ContextGraphQuery) -> ContextGraphQueryResult:
        """Execute a relevance-weighted query over the context graph.

        Args:
            q: The query parameters.

        Returns:
            ContextGraphQueryResult with scored nodes.
        """
        import time

        start = time.time()
        now = utc_now()

        # Determine which node types to query
        target_types = q.node_types or list(KNOWLEDGE_NODE_TYPES)

        candidates: list[ScoredNode] = []

        # Single query with IN clause instead of N separate queries
        nodes = self._graph_store.query(
            node_type=target_types,
            limit=q.limit * 2,
        )

        for node in nodes:
            scored = self._score_node(node, q, now)
            if scored is not None:
                candidates.append(scored)

        # Sort by relevance descending
        candidates.sort(key=lambda n: n.relevance_score, reverse=True)
        total = len(candidates)
        candidates = candidates[: q.limit]

        elapsed_ms = int((time.time() - start) * 1000)

        return ContextGraphQueryResult(
            nodes=candidates,
            total_candidates=total,
            query_time_ms=elapsed_ms,
        )

    async def find_relevant_knowledge(
        self,
        intent: str,
        limit: int = 20,
    ) -> list[ScoredNode]:
        """Keyword-based knowledge retrieval for context assembly.

        Searches knowledge node titles and descriptions for intent keywords.

        Args:
            intent: The intent/query string.
            limit: Maximum results.

        Returns:
            List of scored knowledge nodes.
        """
        keywords = intent.lower().split()

        q = ContextGraphQuery(
            node_types=list(KNOWLEDGE_NODE_TYPES),
            keywords=keywords,
            min_confidence=0.0,
            min_freshness=0.0,
            include_cold=False,
            limit=limit,
        )

        result = await self.query(q)
        return result.nodes

    async def find_similar_trajectories(
        self,
        intent: str,
        limit: int = 5,
    ) -> list[ScoredNode]:
        """Find past trajectories with similar intents (episodic memory).

        Uses keyword matching on trajectory intent and outcome summary.

        Args:
            intent: The current intent to match against.
            limit: Maximum results.

        Returns:
            List of scored trajectory nodes.
        """
        keywords = intent.lower().split()
        now = utc_now()

        trajectories = self._graph_store.query(
            node_type=NodeType.TRAJECTORY.value,
            limit=limit * 3,
        )

        scored: list[ScoredNode] = []
        for traj in trajectories:
            props = traj.get("properties", {})
            traj_intent = (props.get("intent") or "").lower()
            outcome = (props.get("outcome_summary") or "").lower()

            # Keyword overlap scoring
            match_count = sum(
                1 for kw in keywords
                if kw in traj_intent or kw in outcome
            )
            if match_count == 0:
                continue

            keyword_score = match_count / len(keywords) if keywords else 0

            # Recency bonus
            created_at_str = props.get("created_at") or traj.get("created_at", "")
            recency_score = self._recency_score(created_at_str, now)

            # Success bonus
            outcome_status = props.get("outcome_status", "")
            success_bonus = 0.1 if outcome_status == "completed" else 0.0

            relevance = (keyword_score * 0.6) + (recency_score * 0.3) + success_bonus

            scored.append(ScoredNode(
                node_id=traj["node_id"],
                node_type=traj["node_type"],
                properties=props,
                relevance_score=relevance,
                freshness_score=recency_score,
                confidence=1.0,
            ))

        scored.sort(key=lambda n: n.relevance_score, reverse=True)
        return scored[:limit]

    async def get_domain_context(
        self,
        domain_id: str,
        depth: int = 2,
    ) -> TypedGraphSlice:
        """Get all knowledge within a domain subgraph.

        Args:
            domain_id: Node ID of the DOMAIN node.
            depth: Traversal depth.

        Returns:
            TypedGraphSlice with domain knowledge.
        """
        from agent_kernel.core.schemas.graph import GraphEdge, GraphNode

        subgraph = self._graph_store.get_subgraph(
            seed_ids=[domain_id],
            depth=depth,
        )

        nodes = []
        for n in subgraph.get("nodes", []):
            nodes.append(GraphNode(
                node_id=n["node_id"],
                node_type=n["node_type"],
                properties=n.get("properties", {}),
            ))

        edges = []
        for e in subgraph.get("edges", []):
            edges.append(GraphEdge(
                edge_id=e["edge_id"],
                edge_type=e["edge_type"],
                source_id=e["source_id"],
                target_id=e["target_id"],
                properties=e.get("properties", {}),
            ))

        return TypedGraphSlice(nodes=nodes, edges=edges)

    async def get_entity_history(
        self,
        entity_node_id: str,
    ) -> list[ScoredNode]:
        """Event clock: what trajectories touched this entity?

        Args:
            entity_node_id: Node ID of the entity.

        Returns:
            List of trajectory nodes that touched this entity,
            ordered by recency.
        """
        now = utc_now()

        # Find TRAJECTORY_TOUCHED edges pointing to this entity
        edges = self._graph_store.get_edges(
            entity_node_id,
            direction="incoming",
            edge_type=EdgeType.TRAJECTORY_TOUCHED.value,
        )

        # Batch fetch all source nodes instead of N individual queries
        source_ids = [edge["source_id"] for edge in edges]
        nodes_map = {
            n["node_id"]: n
            for n in self._graph_store.get_nodes_bulk(source_ids)
        }

        trajectories: list[ScoredNode] = []
        for edge in edges:
            traj_node = nodes_map.get(edge["source_id"])
            if traj_node is None:
                continue

            props = traj_node.get("properties", {})
            created_at_str = props.get("created_at") or traj_node.get("created_at", "")
            recency = self._recency_score(created_at_str, now)

            trajectories.append(ScoredNode(
                node_id=traj_node["node_id"],
                node_type=traj_node["node_type"],
                properties=props,
                relevance_score=recency,
                freshness_score=recency,
                confidence=1.0,
            ))

        trajectories.sort(key=lambda n: n.relevance_score, reverse=True)
        return trajectories

    async def record_access(self, node_id: str) -> None:
        """Update freshness when a node is included in context.

        Called by the ContextAssembler when a knowledge node is
        selected for inclusion in a ContextPacket.
        """
        node = self._graph_store.get_node(node_id)
        if node is None:
            return

        props = node.get("properties", {})
        freshness_data = props.get("freshness")
        if not freshness_data:
            return

        try:
            freshness = FreshnessScore.model_validate(freshness_data)
            updated = FreshnessCalculator.record_access(freshness)
            props["freshness"] = updated.model_dump(mode="json")
            self._graph_store.upsert_node(
                node_id=node_id,
                node_type=node["node_type"],
                properties=props,
            )
        except Exception:
            logger.debug("freshness_update_failed", node_id=node_id)

    def _score_node(
        self,
        node: dict[str, Any],
        q: ContextGraphQuery,
        now: datetime,
    ) -> ScoredNode | None:
        """Score a node against query criteria. Returns None if filtered out."""
        props = node.get("properties", {})

        # Filter by tier
        tier = props.get("tier", KnowledgeTier.HOT.value)
        if not q.include_cold and tier == KnowledgeTier.COLD.value:
            return None

        # Filter by confidence
        confidence = props.get("confidence", 1.0)
        if confidence < q.min_confidence:
            return None

        # Calculate freshness
        freshness_data = props.get("freshness", {})
        freshness_score = 1.0
        try:
            freshness = FreshnessScore.model_validate(freshness_data)
            freshness_score = freshness.effective_relevance(now)
        except Exception:
            pass

        if freshness_score < q.min_freshness:
            return None

        # Filter by tags
        if q.tags:
            node_tags = set(props.get("tags", []))
            if not node_tags.intersection(q.tags):
                return None

        # Keyword matching
        keyword_score = 1.0
        if q.keywords:
            title = (props.get("title") or "").lower()
            desc = (props.get("description") or "").lower()
            searchable = f"{title} {desc}"

            match_count = sum(1 for kw in q.keywords if kw in searchable)
            if match_count == 0:
                return None
            keyword_score = match_count / len(q.keywords)

        # Combined relevance: keyword * freshness * confidence
        relevance = keyword_score * freshness_score * confidence

        return ScoredNode(
            node_id=node["node_id"],
            node_type=node["node_type"],
            properties=props,
            relevance_score=relevance,
            freshness_score=freshness_score,
            confidence=confidence,
        )

    @staticmethod
    def _recency_score(
        created_at_str: str,
        now: datetime,
        half_life_days: float = 30.0,
    ) -> float:
        """Calculate a recency score (0-1) with exponential decay."""
        if not created_at_str:
            return 0.5

        try:
            if isinstance(created_at_str, str):
                created_at = datetime.fromisoformat(created_at_str)
            else:
                created_at = created_at_str

            if created_at.tzinfo is None:
                from datetime import UTC

                created_at = created_at.replace(tzinfo=UTC)

            days_ago = (now - created_at).total_seconds() / 86400.0
            if days_ago <= 0:
                return 1.0
            return 0.5 ** (days_ago / half_life_days)
        except (ValueError, TypeError):
            return 0.5
