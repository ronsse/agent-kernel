"""Workflow schemas - WorkflowRun and ApprovalRequest for persistence.

These schemas support:
- Durable workflow run tracking with status lifecycle
- Persistent approval requests for resume after approval
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import VersionedModel, utc_now
from agent_kernel.core.schemas.plan import SideEffect
from agent_kernel.core.schemas.trace import ErrorRecord


class WorkflowRunStatus(str, Enum):
    """Status of a workflow run."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowRun(VersionedModel):
    """Durable record of a workflow execution.

    Tracks the full lifecycle of a workflow run, enabling:
    - Status monitoring
    - Resume after approval
    - Retry tracking
    - Trace correlation
    """

    run_id: str = Field(default_factory=generate_ulid)
    workflow_id: str = Field(description="Workflow definition ID")
    status: WorkflowRunStatus = Field(
        default=WorkflowRunStatus.QUEUED,
        description="Current status of the workflow run",
    )
    intent: str | None = Field(
        default=None,
        description="Intent/goal for this run",
    )
    started_at: datetime | None = Field(
        default=None,
        description="When the workflow started executing",
    )
    ended_at: datetime | None = Field(
        default=None,
        description="When the workflow finished (completed, failed, or cancelled)",
    )
    last_step: str | None = Field(
        default=None,
        description="Last step that was executed or attempted",
    )
    retry_count: int = Field(
        default=0,
        description="Number of retry attempts",
    )
    error: ErrorRecord | None = Field(
        default=None,
        description="Error details if status is FAILED",
    )
    trace_ids: list[str] = Field(
        default_factory=list,
        description="Decision trace IDs created during this run",
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Optional idempotency key for deduplication",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional run metadata",
    )


class ApprovalRequestStatus(str, Enum):
    """Status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class ApprovalRequest(VersionedModel):
    """Persistent approval request for workflow resume.

    When a workflow encounters an action requiring approval,
    an ApprovalRequest is created and persisted. The workflow
    status is set to WAITING_APPROVAL.

    When the approval is resolved:
    1. ApprovalRequest status is updated
    2. An APPROVAL_RESOLVED event is emitted
    3. The workflow runner resumes execution
    """

    approval_id: str = Field(default_factory=generate_ulid)
    trace_id: str = Field(description="Associated DecisionTrace ID")
    run_id: str = Field(description="Associated WorkflowRun ID")
    workflow_id: str = Field(description="Workflow definition ID")
    action_id: str = Field(description="ActionRequest ID requiring approval")
    capability_name: str = Field(description="Capability being invoked")
    effective_side_effect: SideEffect = Field(
        description="Effective side effect level (from policy)",
    )
    status: ApprovalRequestStatus = Field(
        default=ApprovalRequestStatus.PENDING,
        description="Current approval status",
    )
    requested_at: datetime = Field(
        default_factory=utc_now,
        description="When the approval was requested",
    )
    resolved_at: datetime | None = Field(
        default=None,
        description="When the approval was resolved",
    )
    resolver: str | None = Field(
        default=None,
        description="Who resolved the approval (user ID or 'system')",
    )
    reason: str | None = Field(
        default=None,
        description="Reason for approval/denial",
    )
    action_preview: dict[str, Any] = Field(
        default_factory=dict,
        description="Redacted preview of action args for UI display",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="When the approval request expires",
    )
    policy_basis: str | None = Field(
        default=None,
        description="Policy rule that triggered the approval requirement",
    )
