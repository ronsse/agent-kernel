"""Unit tests for Workflow schemas."""

from datetime import UTC, datetime

from agent_kernel.core.schemas.base import SCHEMA_VERSION
from agent_kernel.core.schemas.plan import SideEffect
from agent_kernel.core.schemas.workflow import (
    ApprovalRequest,
    ApprovalRequestStatus,
    WorkflowRun,
    WorkflowRunStatus,
)


class TestWorkflowRun:
    """Tests for WorkflowRun schema."""

    def test_basic_creation(self) -> None:
        """Test creating a basic WorkflowRun."""
        run = WorkflowRun(
            workflow_id="daily_checkin",
            intent="Check in on daily tasks",
        )
        assert run.workflow_id == "daily_checkin"
        assert run.status == WorkflowRunStatus.QUEUED
        assert run.intent == "Check in on daily tasks"
        assert run.retry_count == 0
        assert run.schema_version == SCHEMA_VERSION

    def test_status_transitions(self) -> None:
        """Test all valid status values."""
        for status in WorkflowRunStatus:
            run = WorkflowRun(
                workflow_id="test",
                status=status,
            )
            assert run.status == status

    def test_with_traces(self) -> None:
        """Test WorkflowRun with trace IDs."""
        run = WorkflowRun(
            workflow_id="vault_sync",
            trace_ids=["trace_001", "trace_002"],
        )
        assert len(run.trace_ids) == 2
        assert "trace_001" in run.trace_ids


class TestApprovalRequest:
    """Tests for ApprovalRequest schema."""

    def test_basic_creation(self) -> None:
        """Test creating a basic ApprovalRequest."""
        request = ApprovalRequest(
            trace_id="trace_123",
            run_id="run_456",
            workflow_id="daily_checkin",
            action_id="action_789",
            capability_name="tasks.create@v1",
            effective_side_effect=SideEffect.LOCAL_WRITE,
        )
        assert request.trace_id == "trace_123"
        assert request.status == ApprovalRequestStatus.PENDING
        assert request.effective_side_effect == SideEffect.LOCAL_WRITE
        assert request.resolved_at is None

    def test_status_values(self) -> None:
        """Test all status values."""
        for status in ApprovalRequestStatus:
            request = ApprovalRequest(
                trace_id="trace",
                run_id="run",
                workflow_id="wf",
                action_id="action",
                capability_name="test@v1",
                effective_side_effect=SideEffect.NONE,
                status=status,
            )
            assert request.status == status

    def test_with_preview(self) -> None:
        """Test ApprovalRequest with action preview."""
        request = ApprovalRequest(
            trace_id="trace",
            run_id="run",
            workflow_id="wf",
            action_id="action",
            capability_name="notes.create@v1",
            effective_side_effect=SideEffect.LOCAL_WRITE,
            action_preview={
                "title": "New Note",
                "content": "[REDACTED]",
            },
        )
        assert request.action_preview["title"] == "New Note"

    def test_expiration(self) -> None:
        """Test ApprovalRequest with expiration."""
        expires = datetime.now(UTC)
        request = ApprovalRequest(
            trace_id="trace",
            run_id="run",
            workflow_id="wf",
            action_id="action",
            capability_name="test@v1",
            effective_side_effect=SideEffect.EXTERNAL_WRITE,
            expires_at=expires,
        )
        assert request.expires_at == expires
