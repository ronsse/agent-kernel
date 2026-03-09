"""Tests for graph traversal algorithms and operations."""

import pytest


class TestGraphTraversal:
    """Tests for graph traversal operations."""

    def test_breadth_first_traversal(self, graph_store):
        """Test breadth-first traversal from a seed node."""
        # Create a tree structure:
        #     root
        #    /    \
        #   a1    a2
        #  / \    |
        # b1  b2  b3
        graph_store.upsert_node("root", "project", {"name": "Root"})
        graph_store.upsert_node("a1", "area", {"name": "Area 1"})
        graph_store.upsert_node("a2", "area", {"name": "Area 2"})
        graph_store.upsert_node("b1", "task", {"name": "Task 1"})
        graph_store.upsert_node("b2", "task", {"name": "Task 2"})
        graph_store.upsert_node("b3", "task", {"name": "Task 3"})

        graph_store.upsert_edge("root", "a1", "contains")
        graph_store.upsert_edge("root", "a2", "contains")
        graph_store.upsert_edge("a1", "b1", "contains")
        graph_store.upsert_edge("a1", "b2", "contains")
        graph_store.upsert_edge("a2", "b3", "contains")

        # Get subgraph with depth 1 (should get root + areas)
        subgraph_d1 = graph_store.get_subgraph(["root"], depth=1)
        node_ids_d1 = {n["node_id"] for n in subgraph_d1["nodes"]}
        assert "root" in node_ids_d1
        assert "a1" in node_ids_d1
        assert "a2" in node_ids_d1
        assert "b1" not in node_ids_d1  # Too deep

        # Get subgraph with depth 2 (should get all)
        subgraph_d2 = graph_store.get_subgraph(["root"], depth=2)
        node_ids_d2 = {n["node_id"] for n in subgraph_d2["nodes"]}
        assert len(node_ids_d2) == 6  # All nodes

    def test_multi_seed_traversal(self, graph_store):
        """Test traversal from multiple seed nodes."""
        # Create two separate trees
        graph_store.upsert_node("p1", "project", {"name": "Project 1"})
        graph_store.upsert_node("p2", "project", {"name": "Project 2"})
        graph_store.upsert_node("t1", "task", {"name": "Task 1"})
        graph_store.upsert_node("t2", "task", {"name": "Task 2"})
        graph_store.upsert_node("t3", "task", {"name": "Task 3"})

        graph_store.upsert_edge("p1", "t1", "contains")
        graph_store.upsert_edge("p1", "t2", "contains")
        graph_store.upsert_edge("p2", "t3", "contains")

        # Get subgraph from both projects
        subgraph = graph_store.get_subgraph(["p1", "p2"], depth=1)
        node_ids = {n["node_id"] for n in subgraph["nodes"]}

        assert "p1" in node_ids
        assert "p2" in node_ids
        assert "t1" in node_ids
        assert "t2" in node_ids
        assert "t3" in node_ids

    def test_bidirectional_traversal(self, graph_store):
        """Test traversal in both directions."""
        # Create a chain: a -> b -> c
        graph_store.upsert_node("a", "node", {"name": "A"})
        graph_store.upsert_node("b", "node", {"name": "B"})
        graph_store.upsert_node("c", "node", {"name": "C"})

        graph_store.upsert_edge("a", "b", "links")
        graph_store.upsert_edge("b", "c", "links")

        # Get outgoing edges from b
        outgoing = graph_store.get_edges("b", direction="outgoing")
        assert len(outgoing) == 1
        assert outgoing[0]["target_id"] == "c"

        # Get incoming edges to b
        incoming = graph_store.get_edges("b", direction="incoming")
        assert len(incoming) == 1
        assert incoming[0]["source_id"] == "a"

        # Get all edges for b
        all_edges = graph_store.get_edges("b", direction="both")
        assert len(all_edges) == 2

    def test_cycle_detection(self, graph_store):
        """Test handling of cycles in graph traversal."""
        # Create a cycle: a -> b -> c -> a
        graph_store.upsert_node("a", "node", {})
        graph_store.upsert_node("b", "node", {})
        graph_store.upsert_node("c", "node", {})

        graph_store.upsert_edge("a", "b", "next")
        graph_store.upsert_edge("b", "c", "next")
        graph_store.upsert_edge("c", "a", "next")

        # Traversal should handle cycles gracefully (visit each node once)
        subgraph = graph_store.get_subgraph(["a"], depth=10)
        node_ids = [n["node_id"] for n in subgraph["nodes"]]

        # Each node should appear exactly once despite the cycle
        assert node_ids.count("a") == 1
        assert node_ids.count("b") == 1
        assert node_ids.count("c") == 1

    def test_filter_by_edge_type(self, graph_store):
        """Test filtering edges by type during traversal."""
        graph_store.upsert_node("project", "project", {})
        graph_store.upsert_node("task1", "task", {})
        graph_store.upsert_node("task2", "task", {})
        graph_store.upsert_node("note", "note", {})

        graph_store.upsert_edge("project", "task1", "contains")
        graph_store.upsert_edge("project", "task2", "contains")
        graph_store.upsert_edge("project", "note", "references")

        # Get only "contains" edges
        contains_edges = [
            e
            for e in graph_store.get_edges("project", direction="outgoing")
            if e["edge_type"] == "contains"
        ]
        assert len(contains_edges) == 2

        # Get only "references" edges
        ref_edges = [
            e
            for e in graph_store.get_edges("project", direction="outgoing")
            if e["edge_type"] == "references"
        ]
        assert len(ref_edges) == 1

    def test_weighted_edges(self, graph_store):
        """Test edges with weight properties."""
        graph_store.upsert_node("a", "node", {})
        graph_store.upsert_node("b", "node", {})
        graph_store.upsert_node("c", "node", {})

        # Create edges with different weights
        graph_store.upsert_edge("a", "b", "relates", {"weight": 0.9})
        graph_store.upsert_edge("a", "c", "relates", {"weight": 0.3})

        edges = graph_store.get_edges("a", direction="outgoing")

        # Find the strongest connection
        strongest = max(edges, key=lambda e: e["properties"].get("weight", 0))
        assert strongest["target_id"] == "b"
        assert strongest["properties"]["weight"] == 0.9

    def test_path_finding_simple(self, graph_store):
        """Test finding a simple path between two nodes."""
        # Create a path: a -> b -> c -> d
        for node_id in ["a", "b", "c", "d"]:
            graph_store.upsert_node(node_id, "node", {})

        graph_store.upsert_edge("a", "b", "next")
        graph_store.upsert_edge("b", "c", "next")
        graph_store.upsert_edge("c", "d", "next")

        # Get subgraph from a with enough depth to reach d
        subgraph = graph_store.get_subgraph(["a"], depth=3)

        # Verify we can reach d from a
        node_ids = {n["node_id"] for n in subgraph["nodes"]}
        assert "a" in node_ids
        assert "d" in node_ids

    def test_isolated_nodes(self, graph_store):
        """Test handling of isolated nodes with no edges."""
        graph_store.upsert_node("isolated1", "task", {"name": "Isolated 1"})
        graph_store.upsert_node("isolated2", "task", {"name": "Isolated 2"})

        # Isolated nodes should have no edges
        edges1 = graph_store.get_edges("isolated1")
        edges2 = graph_store.get_edges("isolated2")

        assert len(edges1) == 0
        assert len(edges2) == 0

        # Subgraph from isolated node should only contain itself
        subgraph = graph_store.get_subgraph(["isolated1"], depth=5)
        assert len(subgraph["nodes"]) == 1
        assert subgraph["nodes"][0]["node_id"] == "isolated1"

    def test_relationship_strength_aggregation(self, graph_store):
        """Test aggregating relationship strengths."""
        graph_store.upsert_node("person", "entity", {"name": "Alice"})
        graph_store.upsert_node("project1", "project", {"name": "Project 1"})
        graph_store.upsert_node("project2", "project", {"name": "Project 2"})
        graph_store.upsert_node("project3", "project", {"name": "Project 3"})

        # Person works on multiple projects with different involvement
        graph_store.upsert_edge("person", "project1", "works_on", {"hours": 40})
        graph_store.upsert_edge("person", "project2", "works_on", {"hours": 20})
        graph_store.upsert_edge("person", "project3", "works_on", {"hours": 5})

        edges = graph_store.get_edges("person", direction="outgoing")

        # Calculate total hours
        total_hours = sum(e["properties"].get("hours", 0) for e in edges)
        assert total_hours == 65

        # Find primary project (most hours)
        primary = max(edges, key=lambda e: e["properties"].get("hours", 0))
        assert primary["target_id"] == "project1"

    def test_neighbor_discovery(self, graph_store):
        """Test discovering immediate neighbors of a node."""
        graph_store.upsert_node("center", "node", {"name": "Center"})
        graph_store.upsert_node("n1", "node", {"name": "Neighbor 1"})
        graph_store.upsert_node("n2", "node", {"name": "Neighbor 2"})
        graph_store.upsert_node("n3", "node", {"name": "Neighbor 3"})
        graph_store.upsert_node("far", "node", {"name": "Far"})

        # Center connected to 3 neighbors
        graph_store.upsert_edge("center", "n1", "connects")
        graph_store.upsert_edge("center", "n2", "connects")
        graph_store.upsert_edge("n3", "center", "connects")  # Incoming
        graph_store.upsert_edge("n1", "far", "connects")  # n1 connected to far

        # Get immediate neighbors (depth 1)
        subgraph = graph_store.get_subgraph(["center"], depth=1)
        node_ids = {n["node_id"] for n in subgraph["nodes"]}

        assert "center" in node_ids
        assert "n1" in node_ids
        assert "n2" in node_ids
        assert "n3" in node_ids
        assert "far" not in node_ids  # Too far (depth 2)


