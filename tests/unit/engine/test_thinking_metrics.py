"""Tests for thinking metrics computation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import pytest
from agent_kernel.engine.thinking_metrics import (
    compute_thinking_metrics,
)


class FakeOutcomeStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class FakeOutcome:
    status: FakeOutcomeStatus = FakeOutcomeStatus.COMPLETED


@dataclass
class FakeReasoning:
    initial_tier: int = 1
    final_tier: int = 1
    tier_name: str = "standard"
    model_id: str = "gpt-4o"
    reasoning_effort: str = "medium"
    total_attempts: int = 1
    escalation_count: int = 0
    escalation_reasons: list[str] = field(default_factory=list)
    gate_failures: list[str] = field(default_factory=list)
    gate_warnings: list[str] = field(default_factory=list)
    critic_used: bool = False
    critic_issues: list[str] = field(default_factory=list)
    total_reasoning_tokens: int = 0


@dataclass
class FakeLLMCall:
    estimated_cost_usd: float = 0.01


@dataclass
class FakeTrace:
    trace_id: str = "trace_001"
    workflow_id: str | None = None
    reasoning: FakeReasoning | None = None
    outcome: FakeOutcome = field(default_factory=FakeOutcome)
    llm_calls: list[FakeLLMCall] = field(default_factory=list)
    tool_calls: list = field(default_factory=list)


class TestComputeThinkingMetrics:
    def test_tier_distribution(self):
        traces = [
            FakeTrace(reasoning=FakeReasoning(final_tier=1)),
            FakeTrace(reasoning=FakeReasoning(final_tier=1)),
            FakeTrace(reasoning=FakeReasoning(final_tier=2)),
            FakeTrace(reasoning=FakeReasoning(final_tier=3)),
        ]

        metrics = compute_thinking_metrics(traces)
        assert metrics.total_traces == 4
        assert metrics.traces_with_reasoning == 4
        assert metrics.tier_distribution == {1: 2, 2: 1, 3: 1}

    def test_escalation_rate(self):
        traces = [
            FakeTrace(reasoning=FakeReasoning(escalation_count=0)),
            FakeTrace(reasoning=FakeReasoning(escalation_count=1)),
            FakeTrace(reasoning=FakeReasoning(escalation_count=0)),
            FakeTrace(reasoning=FakeReasoning(escalation_count=2)),
        ]

        metrics = compute_thinking_metrics(traces)
        assert metrics.escalation_count == 2  # 2 traces had escalations
        assert metrics.escalation_rate == pytest.approx(0.5)

    def test_gate_failure_counts(self):
        traces = [
            FakeTrace(reasoning=FakeReasoning(
                gate_failures=["coverage_gate", "parity_gate"]
            )),
            FakeTrace(reasoning=FakeReasoning(
                gate_failures=["coverage_gate"]
            )),
            FakeTrace(reasoning=FakeReasoning(gate_failures=[])),
        ]

        metrics = compute_thinking_metrics(traces)
        assert metrics.gate_failure_counts == {
            "coverage_gate": 2,
            "parity_gate": 1,
        }

    def test_critic_utilization(self):
        traces = [
            FakeTrace(reasoning=FakeReasoning(critic_used=True)),
            FakeTrace(reasoning=FakeReasoning(critic_used=False)),
            FakeTrace(reasoning=FakeReasoning(critic_used=True)),
            FakeTrace(reasoning=FakeReasoning(critic_used=False)),
        ]

        metrics = compute_thinking_metrics(traces)
        assert metrics.critic_utilization_rate == pytest.approx(0.5)

    def test_empty_traces(self):
        metrics = compute_thinking_metrics([])
        assert metrics.total_traces == 0
        assert metrics.traces_with_reasoning == 0
        assert metrics.escalation_rate == 0.0
        assert metrics.tier_distribution == {}

    def test_traces_without_reasoning(self):
        traces = [
            FakeTrace(reasoning=None),
            FakeTrace(reasoning=None),
        ]

        metrics = compute_thinking_metrics(traces)
        assert metrics.total_traces == 2
        assert metrics.traces_with_reasoning == 0
        assert metrics.escalation_rate == 0.0

    def test_model_success_rates(self):
        traces = [
            FakeTrace(
                reasoning=FakeReasoning(model_id="gpt-4o"),
                outcome=FakeOutcome(FakeOutcomeStatus.COMPLETED),
            ),
            FakeTrace(
                reasoning=FakeReasoning(model_id="gpt-4o"),
                outcome=FakeOutcome(FakeOutcomeStatus.FAILED),
            ),
            FakeTrace(
                reasoning=FakeReasoning(model_id="gpt-4o-mini"),
                outcome=FakeOutcome(FakeOutcomeStatus.COMPLETED),
            ),
        ]

        metrics = compute_thinking_metrics(traces)
        assert metrics.model_success_rates["gpt-4o"] == pytest.approx(0.5)
        assert metrics.model_success_rates["gpt-4o-mini"] == pytest.approx(1.0)

    def test_tokens_per_tier(self):
        traces = [
            FakeTrace(reasoning=FakeReasoning(
                final_tier=1, total_reasoning_tokens=100,
            )),
            FakeTrace(reasoning=FakeReasoning(
                final_tier=1, total_reasoning_tokens=200,
            )),
            FakeTrace(reasoning=FakeReasoning(
                final_tier=2, total_reasoning_tokens=500,
            )),
        ]

        metrics = compute_thinking_metrics(traces)
        assert metrics.tokens_per_tier[1] == pytest.approx(150.0)
        assert metrics.tokens_per_tier[2] == pytest.approx(500.0)

    def test_cost_per_workflow(self):
        traces = [
            FakeTrace(
                workflow_id="daily_checkin",
                reasoning=FakeReasoning(),
                llm_calls=[FakeLLMCall(estimated_cost_usd=0.05)],
            ),
            FakeTrace(
                workflow_id="daily_checkin",
                reasoning=FakeReasoning(),
                llm_calls=[FakeLLMCall(estimated_cost_usd=0.03)],
            ),
            FakeTrace(
                workflow_id="weekly_review",
                reasoning=FakeReasoning(),
                llm_calls=[FakeLLMCall(estimated_cost_usd=0.10)],
            ),
        ]

        metrics = compute_thinking_metrics(traces)
        assert metrics.cost_per_workflow["daily_checkin"] == pytest.approx(0.08)
        assert metrics.cost_per_workflow["weekly_review"] == pytest.approx(0.10)

    def test_mixed_traces(self):
        traces = [
            FakeTrace(reasoning=FakeReasoning(final_tier=1, escalation_count=0)),
            FakeTrace(reasoning=None),  # No reasoning
            FakeTrace(reasoning=FakeReasoning(final_tier=3, escalation_count=2)),
        ]

        metrics = compute_thinking_metrics(traces)
        assert metrics.total_traces == 3
        assert metrics.traces_with_reasoning == 2
        assert metrics.escalation_count == 1
        assert metrics.escalation_rate == pytest.approx(0.5)
