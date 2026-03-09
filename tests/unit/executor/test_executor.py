"""Tests for deterministic executor."""

import pytest

from agent_kernel.core.errors import PlanValidationError
from agent_kernel.core.schemas import (
    ActionRequest,
    AgentProfile,
    CapabilityDef,
    ContextBudget,
    ContextPacket,
    OutcomeStatus,
    Plan,
    SideEffect,
)
from agent_kernel.executor.executor import DeterministicExecutor
from agent_kernel.tools.broker import ToolBroker
from agent_kernel.tools.registry import CapabilityRegistry
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink


@pytest.fixture
def executor_setup(temp_dir):
    """Set up executor with dependencies."""
    # Set up registry
    registry = CapabilityRegistry()
    registry.register(CapabilityDef(
        capability_name="tasks.list@v1",
        description="List tasks",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        side_effect_level=SideEffect.NONE,
    ))

    # Set up broker
    broker = ToolBroker(registry)
    broker.local_adapter.register(
        "tasks.list@v1",
        lambda **kwargs: {"tasks": [], "total_count": 0},
    )

    # Set up trace store
    trace_store = SQLiteTraceSink(temp_dir / "traces.db")

    # Set up executor
    executor = DeterministicExecutor(
        tool_broker=broker,
        trace_store=trace_store,
    )

    return executor, trace_store


class TestDeterministicExecutor:
    """Tests for DeterministicExecutor."""

    def test_validate_plan_valid(
        self,
        sample_plan: Plan,
        sample_agent_profile: AgentProfile,
        executor_setup,
    ):
        """Test validating a valid plan."""
        executor, _ = executor_setup

        errors = executor.validate_plan(sample_plan, sample_agent_profile)
        assert errors == []

    def test_validate_plan_disallowed_capability(
        self,
        sample_agent_profile: AgentProfile,
        executor_setup,
    ):
        """Test validating plan with disallowed capability."""
        executor, _ = executor_setup

        plan = Plan(
            intent="Send email",
            summary="Send an email",
            actions=[
                ActionRequest(
                    capability_name="email.send@v1",  # Not in allowed list
                    args={},
                )
            ],
        )

        errors = executor.validate_plan(plan, sample_agent_profile)
        assert len(errors) > 0
        assert "not allowed" in errors[0]

    def test_validate_plan_missing_citations(
        self,
        sample_agent_profile: AgentProfile,
        sample_context_packet: ContextPacket,
        executor_setup,
    ):
        """Test validating plan missing required citations when context exists."""
        executor, _ = executor_setup

        plan = Plan(
            intent="Do something",
            summary="A plan without citations",
            context_refs_used=[],  # Empty citations
            actions=[],
        )

        # Profile requires citations
        assert sample_agent_profile.context_policy.must_cite is True
        # Context has items that should be cited
        assert len(sample_context_packet.items) > 0

        # When context items exist but plan has no citations, it's an error
        errors = executor.validate_plan(
            plan, sample_agent_profile, sample_context_packet
        )
        assert len(errors) > 0
        assert "cite" in errors[0].lower()

    def test_validate_plan_missing_citations_ok_when_no_context(
        self,
        sample_agent_profile: AgentProfile,
        executor_setup,
    ):
        """Test validating plan without citations is OK when context is empty."""
        executor, _ = executor_setup

        plan = Plan(
            intent="Do something",
            summary="A plan without citations",
            context_refs_used=[],  # Empty citations
            actions=[],
        )

        # Empty context packet
        empty_context = ContextPacket(
            intent="Do something",
            budget=ContextBudget(max_tokens=4000),
            items=[],  # No items
        )

        # Profile requires citations
        assert sample_agent_profile.context_policy.must_cite is True

        # When no context items exist, no citations required
        errors = executor.validate_plan(
            plan, sample_agent_profile, empty_context
        )
        assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_execute_simple_plan(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
        sample_context_ref,
        executor_setup,
    ):
        """Test executing a simple plan."""
        executor, trace_store = executor_setup

        plan = Plan(
            intent="List tasks",
            summary="Get all open tasks",
            context_refs_used=[sample_context_ref],
            actions=[
                ActionRequest(
                    capability_name="tasks.list@v1",
                    args={"status": "open"},
                    side_effect=SideEffect.NONE,
                    idempotency_key="list_123",
                )
            ],
        )

        trace = await executor.execute(
            plan=plan,
            context_packet=sample_context_packet,
            agent_profile=sample_agent_profile,
            engine_id="custom",
        )

        assert trace is not None
        assert trace.outcome.status == OutcomeStatus.COMPLETED
        assert len(trace.tool_calls) == 1
        assert trace.tool_calls[0].capability_name == "tasks.list@v1"

        # Verify trace was stored
        retrieved = trace_store.get(trace.trace_id)
        assert retrieved is not None

    @pytest.mark.asyncio
    async def test_execute_empty_plan(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
        sample_context_ref,
        executor_setup,
    ):
        """Test executing plan with no actions."""
        executor, _ = executor_setup

        plan = Plan(
            intent="Just thinking",
            summary="No actions needed",
            context_refs_used=[sample_context_ref],
            actions=[],
        )

        trace = await executor.execute(
            plan=plan,
            context_packet=sample_context_packet,
            agent_profile=sample_agent_profile,
            engine_id="custom",
        )

        assert trace.outcome.status == OutcomeStatus.COMPLETED
        assert len(trace.tool_calls) == 0

    @pytest.mark.asyncio
    async def test_execute_invalid_plan_raises(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
        executor_setup,
    ):
        """Test that invalid plan raises error."""
        executor, _ = executor_setup

        plan = Plan(
            intent="Bad plan",
            summary="Uses disallowed capability",
            context_refs_used=[],  # Missing citations
            actions=[],
        )

        with pytest.raises(PlanValidationError):
            await executor.execute(
                plan=plan,
                context_packet=sample_context_packet,
                agent_profile=sample_agent_profile,
                engine_id="custom",
            )
