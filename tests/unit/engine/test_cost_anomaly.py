"""Tests for CostAnomalyDetector."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from agent_kernel.engine.cost_anomaly import (
    AnomalyReport,
    CostAnomalyDetector,
)


@dataclass
class FakeCost:
    estimated_cost_usd: float = 0.0


@dataclass
class FakeToolCall:
    capability_name: str = "tool.a@v1"
    cost: FakeCost | None = None


@dataclass
class FakeLLMCall:
    estimated_cost_usd: float = 0.0


@dataclass
class FakeTrace:
    trace_id: str = "trace_001"
    workflow_id: str | None = None
    tool_calls: list[FakeToolCall] = field(default_factory=list)
    llm_calls: list[FakeLLMCall] = field(default_factory=list)


class FakeEventLog:
    def __init__(self):
        self.events: list[dict] = []

    def emit(self, event_type, *, source, entity_id, entity_type, data):
        self.events.append({
            "event_type": event_type,
            "source": source,
            "entity_id": entity_id,
            "entity_type": entity_type,
            "data": data,
        })


class FakeTraceStore:
    def __init__(self, traces: list[FakeTrace]):
        self._traces = traces

    def list_traces(self, *, since=None, limit=1000, **kwargs):
        return self._traces


class TestCostAnomalyDetector:
    def _make_trace(self, cost: float, trace_id: str = "trace_001") -> FakeTrace:
        return FakeTrace(
            trace_id=trace_id,
            llm_calls=[FakeLLMCall(estimated_cost_usd=cost)],
        )

    def _seed_detector(
        self,
        detector: CostAnomalyDetector,
        costs: list[float],
    ) -> None:
        """Seed the rolling window with traces."""
        for i, cost in enumerate(costs):
            trace = self._make_trace(cost, trace_id=f"seed_{i}")
            detector.check(trace)

    def test_normal_range_no_anomaly(self):
        detector = CostAnomalyDetector(
            std_dev_threshold=2.0,
            min_data_points=10,
        )

        # Seed with 20 traces all costing ~$1.00
        self._seed_detector(detector, [1.0] * 20)

        # A trace at $1.00 should NOT be anomalous
        result = detector.check(self._make_trace(1.0))
        assert result is None

    def test_anomaly_detected(self):
        detector = CostAnomalyDetector(
            std_dev_threshold=2.0,
            min_data_points=10,
        )

        # Seed with costs that have small variance (std > 0)
        self._seed_detector(detector, [1.0, 1.1, 0.9, 1.0, 1.2] * 4)

        # A trace at $100 is a massive outlier
        result = detector.check(self._make_trace(100.0, trace_id="outlier"))
        assert result is not None
        assert isinstance(result, AnomalyReport)
        assert result.current_cost == 100.0
        assert result.trace_id == "outlier"
        assert result.deviation_factor > 2.0

    def test_event_emitted_on_anomaly(self):
        event_log = FakeEventLog()
        detector = CostAnomalyDetector(
            event_log=event_log,
            std_dev_threshold=2.0,
            min_data_points=10,
        )

        # Seed with costs that have some variance
        self._seed_detector(detector, [1.0, 1.1, 0.9, 1.0, 1.2] * 4)

        # Massive outlier
        result = detector.check(self._make_trace(50.0, trace_id="expensive"))
        assert result is not None
        assert len(event_log.events) == 1
        event = event_log.events[0]
        assert event["entity_id"] == "expensive"
        assert event["data"]["current_cost"] == 50.0

    def test_not_enough_data_points(self):
        detector = CostAnomalyDetector(
            std_dev_threshold=2.0,
            min_data_points=10,
        )

        # Only 5 data points — below min
        self._seed_detector(detector, [1.0] * 5)

        # Even an outlier shouldn't trigger (not enough data)
        result = detector.check(self._make_trace(100.0))
        assert result is None

    def test_rolling_window_size(self):
        detector = CostAnomalyDetector(
            std_dev_threshold=2.0,
            window_size=10,
            min_data_points=5,
        )

        # Seed with 10 traces at $1.00
        self._seed_detector(detector, [1.0] * 10)

        # Then add 10 traces at $10.00 — window should shift
        self._seed_detector(detector, [10.0] * 10)

        # Now $10.00 is the new normal — $11.00 should NOT be anomalous
        result = detector.check(self._make_trace(11.0))
        assert result is None

    def test_zero_cost_ignored(self):
        detector = CostAnomalyDetector(
            std_dev_threshold=2.0,
            min_data_points=10,
        )

        # Zero-cost trace returns no anomaly
        result = detector.check(self._make_trace(0.0))
        assert result is None

    def test_tool_call_costs_included(self):
        detector = CostAnomalyDetector(
            std_dev_threshold=2.0,
            min_data_points=10,
        )

        # Seed with slight variance (std > 0)
        self._seed_detector(detector, [1.0, 1.1, 0.9, 1.0, 1.2] * 4)

        # Trace with expensive tool calls
        trace = FakeTrace(
            trace_id="tool_heavy",
            tool_calls=[
                FakeToolCall(cost=FakeCost(estimated_cost_usd=50.0)),
                FakeToolCall(cost=FakeCost(estimated_cost_usd=50.0)),
            ],
            llm_calls=[],
        )
        result = detector.check(trace)
        assert result is not None
        assert result.current_cost == 100.0

    def test_refresh_from_trace_store(self):
        historical = [
            FakeTrace(
                trace_id=f"hist_{i}",
                llm_calls=[FakeLLMCall(estimated_cost_usd=1.0)],
            )
            for i in range(20)
        ]
        store = FakeTraceStore(historical)

        detector = CostAnomalyDetector(
            trace_store=store,
            std_dev_threshold=2.0,
            min_data_points=10,
        )

        # refresh_from_traces populates window
        detector.refresh_from_traces()

        stats = detector.get_rolling_stats()
        assert stats["window_size"] == 20
        assert stats["mean"] == pytest.approx(1.0)

    def test_get_rolling_stats(self):
        detector = CostAnomalyDetector(
            std_dev_threshold=2.0,
            min_data_points=5,
        )

        self._seed_detector(detector, [1.0, 2.0, 3.0, 4.0, 5.0])

        stats = detector.get_rolling_stats()
        assert stats["window_size"] == 5
        assert stats["mean"] == pytest.approx(3.0)
        assert stats["std"] > 0

    def test_workflow_id_in_report(self):
        detector = CostAnomalyDetector(
            std_dev_threshold=2.0,
            min_data_points=10,
        )

        self._seed_detector(detector, [1.0, 1.1, 0.9, 1.0, 1.2] * 4)

        trace = FakeTrace(
            trace_id="wf_trace",
            workflow_id="daily_checkin",
            llm_calls=[FakeLLMCall(estimated_cost_usd=100.0)],
        )
        result = detector.check(trace)
        assert result is not None
        assert result.workflow_id == "daily_checkin"
