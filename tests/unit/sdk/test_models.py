"""Tests for SDK Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_kernel_sdk.models import (
    ActionRecord,
    ContextItem,
    ContextResponse,
    EntityHistoryItem,
    EntityHistoryResponse,
    KnowledgeAddResponse,
    KnowledgeNode,
    KnowledgeSearchResponse,
    Lesson,
    Playbook,
    TraceIngestRequest,
    TraceIngestResponse,
    TraceOutcome,
)


class TestActionRecord:
    def test_minimal(self):
        a = ActionRecord(capability="tasks.list@v1")
        assert a.capability == "tasks.list@v1"
        assert a.input == {}
        assert a.output is None
        assert a.status == "success"
        assert a.duration_ms == 0

    def test_full(self):
        a = ActionRecord(
            capability="tasks.create@v1",
            input={"title": "test"},
            output={"task_id": "123"},
            status="error",
            duration_ms=150,
        )
        assert a.status == "error"
        assert a.output == {"task_id": "123"}

    def test_invalid_status(self):
        with pytest.raises(ValidationError):
            ActionRecord(capability="x", status="invalid")


class TestTraceOutcome:
    def test_completed(self):
        o = TraceOutcome(status="completed")
        assert o.summary is None

    def test_failed_with_summary(self):
        o = TraceOutcome(status="failed", summary="Connection refused")
        assert o.status == "failed"

    def test_invalid_status(self):
        with pytest.raises(ValidationError):
            TraceOutcome(status="bad")


class TestTraceIngestRequest:
    def test_minimal(self):
        r = TraceIngestRequest(
            agent_id="my_agent",
            intent="sync tasks",
            outcome=TraceOutcome(status="completed"),
        )
        assert r.actions == []
        assert r.session_id is None

    def test_with_actions(self):
        r = TraceIngestRequest(
            agent_id="agent",
            intent="test",
            actions=[ActionRecord(capability="x")],
            outcome=TraceOutcome(status="completed"),
            session_id="sess_1",
        )
        assert len(r.actions) == 1
        assert r.session_id == "sess_1"


class TestTraceIngestResponse:
    def test_success(self):
        r = TraceIngestResponse(
            trace_id="trace_01",
            trajectory_node_id="traj_01",
            success=True,
        )
        assert r.success

    def test_no_trajectory(self):
        r = TraceIngestResponse(trace_id="trace_01", success=True)
        assert r.trajectory_node_id is None


class TestContextItem:
    def test_fields(self):
        item = ContextItem(
            type="knowledge",
            title="Test",
            excerpt="Some text",
            relevance_score=0.85,
            source="graph",
        )
        assert item.relevance_score == 0.85


class TestContextResponse:
    def test_defaults(self):
        r = ContextResponse(packet_id="pkt_01")
        assert r.items == []
        assert r.enrichment_text == ""
        assert r.token_estimate == 0


class TestKnowledgeNode:
    def test_fields(self):
        n = KnowledgeNode(
            node_id="n1",
            node_type="concept",
            title="Test Node",
            description="A test",
            relevance_score=0.9,
        )
        assert n.node_type == "concept"

    def test_defaults(self):
        n = KnowledgeNode(node_id="n1", node_type="concept", title="T")
        assert n.description == ""
        assert n.relevance_score == 0.0
        assert n.freshness_score == 0.0
        assert n.confidence == 0.0


class TestKnowledgeSearchResponse:
    def test_empty(self):
        r = KnowledgeSearchResponse()
        assert r.results == []
        assert r.total_candidates == 0

    def test_with_results(self):
        r = KnowledgeSearchResponse(
            results=[
                KnowledgeNode(node_id="n1", node_type="concept", title="T")
            ],
            total_candidates=5,
            query_time_ms=42,
        )
        assert len(r.results) == 1


class TestKnowledgeAddResponse:
    def test_success(self):
        r = KnowledgeAddResponse(node_id="n1", success=True)
        assert r.success


class TestLesson:
    def test_fields(self):
        lesson = Lesson(
            lesson_id="l1",
            title="Test",
            lesson_text="Do this",
            confidence=0.8,
        )
        assert lesson.status == "active"


class TestPlaybook:
    def test_fields(self):
        p = Playbook(
            playbook_id="p1",
            name="Test Playbook",
            checklist=["step1", "step2"],
            pitfalls=["watch out"],
        )
        assert len(p.checklist) == 2

    def test_defaults(self):
        p = Playbook(playbook_id="p1", name="T")
        assert p.checklist == []
        assert p.pitfalls == []


class TestEntityHistory:
    def test_item(self):
        i = EntityHistoryItem(
            node_id="n1",
            intent="test",
            outcome_status="completed",
        )
        assert i.created_at == ""

    def test_response(self):
        r = EntityHistoryResponse(entity_node_id="n1")
        assert r.trajectories == []
