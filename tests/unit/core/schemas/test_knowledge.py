"""Tests for knowledge schemas - FreshnessScore, property models, etc."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_kernel.core.schemas.knowledge import (
    ConceptProperties,
    DataObjectProperties,
    DecisionEventProperties,
    DecompositionResult,
    DomainProperties,
    FreshnessScore,
    InsightProperties,
    KnowledgeNodeProperties,
    KnowledgeSource,
    KnowledgeTier,
    PatternProperties,
    SummaryProperties,
    SystemProperties,
    TrajectoryProperties,
)


class TestFreshnessScore:
    """Tests for FreshnessScore time-decay model."""

    def test_default_freshness(self) -> None:
        f = FreshnessScore()
        assert f.base_relevance == 1.0
        assert f.access_count == 0
        assert f.decay_rate == 0.01
        assert f.pinned is False

    def test_effective_relevance_no_decay(self) -> None:
        now = datetime.now(UTC)
        f = FreshnessScore(
            base_relevance=1.0,
            decay_rate=0.0,
            last_accessed_at=now,
            last_reinforced_at=now,
        )
        future = now + timedelta(days=365)
        assert f.effective_relevance(future) == 1.0

    def test_effective_relevance_with_decay(self) -> None:
        now = datetime.now(UTC)
        f = FreshnessScore(
            base_relevance=1.0,
            decay_rate=0.01,
            last_accessed_at=now,
            last_reinforced_at=now,
        )
        future = now + timedelta(days=100)
        score = f.effective_relevance(future)
        expected = 1.0 * (0.99**100)
        assert abs(score - expected) < 0.001

    def test_effective_relevance_pinned(self) -> None:
        now = datetime.now(UTC)
        f = FreshnessScore(
            base_relevance=0.8,
            pinned=True,
            last_accessed_at=now,
            last_reinforced_at=now,
        )
        future = now + timedelta(days=10000)
        assert f.effective_relevance(future) == 0.8

    def test_effective_relevance_uses_latest_touch(self) -> None:
        now = datetime.now(UTC)
        old = now - timedelta(days=100)
        f = FreshnessScore(
            base_relevance=1.0,
            decay_rate=0.01,
            last_accessed_at=old,
            last_reinforced_at=now,  # More recent
        )
        # Should use last_reinforced_at since it's more recent
        score = f.effective_relevance(now)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_effective_relevance_same_time(self) -> None:
        now = datetime.now(UTC)
        f = FreshnessScore(
            base_relevance=1.0,
            last_accessed_at=now,
            last_reinforced_at=now,
        )
        assert f.effective_relevance(now) == 1.0

    def test_high_decay_rate(self) -> None:
        now = datetime.now(UTC)
        f = FreshnessScore(
            base_relevance=1.0,
            decay_rate=0.5,
            last_accessed_at=now,
            last_reinforced_at=now,
        )
        future = now + timedelta(days=10)
        score = f.effective_relevance(future)
        expected = 1.0 * (0.5**10)
        assert abs(score - expected) < 0.001

    def test_serialization_roundtrip(self) -> None:
        f = FreshnessScore(
            base_relevance=0.9,
            decay_rate=0.05,
            access_count=42,
            pinned=True,
        )
        data = f.model_dump(mode="json")
        f2 = FreshnessScore.model_validate(data)
        assert f2.base_relevance == 0.9
        assert f2.decay_rate == 0.05
        assert f2.access_count == 42
        assert f2.pinned is True


class TestKnowledgeNodeProperties:
    """Tests for KnowledgeNodeProperties and subclasses."""

    def test_base_properties(self) -> None:
        props = KnowledgeNodeProperties(
            title="Test Knowledge",
            knowledge_source=KnowledgeSource.MANUAL,
        )
        assert props.title == "Test Knowledge"
        assert props.knowledge_source == KnowledgeSource.MANUAL
        assert props.confidence == 1.0
        assert props.tier == KnowledgeTier.HOT

    def test_domain_properties(self) -> None:
        props = DomainProperties(
            title="Engineering",
            knowledge_source=KnowledgeSource.MANUAL,
            domain_scope="engineering",
        )
        assert props.domain_scope == "engineering"

    def test_system_properties(self) -> None:
        props = SystemProperties(
            title="PostgreSQL",
            knowledge_source=KnowledgeSource.MANUAL,
            system_type="database",
            url="https://db.example.com",
        )
        assert props.system_type == "database"

    def test_concept_properties(self) -> None:
        props = ConceptProperties(
            title="Context Graph",
            knowledge_source=KnowledgeSource.MANUAL,
            aliases=["knowledge graph", "entity graph"],
        )
        assert len(props.aliases) == 2

    def test_insight_properties(self) -> None:
        props = InsightProperties(
            title="Retry with backoff",
            knowledge_source=KnowledgeSource.TRACE,
            confidence=0.8,
            insight_type="optimization",
            applicable_contexts=["daily_checkin"],
        )
        assert props.insight_type == "optimization"

    def test_pattern_properties(self) -> None:
        props = PatternProperties(
            title="DB + API co-occurrence",
            knowledge_source=KnowledgeSource.INFERENCE,
            occurrence_count=5,
        )
        assert props.occurrence_count == 5

    def test_data_object_properties(self) -> None:
        props = DataObjectProperties(
            title="users table",
            knowledge_source=KnowledgeSource.MANUAL,
            object_type="table",
            system_id="system:postgres",
        )
        assert props.object_type == "table"

    def test_summary_properties(self) -> None:
        props = SummaryProperties(
            title="Summary: Q1 Insights",
            knowledge_source=KnowledgeSource.COMPACTION,
            summarized_node_ids=["a", "b", "c"],
            original_count=3,
        )
        assert props.original_count == 3

    def test_serialization_roundtrip(self) -> None:
        props = KnowledgeNodeProperties(
            title="Test",
            knowledge_source=KnowledgeSource.TRACE,
            confidence=0.7,
            tags=["test", "example"],
        )
        data = props.model_dump(mode="json")
        assert data["title"] == "Test"
        assert data["confidence"] == 0.7
        assert "freshness" in data


class TestTrajectoryProperties:
    """Tests for TrajectoryProperties."""

    def test_basic_trajectory(self) -> None:
        props = TrajectoryProperties(
            trace_id="trace_123",
            agent_profile_id="agent_daily",
            intent="Check outstanding tasks",
            outcome_status="completed",
        )
        assert props.trace_id == "trace_123"
        assert props.step_count == 0
        assert props.reasoning_tier == 1

    def test_full_trajectory(self) -> None:
        props = TrajectoryProperties(
            trace_id="trace_456",
            agent_profile_id="agent_review",
            intent="Review code changes",
            workflow_id="code_review",
            outcome_status="completed",
            outcome_summary="Reviewed 3 PRs",
            entities_touched=["pr:1", "pr:2", "pr:3"],
            capabilities_used=["github.list_prs", "github.review"],
            step_count=5,
            duration_ms=12000,
            reasoning_tier=2,
        )
        assert len(props.entities_touched) == 3
        assert len(props.capabilities_used) == 2
        assert props.duration_ms == 12000


class TestDecisionEventProperties:
    """Tests for DecisionEventProperties."""

    def test_basic_event(self) -> None:
        props = DecisionEventProperties(
            trace_id="trace_123",
            step_order=0,
            action_type="tool_call",
            capability_name="tasks.list",
            status="success",
        )
        assert props.step_order == 0
        assert props.capability_name == "tasks.list"

    def test_failed_event(self) -> None:
        props = DecisionEventProperties(
            trace_id="trace_123",
            step_order=2,
            action_type="tool_call",
            capability_name="api.fetch",
            status="error",
            duration_ms=5000,
            output_summary="Connection timeout",
        )
        assert props.status == "error"
        assert props.duration_ms == 5000


class TestDecompositionResult:
    """Tests for DecompositionResult."""

    def test_basic_result(self) -> None:
        result = DecompositionResult(
            trajectory_node_id="trajectory:trace_123",
            decision_event_ids=["event:0", "event:1"],
            entities_linked=3,
            co_occurrence_edges_updated=3,
            nodes_created=3,
            edges_created=9,
        )
        assert len(result.decision_event_ids) == 2
        assert result.entities_linked == 3
