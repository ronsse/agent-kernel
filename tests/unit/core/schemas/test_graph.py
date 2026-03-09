"""Unit tests for Graph ontology schemas."""


import pytest

from agent_kernel.core.schemas.base import SCHEMA_VERSION, utc_now
from agent_kernel.core.schemas.graph import (
    EdgeType,
    GraphEdge,
    GraphNode,
    NodeType,
    TypedGraphSlice,
)


class TestNodeType:
    """Tests for NodeType enum."""

    def test_all_types_exist(self) -> None:
        """Test all expected node types exist."""
        expected = {"note", "tag", "task", "project", "trace", "calendar_event", "person"}
        actual = {t.value for t in NodeType}
        assert expected.issubset(actual)


class TestEdgeType:
    """Tests for EdgeType enum."""

    def test_note_edges_exist(self) -> None:
        """Test note-related edge types exist."""
        note_edges = {
            EdgeType.NOTE_LINKS_TO_NOTE,
            EdgeType.NOTE_TAGGED_WITH_TAG,
            EdgeType.NOTE_HAS_TASK,
            EdgeType.NOTE_MENTIONS_PERSON,
        }
        assert all(e in EdgeType for e in note_edges)

    def test_trace_edges_exist(self) -> None:
        """Test trace provenance edge types exist."""
        trace_edges = {
            EdgeType.TRACE_USED_CONTEXT,
            EdgeType.TRACE_PRODUCED_ARTIFACT,
        }
        assert all(e in EdgeType for e in trace_edges)


class TestGraphNode:
    """Tests for GraphNode schema."""

    def test_basic_creation(self) -> None:
        """Test creating a basic GraphNode."""
        node = GraphNode(
            node_id="note_01ABC123",
            node_type=NodeType.NOTE,
            label="My Note",
        )
        assert node.node_id == "note_01ABC123"
        assert node.node_type == NodeType.NOTE
        assert node.label == "My Note"
        assert node.schema_version == SCHEMA_VERSION

    def test_with_properties(self) -> None:
        """Test GraphNode with custom properties."""
        node = GraphNode(
            node_id="task_01XYZ",
            node_type=NodeType.TASK,
            properties={
                "status": "pending",
                "priority": "high",
            },
        )
        assert node.properties["status"] == "pending"
        assert node.properties["priority"] == "high"

    def test_with_uri(self) -> None:
        """Test GraphNode with external URI."""
        node = GraphNode(
            node_id="note_123",
            node_type=NodeType.NOTE,
            uri="/vault/notes/my-note.md",
        )
        assert node.uri == "/vault/notes/my-note.md"


class TestGraphEdge:
    """Tests for GraphEdge schema."""

    def test_basic_creation(self) -> None:
        """Test creating a basic GraphEdge."""
        edge = GraphEdge(
            edge_id="edge_001",
            edge_type=EdgeType.NOTE_LINKS_TO_NOTE,
            source_id="note_001",
            target_id="note_002",
        )
        assert edge.source_id == "note_001"
        assert edge.target_id == "note_002"
        assert edge.edge_type == EdgeType.NOTE_LINKS_TO_NOTE
        assert edge.confidence is None
        assert edge.is_auto_extracted is False

    def test_with_confidence(self) -> None:
        """Test auto-extracted edge with confidence."""
        edge = GraphEdge(
            edge_id="edge_002",
            edge_type=EdgeType.NOTE_MENTIONS_PERSON,
            source_id="note_001",
            target_id="person_001",
            confidence=0.85,
            extracted_by="llm_enrichment",
        )
        assert edge.confidence == 0.85
        assert edge.is_auto_extracted is True
        assert edge.extracted_by == "llm_enrichment"

    def test_confidence_bounds(self) -> None:
        """Test confidence is bounded 0-1."""
        with pytest.raises(ValueError):
            GraphEdge(
                edge_id="edge",
                edge_type=EdgeType.NOTE_LINKS_TO_NOTE,
                source_id="a",
                target_id="b",
                confidence=1.5,
            )

    def test_temporal_validity(self) -> None:
        """Test edge with validity interval."""
        now = utc_now()
        edge = GraphEdge(
            edge_id="edge_003",
            edge_type=EdgeType.TASK_ASSIGNED_TO_PERSON,
            source_id="task_001",
            target_id="person_001",
            valid_from=now,
        )
        assert edge.valid_from == now
        assert edge.is_valid is True


class TestTypedGraphSlice:
    """Tests for TypedGraphSlice schema."""

    def test_empty_slice(self) -> None:
        """Test empty graph slice."""
        slice = TypedGraphSlice()
        assert slice.node_count == 0
        assert slice.edge_count == 0

    def test_with_nodes_and_edges(self) -> None:
        """Test slice with nodes and edges."""
        nodes = [
            GraphNode(node_id="note_001", node_type=NodeType.NOTE),
            GraphNode(node_id="tag_001", node_type=NodeType.TAG),
        ]
        edges = [
            GraphEdge(
                edge_id="edge_001",
                edge_type=EdgeType.NOTE_TAGGED_WITH_TAG,
                source_id="note_001",
                target_id="tag_001",
            ),
        ]
        slice = TypedGraphSlice(nodes=nodes, edges=edges)

        assert slice.node_count == 2
        assert slice.edge_count == 1

    def test_get_node(self) -> None:
        """Test getting a node by ID."""
        nodes = [
            GraphNode(node_id="note_001", node_type=NodeType.NOTE, label="Test"),
        ]
        slice = TypedGraphSlice(nodes=nodes)

        node = slice.get_node("note_001")
        assert node is not None
        assert node.label == "Test"

        assert slice.get_node("nonexistent") is None

    def test_get_edges_from(self) -> None:
        """Test getting edges from a source node."""
        edges = [
            GraphEdge(
                edge_id="e1",
                edge_type=EdgeType.NOTE_LINKS_TO_NOTE,
                source_id="note_001",
                target_id="note_002",
            ),
            GraphEdge(
                edge_id="e2",
                edge_type=EdgeType.NOTE_LINKS_TO_NOTE,
                source_id="note_001",
                target_id="note_003",
            ),
            GraphEdge(
                edge_id="e3",
                edge_type=EdgeType.NOTE_LINKS_TO_NOTE,
                source_id="note_002",
                target_id="note_001",
            ),
        ]
        slice = TypedGraphSlice(edges=edges)

        from_001 = slice.get_edges_from("note_001")
        assert len(from_001) == 2

    def test_get_edges_to(self) -> None:
        """Test getting edges to a target node."""
        edges = [
            GraphEdge(
                edge_id="e1",
                edge_type=EdgeType.NOTE_LINKS_TO_NOTE,
                source_id="note_001",
                target_id="note_002",
            ),
            GraphEdge(
                edge_id="e2",
                edge_type=EdgeType.NOTE_LINKS_TO_NOTE,
                source_id="note_003",
                target_id="note_002",
            ),
        ]
        slice = TypedGraphSlice(edges=edges)

        to_002 = slice.get_edges_to("note_002")
        assert len(to_002) == 2
