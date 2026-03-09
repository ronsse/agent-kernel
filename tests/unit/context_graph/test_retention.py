"""Tests for RetentionExecutor - tiering, pruning, and compaction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_kernel.context_graph.retention import RetentionExecutor
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.core.schemas.knowledge import (
    FreshnessScore,
    KnowledgeSource,
    KnowledgeTier,
)
from agent_kernel.core.schemas.retention import RetentionPolicy
from agent_kernel.memory.graph_store import SQLiteGraphStore


@pytest.fixture
def graph_store(tmp_path):
    store = SQLiteGraphStore(tmp_path / "test.db")
    yield store
    store.close()


@pytest.fixture
def executor(graph_store):
    return RetentionExecutor(graph_store=graph_store)


def _create_knowledge_node(
    graph_store,
    node_id: str,
    node_type: str = NodeType.CONCEPT.value,
    title: str = "Test",
    tier: str = KnowledgeTier.HOT.value,
    confidence: float = 1.0,
    pinned: bool = False,
    last_accessed_days_ago: int = 0,
) -> None:
    """Helper to create a knowledge node with specified freshness."""
    now = utc_now()
    access_time = now - timedelta(days=last_accessed_days_ago)
    freshness = FreshnessScore(
        base_relevance=1.0,
        last_accessed_at=access_time,
        last_reinforced_at=access_time,
        decay_rate=0.01,
        pinned=pinned,
    )
    graph_store.upsert_node(
        node_id=node_id,
        node_type=node_type,
        properties={
            "title": title,
            "knowledge_source": KnowledgeSource.MANUAL.value,
            "confidence": confidence,
            "tier": tier,
            "freshness": freshness.model_dump(mode="json"),
            "tags": [],
        },
    )


@pytest.mark.asyncio
async def test_tier_hot_to_warm(executor, graph_store):
    """Nodes accessed > hot_days ago should transition from HOT to WARM."""
    _create_knowledge_node(
        graph_store,
        node_id="concept:old_hot",
        tier=KnowledgeTier.HOT.value,
        last_accessed_days_ago=100,  # Past default 90-day HOT window
    )

    result = await executor.tier_knowledge_nodes()

    assert result.transitions == 1
    assert result.warm_count >= 1

    node = graph_store.get_node("concept:old_hot")
    assert node["properties"]["tier"] == KnowledgeTier.WARM.value


@pytest.mark.asyncio
async def test_tier_warm_to_cold(executor, graph_store):
    """Nodes accessed > (hot_days + warm_days) ago should become COLD."""
    _create_knowledge_node(
        graph_store,
        node_id="concept:very_old",
        tier=KnowledgeTier.HOT.value,
        last_accessed_days_ago=500,  # Past 90+365=455 day window
    )

    result = await executor.tier_knowledge_nodes()

    assert result.cold_count >= 1

    node = graph_store.get_node("concept:very_old")
    assert node["properties"]["tier"] == KnowledgeTier.COLD.value


@pytest.mark.asyncio
async def test_tier_recent_stays_hot(executor, graph_store):
    """Recently accessed nodes should remain HOT."""
    _create_knowledge_node(
        graph_store,
        node_id="concept:recent",
        tier=KnowledgeTier.HOT.value,
        last_accessed_days_ago=10,
    )

    result = await executor.tier_knowledge_nodes()

    assert result.transitions == 0
    assert result.hot_count >= 1

    node = graph_store.get_node("concept:recent")
    assert node["properties"]["tier"] == KnowledgeTier.HOT.value


@pytest.mark.asyncio
async def test_pinned_nodes_stay_hot(executor, graph_store):
    """Pinned nodes should remain HOT regardless of age."""
    _create_knowledge_node(
        graph_store,
        node_id="concept:pinned_old",
        tier=KnowledgeTier.HOT.value,
        last_accessed_days_ago=999,
        pinned=True,
    )

    result = await executor.tier_knowledge_nodes()

    assert result.transitions == 0
    assert result.hot_count >= 1

    node = graph_store.get_node("concept:pinned_old")
    # Pinned should not be transitioned
    assert node["properties"]["tier"] == KnowledgeTier.HOT.value


@pytest.mark.asyncio
async def test_prune_low_confidence_cold(executor, graph_store):
    """COLD nodes below confidence threshold should be pruned."""
    _create_knowledge_node(
        graph_store,
        node_id="concept:low_conf",
        tier=KnowledgeTier.COLD.value,
        confidence=0.1,  # Below default 0.3 threshold
        last_accessed_days_ago=500,
    )

    result = await executor.prune_low_quality()

    assert result.nodes_pruned == 1
    assert graph_store.get_node("concept:low_conf") is None


@pytest.mark.asyncio
async def test_prune_skips_non_cold(executor, graph_store):
    """Only COLD nodes should be pruned, not HOT or WARM."""
    _create_knowledge_node(
        graph_store,
        node_id="concept:hot_low",
        tier=KnowledgeTier.HOT.value,
        confidence=0.1,
    )

    result = await executor.prune_low_quality()

    assert result.nodes_pruned == 0
    assert graph_store.get_node("concept:hot_low") is not None


@pytest.mark.asyncio
async def test_prune_skips_pinned(executor, graph_store):
    """Pinned COLD nodes should not be pruned."""
    _create_knowledge_node(
        graph_store,
        node_id="concept:pinned_cold",
        tier=KnowledgeTier.COLD.value,
        confidence=0.1,
        pinned=True,
        last_accessed_days_ago=500,
    )

    result = await executor.prune_low_quality()

    assert result.nodes_pruned == 0
    assert graph_store.get_node("concept:pinned_cold") is not None


@pytest.mark.asyncio
async def test_compact_cold_nodes_batch(executor, graph_store):
    """5+ COLD nodes of the same type should be compacted into a SUMMARY."""
    for i in range(6):
        _create_knowledge_node(
            graph_store,
            node_id=f"concept:cold_{i}",
            tier=KnowledgeTier.COLD.value,
            title=f"Cold Concept {i}",
            last_accessed_days_ago=500,
        )

    result = await executor.compact_cold_nodes()

    assert result.summaries_created == 1
    assert result.nodes_compacted == 6

    # Check that originals are marked as superseded
    node = graph_store.get_node("concept:cold_0")
    assert node["properties"].get("superseded_by") is not None


@pytest.mark.asyncio
async def test_compact_cold_skips_small_batches(executor, graph_store):
    """Fewer than 5 COLD nodes should not be compacted."""
    for i in range(3):
        _create_knowledge_node(
            graph_store,
            node_id=f"concept:cold_{i}",
            tier=KnowledgeTier.COLD.value,
            title=f"Cold Concept {i}",
            last_accessed_days_ago=500,
        )

    result = await executor.compact_cold_nodes()

    assert result.summaries_created == 0


@pytest.mark.asyncio
async def test_compact_old_trajectories(executor, graph_store):
    """Old trajectories should have their decision events removed."""
    now = utc_now()
    old_time = (now - timedelta(days=200)).isoformat()

    # Create trajectory
    graph_store.upsert_node(
        node_id="trajectory:old_trace",
        node_type=NodeType.TRAJECTORY.value,
        properties={
            "trace_id": "old_trace",
            "created_at": old_time,
            "intent": "Old task",
            "compacted": False,
        },
    )

    # Create decision events
    for i in range(3):
        event_id = f"decision_event:old_trace:{i}"
        graph_store.upsert_node(
            node_id=event_id,
            node_type=NodeType.DECISION_EVENT.value,
            properties={"step_order": i},
        )
        graph_store.upsert_edge(
            source_id="trajectory:old_trace",
            target_id=event_id,
            edge_type=EdgeType.TRAJECTORY_DECIDED.value,
            properties={},
        )

    result = await executor.compact_old_trajectories()

    assert result.trajectories_compacted == 1

    # Trajectory node should still exist but be marked compacted
    traj = graph_store.get_node("trajectory:old_trace")
    assert traj["properties"]["compacted"] is True

    # Decision events should be deleted
    for i in range(3):
        assert graph_store.get_node(f"decision_event:old_trace:{i}") is None


@pytest.mark.asyncio
async def test_compact_skips_recent_trajectories(executor, graph_store):
    """Recent trajectories should not be compacted."""
    now = utc_now()

    graph_store.upsert_node(
        node_id="trajectory:recent_trace",
        node_type=NodeType.TRAJECTORY.value,
        properties={
            "trace_id": "recent_trace",
            "created_at": now.isoformat(),
            "intent": "Recent task",
            "compacted": False,
        },
    )

    result = await executor.compact_old_trajectories()

    assert result.trajectories_compacted == 0


@pytest.mark.asyncio
async def test_run_full(executor, graph_store):
    """run_full should execute all retention operations and return a report."""
    # Create a node that will be tiered
    _create_knowledge_node(
        graph_store,
        node_id="concept:for_full",
        tier=KnowledgeTier.HOT.value,
        last_accessed_days_ago=10,
    )

    report = await executor.run_full()

    assert report.tiering is not None
    assert report.pruning is not None
    assert report.compaction is not None
    assert report.freshness_updated >= 0


@pytest.mark.asyncio
async def test_recalculate_freshness(executor, graph_store):
    """recalculate_freshness should process all knowledge nodes."""
    _create_knowledge_node(
        graph_store,
        node_id="concept:fresh_1",
        last_accessed_days_ago=5,
    )
    _create_knowledge_node(
        graph_store,
        node_id="concept:fresh_2",
        last_accessed_days_ago=30,
    )

    count = await executor.recalculate_freshness()

    assert count == 2