class TestGraphBatchOptimization:
    """Tests for batch query optimizations in graph store."""

    def test_batch_subgraph_matches_iterative(self, graph_store):
        """Verify batch method returns same results as iterative."""
        # Create a complex graph
        graph_store.upsert_node("root", "project", {"name": "Root"})
        graph_store.upsert_node("a1", "area", {"name": "Area 1"})
        graph_store.upsert_node("a2", "area", {"name": "Area 2"})
        graph_store.upsert_node("b1", "task", {"name": "Task 1"})
        graph_store.upsert_node("b2", "task", {"name": "Task 2"})
        graph_store.upsert_node("b3", "task", {"name": "Task 3"})

        graph_store.upsert_edge("root", "a1", "contains")
        graph_store.upsert_edge("root", "a2", "contains")
        graph_store.upsert_edge("a1", "b1", "contains")
        graph_store.upsert_edge("a1", "b2", "contains")
        graph_store.upsert_edge("a2", "b3", "contains")

        # Get results from batch method (default)
        batch_result = graph_store.get_subgraph(["root"], depth=2)

        # Get results from iterative method
        iterative_result = graph_store._get_subgraph_iterative(["root"], depth=2)

        # Compare node sets
        batch_nodes = {n["node_id"] for n in batch_result["nodes"]}
        iterative_nodes = {n["node_id"] for n in iterative_result["nodes"]}
        assert batch_nodes == iterative_nodes

        # Compare edge sets
        batch_edges = {e["edge_id"] for e in batch_result["edges"]}
        iterative_edges = {e["edge_id"] for e in iterative_result["edges"]}
        assert batch_edges == iterative_edges

    def test_batch_handles_empty_seed(self, graph_store):
        """Test batch method handles empty seed list."""
        result = graph_store.get_subgraph([], depth=2)
        assert result["nodes"] == []
        assert result["edges"] == []

    def test_batch_handles_missing_seeds(self, graph_store):
        """Test batch method handles non-existent seed nodes."""
        result = graph_store.get_subgraph(["nonexistent"], depth=2)
        assert result["nodes"] == []
        assert result["edges"] == []

    def test_batch_edge_type_filter(self, graph_store):
        """Test batch method respects edge type filtering."""
        graph_store.upsert_node("project", "project", {})
        graph_store.upsert_node("task", "task", {})
        graph_store.upsert_node("note", "note", {})

        graph_store.upsert_edge("project", "task", "contains")
        graph_store.upsert_edge("project", "note", "references")

        # Filter to only "contains" edges
        result = graph_store.get_subgraph(
            ["project"], depth=1, edge_types=["contains"]
        )

        node_ids = {n["node_id"] for n in result["nodes"]}
        assert "project" in node_ids
        assert "task" in node_ids
        assert "note" not in node_ids  # Filtered out

    def test_batch_large_graph_performance(self, graph_store):
        """Test batch method handles larger graphs efficiently."""
        # Create a larger graph (100 nodes in 10x10 grid-like structure)
        for i in range(10):
            for j in range(10):
                node_id = f"node_{i}_{j}"
                graph_store.upsert_node(node_id, "cell", {"x": i, "y": j})

                # Connect to neighbors
                if i > 0:
                    graph_store.upsert_edge(f"node_{i-1}_{j}", node_id, "right")
                if j > 0:
                    graph_store.upsert_edge(f"node_{i}_{j-1}", node_id, "down")

        # Query subgraph - this should complete quickly with batch method
        result = graph_store.get_subgraph(["node_5_5"], depth=3)

        # Should find multiple nodes within 3 hops
        assert len(result["nodes"]) > 1
        assert len(result["edges"]) > 0


