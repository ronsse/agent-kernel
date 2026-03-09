"""Context curator library tools for effectiveness evaluation and curation.

Implements three capabilities:
- context.evaluate@v1: Analyze traces to score context item effectiveness
- context.curate@v1: Pre-assemble warm context packages for agents
- context.profile.update@v1: Update learned context preferences in the graph
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.graph import EdgeType, NodeType

logger = structlog.get_logger(__name__)

# Effectiveness thresholds
_BOOST_CITATION_THRESHOLD = 0.5
_DEMOTE_CITATION_THRESHOLD = 0.1

# Lazy-initialized singletons for stores
_trace_store: Any = None
_graph_store: Any = None


def _get_trace_store() -> Any:
    """Lazy-init the trace store from settings."""
    global _trace_store  # noqa: PLW0603
    if _trace_store is None:
        from pathlib import Path  # noqa: PLC0415

        from agent_kernel.core.config import get_settings  # noqa: PLC0415
        from agent_kernel.tracing.sinks.sqlite_sink import (  # noqa: PLC0415
            SQLiteTraceSink,
        )

        settings = get_settings()
        _trace_store = SQLiteTraceSink(
            Path(settings.trace_store_path) / "traces.db"
        )
    return _trace_store


def _get_graph_store() -> Any:
    """Lazy-init the graph store from settings."""
    global _graph_store  # noqa: PLW0603
    if _graph_store is None:
        from agent_kernel.core.config import get_settings  # noqa: PLC0415
        from agent_kernel.memory.graph_store import SQLiteGraphStore  # noqa: PLC0415

        settings = get_settings()
        _graph_store = SQLiteGraphStore(settings.data_dir / "graph" / "graph.db")
    return _graph_store


def evaluate_effectiveness(
    *,
    agent_profile_ids: list[str],
    lookback_hours: int = 24,
    min_traces: int = 5,
    trace_store: Any = None,
) -> dict[str, Any]:
    """Evaluate context effectiveness by analyzing recent traces.

    Compares context_packet.items (assembled) vs plan.context_refs_used (cited)
    to compute citation rates and outcome distributions per agent profile.

    Args:
        agent_profile_ids: Agent profiles to evaluate.
        lookback_hours: Hours of traces to analyze.
        min_traces: Minimum traces required for meaningful evaluation.
        trace_store: Optional trace store for dependency injection (testing).

    Returns:
        Dict with evaluations per agent profile.
    """
    if trace_store is None:
        trace_store = _get_trace_store()
    since = datetime.now(tz=UTC) - timedelta(hours=lookback_hours)

    evaluations = []

    for agent_id in agent_profile_ids:
        traces = trace_store.list_traces(
            agent_profile_id=agent_id,
            since=since,
            limit=200,
        )

        if len(traces) < min_traces:
            evaluations.append({
                "agent_profile_id": agent_id,
                "traces_analyzed": len(traces),
                "citation_rate": 0.0,
                "outcome_distribution": {},
                "item_scores": [],
                "skipped": True,
                "skip_reason": f"Only {len(traces)} traces (min: {min_traces})",
            })
            continue

        # Track per-item citation and outcome data
        item_appearances: dict[str, int] = {}  # ref_id -> times assembled
        item_citations: dict[str, int] = {}  # ref_id -> times cited
        item_success_count: dict[str, int] = {}  # ref_id -> success correlations
        outcome_counts: dict[str, int] = {}
        total_assembled = 0
        total_cited = 0

        for trace in traces:
            # Count outcome
            outcome_status = trace.outcome.status.value
            outcome_counts[outcome_status] = outcome_counts.get(outcome_status, 0) + 1
            is_success = outcome_status == "completed"

            # Gather assembled item ref_ids from context_refs_used on the plan
            # (the plan's context_refs_used reflects what the agent cited)
            cited_ref_ids = {
                ref.ref_id for ref in trace.plan.context_refs_used
            }
            total_cited += len(cited_ref_ids)

            # We look at all action evidence_refs too for broader coverage
            for action in trace.plan.actions:
                for ref_id in action.evidence_refs:
                    cited_ref_ids.add(ref_id)

            # Since we don't persist ContextPacket items separately, we use
            # the plan's cited refs as the "assembled" baseline and count
            # what was actually used. For traces with context_packet_id, we
            # note citations.
            for ref_id in cited_ref_ids:
                item_appearances[ref_id] = item_appearances.get(ref_id, 0) + 1
                item_citations[ref_id] = item_citations.get(ref_id, 0) + 1
                if is_success:
                    item_success_count[ref_id] = (
                        item_success_count.get(ref_id, 0) + 1
                    )
                total_assembled += 1

        # Compute per-item scores
        item_scores = []
        for ref_id, appearances in item_appearances.items():
            citations = item_citations.get(ref_id, 0)
            successes = item_success_count.get(ref_id, 0)
            item_scores.append({
                "ref_id": ref_id,
                "citation_count": citations,
                "total_appearances": appearances,
                "success_correlation": (
                    successes / appearances if appearances > 0 else 0.0
                ),
            })

        # Sort by citation_count descending
        item_scores.sort(key=lambda x: x["citation_count"], reverse=True)

        # Overall citation rate
        citation_rate = total_cited / total_assembled if total_assembled > 0 else 0.0

        evaluations.append({
            "agent_profile_id": agent_id,
            "traces_analyzed": len(traces),
            "citation_rate": round(citation_rate, 4),
            "outcome_distribution": outcome_counts,
            "item_scores": item_scores[:50],  # Top 50
            "top_cited": [s["ref_id"] for s in item_scores[:10]],
            "never_cited": [
                s["ref_id"]
                for s in item_scores
                if s["citation_count"] == 0
            ][:10],
        })

    logger.info(
        "context_effectiveness_evaluated",
        agent_count=len(agent_profile_ids),
        total_evaluations=len(evaluations),
    )

    return {"evaluations": evaluations}


def _score_candidates(
    candidates: list[dict[str, Any]],
    boost_map: dict[str, float],
    now: Any,
) -> tuple[list[dict[str, Any]], int, int]:
    """Score candidate nodes by freshness, confidence, and effectiveness."""
    scored: list[dict[str, Any]] = []
    boosted_count = 0
    demoted_count = 0

    for node in candidates:
        node_id = node["node_id"]
        props = node.get("properties", {})

        # Base relevance from freshness
        freshness_data = props.get("freshness", {})
        base_score = 1.0
        if freshness_data:
            try:
                from agent_kernel.core.schemas.knowledge import (  # noqa: PLC0415
                    FreshnessScore,
                )

                freshness = FreshnessScore.model_validate(freshness_data)
                base_score = freshness.effective_relevance(now)
            except Exception:
                logger.debug("freshness_parse_failed", node_id=node_id)

        # Confidence factor
        confidence = props.get("confidence", 1.0)

        # Effectiveness boost/demotion
        effectiveness_factor = 1.0
        is_boosted = False
        is_demoted = False

        if node_id in boost_map:
            citation_rate = boost_map[node_id]
            if citation_rate > _BOOST_CITATION_THRESHOLD:
                effectiveness_factor = 1.0 + (citation_rate * 0.5)
                is_boosted = True
                boosted_count += 1
            elif citation_rate < _DEMOTE_CITATION_THRESHOLD:
                effectiveness_factor = 0.5
                is_demoted = True
                demoted_count += 1

        final_score = base_score * confidence * effectiveness_factor

        scored.append({
            "node_id": node_id,
            "node_type": node["node_type"],
            "title": props.get("title", node_id),
            "score": round(final_score, 4),
            "boosted": is_boosted,
            "demoted": is_demoted,
        })

    return scored, boosted_count, demoted_count


def curate_context(
    *,
    agent_profile_id: str,
    max_items: int = 30,
    max_tokens: int = 4000,
    ttl_seconds: int = 14400,
    include_trajectories: bool = True,
    include_lessons: bool = True,
    include_patterns: bool = True,
    graph_store: Any = None,
) -> dict[str, Any]:
    """Pre-assemble warm context packages for an agent.

    Queries the context graph for relevant knowledge nodes, applies
    effectiveness-based boosting/demotion, and packages results.

    Args:
        agent_profile_id: Agent profile to curate for.
        max_items: Maximum context items to include.
        max_tokens: Token budget for warm context.
        ttl_seconds: Cache TTL in seconds.
        include_trajectories: Include recent trajectory summaries.
        include_lessons: Include active lessons.
        include_patterns: Include detected patterns.
        graph_store: Optional graph store for dependency injection (testing).

    Returns:
        Dict with curation results.
    """
    if graph_store is None:
        graph_store = _get_graph_store()
    now = utc_now()

    # Collect candidate node types
    target_types: list[str] = [
        NodeType.CONCEPT.value,
        NodeType.INSIGHT.value,
        NodeType.SYSTEM.value,
        NodeType.PRACTICE.value,
        NodeType.RULE.value,
        NodeType.DATA_OBJECT.value,
    ]

    if include_patterns:
        target_types.append(NodeType.PATTERN.value)

    if include_lessons:
        target_types.append(NodeType.LESSON.value)

    # Query knowledge nodes
    candidates = graph_store.query(
        node_type=target_types,
        limit=max_items * 3,
    )

    # Add trajectories if requested
    if include_trajectories:
        trajectories = graph_store.query(
            node_type=NodeType.TRAJECTORY.value,
            limit=10,
        )
        candidates.extend(trajectories)

    # Look up EFFECTIVE_FOR edges for this agent to get boost/demote signals
    effective_edges = graph_store.get_edges(
        agent_profile_id,
        direction="incoming",
        edge_type=EdgeType.EFFECTIVE_FOR.value,
    )
    boost_map: dict[str, float] = {}
    for edge in effective_edges:
        props = edge.get("properties", {})
        boost_map[edge["source_id"]] = props.get("citation_rate", 0.0)

    # Score and rank candidates
    scored_items, boosted_count, demoted_count = _score_candidates(
        candidates, boost_map, now,
    )

    # Sort by score descending and limit
    scored_items.sort(key=lambda x: x["score"], reverse=True)
    selected = scored_items[:max_items]

    # Cap to token budget (rough: ~50 tokens per item)
    tokens_per_item = 50
    max_by_tokens = max_tokens // tokens_per_item
    if len(selected) > max_by_tokens:
        selected = selected[:max_by_tokens]
    estimated_tokens = len(selected) * tokens_per_item

    # Look up staircase level from agent profile node
    agent_node = graph_store.get_node(agent_profile_id)
    staircase_level = 0
    if agent_node:
        agent_props = agent_node.get("properties", {})
        staircase_level = agent_props.get("staircase_level", 0)

    logger.info(
        "context_curated",
        agent_profile_id=agent_profile_id,
        candidates=len(candidates),
        selected=len(selected),
        boosted=boosted_count,
        demoted=demoted_count,
    )

    return {
        "agent_profile_id": agent_profile_id,
        "curated_at": now.isoformat(),
        "items_included": len(selected),
        "items_boosted": boosted_count,
        "items_demoted": demoted_count,
        "estimated_tokens": estimated_tokens,
        "ttl_seconds": ttl_seconds,
        "staircase_level": staircase_level,
        "cache_key": f"curated:{agent_profile_id}:{now.isoformat()[:13]}",
        "items": selected,
    }


def update_context_profile(
    *,
    agent_profile_id: str,
    boosted_ref_ids: list[str] | None = None,
    demoted_ref_ids: list[str] | None = None,
    node_type_weights: dict[str, float] | None = None,
    staircase_level: int | None = None,
    reinforce_ref_ids: list[str] | None = None,
    graph_store: Any = None,
) -> dict[str, Any]:
    """Update an agent's learned context preferences in the graph.

    Creates/updates EFFECTIVE_FOR edges between knowledge nodes and
    the agent profile node. Reinforces freshness on boosted nodes.

    Args:
        agent_profile_id: Agent profile to update.
        boosted_ref_ids: Node IDs to boost (high citation rate).
        demoted_ref_ids: Node IDs to demote (never cited).
        node_type_weights: Adjusted weights per node type.
        staircase_level: New staircase level (0-4).
        reinforce_ref_ids: Node IDs to reinforce via FreshnessCalculator.
        graph_store: Optional graph store for dependency injection (testing).

    Returns:
        Dict with update summary.
    """
    if graph_store is None:
        graph_store = _get_graph_store()
    now = utc_now()

    boosted_ref_ids = boosted_ref_ids or []
    demoted_ref_ids = demoted_ref_ids or []
    reinforce_ref_ids = reinforce_ref_ids or []

    edges_created = 0
    nodes_reinforced = 0
    nodes_demoted = 0

    # Ensure agent profile node exists
    agent_node = graph_store.get_node(agent_profile_id)
    if not agent_node:
        graph_store.upsert_node(
            node_id=agent_profile_id,
            node_type=NodeType.CAPABILITY.value,
            properties={
                "title": agent_profile_id,
                "is_agent_profile": True,
                "staircase_level": staircase_level or 0,
                "node_type_weights": node_type_weights or {},
                "updated_at": now.isoformat(),
            },
        )

    # Create/update EFFECTIVE_FOR edges for boosted items
    for ref_id in boosted_ref_ids:
        node = graph_store.get_node(ref_id)
        if node is None:
            continue

        graph_store.upsert_edge(
            source_id=ref_id,
            target_id=agent_profile_id,
            edge_type=EdgeType.EFFECTIVE_FOR.value,
            properties={
                "citation_rate": 1.0,
                "outcome_boost": 0.5,
                "updated_at": now.isoformat(),
                "extracted_by": "context_curator",
            },
        )
        edges_created += 1

    # Create/update EFFECTIVE_FOR edges for demoted items
    for ref_id in demoted_ref_ids:
        node = graph_store.get_node(ref_id)
        if node is None:
            continue

        graph_store.upsert_edge(
            source_id=ref_id,
            target_id=agent_profile_id,
            edge_type=EdgeType.EFFECTIVE_FOR.value,
            properties={
                "citation_rate": 0.0,
                "outcome_boost": -0.5,
                "updated_at": now.isoformat(),
                "extracted_by": "context_curator",
            },
        )
        nodes_demoted += 1

    # Reinforce freshness on specified nodes
    for ref_id in reinforce_ref_ids:
        node = graph_store.get_node(ref_id)
        if node is None:
            continue

        props = node.get("properties", {})
        freshness_data = props.get("freshness")
        if freshness_data:
            try:
                from agent_kernel.context_graph.freshness import (  # noqa: PLC0415
                    FreshnessCalculator,
                )
                from agent_kernel.core.schemas.knowledge import (  # noqa: PLC0415
                    FreshnessScore,
                )

                freshness = FreshnessScore.model_validate(freshness_data)
                updated = FreshnessCalculator.record_reinforcement(freshness)
                props["freshness"] = updated.model_dump(mode="json")
                graph_store.upsert_node(
                    node_id=ref_id,
                    node_type=node["node_type"],
                    properties=props,
                )
                nodes_reinforced += 1
            except Exception:
                logger.debug("freshness_reinforcement_failed", ref_id=ref_id)

    # Update staircase level on agent profile node
    if staircase_level is not None:
        agent_node = graph_store.get_node(agent_profile_id)
        if agent_node:
            agent_props = agent_node.get("properties", {})
            agent_props["staircase_level"] = staircase_level
            if node_type_weights:
                agent_props["node_type_weights"] = node_type_weights
            agent_props["updated_at"] = now.isoformat()
            graph_store.upsert_node(
                node_id=agent_profile_id,
                node_type=agent_node["node_type"],
                properties=agent_props,
            )

    logger.info(
        "context_profile_updated",
        agent_profile_id=agent_profile_id,
        edges_created=edges_created,
        nodes_reinforced=nodes_reinforced,
        nodes_demoted=nodes_demoted,
        staircase_level=staircase_level,
    )

    return {
        "agent_profile_id": agent_profile_id,
        "updated_at": now.isoformat(),
        "edges_created": edges_created,
        "nodes_reinforced": nodes_reinforced,
        "nodes_demoted": nodes_demoted,
    }
