"""Approval Policy Optimizer - analyzes approval patterns for recommendations.

Examines historical approval decisions to suggest policy changes:
- Capabilities always approved -> suggest auto_approve
- Capabilities always denied -> suggest add_to_blocklist
- Advisory only - no automatic policy changes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.workflow import (
    ApprovalRequest,
    ApprovalRequestStatus,
)
from agent_kernel.memory.experience_store import ExperienceStore
from agent_kernel.workflows.store import WorkflowRunStore

logger = structlog.get_logger(__name__)


@dataclass
class PolicyRecommendation:
    """A single recommendation for approval policy change."""

    capability_name: str
    recommendation: str  # "auto_approve" or "add_to_blocklist"
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class ApprovalAnalysis:
    """Result of analyzing approval patterns."""

    recommendations: list[PolicyRecommendation] = field(default_factory=list)
    analyzed_count: int = 0
    period_days: int = 30
    generated_at: datetime = field(default_factory=utc_now)


class ApprovalPolicyOptimizer:
    """Analyzes historical approval decisions and suggests policy changes.

    This is advisory only -- it does not modify policies automatically.
    """

    def __init__(
        self,
        workflow_store: WorkflowRunStore,
        experience_store: ExperienceStore | None = None,
        min_samples: int = 5,
    ) -> None:
        self._store = workflow_store
        self._experience_store = experience_store
        self._min_samples = min_samples

    def _get_all_approvals(self) -> list[ApprovalRequest]:
        """Get all approval requests from the store.

        Uses duck-typing to access approval data through whatever
        interface is available on the concrete store implementation.
        """
        # Try the new method first (added by task 4)
        if hasattr(self._store, "list_approval_requests"):
            return self._store.list_approval_requests(limit=1000)

        # InMemoryWorkflowRunStore exposes _approvals directly
        if hasattr(self._store, "_approvals"):
            return list(self._store._approvals.values())

        # SQLiteWorkflowRunStore: query directly
        if hasattr(self._store, "_conn") and hasattr(
            self._store, "_row_to_approval"
        ):
            cursor = self._store._conn.execute(
                "SELECT * FROM approval_requests "
                "ORDER BY requested_at DESC LIMIT 1000"
            )
            return [
                self._store._row_to_approval(row)
                for row in cursor.fetchall()
            ]

        return []

    def analyze(self, period_days: int = 30) -> ApprovalAnalysis:
        """Analyze approval patterns and generate recommendations.

        Args:
            period_days: Number of days to include in the analysis window.

        Returns:
            ApprovalAnalysis with any recommendations.
        """
        approvals = self._get_all_approvals()

        if not approvals:
            return ApprovalAnalysis(
                analyzed_count=0,
                period_days=period_days,
            )

        # Group by capability_name
        by_capability: dict[str, list[ApprovalRequest]] = defaultdict(list)
        for req in approvals:
            by_capability[req.capability_name].append(req)

        recommendations: list[PolicyRecommendation] = []

        for capability_name, reqs in by_capability.items():
            # Only resolved requests (approved or denied)
            resolved = [
                r
                for r in reqs
                if r.status
                in (
                    ApprovalRequestStatus.APPROVED,
                    ApprovalRequestStatus.DENIED,
                )
            ]

            if len(resolved) < self._min_samples:
                continue

            approved_count = sum(
                1
                for r in resolved
                if r.status == ApprovalRequestStatus.APPROVED
            )
            denied_count = sum(
                1
                for r in resolved
                if r.status == ApprovalRequestStatus.DENIED
            )
            total = len(resolved)

            if approved_count == total:
                # All approved -> suggest auto_approve
                recommendations.append(
                    PolicyRecommendation(
                        capability_name=capability_name,
                        recommendation="auto_approve",
                        confidence=approved_count / max(total, 1),
                        evidence={
                            "approved": approved_count,
                            "denied": denied_count,
                            "total": total,
                        },
                        reason=(
                            f"All {total} requests for "
                            f"{capability_name} were approved"
                        ),
                    )
                )
            elif denied_count == total:
                # All denied -> suggest blocklist
                recommendations.append(
                    PolicyRecommendation(
                        capability_name=capability_name,
                        recommendation="add_to_blocklist",
                        confidence=denied_count / max(total, 1),
                        evidence={
                            "approved": approved_count,
                            "denied": denied_count,
                            "total": total,
                        },
                        reason=(
                            f"All {total} requests for "
                            f"{capability_name} were denied"
                        ),
                    )
                )
            # Mixed results: no recommendation

        logger.info(
            "approval_analysis_complete",
            analyzed_count=len(approvals),
            recommendations_count=len(recommendations),
        )

        return ApprovalAnalysis(
            recommendations=recommendations,
            analyzed_count=len(approvals),
            period_days=period_days,
        )
