"""Tests for workflow write-back notifications."""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest
from agent_kernel.memory.event_log import EventType
from agent_kernel.workflows.runner import WorkflowRunner

EXPECTED_TWO_TARGETS = 2


class TestSendNotifications:
    """Tests for WorkflowRunner._send_notifications."""

    def _make_trace(self) -> MagicMock:
        """Create a minimal mock trace."""
        trace = MagicMock()
        trace.trace_id = "trace_001"
        trace.outcome.status.value = "completed"
        trace.plan.summary = "Test summary"
        return trace

    def _make_spec(self, notify: list[str]) -> MagicMock:
        """Create a minimal mock workflow spec."""
        spec = MagicMock()
        spec.workflow_id = "test_workflow"
        spec.write_back.notify = notify
        return spec

    def _make_runner(self, event_log: MagicMock | None = None) -> MagicMock:
        """Create a minimal WorkflowRunner-like object with _send_notifications."""
        runner = MagicMock(spec=WorkflowRunner)
        runner._event_log = event_log

        # Bind the real method
        runner._send_notifications = types.MethodType(
            WorkflowRunner._send_notifications, runner
        )
        return runner

    @pytest.mark.asyncio
    async def test_send_notifications_emits_events(self) -> None:
        """Test that notifications emit events to the event log."""
        event_log = MagicMock()
        runner = self._make_runner(event_log)
        trace = self._make_trace()
        spec = self._make_spec(notify=["slack", "email"])

        await runner._send_notifications(trace, spec)

        assert event_log.emit.call_count == EXPECTED_TWO_TARGETS

        # Check first call
        first_call = event_log.emit.call_args_list[0]
        assert first_call[0][0] == EventType.WORKFLOW_NOTIFICATION
        payload = first_call[1]["payload"]
        assert payload["target"] == "slack"
        assert payload["workflow_id"] == "test_workflow"
        assert payload["trace_id"] == "trace_001"

        # Check second call
        second_call = event_log.emit.call_args_list[1]
        payload = second_call[1]["payload"]
        assert payload["target"] == "email"

    @pytest.mark.asyncio
    async def test_send_notifications_handles_error(self) -> None:
        """Test that a failing emit doesn't propagate."""
        event_log = MagicMock()
        event_log.emit.side_effect = RuntimeError("Emit failed")

        runner = self._make_runner(event_log)
        trace = self._make_trace()
        spec = self._make_spec(notify=["slack"])

        # Should not raise
        await runner._send_notifications(trace, spec)

        # Emit was attempted
        assert event_log.emit.call_count == 1

    @pytest.mark.asyncio
    async def test_send_notifications_skipped_when_empty(self) -> None:
        """Test no events emitted with empty notify list."""
        event_log = MagicMock()
        runner = self._make_runner(event_log)
        trace = self._make_trace()
        spec = self._make_spec(notify=[])

        await runner._send_notifications(trace, spec)

        event_log.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_notifications_skipped_without_event_log(self) -> None:
        """Test no-op when event_log is None."""
        runner = self._make_runner(event_log=None)
        trace = self._make_trace()
        spec = self._make_spec(notify=["slack"])

        # Should not raise
        await runner._send_notifications(trace, spec)
