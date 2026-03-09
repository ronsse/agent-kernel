"""Tests for ApprovalPolicyOptimizer service."""

from __future__ import annotations

import pytest

from agent_kernel.core.schemas.plan import SideEffect
from agent_kernel.core.schemas.workflow import (
    ApprovalRequest,
    ApprovalRequestStatus,
    WorkflowRun,
)
from agent_kernel.services.approval_optimizer import (
    ApprovalPolicyOptimizer,
)
from agent_kernel.workflows.store import InMemoryWorkflowRunStore


@pytest.fixture
def store():
    return InMemoryWorkflowRunStore()


def _add_approval(
    store: InMemoryWorkflowRunStore,
    capability_name: str,
    status: ApprovalRequestStatus,
    run_id: str = "run_1",
    workflow_id: str = "test_wf",
) -> ApprovalRequest:
    """Helper to add an approval request to the store."""
    req = ApprovalRequest(
        trace_id="trace_1",
        run_id=run_id,
        workflow_id=workflow_id,
        action_id="action_1",
        capability_name=capability_name,
        effective_side_effect=SideEffect.LOCAL_WRITE,
        status=status,
    )
    store.create_approval_request(req)
    return req


def _setup_run(store: InMemoryWorkflowRunStore) -> WorkflowRun:
    """Create a workflow run in the store."""
    run = WorkflowRun(workflow_id="test_wf")
    store.create_run(run)
    return run


class TestApprovalPolicyOptimizer:
    def test_all_approved_recommends_auto_approve(self, store):
        _setup_run(store)
        for _ in range(5):
            _add_approval(
                store,
                capability_name="tasks.create@v1",
                status=ApprovalRequestStatus.APPROVED,
            )

        optimizer = ApprovalPolicyOptimizer(store, min_samples=5)
        analysis = optimizer.analyze()

        assert len(analysis.recommendations) == 1
        rec = analysis.recommendations[0]
        assert rec.capability_name == "tasks.create@v1"
        assert rec.recommendation == "auto_approve"
        assert rec.confidence == 1.0
        assert rec.evidence["approved"] == 5
        assert rec.evidence["denied"] == 0

    def test_all_denied_recommends_blocklist(self, store):
        _setup_run(store)
        for _ in range(5):
            _add_approval(
                store,
                capability_name="calendar.delete@v1",
                status=ApprovalRequestStatus.DENIED,
            )

        optimizer = ApprovalPolicyOptimizer(store, min_samples=5)
        analysis = optimizer.analyze()

        assert len(analysis.recommendations) == 1
        rec = analysis.recommendations[0]
        assert rec.capability_name == "calendar.delete@v1"
        assert rec.recommendation == "add_to_blocklist"
        assert rec.confidence == 1.0
        assert rec.evidence["denied"] == 5

    def test_below_min_samples_no_recommendation(self, store):
        _setup_run(store)
        for _ in range(3):
            _add_approval(
                store,
                capability_name="tasks.create@v1",
                status=ApprovalRequestStatus.APPROVED,
            )

        optimizer = ApprovalPolicyOptimizer(store, min_samples=5)
        analysis = optimizer.analyze()

        assert len(analysis.recommendations) == 0

    def test_mixed_results_no_recommendation(self, store):
        _setup_run(store)
        for _ in range(3):
            _add_approval(
                store,
                capability_name="tasks.create@v1",
                status=ApprovalRequestStatus.APPROVED,
            )
        for _ in range(3):
            _add_approval(
                store,
                capability_name="tasks.create@v1",
                status=ApprovalRequestStatus.DENIED,
            )

        optimizer = ApprovalPolicyOptimizer(store, min_samples=5)
        analysis = optimizer.analyze()

        assert len(analysis.recommendations) == 0

    def test_empty_store_empty_analysis(self, store):
        optimizer = ApprovalPolicyOptimizer(store, min_samples=5)
        analysis = optimizer.analyze()

        assert len(analysis.recommendations) == 0
        assert analysis.analyzed_count == 0

    def test_pending_requests_ignored(self, store):
        """Pending (unresolved) requests should not count toward analysis."""
        _setup_run(store)
        for _ in range(5):
            _add_approval(
                store,
                capability_name="tasks.create@v1",
                status=ApprovalRequestStatus.PENDING,
            )

        optimizer = ApprovalPolicyOptimizer(store, min_samples=5)
        analysis = optimizer.analyze()

        assert len(analysis.recommendations) == 0

    def test_multiple_capabilities(self, store):
        """Separate recommendations for different capabilities."""
        _setup_run(store)
        # 5 approved for tasks.create
        for _ in range(5):
            _add_approval(
                store,
                capability_name="tasks.create@v1",
                status=ApprovalRequestStatus.APPROVED,
            )
        # 5 denied for calendar.delete
        for _ in range(5):
            _add_approval(
                store,
                capability_name="calendar.delete@v1",
                status=ApprovalRequestStatus.DENIED,
            )

        optimizer = ApprovalPolicyOptimizer(store, min_samples=5)
        analysis = optimizer.analyze()

        assert len(analysis.recommendations) == 2
        recs_by_cap = {r.capability_name: r for r in analysis.recommendations}
        assert recs_by_cap["tasks.create@v1"].recommendation == "auto_approve"
        assert recs_by_cap["calendar.delete@v1"].recommendation == "add_to_blocklist"

    def test_analysis_reports_total_count(self, store):
        _setup_run(store)
        for _ in range(7):
            _add_approval(
                store,
                capability_name="tasks.create@v1",
                status=ApprovalRequestStatus.APPROVED,
            )

        optimizer = ApprovalPolicyOptimizer(store, min_samples=5)
        analysis = optimizer.analyze()

        assert analysis.analyzed_count == 7
        assert analysis.period_days == 30
