"""Tests for QualityGateRunner."""

import pytest

from agent_kernel.core.schemas import (
    ActionRequest,
    AgentProfile,
    ContextBudget,
    ContextItem,
    ContextPacket,
    ContextPolicy,
    ContextRef,
    Plan,
    RefType,
    SideEffect,
)
from agent_kernel.executor.quality_gates import (
    GateSeverity,
    QualityGateRunner,
)


@pytest.fixture
def sample_agent_profile() -> AgentProfile:
    """Create a sample agent profile."""
    return AgentProfile(
        agent_profile_id="test_agent",
        name="Test Agent",
        engine="custom",
        allowed_capabilities=["tasks.list@v1", "tasks.create@v1"],
        context_policy=ContextPolicy(max_tokens=4000, must_cite=True),
    )


@pytest.fixture
def sample_context_packet() -> ContextPacket:
    """Create a sample context packet with items."""
    return ContextPacket(
        intent="Test intent",
        budget=ContextBudget(max_tokens=4000),
        items=[
            ContextItem(
                ref=ContextRef(ref_type=RefType.TASK, ref_id="task_123"),
                excerpt="Sample task content",
            ),
        ],
    )


@pytest.fixture
def empty_context_packet() -> ContextPacket:
    """Create an empty context packet."""
    return ContextPacket(
        intent="Test intent",
        budget=ContextBudget(max_tokens=4000),
        items=[],
    )


@pytest.fixture
def valid_plan() -> Plan:
    """Create a valid plan."""
    return Plan(
        intent="List tasks",
        summary="A simple plan to list tasks",
        context_refs_used=[
            ContextRef(ref_type=RefType.TASK, ref_id="task_123"),
        ],
        actions=[
            ActionRequest(
                capability_name="tasks.list@v1",
                args={"status": "open"},
                side_effect=SideEffect.NONE,
            ),
        ],
        confidence=0.9,
    )


