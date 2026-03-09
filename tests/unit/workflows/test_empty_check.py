"""Unit tests for WorkflowRunner._run_empty_check broker call signature.

Verifies that _run_empty_check calls broker.execute() with keyword args
(capability_name, args, agent_profile, action_id) — NOT with action=ActionRequest.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agent_kernel.core.schemas.agent import (
    AgentProfile,
    ApprovalPolicy,
    ContextPolicy,
    ModelConfig,
)
from agent_kernel.core.schemas.trace import CallStatus, ToolCallRecord
from agent_kernel.core.schemas.plan import SideEffect
from agent_kernel.workflows.runner import WorkflowRunner
from agent_kernel.workflows.spec import EmptyCheck


def _make_agent_profile() -> AgentProfile:
    """Create a minimal AgentProfile for testing."""
    return AgentProfile(
        agent_profile_id="test-agent",
        name="Test Agent",
        engine="custom",
        llm_config=ModelConfig(provider="openai", model="gpt-4o"),
        allowed_capabilities=["test.cap@v1"],
        context_policy=ContextPolicy(),
        approval_policy=ApprovalPolicy(),
        output_schema_version="1.0.0",
    )


def _make_tool_call_record(
    status: CallStatus = CallStatus.SUCCESS,
    output: dict | None = None,
) -> ToolCallRecord:
    """Create a minimal ToolCallRecord for testing."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return ToolCallRecord(
        tool_call_id="tcr-001",
        capability_name="test.cap@v1",
        started_at=now,
        ended_at=now,
        duration_ms=10,
        input={},
        output=output or {},
        status=status,
        related_action_id="act-001",
        effective_side_effect=SideEffect.NONE,
        effective_requires_approval=False,
    )


@pytest.fixture
def mock_runner() -> WorkflowRunner:
    """Create a WorkflowRunner with mocked dependencies."""
    runner = MagicMock(spec=WorkflowRunner)
    runner._executor = MagicMock()
    runner._executor._broker = MagicMock()
    runner._executor._broker.execute = AsyncMock()
    # Bind the real method to our mock instance
    runner._run_empty_check = WorkflowRunner._run_empty_check.__get__(runner)
    return runner


@pytest.mark.asyncio
async def test_empty_check_calls_broker_with_keyword_args(mock_runner: WorkflowRunner) -> None:
    """broker.execute must be called with capability_name, args, agent_profile, action_id kwargs."""
    profile = _make_agent_profile()
    check = EmptyCheck(capability="test.cap@v1", args={"key": "val"}, empty_key="items")

    mock_runner._executor._broker.execute.return_value = _make_tool_call_record(
        output={"items": []}
    )

    await mock_runner._run_empty_check(check, profile)

    mock_runner._executor._broker.execute.assert_called_once()
    call_kwargs = mock_runner._executor._broker.execute.call_args

    # Must NOT have 'action' kwarg (old broken signature)
    assert "action" not in (call_kwargs.kwargs or {}), (
        "broker.execute should NOT be called with action=ActionRequest"
    )

    # Must have these keyword args
    assert "capability_name" in call_kwargs.kwargs
    assert "args" in call_kwargs.kwargs
    assert "agent_profile" in call_kwargs.kwargs
    assert "action_id" in call_kwargs.kwargs

    assert call_kwargs.kwargs["capability_name"] == "test.cap@v1"
    assert call_kwargs.kwargs["args"] == {"key": "val"}
    assert call_kwargs.kwargs["agent_profile"] is profile


@pytest.mark.asyncio
async def test_empty_check_returns_true_when_empty(mock_runner: WorkflowRunner) -> None:
    """_run_empty_check returns True when broker returns empty list for empty_key."""
    profile = _make_agent_profile()
    check = EmptyCheck(capability="test.cap@v1", args={}, empty_key="items")

    mock_runner._executor._broker.execute.return_value = _make_tool_call_record(
        output={"items": []}
    )

    result = await mock_runner._run_empty_check(check, profile)
    assert result is True


@pytest.mark.asyncio
async def test_empty_check_returns_false_on_broker_error(mock_runner: WorkflowRunner) -> None:
    """_run_empty_check returns False when broker raises an exception (swallows it)."""
    profile = _make_agent_profile()
    check = EmptyCheck(capability="test.cap@v1", args={}, empty_key="items")

    mock_runner._executor._broker.execute.side_effect = RuntimeError("connection failed")

    result = await mock_runner._run_empty_check(check, profile)
    assert result is False
