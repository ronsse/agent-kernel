"""Tests for compaction strategies - DeterministicCompaction + TrajectoryCompaction."""

from __future__ import annotations

import pytest

from agent_kernel.context_graph.compaction import (
    DeterministicCompaction,
    TrajectoryCompaction,
)
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.memory.graph_store import SQLiteGraphStore


@pytest.fixture
def graph_store(tmp_path):
    store = SQLiteGraphStore(tmp_path / "test.db")
    yield store
    store.close()


class TestDeterministicCompaction:
    """Tests for DeterministicCompaction strategy."""

    @pytest.fixture
    def compactor(self):
        return DeterministicCompaction()

    @pytest.mark.asyncio
    async def test_compact_basic(self, compactor):
        """Should merge nodes with concatenated descriptions and averaged confidence."""
        nodes = [
            {
                "node_id": "concept:a",
                "node_type": "concept",
                "properties": {
                    "title": "First",
                    "description": "Desc A",
                    "confidence": 0.8,
                    "tags": ["tag1"],
                    "source_refs": ["ref1"],
                },
            },
            {
                "node_id": "concept:b",
                "node_type": "concept",
                "properties": {
                    "title": "Second",
                    "description": "Desc B",
                    "confidence": 0.6,
                    "tags": ["tag2"],
                    "source_refs": ["ref2"],
                },
            },
        ]

        result = await compactor.compact(nodes, [])

        assert result["node_type"] == NodeType.SUMMARY.value
        props = result["properties"]
        assert props["title"] == "Summary: Second"  # Most recent title
        assert "Desc A" in props["description"]
        assert "Desc B" in props["description"]
        assert props["confidence"] == pytest.approx(0.7, abs=0.01)
        assert props["original_count"] == 2
        assert "ref1" in props["source_refs"]
        assert "ref2" in props["source_refs"]

    @pytest.mark.asyncio
    async def test_compact_unions_tags(self, compactor):
        """Should union all tags from source nodes."""
        nodes = [
            {
                "node_id": "a",
                "node_type": "concept",
                "properties": {
                    "title": "A",
                    "tags": ["shared", "alpha"],
                    "confidence": 1.0,
                },
            },
            {
                "node_id": "b",
                "node_type": "concept",
                "properties": {
                    "title": "B",
                    "tags": ["shared", "beta"],
                    "confidence": 1.0,
                },
            },
        ]

        result = await compactor.compact(nodes, [])
        tags = result["properties"]["tags"]
        assert set(tags) == {"shared", "alpha", "beta"}

    @pytest.mark.asyncio
    async def test_compact_preserves_node_ids(self, compactor):
        """Summary should contain IDs of all summarized nodes."""
        nodes = [
            {"node_id": "c:1", "node_type": "concept", "properties": {"title": "A", "confidence": 1.0}},
            {"node_id": "c:2", "node_type": "concept", "properties": {"title": "B", "confidence": 1.0}},
            {"node_id": "c:3", "node_type": "concept", "properties": {"title": "C", "confidence": 1.0}},
        ]

        result = await compactor.compact(nodes, [])
        assert result["properties"]["summarized_node_ids"] == ["c:1", "c:2", "c:3"]

    @pytest.mark.asyncio
    async def test_compact_empty_raises(self, compactor):
        """Compacting empty list should raise ValueError."""
        with pytest.raises(ValueError, match="Cannot compact empty"):
            await compactor.compact([], [])

    @pytest.mark.asyncio
    async def test_compact_no_descriptions(self, compactor):
        """Should handle nodes without descriptions."""
        nodes = [
            {"node_id": "a", "node_type": "concept", "properties": {"title": "A", "confidence": 1.0}},
            {"node_id": "b", "node_type": "concept", "properties": {"title": "B", "confidence": 1.0}},
        ]

        result = await compactor.compact(nodes, [])
        assert result["properties"]["description"] is None

    @pytest.mark.asyncio
    async def test_compact_single_node(self, compactor):
        """Should handle compacting a single node."""
        nodes = [
            {
                "node_id": "c:only",
                "node_type": "concept",
                "properties": {
                    "title": "Only One",
                    "description": "Sole description",
                    "confidence": 0.9,
                    "tags": ["solo"],
                },
            },
        ]

        result = await compactor.compact(nodes, [])
        assert result["properties"]["title"] == "Summary: Only One"
        assert result["properties"]["original_count"] == 1
        assert result["properties"]["confidence"] == pytest.approx(0.9)


class TestTrajectoryCompaction:
    """Tests for TrajectoryCompaction strategy."""

    @pytest.mark.asyncio
    async def test_compact_trajectory_removes_events(self, graph_store):
        """Compacting should remove decision event nodes."""
        compactor = TrajectoryCompaction(graph_store)

        # Create trajectory
        graph_store.upsert_node(
            node_id="trajectory:t1",
            node_type=NodeType.TRAJECTORY.value,
            properties={"trace_id": "t1", "intent": "Test"},
        )

        # Create events and link them
        for i in range(3):
            event_id = f"decision_event:t1:{i}"
            graph_store.upsert_node(
                node_id=event_id,
                node_type=NodeType.DECISION_EVENT.value,
                properties={"step_order": i},
            )
            graph_store.upsert_edge(
                source_id="trajectory:t1",
                target_id=event_id,
                edge_type=EdgeType.TRAJECTORY_DECIDED.value,
                properties={},
            )

        result = await compactor.compact_trajectory("trajectory:t1")

        assert result is True

        # Trajectory should still exist
        traj = graph_store.get_node("trajectory:t1")
        assert traj is not None
        assert traj["properties"]["compacted"] is True
        assert traj["properties"]["events_removed"] == 3

        # Events should be deleted
        for i in range(3):
            assert graph_store.get_node(f"decision_event:t1:{i}") is None

    @pytest.mark.asyncio
    async def test_compact_already_compacted(self, graph_store):
        """Should return False for already compacted trajectories."""
        compactor = TrajectoryCompaction(graph_store)

        graph_store.upsert_node(
            node_id="trajectory:t2",
            node_type=NodeType.TRAJECTORY.value,
            properties={"trace_id": "t2", "compacted": True},
        )

        result = await compactor.compact_trajectory("trajectory:t2")
        assert result is False

    @pytest.mark.asyncio
    async def test_compact_nonexistent(self, graph_store):
        """Should return False for nonexistent trajectory."""
        compactor = TrajectoryCompaction(graph_store)

        result = await compactor.compact_trajectory("trajectory:nope")
        assert result is False

    @pytest.mark.asyncio
    async def test_compact_no_events(self, graph_store):
        """Should handle trajectory with no decision events."""
        compactor = TrajectoryCompaction(graph_store)

        graph_store.upsert_node(
            node_id="trajectory:empty",
            node_type=NodeType.TRAJECTORY.value,
            properties={"trace_id": "empty", "intent": "No events"},
        )

        result = await compactor.compact_trajectory("trajectory:empty")

        assert result is True
        traj = graph_store.get_node("trajectory:empty")
        assert traj["properties"]["compacted"] is True
        assert traj["properties"]["events_removed"] == 0
