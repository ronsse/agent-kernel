"""Tests for tool broker."""

import pytest

from agent_kernel.core.errors import (
    ApprovalRequiredError,
    CapabilityNotAllowedError,
    CapabilityNotFoundError,
)
from agent_kernel.core.schemas import (
    AgentProfile,
    ApprovalPolicy,
    CallStatus,
    CapabilityDef,
    SideEffect,
)
from agent_kernel.tools.broker import ToolBroker
from agent_kernel.tools.registry import CapabilityRegistry


@pytest.fixture
def registry_with_capabilities() -> CapabilityRegistry:
    """Create a registry with test capabilities."""
    registry = CapabilityRegistry()

    registry.register(CapabilityDef(
        capability_name="tasks.list@v1",
        description="List tasks",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "tasks": {"type": "array"},
            },
        },
        side_effect_level=SideEffect.NONE,
    ))

    registry.register(CapabilityDef(
        capability_name="tasks.create@v1",
        description="Create task",
        input_schema={
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string"},
            },
        },
        output_schema={"type": "object"},
        side_effect_level=SideEffect.LOCAL_WRITE,
    ))

    registry.register(CapabilityDef(
        capability_name="email.send@v1",
        description="Send email",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        side_effect_level=SideEffect.EXTERNAL_WRITE,
        requires_approval_default=True,
    ))

    return registry


@pytest.fixture
def broker(registry_with_capabilities: CapabilityRegistry) -> ToolBroker:
    """Create a tool broker with registered capabilities."""
    broker = ToolBroker(registry_with_capabilities)

    # Register test functions
    broker.local_adapter.register(
        "tasks.list@v1",
        lambda status="open", limit=10: {"tasks": [], "total_count": 0},
    )
    broker.local_adapter.register(
        "tasks.create@v1",
        lambda title, **kwargs: {"task_id": "task_123", "title": title},
    )

    return broker


class TestToolBroker:
    """Tests for ToolBroker."""

    def test_validate_input_valid(self, broker: ToolBroker):
        """Test input validation with valid args."""
        cap = broker.registry.get("tasks.list@v1")
        errors = broker.validate_input(cap, {"status": "open", "limit": 10})
        assert errors == []

    def test_validate_input_invalid(self, broker: ToolBroker):
        """Test input validation with invalid args."""
        cap = broker.registry.get("tasks.list@v1")
        errors = broker.validate_input(cap, {"limit": "not a number"})
        assert len(errors) > 0

    def test_check_allowlist_allowed(
        self,
        broker: ToolBroker,
        sample_agent_profile: AgentProfile,
    ):
        """Test allowlist check for allowed capability."""
        assert broker.check_allowlist("tasks.list@v1", sample_agent_profile)

    def test_check_allowlist_denied(
        self,
        broker: ToolBroker,
        sample_agent_profile: AgentProfile,
    ):
        """Test allowlist check for denied capability."""
        assert not broker.check_allowlist("email.send@v1", sample_agent_profile)

    @pytest.mark.asyncio
    async def test_execute_success(
        self,
        broker: ToolBroker,
        sample_agent_profile: AgentProfile,
    ):
        """Test successful tool execution."""
        record = await broker.execute(
            capability_name="tasks.list@v1",
            args={"status": "open"},
            agent_profile=sample_agent_profile,
        )

        assert record.status == CallStatus.SUCCESS
        assert record.capability_name == "tasks.list@v1"
        assert record.duration_ms >= 0
        assert "tasks" in record.output

    @pytest.mark.asyncio
    async def test_execute_capability_not_found(
        self,
        broker: ToolBroker,
        sample_agent_profile: AgentProfile,
    ):
        """Test execution with unknown capability."""
        with pytest.raises(CapabilityNotFoundError):
            await broker.execute(
                capability_name="unknown@v1",
                args={},
                agent_profile=sample_agent_profile,
            )

    @pytest.mark.asyncio
    async def test_execute_capability_not_allowed(
        self,
        broker: ToolBroker,
        sample_agent_profile: AgentProfile,
    ):
        """Test execution with disallowed capability."""
        with pytest.raises(CapabilityNotAllowedError):
            await broker.execute(
                capability_name="email.send@v1",
                args={},
                agent_profile=sample_agent_profile,
            )

    @pytest.mark.asyncio
    async def test_execute_requires_approval(
        self,
        broker: ToolBroker,
    ):
        """Test execution requiring approval."""
        # Create profile that allows email but requires approval
        profile = AgentProfile(
            agent_profile_id="email_agent",
            name="Email Agent",
            allowed_capabilities=["email.send@v1"],
            approval_policy=ApprovalPolicy(
                auto_approve_side_effects=[SideEffect.NONE],
            ),
        )

        with pytest.raises(ApprovalRequiredError):
            await broker.execute(
                capability_name="email.send@v1",
                args={},
                agent_profile=profile,
            )

    def test_get_recent_calls(
        self,
        broker: ToolBroker,
    ):
        """Test getting recent call records."""
        # Initially empty
        assert len(broker.get_recent_calls()) == 0

    @pytest.mark.asyncio
    async def test_records_stored(
        self,
        broker: ToolBroker,
        sample_agent_profile: AgentProfile,
    ):
        """Test that call records are stored."""
        await broker.execute(
            capability_name="tasks.list@v1",
            args={},
            agent_profile=sample_agent_profile,
        )

        records = broker.get_recent_calls()
        assert len(records) == 1
        assert records[0].capability_name == "tasks.list@v1"


