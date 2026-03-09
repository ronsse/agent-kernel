"""Tests for Context Assembler."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_kernel.context.assembler import ContextAssembler
from agent_kernel.core.schemas import ContextPolicy, RefType, SkillManifest, SkillOrigin
from agent_kernel.core.schemas.base import utc_now


@pytest.fixture
def mock_document_store():
    """Create a mock document store."""
    store = MagicMock()
    store.search.return_value = [
        {
            "doc_id": "doc_1",
            "content": "This is a test document about Python programming.",
            "metadata": {"title": "Python Guide"},
            "rank": -0.5,
        },
        {
            "doc_id": "doc_2",
            "content": "Another document about machine learning.",
            "metadata": {"title": "ML Basics"},
            "rank": -0.3,
        },
    ]
    return store


@pytest.fixture
def mock_vector_store():
    """Create a mock vector store."""
    store = MagicMock()
    store.query.return_value = [
        {
            "item_id": "vec_1",
            "score": 0.92,
            "metadata": {"ref_type": "note", "excerpt": "Semantic match content"},
        },
        {
            "item_id": "vec_2",
            "score": 0.85,
            "metadata": {"ref_type": "task", "excerpt": "Another semantic match"},
        },
    ]
    return store


@pytest.fixture
def mock_graph_store():
    """Create a mock graph store."""
    store = MagicMock()
    store.get_subgraph.return_value = {
        "nodes": [
            {"node_id": "n1", "node_type": "project", "properties": {"name": "P1"}},
            {"node_id": "n2", "node_type": "task", "properties": {"name": "T1"}},
        ],
        "edges": [
            {"source_id": "n1", "target_id": "n2", "edge_type": "contains"},
        ],
    }
    return store


@pytest.fixture
def mock_embedding_service():
    """Create a mock embedding service."""
    service = MagicMock()
    service.embed = AsyncMock(return_value=[0.1] * 1536)
    return service


@pytest.fixture
def default_policy():
    """Create a default context policy."""
    return ContextPolicy(
        max_tokens=4000,
        max_notes=10,
        max_tasks=10,
        max_events=5,
    )


class TestContextAssembler:
    """Tests for ContextAssembler."""

    def test_init_minimal(self):
        """Test initialization with no stores."""
        assembler = ContextAssembler()
        assert assembler._document_store is None
        assert assembler._vector_store is None
        assert assembler._graph_store is None

    def test_init_with_stores(
        self,
        mock_document_store,
        mock_vector_store,
        mock_graph_store,
    ):
        """Test initialization with all stores."""
        assembler = ContextAssembler(
            document_store=mock_document_store,
            vector_store=mock_vector_store,
            graph_store=mock_graph_store,
        )

        assert assembler._document_store is not None
        assert assembler._vector_store is not None
        assert assembler._graph_store is not None

    def test_assemble_with_documents(
        self,
        mock_document_store,
        default_policy,
    ):
        """Test assembling context from documents."""
        assembler = ContextAssembler(document_store=mock_document_store)

        packet = assembler.assemble(
            intent="Tell me about Python",
            policy=default_policy,
        )

        assert packet is not None
        assert packet.intent == "Tell me about Python"
        assert len(packet.items) == 2
        assert packet.items[0].ref.ref_id == "doc_1"
        mock_document_store.search.assert_called_once()

    def test_assemble_with_vectors(
        self,
        mock_vector_store,
        default_policy,
    ):
        """Test assembling context from vectors."""
        assembler = ContextAssembler(vector_store=mock_vector_store)

        embedding = [0.1] * 1536
        packet = assembler.assemble(
            intent="Find similar content",
            policy=default_policy,
            embedding=embedding,
        )

        assert len(packet.items) == 2
        assert packet.items[0].relevance_score == 0.92
        mock_vector_store.query.assert_called_once()

    def test_assemble_with_graph(
        self,
        mock_graph_store,
        default_policy,
    ):
        """Test assembling context with graph."""
        assembler = ContextAssembler(graph_store=mock_graph_store)

        packet = assembler.assemble(
            intent="Show project structure",
            policy=default_policy,
            seed_node_ids=["n1"],
        )

        assert packet.graph_slice is not None
        assert len(packet.graph_slice.nodes) == 2
        assert len(packet.graph_slice.edges) == 1
        mock_graph_store.get_subgraph.assert_called_once()

    def test_assemble_combined_sources(
        self,
        mock_document_store,
        mock_vector_store,
        mock_graph_store,
        default_policy,
    ):
        """Test assembling from all sources."""
        assembler = ContextAssembler(
            document_store=mock_document_store,
            vector_store=mock_vector_store,
            graph_store=mock_graph_store,
        )

        embedding = [0.1] * 1536
        packet = assembler.assemble(
            intent="Combined search",
            policy=default_policy,
            embedding=embedding,
            seed_node_ids=["n1"],
        )

        # Should have items from documents + vectors
        assert len(packet.items) == 4
        # Should have graph context
        assert packet.graph_slice is not None
        # Should have queries in report
        assert len(packet.retrieval_report.queries_run) == 3

    def test_assemble_includes_skill_manifests(self, default_policy):
        """Test skill manifests included when store is provided."""

        class DummySkillStore:
            def __init__(self, manifests):
                self._manifests = manifests

            def search_sync(self, query, top_k=10):
                return self._manifests[:top_k]

        manifest = SkillManifest(
            skill_id="daily-review",
            name="Daily Review",
            description="Review tasks and notes.",
            origin=SkillOrigin(
                kind="local",
                path="/tmp/daily-review",
                installed_at=utc_now(),
                content_hash="abc123",
            ),
        )

        assembler = ContextAssembler(skill_store=DummySkillStore([manifest]))
        packet = assembler.assemble(
            intent="daily review",
            policy=default_policy,
        )

        assert any(item.ref.ref_type == RefType.SKILL for item in packet.items)

    def test_assemble_deduplication(
        self,
        default_policy,
    ):
        """Test that duplicate items are removed."""
        doc_store = MagicMock()
        doc_store.search.return_value = [
            {"doc_id": "shared_id", "content": "Doc content", "metadata": {}},
        ]

        vector_store = MagicMock()
        vector_store.query.return_value = [
            {"item_id": "shared_id", "score": 0.9, "metadata": {"ref_type": "doc"}},
        ]

        assembler = ContextAssembler(
            document_store=doc_store,
            vector_store=vector_store,
        )

        packet = assembler.assemble(
            intent="Test dedup",
            policy=default_policy,
            embedding=[0.1] * 10,
        )

        # Should deduplicate by ref_id
        assert len(packet.items) == 1

    def test_assemble_ranking(
        self,
        default_policy,
    ):
        """Test that items are ranked by relevance."""
        doc_store = MagicMock()
        doc_store.search.return_value = [
            {"doc_id": "low", "content": "Low relevance", "metadata": {}, "rank": -0.1},
            {"doc_id": "high", "content": "High relevance", "metadata": {}, "rank": -0.9},
            {"doc_id": "mid", "content": "Mid relevance", "metadata": {}, "rank": -0.5},
        ]

        assembler = ContextAssembler(document_store=doc_store)

        packet = assembler.assemble(
            intent="Test ranking",
            policy=default_policy,
        )

        # Should be sorted by relevance (highest first)
        scores = [item.relevance_score for item in packet.items]
        assert scores == sorted(scores, reverse=True)

    def test_assemble_budget_limits(
        self,
        mock_document_store,
        default_policy,
    ):
        """Test that budget limits are applied."""
        # Create many documents
        mock_document_store.search.return_value = [
            {"doc_id": f"doc_{i}", "content": "x" * 100, "metadata": {}}
            for i in range(50)
        ]

        assembler = ContextAssembler(document_store=mock_document_store)

        # Set very low budget
        policy = ContextPolicy(
            max_tokens=100,
            max_notes=5,
        )

        packet = assembler.assemble(
            intent="Test budget",
            policy=policy,
        )

        # Should be limited by max_items
        assert len(packet.items) <= 5

    def test_set_embedding_service(
        self,
        mock_embedding_service,
    ):
        """Test setting embedding service after init."""
        assembler = ContextAssembler()
        assert assembler._embedding_service is None

        assembler.set_embedding_service(mock_embedding_service)
        assert assembler._embedding_service is not None


class TestContextAssemblerAsync:
    """Tests for async assembly with auto-embedding.

    Note: v1.0.2 refactored assemble_async to use the new retrieval pipeline
    (ContextPackResolver -> RetrievalPlanner -> RetrievalExecutor -> Gates).
    The embedding service is now called via the executor, not directly.
    """

    @pytest.mark.asyncio
    async def test_assemble_async_returns_packet(
        self,
        mock_vector_store,
        mock_embedding_service,
        default_policy,
    ):
        """Test async assembly returns a valid ContextPacket with v1.0.2 fields."""
        assembler = ContextAssembler(
            vector_store=mock_vector_store,
            embedding_service=mock_embedding_service,
        )

        packet = await assembler.assemble_async(
            intent="Auto embed this",
            policy=default_policy,
        )

        # Should return a valid packet with v1.0.2 fields
        assert packet.packet_id is not None
        assert packet.intent == "Auto embed this"
        # v1.0.2: retrieval_mode should be set
        assert packet.retrieval_mode in ["baseline", "instructed", "iterative"]
        # v1.0.2: context_packs should be a list (may be empty if no packs configured)
        assert isinstance(packet.context_packs, list)
        # v1.0.2: retrieval_report should have quality
        assert packet.retrieval_report is not None

    @pytest.mark.asyncio
    async def test_assemble_async_with_scope(
        self,
        mock_vector_store,
        mock_embedding_service,
        default_policy,
    ):
        """Test async assembly with v1.0.2 scope parameters."""
        assembler = ContextAssembler(
            vector_store=mock_vector_store,
            embedding_service=mock_embedding_service,
        )

        packet = await assembler.assemble_async(
            intent="Scoped query",
            policy=default_policy,
            vault_id="test_vault",
            project_id="test_project",
            workflow_id="daily_checkin",
        )

        # Should return a valid packet
        assert packet.packet_id is not None
        assert packet.project_id == "test_project"

        # v1.0.2: The async pipeline uses the executor which validates against
        # source registry. Items may be empty if sources aren't configured.
        # The key assertion is that the packet is valid with the right project.
        assert isinstance(packet.items, list)

    @pytest.mark.asyncio
    async def test_assemble_async_embedding_failure(
        self,
        mock_vector_store,
        default_policy,
    ):
        """Test async assembly handles embedding failure gracefully."""
        failing_service = MagicMock()
        failing_service.embed = AsyncMock(side_effect=Exception("API Error"))

        assembler = ContextAssembler(
            vector_store=mock_vector_store,
            embedding_service=failing_service,
        )

        # Should not raise, just return a packet (possibly empty)
        packet = await assembler.assemble_async(
            intent="This will fail to embed",
            policy=default_policy,
        )

        assert packet is not None
        assert packet.packet_id is not None

    @pytest.mark.asyncio
    async def test_assemble_async_no_embedding_service(
        self,
        mock_document_store,
        default_policy,
    ):
        """Test async assembly without embedding service.

        v1.0.2: The async pipeline uses the retrieval executor which
        routes to different stores based on directives. Without proper
        source descriptors configured, items may be empty.
        """
        assembler = ContextAssembler(
            document_store=mock_document_store,
        )

        packet = await assembler.assemble_async(
            intent="No embedding service",
            policy=default_policy,
        )

        # Should return a valid packet
        assert packet is not None
        assert packet.packet_id is not None
        # v1.0.2: Check that retrieval report has the quality field
        assert packet.retrieval_report is not None
