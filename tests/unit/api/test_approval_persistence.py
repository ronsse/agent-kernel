"""Tests for approval persistence through REST API.

Verifies that the REST API reads/writes approvals from/to the
WorkflowRunStore (persistent) rather than the in-memory ApprovalGate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from agent_kernel.api.server import create_app
from agent_kernel.core.schemas.plan import SideEffect
from agent_kernel.core.schemas.workflow import (
    ApprovalRequest as WorkflowApprovalRequest,
    ApprovalRequestStatus,
)
from agent_kernel.workflows.store import InMemoryWorkflowRunStore


def _make_store() -> InMemoryWorkflowRunStore:
    """Create a fresh in-memory workflow run store."""
    return InMemoryWorkflowRunStore()


def _create_test_approval(
    store: InMemoryWorkflowRunStore,
    **overrides: object,
) -> WorkflowApprovalRequest:
    """Create a pending approval in the store and return it."""
    defaults: dict = {
        "approval_id": "test_appr_001",
        "trace_id": "trace_001",
        "run_id": "run_001",
        "workflow_id": "test_workflow",
        "action_id": "action_001",
        "capability_name": "calendar.create@v1",
        "effective_side_effect": SideEffect.EXTERNAL_WRITE,
        "status": ApprovalRequestStatus.PENDING,
        "requested_at": datetime(2026, 3, 4, 12, 0, 0, tzinfo=timezone.utc),
        "action_preview": {"title": "Test Meeting"},
    }
    defaults.update(overrides)
    approval = WorkflowApprovalRequest(**defaults)
    store.create_approval_request(approval)
    return approval


def _build_client(
    store: InMemoryWorkflowRunStore,
    workflow_runner: object | None = None,
) -> TestClient:
    """Build a TestClient backed by the given store."""
    app = create_app(
        workflow_store=store,
        workflow_runner=workflow_runner,
    )
    return TestClient(app)


# -------------------------------------------------------------------------
# Test 1: GET /approvals/pending returns approvals from store
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending_approvals_reads_from_store():
    """GET /approvals/pending returns approvals from the workflow store."""
    store = _make_store()
    _create_test_approval(store, approval_id="appr_A")
    _create_test_approval(store, approval_id="appr_B")

    client = _build_client(store)
    resp = client.get("/approvals/pending")

    assert resp.status_code == 200
    data = resp.json()
    ids = {item["approval_id"] for item in data["pending"]}
    assert "appr_A" in ids
    assert "appr_B" in ids


# -------------------------------------------------------------------------
# Test 2: POST /approvals/respond updates approval status in the store
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_respond_to_approval_writes_to_store():
    """POST /approvals/respond updates approval in the workflow store."""
    store = _make_store()
    _create_test_approval(store)

    client = _build_client(store)
    resp = client.post(
        "/approvals/respond",
        json={
            "approval_id": "test_appr_001",
            "approved": True,
            "approved_by": "test-user",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["approved"] is True

    # Verify the store was updated
    updated = store.get_approval_request("test_appr_001")
    assert updated is not None
    assert updated.status == ApprovalRequestStatus.APPROVED
    assert updated.resolver == "test-user"
    assert updated.resolved_at is not None


# -------------------------------------------------------------------------
# Test 3: Approval survives app recreation (restart simulation)
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_survives_app_recreation():
    """Create approval, rebuild app with same store, approval still visible."""
    store = _make_store()
    _create_test_approval(store, approval_id="survive_001")

    # First app instance -- verify approval exists
    client1 = _build_client(store)
    resp1 = client1.get("/approvals/pending")
    assert resp1.status_code == 200
    assert any(
        p["approval_id"] == "survive_001" for p in resp1.json()["pending"]
    )

    # Simulate restart: create a NEW app with the SAME store
    client2 = _build_client(store)
    resp2 = client2.get("/approvals/pending")
    assert resp2.status_code == 200
    assert any(
        p["approval_id"] == "survive_001" for p in resp2.json()["pending"]
    )


# -------------------------------------------------------------------------
# Test 4: Approved triggers workflow resume
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_response_triggers_workflow_resume():
    """POST /approvals/respond with approved=true calls runner.resume()."""
    store = _make_store()
    _create_test_approval(store)

    mock_runner = MagicMock()
    mock_runner.resume = AsyncMock()

    client = _build_client(store, workflow_runner=mock_runner)
    resp = client.post(
        "/approvals/respond",
        json={
            "approval_id": "test_appr_001",
            "approved": True,
            "approved_by": "test-user",
        },
    )

    assert resp.status_code == 200
    mock_runner.resume.assert_called_once()
    call_args = mock_runner.resume.call_args
    assert call_args[0][0] == "run_001"  # run_id
    assert "action_001" in call_args[1]["approval_tokens"]


# -------------------------------------------------------------------------
# Test 5: Denied does NOT trigger resume
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deny_approval_does_not_resume():
    """POST /approvals/respond with approved=false does NOT call resume."""
    store = _make_store()
    _create_test_approval(store)

    mock_runner = MagicMock()
    mock_runner.resume = AsyncMock()

    client = _build_client(store, workflow_runner=mock_runner)
    resp = client.post(
        "/approvals/respond",
        json={
            "approval_id": "test_appr_001",
            "approved": False,
            "approved_by": "test-user",
            "reason": "Not needed",
        },
    )

    assert resp.status_code == 200
    mock_runner.resume.assert_not_called()

    # Verify denial was recorded
    updated = store.get_approval_request("test_appr_001")
    assert updated is not None
    assert updated.status == ApprovalRequestStatus.DENIED
    assert updated.reason == "Not needed"


# -------------------------------------------------------------------------
# Test 6: Double-approving returns 409
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_approval_returns_409():
    """POST /approvals/respond on already-resolved approval returns 409."""
    store = _make_store()
    _create_test_approval(store)

    client = _build_client(store)

    # First approval succeeds
    resp1 = client.post(
        "/approvals/respond",
        json={
            "approval_id": "test_appr_001",
            "approved": True,
            "approved_by": "test-user",
        },
    )
    assert resp1.status_code == 200

    # Second approval returns 409
    resp2 = client.post(
        "/approvals/respond",
        json={
            "approval_id": "test_appr_001",
            "approved": True,
            "approved_by": "test-user",
        },
    )
    assert resp2.status_code == 409
