"""Tests for DASH-03: workflow runs table partial."""
from __future__ import annotations

from unittest.mock import MagicMock

from agent_kernel.api.server import create_app
from fastapi.testclient import TestClient


class TestRunsPartial:
    """Tests for GET /dashboard/partials/runs."""

    def test_runs_partial_returns_200(self, client: TestClient) -> None:
        """GET /dashboard/partials/runs returns 200."""
        response = client.get("/dashboard/partials/runs")
        assert response.status_code == 200

    def test_runs_partial_contains_table(self, client: TestClient) -> None:
        """Response contains a <table> element when runs exist."""
        response = client.get("/dashboard/partials/runs")
        assert "<table" in response.text

    def test_runs_partial_shows_workflow_id(self, client: TestClient) -> None:
        """Response contains the mock workflow IDs."""
        response = client.get("/dashboard/partials/runs")
        # FAKE_RUNS has daily_checkin, weekly_review, vault_sync
        assert "daily_checkin" in response.text or "weekly_review" in response.text

    def test_runs_partial_shows_status_badge(self, client: TestClient) -> None:
        """Response contains color-coded status badge CSS classes."""
        response = client.get("/dashboard/partials/runs")
        # At least one status class must be present
        text = response.text
        has_status = any(
            f"status-{s}" in text
            for s in ("completed", "failed", "running", "waiting_approval")
        )
        assert has_status

    def test_runs_partial_empty_state(
        self,
        mock_workflow_store: MagicMock,
        mock_trace_store: MagicMock,
    ) -> None:
        """When no runs, shows 'No recent workflow runs'."""
        mock_workflow_store.list_runs.return_value = []
        app = create_app(
            workflow_store=mock_workflow_store,
            trace_store=mock_trace_store,
        )
        c = TestClient(app)
        response = c.get("/dashboard/partials/runs")
        assert response.status_code == 200
        assert "No recent workflow runs" in response.text
