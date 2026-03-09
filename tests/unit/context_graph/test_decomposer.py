"""Tests for TraceDecomposer - trace → graph structure."""

from __future__ import annotations

import pytest

from agent_kernel.context_graph.decomposer import TraceDecomposer
from agent_kernel.core.schemas.context import ContextRef, RefType
from agent_kernel.core.schemas.graph import EdgeType, NodeType
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
def decomposer(graph_store):
    return TraceDecomposer(graph_store=graph_store)


def _make_trace(
    trace_id: str = "test_trace_001",
    intent: str = "Check outstanding tasks",
    tool_calls: list | None = None,
    citations: list | None = None,
    artifacts: list | None = None,
) -> DecisionTrace:
    """Create a minimal DecisionTrace for testing."""
    plan = Plan(
        intent=intent,
        summary="Test plan",
        actions=[
            ActionRequest(
                capability_name="tasks.list@v1",
                args={"project": "test"},
            ),
        ],
        context_refs_used=citations or [],
    )

    return DecisionTrace(
        trace_id=trace_id,
        agent_profile_id="test_agent",
        engine_id="test_engine",
        intent=intent,
        context_packet_id="pkt_001",
        plan=plan,
        tool_calls=(
            tool_calls
            if tool_calls is not None
            else [
                ToolCallRecord(
                    capability_name="tasks.list",
                    input={"project": "test"},
                    output={"tasks": ["task1", "task2"]},
                    status=CallStatus.SUCCESS,
                    duration_ms=100,
                ),
            ]
        ),
        outcome=Outcome(
            status=OutcomeStatus.COMPLETED,
            summary="Listed 2 tasks",
            artifacts=artifacts or [],
        ),
    )


@pytest.mark.asyncio
async def test_decompose_creates_trajectory(decomposer, graph_store):
    """Decomposing a trace should create a TRAJECTORY node."""
    trace = _make_trace()
    result = await decomposer.decompose(trace)

    assert result.trajectory_node_id == f"trajectory:{trace.trace_id}"
    assert result.nodes_created >= 1

    node = graph_store.get_node(result.trajectory_node_id)
    assert node is not None
    assert node["node_type"] == NodeType.TRAJECTORY.value
    assert node["properties"]["intent"] == "Check outstanding tasks"
    assert node["properties"]["outcome_status"] == "completed"


@pytest.mark.asyncio
async def test_decompose_creates_decision_events(decomposer, graph_store):
    """Decomposing a trace should create DECISION_EVENT nodes for each tool call."""
    trace = _make_trace(
        tool_calls=[
            ToolCallRecord(
                capability_name="tasks.list",
                input={"project": "test"},
                output={"tasks": ["t1"]},
                status=CallStatus.SUCCESS,
                duration_ms=50,
            ),
            ToolCallRecord(
                capability_name="tasks.create",
                input={"title": "New task"},
                output={"task_id": "t2"},
                status=CallStatus.SUCCESS,
                duration_ms=80,
            ),
        ],
    )

    result = await decomposer.decompose(trace)

    assert len(result.decision_event_ids) == 2

    # Check first event
    event0 = graph_store.get_node(result.decision_event_ids[0])
    assert event0 is not None
    assert event0["node_type"] == NodeType.DECISION_EVENT.value
    assert event0["properties"]["step_order"] == 0
    assert event0["properties"]["capability_name"] == "tasks.list"

    # Check second event
    event1 = graph_store.get_node(result.decision_event_ids[1])
    assert event1["properties"]["step_order"] == 1
    assert event1["properties"]["capability_name"] == "tasks.create"


@pytest.mark.asyncio
async def test_decompose_creates_trajectory_decided_edges(decomposer, graph_store):
    """TRAJECTORY_DECIDED edges should link trajectory to events."""
    trace = _make_trace()
    result = await decomposer.decompose(trace)

    edges = graph_store.get_edges(
        result.trajectory_node_id,
        direction="outgoing",
        edge_type=EdgeType.TRAJECTORY_DECIDED.value,
    )
    assert len(edges) == len(result.decision_event_ids)


