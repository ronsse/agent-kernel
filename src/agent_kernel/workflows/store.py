"""Workflow Run Store - persistent storage for workflow state and checkpoints.

Provides durable storage for:
- WorkflowRun lifecycle tracking
- Step checkpoints for resumption
- ApprovalRequest persistence

This enables:
- Workflow state survives process restarts
- True checkpoint resumption (not re-execution)
- Approval flow across sessions
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.schemas.workflow import (
    ApprovalRequest,
    ApprovalRequestStatus,
    WorkflowRun,
    WorkflowRunStatus,
)

logger = structlog.get_logger(__name__)


class WorkflowCheckpoint:
    """Checkpoint data for resuming a workflow from a specific step.

    Contains all state needed to resume execution without re-running
    prior steps.
    """

    def __init__(
        self,
        run_id: str,
        step_index: int,
        step_name: str,
        step_outputs: dict[str, Any],
        state_json: str | None = None,
    ) -> None:
        """Initialize a checkpoint.

        Args:
            run_id: The workflow run ID.
            step_index: Index of the completed step (0-based).
            step_name: Name of the completed step.
            step_outputs: Outputs from completed steps keyed by step name.
            state_json: Serialized workflow-specific state (e.g., CalendarDerivationState).
        """
        self.run_id = run_id
        self.step_index = step_index
        self.step_name = step_name
        self.step_outputs = step_outputs
        self.state_json = state_json

    @property
    def resume_from_index(self) -> int:
        """Index to resume from (next step after checkpoint)."""
        return self.step_index + 1


class WorkflowRunStore(ABC):
    """Abstract interface for workflow run persistence."""

    @abstractmethod
    def create_run(self, run: WorkflowRun) -> None:
        """Create a new workflow run record."""

    @abstractmethod
    def get_run(self, run_id: str) -> WorkflowRun | None:
        """Get a workflow run by ID."""

    @abstractmethod
    def update_run(self, run: WorkflowRun) -> None:
        """Update an existing workflow run."""

    @abstractmethod
    def list_runs(
        self,
        workflow_id: str | None = None,
        status: WorkflowRunStatus | None = None,
        limit: int = 100,
    ) -> list[WorkflowRun]:
        """List workflow runs with optional filters."""

    @abstractmethod
    def save_checkpoint(
        self,
        run_id: str,
        step_index: int,
        step_name: str,
        step_outputs: dict[str, Any],
        state_json: str | None = None,
    ) -> None:
        """Save a checkpoint after a step completes.

        Args:
            run_id: The workflow run ID.
            step_index: Index of the completed step.
            step_name: Name of the completed step.
            step_outputs: All step outputs accumulated so far.
            state_json: Optional serialized workflow state.
        """

    @abstractmethod
    def get_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None:
        """Get the latest checkpoint for a workflow run."""

    @abstractmethod
    def delete_checkpoints(self, run_id: str) -> None:
        """Delete all checkpoints for a workflow run (on completion)."""

    @abstractmethod
    def create_approval_request(self, request: ApprovalRequest) -> None:
        """Create a new approval request."""

    @abstractmethod
    def get_approval_request(self, approval_id: str) -> ApprovalRequest | None:
        """Get an approval request by ID."""

    @abstractmethod
    def get_pending_approvals(self, run_id: str) -> list[ApprovalRequest]:
        """Get all pending approval requests for a run."""

    @abstractmethod
    def update_approval_request(self, request: ApprovalRequest) -> None:
        """Update an approval request."""

    @abstractmethod
    def get_run_by_idempotency_key(self, key: str) -> WorkflowRun | None:
        """Get a workflow run by its idempotency key."""

    @abstractmethod
    def list_approval_requests(
        self,
        status: ApprovalRequestStatus | None = None,
        limit: int = 1000,
    ) -> list[ApprovalRequest]:
        """List approval requests with optional status filter."""

    @abstractmethod
    def close(self) -> None:
        """Close the store."""


class SQLiteWorkflowRunStore(WorkflowRunStore):
    """SQLite-based workflow run store with checkpoint support.

    Schema:
    - workflow_runs: Run records with status lifecycle
    - workflow_checkpoints: Step checkpoints for resumption
    - approval_requests: Pending approval tracking
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialize SQLite workflow store.

        Args:
            db_path: Path to SQLite database file.
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        logger.info("sqlite_workflow_store_initialized", db_path=str(self._db_path))

    def _init_schema(self) -> None:
        """Initialize database schema."""
        self._conn.executescript("""
            -- Workflow run records
            CREATE TABLE IF NOT EXISTS workflow_runs (
                run_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                intent TEXT,
                started_at TEXT,
                ended_at TEXT,
                last_step TEXT,
                retry_count INTEGER DEFAULT 0,
                error_json TEXT,
                trace_ids_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_runs_workflow_id
                ON workflow_runs(workflow_id);
            CREATE INDEX IF NOT EXISTS idx_runs_status
                ON workflow_runs(status);
            CREATE INDEX IF NOT EXISTS idx_runs_created
                ON workflow_runs(created_at DESC);

            -- Step checkpoints for resumption
            CREATE TABLE IF NOT EXISTS workflow_checkpoints (
                run_id TEXT PRIMARY KEY,
                step_index INTEGER NOT NULL,
                step_name TEXT NOT NULL,
                step_outputs_json TEXT NOT NULL,
                state_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
            );

            -- Approval requests
            CREATE TABLE IF NOT EXISTS approval_requests (
                approval_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                capability_name TEXT NOT NULL,
                effective_side_effect TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_at TEXT NOT NULL,
                resolved_at TEXT,
                resolver TEXT,
                reason TEXT,
                action_preview_json TEXT NOT NULL DEFAULT '{}',
                expires_at TEXT,
                policy_basis TEXT,
                FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_approvals_run_id
                ON approval_requests(run_id);
            CREATE INDEX IF NOT EXISTS idx_approvals_status
                ON approval_requests(status);
        """)
        self._conn.commit()

        # Safe migration: add idempotency_key column if missing
        try:
            self._conn.execute("SELECT idempotency_key FROM workflow_runs LIMIT 1")
        except sqlite3.OperationalError:
            logger.info("migrating_workflow_runs_add_idempotency_key")
            self._conn.execute(
                "ALTER TABLE workflow_runs ADD COLUMN idempotency_key TEXT"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_idempotency "
                "ON workflow_runs(idempotency_key)"
            )
            self._conn.commit()

    def create_run(self, run: WorkflowRun) -> None:
        """Create a new workflow run record."""
        self._conn.execute(
            """
            INSERT INTO workflow_runs (
                run_id, workflow_id, status, intent, started_at, ended_at,
                last_step, retry_count, error_json, trace_ids_json,
                metadata_json, idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                run.workflow_id,
                run.status.value,
                run.intent,
                run.started_at.isoformat() if run.started_at else None,
                run.ended_at.isoformat() if run.ended_at else None,
                run.last_step,
                run.retry_count,
                run.error.model_dump_json() if run.error else None,
                json.dumps(run.trace_ids),
                json.dumps(run.metadata),
                run.idempotency_key,
            ),
        )
        self._conn.commit()
        logger.debug("workflow_run_created", run_id=run.run_id, workflow_id=run.workflow_id)

    def get_run(self, run_id: str) -> WorkflowRun | None:
        """Get a workflow run by ID."""
        cursor = self._conn.execute(
            "SELECT * FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return self._row_to_run(row)

    def _row_to_run(self, row: sqlite3.Row) -> WorkflowRun:
        """Convert a database row to a WorkflowRun."""
        from agent_kernel.core.schemas.trace import ErrorRecord

        error = None
        if row["error_json"]:
            error = ErrorRecord.model_validate_json(row["error_json"])

        # Handle idempotency_key column (may not exist in older DBs)
        idempotency_key = None
        try:
            idempotency_key = row["idempotency_key"]
        except (IndexError, KeyError):
            pass

        return WorkflowRun(
            run_id=row["run_id"],
            workflow_id=row["workflow_id"],
            status=WorkflowRunStatus(row["status"]),
            intent=row["intent"],
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
            last_step=row["last_step"],
            retry_count=row["retry_count"],
            error=error,
            trace_ids=json.loads(row["trace_ids_json"]),
            metadata=json.loads(row["metadata_json"]),
            idempotency_key=idempotency_key,
        )

    def update_run(self, run: WorkflowRun) -> None:
        """Update an existing workflow run."""
        self._conn.execute(
            """
            UPDATE workflow_runs SET
                status = ?,
                intent = ?,
                started_at = ?,
                ended_at = ?,
                last_step = ?,
                retry_count = ?,
                error_json = ?,
                trace_ids_json = ?,
                metadata_json = ?,
                idempotency_key = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE run_id = ?
            """,
            (
                run.status.value,
                run.intent,
                run.started_at.isoformat() if run.started_at else None,
                run.ended_at.isoformat() if run.ended_at else None,
                run.last_step,
                run.retry_count,
                run.error.model_dump_json() if run.error else None,
                json.dumps(run.trace_ids),
                json.dumps(run.metadata),
                run.idempotency_key,
                run.run_id,
            ),
        )
        self._conn.commit()
        logger.debug("workflow_run_updated", run_id=run.run_id, status=run.status.value)

    def list_runs(
        self,
        workflow_id: str | None = None,
        status: WorkflowRunStatus | None = None,
        limit: int = 100,
    ) -> list[WorkflowRun]:
        """List workflow runs with optional filters."""
        query = "SELECT * FROM workflow_runs WHERE 1=1"
        params: list[Any] = []

        if workflow_id:
            query += " AND workflow_id = ?"
            params.append(workflow_id)
        if status:
            query += " AND status = ?"
            params.append(status.value)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(query, params)
        return [self._row_to_run(row) for row in cursor.fetchall()]

    def save_checkpoint(
        self,
        run_id: str,
        step_index: int,
        step_name: str,
        step_outputs: dict[str, Any],
        state_json: str | None = None,
    ) -> None:
        """Save a checkpoint after a step completes."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO workflow_checkpoints
            (run_id, step_index, step_name, step_outputs_json, state_json, created_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                run_id,
                step_index,
                step_name,
                json.dumps(step_outputs),
                state_json,
            ),
        )
        self._conn.commit()
        logger.debug(
            "checkpoint_saved",
            run_id=run_id,
            step_index=step_index,
            step_name=step_name,
        )

    def get_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None:
        """Get the latest checkpoint for a workflow run."""
        cursor = self._conn.execute(
            "SELECT * FROM workflow_checkpoints WHERE run_id = ?",
            (run_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return WorkflowCheckpoint(
            run_id=row["run_id"],
            step_index=row["step_index"],
            step_name=row["step_name"],
            step_outputs=json.loads(row["step_outputs_json"]),
            state_json=row["state_json"],
        )

    def delete_checkpoints(self, run_id: str) -> None:
        """Delete all checkpoints for a workflow run (on completion)."""
        self._conn.execute(
            "DELETE FROM workflow_checkpoints WHERE run_id = ?",
            (run_id,),
        )
        self._conn.commit()
        logger.debug("checkpoints_deleted", run_id=run_id)

    def create_approval_request(self, request: ApprovalRequest) -> None:
        """Create a new approval request."""
        self._conn.execute(
            """
            INSERT INTO approval_requests (
                approval_id, trace_id, run_id, workflow_id, action_id,
                capability_name, effective_side_effect, status, requested_at,
                resolved_at, resolver, reason, action_preview_json, expires_at, policy_basis
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.approval_id,
                request.trace_id,
                request.run_id,
                request.workflow_id,
                request.action_id,
                request.capability_name,
                request.effective_side_effect.value,
                request.status.value,
                request.requested_at.isoformat(),
                request.resolved_at.isoformat() if request.resolved_at else None,
                request.resolver,
                request.reason,
                json.dumps(request.action_preview),
                request.expires_at.isoformat() if request.expires_at else None,
                request.policy_basis,
            ),
        )
        self._conn.commit()
        logger.debug("approval_request_created", approval_id=request.approval_id)

    def get_approval_request(self, approval_id: str) -> ApprovalRequest | None:
        """Get an approval request by ID."""
        cursor = self._conn.execute(
            "SELECT * FROM approval_requests WHERE approval_id = ?",
            (approval_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return self._row_to_approval(row)

    def _row_to_approval(self, row: sqlite3.Row) -> ApprovalRequest:
        """Convert a database row to an ApprovalRequest."""
        from agent_kernel.core.schemas.plan import SideEffect

        return ApprovalRequest(
            approval_id=row["approval_id"],
            trace_id=row["trace_id"],
            run_id=row["run_id"],
            workflow_id=row["workflow_id"],
            action_id=row["action_id"],
            capability_name=row["capability_name"],
            effective_side_effect=SideEffect(row["effective_side_effect"]),
            status=ApprovalRequestStatus(row["status"]),
            requested_at=datetime.fromisoformat(row["requested_at"]),
            resolved_at=(
                datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None
            ),
            resolver=row["resolver"],
            reason=row["reason"],
            action_preview=json.loads(row["action_preview_json"]),
            expires_at=(
                datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
            ),
            policy_basis=row["policy_basis"],
        )

    def get_pending_approvals(self, run_id: str) -> list[ApprovalRequest]:
        """Get all pending approval requests for a run."""
        cursor = self._conn.execute(
            "SELECT * FROM approval_requests WHERE run_id = ? AND status = 'pending'",
            (run_id,),
        )
        return [self._row_to_approval(row) for row in cursor.fetchall()]

    def update_approval_request(self, request: ApprovalRequest) -> None:
        """Update an approval request."""
        self._conn.execute(
            """
            UPDATE approval_requests SET
                status = ?,
                resolved_at = ?,
                resolver = ?,
                reason = ?
            WHERE approval_id = ?
            """,
            (
                request.status.value,
                request.resolved_at.isoformat() if request.resolved_at else None,
                request.resolver,
                request.reason,
                request.approval_id,
            ),
        )
        self._conn.commit()
        logger.debug(
            "approval_request_updated",
            approval_id=request.approval_id,
            status=request.status.value,
        )

    def get_run_by_idempotency_key(self, key: str) -> WorkflowRun | None:
        """Get a workflow run by its idempotency key."""
        cursor = self._conn.execute(
            "SELECT * FROM workflow_runs WHERE idempotency_key = ?",
            (key,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_run(row)

    def list_approval_requests(
        self,
        status: ApprovalRequestStatus | None = None,
        limit: int = 1000,
    ) -> list[ApprovalRequest]:
        """List approval requests with optional status filter."""
        if status:
            cursor = self._conn.execute(
                "SELECT * FROM approval_requests WHERE status = ? ORDER BY requested_at DESC LIMIT ?",
                (status.value, limit),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM approval_requests ORDER BY requested_at DESC LIMIT ?",
                (limit,),
            )
        return [self._row_to_approval(row) for row in cursor.fetchall()]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
        logger.info("sqlite_workflow_store_closed")


class InMemoryWorkflowRunStore(WorkflowRunStore):
    """In-memory workflow run store for testing.

    All data is lost when the store is closed or the process ends.
    """

    def __init__(self) -> None:
        """Initialize in-memory store."""
        self._runs: dict[str, WorkflowRun] = {}
        self._checkpoints: dict[str, WorkflowCheckpoint] = {}
        self._approvals: dict[str, ApprovalRequest] = {}
        logger.info("in_memory_workflow_store_initialized")

    def create_run(self, run: WorkflowRun) -> None:
        """Create a new workflow run record."""
        self._runs[run.run_id] = run

    def get_run(self, run_id: str) -> WorkflowRun | None:
        """Get a workflow run by ID."""
        return self._runs.get(run_id)

    def update_run(self, run: WorkflowRun) -> None:
        """Update an existing workflow run."""
        self._runs[run.run_id] = run

    def list_runs(
        self,
        workflow_id: str | None = None,
        status: WorkflowRunStatus | None = None,
        limit: int = 100,
    ) -> list[WorkflowRun]:
        """List workflow runs with optional filters."""
        runs = list(self._runs.values())

        if workflow_id:
            runs = [r for r in runs if r.workflow_id == workflow_id]
        if status:
            runs = [r for r in runs if r.status == status]

        # Sort by created_at (approximated by run_id which is ULID)
        runs.sort(key=lambda r: r.run_id, reverse=True)
        return runs[:limit]

    def save_checkpoint(
        self,
        run_id: str,
        step_index: int,
        step_name: str,
        step_outputs: dict[str, Any],
        state_json: str | None = None,
    ) -> None:
        """Save a checkpoint after a step completes."""
        self._checkpoints[run_id] = WorkflowCheckpoint(
            run_id=run_id,
            step_index=step_index,
            step_name=step_name,
            step_outputs=step_outputs,
            state_json=state_json,
        )

    def get_checkpoint(self, run_id: str) -> WorkflowCheckpoint | None:
        """Get the latest checkpoint for a workflow run."""
        return self._checkpoints.get(run_id)

    def delete_checkpoints(self, run_id: str) -> None:
        """Delete all checkpoints for a workflow run (on completion)."""
        self._checkpoints.pop(run_id, None)

    def create_approval_request(self, request: ApprovalRequest) -> None:
        """Create a new approval request."""
        self._approvals[request.approval_id] = request

    def get_approval_request(self, approval_id: str) -> ApprovalRequest | None:
        """Get an approval request by ID."""
        return self._approvals.get(approval_id)

    def get_pending_approvals(self, run_id: str) -> list[ApprovalRequest]:
        """Get all pending approval requests for a run."""
        return [
            a
            for a in self._approvals.values()
            if a.run_id == run_id and a.status == ApprovalRequestStatus.PENDING
        ]

    def update_approval_request(self, request: ApprovalRequest) -> None:
        """Update an approval request."""
        self._approvals[request.approval_id] = request

    def get_run_by_idempotency_key(self, key: str) -> WorkflowRun | None:
        """Get a workflow run by its idempotency key."""
        for run in self._runs.values():
            if run.idempotency_key == key:
                return run
        return None

    def list_approval_requests(
        self,
        status: ApprovalRequestStatus | None = None,
        limit: int = 1000,
    ) -> list[ApprovalRequest]:
        """List approval requests with optional status filter."""
        approvals = list(self._approvals.values())
        if status:
            approvals = [a for a in approvals if a.status == status]
        approvals.sort(
            key=lambda a: a.requested_at.isoformat(), reverse=True
        )
        return approvals[:limit]

    def close(self) -> None:
        """Close the store (no-op for in-memory)."""
        logger.info("in_memory_workflow_store_closed")
