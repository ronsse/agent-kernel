"""Tests for SuccessRateRouter."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_kernel.engine.success_rate_router import (
    SuccessRateRouter,
)


@dataclass
class FakeLLMRequest:
    model: str
    provider: str = "openai"


@dataclass
class FakeLLMResponse:
    output_text: str | None = "ok"


@dataclass
class FakeLLMCall:
    request: FakeLLMRequest
    response: FakeLLMResponse
    estimated_cost_usd: float = 0.01
    duration_ms: int = 100
    total_tokens: int = 100


@dataclass
class FakeTrace:
    llm_calls: list[FakeLLMCall] = field(default_factory=list)


class FakeTraceStore:
    def __init__(self, traces: list[FakeTrace]):
        self._traces = traces

    def list_traces(self, *, since=None, limit=1000, **kwargs):
        return self._traces


class TestSuccessRateRouter:
    def _make_traces(
        self,
        model: str,
        total: int,
        successes: int,
        cost_per_call: float = 0.01,
    ) -> list[FakeTrace]:
        traces = []
        for i in range(total):
            output = "ok" if i < successes else None
            traces.append(FakeTrace(llm_calls=[
                FakeLLMCall(
                    request=FakeLLMRequest(model=model),
                    response=FakeLLMResponse(output_text=output),
                    estimated_cost_usd=cost_per_call,
                )
            ]))
        return traces

    def test_sorted_by_success_rate(self):
        traces = (
            self._make_traces("model-a", 30, 27)  # 90%
            + self._make_traces("model-b", 30, 30)  # 100%
        )
        store = FakeTraceStore(traces)
        router = SuccessRateRouter(
            trace_store=store,
            min_success_rate=0.85,
            min_samples=20,
        )

        recs = router.recommend()
        assert len(recs) == 2
        assert recs[0].model_id == "model-b"
        assert recs[0].success_rate == 1.0
        assert recs[1].model_id == "model-a"
        assert recs[1].success_rate == 0.9

    def test_min_samples_filter(self):
        traces = self._make_traces("model-a", 5, 5)  # 100% but only 5 samples
        store = FakeTraceStore(traces)
        router = SuccessRateRouter(
            trace_store=store,
            min_samples=20,
        )

        recs = router.recommend()
        assert len(recs) == 0

    def test_min_success_rate_filter(self):
        traces = self._make_traces("model-a", 30, 15)  # 50%
        store = FakeTraceStore(traces)
        router = SuccessRateRouter(
            trace_store=store,
            min_success_rate=0.85,
            min_samples=20,
        )

        recs = router.recommend()
        assert len(recs) == 0

    def test_budget_filter(self):
        traces = (
            self._make_traces("cheap-model", 30, 28, cost_per_call=0.001)
            + self._make_traces("expensive-model", 30, 30, cost_per_call=1.0)
        )
        store = FakeTraceStore(traces)
        router = SuccessRateRouter(
            trace_store=store,
            min_success_rate=0.85,
            min_samples=20,
        )

        recs = router.recommend(budget_usd=0.01)
        assert len(recs) == 1
        assert recs[0].model_id == "cheap-model"

    def test_fallback_model(self):
        store = FakeTraceStore([])
        router = SuccessRateRouter(trace_store=store)
        assert router.best_model(fallback="gpt-4o-mini") == "gpt-4o-mini"

    def test_best_model_returns_top(self):
        traces = self._make_traces("best-model", 30, 30)
        store = FakeTraceStore(traces)
        router = SuccessRateRouter(
            trace_store=store,
            min_samples=20,
        )
        assert router.best_model() == "best-model"

    def test_candidate_models_filter(self):
        traces = (
            self._make_traces("model-a", 30, 30)
            + self._make_traces("model-b", 30, 30)
        )
        store = FakeTraceStore(traces)
        router = SuccessRateRouter(
            trace_store=store,
            min_samples=20,
        )

        recs = router.recommend(candidate_models=["model-b"])
        assert len(recs) == 1
        assert recs[0].model_id == "model-b"

    def test_no_trace_store(self):
        router = SuccessRateRouter(trace_store=None)
        recs = router.recommend()
        assert recs == []
