"""Tests for ContextGraphQueryService - relevance-weighted retrieval."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_kernel.context_graph.query import (
    ContextGraphQuery,
    ContextGraphQueryService,
)
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.core.schemas.knowledge import (
    FreshnessScore,
    KnowledgeSource,
    KnowledgeTier,
)
from agent_kernel.memory.graph_store import SQLiteGraphStore


@pytest.fixture
def graph_store(tmp_path):
    store = SQLiteGraphStore(tmp_path / "test.db")
    yield store
    store.close()


@pytest.fixture
def query_service(graph_store):
    return ContextGraphQueryService(graph_store=graph_store)


def _create_knowledge_node(
    graph_store,
    node_id: str,
    node_type: str = NodeType.CONCEPT.value,
    title: str = "Test",
    description: str | None = None,
    tier: str = KnowledgeTier.HOT.value,
    confidence: float = 1.0,
    tags: list[str] | None = None,
    days_ago: int = 0,
) -> None:
    """Helper to create a knowledge node."""
    now = utc_now()
    access_time = now - timedelta(days=days_ago)
    freshness = FreshnessScore(
        base_relevance=1.0,
        last_accessed_at=access_time,
        last_reinforced_at=access_time,
        decay_rate=0.01,
    )
    graph_store.upsert_node(
        node_id=node_id,
        node_type=node_type,
        properties={
            "title": title,
            "description": description,
            "knowledge_source": KnowledgeSource.MANUAL.value,
            "confidence": confidence,
            "tier": tier,
            "freshness": freshness.model_dump(mode="json"),
            "tags": tags or [],
        },
    )


@pytest.mark.asyncio
async def test_query_by_type(query_service, graph_store):
    """Should return only nodes of the specified type."""
    _create_knowledge_node(graph_store, "concept:a", NodeType.CONCEPT.value, "Concept A")
    _create_knowledge_node(graph_store, "system:b", NodeType.SYSTEM.value, "System B")

    result = await query_service.query(
        ContextGraphQuery(node_types=[NodeType.CONCEPT.value])
    )

    assert len(result.nodes) == 1
    assert result.nodes[0].node_id == "concept:a"


@pytest.mark.asyncio
async def test_query_keyword_matching(query_service, graph_store):
    """Should filter by keyword match in title/description."""
    _create_knowledge_node(
        graph_store, "concept:graph", title="Context Graph",
        description="A traversable knowledge graph",
    )
    _create_knowledge_node(
        graph_store, "concept:other", title="Unrelated Concept",
        description="Something else entirely",
    )

    result = await query_service.query(
        ContextGraphQuery(keywords=["graph"])
    )

    assert len(result.nodes) == 1
    assert result.nodes[0].node_id == "concept:graph"


@pytest.mark.asyncio
async def test_query_filters_cold_by_default(query_service, graph_store):
    """COLD nodes should be excluded unless include_cold is True."""
    _create_knowledge_node(
        graph_store, "concept:cold", tier=KnowledgeTier.COLD.value, title="Cold One",
    )
    _create_knowledge_node(
        graph_store, "concept:hot", tier=KnowledgeTier.HOT.value, title="Hot One",
    )

    # Default: exclude cold
    result = await query_service.query(ContextGraphQuery())
    node_ids = {n.node_id for n in result.nodes}
    assert "concept:cold" not in node_ids
    assert "concept:hot" in node_ids

    # Include cold
    result = await query_service.query(ContextGraphQuery(include_cold=True))
    node_ids = {n.node_id for n in result.nodes}
    assert "concept:cold" in node_ids


@pytest.mark.asyncio
async def test_query_min_confidence(query_service, graph_store):
    """Should filter out nodes below min_confidence."""
    _create_knowledge_node(graph_store, "concept:high", confidence=0.9, title="High")
    _create_knowledge_node(graph_store, "concept:low", confidence=0.2, title="Low")

    result = await query_service.query(
        ContextGraphQuery(min_confidence=0.5)
    )

    node_ids = {n.node_id for n in result.nodes}
    assert "concept:high" in node_ids
    assert "concept:low" not in node_ids


@pytest.mark.asyncio
async def test_query_tag_filter(query_service, graph_store):
    """Should filter by tag intersection."""
    _create_knowledge_node(
        graph_store, "concept:tagged", title="Tagged", tags=["important", "review"],
    )
    _create_knowledge_node(
        graph_store, "concept:untagged", title="Untagged", tags=["other"],
    )

    result = await query_service.query(
        ContextGraphQuery(tags=["important"])
    )

    assert len(result.nodes) == 1
    assert result.nodes[0].node_id == "concept:tagged"


@pytest.mark.asyncio
async def test_query_relevance_scoring(query_service, graph_store):
    """Nodes with higher freshness and confidence should score higher."""
    _create_knowledge_node(
        graph_store, "concept:fresh", title="Fresh Node",
        description="A fresh node for testing",
        confidence=0.9, days_ago=1,
    )
    _create_knowledge_node(
        graph_store, "concept:stale", title="Stale Node",
        description="A stale node for testing",
        confidence=0.5, days_ago=200,
    )

    result = await query_service.query(
        ContextGraphQuery(keywords=["node"])
    )

    assert len(result.nodes) == 2
    # Fresh + high confidence should score higher
    assert result.nodes[0].node_id == "concept:fresh"
    assert result.nodes[0].relevance_score > result.nodes[1].relevance_score


@pytest.mark.asyncio
async def test_query_limit(query_service, graph_store):
    """Should respect the limit parameter."""
    for i in range(10):
        _create_knowledge_node(
            graph_store, f"concept:{i}", title=f"Concept {i}",
        )

    result = await query_service.query(ContextGraphQuery(limit=3))

    assert len(result.nodes) <= 3
    assert result.total_candidates >= 3  # At least more than limit


@pytest.mark.asyncio
async def test_find_relevant_knowledge(query_service, graph_store):
    """find_relevant_knowledge should return matching knowledge nodes."""
    _create_knowledge_node(
        graph_store, "concept:auth", title="Authentication",
        description="User authentication system",
    )
    _create_knowledge_node(
        graph_store, "concept:deploy", title="Deployment",
        description="CI/CD deployment pipeline",
    )

    results = await query_service.find_relevant_knowledge("authentication")

    assert len(results) >= 1
    assert results[0].node_id == "concept:auth"


@pytest.mark.asyncio
async def test_find_similar_trajectories(query_service, graph_store):
    """find_similar_trajectories should match by intent keywords."""
    now = utc_now()
    graph_store.upsert_node(
        node_id="trajectory:t1",
        node_type=NodeType.TRAJECTORY.value,
        properties={
            "trace_id": "t1",
            "intent": "Check outstanding tasks",
            "outcome_summary": "Listed 3 tasks",
            "outcome_status": "completed",
            "created_at": now.isoformat(),
        },
    )
    graph_store.upsert_node(
        node_id="trajectory:t2",
        node_type=NodeType.TRAJECTORY.value,
        properties={
            "trace_id": "t2",
            "intent": "Deploy application",
            "outcome_summary": "Deployed to prod",
            "outcome_status": "completed",
            "created_at": now.isoformat(),
        },
    )

    results = await query_service.find_similar_trajectories("check tasks")

    assert len(results) >= 1
    assert results[0].node_id == "trajectory:t1"


@pytest.mark.asyncio
async def test_get_entity_history(query_service, graph_store):
    """get_entity_history should return trajectories that touched an entity."""
    # Create entity
    graph_store.upsert_node(
        node_id="note:readme",
        node_type="note",
        properties={"title": "README"},
    )

    # Create trajectory that touched it
    now = utc_now()
    graph_store.upsert_node(
        node_id="trajectory:touch1",
        node_type=NodeType.TRAJECTORY.value,
        properties={
            "trace_id": "touch1",
            "intent": "Update README",
            "created_at": now.isoformat(),
        },
    )
    graph_store.upsert_edge(
        source_id="trajectory:touch1",
        target_id="note:readme",
        edge_type=EdgeType.TRAJECTORY_TOUCHED.value,
        properties={},
    )

    results = await query_service.get_entity_history("note:readme")

    assert len(results) == 1
    assert results[0].node_id == "trajectory:touch1"


@pytest.mark.asyncio
async def test_record_access_updates_freshness(query_service, graph_store):
    """record_access should update last_accessed_at and increment access_count."""
    now = utc_now()
    old_time = now - timedelta(days=30)
    freshness = FreshnessScore(
        last_accessed_at=old_time,
        last_reinforced_at=old_time,
        access_count=5,
    )
    graph_store.upsert_node(
        node_id="concept:accessed",
        node_type=NodeType.CONCEPT.value,
        properties={
            "title": "To Access",
            "freshness": freshness.model_dump(mode="json"),
        },
    )

    await query_service.record_access("concept:accessed")

    node = graph_store.get_node("concept:accessed")
    updated_freshness = node["properties"]["freshness"]
    assert updated_freshness["access_count"] == 6
    # last_accessed_at should be more recent than old_time
    new_access = datetime.fromisoformat(updated_freshness["last_accessed_at"])
    assert new_access > old_time


@pytest.mark.asyncio
async def test_get_domain_context(query_service, graph_store):
    """get_domain_context should return a subgraph around a domain node."""
    graph_store.upsert_node(
        node_id="domain:eng",
        node_type=NodeType.DOMAIN.value,
        properties={"title": "Engineering"},
    )
    graph_store.upsert_node(
        node_id="system:postgres",
        node_type=NodeType.SYSTEM.value,
        properties={"title": "PostgreSQL"},
    )
    graph_store.upsert_edge(
        source_id="domain:eng",
        target_id="system:postgres",
        edge_type=EdgeType.DOMAIN_CONTAINS.value,
        properties={},
    )

    result = await query_service.get_domain_context("domain:eng", depth=1)

    assert len(result.nodes) >= 1
    node_ids = {n.node_id for n in result.nodes}
    assert "domain:eng" in node_ids