class TestToolBrokerRetryIntegration:
    """Tests for broker retry and circuit breaker integration."""

    @pytest.fixture
    def registry(self) -> CapabilityRegistry:
        """Create a registry with test capability."""
        registry = CapabilityRegistry()
        registry.register(CapabilityDef(
            capability_name="flaky.operation@v1",
            description="A flaky operation",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level=SideEffect.NONE,
        ))
        return registry

    @pytest.fixture
    def profile(self) -> AgentProfile:
        """Create a test agent profile."""
        return AgentProfile(
            agent_profile_id="test",
            name="Test Agent",
            allowed_capabilities=["flaky.operation@v1"],
        )

    def test_broker_with_retry_config(self, registry):
        """Test broker can be created with retry config."""
        from agent_kernel.tools.retry import RetryConfig

        config = RetryConfig(max_retries=3, base_delay_ms=100)
        broker = ToolBroker(
            registry=registry,
            retry_config=config,
            enable_circuit_breaker=True,
        )

        assert broker._retry_config is not None
        assert broker._retry_config.max_retries == 3
        assert broker._circuit_breakers is not None

    def test_broker_without_circuit_breaker(self, registry):
        """Test broker can disable circuit breaker."""
        broker = ToolBroker(
            registry=registry,
            enable_circuit_breaker=False,
        )

        assert broker._circuit_breakers is None
        assert broker.get_circuit_breaker_states() == {}
        assert broker.get_open_circuits() == []

    def test_circuit_breaker_state_methods(self, registry):
        """Test circuit breaker state methods."""
        broker = ToolBroker(registry=registry, enable_circuit_breaker=True)

        # Initially no breakers
        assert broker.get_circuit_breaker_states() == {}
        assert broker.get_open_circuits() == []

    @pytest.mark.asyncio
    async def test_retry_on_retryable_failure(self, registry, profile):
        """Test that broker retries on retryable failures."""
        from agent_kernel.tools.retry import RetryConfig

        call_count = 0

        def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                # Return a retryable failure (simulating via exception)
                raise TimeoutError("Transient failure")
            return {"status": "ok"}

        config = RetryConfig(max_retries=3, base_delay_ms=10, jitter_factor=0.0)
        broker = ToolBroker(
            registry=registry,
            retry_config=config,
            enable_circuit_breaker=False,
        )
        broker.local_adapter.register("flaky.operation@v1", flaky_function)

        # The adapter will catch the exception and set retryable=True for timeout
        result = await broker.execute(
            capability_name="flaky.operation@v1",
            args={},
            agent_profile=profile,
        )

        # Should have retried and succeeded
        assert result.status == CallStatus.SUCCESS
        assert call_count == 2

    def test_reset_circuit_breaker(self, registry):
        """Test resetting circuit breakers."""
        broker = ToolBroker(registry=registry, enable_circuit_breaker=True)

        # Reset non-existent breaker
        assert broker.reset_circuit_breaker("nonexistent@v1") is False

        # Reset all (no-op when empty)
        broker.reset_all_circuit_breakers()

    def test_circuit_breaker_disabled_reset_returns_false(self, registry):
        """Test reset returns False when circuit breaker disabled."""
        broker = ToolBroker(registry=registry, enable_circuit_breaker=False)

        assert broker.reset_circuit_breaker("any@v1") is False
