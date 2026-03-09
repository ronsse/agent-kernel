"""Tests for context schemas."""


from agent_kernel.core.schemas import (
    ContextBudget,
    ContextItem,
    ContextPacket,
    ContextRef,
    GraphSlice,
    QueryRecord,
    RefType,
    RetrievalLimits,
    RetrievalReport,
)


class TestContextRef:
    """Tests for ContextRef schema."""

    def test_create_context_ref(self):
        """Test creating a context reference."""
        ref = ContextRef(
            ref_type=RefType.NOTE,
            ref_id="note_123",
            uri="obsidian://vault/notes/test.md",
            hash="abc123def456",
            metadata={"title": "Test Note"},
        )

        assert ref.ref_type == RefType.NOTE
        assert ref.ref_id == "note_123"
        assert ref.uri == "obsidian://vault/notes/test.md"
        assert ref.hash == "abc123def456"
        assert ref.metadata["title"] == "Test Note"

    def test_context_ref_minimal(self):
        """Test creating context ref with minimal fields."""
        ref = ContextRef(
            ref_type=RefType.TASK,
            ref_id="task_456",
        )

        assert ref.ref_type == RefType.TASK
        assert ref.ref_id == "task_456"
        assert ref.uri is None
        assert ref.hash is None
        assert ref.metadata == {}

    def test_all_ref_types(self):
        """Test all reference types are valid."""
        for ref_type in RefType:
            ref = ContextRef(ref_type=ref_type, ref_id="test_id")
            assert ref.ref_type == ref_type


class TestContextBudget:
    """Tests for ContextBudget schema."""

    def test_default_budget(self):
        """Test default budget values."""
        budget = ContextBudget()

        assert budget.max_tokens == 8000
        assert budget.max_items == 50
        assert budget.retrieval_limits.max_notes == 20
        assert budget.retrieval_limits.max_tasks == 30

    def test_custom_budget(self):
        """Test custom budget values."""
        budget = ContextBudget(
            max_tokens=4000,
            max_items=25,
            retrieval_limits=RetrievalLimits(
                max_notes=10,
                max_tasks=15,
            ),
        )

        assert budget.max_tokens == 4000
        assert budget.max_items == 25
        assert budget.retrieval_limits.max_notes == 10


class TestContextItem:
    """Tests for ContextItem schema."""

    def test_create_context_item(self):
        """Test creating a context item."""
        ref = ContextRef(ref_type=RefType.DOCUMENT, ref_id="doc_123")
        item = ContextItem(
            ref=ref,
            excerpt="This is the excerpt text...",
            summary="A brief summary",
            relevance_score=0.92,
            included_reason="semantic_match",
        )

        assert item.ref.ref_id == "doc_123"
        assert "excerpt" in item.excerpt
        assert item.relevance_score == 0.92


class TestContextPacket:
    """Tests for ContextPacket schema."""

    def test_create_context_packet(self):
        """Test creating a context packet."""
        packet = ContextPacket(
            intent="What are my tasks for today?",
            project_id="proj_123",
        )

        assert packet.intent == "What are my tasks for today?"
        assert packet.project_id == "proj_123"
        assert packet.packet_id is not None
        assert len(packet.packet_id) == 26  # ULID length
        assert packet.items == []

    def test_packet_with_items(self):
        """Test packet with context items."""
        ref = ContextRef(ref_type=RefType.TASK, ref_id="task_1")
        item = ContextItem(ref=ref, excerpt="Task content")

        packet = ContextPacket(
            intent="Review tasks",
            items=[item],
        )

        assert len(packet.items) == 1
        assert packet.items[0].ref.ref_id == "task_1"

    def test_packet_with_graph_slice(self):
        """Test packet with graph context."""
        graph = GraphSlice(
            nodes=[{"node_id": "n1", "type": "project"}],
            edges=[{"source": "n1", "target": "n2", "type": "contains"}],
        )

        packet = ContextPacket(
            intent="Show project structure",
            graph_slice=graph,
        )

        assert packet.graph_slice is not None
        assert len(packet.graph_slice.nodes) == 1


class TestRetrievalReport:
    """Tests for RetrievalReport schema."""

    def test_default_report(self):
        """Test default retrieval report."""
        report = RetrievalReport()

        assert report.queries_run == []
        assert report.filters_applied == []
        assert report.items_considered == 0
        assert report.selection_strategy == "relevance_ranked"

    def test_report_with_queries(self):
        """Test report with query records."""
        query = QueryRecord(
            source="vector",
            query="task management",
            results_count=15,
            duration_ms=45,
        )

        report = RetrievalReport(
            queries_run=[query],
            items_considered=15,
            items_selected=5,
        )

        assert len(report.queries_run) == 1
        assert report.queries_run[0].duration_ms == 45
