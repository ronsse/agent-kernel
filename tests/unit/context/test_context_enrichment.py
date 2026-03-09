"""Tests for context enrichment and assembly."""

import pytest


class TestContextEnrichment:
    """Tests for context enrichment functionality."""

    def test_enrich_with_related_notes(self, document_store, graph_store):
        """Test enriching context with related notes via graph."""
        # Create main note
        main_note_id = document_store.put(
            "note_main",
            "Main note about agent systems",
            {"title": "Agent Systems", "tags": ["ai", "agents"], "item_type": "note"},
        )

        # Create related notes
        related1_id = document_store.put(
            "note_related1",
            "Note about memory management",
            {"title": "Memory Management", "tags": ["ai", "memory"], "item_type": "note"},
        )

        related2_id = document_store.put(
            "note_related2",
            "Note about planning algorithms",
            {"title": "Planning", "tags": ["ai", "planning"], "item_type": "note"},
        )

        # Create graph relationships
        graph_store.upsert_node("note_main", "note", {"title": "Agent Systems"})
        graph_store.upsert_node("note_related1", "note", {"title": "Memory Management"})
        graph_store.upsert_node("note_related2", "note", {"title": "Planning"})

        graph_store.upsert_edge("note_main", "note_related1", "references", {"strength": 0.9})
        graph_store.upsert_edge("note_main", "note_related2", "references", {"strength": 0.7})

        # Get related notes via graph traversal
        subgraph = graph_store.get_subgraph(["note_main"], depth=1)
        related_note_ids = {
            n["node_id"] for n in subgraph["nodes"] if n["node_id"] != "note_main"
        }

        assert "note_related1" in related_note_ids
        assert "note_related2" in related_note_ids

        # Fetch full documents for related notes
        related_docs = [
            document_store.get(nid) for nid in related_note_ids if nid.startswith("note_")
        ]
        related_docs = [d for d in related_docs if d is not None]

        assert len(related_docs) == 2

    def test_enrich_with_tasks(self, document_store, graph_store):
        """Test enriching context with related tasks."""
        # Create a project note
        project_id = document_store.put(
            "project_alpha",
            "Project Alpha overview",
            {"title": "Project Alpha", "status": "active", "item_type": "project"},
        )

        # Create related tasks
        task1_id = "task_1"
        task2_id = "task_2"

        # Store tasks in graph
        graph_store.upsert_node("project_alpha", "project", {"title": "Project Alpha"})
        graph_store.upsert_node(task1_id, "task", {"title": "Implement feature X", "status": "in_progress"})
        graph_store.upsert_node(task2_id, "task", {"title": "Write tests", "status": "pending"})

        graph_store.upsert_edge("project_alpha", task1_id, "contains")
        graph_store.upsert_edge("project_alpha", task2_id, "contains")

        # Get all tasks for the project
        edges = graph_store.get_edges("project_alpha", direction="outgoing")
        task_edges = [e for e in edges if e["edge_type"] == "contains"]

        assert len(task_edges) == 2

        # Extract task nodes
        task_ids = [e["target_id"] for e in task_edges]
        tasks = [graph_store.get_node(tid) for tid in task_ids]

        assert len(tasks) == 2
        assert any(t["properties"]["status"] == "in_progress" for t in tasks)

    def test_enrich_with_temporal_context(self, document_store):
        """Test enriching with temporally related documents."""
        from datetime import datetime, timedelta

        # Create documents with timestamps
        now = datetime.now()

        recent_id = document_store.put(
            "recent_note",
            "Recent note",
            {"created_at": now.isoformat(), "tags": ["project"], "item_type": "note"},
        )

        older_id = document_store.put(
            "older_note",
            "Older note",
            {
                "created_at": (now - timedelta(days=7)).isoformat(),
                "tags": ["project"],
                "item_type": "note",
            },
        )

        ancient_id = document_store.put(
            "ancient_note",
            "Ancient note",
            {
                "created_at": (now - timedelta(days=90)).isoformat(),
                "tags": ["project"],
                "item_type": "note",
            },
        )

        # Query all project notes
        all_docs = [
            document_store.get(recent_id),
            document_store.get(older_id),
            document_store.get(ancient_id),
        ]

        # Sort by recency
        def get_created_at(doc):
            created_str = doc["metadata"].get("created_at", "")
            return datetime.fromisoformat(created_str) if created_str else datetime.min

        sorted_docs = sorted(all_docs, key=get_created_at, reverse=True)

        # Most recent should come first
        assert sorted_docs[0]["doc_id"] == "recent_note"
        assert sorted_docs[1]["doc_id"] == "older_note"
        assert sorted_docs[2]["doc_id"] == "ancient_note"

    def test_enrich_with_tag_similarity(self, document_store):
        """Test enriching with notes that share tags."""
        # Create notes with overlapping tags
        doc1_id = document_store.put(
            "doc1",
            "Note about AI agents",
            {"tags": ["ai", "agents", "automation"], "item_type": "note"},
        )

        doc2_id = document_store.put(
            "doc2",
            "Note about AI planning",
            {"tags": ["ai", "planning", "search"], "item_type": "note"},
        )

        doc3_id = document_store.put(
            "doc3",
            "Note about web development",
            {"tags": ["web", "javascript", "frontend"], "item_type": "note"},
        )

        # Find notes with "ai" tag
        all_docs = [
            document_store.get(doc1_id),
            document_store.get(doc2_id),
            document_store.get(doc3_id),
        ]

        ai_docs = [d for d in all_docs if "ai" in d["metadata"].get("tags", [])]

        assert len(ai_docs) == 2
        assert any(d["doc_id"] == "doc1" for d in ai_docs)
        assert any(d["doc_id"] == "doc2" for d in ai_docs)

    def test_enrich_with_entity_mentions(self, document_store, graph_store):
        """Test enriching with notes that mention specific entities."""
        # Create entity
        graph_store.upsert_node("entity_alice", "person", {"name": "Alice", "role": "engineer"})

        # Create notes that mention Alice
        note1_id = document_store.put(
            "note1",
            "Alice is working on the new feature",
            {"entities": ["entity_alice"], "title": "Feature Update", "item_type": "note"},
        )

        note2_id = document_store.put(
            "note2",
            "Meeting with Alice about architecture",
            {"entities": ["entity_alice"], "title": "Architecture Meeting", "item_type": "note"},
        )

        note3_id = document_store.put(
            "note3",
            "Random note without mentions",
            {"title": "Random", "item_type": "note"},
        )

        # Link notes to entity in graph
        graph_store.upsert_node("note1", "note", {})
        graph_store.upsert_node("note2", "note", {})

        graph_store.upsert_edge("note1", "entity_alice", "mentions")
        graph_store.upsert_edge("note2", "entity_alice", "mentions")

        # Find all notes mentioning Alice
        incoming = graph_store.get_edges("entity_alice", direction="incoming")
        mentioning_edges = [e for e in incoming if e["edge_type"] == "mentions"]

        assert len(mentioning_edges) == 2

        note_ids = {e["source_id"] for e in mentioning_edges}
        assert "note1" in note_ids
        assert "note2" in note_ids

    def test_context_budget_management(self, document_store):
        """Test respecting context budget during enrichment."""
        # Create many documents
        doc_ids = []
        for i in range(20):
            doc_id = document_store.put(
                f"doc_{i}",
                f"Document {i} " * 100,
                {"relevance": 1.0 - (i * 0.05), "index": i, "item_type": "note"},
            )
            doc_ids.append(doc_id)

        # Fetch all documents
        all_docs = [document_store.get(doc_id) for doc_id in doc_ids]

        # Sort by relevance (stored in metadata for this test)
        sorted_docs = sorted(
            all_docs,
            key=lambda d: d["metadata"].get("relevance", 0),
            reverse=True,
        )

        # Apply budget constraint (max 5 documents)
        max_items = 5
        selected_docs = sorted_docs[:max_items]

        assert len(selected_docs) == max_items

        # Verify we selected the most relevant ones
        indices = [d["metadata"]["index"] for d in selected_docs]
        assert indices == [0, 1, 2, 3, 4]

    def test_hierarchical_context_enrichment(self, graph_store, document_store):
        """Test enriching with hierarchical relationships (parent/child)."""
        # Create hierarchy: workspace -> project -> area -> task
        graph_store.upsert_node("workspace", "workspace", {"name": "Work"})
        graph_store.upsert_node("project", "project", {"name": "Agent System"})
        graph_store.upsert_node("area", "area", {"name": "Memory"})
        graph_store.upsert_node("task", "task", {"name": "Implement vector store"})

        graph_store.upsert_edge("workspace", "project", "contains")
        graph_store.upsert_edge("project", "area", "contains")
        graph_store.upsert_edge("area", "task", "contains")

        # Starting from task, enrich upward to get full context
        # Get ancestors by traversing incoming "contains" edges
        def get_ancestors(node_id, max_depth=10):
            ancestors = []
            current_nodes = [node_id]
            visited = set()

            for _ in range(max_depth):
                if not current_nodes:
                    break

                next_nodes = []
                for node in current_nodes:
                    if node in visited:
                        continue
                    visited.add(node)

                    # Get incoming contains edges (parents)
                    incoming = graph_store.get_edges(node, direction="incoming")
                    parent_edges = [e for e in incoming if e["edge_type"] == "contains"]

                    for edge in parent_edges:
                        parent_id = edge["source_id"]
                        if parent_id not in visited:
                            ancestors.append(parent_id)
                            next_nodes.append(parent_id)

                current_nodes = next_nodes

            return ancestors

        ancestors = get_ancestors("task")

        assert "area" in ancestors
        assert "project" in ancestors
        assert "workspace" in ancestors

    def test_cross_reference_enrichment(self, document_store, graph_store):
        """Test enriching with cross-referenced notes."""
        # Create a note with references
        main_id = document_store.put(
            "main",
            "Main note references [[Note A]] and [[Note B]]",
            {"title": "Main", "outbound_refs": ["note_a", "note_b"], "item_type": "note"},
        )

        ref_a_id = document_store.put(
            "note_a",
            "Note A content",
            {"title": "Note A", "item_type": "note"},
        )

        ref_b_id = document_store.put(
            "note_b",
            "Note B content",
            {"title": "Note B", "item_type": "note"},
        )

        # Create graph edges for references
        graph_store.upsert_node("main", "note", {})
        graph_store.upsert_node("note_a", "note", {})
        graph_store.upsert_node("note_b", "note", {})

        graph_store.upsert_edge("main", "note_a", "references")
        graph_store.upsert_edge("main", "note_b", "references")

        # Enrich by following references
        edges = graph_store.get_edges("main", direction="outgoing")
        ref_edges = [e for e in edges if e["edge_type"] == "references"]

        referenced_ids = [e["target_id"] for e in ref_edges]

        assert "note_a" in referenced_ids
        assert "note_b" in referenced_ids

        # Fetch referenced documents
        referenced_docs = [document_store.get(rid) for rid in referenced_ids]
        referenced_docs = [d for d in referenced_docs if d is not None]

        assert len(referenced_docs) == 2
