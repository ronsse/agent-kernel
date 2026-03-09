"""Adaptive timeout manager based on trace P99 latency.

Per-capability timeout tuning using historical tool call duration data.
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
class CapabilityLatencyStats:
    """Latency statistics for a single capability."""

    capability_name: str
    total_calls: int = 0
    p50_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    recommended_timeout_ms: int = 30000


class AdaptiveTimeoutManager:
    """Per-capability P99-based timeout tuning from trace data.

    Queries historical ``ToolCallRecord.duration_ms`` from the trace
    store and computes recommended timeouts as ``P99 * buffer_factor``.
    """

    def __init__(
        self,
        trace_store: TraceStore | None = None,
        buffer_factor: float = 1.2,
        min_samples: int = 10,
        cache_ttl_seconds: int = 300,
        lookback_hours: int = 168,
    ) -> None:
        self._trace_store = trace_store
        self._buffer_factor = buffer_factor
        self._min_samples = min_samples
        self._cache_ttl = cache_ttl_seconds
        self._lookback_hours = lookback_hours

        self._stats: dict[str, CapabilityLatencyStats] = {}
        self._cache_updated_at: datetime | None = None

        logger.info(
            "adaptive_timeout_manager_initialized",
            buffer_factor=buffer_factor,
            min_samples=min_samples,
        )

    def refresh_stats(self) -> None:
        """Refresh latency stats from trace store."""
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
            logger.warning("adaptive_timeout_refresh_failed", error=str(e))

    def _compute_stats(self, traces: list[Any]) -> None:
        """Compute per-capability latency stats from traces."""
        latencies: dict[str, list[int]] = {}

        for trace in traces:
            if not trace.tool_calls:
                continue
            for tc in trace.tool_calls:
                name = tc.capability_name
                if tc.duration_ms > 0:
                    latencies.setdefault(name, []).append(tc.duration_ms)

        for name, durations in latencies.items():
            if len(durations) < self._min_samples:
                continue

            durations.sort()
            n = len(durations)
            p50_idx = n // 2
            p99_idx = min(int(n * 0.99), n - 1)

            p50 = float(durations[p50_idx])
            p99 = float(durations[p99_idx])
            max_val = float(durations[-1])
            recommended = int(p99 * self._buffer_factor)

            self._stats[name] = CapabilityLatencyStats(
                capability_name=name,
                total_calls=n,
                p50_latency_ms=p50,
                p99_latency_ms=p99,
                max_latency_ms=max_val,
                recommended_timeout_ms=recommended,
            )

    def get_timeout(
        self,
        capability_name: str,
        default_timeout_ms: int = 30000,
    ) -> int:
        """Get recommended timeout for a capability.

        Returns the P99 * buffer_factor based timeout if enough data
        exists, otherwise returns the default.
        """
        self.refresh_stats()

        stats = self._stats.get(capability_name)
        if stats is not None:
            return stats.recommended_timeout_ms

        return default_timeout_ms

    def get_all_stats(self) -> dict[str, CapabilityLatencyStats]:
        """Get latency stats for all capabilities."""
        self.refresh_stats()
        return dict(self._stats)
