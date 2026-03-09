"""Tests for workflow run store and checkpointing."""

from datetime import datetime, timezone

import pytest

from agent_kernel.core.schemas.plan import SideEffect
from agent_kernel.core.schemas.workflow import (
    ApprovalRequest,
    ApprovalRequestStatus,
    WorkflowRun,
    WorkflowRunStatus,
)
from agent_kernel.workflows.store import (
    InMemoryWorkflowRunStore,
    SQLiteWorkflowRunStore,
    WorkflowCheckpoint,
)


class TestSQLiteWorkflowRunStore:
    """Tests for SQLiteWorkflowRunStore."""

    @pytest.fixture
    def store(self, temp_dir):
        """Create a temporary workflow store."""
        store = SQLiteWorkflowRunStore(temp_dir / "workflows.db")
        yield store
        store.close()

    def test_create_and_get_run(self, store):
        """Test creating and retrieving a workflow run."""
        run = WorkflowRun(
            run_id="run_001",
            workflow_id="daily_checkin",
            status=WorkflowRunStatus.RUNNING,
            intent="Daily review",
        )

        store.create_run(run)
        retrieved = store.get_run("run_001")

        assert retrieved is not None
        assert retrieved.run_id == "run_001"
        assert retrieved.workflow_id == "daily_checkin"
        assert retrieved.status == WorkflowRunStatus.RUNNING
        assert retrieved.intent == "Daily review"

    def test_update_run(self, store):
        """Test updating a workflow run."""
        run = WorkflowRun(
            run_id="run_002",
            workflow_id="inbox_sweep",
            status=WorkflowRunStatus.RUNNING,
        )
        store.create_run(run)

        # Update status
        run.status = WorkflowRunStatus.COMPLETED
        run.last_step = "execute"
        store.update_run(run)

        retrieved = store.get_run("run_002")
        assert retrieved.status == WorkflowRunStatus.COMPLETED
        assert retrieved.last_step == "execute"

    def test_list_runs_filters(self, store):
        """Test listing runs with filters."""
        # Create multiple runs
        for i in range(5):
            status = WorkflowRunStatus.COMPLETED if i % 2 == 0 else WorkflowRunStatus.FAILED
            run = WorkflowRun(
                run_id=f"run_{i:03d}",
                workflow_id="daily_checkin" if i < 3 else "inbox_sweep",
                status=status,
            )
            store.create_run(run)

        # Filter by workflow_id
        daily_runs = store.list_runs(workflow_id="daily_checkin")
        assert len(daily_runs) == 3

        # Filter by status
        completed_runs = store.list_runs(status=WorkflowRunStatus.COMPLETED)
        assert len(completed_runs) == 3

        # Filter by both
        daily_completed = store.list_runs(
            workflow_id="daily_checkin",
            status=WorkflowRunStatus.COMPLETED,
        )
        assert len(daily_completed) == 2

    def test_save_and_get_checkpoint(self, store):
        """Test saving and retrieving checkpoints."""
        # Create a run first
        run = WorkflowRun(
            run_id="run_checkpoint",
            workflow_id="calendar_sync",
            status=WorkflowRunStatus.RUNNING,
        )
        store.create_run(run)

        # Save checkpoint
        step_outputs = {
            "context_packet": {"intent": "Sync calendar"},
            "plan": {"actions": []},
        }
        store.save_checkpoint(
            run_id="run_checkpoint",
            step_index=2,
            step_name="execute",
            step_outputs=step_outputs,
            state_json='{"events": []}',
        )

        # Retrieve checkpoint
        checkpoint = store.get_checkpoint("run_checkpoint")
        assert checkpoint is not None
        assert checkpoint.run_id == "run_checkpoint"
        assert checkpoint.step_index == 2
        assert checkpoint.step_name == "execute"
        assert checkpoint.resume_from_index == 3
        assert checkpoint.step_outputs["context_packet"]["intent"] == "Sync calendar"
        assert checkpoint.state_json == '{"events": []}'

    def test_delete_checkpoints(self, store):
        """Test deleting checkpoints."""
        run = WorkflowRun(
            run_id="run_delete",
            workflow_id="test_workflow",
            status=WorkflowRunStatus.RUNNING,
        )
        store.create_run(run)

        store.save_checkpoint("run_delete", 1, "step_1", {"data": "value"})

        # Verify checkpoint exists
        assert store.get_checkpoint("run_delete") is not None

        # Delete checkpoints
        store.delete_checkpoints("run_delete")

        # Verify checkpoint is gone
        assert store.get_checkpoint("run_delete") is None

    def test_approval_request_lifecycle(self, store):
        """Test creating, updating, and querying approval requests."""
        run = WorkflowRun(
            run_id="run_approval",
            workflow_id="inbox_sweep",
            status=WorkflowRunStatus.WAITING_APPROVAL,
        )
        store.create_run(run)

        # Create approval request
        approval = ApprovalRequest(
            approval_id="approval_001",
            trace_id="trace_001",
            run_id="run_approval",
            workflow_id="inbox_sweep",
            action_id="action_001",
            capability_name="email.send@v1",
            effective_side_effect=SideEffect.EXTERNAL_WRITE,
            status=ApprovalRequestStatus.PENDING,
        )
        store.create_approval_request(approval)

        # Get by ID
        retrieved = store.get_approval_request("approval_001")
        assert retrieved is not None
        assert retrieved.capability_name == "email.send@v1"
        assert retrieved.status == ApprovalRequestStatus.PENDING

        # Get pending approvals
        pending = store.get_pending_approvals("run_approval")
        assert len(pending) == 1
        assert pending[0].approval_id == "approval_001"

        # Update to approved
        approval.status = ApprovalRequestStatus.APPROVED
        approval.resolved_at = datetime.now(timezone.utc)
        approval.resolver = "user_123"
        store.update_approval_request(approval)

        # Verify update
        updated = store.get_approval_request("approval_001")
        assert updated.status == ApprovalRequestStatus.APPROVED
        assert updated.resolver == "user_123"

        # Pending should be empty now
        pending = store.get_pending_approvals("run_approval")
        assert len(pending) == 0


