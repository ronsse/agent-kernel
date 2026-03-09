"""Approval Store - persistent storage for approval requests.

Provides durable persistence for ApprovalRequest entities,
enabling workflow resume after approval.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.workflow import (
    ApprovalRequest,
    ApprovalRequestStatus,
)

logger = structlog.get_logger(__name__)


class ApprovalStore:
    """SQLite-backed store for approval requests.

    Supports:
    - Persistent storage of pending approvals
    - Status updates for approve/deny/expire
    - Querying pending approvals for resume
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialize approval store.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        logger.info("approval_store_initialized", db_path=str(self._db_path))

    def _init_schema(self) -> None:
        """Initialize database schema."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS approval_requests (
                approval_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                capability_name TEXT NOT NULL,
                effective_side_effect TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                resolved_at TEXT,
                resolver TEXT,
                reason TEXT,
                action_preview_json TEXT,
                expires_at TEXT,
                policy_basis TEXT,
                schema_version TEXT NOT NULL,
                kernel_version TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_approvals_status
                ON approval_requests(status);
            CREATE INDEX IF NOT EXISTS idx_approvals_run_id
                ON approval_requests(run_id);
            CREATE INDEX IF NOT EXISTS idx_approvals_workflow_id
                ON approval_requests(workflow_id);
            CREATE INDEX IF NOT EXISTS idx_approvals_requested_at
                ON approval_requests(requested_at);
        """)
        self._conn.commit()

    def save(self, approval: ApprovalRequest) -> None:
        """Save or update an approval request.

        Args:
            approval: The approval request to save.
        """
        self._conn.execute(
            """
            INSERT OR REPLACE INTO approval_requests
            (approval_id, trace_id, run_id, workflow_id, action_id,
             capability_name, effective_side_effect, status, requested_at,
             resolved_at, resolver, reason, action_preview_json, expires_at,
             policy_basis, schema_version, kernel_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval.approval_id,
                approval.trace_id,
                approval.run_id,
                approval.workflow_id,
                approval.action_id,
                approval.capability_name,
                approval.effective_side_effect.value,
                approval.status.value,
                approval.requested_at.isoformat(),
                approval.resolved_at.isoformat() if approval.resolved_at else None,
                approval.resolver,
                approval.reason,
                json.dumps(approval.action_preview),
                approval.expires_at.isoformat() if approval.expires_at else None,
                approval.policy_basis,
                approval.schema_version,
                approval.kernel_version,
            ),
        )
        self._conn.commit()
        logger.debug("approval_saved", approval_id=approval.approval_id)

    def get(self, approval_id: str) -> ApprovalRequest | None:
        """Get an approval request by ID.

        Args:
            approval_id: The approval request ID.

        Returns:
            The approval request if found, None otherwise.
        """
        cursor = self._conn.execute(
            "SELECT * FROM approval_requests WHERE approval_id = ?",
            (approval_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return self._row_to_approval(row)

    def list_pending(
        self,
        *,
        run_id: str | None = None,
        workflow_id: str | None = None,
        limit: int = 100,
    ) -> list[ApprovalRequest]:
        """List pending approval requests.

        Args:
            run_id: Filter by workflow run ID.
            workflow_id: Filter by workflow definition ID.
            limit: Maximum number of results.

        Returns:
            List of pending approval requests.
        """
        conditions = ["status = ?"]
        params: list[Any] = [ApprovalRequestStatus.PENDING.value]

        if run_id:
            conditions.append("run_id = ?")
            params.append(run_id)

        if workflow_id:
            conditions.append("workflow_id = ?")
            params.append(workflow_id)

        where_clause = " AND ".join(conditions)
        params.append(limit)

        cursor = self._conn.execute(
            f"""
            SELECT * FROM approval_requests
            WHERE {where_clause}
            ORDER BY requested_at ASC
            LIMIT ?
            """,
            params,
        )

        return [self._row_to_approval(row) for row in cursor.fetchall()]

    def list_by_run(self, run_id: str) -> list[ApprovalRequest]:
        """List all approval requests for a workflow run.

        Args:
            run_id: The workflow run ID.

        Returns:
            List of approval requests.
        """
        cursor = self._conn.execute(
            """
            SELECT * FROM approval_requests
            WHERE run_id = ?
            ORDER BY requested_at ASC
            """,
            (run_id,),
        )
        return [self._row_to_approval(row) for row in cursor.fetchall()]

    def approve(
        self,
        approval_id: str,
        resolver: str = "user",
        reason: str | None = None,
    ) -> ApprovalRequest | None:
        """Approve a pending request.

        Args:
            approval_id: The approval request ID.
            resolver: Who approved it.
            reason: Optional reason.

        Returns:
            Updated approval request if found, None otherwise.
        """
        return self._update_status(
            approval_id,
            ApprovalRequestStatus.APPROVED,
            resolver,
            reason,
        )

    def deny(
        self,
        approval_id: str,
        resolver: str = "user",
        reason: str | None = None,
    ) -> ApprovalRequest | None:
        """Deny a pending request.

        Args:
            approval_id: The approval request ID.
            resolver: Who denied it.
            reason: Reason for denial.

        Returns:
            Updated approval request if found, None otherwise.
        """
        return self._update_status(
            approval_id,
            ApprovalRequestStatus.DENIED,
            resolver,
            reason,
        )

    def expire(self, approval_id: str) -> ApprovalRequest | None:
        """Mark a request as expired.

        Args:
            approval_id: The approval request ID.

        Returns:
            Updated approval request if found, None otherwise.
        """
        return self._update_status(
            approval_id,
            ApprovalRequestStatus.EXPIRED,
            "system",
            "Request expired",
        )

    def cleanup_expired(self) -> int:
        """Mark expired approval requests.

        Returns:
            Number of requests marked as expired.
        """
        now = utc_now()
        cursor = self._conn.execute(
            """
            UPDATE approval_requests
            SET status = ?, resolved_at = ?, resolver = ?, reason = ?
            WHERE status = ? AND expires_at IS NOT NULL AND expires_at < ?
            """,
            (
                ApprovalRequestStatus.EXPIRED.value,
                now.isoformat(),
                "system",
                "Request expired",
                ApprovalRequestStatus.PENDING.value,
                now.isoformat(),
            ),
        )
        self._conn.commit()
        count = cursor.rowcount
        if count > 0:
            logger.info("expired_approvals_cleaned", count=count)
        return count

    def count_pending(self, workflow_id: str | None = None) -> int:
        """Count pending approval requests.

        Args:
            workflow_id: Optional filter by workflow ID.

        Returns:
            Number of pending requests.
        """
        if workflow_id:
            cursor = self._conn.execute(
                """
                SELECT COUNT(*) as cnt FROM approval_requests
                WHERE status = ? AND workflow_id = ?
                """,
                (ApprovalRequestStatus.PENDING.value, workflow_id),
            )
        else:
            cursor = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM approval_requests WHERE status = ?",
                (ApprovalRequestStatus.PENDING.value,),
            )
        return cursor.fetchone()["cnt"]

    def _update_status(
        self,
        approval_id: str,
        status: ApprovalRequestStatus,
        resolver: str,
        reason: str | None,
    ) -> ApprovalRequest | None:
        """Update the status of an approval request."""
        approval = self.get(approval_id)
        if approval is None:
            logger.warning("approval_not_found", approval_id=approval_id)
            return None

        if approval.status != ApprovalRequestStatus.PENDING:
            logger.warning(
                "approval_already_resolved",
                approval_id=approval_id,
                current_status=approval.status.value,
            )
            return approval

        now = utc_now()
        self._conn.execute(
            """
            UPDATE approval_requests
            SET status = ?, resolved_at = ?, resolver = ?, reason = ?
            WHERE approval_id = ?
            """,
            (
                status.value,
                now.isoformat(),
                resolver,
                reason,
                approval_id,
            ),
        )
        self._conn.commit()

        # Reload and return
        updated = self.get(approval_id)
        if updated:
            logger.info(
                "approval_status_updated",
                approval_id=approval_id,
                status=status.value,
                resolver=resolver,
            )
        return updated

    def _row_to_approval(self, row: sqlite3.Row) -> ApprovalRequest:
        """Convert a database row to ApprovalRequest."""
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
                datetime.fromisoformat(row["resolved_at"])
                if row["resolved_at"]
                else None
            ),
            resolver=row["resolver"],
            reason=row["reason"],
            action_preview=json.loads(row["action_preview_json"] or "{}"),
            expires_at=(
                datetime.fromisoformat(row["expires_at"])
                if row["expires_at"]
                else None
            ),
            policy_basis=row["policy_basis"],
            schema_version=row["schema_version"],
            kernel_version=row["kernel_version"],
        )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
        logger.info("approval_store_closed")
