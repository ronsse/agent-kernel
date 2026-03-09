"""Tests for trace schemas."""



from agent_kernel.core.schemas import (
    ApprovalRecord,
    CallStatus,
    CostRecord,
    DecisionTrace,
    ErrorRecord,
    Outcome,
    OutcomeStatus,
    Plan,
    Provenance,
    ReasoningMetadata,
    ToolCallRecord,
)
from agent_kernel.core.schemas.base import utc_now


class TestToolCallRecord:
    """Tests for ToolCallRecord schema."""

    def test_create_tool_call_record(self):
        """Test creating a tool call record."""
        record = ToolCallRecord(
            capability_name="tasks.list@v1",
            input={"status": "open"},
            output={"tasks": [], "total_count": 0},
            status=CallStatus.SUCCESS,
            duration_ms=45,
        )

        assert record.capability_name == "tasks.list@v1"
        assert record.status == CallStatus.SUCCESS
        assert record.duration_ms == 45
        assert record.tool_call_id is not None

    def test_tool_call_with_error(self):
        """Test tool call with error."""
        record = ToolCallRecord(
            capability_name="external.api@v1",
            input={"url": "https://api.example.com"},
            output={},
            status=CallStatus.ERROR,
            error=ErrorRecord(
                code="TIMEOUT",
                message="Request timed out after 30s",
                retryable=True,
            ),
        )

        assert record.status == CallStatus.ERROR
        assert record.error is not None
        assert record.error.code == "TIMEOUT"
        assert record.error.retryable is True

    def test_tool_call_with_cost(self):
        """Test tool call with cost tracking."""
        record = ToolCallRecord(
            capability_name="llm.generate@v1",
            input={"prompt": "Hello"},
            output={"text": "Hi there!"},
            status=CallStatus.SUCCESS,
            cost=CostRecord(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                estimated_cost_usd=0.0003,
            ),
        )

        assert record.cost is not None
        assert record.cost.total_tokens == 15

    def test_all_call_statuses(self):
        """Test all call status types."""
        for status in CallStatus:
            record = ToolCallRecord(
                capability_name="test@v1",
                status=status,
            )
            assert record.status == status


class TestApprovalRecord:
    """Tests for ApprovalRecord schema."""

    def test_approved_record(self):
        """Test approved action record."""
        record = ApprovalRecord(
            action_id="action_123",
            approved=True,
            approved_by="user@example.com",
            approved_at=utc_now(),
            reason="Verified safe operation",
        )

        assert record.approved is True
        assert record.approved_by == "user@example.com"

    def test_denied_record(self):
        """Test denied action record."""
        record = ApprovalRecord(
            action_id="action_456",
            approved=False,
            approved_by="admin",
            reason="External writes not allowed",
        )

        assert record.approved is False
        assert "not allowed" in record.reason


class TestOutcome:
    """Tests for Outcome schema."""

    def test_completed_outcome(self):
        """Test completed outcome."""
        outcome = Outcome(
            status=OutcomeStatus.COMPLETED,
            summary="All actions executed successfully",
        )

        assert outcome.status == OutcomeStatus.COMPLETED
        assert outcome.artifacts == []

    def test_partial_outcome(self):
        """Test partial outcome."""
        outcome = Outcome(
            status=OutcomeStatus.PARTIAL,
            summary="2 of 3 actions completed",
        )

        assert outcome.status == OutcomeStatus.PARTIAL


