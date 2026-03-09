"""Tests for workflow debug info collector."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from agent_kernel.core.schemas.plan import (
    Plan,
    PlanValidation,
    RiskAssessment,
    RiskLevel,
    SideEffect,
)
from agent_kernel.core.schemas.trace import (
    CallStatus,
    DecisionTrace,
    ErrorRecord,
    Outcome,
    OutcomeStatus,
    ToolCallRecord,
)
from agent_kernel.core.schemas.workflow import (
    ApprovalRequest,
    WorkflowRun,
    WorkflowRunStatus,
)
from agent_kernel.services.workflow_debug import collect_debug_info
from agent_kernel.workflows.store import InMemoryWorkflowRunStore

# --- Helpers ---


def _make_run(
    run_id: str = "run_001",
    workflow_id: str = "daily_checkin",
    status: WorkflowRunStatus = WorkflowRunStatus.COMPLETED,
    trace_ids: list[str] | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    error: ErrorRecord | None = None,
) -> WorkflowRun:
    return WorkflowRun(
        run_id=run_id,
        workflow_id=workflow_id,
        status=status,
        intent="Test intent",
        trace_ids=trace_ids or [],
        started_at=started_at,
        ended_at=ended_at,
        error=error,
    )


def _make_trace(
    trace_id: str = "trace_001",
    run_id: str = "run_001",
    tool_calls: list[ToolCallRecord] | None = None,
    outcome_status: OutcomeStatus = OutcomeStatus.COMPLETED,
) -> DecisionTrace:
    return DecisionTrace(
        trace_id=trace_id,
        run_id=run_id,
        agent_profile_id="test_agent",
        engine_id="custom",
        intent="Test intent",
        context_packet_id="ctx_001",
        plan=Plan(
            plan_id="plan_001",
            intent="Test intent",
            summary="Test summary",
            context_refs_used=[],
            actions=[],
            risk=RiskAssessment(level=RiskLevel.LOW, reasons=[]),
            validation=PlanValidation(),
        ),
        tool_calls=tool_calls or [],
        outcome=Outcome(status=outcome_status),
    )


def _make_tool_call(
    capability: str = "test.cap@v1",
    status: CallStatus = CallStatus.SUCCESS,
    duration_ms: int = 100,
    error: ErrorRecord | None = None,
) -> ToolCallRecord:
    now = datetime.now(UTC)
    return ToolCallRecord(
        tool_call_id="tc_001",
        capability_name=capability,
        started_at=now,
        ended_at=now + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        input={},
        output={},
        status=status,
        error=error,
        related_action_id="action_001",
        effective_side_effect=SideEffect.NONE,
        effective_requires_approval=False,
    )


def _make_approval(
    run_id: str = "run_001",
) -> ApprovalRequest:
    return ApprovalRequest(
        approval_id="appr_001",
        trace_id="trace_001",
        run_id=run_id,
        workflow_id="daily_checkin",
        action_id="action_001",
        capability_name="tasks.create@v1",
        effective_side_effect=SideEffect.LOCAL_WRITE,
    )


class FakeTraceStore:
    """Minimal trace store for testing."""

    def __init__(
        self, traces: list[DecisionTrace] | None = None,
    ) -> None:
        self._traces = traces or []

    def list_traces(self, **kwargs: object) -> list[DecisionTrace]:
        return self._traces

    def get(self, trace_id: str) -> DecisionTrace | None:
        for t in self._traces:
            if t.trace_id == trace_id:
                return t
        return None

    def close(self) -> None:
        pass


class FakeEventLog:
    """Minimal event log for testing."""

    def __init__(self, events: list | None = None) -> None:
        self._events = events or []

    def get_events(self, **kwargs: object) -> list:
        entity_id = kwargs.get("entity_id")
        if entity_id:
            return [
                e for e in self._events
                if getattr(e, "entity_id", None) == entity_id
            ]
        return self._events

    def close(self) -> None:
        pass


# --- Tests ---


class TestCollectBasicCompletedRun:
    """Test collecting debug info for a basic completed run."""

    def test_collects_run_and_traces(self) -> None:
        store = InMemoryWorkflowRunStore()
        run = _make_run(trace_ids=["trace_001"])
        store.create_run(run)

        trace = _make_trace()
        trace_store = FakeTraceStore([trace])
        event_log = FakeEventLog()

        info = collect_debug_info(
            "run_001", store, trace_store, event_log,
        )

        assert info.run.run_id == "run_001"
        assert len(info.traces) == 1
        assert info.traces[0].trace_id == "trace_001"

    def test_computes_duration(self) -> None:
        store = InMemoryWorkflowRunStore()
        start = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
        end = datetime(2026, 1, 15, 10, 0, 5, tzinfo=UTC)
        run = _make_run(started_at=start, ended_at=end)
        store.create_run(run)

        info = collect_debug_info(
            "run_001", store, FakeTraceStore(), FakeEventLog(),
        )

        assert info.duration_ms == 5000


class TestCollectFailedRunWithError:
    """Test collecting debug info for a failed run."""

    def test_error_populated(self) -> None:
        store = InMemoryWorkflowRunStore()
        error = ErrorRecord(
            code="TIMEOUT", message="Engine timed out",
        )
        run = _make_run(
            status=WorkflowRunStatus.FAILED, error=error,
        )
        store.create_run(run)

        info = collect_debug_info(
            "run_001", store, FakeTraceStore(), FakeEventLog(),
        )

        assert info.run.error is not None
        assert info.run.error.code == "TIMEOUT"
        assert info.run.error.message == "Engine timed out"


class TestCollectRunWithCheckpoint:
    """Test collecting debug info with a checkpoint."""

    def test_checkpoint_present(self) -> None:
        store = InMemoryWorkflowRunStore()
        run = _make_run(status=WorkflowRunStatus.WAITING_APPROVAL)
        store.create_run(run)
        store.save_checkpoint(
            "run_001", 2, "gate_approvals", {"plan": "data"},
        )

        info = collect_debug_info(
            "run_001", store, FakeTraceStore(), FakeEventLog(),
        )

        assert info.checkpoint is not None
        assert info.checkpoint.step_index == 2
        assert info.checkpoint.step_name == "gate_approvals"
        assert info.checkpoint.resume_from_index == 3


class TestCollectRunWithPendingApprovals:
    """Test collecting debug info with pending approvals."""

    def test_approvals_populated(self) -> None:
        store = InMemoryWorkflowRunStore()
        run = _make_run(status=WorkflowRunStatus.WAITING_APPROVAL)
        store.create_run(run)

        approval = _make_approval()
        store.create_approval_request(approval)

        info = collect_debug_info(
            "run_001", store, FakeTraceStore(), FakeEventLog(),
        )

        assert len(info.pending_approvals) == 1
        assert info.pending_approvals[0].capability_name == "tasks.create@v1"


class TestCollectRunNotFound:
    """Test that missing run raises ValueError."""

    def test_raises_value_error(self) -> None:
        store = InMemoryWorkflowRunStore()

        with pytest.raises(ValueError, match="Workflow run not found"):
            collect_debug_info(
                "nonexistent", store,
                FakeTraceStore(), FakeEventLog(),
            )


class TestCollectNoTraces:
    """Test collecting debug info with no traces."""

    def test_handles_empty_traces(self) -> None:
        store = InMemoryWorkflowRunStore()
        run = _make_run(trace_ids=[])
        store.create_run(run)

        info = collect_debug_info(
            "run_001", store, FakeTraceStore(), FakeEventLog(),
        )

        assert info.traces == []
        assert info.tool_call_summary == {}


class TestToolCallSummary:
    """Test tool call summary computation."""

    def test_correct_counts_and_rates(self) -> None:
        store = InMemoryWorkflowRunStore()
        run = _make_run(trace_ids=["trace_001"])
        store.create_run(run)

        tool_calls = [
            _make_tool_call(
                capability="a@v1",
                status=CallStatus.SUCCESS,
                duration_ms=100,
            ),
            _make_tool_call(
                capability="b@v1",
                status=CallStatus.SUCCESS,
                duration_ms=200,
            ),
            _make_tool_call(
                capability="c@v1",
                status=CallStatus.ERROR,
                duration_ms=50,
            ),
        ]
        trace = _make_trace(tool_calls=tool_calls)
        trace_store = FakeTraceStore([trace])

        info = collect_debug_info(
            "run_001", store, trace_store, FakeEventLog(),
        )

        assert info.tool_call_summary["total"] == 3
        assert info.tool_call_summary["successes"] == 2
        assert info.tool_call_summary["failures"] == 1
        assert info.tool_call_summary["success_rate"] == pytest.approx(
            0.667, abs=0.001,
        )
        assert info.tool_call_summary["avg_duration_ms"] == pytest.approx(
            116.7, abs=0.1,
        )


class TestDurationRunningUsesNow:
    """Test that running workflow uses current time for duration."""

    def test_duration_computed_from_now(self) -> None:
        store = InMemoryWorkflowRunStore()
        # Started 10 seconds ago, no ended_at
        start = datetime.now(UTC) - timedelta(seconds=10)
        run = _make_run(
            status=WorkflowRunStatus.RUNNING,
            started_at=start,
            ended_at=None,
        )
        store.create_run(run)

        info = collect_debug_info(
            "run_001", store, FakeTraceStore(), FakeEventLog(),
        )

        assert info.duration_ms is not None
        # ~10000ms (10 seconds), allow some tolerance
        assert 9000 <= info.duration_ms <= 12000


class TestToDict:
    """Test WorkflowDebugInfo serialization."""

    def test_to_dict_basic(self) -> None:
        store = InMemoryWorkflowRunStore()
        run = _make_run()
        store.create_run(run)

        info = collect_debug_info(
            "run_001", store, FakeTraceStore(), FakeEventLog(),
        )
        data = info.to_dict()

        assert data["run"]["run_id"] == "run_001"
        assert data["traces"] == []
        assert data["checkpoint"] is None
        assert data["pending_approvals"] == []
