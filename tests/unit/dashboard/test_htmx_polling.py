"""Tests for DASH-05: HTMX live polling attributes on the dashboard."""
from __future__ import annotations

from fastapi.testclient import TestClient  # noqa: TC002


class TestHtmxPolling:
    """Tests that verify HTMX polling attributes are present on the dashboard."""

    def test_approvals_section_has_polling(self, client: TestClient) -> None:
        """GET /dashboard HTML contains hx-trigger with every 10s for approvals."""
        response = client.get("/dashboard")
        assert response.status_code == 200
        text = response.text
        # Dashboard has a section polling approvals every 10s
        assert "every 10s" in text
        assert "/dashboard/partials/approvals" in text

    def test_runs_section_has_polling(self, client: TestClient) -> None:
        """GET /dashboard HTML contains hx-trigger with every 10s for runs."""
        response = client.get("/dashboard")
        assert response.status_code == 200
        text = response.text
        assert "every 10s" in text
        assert "/dashboard/partials/runs" in text

    def test_stats_section_has_polling(self, client: TestClient) -> None:
        """GET /dashboard HTML contains hx-trigger with every 10s for stats."""
        response = client.get("/dashboard")
        assert response.status_code == 200
        text = response.text
        assert "every 10s" in text
        assert "/dashboard/partials/stats" in text

    def test_approvals_partial_has_oob_badge(self, client: TestClient) -> None:
        """GET /dashboard/partials/approvals contains hx-swap-oob for badge update."""
        response = client.get("/dashboard/partials/approvals")
        assert response.status_code == 200
        assert "hx-swap-oob" in response.text
        assert "pending-badge" in response.text

    def test_approvals_partial_has_oob_title(self, client: TestClient) -> None:
        """GET /dashboard/partials/approvals contains hx-swap-oob for title update."""
        response = client.get("/dashboard/partials/approvals")
        assert response.status_code == 200
        assert "hx-swap-oob" in response.text
        assert "page-title" in response.text
