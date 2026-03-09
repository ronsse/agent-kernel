"""Tests for ExperienceBridge - one-way sync from ExperienceStore to Context Graph."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_kernel.context_graph.experience_bridge import ExperienceBridge
from agent_kernel.core.schemas.context import ContextRef, RefType
from agent_kernel.core.schemas.experience import LessonLearned, LessonScope, Playbook
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.core.schemas.knowledge import KnowledgeSource
from agent_kernel.memory.graph_store import SQLiteGraphStore


@pytest.fixture
def graph_store(tmp_path):
    store = SQLiteGraphStore(tmp_path / "test.db")
    yield store
    store.close()


@pytest.fixture
def bridge(graph_store):
    return ExperienceBridge(graph_store=graph_store)


@pytest.mark.asyncio
async def test_sync_lesson_creates_insight(bridge, graph_store):
    """sync_lesson should create an INSIGHT node."""
    now = datetime.now(UTC)
    lesson = LessonLearned(
        lesson_id="lesson_eb_001",
        title="Use backoff for retries",
        lesson_text="Exponential backoff prevents rate limiting",
        scope=LessonScope(workflow_id="api_sync"),
        source_trace_ids=["trace_100"],
        confidence=0.85,
        created_at=now,
        updated_at=now,
    )

    node_id = await bridge.sync_lesson(lesson)

    assert node_id == "insight:lesson_eb_001"
    node = graph_store.get_node(node_id)
    assert node is not None
    assert node["node_type"] == NodeType.INSIGHT.value
    assert node["properties"]["title"] == "Use backoff for retries"


@pytest.mark.asyncio
async def test_sync_playbook_creates_practice(bridge, graph_store):
    """sync_playbook should create a PRACTICE node."""
    now = datetime.now(UTC)
    playbook = Playbook(
        playbook_id="pb_eb_001",
        name="Daily Sync Playbook",
        description="Steps for daily sync workflow",
        derived_from_lessons=["lesson_eb_001"],
        created_at=now,
        updated_at=now,
    )

    node_id = await bridge.sync_playbook(playbook)

    assert node_id == "practice:pb_eb_001"
    node = graph_store.get_node(node_id)
    assert node is not None
    assert node["node_type"] == NodeType.PRACTICE.value
    assert node["properties"]["title"] == "Daily Sync Playbook"


@pytest.mark.asyncio
async def test_detect_patterns_creates_pattern_nodes(bridge, graph_store):
    """detect_patterns should create PATTERN nodes for high-weight co-occurrences."""
    # Create entity nodes
    graph_store.upsert_node(
        node_id="concept:entity_a",
        node_type=NodeType.CONCEPT.value,
        properties={"title": "Entity A"},
    )
    graph_store.upsert_node(
        node_id="concept:entity_b",
        node_type=NodeType.CONCEPT.value,
        properties={"title": "Entity B"},
    )

    # Create CO_OCCURS_WITH edge with weight >= 3
    graph_store.upsert_edge(
        source_id="concept:entity_a",
        target_id="concept:entity_b",
        edge_type=EdgeType.CO_OCCURS_WITH.value,
        properties={"weight": 5},
    )

    pattern_ids = await bridge.detect_patterns(min_occurrences=3)

    assert len(pattern_ids) >= 1

    # Verify pattern node was created
    pattern_node = graph_store.get_node(pattern_ids[0])
    assert pattern_node is not None
    assert pattern_node["node_type"] == NodeType.PATTERN.value
    assert pattern_node["properties"]["occurrence_count"] == 5
    assert "Entity A" in pattern_node["properties"]["title"]
    assert "Entity B" in pattern_node["properties"]["title"]


@pytest.mark.asyncio
async def test_detect_patterns_skips_low_weight(bridge, graph_store):
    """detect_patterns should skip co-occurrences below min_occurrences."""
    graph_store.upsert_node(
        node_id="concept:low_a",
        node_type=NodeType.CONCEPT.value,
        properties={"title": "Low A"},
    )
    graph_store.upsert_node(
        node_id="concept:low_b",
        node_type=NodeType.CONCEPT.value,
        properties={"title": "Low B"},
    )
    graph_store.upsert_edge(
        source_id="concept:low_a",
        target_id="concept:low_b",
        edge_type=EdgeType.CO_OCCURS_WITH.value,
        properties={"weight": 1},
    )

    pattern_ids = await bridge.detect_patterns(min_occurrences=3)

    assert len(pattern_ids) == 0


@pytest.mark.asyncio
async def test_detect_patterns_links_to_source_entities(bridge, graph_store):
    """Pattern nodes should be linked to their source entities."""
    graph_store.upsert_node(
        node_id="concept:src_a",
        node_type=NodeType.CONCEPT.value,
        properties={"title": "Source A"},
    )
    graph_store.upsert_node(
        node_id="concept:src_b",
        node_type=NodeType.CONCEPT.value,
        properties={"title": "Source B"},
    )
    graph_store.upsert_edge(
        source_id="concept:src_a",
        target_id="concept:src_b",
        edge_type=EdgeType.CO_OCCURS_WITH.value,
        properties={"weight": 4},
    )

    pattern_ids = await bridge.detect_patterns(min_occurrences=3)

    assert len(pattern_ids) >= 1

    # Check PATTERN_OBSERVED_IN edges
    edges = graph_store.get_edges(
        pattern_ids[0],
        direction="outgoing",
        edge_type=EdgeType.PATTERN_OBSERVED_IN.value,
    )
    target_ids = {e["target_id"] for e in edges}
    assert "concept:src_a" in target_ids
    assert "concept:src_b" in target_ids


@pytest.mark.asyncio
async def test_detect_patterns_updates_existing(bridge, graph_store):
    """detect_patterns should update occurrence_count on existing patterns."""
    graph_store.upsert_node(
        node_id="concept:upd_a",
        node_type=NodeType.CONCEPT.value,
        properties={"title": "Update A"},
    )
    graph_store.upsert_node(
        node_id="concept:upd_b",
        node_type=NodeType.CONCEPT.value,
        properties={"title": "Update B"},
    )
    graph_store.upsert_edge(
        source_id="concept:upd_a",
        target_id="concept:upd_b",
        edge_type=EdgeType.CO_OCCURS_WITH.value,
        properties={"weight": 3},
    )

    # First detection
    pattern_ids_1 = await bridge.detect_patterns(min_occurrences=3)
    assert len(pattern_ids_1) >= 1

    # Increase weight
    graph_store.upsert_edge(
        source_id="concept:upd_a",
        target_id="concept:upd_b",
        edge_type=EdgeType.CO_OCCURS_WITH.value,
        properties={"weight": 7},
    )

    # Second detection should update, not create new
    pattern_ids_2 = await bridge.detect_patterns(min_occurrences=3)

    # Should not create a new pattern (returns empty because existing was updated)
    assert len(pattern_ids_2) == 0

    # Existing pattern should have updated count
    pattern = graph_store.get_node(pattern_ids_1[0])
    assert pattern["properties"]["occurrence_count"] == 7


@pytest.mark.asyncio
async def test_detect_patterns_no_entities(bridge, graph_store):
    """detect_patterns with no entities should return empty."""
    pattern_ids = await bridge.detect_patterns(min_occurrences=3)
    assert pattern_ids == []
