"""Tests for external-agent-to-Kernel bridge API endpoints.

Tests knowledge search, knowledge add, entity history,
trace ingestion, and context assembly endpoints.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent_kernel.api.server import (
    _build_enrichment_text,
    _format_trajectory_summary,
    ContextEnrichmentItem,
    create_app,
)
from agent_kernel.context_graph.query import (
    ContextGraphQueryResult,
    ScoredNode,
)
from agent_kernel.core.schemas.knowledge import DecompositionResult


# =============================================================================
# Fixtures
# =============================================================================


def _make_scored_node(
    node_id: str = "concept:abc",
    node_type: str = "concept",
    title: str = "Test Concept",
    description: str = "A test concept",
    relevance: float = 0.9,
    **extra_props,
) -> ScoredNode:
    """Helper to build a ScoredNode for tests."""
    props = {"title": title, "description": description, **extra_props}
    return ScoredNode(
        node_id=node_id,
        node_type=node_type,
        properties=props,
        relevance_score=relevance,
        freshness_score=0.8,
        confidence=1.0,
    )


@pytest.fixture
def mock_cg_query():
    """Create mock ContextGraphQueryService."""
    svc = AsyncMock()
    svc.query = AsyncMock(return_value=ContextGraphQueryResult(
        nodes=[
            _make_scored_node(
                node_id="concept:001",
                title="Daily Planning",
                description="Concepts around daily planning routines",
            ),
        ],
        total_candidates=1,
        query_time_ms=5,
    ))
    svc.find_similar_trajectories = AsyncMock(return_value=[
        _make_scored_node(
            node_id="trajectory:t1",
            node_type="trajectory",
            title="",
            description="",
            intent="plan my day",
            outcome_summary="Created daily plan",
            outcome_status="completed",
            relevance=0.7,
        ),
    ])
    svc.find_relevant_knowledge = AsyncMock(return_value=[
        _make_scored_node(
            node_id="concept:001",
            title="Daily Planning",
            description="Concepts around daily planning routines",
        ),
    ])
    svc.get_entity_history = AsyncMock(return_value=[
        _make_scored_node(
            node_id="trajectory:t1",
            node_type="trajectory",
            title="",
            description="",
            intent="used this entity",
            outcome_status="completed",
            created_at="2026-01-15T10:00:00Z",
            relevance=0.9,
        ),
    ])
    return svc


@pytest.fixture
def mock_cg_ingestion():
    """Create mock ContextGraphIngestion."""
    ingestion = AsyncMock()
    ingestion.ingest_manual = AsyncMock(return_value="concept:new123")
    ingestion.ingest_trace = AsyncMock(return_value=DecompositionResult(
        trajectory_node_id="trajectory:abc",
        decision_event_ids=["event:e1"],
    ))
    return ingestion


@pytest.fixture
def mock_event_log():
    """Create mock EventLog."""
    log = MagicMock()
    log.emit = MagicMock()
    return log


@pytest.fixture
def bridge_client(mock_cg_query, mock_cg_ingestion, mock_event_log):
    """Create test client with bridge dependencies."""
    app = create_app(
        context_graph_query=mock_cg_query,
        context_graph_ingestion=mock_cg_ingestion,
        event_log=mock_event_log,
    )
    return TestClient(app)


@pytest.fixture
def bare_client():
    """Create test client with no dependencies."""
    app = create_app()
    return TestClient(app)


# =============================================================================
# Status endpoint shows new components
# =============================================================================


class TestStatusWithBridge:
    """Status endpoint reports bridge component availability."""

    def test_status_shows_bridge_components(self, bridge_client):
        response = bridge_client.get("/status")
        data = response.json()

        assert data["components"]["context_graph_query"] is True
        assert data["components"]["context_graph_ingestion"] is True

    def test_status_shows_missing_bridge(self, bare_client):
        response = bare_client.get("/status")
        data = response.json()

        assert data["components"]["context_graph_query"] is False
        assert data["components"]["context_graph_ingestion"] is False
        assert data["components"]["context_assembler"] is False


# =============================================================================
# Knowledge Search
# =============================================================================


class TestKnowledgeSearch:
    """Tests for POST /knowledge/search."""

    def test_search_success(self, bridge_client, mock_cg_query):
        response = bridge_client.post(
            "/knowledge/search",
            json={"query": "daily planning"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) > 0
        assert data["results"][0]["node_id"] == "concept:001"
        assert data["results"][0]["title"] == "Daily Planning"
        assert data["total_candidates"] >= 1
        assert data["query_time_ms"] >= 0

    def test_search_with_node_types(self, bridge_client, mock_cg_query):
        response = bridge_client.post(
            "/knowledge/search",
            json={
                "query": "test",
                "node_types": ["concept", "insight"],
                "include_trajectories": False,
            },
        )

        assert response.status_code == 200
        # With explicit node_types, trajectories should not be searched
        mock_cg_query.find_similar_trajectories.assert_not_called()

    def test_search_with_tags(self, bridge_client, mock_cg_query):
        response = bridge_client.post(
            "/knowledge/search",
            json={"query": "test", "tags": ["planning"]},
        )

        assert response.status_code == 200
        call_args = mock_cg_query.query.call_args
        q = call_args[0][0]
        assert q.tags == ["planning"]

    def test_search_with_limit(self, bridge_client):
        response = bridge_client.post(
            "/knowledge/search",
            json={"query": "test", "limit": 5},
        )

        assert response.status_code == 200

    def test_search_not_configured(self, bare_client):
        response = bare_client.post(
            "/knowledge/search",
            json={"query": "test"},
        )

        assert response.status_code == 503

    def test_search_includes_trajectories_by_default(
        self, bridge_client, mock_cg_query,
    ):
        response = bridge_client.post(
            "/knowledge/search",
            json={"query": "plan my day"},
        )

        assert response.status_code == 200
        mock_cg_query.find_similar_trajectories.assert_called_once()

        # Trajectory results should be included
        data = response.json()
        types = {r["node_type"] for r in data["results"]}
        assert "trajectory" in types


# =============================================================================
# Knowledge Add
# =============================================================================


class TestKnowledgeAdd:
    """Tests for POST /knowledge/add."""

    def test_add_success(self, bridge_client, mock_cg_ingestion):
        response = bridge_client.post(
            "/knowledge/add",
            json={
                "node_type": "concept",
                "title": "Test Concept",
                "description": "A new concept",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["node_id"] == "concept:new123"
        assert data["success"] is True

        mock_cg_ingestion.ingest_manual.assert_called_once()
        call_args = mock_cg_ingestion.ingest_manual.call_args
        assert call_args.kwargs["node_type"] == "concept"
        assert call_args.kwargs["properties"]["title"] == "Test Concept"

    def test_add_with_tags_and_edges(self, bridge_client, mock_cg_ingestion):
        response = bridge_client.post(
            "/knowledge/add",
            json={
                "node_type": "insight",
                "title": "Insight X",
                "tags": ["important", "planning"],
                "confidence": 0.8,
                "edges": [
                    {"target_id": "concept:001", "edge_type": "related_to"},
                ],
            },
        )

        assert response.status_code == 200
        call_args = mock_cg_ingestion.ingest_manual.call_args
        assert call_args.kwargs["properties"]["tags"] == ["important", "planning"]
        assert call_args.kwargs["properties"]["confidence"] == 0.8
        assert len(call_args.kwargs["edges"]) == 1

    def test_add_with_source(self, bridge_client, mock_cg_ingestion):
        response = bridge_client.post(
            "/knowledge/add",
            json={
                "node_type": "concept",
                "title": "From External Agent",
                "source": "external_agent",
            },
        )

        assert response.status_code == 200
        call_args = mock_cg_ingestion.ingest_manual.call_args
        assert call_args.kwargs["properties"]["knowledge_source"] == "external_agent"

    def test_add_not_configured(self, bare_client):
        response = bare_client.post(
            "/knowledge/add",
            json={"node_type": "concept", "title": "Test"},
        )

        assert response.status_code == 503

    def test_add_edges_filter_empty_targets(self, bridge_client, mock_cg_ingestion):
        """Edges with empty target_id should be filtered out."""
        response = bridge_client.post(
            "/knowledge/add",
            json={
                "node_type": "concept",
                "title": "Test",
                "edges": [
                    {"target_id": "", "edge_type": "related_to"},
                    {"target_id": "concept:001", "edge_type": "related_to"},
                ],
            },
        )

        assert response.status_code == 200
        call_args = mock_cg_ingestion.ingest_manual.call_args
        # Only the non-empty edge should be passed
        assert len(call_args.kwargs["edges"]) == 1
        assert call_args.kwargs["edges"][0]["target_id"] == "concept:001"


# =============================================================================
# Entity History
# =============================================================================


class TestEntityHistory:
    """Tests for GET /knowledge/{node_id}/history."""

    def test_history_success(self, bridge_client, mock_cg_query):
        response = bridge_client.get("/knowledge/concept:001/history")

        assert response.status_code == 200
        data = response.json()
        assert data["entity_node_id"] == "concept:001"
        assert len(data["trajectories"]) == 1
        assert data["trajectories"][0]["intent"] == "used this entity"

    def test_history_empty(self, bridge_client, mock_cg_query):
        mock_cg_query.get_entity_history = AsyncMock(return_value=[])

        response = bridge_client.get("/knowledge/concept:nonexistent/history")

        assert response.status_code == 200
        data = response.json()
        assert data["trajectories"] == []

    def test_history_not_configured(self, bare_client):
        response = bare_client.get("/knowledge/concept:001/history")

        assert response.status_code == 503


# =============================================================================
# Trace Ingestion
# =============================================================================


class TestTraceIngestion:
    """Tests for POST /traces/ingest."""

    def test_ingest_success(self, bridge_client, mock_cg_ingestion, mock_event_log):
        response = bridge_client.post(
            "/traces/ingest",
            json={
                "agent_id": "example",
                "intent": "search for notes about planning",
                "actions": [
                    {
                        "capability": "notes.search@v1",
                        "input": {"query": "planning"},
                        "output": {"results": []},
                        "status": "success",
                        "duration_ms": 50,
                    },
                ],
                "outcome": {"status": "completed", "summary": "Found 0 notes"},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["trace_id"]
        assert data["trajectory_node_id"] == "trajectory:abc"

        # Verify trace was ingested
        mock_cg_ingestion.ingest_trace.assert_called_once()
        trace = mock_cg_ingestion.ingest_trace.call_args[0][0]
        assert trace.agent_profile_id == "example_agent"
        assert trace.engine_id == "external"
        assert trace.intent == "search for notes about planning"

        # Verify audit event was emitted
        mock_event_log.emit.assert_called_once()

    def test_ingest_maps_agent_ids(self, bridge_client, mock_cg_ingestion):
        """Agent IDs from external runtime map to kernel profile IDs."""
        for oc_id, kernel_id in [
            ("example", "example_agent"),
            ("work", "work_agent"),
            ("code", "code_agent"),
        ]:
            mock_cg_ingestion.ingest_trace.reset_mock()

            response = bridge_client.post(
                "/traces/ingest",
                json={"agent_id": oc_id, "intent": "test"},
            )

            assert response.status_code == 200
            trace = mock_cg_ingestion.ingest_trace.call_args[0][0]
            assert trace.agent_profile_id == kernel_id

    def test_ingest_unknown_agent_fallback(self, bridge_client, mock_cg_ingestion):
        """Unknown agent IDs get a suffix appended."""
        response = bridge_client.post(
            "/traces/ingest",
            json={"agent_id": "custom_agent", "intent": "test"},
        )

        assert response.status_code == 200
        trace = mock_cg_ingestion.ingest_trace.call_args[0][0]
        assert trace.agent_profile_id == "custom_agent_agent"

    def test_ingest_minimal_payload(self, bridge_client, mock_cg_ingestion):
        """Minimal payload with just agent_id and intent."""
        response = bridge_client.post(
            "/traces/ingest",
            json={"agent_id": "example", "intent": "hello"},
        )

        assert response.status_code == 200
        trace = mock_cg_ingestion.ingest_trace.call_args[0][0]
        assert len(trace.tool_calls) == 0
        assert trace.outcome.status.value == "completed"

    def test_ingest_with_session_id(self, bridge_client, mock_cg_ingestion):
        response = bridge_client.post(
            "/traces/ingest",
            json={
                "agent_id": "example",
                "intent": "test",
                "session_id": "session-abc-123",
            },
        )

        assert response.status_code == 200
        trace = mock_cg_ingestion.ingest_trace.call_args[0][0]
        assert "session-abc-123" in trace.context_packet_id

    def test_ingest_invalid_outcome_status(self, bridge_client, mock_cg_ingestion):
        """Invalid outcome status falls back to 'completed'."""
        response = bridge_client.post(
            "/traces/ingest",
            json={
                "agent_id": "example",
                "intent": "test",
                "outcome": {"status": "not_a_real_status"},
            },
        )

        assert response.status_code == 200
        trace = mock_cg_ingestion.ingest_trace.call_args[0][0]
        assert trace.outcome.status.value == "completed"

    def test_ingest_not_configured(self, bare_client):
        response = bare_client.post(
            "/traces/ingest",
            json={"agent_id": "example", "intent": "test"},
        )

        assert response.status_code == 503

    def test_ingest_bridge_field_names(self, bridge_client, mock_cg_ingestion):
        """Bridge sends capability_name/input_summary/output_summary fields."""
        response = bridge_client.post(
            "/traces/ingest",
            json={
                "agent_id": "example",
                "intent": "external_agent_run",
                "actions": [
                    {
                        "capability_name": "exec_command",
                        "input_summary": '{"command":"git status"}',
                        "output_summary": "On branch main",
                        "status": "success",
                        "duration_ms": 120,
                    },
                ],
                "outcome": {"status": "completed", "summary": "Done"},
            },
        )

        assert response.status_code == 200
        trace = mock_cg_ingestion.ingest_trace.call_args[0][0]
        assert len(trace.tool_calls) == 1
        tc = trace.tool_calls[0]
        assert tc.capability_name == "exec_command"
        assert tc.input == {"summary": '{"command":"git status"}'}
        assert tc.output == {"summary": "On branch main"}

    def test_ingest_persists_to_trace_store(
        self, mock_cg_query, mock_cg_ingestion, mock_event_log,
    ):
        """Traces are written to trace_store when available."""
        mock_trace_store = MagicMock()
        mock_trace_store.write = MagicMock()
        app = create_app(
            context_graph_query=mock_cg_query,
            context_graph_ingestion=mock_cg_ingestion,
            event_log=mock_event_log,
            trace_store=mock_trace_store,
        )
        client = TestClient(app)

        response = client.post(
            "/traces/ingest",
            json={"agent_id": "example", "intent": "test persistence"},
        )

        assert response.status_code == 200
        mock_trace_store.write.assert_called_once()
        written_trace = mock_trace_store.write.call_args[0][0]
        assert written_trace.intent == "test persistence"


# =============================================================================
# Context Assembly
# =============================================================================


class TestContextAssembly:
    """Tests for POST /context/assemble."""

    def test_assemble_success(self, bridge_client, mock_cg_query):
        response = bridge_client.post(
            "/context/assemble",
            json={"intent": "plan my day", "agent_id": "example"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["packet_id"]
        assert len(data["items"]) > 0
        assert data["enrichment_text"]
        assert data["token_estimate"] > 0

    def test_assemble_returns_knowledge_items(self, bridge_client, mock_cg_query):
        response = bridge_client.post(
            "/context/assemble",
            json={"intent": "planning", "agent_id": "example"},
        )

        data = response.json()
        types = {item["type"] for item in data["items"]}
        assert "knowledge" in types

    def test_assemble_returns_trajectory_items(self, bridge_client, mock_cg_query):
        response = bridge_client.post(
            "/context/assemble",
            json={"intent": "plan my day", "agent_id": "example"},
        )

        data = response.json()
        types = {item["type"] for item in data["items"]}
        assert "trajectory" in types

    def test_assemble_enrichment_text_has_sections(self, bridge_client, mock_cg_query):
        response = bridge_client.post(
            "/context/assemble",
            json={"intent": "plan my day", "agent_id": "example"},
        )

        data = response.json()
        text = data["enrichment_text"]
        assert "## Relevant Knowledge" in text
        assert "## Similar Past Actions" in text

    def test_assemble_empty_graph(self, bridge_client, mock_cg_query):
        """When no knowledge or trajectories exist."""
        mock_cg_query.find_relevant_knowledge = AsyncMock(return_value=[])
        mock_cg_query.find_similar_trajectories = AsyncMock(return_value=[])

        response = bridge_client.post(
            "/context/assemble",
            json={"intent": "anything", "agent_id": "example"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["enrichment_text"] == ""
        assert data["token_estimate"] == 0

    def test_assemble_custom_max_tokens(self, bridge_client, mock_cg_query):
        response = bridge_client.post(
            "/context/assemble",
            json={
                "intent": "test",
                "agent_id": "example",
                "max_tokens": 500,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["token_estimate"] <= 500

    def test_assemble_not_configured(self, bare_client):
        response = bare_client.post(
            "/context/assemble",
            json={"intent": "test", "agent_id": "example"},
        )

        assert response.status_code == 503


# =============================================================================
# Helper Functions
# =============================================================================


class TestHelpers:
    """Tests for helper functions."""

    def test_format_trajectory_summary_full(self):
        props = {
            "intent": "search notes",
            "outcome_summary": "Found 3 notes",
            "outcome_status": "completed",
            "capabilities_used": ["notes.search@v1", "notes.get@v1"],
        }
        result = _format_trajectory_summary(props)
        assert "search notes" in result
        assert "Found 3 notes" in result
        assert "completed" in result
        assert "notes.search@v1" in result

    def test_format_trajectory_summary_minimal(self):
        result = _format_trajectory_summary({"intent": "test"})
        assert "Past: test" in result

    def test_build_enrichment_text_knowledge_only(self):
        items = [
            ContextEnrichmentItem(
                type="knowledge",
                title="Concept A",
                excerpt="Description A",
                relevance_score=0.9,
                source="context_graph",
            ),
        ]
        text = _build_enrichment_text(items)
        assert "## Relevant Knowledge" in text
        assert "**Concept A**" in text
        assert "## Similar Past Actions" not in text

    def test_build_enrichment_text_trajectories_only(self):
        items = [
            ContextEnrichmentItem(
                type="trajectory",
                title="Past action",
                excerpt="Did something",
                relevance_score=0.7,
                source="context_graph",
            ),
        ]
        text = _build_enrichment_text(items)
        assert "## Similar Past Actions" in text
        assert "## Relevant Knowledge" not in text

    def test_build_enrichment_text_empty(self):
        assert _build_enrichment_text([]) == ""

    def test_build_enrichment_text_mixed(self):
        items = [
            ContextEnrichmentItem(
                type="knowledge",
                title="K1",
                excerpt="knowledge excerpt",
                relevance_score=0.9,
                source="context_graph",
            ),
            ContextEnrichmentItem(
                type="trajectory",
                title="T1",
                excerpt="trajectory excerpt",
                relevance_score=0.7,
                source="context_graph",
            ),
        ]
        text = _build_enrichment_text(items)
        assert "## Relevant Knowledge" in text
        assert "## Similar Past Actions" in text
        assert "knowledge excerpt" in text
        assert "trajectory excerpt" in text


# =============================================================================
# Agent Profile Mapping
# =============================================================================


class TestAgentProfileMapping:
    """Tests for custom agent profile mapping."""

    def test_custom_profile_map(self, mock_cg_query, mock_cg_ingestion):
        custom_map = {"my_agent": "my_kernel_profile"}
        app = create_app(
            context_graph_query=mock_cg_query,
            context_graph_ingestion=mock_cg_ingestion,
            agent_profile_map=custom_map,
        )
        client = TestClient(app)

        response = client.post(
            "/traces/ingest",
            json={"agent_id": "my_agent", "intent": "test"},
        )

        assert response.status_code == 200
        trace = mock_cg_ingestion.ingest_trace.call_args[0][0]
        assert trace.agent_profile_id == "my_kernel_profile"


# =============================================================================
# Existing endpoints still work
# =============================================================================


class TestBackwardsCompatibility:
    """Ensure existing endpoints still work with new factory params."""

    def test_health_still_works(self, bridge_client):
        response = bridge_client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_traces_still_work_without_store(self, bridge_client):
        response = bridge_client.get("/traces")
        assert response.status_code == 200
        assert response.json()["total_count"] == 0

    def test_capabilities_still_work_without_registry(self, bridge_client):
        response = bridge_client.get("/capabilities")
        assert response.status_code == 200
        assert response.json()["capabilities"] == []
