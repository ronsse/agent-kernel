"""Tests for ContextAssembler context graph integration (v1.0.6).

Tests the _search_context_graph method and context graph query service
integration into the assembly pipeline.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from agent_kernel.context.assembler import ContextAssembler
from agent_kernel.context_graph.query import ContextGraphQueryService
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.context import RefType
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


@pytest.fixture
def assembler(query_service):
    return ContextAssembler(context_graph_query=query_service)


def _create_knowledge_node(
    graph_store,
    node_id: str,
    node_type: str,
    title: str,
    description: str | None = None,
    confidence: float = 1.0,
) -> None:
    """Helper to create a knowledge node."""
    now = utc_now()
    freshness = FreshnessScore(
        last_accessed_at=now,
        last_reinforced_at=now,
    )
    graph_store.upsert_node(
        node_id=node_id,
        node_type=node_type,
        properties={
            "title": title,
            "description": description,
            "knowledge_source": KnowledgeSource.MANUAL.value,
            "confidence": confidence,
            "tier": KnowledgeTier.HOT.value,
            "freshness": freshness.model_dump(mode="json"),
            "tags": [],
        },
    )


@pytest.mark.asyncio
async def test_search_context_graph_returns_knowledge_items(assembler, graph_store):
    """_search_context_graph should return knowledge nodes as ContextItems."""
    _create_knowledge_node(
        graph_store,
        "concept:auth",
        NodeType.CONCEPT.value,
        "Authentication",
        "User authentication system",
    )

    items, query_record = await assembler._search_context_graph(
        "authentication", limit=10,
    )

    assert len(items) >= 1
    assert query_record.source == "context_graph"
    assert query_record.results_count >= 1

    # Check item properties
    knowledge_items = [i for i in items if i.included_reason == "context_graph_knowledge"]
    assert len(knowledge_items) >= 1
    assert knowledge_items[0].ref.ref_type == RefType.KNOWLEDGE
    assert "Authentication" in knowledge_items[0].excerpt


@pytest.mark.asyncio
async def test_search_context_graph_returns_trajectory_items(assembler, graph_store):
    """_search_context_graph should return trajectory matches as episodic items."""
    now = utc_now()
    graph_store.upsert_node(
        node_id="trajectory:t1",
        node_type=NodeType.TRAJECTORY.value,
        properties={
            "trace_id": "t1",
            "intent": "Review authentication code",
            "outcome_summary": "Found 2 auth issues",
            "outcome_status": "completed",
            "created_at": now.isoformat(),
            "capabilities_used": ["github.review"],
        },
    )

    items, query_record = await assembler._search_context_graph(
        "review authentication", limit=20,
    )

    episodic_items = [i for i in items if i.included_reason == "context_graph_episodic"]
    assert len(episodic_items) >= 1
    assert episodic_items[0].ref.ref_type == RefType.TRAJECTORY
    assert "authentication" in episodic_items[0].excerpt.lower()


@pytest.mark.asyncio
async def test_search_context_graph_empty_with_no_matches(assembler, graph_store):
    """_search_context_graph should return empty for no keyword matches."""
    _create_knowledge_node(
        graph_store,
        "concept:db",
        NodeType.CONCEPT.value,
        "Database Design",
        "PostgreSQL schema patterns",
    )

    items, query_record = await assembler._search_context_graph(
        "completely unrelated topic xyz123", limit=10,
    )

    assert len(items) == 0
    assert query_record.results_count == 0


@pytest.mark.asyncio
async def test_search_context_graph_no_service():
    """Without ContextGraphQueryService, should return empty results."""
    assembler = ContextAssembler()

    items, query_record = await assembler._search_context_graph(
        "anything", limit=10,
    )

    assert items == []
    assert query_record.results_count == 0
    assert query_record.source == "context_graph"


@pytest.mark.asyncio
async def test_search_context_graph_records_access(assembler, graph_store):
    """_search_context_graph should record access for freshness tracking."""
    _create_knowledge_node(
        graph_store,
        "concept:tracked",
        NodeType.CONCEPT.value,
        "Tracked Node",
        "This node's access should be recorded",
    )

    # Get initial access count
    node_before = graph_store.get_node("concept:tracked")
    initial_count = node_before["properties"]["freshness"]["access_count"]

    await assembler._search_context_graph("tracked node", limit=10)

    # Access count should have incremented
    node_after = graph_store.get_node("concept:tracked")
    assert node_after["properties"]["freshness"]["access_count"] > initial_count


@pytest.mark.asyncio
async def test_format_knowledge_excerpt():
    """_format_knowledge_excerpt should combine title and description."""
    excerpt = ContextAssembler._format_knowledge_excerpt({
        "title": "Auth System",
        "description": "Handles user login and session management",
    })

    assert "Auth System" in excerpt
    assert "Handles user login" in excerpt


@pytest.mark.asyncio
async def test_format_knowledge_excerpt_no_desc():
    """_format_knowledge_excerpt should handle missing description."""
    excerpt = ContextAssembler._format_knowledge_excerpt({
        "title": "Simple Node",
    })

    assert "Simple Node" in excerpt


@pytest.mark.asyncio
async def test_format_trajectory_excerpt():
    """_format_trajectory_excerpt should format trajectory info."""
    excerpt = ContextAssembler._format_trajectory_excerpt({
        "intent": "Check tasks",
        "outcome_summary": "Listed 3 tasks",
        "outcome_status": "completed",
        "capabilities_used": ["tasks.list"],
    })

    assert "Check tasks" in excerpt
    assert "Listed 3 tasks" in excerpt
    assert "completed" in excerpt
    assert "tasks.list" in excerpt


@pytest.mark.asyncio
async def test_knowledge_relevance_scaling(assembler, graph_store):
    """Knowledge items should have relevance scaled by 0.8 factor."""
    _create_knowledge_node(
        graph_store,
        "concept:scaled",
        NodeType.CONCEPT.value,
        "Scaled Node",
        "Test relevance scaling",
        confidence=1.0,
    )

    items, _ = await assembler._search_context_graph("scaled node", limit=10)

    knowledge_items = [i for i in items if i.included_reason == "context_graph_knowledge"]
    if knowledge_items:
        # Relevance should be scaled down from raw score
        assert knowledge_items[0].relevance_score <= 0.8


@pytest.mark.asyncio
async def test_trajectory_relevance_scaling(assembler, graph_store):
    """Trajectory items should have relevance scaled by 0.7 factor."""
    now = utc_now()
    graph_store.upsert_node(
        node_id="trajectory:scaled",
        node_type=NodeType.TRAJECTORY.value,
        properties={
            "trace_id": "scaled",
            "intent": "Scale test query",
            "outcome_summary": "Completed scale test",
            "outcome_status": "completed",
            "created_at": now.isoformat(),
        },
    )

    items, _ = await assembler._search_context_graph("scale test query", limit=20)

    episodic_items = [i for i in items if i.included_reason == "context_graph_episodic"]
    if episodic_items:
        # Relevance should be scaled down
        assert episodic_items[0].relevance_score <= 0.7
