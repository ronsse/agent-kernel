"""Tests for ContextGraphIngestion - multi-source ingestion orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_kernel.context_graph.ingestion import ContextGraphIngestion
from agent_kernel.core.schemas.context import ContextRef, RefType
from agent_kernel.core.schemas.experience import LessonLearned, LessonScope, Playbook
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.core.schemas.knowledge import KnowledgeSource, KnowledgeTier
from agent_kernel.core.schemas.plan import ActionRequest, Plan
from agent_kernel.core.schemas.trace import (
    CallStatus,
    DecisionTrace,
    Outcome,
    OutcomeStatus,
    ToolCallRecord,
)
from agent_kernel.memory.graph_store import SQLiteGraphStore


@pytest.fixture
def graph_store(tmp_path):
    store = SQLiteGraphStore(tmp_path / "test.db")
    yield store
    store.close()


@pytest.fixture
def ingestion(graph_store):
    return ContextGraphIngestion(graph_store=graph_store)


def _make_trace(trace_id: str = "trace_001") -> DecisionTrace:
    """Create a minimal DecisionTrace for testing."""
    plan = Plan(
        intent="Check tasks",
        summary="Test plan",
        actions=[
            ActionRequest(
                capability_name="tasks.list@v1",
                args={"project": "test"},
            ),
        ],
    )

    return DecisionTrace(
        trace_id=trace_id,
        agent_profile_id="test_agent",
        engine_id="test_engine",
        intent="Check tasks",
        context_packet_id="pkt_001",
        plan=plan,
        tool_calls=[
            ToolCallRecord(
                capability_name="tasks.list",
                input={"project": "test"},
                output={"tasks": ["t1"]},
                status=CallStatus.SUCCESS,
                duration_ms=50,
            ),
        ],
        outcome=Outcome(
            status=OutcomeStatus.COMPLETED,
            summary="Listed 1 task",
            artifacts=[],
        ),
    )


@pytest.mark.asyncio
async def test_ingest_trace(ingestion, graph_store):
    """ingest_trace should decompose trace into graph structure."""
    trace = _make_trace()
    result = await ingestion.ingest_trace(trace)

    assert result.trajectory_node_id == "trajectory:trace_001"
    assert result.nodes_created >= 1

    node = graph_store.get_node("trajectory:trace_001")
    assert node is not None
    assert node["node_type"] == NodeType.TRAJECTORY.value


@pytest.mark.asyncio
async def test_ingest_manual_creates_node(ingestion, graph_store):
    """ingest_manual should create a node with given type and properties."""
    node_id = await ingestion.ingest_manual(
        node_type=NodeType.DOMAIN.value,
        properties={
            "title": "Engineering",
            "description": "Engineering domain",
        },
    )

    assert node_id.startswith("domain:")

    node = graph_store.get_node(node_id)
    assert node is not None
    assert node["node_type"] == NodeType.DOMAIN.value
    assert node["properties"]["title"] == "Engineering"
    # Should have auto-generated freshness
    assert "freshness" in node["properties"]
    # Should have auto-set knowledge_source
    assert node["properties"]["knowledge_source"] == KnowledgeSource.MANUAL.value


@pytest.mark.asyncio
async def test_ingest_manual_with_edges(ingestion, graph_store):
    """ingest_manual should create edges when provided."""
    # Create target node first
    target_id = await ingestion.ingest_manual(
        node_type=NodeType.SYSTEM.value,
        properties={"title": "PostgreSQL"},
    )

    source_id = await ingestion.ingest_manual(
        node_type=NodeType.DOMAIN.value,
        properties={"title": "Engineering"},
        edges=[
            {
                "target_id": target_id,
                "edge_type": EdgeType.DOMAIN_CONTAINS.value,
            },
        ],
    )

    edges = graph_store.get_edges(
        source_id,
        direction="outgoing",
        edge_type=EdgeType.DOMAIN_CONTAINS.value,
    )
    assert len(edges) == 1
    assert edges[0]["target_id"] == target_id


@pytest.mark.asyncio
async def test_ingest_manual_preserves_existing_properties(ingestion, graph_store):
    """ingest_manual should not override provided freshness/source/tier."""
    node_id = await ingestion.ingest_manual(
        node_type=NodeType.CONCEPT.value,
        properties={
            "title": "Test Concept",
            "knowledge_source": KnowledgeSource.TRACE.value,
            "tier": KnowledgeTier.WARM.value,
        },
    )

    node = graph_store.get_node(node_id)
    assert node["properties"]["knowledge_source"] == KnowledgeSource.TRACE.value
    assert node["properties"]["tier"] == KnowledgeTier.WARM.value


@pytest.mark.asyncio
async def test_ingest_lesson_creates_insight(ingestion, graph_store):
    """ingest_lesson should create an INSIGHT node from a LessonLearned."""
    now = datetime.now(UTC)
    lesson = LessonLearned(
        lesson_id="lesson_001",
        title="Retry with backoff",
        lesson_text="Always use exponential backoff when retrying API calls",
        scope=LessonScope(workflow_id="daily_checkin"),
        source_trace_ids=["trace_001"],
        confidence=0.8,
        created_at=now,
        updated_at=now,
    )

    node_id = await ingestion.ingest_lesson(lesson)

    assert node_id == "insight:lesson_001"

    node = graph_store.get_node(node_id)
    assert node is not None
    assert node["node_type"] == NodeType.INSIGHT.value
    assert node["properties"]["title"] == "Retry with backoff"
    assert node["properties"]["confidence"] == 0.8
    assert node["properties"]["insight_type"] == "lesson"
    assert "daily_checkin" in node["properties"]["applicable_contexts"]


@pytest.mark.asyncio
async def test_ingest_lesson_links_to_existing_trajectories(ingestion, graph_store):
    """ingest_lesson should create INSIGHT_DERIVED_FROM edges to existing trajectories."""
    # First create a trajectory via trace decomposition
    trace = _make_trace(trace_id="trace_001")
    await ingestion.ingest_trace(trace)

    now = datetime.now(UTC)
    lesson = LessonLearned(
        lesson_id="lesson_002",
        title="Check tasks first",
        lesson_text="Always check tasks before starting work",
        source_trace_ids=["trace_001"],
        confidence=0.9,
        created_at=now,
        updated_at=now,
    )

    node_id = await ingestion.ingest_lesson(lesson)

    edges = graph_store.get_edges(
        node_id,
        direction="outgoing",
        edge_type=EdgeType.INSIGHT_DERIVED_FROM.value,
    )
    assert len(edges) == 1
    assert edges[0]["target_id"] == "trajectory:trace_001"


@pytest.mark.asyncio
async def test_ingest_playbook_creates_practice(ingestion, graph_store):
    """ingest_playbook should create a PRACTICE node from a Playbook."""
    now = datetime.now(UTC)
    playbook = Playbook(
        playbook_id="pb_001",
        name="Daily Review",
        description="Steps for daily review workflow",
        derived_from_lessons=["lesson_001"],
        created_at=now,
        updated_at=now,
    )

    node_id = await ingestion.ingest_playbook(playbook)

    assert node_id == "practice:pb_001"

    node = graph_store.get_node(node_id)
    assert node is not None
    assert node["node_type"] == NodeType.PRACTICE.value
    assert node["properties"]["title"] == "Daily Review"
    assert node["properties"]["description"] == "Steps for daily review workflow"


@pytest.mark.asyncio
async def test_ingest_playbook_links_to_existing_insights(ingestion, graph_store):
    """ingest_playbook should create edges to existing INSIGHT nodes."""
    # Create an insight first
    now = datetime.now(UTC)
    lesson = LessonLearned(
        lesson_id="lesson_003",
        title="Check tasks",
        lesson_text="Check tasks first",
        source_trace_ids=[],
        confidence=0.9,
        created_at=now,
        updated_at=now,
    )
    await ingestion.ingest_lesson(lesson)

    playbook = Playbook(
        playbook_id="pb_002",
        name="Task Review",
        description="Review tasks",
        derived_from_lessons=["lesson_003"],
        created_at=now,
        updated_at=now,
    )

    node_id = await ingestion.ingest_playbook(playbook)

    edges = graph_store.get_edges(
        node_id,
        direction="outgoing",
        edge_type=EdgeType.INSIGHT_ABOUT.value,
    )
    assert len(edges) == 1
    assert edges[0]["target_id"] == "insight:lesson_003"
