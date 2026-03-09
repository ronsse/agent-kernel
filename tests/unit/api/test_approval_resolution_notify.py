"""Unit tests for TraceItem cost field and /traces endpoint cost population.

Tests:
1. TraceItem has estimated_cost_usd field (float | None, default None)
2. /traces endpoint returns estimated_cost_usd summed from llm_calls[*].response.usage
3. /traces returns estimated_cost_usd = None for traces with no llm_calls
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agent_kernel.api.server import TraceItem, create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_llm_call(estimated_cost_usd: float | None = None) -> MagicMock:
    """Build a fake LLMCallRecord-like object."""
    llm_call = MagicMock()
    llm_call.response = MagicMock()
    llm_call.response.usage = MagicMock()
    llm_call.response.usage.estimated_cost_usd = estimated_cost_usd
    return llm_call


def _make_trace(
    trace_id: str = "tr-001",
    agent_profile_id: str = "default",
    llm_calls: list[Any] | None = None,
    tool_calls: list[Any] | None = None,
) -> MagicMock:
    """Build a fake DecisionTrace-like object."""
    from agent_kernel.core.schemas.trace import OutcomeStatus

    trace = MagicMock()
    trace.trace_id = trace_id
    trace.agent_profile_id = agent_profile_id
    trace.created_at = _utc_now()
    trace.outcome = MagicMock()
    trace.outcome.status = OutcomeStatus.COMPLETED
    trace.tool_calls = tool_calls or []
    trace.llm_calls = llm_calls or []
    return trace


def _make_trace_store(traces: list[Any]) -> MagicMock:
    """Build a mock trace store."""
    store = MagicMock()
    store.list = MagicMock(return_value=traces)
    store.count = MagicMock(return_value=len(traces))
    return store


# ---------------------------------------------------------------------------
# Test 1: TraceItem has estimated_cost_usd field
# ---------------------------------------------------------------------------


def test_trace_item_has_estimated_cost_usd_field() -> None:
    """TraceItem has estimated_cost_usd field that defaults to None."""
    item = TraceItem(
        trace_id="tr-001",
        agent_profile_id="default",
        outcome_status="completed",
        created_at="2026-03-05T10:00:00Z",
        tool_call_count=0,
    )
    assert hasattr(item, "estimated_cost_usd")
    assert item.estimated_cost_usd is None


def test_trace_item_accepts_float_cost() -> None:
    """TraceItem accepts a float estimated_cost_usd."""
    item = TraceItem(
        trace_id="tr-001",
        agent_profile_id="default",
        outcome_status="completed",
        created_at="2026-03-05T10:00:00Z",
        tool_call_count=2,
        estimated_cost_usd=0.0123,
    )
    assert item.estimated_cost_usd == pytest.approx(0.0123)


# ---------------------------------------------------------------------------
# Test 2: /traces returns estimated_cost_usd summed from llm_calls
# ---------------------------------------------------------------------------


def test_list_traces_returns_cost_sum_when_llm_calls_have_cost() -> None:
    """/traces endpoint populates estimated_cost_usd from llm_calls."""
    llm1 = _make_llm_call(estimated_cost_usd=0.005)
    llm2 = _make_llm_call(estimated_cost_usd=0.003)
    trace = _make_trace(trace_id="tr-001", llm_calls=[llm1, llm2])

    store = _make_trace_store([trace])
    app = create_app(trace_store=store)

    with TestClient(app) as client:
        resp = client.get("/traces")

    assert resp.status_code == 200
    data = resp.json()
    traces = data["traces"]
    assert len(traces) == 1
    cost = traces[0]["estimated_cost_usd"]
    assert cost is not None
    assert abs(cost - 0.008) < 1e-6


def test_list_traces_returns_cost_none_when_no_llm_calls() -> None:
    """/traces returns estimated_cost_usd=None for traces with no llm_calls."""
    trace = _make_trace(trace_id="tr-002", llm_calls=[])

    store = _make_trace_store([trace])
    app = create_app(trace_store=store)

    with TestClient(app) as client:
        resp = client.get("/traces")

    assert resp.status_code == 200
    data = resp.json()
    traces = data["traces"]
    assert len(traces) == 1
    assert traces[0]["estimated_cost_usd"] is None


def test_list_traces_returns_cost_none_when_llm_calls_have_no_cost() -> None:
    """/traces returns None when llm_calls exist but have no cost data."""
    llm1 = _make_llm_call(estimated_cost_usd=None)
    trace = _make_trace(trace_id="tr-003", llm_calls=[llm1])

    store = _make_trace_store([trace])
    app = create_app(trace_store=store)

    with TestClient(app) as client:
        resp = client.get("/traces")

    assert resp.status_code == 200
    data = resp.json()
    traces = data["traces"]
    assert len(traces) == 1
    assert traces[0]["estimated_cost_usd"] is None
