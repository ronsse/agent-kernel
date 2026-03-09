"""Rolling cost anomaly detection with event emission.

Compares per-trace costs against a rolling window of recent costs
and flags outliers that exceed a configurable std-dev threshold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from agent_kernel.memory.event_log import EventLog
    from agent_kernel.tracing.trace_store import TraceStore

# Lazy-loaded at runtime to avoid circular imports
_EventType: Any = None


def _get_event_type() -> Any:
    """Get EventType enum, importing lazily."""
    global _EventType  # noqa: PLW0603
    if _EventType is None:
        from agent_kernel.memory.event_log import EventType  # noqa: PLC0415
        _EventType = EventType
    return _EventType

logger = structlog.get_logger(__name__)


@dataclass
class AnomalyReport:
    """Report emitted when a cost anomaly is detected."""

    detected_at: datetime
    current_cost: float
    rolling_mean: float
    rolling_std: float
    deviation_factor: float
    trace_id: str
    workflow_id: str | None = None


@dataclass
class _RollingWindow:
    """Internal fixed-size cost window."""

    max_size: int = 50
    values: list[float] = field(default_factory=list)

    def add(self, value: float) -> None:
        self.values.append(value)
        if len(self.values) > self.max_size:
            self.values = self.values[-self.max_size :]

    @property
    def mean(self) -> float:
        if not self.values:
            return 0.0
        return sum(self.values) / len(self.values)

    @property
    def std(self) -> float:
        _min_for_std = 2
        if len(self.values) < _min_for_std:
            return 0.0
        m = self.mean
        variance = sum((v - m) ** 2 for v in self.values) / len(self.values)
        return math.sqrt(variance)


class CostAnomalyDetector:
    """Rolling cost anomaly detection with event emission.

    Compares total cost of each trace against a rolling window
    of recent trace costs and flags statistical outliers.
    """

    def __init__(
        self,
        event_log: EventLog | None = None,
        trace_store: TraceStore | None = None,
        std_dev_threshold: float = 2.0,
        window_size: int = 50,
        lookback_hours: int = 168,
        min_data_points: int = 10,
    ) -> None:
        self._event_log = event_log
        self._trace_store = trace_store
        self._std_dev_threshold = std_dev_threshold
        self._lookback_hours = lookback_hours
        self._min_data_points = min_data_points
        self._window = _RollingWindow(max_size=window_size)
        self._initialized = False

        logger.info(
            "cost_anomaly_detector_initialized",
            std_dev_threshold=std_dev_threshold,
            window_size=window_size,
            min_data_points=min_data_points,
        )

    def refresh_from_traces(self) -> None:
        """Load cost history from trace store into rolling window."""
        if self._trace_store is None:
            return

        try:
            now = datetime.now(UTC)
            since = now - timedelta(hours=self._lookback_hours)
            traces = self._trace_store.list_traces(since=since, limit=1000)

            costs: list[float] = []
            for trace in traces:
                cost = self._compute_trace_cost(trace)
                if cost > 0:
                    costs.append(cost)

            # Reset window and fill with historical data
            self._window = _RollingWindow(max_size=self._window.max_size)
            for c in costs:
                self._window.add(c)

            self._initialized = True
            logger.debug(
                "cost_anomaly_history_loaded",
                data_points=len(costs),
            )
        except Exception as e:
            logger.warning("cost_anomaly_refresh_failed", error=str(e))

    def check(self, trace: Any) -> AnomalyReport | None:
        """Check a trace for cost anomaly.

        Args:
            trace: A DecisionTrace (or compatible object) with
                   tool_calls and/or llm_calls attributes.

        Returns:
            AnomalyReport if anomaly detected, None otherwise.
        """
        if not self._initialized:
            self.refresh_from_traces()

        current_cost = self._compute_trace_cost(trace)
        if current_cost <= 0:
            return None

        mean = self._window.mean
        std = self._window.std

        # Add to window regardless of anomaly
        self._window.add(current_cost)

        # Need minimum data points for meaningful comparison
        if len(self._window.values) < self._min_data_points:
            return None

        # No deviation possible if std is zero
        if std == 0:
            return None

        deviation = (current_cost - mean) / std

        if deviation > self._std_dev_threshold:
            trace_id = getattr(trace, "trace_id", "unknown")
            workflow_id = getattr(trace, "workflow_id", None)

            report = AnomalyReport(
                detected_at=datetime.now(UTC),
                current_cost=current_cost,
                rolling_mean=mean,
                rolling_std=std,
                deviation_factor=deviation,
                trace_id=trace_id,
                workflow_id=workflow_id,
            )

            # Emit event
            if self._event_log is not None:
                self._event_log.emit(
                    _get_event_type().COST_ANOMALY,
                    source="cost_anomaly_detector",
                    entity_id=trace_id,
                    entity_type="trace",
                    data={
                        "current_cost": current_cost,
                        "rolling_mean": mean,
                        "rolling_std": std,
                        "deviation_factor": deviation,
                        "workflow_id": workflow_id,
                    },
                )

            logger.warning(
                "cost_anomaly_detected",
                trace_id=trace_id,
                current_cost=current_cost,
                rolling_mean=mean,
                deviation_factor=deviation,
            )

            return report

        return None

    def get_rolling_stats(self) -> dict[str, Any]:
        """Get current rolling window statistics."""
        return {
            "mean": self._window.mean,
            "std": self._window.std,
            "window_size": len(self._window.values),
            "max_window_size": self._window.max_size,
        }

    def _compute_trace_cost(self, trace: Any) -> float:
        """Compute total cost from a trace's tool_calls and llm_calls."""
        total = 0.0

        # Sum tool call costs
        tool_calls = getattr(trace, "tool_calls", None) or []
        for tc in tool_calls:
            cost = getattr(tc, "cost", None)
            if cost is not None:
                estimated = getattr(cost, "estimated_cost_usd", None)
                if estimated is not None:
                    total += estimated

        # Sum LLM call costs
        llm_calls = getattr(trace, "llm_calls", None) or []
        for lc in llm_calls:
            estimated = getattr(lc, "estimated_cost_usd", None)
            if estimated is not None:
                total += estimated

        return total
