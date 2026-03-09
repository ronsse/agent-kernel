"""Thinking metrics computation from decision traces.

Aggregates reasoning metadata from traces to provide insights
on tier usage, escalation rates, gate failures, and costs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ThinkingMetrics:
    """Aggregated thinking metrics from a set of traces."""

    total_traces: int = 0
    traces_with_reasoning: int = 0
    tier_distribution: dict[int, int] = field(default_factory=dict)
    escalation_count: int = 0
    escalation_rate: float = 0.0
    gate_failure_counts: dict[str, int] = field(default_factory=dict)
    critic_utilization_rate: float = 0.0
    model_success_rates: dict[str, float] = field(default_factory=dict)
    tokens_per_tier: dict[int, float] = field(default_factory=dict)
    cost_per_workflow: dict[str, float] = field(default_factory=dict)


def compute_thinking_metrics(traces: list[Any]) -> ThinkingMetrics:
    """Aggregate thinking metrics from traces with ReasoningMetadata.

    Args:
        traces: List of DecisionTrace objects (or compatible) with
                optional ``reasoning`` attribute.

    Returns:
        ThinkingMetrics with aggregated data.
    """
    metrics = ThinkingMetrics()
    metrics.total_traces = len(traces)

    # Accumulators
    tier_token_sums: dict[int, list[int]] = {}
    model_calls: dict[str, int] = {}
    model_successes: dict[str, int] = {}
    workflow_costs: dict[str, float] = {}
    critic_used_count = 0

    for trace in traces:
        reasoning = getattr(trace, "reasoning", None)
        if reasoning is None:
            continue

        metrics.traces_with_reasoning += 1

        # Tier distribution (final tier)
        final_tier = getattr(reasoning, "final_tier", 1)
        metrics.tier_distribution[final_tier] = (
            metrics.tier_distribution.get(final_tier, 0) + 1
        )

        # Escalation tracking
        esc_count = getattr(reasoning, "escalation_count", 0)
        if esc_count > 0:
            metrics.escalation_count += 1

        # Gate failures
        gate_failures = getattr(reasoning, "gate_failures", []) or []
        for failure in gate_failures:
            metrics.gate_failure_counts[failure] = (
                metrics.gate_failure_counts.get(failure, 0) + 1
            )

        # Critic utilization
        if getattr(reasoning, "critic_used", False):
            critic_used_count += 1

        # Tokens per tier
        tokens = getattr(reasoning, "total_reasoning_tokens", 0)
        if tokens > 0:
            tier_token_sums.setdefault(final_tier, []).append(tokens)

        # Model success rates from LLM calls
        model_id = getattr(reasoning, "model_id", "")
        if model_id:
            model_calls[model_id] = model_calls.get(model_id, 0) + 1
            if _is_successful_trace(trace):
                model_successes[model_id] = (
                    model_successes.get(model_id, 0) + 1
                )

        # Cost per workflow
        workflow_id = getattr(trace, "workflow_id", None)
        if workflow_id:
            trace_cost = _compute_trace_cost(trace)
            workflow_costs[workflow_id] = (
                workflow_costs.get(workflow_id, 0.0) + trace_cost
            )

    # Compute derived metrics
    if metrics.traces_with_reasoning > 0:
        metrics.escalation_rate = (
            metrics.escalation_count / metrics.traces_with_reasoning
        )
        metrics.critic_utilization_rate = (
            critic_used_count / metrics.traces_with_reasoning
        )

    # Average tokens per tier
    for tier, token_list in tier_token_sums.items():
        metrics.tokens_per_tier[tier] = sum(token_list) / len(token_list)

    # Model success rates
    for model_id, total in model_calls.items():
        successes = model_successes.get(model_id, 0)
        metrics.model_success_rates[model_id] = successes / total if total > 0 else 0.0

    metrics.cost_per_workflow = workflow_costs

    return metrics


def _is_successful_trace(trace: Any) -> bool:
    """Check if a trace has a successful outcome."""
    outcome = getattr(trace, "outcome", None)
    if outcome is None:
        return False
    status = getattr(outcome, "status", None)
    status_val = getattr(status, "value", str(status)) if status else ""
    return status_val in ("completed", "partial")


def _compute_trace_cost(trace: Any) -> float:
    """Compute total cost from a trace's LLM calls."""
    total = 0.0

    llm_calls = getattr(trace, "llm_calls", None) or []
    for lc in llm_calls:
        estimated = getattr(lc, "estimated_cost_usd", None)
        if estimated is not None:
            total += estimated

    tool_calls = getattr(trace, "tool_calls", None) or []
    for tc in tool_calls:
        cost = getattr(tc, "cost", None)
        if cost is not None:
            estimated = getattr(cost, "estimated_cost_usd", None)
            if estimated is not None:
                total += estimated

    return total
