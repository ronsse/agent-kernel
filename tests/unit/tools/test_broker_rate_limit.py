"""Tests for rate limiter and idempotency integration in ToolBroker."""

from __future__ import annotations

import pytest
from agent_kernel.core.schemas import (
    AgentProfile,
    CallStatus,
    CapabilityDef,
)
from agent_kernel.core.schemas.agent import (
    ApprovalPolicy,
    ContextPolicy,
    ModelConfig,
)
from agent_kernel.core.schemas.capability import RateLimit
from agent_kernel.memory.event_log import EventType, SQLiteEventLog
from agent_kernel.tools.broker import ToolBroker
from agent_kernel.tools.idempotency import IdempotencyStore
from agent_kernel.tools.registry import CapabilityRegistry


def _make_profile() -> AgentProfile:
    """Create a test agent profile."""
    return AgentProfile(
        agent_profile_id="test-agent",
        name="Test Agent",
        engine="custom",
        llm_config=ModelConfig(provider="openai", model="gpt-4o"),
        allowed_capabilities=["test.echo@v1"],
        context_policy=ContextPolicy(),
        approval_policy=ApprovalPolicy(),
    )


def _make_registry_with_rate_limit(
    max_per_minute: int = 2,
    max_per_hour: int = 100,
) -> CapabilityRegistry:
    """Create a registry with a rate-limited capability."""
    registry = CapabilityRegistry()
    cap = CapabilityDef(
        capability_name="test.echo@v1",
        description="Echo test",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        side_effect_level="none",
        requires_approval_default=False,
        timeout_ms=5000,
        rate_limit=RateLimit(
            max_calls_per_minute=max_per_minute,
            max_calls_per_hour=max_per_hour,
        ),
    )
    registry.register(cap)
    return registry


def _make_registry_no_rate_limit() -> CapabilityRegistry:
    """Create a registry with a capability that has no rate limit."""
    registry = CapabilityRegistry()
    cap = CapabilityDef(
        capability_name="test.echo@v1",
        description="Echo test",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        side_effect_level="none",
        requires_approval_default=False,
        timeout_ms=5000,
    )
    registry.register(cap)
    return registry


async def _echo_fn(**kwargs: object) -> dict[str, object]:
    """Simple echo adapter function for tests."""
    return {"echo": kwargs}


async def _ok_fn(**kwargs: object) -> dict[str, object]:
    """Simple OK adapter function for tests."""
    return {"result": "ok"}


@pytest.mark.asyncio
async def test_rate_limited_returns_error():
    """Calls exceeding rate limit return RATE_LIMITED error."""
    registry = _make_registry_with_rate_limit(max_per_minute=1)
    broker = ToolBroker(
        registry=registry,
        enable_rate_limiting=True,
        enable_circuit_breaker=False,
    )

    broker.local_adapter.register("test.echo@v1", _echo_fn)

    profile = _make_profile()

    # First call should succeed
    record1 = await broker.execute("test.echo@v1", {}, profile)
    assert record1.status == CallStatus.SUCCESS

    # Second call should be rate limited
    record2 = await broker.execute("test.echo@v1", {}, profile)
    assert record2.status == CallStatus.ERROR
    assert record2.error is not None
    assert record2.error.code == "RATE_LIMITED"
    assert record2.error.retryable is True


@pytest.mark.asyncio
async def test_rate_limit_event_emitted(tmp_path):
    """Rate limited calls emit TOOL_FAILED event."""
    registry = _make_registry_with_rate_limit(max_per_minute=1)
    event_log = SQLiteEventLog(tmp_path / "events.db")
    broker = ToolBroker(
        registry=registry,
        event_log=event_log,
        enable_rate_limiting=True,
        enable_circuit_breaker=False,
    )

    broker.local_adapter.register("test.echo@v1", _echo_fn)

    profile = _make_profile()

    # First call succeeds
    await broker.execute("test.echo@v1", {}, profile)

    # Second call is rate limited
    await broker.execute("test.echo@v1", {}, profile)

    # Check that TOOL_FAILED event was emitted with RATE_LIMITED code
    events = event_log.get_events(
        event_type=EventType.TOOL_FAILED, limit=10,
    )
    rate_limited_events = [
        e for e in events
        if e.payload.get("error_code") == "RATE_LIMITED"
    ]
    assert len(rate_limited_events) >= 1
    assert (
        rate_limited_events[0].payload.get("wait_seconds") is not None
    )

    event_log.close()


