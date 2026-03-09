"""Tests for graph store."""


from agent_kernel.memory.graph_store import SQLiteGraphStore


class TestSQLiteGraphStore:
    """Tests for SQLiteGraphStore."""

    def test_upsert_and_get_node(self, graph_store: SQLiteGraphStore):
        """Test creating and retrieving a node."""
        node_id = graph_store.upsert_node(
            node_id="node_1",
            node_type="project",
            properties={"name": "Agent Kernel", "status": "active"},
        )

        assert node_id == "node_1"

        node = graph_store.get_node("node_1")
        assert node is not None
        assert node["node_type"] == "project"
        assert node["properties"]["name"] == "Agent Kernel"

    def test_upsert_generates_id(self, graph_store: SQLiteGraphStore):
        """Test that upsert generates an ID if not provided."""
        node_id = graph_store.upsert_node(
            node_id=None,
            node_type="task",
            properties={"title": "Auto-ID task"},
        )

        assert node_id is not None
        assert len(node_id) == 26  # ULID

    def test_upsert_and_get_edge(self, graph_store: SQLiteGraphStore):
        """Test creating and retrieving an edge."""
        graph_store.upsert_node("n1", "project", {"name": "Project"})
        graph_store.upsert_node("n2", "task", {"name": "Task"})

        edge_id = graph_store.upsert_edge(
            source_id="n1",
            target_id="n2",
            edge_type="contains",
            properties={"weight": 1.0},
        )

        assert edge_id is not None

        edges = graph_store.get_edges("n1", direction="outgoing")
        assert len(edges) == 1
        assert edges[0]["target_id"] == "n2"
        assert edges[0]["edge_type"] == "contains"

    def test_get_subgraph(self, graph_store: SQLiteGraphStore):
        """Test getting a subgraph around seed nodes."""
        # Create a simple graph: p1 -> t1 -> t2
        graph_store.upsert_node("p1", "project", {"name": "Project 1"})
        graph_store.upsert_node("t1", "task", {"name": "Task 1"})
        graph_store.upsert_node("t2", "task", {"name": "Task 2"})
        graph_store.upsert_node("t3", "task", {"name": "Task 3"})  # Unconnected

        graph_store.upsert_edge("p1", "t1", "contains")
        graph_store.upsert_edge("t1", "t2", "depends_on")

        # Get subgraph from p1 with depth 2
        subgraph = graph_store.get_subgraph(["p1"], depth=2)

        node_ids = {n["node_id"] for n in subgraph["nodes"]}
        assert "p1" in node_ids
        assert "t1" in node_ids
        assert "t2" in node_ids
        assert "t3" not in node_ids  # Not connected

    def test_query_nodes_by_type(self, graph_store: SQLiteGraphStore):
        """Test querying nodes by type."""
        graph_store.upsert_node("p1", "project", {"name": "P1"})
        graph_store.upsert_node("p2", "project", {"name": "P2"})
        graph_store.upsert_node("t1", "task", {"name": "T1"})

        projects = graph_store.query(node_type="project")
        assert len(projects) == 2

        tasks = graph_store.query(node_type="task")
        assert len(tasks) == 1

    def test_delete_node(self, graph_store: SQLiteGraphStore):
        """Test deleting a node and its edges."""
        graph_store.upsert_node("n1", "project", {})
        graph_store.upsert_node("n2", "task", {})
        graph_store.upsert_edge("n1", "n2", "contains")

        deleted = graph_store.delete_node("n1")
        assert deleted is True

        # Node should be gone
        assert graph_store.get_node("n1") is None

        # Edge should also be gone
        edges = graph_store.get_edges("n2")
        assert len(edges) == 0

    def test_delete_edge(self, graph_store: SQLiteGraphStore):
        """Test deleting an edge."""
        graph_store.upsert_node("a", "node", {})
        graph_store.upsert_node("b", "node", {})
        edge_id = graph_store.upsert_edge("a", "b", "links")

        deleted = graph_store.delete_edge(edge_id)
        assert deleted is True

        edges = graph_store.get_edges("a")
        assert len(edges) == 0

    def test_count_nodes_and_edges(self, graph_store: SQLiteGraphStore):
        """Test counting nodes and edges."""
        graph_store.upsert_node("n1", "type", {})
        graph_store.upsert_node("n2", "type", {})
        graph_store.upsert_node("n3", "type", {})
        graph_store.upsert_edge("n1", "n2", "link")
        graph_store.upsert_edge("n2", "n3", "link")

        assert graph_store.count_nodes() == 3
        assert graph_store.count_edges() == 2

    def test_get_nodes_bulk_returns_matching(self, graph_store: SQLiteGraphStore):
        """Test bulk fetching multiple nodes by ID."""
        graph_store.upsert_node("b1", "project", {"name": "P1"})
        graph_store.upsert_node("b2", "task", {"name": "T1"})
        graph_store.upsert_node("b3", "note", {"name": "N1"})

        results = graph_store.get_nodes_bulk(["b1", "b3"])
        assert len(results) == 2
        result_ids = {n["node_id"] for n in results}
        assert result_ids == {"b1", "b3"}

    def test_get_nodes_bulk_empty_input(self, graph_store: SQLiteGraphStore):
        """Test bulk fetch with empty list returns empty."""
        results = graph_store.get_nodes_bulk([])
        assert results == []

    def test_get_nodes_bulk_missing_ids(self, graph_store: SQLiteGraphStore):
        """Test bulk fetch with some non-existent IDs returns only found."""
        graph_store.upsert_node("exists", "project", {"name": "P"})

        results = graph_store.get_nodes_bulk(["exists", "missing1", "missing2"])
        assert len(results) == 1
        assert results[0]["node_id"] == "exists"

    def test_query_multiple_types(self, graph_store: SQLiteGraphStore):
        """Test querying with a list of node types uses IN clause."""
        graph_store.upsert_node("p1", "project", {"name": "P1"})
        graph_store.upsert_node("t1", "task", {"name": "T1"})
        graph_store.upsert_node("n1", "note", {"name": "N1"})
        graph_store.upsert_node("t2", "task", {"name": "T2"})

        results = graph_store.query(node_type=["project", "task"])
        assert len(results) == 3
        result_types = {n["node_type"] for n in results}
        assert result_types == {"project", "task"}

    def test_get_edges_for_nodes_batch(self, graph_store: SQLiteGraphStore):
        """Test batch edge fetching for multiple nodes."""
        graph_store.upsert_node("a", "node", {})
        graph_store.upsert_node("b", "node", {})
        graph_store.upsert_node("c", "node", {})
        graph_store.upsert_node("d", "node", {})

        graph_store.upsert_edge("a", "b", "links")
        graph_store.upsert_edge("a", "c", "links")
        graph_store.upsert_edge("c", "d", "depends")

        result = graph_store.get_edges_for_nodes(
            ["a", "c"], direction="outgoing"
        )

        assert len(result["a"]) == 2
        assert len(result["c"]) == 1
        assert result["c"][0]["edge_type"] == "depends"

    def test_get_edges_for_nodes_with_type_filter(
        self, graph_store: SQLiteGraphStore
    ):
        """Test batch edge fetch with edge type filter."""
        graph_store.upsert_node("x", "node", {})
        graph_store.upsert_node("y", "node", {})
        graph_store.upsert_node("z", "node", {})

        graph_store.upsert_edge("x", "y", "links")
        graph_store.upsert_edge("x", "z", "depends")

        result = graph_store.get_edges_for_nodes(
            ["x"], direction="outgoing", edge_type="links"
        )
        assert len(result["x"]) == 1
        assert result["x"][0]["target_id"] == "y"

    def test_delete_nodes_bulk(self, graph_store: SQLiteGraphStore):
        """Test bulk deleting nodes and their edges."""
        graph_store.upsert_node("d1", "node", {})
        graph_store.upsert_node("d2", "node", {})
        graph_store.upsert_node("d3", "node", {})
        graph_store.upsert_edge("d1", "d2", "link")
        graph_store.upsert_edge("d2", "d3", "link")
        graph_store.upsert_edge("d1", "d3", "link")

        deleted = graph_store.delete_nodes_bulk(["d1", "d2"])
        assert deleted == 2

        # d3 should remain
        assert graph_store.get_node("d3") is not None
        assert graph_store.get_node("d1") is None
        assert graph_store.get_node("d2") is None

        # All edges involving d1 or d2 should be gone
        edges = graph_store.get_edges("d3")
        assert len(edges) == 0
