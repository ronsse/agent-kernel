"""Shared fixtures for dashboard tests."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from agent_kernel.api.server import create_app
from agent_kernel.core.schemas.plan import (
    ActionRequest,
    Plan,
    RiskAssessment,
    RiskLevel,
    SideEffect,
)
from agent_kernel.core.schemas.trace import (
    DecisionTrace,
    Outcome,
    OutcomeStatus,
    ToolCallRecord,
)
from agent_kernel.core.schemas.workflow import (
    ApprovalRequest,
    ApprovalRequestStatus,
    WorkflowRun,
    WorkflowRunStatus,
)
from fastapi.testclient import TestClient


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Fake data
# ---------------------------------------------------------------------------

def _make_pending_approval() -> ApprovalRequest:
    """Create a fresh pending approval (avoids mutation across tests)."""
    return ApprovalRequest(
        approval_id="approval-pending-001",
        trace_id="trace-001",
        run_id="run-001",
        workflow_id="daily_checkin",
        action_id="action-001",
        capability_name="shell.exec@v1",
        effective_side_effect=SideEffect.EXTERNAL_WRITE,
        status=ApprovalRequestStatus.PENDING,
        requested_at=_utc_now(),
        action_preview={"command": "rm -rf /", "truncated": True},
    )


def _make_approved_approval() -> ApprovalRequest:
    """Create a fresh approved approval."""
    return ApprovalRequest(
        approval_id="approval-approved-002",
        trace_id="trace-002",
        run_id="run-002",
        workflow_id="weekly_review",
        action_id="action-002",
        capability_name="tasks.create@v1",
        effective_side_effect=SideEffect.LOCAL_WRITE,
        status=ApprovalRequestStatus.APPROVED,
        requested_at=_utc_now(),
        resolved_at=_utc_now(),
        resolver="dashboard",
        action_preview={"title": "Review kernel schema"},
    )

FAKE_RUNS = [
    WorkflowRun(
        run_id="run-001",
        workflow_id="daily_checkin",
        status=WorkflowRunStatus.WAITING_APPROVAL,
        intent="Run daily checkin",
        started_at=_utc_now(),
    ),
    WorkflowRun(
        run_id="run-002",
        workflow_id="weekly_review",
        status=WorkflowRunStatus.COMPLETED,
        intent="Run weekly review",
        started_at=_utc_now(),
        ended_at=_utc_now(),
    ),
    WorkflowRun(
        run_id="run-003",
        workflow_id="vault_sync",
        status=WorkflowRunStatus.FAILED,
        intent="Sync vault",
        started_at=_utc_now(),
        ended_at=_utc_now(),
    ),
]


def _make_all_approvals() -> list[ApprovalRequest]:
    """Return fresh copies of all test approvals."""
    return [_make_pending_approval(), _make_approved_approval()]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_workflow_store() -> MagicMock:
    """Mock workflow store with realistic approval and run data."""
    store = MagicMock()

    # list_approval_requests: filter by status kwarg (returns fresh copies each call)
    def _list_approval_requests(
        status: ApprovalRequestStatus | None = None,
        run_id: str | None = None,  # noqa: ARG001
        limit: int = 100,  # noqa: ARG001
    ) -> list[ApprovalRequest]:
        all_approvals = _make_all_approvals()
        if status is None:
            return all_approvals
        return [a for a in all_approvals if a.status == status]

    store.list_approval_requests.side_effect = _list_approval_requests

    # get_approval_request: look up by ID (returns fresh copy each call)
    def _get_approval_request(approval_id: str) -> ApprovalRequest | None:
        all_approvals = _make_all_approvals()
        for a in all_approvals:
            if a.approval_id == approval_id:
                return a
        return None

    store.get_approval_request.side_effect = _get_approval_request

    # update_approval_request: record the call, no-op
    store.update_approval_request.return_value = None

    # list_runs: return fake runs
    store.list_runs.return_value = FAKE_RUNS

    return store


def _make_fake_trace() -> DecisionTrace:
    """Create a realistic DecisionTrace for testing."""
    now = _utc_now()
    action = ActionRequest(
        capability_name="tasks.create@v1",
        args={"title": "Review kernel schema", "priority": "high"},
        side_effect=SideEffect.LOCAL_WRITE,
        requires_approval=False,
        evidence_refs=[],
        idempotency_key="idem-test-001",
    )
    plan = Plan(
        intent="Run daily checkin",
        summary="Created tasks for today's review session.",
        context_refs_used=[],
        actions=[action],
        risk=RiskAssessment(level=RiskLevel.LOW, reasons=["local write only"]),
    )
    tool_call = ToolCallRecord(
        capability_name="tasks.create@v1",
        started_at=now,
        ended_at=now,
        duration_ms=42,
        input={"title": "Review kernel schema", "priority": "high"},
        output={"task_id": "task-test-001"},
        status="success",
        related_action_id=action.action_id,
        effective_side_effect=SideEffect.LOCAL_WRITE,
        effective_requires_approval=False,
    )
    return DecisionTrace(
        trace_id="trace-test-001",
        run_id="run-001",
        workflow_id="daily_checkin",
        agent_profile_id="daily_review_agent",
        engine_id="custom",
        intent="Run daily checkin",
        context_packet_id="packet-test-001",
        plan=plan,
        tool_calls=[tool_call],
        llm_calls=[],
        outcome=Outcome(
            status=OutcomeStatus.COMPLETED,
            summary="Completed successfully",
        ),
    )


@pytest.fixture
def mock_trace_store() -> MagicMock:
    """Mock trace store with realistic trace data."""
    store = MagicMock()

    fake_trace = _make_fake_trace()

    store.list.return_value = [fake_trace]
    store.get.return_value = fake_trace
    store.count.return_value = 1

    return store


@pytest.fixture
def dashboard_app(
    mock_workflow_store: MagicMock,
    mock_trace_store: MagicMock,
) -> Any:
    """Dashboard FastAPI app with mock stores."""
    return create_app(
        workflow_store=mock_workflow_store,
        trace_store=mock_trace_store,
    )


@pytest.fixture
def client(dashboard_app: Any) -> TestClient:
    """HTTP test client for the dashboard app."""
    return TestClient(dashboard_app)
