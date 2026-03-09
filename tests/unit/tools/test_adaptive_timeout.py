"""Tests for AdaptiveTimeoutManager."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_kernel.tools.adaptive_timeout import (
    AdaptiveTimeoutManager,
)


@dataclass
class FakeToolCall:
    capability_name: str
    duration_ms: int


@dataclass
class FakeTrace:
    tool_calls: list[FakeToolCall] = field(default_factory=list)


class FakeTraceStore:
    def __init__(self, traces: list[FakeTrace]):
        self._traces = traces

    def list_traces(self, *, since=None, limit=1000, **kwargs):
        return self._traces


class TestAdaptiveTimeoutManager:
    def _make_traces(
        self,
        capability_name: str,
        durations: list[int],
    ) -> list[FakeTrace]:
        return [
            FakeTrace(tool_calls=[FakeToolCall(capability_name, d)])
            for d in durations
        ]

    def test_p99_computation_from_traces(self):
        # 100 calls with durations 1..100
        durations = list(range(1, 101))
        traces = self._make_traces("tool.a@v1", durations)
        store = FakeTraceStore(traces)

        manager = AdaptiveTimeoutManager(
            trace_store=store,
            buffer_factor=1.0,  # No buffer for exact P99
            min_samples=10,
        )

        stats = manager.get_all_stats()
        assert "tool.a@v1" in stats
        s = stats["tool.a@v1"]
        assert s.total_calls == 100
        assert s.p50_latency_ms == 51.0  # index 50 of [1..100]
        assert s.p99_latency_ms == 100.0  # index 99 of [1..100]
        assert s.max_latency_ms == 100.0

    def test_buffer_factor_applied(self):
        durations = list(range(1, 101))
        traces = self._make_traces("tool.a@v1", durations)
        store = FakeTraceStore(traces)

        manager = AdaptiveTimeoutManager(
            trace_store=store,
            buffer_factor=1.5,
            min_samples=10,
        )

        timeout = manager.get_timeout("tool.a@v1", default_timeout_ms=30000)
        # P99 = 100, buffer = 1.5 → 150 (int(100 * 1.5))
        assert timeout == 150

    def test_fallback_to_default(self):
        store = FakeTraceStore([])
        manager = AdaptiveTimeoutManager(
            trace_store=store,
            min_samples=10,
        )

        timeout = manager.get_timeout("unknown.tool@v1", default_timeout_ms=5000)
        assert timeout == 5000

    def test_min_samples_filter(self):
        # Only 5 samples, threshold is 10
        durations = [100, 200, 300, 400, 500]
        traces = self._make_traces("tool.a@v1", durations)
        store = FakeTraceStore(traces)

        manager = AdaptiveTimeoutManager(
            trace_store=store,
            min_samples=10,
        )

        # Should fall back to default since not enough samples
        timeout = manager.get_timeout("tool.a@v1", default_timeout_ms=30000)
        assert timeout == 30000

    def test_per_capability_stats(self):
        traces_a = self._make_traces("tool.a@v1", list(range(10, 110)))
        traces_b = self._make_traces("tool.b@v1", list(range(50, 150)))
        store = FakeTraceStore(traces_a + traces_b)

        manager = AdaptiveTimeoutManager(
            trace_store=store,
            buffer_factor=1.2,
            min_samples=10,
        )

        stats = manager.get_all_stats()
        assert len(stats) == 2
        assert "tool.a@v1" in stats
        assert "tool.b@v1" in stats
        # Different capabilities have different stats
        assert stats["tool.a@v1"].p50_latency_ms != stats["tool.b@v1"].p50_latency_ms

    def test_no_trace_store(self):
        manager = AdaptiveTimeoutManager(trace_store=None)
        timeout = manager.get_timeout("tool.a@v1", default_timeout_ms=10000)
        assert timeout == 10000

    def test_zero_duration_excluded(self):
        # duration_ms must be > 0 to be counted
        traces = [
            FakeTrace(tool_calls=[FakeToolCall("tool.a@v1", 0)]),
        ] * 20
        store = FakeTraceStore(traces)

        manager = AdaptiveTimeoutManager(
            trace_store=store,
            min_samples=10,
        )

        # All durations are 0, so none counted → fallback
        timeout = manager.get_timeout("tool.a@v1", default_timeout_ms=5000)
        assert timeout == 5000

    def test_cache_ttl_prevents_recompute(self):
        durations = list(range(1, 51))
        traces = self._make_traces("tool.a@v1", durations)
        store = FakeTraceStore(traces)

        manager = AdaptiveTimeoutManager(
            trace_store=store,
            buffer_factor=1.0,
            min_samples=10,
            cache_ttl_seconds=300,
        )

        # First call computes stats
        t1 = manager.get_timeout("tool.a@v1")
        assert t1 != 30000  # Not default

        # Replace store data — but TTL hasn't expired
        store._traces = []
        t2 = manager.get_timeout("tool.a@v1")
        assert t2 == t1  # Still cached
