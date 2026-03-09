"""Tests for dashboard route: GET /dashboard and partials."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_dashboard_returns_200(client: TestClient) -> None:
    """GET /dashboard returns 200 with HTML content-type."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_dashboard_contains_approvals_section(client: TestClient) -> None:
    """Dashboard HTML contains the approvals section."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert 'id="approvals-section"' in response.text


def test_dashboard_contains_runs_section(client: TestClient) -> None:
    """Dashboard HTML contains the runs section."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert 'id="runs-section"' in response.text


def test_dashboard_contains_stats_section(client: TestClient) -> None:
    """Dashboard HTML contains the stats section or stats bar."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert 'id="stats-section"' in response.text or "stats" in response.text.lower()


def test_dashboard_includes_htmx(client: TestClient) -> None:
    """Dashboard HTML includes the HTMX script tag."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "htmx.org" in response.text


def test_dashboard_includes_pico(client: TestClient) -> None:
    """Dashboard HTML includes the Pico CSS CDN link."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "picocss" in response.text or "pico" in response.text.lower()
