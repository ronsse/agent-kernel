"""Tests for trace store."""

from datetime import timedelta

import pytest

from agent_kernel.core.schemas import (
    CallStatus,
    DecisionTrace,
    Outcome,
    OutcomeStatus,
    Plan,
    Provenance,
    ToolCallRecord,
)
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink


def make_trace(
    agent_profile_id: str = "test_agent",
    intent: str = "Test intent",
) -> DecisionTrace:
    """Helper to create a test trace."""
    return DecisionTrace(
        run_id="run_123",
        agent_profile_id=agent_profile_id,
        engine_id="custom",
        intent=intent,
        context_packet_id="packet_123",
        plan=Plan(intent=intent, summary="Test summary", actions=[]),
        outcome=Outcome(status=OutcomeStatus.COMPLETED),
        provenance=Provenance(
            config_hash="abc123",
            engine_version="1.0.0",
            kernel_version="0.1.0",
        ),
    )


class TestSQLiteTraceSink:
    """Tests for SQLiteTraceSink."""

    def test_write_and_get(self, trace_store: SQLiteTraceSink):
        """Test writing and retrieving a trace."""
        trace = make_trace()
        trace_store.write(trace)

        retrieved = trace_store.get(trace.trace_id)
        assert retrieved is not None
        assert retrieved.trace_id == trace.trace_id
        assert retrieved.intent == trace.intent
        assert retrieved.agent_profile_id == trace.agent_profile_id

    def test_get_nonexistent(self, trace_store: SQLiteTraceSink):
        """Test getting non-existent trace."""
        result = trace_store.get("nonexistent_id")
        assert result is None

    def test_get_or_raise(self, trace_store: SQLiteTraceSink):
        """Test get_or_raise method."""
        trace = make_trace()
        trace_store.write(trace)

        # Should work for existing
        retrieved = trace_store.get_or_raise(trace.trace_id)
        assert retrieved.trace_id == trace.trace_id

        # Should raise for non-existing
        from agent_kernel.core.errors import TraceNotFoundError
        with pytest.raises(TraceNotFoundError):
            trace_store.get_or_raise("nonexistent")

    def test_list_traces(self, trace_store: SQLiteTraceSink):
        """Test listing traces."""
        for i in range(5):
            trace_store.write(make_trace(intent=f"Intent {i}"))

        traces = trace_store.list_traces(limit=3)
        assert len(traces) == 3

    def test_list_traces_by_agent(self, trace_store: SQLiteTraceSink):
        """Test filtering traces by agent."""
        trace_store.write(make_trace(agent_profile_id="agent_a"))
        trace_store.write(make_trace(agent_profile_id="agent_b"))
        trace_store.write(make_trace(agent_profile_id="agent_a"))

        traces = trace_store.list_traces(agent_profile_id="agent_a")
        assert len(traces) == 2
        for t in traces:
            assert t.agent_profile_id == "agent_a"

    def test_list_traces_with_time_filter(self, trace_store: SQLiteTraceSink):
        """Test filtering traces by time."""
        now = utc_now()

        trace_store.write(make_trace())

        # Should find recent traces
        traces = trace_store.list_traces(since=now - timedelta(minutes=1))
        assert len(traces) >= 1

        # Should not find future traces
        traces = trace_store.list_traces(since=now + timedelta(hours=1))
        assert len(traces) == 0

    def test_count_traces(self, trace_store: SQLiteTraceSink):
        """Test counting traces."""
        for _ in range(7):
            trace_store.write(make_trace())

        count = trace_store.count()
        assert count == 7

    def test_trace_with_tool_calls(self, trace_store: SQLiteTraceSink):
        """Test trace with tool calls is stored correctly."""
        trace = DecisionTrace(
            run_id="run_with_tools",
            agent_profile_id="test_agent",
            engine_id="custom",
            intent="Test with tools",
            context_packet_id="packet_123",
            plan=Plan(intent="Test", summary="Test", actions=[]),
            tool_calls=[
                ToolCallRecord(
                    capability_name="tasks.list@v1",
                    status=CallStatus.SUCCESS,
                    duration_ms=50,
                ),
                ToolCallRecord(
                    capability_name="tasks.create@v1",
                    status=CallStatus.SUCCESS,
                    duration_ms=100,
                ),
            ],
            outcome=Outcome(status=OutcomeStatus.COMPLETED),
            provenance=Provenance(
                config_hash="abc",
                engine_version="1.0",
                kernel_version="0.1",
            ),
        )

        trace_store.write(trace)

        retrieved = trace_store.get(trace.trace_id)
        assert len(retrieved.tool_calls) == 2
        assert retrieved.tool_calls[0].capability_name == "tasks.list@v1"

    def test_tool_call_stats(self, trace_store: SQLiteTraceSink):
        """Test tool call statistics."""
        trace = DecisionTrace(
            run_id="run_stats",
            agent_profile_id="test_agent",
            engine_id="custom",
            intent="Stats test",
            context_packet_id="packet_123",
            plan=Plan(intent="Test", summary="Test", actions=[]),
            tool_calls=[
                ToolCallRecord(
                    capability_name="tasks.list@v1",
                    status=CallStatus.SUCCESS,
                    duration_ms=50,
                ),
                ToolCallRecord(
                    capability_name="tasks.list@v1",
                    status=CallStatus.SUCCESS,
                    duration_ms=100,
                ),
                ToolCallRecord(
                    capability_name="tasks.list@v1",
                    status=CallStatus.ERROR,
                    duration_ms=30,
                ),
            ],
            outcome=Outcome(status=OutcomeStatus.PARTIAL),
            provenance=Provenance(
                config_hash="abc",
                engine_version="1.0",
                kernel_version="0.1",
            ),
        )

        trace_store.write(trace)

        stats = trace_store.get_tool_call_stats(capability_name="tasks.list@v1")
        assert stats["total_calls"] == 3
        assert stats["successes"] == 2
        assert stats["failures"] == 1
        assert stats["success_rate"] == pytest.approx(0.667, rel=0.01)
