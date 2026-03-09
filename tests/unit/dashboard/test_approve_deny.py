"""Tests for dashboard approve/deny actions and approvals partial."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agent_kernel.core.schemas.workflow import ApprovalRequestStatus


def test_approve_action_returns_html(client: TestClient) -> None:
    """POST /dashboard/approvals/{id}/approve returns 200 HTML."""
    response = client.post("/dashboard/approvals/approval-pending-001/approve")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_deny_action_returns_html(client: TestClient) -> None:
    """POST /dashboard/approvals/{id}/deny returns 200 HTML."""
    response = client.post("/dashboard/approvals/approval-pending-001/deny")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_approve_updates_store(
    client: TestClient,
    mock_workflow_store: MagicMock,
) -> None:
    """After POST approve, the store's update method was called with APPROVED status."""
    response = client.post("/dashboard/approvals/approval-pending-001/approve")
    assert response.status_code == 200
    # Verify update was called
    mock_workflow_store.update_approval_request.assert_called_once()
    updated = mock_workflow_store.update_approval_request.call_args[0][0]
    assert updated.status == ApprovalRequestStatus.APPROVED


def test_deny_updates_store(
    client: TestClient,
    mock_workflow_store: MagicMock,
) -> None:
    """After POST deny, the store's update method was called with DENIED status."""
    response = client.post("/dashboard/approvals/approval-pending-001/deny")
    assert response.status_code == 200
    # Verify update was called
    mock_workflow_store.update_approval_request.assert_called_once()
    updated = mock_workflow_store.update_approval_request.call_args[0][0]
    assert updated.status == ApprovalRequestStatus.DENIED


def test_approve_nonexistent_returns_404(client: TestClient) -> None:
    """POST approve with unknown ID returns 404."""
    response = client.post("/dashboard/approvals/nonexistent-id-xyz/approve")
    assert response.status_code == 404


def test_approvals_partial_returns_html(client: TestClient) -> None:
    """GET /dashboard/partials/approvals returns 200 HTML."""
    response = client.get("/dashboard/partials/approvals")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_approvals_partial_contains_pending(client: TestClient) -> None:
    """Approvals partial response contains the pending approval's capability_name."""
    response = client.get("/dashboard/partials/approvals")
    assert response.status_code == 200
    assert "shell.exec@v1" in response.text


def test_approvals_partial_has_polling_attr(client: TestClient) -> None:
    """Approvals partial response contains hx-trigger with 'every 10s' polling."""
    response = client.get("/dashboard/partials/approvals")
    assert response.status_code == 200
    assert "every 10s" in response.text