@pytest.mark.asyncio
async def test_no_rate_limit_when_disabled():
    """Calls succeed when rate limiting is disabled."""
    registry = _make_registry_with_rate_limit(max_per_minute=1)
    broker = ToolBroker(
        registry=registry,
        enable_rate_limiting=False,
        enable_circuit_breaker=False,
    )

    broker.local_adapter.register("test.echo@v1", _echo_fn)

    profile = _make_profile()

    # Both calls should succeed since rate limiting is off
    record1 = await broker.execute("test.echo@v1", {}, profile)
    assert record1.status == CallStatus.SUCCESS

    record2 = await broker.execute("test.echo@v1", {}, profile)
    assert record2.status == CallStatus.SUCCESS


@pytest.mark.asyncio
async def test_no_rate_limit_when_capability_has_none():
    """Calls succeed when capability has no rate_limit configured."""
    registry = _make_registry_no_rate_limit()
    broker = ToolBroker(
        registry=registry,
        enable_rate_limiting=True,
        enable_circuit_breaker=False,
    )

    broker.local_adapter.register("test.echo@v1", _echo_fn)

    profile = _make_profile()

    # Both calls should succeed since capability has no rate limit
    record1 = await broker.execute("test.echo@v1", {}, profile)
    assert record1.status == CallStatus.SUCCESS

    record2 = await broker.execute("test.echo@v1", {}, profile)
    assert record2.status == CallStatus.SUCCESS


@pytest.mark.asyncio
async def test_idempotency_dedup(tmp_path):
    """Duplicate idempotency keys return SKIPPED status."""
    registry = _make_registry_no_rate_limit()
    idem_store = IdempotencyStore(tmp_path / "idem.db")
    broker = ToolBroker(
        registry=registry,
        enable_rate_limiting=False,
        enable_circuit_breaker=False,
        idempotency_store=idem_store,
    )

    broker.local_adapter.register("test.echo@v1", _ok_fn)

    profile = _make_profile()

    # First call with idempotency key
    record1 = await broker.execute(
        "test.echo@v1",
        {"idempotency_key": "unique-key-123"},
        profile,
    )
    assert record1.status == CallStatus.SUCCESS

    # Second call with same key should be deduplicated
    record2 = await broker.execute(
        "test.echo@v1",
        {"idempotency_key": "unique-key-123"},
        profile,
    )
    assert record2.status == CallStatus.SKIPPED
    assert record2.output["deduplicated"] is True
    assert (
        record2.output["original_tool_call_id"] == record1.tool_call_id
    )

    idem_store.close()


@pytest.mark.asyncio
async def test_idempotency_different_keys(tmp_path):
    """Different idempotency keys execute independently."""
    registry = _make_registry_no_rate_limit()
    idem_store = IdempotencyStore(tmp_path / "idem.db")
    broker = ToolBroker(
        registry=registry,
        enable_rate_limiting=False,
        enable_circuit_breaker=False,
        idempotency_store=idem_store,
    )

    broker.local_adapter.register("test.echo@v1", _ok_fn)

    profile = _make_profile()

    record1 = await broker.execute(
        "test.echo@v1",
        {"idempotency_key": "key-a"},
        profile,
    )
    assert record1.status == CallStatus.SUCCESS

    record2 = await broker.execute(
        "test.echo@v1",
        {"idempotency_key": "key-b"},
        profile,
    )
    assert record2.status == CallStatus.SUCCESS

    idem_store.close()