@pytest.mark.asyncio
async def test_decompose_creates_preceded_by_edges(decomposer, graph_store):
    """PRECEDED_BY edges should link events in causal order."""
    trace = _make_trace(
        tool_calls=[
            ToolCallRecord(
                capability_name="a",
                input={},
                status=CallStatus.SUCCESS,
                duration_ms=10,
            ),
            ToolCallRecord(
                capability_name="b",
                input={},
                status=CallStatus.SUCCESS,
                duration_ms=20,
            ),
        ],
    )

    result = await decomposer.decompose(trace)

    # Second event should have PRECEDED_BY edge to first
    edges = graph_store.get_edges(
        result.decision_event_ids[1],
        direction="outgoing",
        edge_type=EdgeType.PRECEDED_BY.value,
    )
    assert len(edges) == 1
    assert edges[0]["target_id"] == result.decision_event_ids[0]


@pytest.mark.asyncio
async def test_decompose_links_cited_entities(decomposer, graph_store):
    """Trace citations should become TRAJECTORY_TOUCHED edges."""
    citations = [
        ContextRef(ref_type=RefType.NOTE, ref_id="note_001"),
        ContextRef(ref_type=RefType.TASK, ref_id="task_001"),
    ]
    trace = _make_trace(citations=citations)

    result = await decomposer.decompose(trace)

    assert result.entities_linked == 2

    edges = graph_store.get_edges(
        result.trajectory_node_id,
        direction="outgoing",
        edge_type=EdgeType.TRAJECTORY_TOUCHED.value,
    )
    assert len(edges) == 2


@pytest.mark.asyncio
async def test_decompose_links_artifacts(decomposer, graph_store):
    """Outcome artifacts should become TRAJECTORY_PRODUCED edges."""
    artifacts = [
        ContextRef(ref_type=RefType.NOTE, ref_id="created_note"),
    ]
    trace = _make_trace(artifacts=artifacts)

    result = await decomposer.decompose(trace)

    edges = graph_store.get_edges(
        result.trajectory_node_id,
        direction="outgoing",
        edge_type=EdgeType.TRAJECTORY_PRODUCED.value,
    )
    assert len(edges) == 1


@pytest.mark.asyncio
async def test_decompose_co_occurrence_edges(decomposer, graph_store):
    """Entities in the same trajectory should get CO_OCCURS_WITH edges."""
    citations = [
        ContextRef(ref_type=RefType.NOTE, ref_id="note_a"),
        ContextRef(ref_type=RefType.NOTE, ref_id="note_b"),
        ContextRef(ref_type=RefType.TASK, ref_id="task_c"),
    ]
    trace = _make_trace(citations=citations)

    result = await decomposer.decompose(trace)

    # 3 entities → 3 pairs: (a,b), (a,c), (b,c)
    assert result.co_occurrence_edges_updated == 3


@pytest.mark.asyncio
async def test_decompose_co_occurrence_weight_increments(decomposer, graph_store):
    """Multiple traces with same entities should increment co-occurrence weight."""
    citations = [
        ContextRef(ref_type=RefType.NOTE, ref_id="shared_note"),
        ContextRef(ref_type=RefType.TASK, ref_id="shared_task"),
    ]

    trace1 = _make_trace(trace_id="t1", citations=citations)
    trace2 = _make_trace(trace_id="t2", citations=citations)

    await decomposer.decompose(trace1)
    await decomposer.decompose(trace2)

    # Check co-occurrence weight is 2
    source = "note:shared_note"
    target = "task:shared_task"
    # Normalize order
    if source > target:
        source, target = target, source

    edges = graph_store.get_edges(
        source,
        direction="outgoing",
        edge_type=EdgeType.CO_OCCURS_WITH.value,
    )
    co_edge = next((e for e in edges if e["target_id"] == target), None)
    assert co_edge is not None
    assert co_edge["properties"]["weight"] == 2


@pytest.mark.asyncio
async def test_decompose_empty_trace(decomposer, graph_store):
    """A trace with no tool calls should still create trajectory."""
    trace = _make_trace(tool_calls=[])

    result = await decomposer.decompose(trace)

    assert result.trajectory_node_id is not None
    assert result.decision_event_ids == []
    assert result.nodes_created == 1
