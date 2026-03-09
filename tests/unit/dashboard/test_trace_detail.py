"""Tests for DASH-04: trace detail drill-down partial."""
from __future__ import annotations

from unittest.mock import MagicMock

from agent_kernel.api.server import create_app
from fastapi.testclient import TestClient

from tests.unit.dashboard.conftest import _make_fake_trace

TRACE_ID = "trace-test-001"


class TestTraceDetail:
    """Tests for GET /dashboard/partials/trace/{trace_id}."""

    def test_trace_detail_returns_200(self, client: TestClient) -> None:
        """GET /dashboard/partials/trace/{id} returns 200."""
        response = client.get(f"/dashboard/partials/trace/{TRACE_ID}")
        assert response.status_code == 200

    def test_trace_detail_shows_intent(self, client: TestClient) -> None:
        """Response contains the trace's intent text."""
        response = client.get(f"/dashboard/partials/trace/{TRACE_ID}")
        assert "Run daily checkin" in response.text

    def test_trace_detail_shows_status(self, client: TestClient) -> None:
        """Response contains status badge markup."""
        response = client.get(f"/dashboard/partials/trace/{TRACE_ID}")
        assert "status-completed" in response.text

    def test_trace_detail_shows_tool_calls(self, client: TestClient) -> None:
        """Response contains tool call capability names."""
        response = client.get(f"/dashboard/partials/trace/{TRACE_ID}")
        assert "tasks.create@v1" in response.text

    def test_trace_detail_shows_plan_summary(self, client: TestClient) -> None:
        """Response contains plan summary text."""
        response = client.get(f"/dashboard/partials/trace/{TRACE_ID}")
        assert "Created tasks for today" in response.text

    def test_trace_detail_has_raw_json(self, client: TestClient) -> None:
        """Response contains 'Raw JSON' toggle text."""
        response = client.get(f"/dashboard/partials/trace/{TRACE_ID}")
        assert "Raw JSON" in response.text

    def test_trace_detail_not_found(
        self,
        mock_workflow_store: MagicMock,
        mock_trace_store: MagicMock,
    ) -> None:
        """GET with nonexistent ID returns HTML with 'not found' message."""
        mock_trace_store.get.return_value = None
        app = create_app(
            workflow_store=mock_workflow_store,
            trace_store=mock_trace_store,
        )
        c = TestClient(app)
        response = c.get("/dashboard/partials/trace/nonexistent-id")
        assert response.status_code == 200
        assert "not found" in response.text.lower()

    def test_trace_detail_shows_cost(
        self,
        mock_workflow_store: MagicMock,
        mock_trace_store: MagicMock,
    ) -> None:
        """If trace has LLM calls with cost, response contains dollar amount."""
        # Build a trace with an LLM call that has cost data
        trace = _make_fake_trace()

        # Create an LLM call mock with cost
        llm_call = MagicMock()
        llm_call.stage = "propose_plan"
        llm_call.duration_ms = 1234
        llm_call.request.model = "gpt-4o"
        llm_call.request.provider = "openai"
        llm_call.request.reasoning_effort = None
        llm_call.response.usage.estimated_cost_usd = 0.0042
        llm_call.response.usage.input_tokens = 800
        llm_call.response.usage.output_tokens = 200
        llm_call.response.usage.total_tokens = 1000

        # Replace llm_calls on the trace object
        object.__setattr__(trace, "llm_calls", [llm_call])

        mock_trace_store.get.return_value = trace
        app = create_app(
            workflow_store=mock_workflow_store,
            trace_store=mock_trace_store,
        )
        c = TestClient(app)
        response = c.get(f"/dashboard/partials/trace/{TRACE_ID}")
        assert response.status_code == 200
        # The cost should appear formatted (e.g. "$0.0042")
        assert "$" in response.text