class TestInMemoryWorkflowRunStore:
    """Tests for InMemoryWorkflowRunStore."""

    @pytest.fixture
    def store(self):
        """Create an in-memory workflow store."""
        return InMemoryWorkflowRunStore()

    def test_create_and_get_run(self, store):
        """Test basic create and get operations."""
        run = WorkflowRun(
            run_id="mem_run_001",
            workflow_id="test_workflow",
            status=WorkflowRunStatus.RUNNING,
        )

        store.create_run(run)
        retrieved = store.get_run("mem_run_001")

        assert retrieved is not None
        assert retrieved.run_id == "mem_run_001"

    def test_checkpoint_operations(self, store):
        """Test checkpoint save/get/delete."""
        run = WorkflowRun(
            run_id="mem_checkpoint",
            workflow_id="test_workflow",
            status=WorkflowRunStatus.RUNNING,
        )
        store.create_run(run)

        store.save_checkpoint("mem_checkpoint", 1, "step_1", {"key": "value"})

        checkpoint = store.get_checkpoint("mem_checkpoint")
        assert checkpoint is not None
        assert checkpoint.step_outputs["key"] == "value"

        store.delete_checkpoints("mem_checkpoint")
        assert store.get_checkpoint("mem_checkpoint") is None


class TestWorkflowCheckpoint:
    """Tests for WorkflowCheckpoint dataclass."""

    def test_resume_from_index(self):
        """Test resume_from_index property."""
        checkpoint = WorkflowCheckpoint(
            run_id="run_001",
            step_index=2,
            step_name="execute",
            step_outputs={},
        )

        assert checkpoint.resume_from_index == 3

    def test_with_state_json(self):
        """Test checkpoint with serialized state."""
        checkpoint = WorkflowCheckpoint(
            run_id="run_002",
            step_index=1,
            step_name="import_calendar",
            step_outputs={"events_count": 10},
            state_json='{"sources": {}}',
        )

        assert checkpoint.state_json == '{"sources": {}}'
