"""Approval notification callbacks.

Pluggable notifiers that fire when an ApprovalGate creates a new
pending approval.  Each factory returns an ``ApprovalNotifyCallback``
suitable for passing to ``ApprovalGate(on_approval_requested=...)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


def log_only_approval_notifier() -> "ApprovalNotifyCallback":  # noqa: F821
    """Notifier that only logs -- useful for testing or when no external
    notification channel is configured."""
    from agent_kernel.executor.approval import PendingApproval

    def _notify(pending: PendingApproval) -> None:
        logger.info(
            "approval_notification_log_only",
            approval_id=pending.approval_id,
            capability_name=pending.capability_name,
            agent=pending.agent_profile_id,
        )

    return _notify