class TestGraphPropertyFiltering:
    """Tests for SQL-pushed property filtering."""

    def test_query_with_string_property(self, graph_store):
        """Test querying by string property uses SQL."""
        graph_store.upsert_node("n1", "person", {"name": "Alice", "role": "admin"})
        graph_store.upsert_node("n2", "person", {"name": "Bob", "role": "user"})
        graph_store.upsert_node("n3", "person", {"name": "Charlie", "role": "admin"})

        results = graph_store.query(node_type="person", properties={"role": "admin"})

        assert len(results) == 2
        names = {r["properties"]["name"] for r in results}
        assert names == {"Alice", "Charlie"}

    def test_query_with_numeric_property(self, graph_store):
        """Test querying by numeric property uses SQL."""
        graph_store.upsert_node("t1", "task", {"name": "Task 1", "priority": 1})
        graph_store.upsert_node("t2", "task", {"name": "Task 2", "priority": 2})
        graph_store.upsert_node("t3", "task", {"name": "Task 3", "priority": 1})

        results = graph_store.query(node_type="task", properties={"priority": 1})

        assert len(results) == 2
        names = {r["properties"]["name"] for r in results}
        assert names == {"Task 1", "Task 3"}

    def test_query_with_boolean_property(self, graph_store):
        """Test querying by boolean property uses SQL."""
        graph_store.upsert_node("t1", "task", {"name": "Task 1", "completed": True})
        graph_store.upsert_node("t2", "task", {"name": "Task 2", "completed": False})
        graph_store.upsert_node("t3", "task", {"name": "Task 3", "completed": True})

        results = graph_store.query(node_type="task", properties={"completed": True})

        assert len(results) == 2
        names = {r["properties"]["name"] for r in results}
        assert names == {"Task 1", "Task 3"}

    def test_query_by_property_path(self, graph_store):
        """Test query_by_property uses SQL json_extract."""
        graph_store.upsert_node("n1", "entity", {"type": "project", "status": "active"})
        graph_store.upsert_node("n2", "entity", {"type": "task", "status": "active"})
        graph_store.upsert_node("n3", "entity", {"type": "project", "status": "archived"})

        results = graph_store.query_by_property("status", "active")

        assert len(results) == 2

    def test_query_by_property_with_type_filter(self, graph_store):
        """Test query_by_property with node type filter."""
        graph_store.upsert_node("n1", "project", {"status": "active"})
        graph_store.upsert_node("n2", "task", {"status": "active"})
        graph_store.upsert_node("n3", "project", {"status": "archived"})

        results = graph_store.query_by_property(
            "status", "active", node_type="project"
        )

        assert len(results) == 1
        assert results[0]["node_id"] == "n1"
