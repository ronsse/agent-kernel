"""Standalone model routing by historical success rate.

Queries trace store data to rank models by their success rates
and recommend the best-performing model within budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from agent_kernel.tracing.trace_store import TraceStore

logger = structlog.get_logger(__name__)


@dataclass
class ModelRecommendation:
    """A model recommendation based on historical performance."""

    model_id: str
    success_rate: float
    total_calls: int
    avg_cost_per_call: float = 0.0


class SuccessRateRouter:
    """Route to best-performing model based on trace history.

    Maintains a cache of model performance stats refreshed from
    the trace store periodically.
    """

    def __init__(
        self,
        trace_store: TraceStore | None = None,
        min_success_rate: float = 0.85,
        min_samples: int = 20,
        max_cost_per_call: float | None = None,
        cache_ttl_seconds: int = 300,
        lookback_hours: int = 168,
    ) -> None:
        self._trace_store = trace_store
        self._min_success_rate = min_success_rate
        self._min_samples = min_samples
        self._max_cost_per_call = max_cost_per_call
        self._cache_ttl = cache_ttl_seconds
        self._lookback_hours = lookback_hours

        self._model_stats: dict[str, _ModelStats] = {}
        self._cache_updated_at: datetime | None = None

        logger.info(
            "success_rate_router_initialized",
            min_success_rate=min_success_rate,
            min_samples=min_samples,
        )

    def refresh_stats(self) -> None:
        """Refresh model stats from trace store."""
        if self._trace_store is None:
            return

        now = datetime.now(UTC)
        if (
            self._cache_updated_at is not None
            and (now - self._cache_updated_at).total_seconds() < self._cache_ttl
        ):
            return

        try:
            since = now - timedelta(hours=self._lookback_hours)
            traces = self._trace_store.list_traces(since=since, limit=1000)
            self._compute_stats(traces)
            self._cache_updated_at = now
        except Exception as e:
            logger.warning("success_rate_router_refresh_failed", error=str(e))

    def _compute_stats(self, traces: list[Any]) -> None:
        """Aggregate model stats from traces."""
        model_data: dict[str, _ModelStats] = {}

        for trace in traces:
            if not trace.llm_calls:
                continue
            for llm_call in trace.llm_calls:
                model_id = llm_call.request.model
                if model_id not in model_data:
                    model_data[model_id] = _ModelStats(model_id=model_id)

                stats = model_data[model_id]
                stats.total_calls += 1

                if llm_call.response.output_text is not None:
                    stats.successful_calls += 1
                else:
                    stats.failed_calls += 1

                stats.total_cost += llm_call.estimated_cost_usd or 0.0

        self._model_stats = model_data

    def recommend(
        self,
        workflow_id: str | None = None,  # noqa: ARG002
        budget_usd: float | None = None,
        candidate_models: list[str] | None = None,
    ) -> list[ModelRecommendation]:
        """Get model recommendations sorted by success rate.

        Args:
            workflow_id: Unused currently, reserved for workflow-specific routing.
            budget_usd: Optional max cost per call filter.
            candidate_models: Optional whitelist of models to consider.

        Returns:
            List of ModelRecommendation sorted by success_rate DESC.
        """
        self.refresh_stats()

        results: list[ModelRecommendation] = []
        effective_budget = budget_usd or self._max_cost_per_call

        for model_id, stats in self._model_stats.items():
            if candidate_models and model_id not in candidate_models:
                continue

            if stats.total_calls < self._min_samples:
                continue

            rate = stats.success_rate
            if rate < self._min_success_rate:
                continue

            avg_cost = stats.avg_cost_per_call
            if effective_budget is not None and avg_cost > effective_budget:
                continue

            results.append(ModelRecommendation(
                model_id=model_id,
                success_rate=rate,
                total_calls=stats.total_calls,
                avg_cost_per_call=avg_cost,
            ))

        results.sort(key=lambda r: r.success_rate, reverse=True)
        return results

    def best_model(
        self,
        workflow_id: str | None = None,
        fallback: str = "gpt-4o",
    ) -> str:
        """Get the single best model, or fallback."""
        recs = self.recommend(workflow_id=workflow_id)
        if recs:
            return recs[0].model_id
        return fallback

    def get_all_stats(self) -> dict[str, dict[str, Any]]:
        """Return raw stats for all models."""
        self.refresh_stats()
        return {
            model_id: {
                "success_rate": s.success_rate,
                "total_calls": s.total_calls,
                "avg_cost_per_call": s.avg_cost_per_call,
            }
            for model_id, s in self._model_stats.items()
        }


@dataclass
class _ModelStats:
    """Internal model stats accumulator."""

    model_id: str
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_cost: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.successful_calls / self.total_calls

    @property
    def avg_cost_per_call(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.total_cost / self.total_calls
