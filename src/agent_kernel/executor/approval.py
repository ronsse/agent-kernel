"""Approval Gate - manages approval workflow for actions.

Handles pending approvals, approval tokens, and approval/denial flow.
Supports pluggable notification callbacks for external alerting.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import structlog
from pydantic import Field

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas import ApprovalRecord
from agent_kernel.core.schemas.base import KernelModel, utc_now
from agent_kernel.memory.event_log import EventLog, EventType

logger = structlog.get_logger(__name__)

# Callback type: receives PendingApproval, returns None.
# May be sync or async — ApprovalGate calls it synchronously.
ApprovalNotifyCallback = Callable[["PendingApproval"], None]


class PendingApproval(KernelModel):
    """A pending approval request."""

    approval_id: str = Field(default_factory=generate_ulid)
    action_id: str
    capability_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    trace_id: str
    agent_profile_id: str
    requested_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    token: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    status: str = "pending"  # pending, approved, denied, expired
    reason: str | None = None


class ApprovalGate:
    """Manages the approval workflow for actions.

    Stores pending approvals and provides methods to approve or deny them.
    """

    def __init__(
        self,
        event_log: EventLog | None = None,
        default_expiry_hours: int = 24,
        on_approval_requested: ApprovalNotifyCallback | None = None,
    ) -> None:
        """Initialize approval gate.

        Args:
            event_log: Optional event log for recording events.
            default_expiry_hours: Hours until approval requests expire.
            on_approval_requested: Optional callback fired after each new
                approval request.  Use this to push notifications to
                external services, etc.
        """
        self._event_log = event_log
        self._default_expiry_hours = default_expiry_hours
        self._on_approval_requested = on_approval_requested
        self._pending: dict[str, PendingApproval] = {}
        self._records: list[ApprovalRecord] = []
        logger.info("approval_gate_initialized")

    def request_approval(
        self,
        action_id: str,
        capability_name: str,
        args: dict[str, Any],
        trace_id: str,
        agent_profile_id: str,
    ) -> PendingApproval:
        """Create a pending approval request.

        Args:
            action_id: The action requiring approval.
            capability_name: The capability being used.
            args: Action arguments.
            trace_id: Associated trace ID.
            agent_profile_id: The agent requesting approval.

        Returns:
            PendingApproval with token for later approval.
        """
        expires_at = utc_now() + timedelta(hours=self._default_expiry_hours)

        pending = PendingApproval(
            action_id=action_id,
            capability_name=capability_name,
            args=args,
            trace_id=trace_id,
            agent_profile_id=agent_profile_id,
            expires_at=expires_at,
        )

        self._pending[pending.approval_id] = pending

        if self._event_log:
            self._event_log.emit(
                EventType.APPROVAL_REQUESTED,
                source="approval_gate",
                entity_id=pending.approval_id,
                entity_type="approval",
                data={
                    "action_id": action_id,
                    "capability_name": capability_name,
                    "trace_id": trace_id,
                },
            )

        logger.info(
            "approval_requested",
            approval_id=pending.approval_id,
            action_id=action_id,
            capability_name=capability_name,
        )

        if self._on_approval_requested:
            try:
                self._on_approval_requested(pending)
            except Exception:
                logger.exception(
                    "approval_notification_failed",
                    approval_id=pending.approval_id,
                )

        return pending

    def approve(
        self,
        approval_id: str,
        approved_by: str = "user",
        reason: str | None = None,
    ) -> ApprovalRecord | None:
        """Approve a pending request.

        Args:
            approval_id: The approval ID.
            approved_by: Who approved it.
            reason: Optional reason.

        Returns:
            ApprovalRecord if found and approved, None otherwise.
        """
        pending = self._pending.get(approval_id)
        if pending is None:
            logger.warning("approval_not_found", approval_id=approval_id)
            return None

        if pending.status != "pending":
            logger.warning("approval_already_processed", approval_id=approval_id, status=pending.status)
            return None

        # Check expiry
        if pending.expires_at and utc_now() > pending.expires_at:
            pending.status = "expired"
            logger.warning("approval_expired", approval_id=approval_id)
            return None

        pending.status = "approved"
        pending.reason = reason

        record = ApprovalRecord(
            action_id=pending.action_id,
            approved=True,
            approved_by=approved_by,
            approved_at=utc_now(),
            reason=reason,
        )
        self._records.append(record)

        if self._event_log:
            self._event_log.emit(
                EventType.APPROVAL_GRANTED,
                source="approval_gate",
                entity_id=approval_id,
                entity_type="approval",
                data={
                    "action_id": pending.action_id,
                    "approved_by": approved_by,
                },
            )

        logger.info(
            "approval_granted",
            approval_id=approval_id,
            action_id=pending.action_id,
            approved_by=approved_by,
        )

        return record

    def deny(
        self,
        approval_id: str,
        denied_by: str = "user",
        reason: str | None = None,
    ) -> ApprovalRecord | None:
        """Deny a pending request.

        Args:
            approval_id: The approval ID.
            denied_by: Who denied it.
            reason: Reason for denial.

        Returns:
            ApprovalRecord if found, None otherwise.
        """
        pending = self._pending.get(approval_id)
        if pending is None:
            logger.warning("approval_not_found", approval_id=approval_id)
            return None

        if pending.status != "pending":
            logger.warning("approval_already_processed", approval_id=approval_id, status=pending.status)
            return None

        pending.status = "denied"
        pending.reason = reason

        record = ApprovalRecord(
            action_id=pending.action_id,
            approved=False,
            approved_by=denied_by,
            approved_at=utc_now(),
            reason=reason,
        )
        self._records.append(record)

        if self._event_log:
            self._event_log.emit(
                EventType.APPROVAL_DENIED,
                source="approval_gate",
                entity_id=approval_id,
                entity_type="approval",
                data={
                    "action_id": pending.action_id,
                    "denied_by": denied_by,
                    "reason": reason,
                },
            )

        logger.info(
            "approval_denied",
            approval_id=approval_id,
            action_id=pending.action_id,
            denied_by=denied_by,
        )

        return record

    def get_pending(self, approval_id: str) -> PendingApproval | None:
        """Get a pending approval by ID."""
        return self._pending.get(approval_id)

    def get_by_token(self, token: str) -> PendingApproval | None:
        """Get a pending approval by its token."""
        for pending in self._pending.values():
            if pending.token == token and pending.status == "pending":
                return pending
        return None

    def list_pending(
        self,
        agent_profile_id: str | None = None,
    ) -> list[PendingApproval]:
        """List pending approvals.

        Args:
            agent_profile_id: Filter by agent.

        Returns:
            List of pending approvals.
        """
        pending_list = [p for p in self._pending.values() if p.status == "pending"]

        if agent_profile_id:
            pending_list = [p for p in pending_list if p.agent_profile_id == agent_profile_id]

        # Check for expired
        now = utc_now()
        for p in pending_list:
            if p.expires_at and now > p.expires_at:
                p.status = "expired"

        return [p for p in pending_list if p.status == "pending"]

    def get_records(self, trace_id: str | None = None) -> list[ApprovalRecord]:
        """Get approval records.

        Args:
            trace_id: Filter by trace.

        Returns:
            List of approval records.
        """
        return self._records

    def cleanup_expired(self) -> int:
        """Remove expired pending approvals.

        Returns:
            Number of expired approvals removed.
        """
        now = utc_now()
        expired_ids = [
            approval_id
            for approval_id, p in self._pending.items()
            if p.expires_at and now > p.expires_at
        ]

        for approval_id in expired_ids:
            self._pending[approval_id].status = "expired"

        logger.debug("expired_approvals_cleaned", count=len(expired_ids))
        return len(expired_ids)

    def validate_token(self, action_id: str, token: str) -> bool:
        """Validate an approval token for an action.

        Args:
            action_id: The action ID.
            token: The approval token.

        Returns:
            True if token is valid and approved.
        """
        for pending in self._pending.values():
            if (
                pending.action_id == action_id
                and pending.token == token
                and pending.status == "approved"
            ):
                return True
        return False