class TestDecisionTrace:
    """Tests for DecisionTrace schema."""

    def test_create_decision_trace(self):
        """Test creating a decision trace."""
        plan = Plan(
            intent="Test intent",
            summary="Test summary",
            actions=[],
        )

        trace = DecisionTrace(
            run_id="run_123",
            agent_profile_id="test_agent",
            engine_id="custom",
            intent="Test intent",
            context_packet_id="packet_123",
            plan=plan,
            provenance=Provenance(
                config_hash="abc123",
                engine_version="1.0.0",
                kernel_version="0.1.0",
            ),
        )

        assert trace.run_id == "run_123"
        assert trace.agent_profile_id == "test_agent"
        assert trace.trace_id is not None
        assert trace.timestamp is not None

    def test_trace_total_duration(self):
        """Test calculating total duration."""
        plan = Plan(intent="Test", summary="Test", actions=[])

        trace = DecisionTrace(
            run_id="run_1",
            agent_profile_id="agent_1",
            engine_id="custom",
            intent="Test",
            context_packet_id="packet_1",
            plan=plan,
            tool_calls=[
                ToolCallRecord(
                    capability_name="tool1@v1",
                    duration_ms=100,
                    status=CallStatus.SUCCESS,
                ),
                ToolCallRecord(
                    capability_name="tool2@v1",
                    duration_ms=200,
                    status=CallStatus.SUCCESS,
                ),
            ],
            provenance=Provenance(
                config_hash="abc",
                engine_version="1.0",
                kernel_version="0.1",
            ),
        )

        assert trace.total_duration_ms() == 300

    def test_trace_has_errors(self):
        """Test checking for errors."""
        plan = Plan(intent="Test", summary="Test", actions=[])

        trace_success = DecisionTrace(
            run_id="run_1",
            agent_profile_id="agent_1",
            engine_id="custom",
            intent="Test",
            context_packet_id="packet_1",
            plan=plan,
            tool_calls=[
                ToolCallRecord(capability_name="tool@v1", status=CallStatus.SUCCESS),
            ],
            provenance=Provenance(
                config_hash="abc",
                engine_version="1.0",
                kernel_version="0.1",
            ),
        )

        trace_error = DecisionTrace(
            run_id="run_2",
            agent_profile_id="agent_1",
            engine_id="custom",
            intent="Test",
            context_packet_id="packet_1",
            plan=plan,
            tool_calls=[
                ToolCallRecord(capability_name="tool@v1", status=CallStatus.ERROR),
            ],
            provenance=Provenance(
                config_hash="abc",
                engine_version="1.0",
                kernel_version="0.1",
            ),
        )

        assert trace_success.has_errors() is False
        assert trace_error.has_errors() is True

    def test_trace_success_rate(self):
        """Test calculating success rate."""
        plan = Plan(intent="Test", summary="Test", actions=[])

        trace = DecisionTrace(
            run_id="run_1",
            agent_profile_id="agent_1",
            engine_id="custom",
            intent="Test",
            context_packet_id="packet_1",
            plan=plan,
            tool_calls=[
                ToolCallRecord(capability_name="t1@v1", status=CallStatus.SUCCESS),
                ToolCallRecord(capability_name="t2@v1", status=CallStatus.SUCCESS),
                ToolCallRecord(capability_name="t3@v1", status=CallStatus.ERROR),
                ToolCallRecord(capability_name="t4@v1", status=CallStatus.DENIED),
            ],
            provenance=Provenance(
                config_hash="abc",
                engine_version="1.0",
                kernel_version="0.1",
            ),
        )

        assert trace.success_rate() == 0.5  # 2 success out of 4

    def test_trace_with_reasoning_metadata(self):
        """Test trace with reasoning metadata."""
        plan = Plan(intent="Test", summary="Test", actions=[])

        reasoning = ReasoningMetadata(
            initial_tier=1,
            final_tier=2,
            tier_name="deep",
            model_id="gpt-4o",
            reasoning_effort="high",
            total_attempts=2,
            escalation_count=1,
            escalation_reasons=["gate failures: low confidence"],
            gate_failures=["confidence too low"],
            critic_used=False,
        )

        trace = DecisionTrace(
            run_id="run_1",
            agent_profile_id="agent_1",
            engine_id="custom",
            intent="Test",
            context_packet_id="packet_1",
            plan=plan,
            provenance=Provenance(
                config_hash="abc",
                engine_version="1.0",
                kernel_version="0.1",
            ),
            reasoning=reasoning,
        )

        assert trace.reasoning is not None
        assert trace.reasoning.initial_tier == 1
        assert trace.reasoning.final_tier == 2
        assert trace.reasoning.escalation_count == 1
        assert len(trace.reasoning.escalation_reasons) == 1


class TestReasoningMetadata:
    """Tests for ReasoningMetadata schema."""

    def test_default_values(self):
        """Test default values."""
        metadata = ReasoningMetadata()

        assert metadata.initial_tier == 1
        assert metadata.final_tier == 1
        assert metadata.tier_name == "standard"
        assert metadata.reasoning_effort == "medium"
        assert metadata.total_attempts == 1
        assert metadata.escalation_count == 0
        assert metadata.critic_used is False

    def test_full_metadata(self):
        """Test fully populated metadata."""
        metadata = ReasoningMetadata(
            initial_tier=1,
            final_tier=3,
            tier_name="deep_with_critic",
            model_id="gpt-4o",
            reasoning_effort="high",
            total_attempts=3,
            escalation_count=2,
            escalation_reasons=["low confidence", "gate failures"],
            gate_failures=["missing citations"],
            gate_warnings=["high action count"],
            critic_used=True,
            critic_issues=["plan too broad"],
            total_reasoning_tokens=5000,
        )

        assert metadata.final_tier == 3
        assert metadata.escalation_count == 2
        assert len(metadata.escalation_reasons) == 2
        assert metadata.critic_used is True
        assert len(metadata.critic_issues) == 1
        assert metadata.total_reasoning_tokens == 5000