class TestQualityGateRunner:
    """Tests for QualityGateRunner."""

    def test_initialization(self):
        """Test runner initializes correctly."""
        runner = QualityGateRunner()
        assert runner._confidence_threshold == 0.7

    def test_valid_plan_passes(
        self,
        valid_plan: Plan,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that a valid plan passes all gates."""
        runner = QualityGateRunner()

        result = runner.validate(
            valid_plan, sample_context_packet, sample_agent_profile
        )

        assert result.passed is True
        assert result.error_count == 0
        assert result.should_escalate is False

    def test_empty_intent_fails(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that empty intent fails schema validity."""
        runner = QualityGateRunner()
        plan = Plan(intent="", summary="Test")

        result = runner.validate(
            plan, sample_context_packet, sample_agent_profile
        )

        assert result.passed is False
        assert any(
            f.gate_name == "schema_validity" and f.severity == GateSeverity.ERROR
            for f in result.failures
        )

    def test_invalid_capability_fails(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that disallowed capability fails."""
        runner = QualityGateRunner()
        plan = Plan(
            intent="Send email",
            summary="Send an email",
            actions=[
                ActionRequest(
                    capability_name="email.send@v1",  # Not allowed
                    args={},
                ),
            ],
        )

        result = runner.validate(
            plan, sample_context_packet, sample_agent_profile
        )

        assert result.passed is False
        assert any(
            f.gate_name == "capability_allowlist"
            for f in result.failures
        )

    def test_missing_citations_with_context(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that missing citations fail when context exists."""
        runner = QualityGateRunner()
        plan = Plan(
            intent="Do something",
            summary="A plan",
            context_refs_used=[],  # No citations
        )

        result = runner.validate(
            plan, sample_context_packet, sample_agent_profile
        )

        assert result.passed is False
        assert any(
            f.gate_name == "citations"
            for f in result.failures
        )

    def test_missing_citations_ok_without_context(
        self,
        empty_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that missing citations are OK when no context."""
        runner = QualityGateRunner()
        plan = Plan(
            intent="Do something",
            summary="A plan",
            context_refs_used=[],
        )

        result = runner.validate(
            plan, empty_context_packet, sample_agent_profile
        )

        # Should not fail on citations when no context
        citation_failures = [
            f for f in result.failures if f.gate_name == "citations"
        ]
        assert len(citation_failures) == 0

    def test_missing_idempotency_key_fails(
        self,
        empty_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that write action without idempotency key fails."""
        runner = QualityGateRunner()
        plan = Plan(
            intent="Create task",
            summary="Create a new task",
            actions=[
                ActionRequest(
                    capability_name="tasks.create@v1",
                    args={"title": "New task"},
                    side_effect=SideEffect.LOCAL_WRITE,
                    idempotency_key=None,  # Missing!
                ),
            ],
        )

        result = runner.validate(
            plan, empty_context_packet, sample_agent_profile
        )

        assert result.passed is False
        assert any(
            f.gate_name == "idempotency_keys"
            for f in result.failures
        )

    def test_action_caps_exceeded(
        self,
        empty_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that cap_group limits are enforced."""
        runner = QualityGateRunner()
        plan = Plan(
            intent="Cap test",
            summary="Cap enforcement",
            actions=[
                ActionRequest(
                    capability_name="tasks.create@v1",
                    side_effect=SideEffect.LOCAL_WRITE,
                    idempotency_key="cap_test_1",
                    cap_group="tasks.create:project_a",
                    cap_limit=1,
                ),
                ActionRequest(
                    capability_name="tasks.create@v1",
                    side_effect=SideEffect.LOCAL_WRITE,
                    idempotency_key="cap_test_2",
                    cap_group="tasks.create:project_a",
                    cap_limit=1,
                ),
            ],
        )

        result = runner.validate(
            plan, empty_context_packet, sample_agent_profile
        )

        assert result.passed is False
        assert any(
            f.gate_name == "action_caps"
            for f in result.failures
        )

    def test_action_caps_within_limit(
        self,
        empty_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that actions within cap limits pass."""
        runner = QualityGateRunner()
        plan = Plan(
            intent="Cap test",
            summary="Cap enforcement",
            actions=[
                ActionRequest(
                    capability_name="tasks.create@v1",
                    side_effect=SideEffect.LOCAL_WRITE,
                    idempotency_key="cap_test_1",
                    cap_group="tasks.create:project_a",
                    cap_limit=2,
                ),
            ],
        )

        result = runner.validate(
            plan, empty_context_packet, sample_agent_profile
        )

        cap_failures = [
            f for f in result.failures if f.gate_name == "action_caps"
        ]
        assert cap_failures == []

    def test_idempotency_key_not_required_disabled(
        self,
        empty_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test idempotency check can be disabled."""
        runner = QualityGateRunner(require_idempotency_keys=False)
        plan = Plan(
            intent="Create task",
            summary="Create a new task",
            actions=[
                ActionRequest(
                    capability_name="tasks.create@v1",
                    args={"title": "New task"},
                    side_effect=SideEffect.LOCAL_WRITE,
                    idempotency_key=None,
                ),
            ],
        )

        result = runner.validate(
            plan, empty_context_packet, sample_agent_profile
        )

        # Should not fail on idempotency when disabled
        idem_failures = [
            f for f in result.failures if f.gate_name == "idempotency_keys"
        ]
        assert len(idem_failures) == 0

    def test_too_many_actions_warns(
        self,
        empty_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that too many actions generates a warning."""
        runner = QualityGateRunner(max_actions=2)
        plan = Plan(
            intent="Do things",
            summary="A plan with many actions",
            actions=[
                ActionRequest(capability_name="tasks.list@v1", args={})
                for _ in range(5)
            ],
        )

        result = runner.validate(
            plan, empty_context_packet, sample_agent_profile
        )

        assert result.warning_count > 0
        assert any(
            f.gate_name == "action_count"
            for f in result.warnings
        )

    def test_invalid_context_ref_warns(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that referencing non-existent context item warns."""
        runner = QualityGateRunner()
        plan = Plan(
            intent="Do something",
            summary="A plan",
            context_refs_used=[
                ContextRef(ref_type=RefType.TASK, ref_id="task_123"),  # Valid
                ContextRef(ref_type=RefType.TASK, ref_id="nonexistent"),  # Invalid
            ],
        )

        result = runner.validate(
            plan, sample_context_packet, sample_agent_profile
        )

        assert any(
            f.gate_name == "context_references" and "nonexistent" in f.message
            for f in result.warnings
        )

    def test_low_confidence_warns(
        self,
        empty_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that very low confidence is a warning, not error (v1.0.1 change).

        Per v1.0.1 design, confidence alone is a soft signal and shouldn't
        block execution. It's used for escalation decisions combined with
        other signals.
        """
        runner = QualityGateRunner()
        plan = Plan(
            intent="Do something",
            summary="A plan",
            confidence=0.2,  # Very low
        )

        result = runner.validate(
            plan, empty_context_packet, sample_agent_profile
        )

        # Low confidence is now a warning, not an error
        assert result.passed is True
        assert any(
            f.gate_name == "confidence"
            for f in result.warnings
        )
        # But should trigger escalation
        assert result.should_escalate is True

    def test_medium_confidence_warns(
        self,
        empty_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that medium confidence triggers warning and escalation."""
        runner = QualityGateRunner(confidence_threshold=0.8)
        plan = Plan(
            intent="Do something",
            summary="A plan",
            confidence=0.6,  # Below threshold but not critical
        )

        result = runner.validate(
            plan, empty_context_packet, sample_agent_profile
        )

        assert result.warning_count > 0
        assert result.should_escalate is True
        assert "confidence" in result.escalation_reason.lower()

    def test_escalation_on_failures(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that gate failures trigger escalation."""
        runner = QualityGateRunner()
        plan = Plan(
            intent="",  # Invalid
            summary="A plan",
        )

        result = runner.validate(
            plan, sample_context_packet, sample_agent_profile
        )

        assert result.should_escalate is True
        assert "failures" in result.escalation_reason.lower()
