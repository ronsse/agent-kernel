"""System health checker for all kernel components.

Provides lightweight health probing for each subsystem,
measuring latency and detecting failures.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

import structlog

from agent_kernel.core.schemas.base import utc_now

logger = structlog.get_logger(__name__)


class ComponentStatus(StrEnum):
    """Health status for a single component."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNCONFIGURED = "unconfigured"


@dataclass
class ComponentHealth:
    """Health result for a single component."""

    name: str
    status: ComponentStatus
    latency_ms: float | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SystemHealth:
    """Aggregate health across all components."""

    status: ComponentStatus
    components: list[ComponentHealth]
    checked_at: datetime
    healthy_count: int = 0
    total_count: int = 0


class HealthChecker:
    """Probes all kernel components for health status."""

    def __init__(
        self,
        document_store: Any | None = None,
        vector_store: Any | None = None,
        graph_store: Any | None = None,
        event_log: Any | None = None,
        trace_store: Any | None = None,
        workflow_store: Any | None = None,
        experience_store: Any | None = None,
        llm_service: Any | None = None,
    ) -> None:
        self._document_store = document_store
        self._vector_store = vector_store
        self._graph_store = graph_store
        self._event_log = event_log
        self._trace_store = trace_store
        self._workflow_store = workflow_store
        self._experience_store = experience_store
        self._llm_service = llm_service

    def check_all(self) -> SystemHealth:
        """Run health checks on all configured components.

        Returns:
            SystemHealth with per-component results and overall status.
        """
        checks: list[tuple[str, Callable[[], ComponentHealth]]] = [
            ("document_store", self._check_document_store),
            ("vector_store", self._check_vector_store),
            ("graph_store", self._check_graph_store),
            ("event_log", self._check_event_log),
            ("trace_store", self._check_trace_store),
            ("workflow_store", self._check_workflow_store),
            ("experience_store", self._check_experience_store),
            ("llm_service", self._check_llm_service),
        ]

        components: list[ComponentHealth] = []
        for name, check_fn in checks:
            result = self._timed_check(name, check_fn)
            components.append(result)

        configured = [
            c for c in components
            if c.status != ComponentStatus.UNCONFIGURED
        ]
        healthy_count = sum(
            1 for c in configured if c.status == ComponentStatus.HEALTHY
        )
        total_count = len(configured)

        if total_count == 0:
            overall = ComponentStatus.HEALTHY
        elif any(c.status == ComponentStatus.UNHEALTHY for c in configured):
            overall = ComponentStatus.UNHEALTHY
        elif all(c.status == ComponentStatus.HEALTHY for c in configured):
            overall = ComponentStatus.HEALTHY
        else:
            overall = ComponentStatus.DEGRADED

        health = SystemHealth(
            status=overall,
            components=components,
            checked_at=utc_now(),
            healthy_count=healthy_count,
            total_count=total_count,
        )

        logger.info(
            "health_check_complete",
            status=overall.value,
            healthy=healthy_count,
            total=total_count,
        )
        return health

    def _timed_check(
        self, name: str, check_fn: Callable[[], ComponentHealth]
    ) -> ComponentHealth:
        """Run a check function and measure its latency."""
        start = time.monotonic()
        try:
            result = check_fn()
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            logger.warning(
                "health_check_failed",
                component=name,
                error=str(e),
            )
            return ComponentHealth(
                name=name,
                status=ComponentStatus.UNHEALTHY,
                latency_ms=latency,
                message=str(e),
            )
        else:
            result.latency_ms = (time.monotonic() - start) * 1000
            return result

    def _check_document_store(self) -> ComponentHealth:
        """Check document store health."""
        if self._document_store is None:
            return ComponentHealth(
                name="document_store",
                status=ComponentStatus.UNCONFIGURED,
            )
        count = self._document_store.count()
        return ComponentHealth(
            name="document_store",
            status=ComponentStatus.HEALTHY,
            message=f"{count} documents",
            details={"count": count},
        )

    def _check_vector_store(self) -> ComponentHealth:
        """Check vector store health."""
        if self._vector_store is None:
            return ComponentHealth(
                name="vector_store",
                status=ComponentStatus.UNCONFIGURED,
            )
        count = self._vector_store.count()
        return ComponentHealth(
            name="vector_store",
            status=ComponentStatus.HEALTHY,
            message=f"{count} vectors",
            details={"count": count},
        )

    def _check_graph_store(self) -> ComponentHealth:
        """Check graph store health."""
        if self._graph_store is None:
            return ComponentHealth(
                name="graph_store",
                status=ComponentStatus.UNCONFIGURED,
            )
        node_count = self._graph_store.count_nodes()
        edge_count = self._graph_store.count_edges()
        return ComponentHealth(
            name="graph_store",
            status=ComponentStatus.HEALTHY,
            message=f"{node_count} nodes, {edge_count} edges",
            details={"node_count": node_count, "edge_count": edge_count},
        )

    def _check_event_log(self) -> ComponentHealth:
        """Check event log health."""
        if self._event_log is None:
            return ComponentHealth(
                name="event_log",
                status=ComponentStatus.UNCONFIGURED,
            )
        count = self._event_log.count()
        return ComponentHealth(
            name="event_log",
            status=ComponentStatus.HEALTHY,
            message=f"{count} events",
            details={"count": count},
        )

    def _check_trace_store(self) -> ComponentHealth:
        """Check trace store health."""
        if self._trace_store is None:
            return ComponentHealth(
                name="trace_store",
                status=ComponentStatus.UNCONFIGURED,
            )
        traces = self._trace_store.list_traces(limit=1)
        count = len(traces)
        return ComponentHealth(
            name="trace_store",
            status=ComponentStatus.HEALTHY,
            message=f"accessible ({count} recent)",
            details={"sample_count": count},
        )

    def _check_workflow_store(self) -> ComponentHealth:
        """Check workflow store health."""
        if self._workflow_store is None:
            return ComponentHealth(
                name="workflow_store",
                status=ComponentStatus.UNCONFIGURED,
            )
        runs = self._workflow_store.list_runs(limit=1)
        count = len(runs)
        return ComponentHealth(
            name="workflow_store",
            status=ComponentStatus.HEALTHY,
            message=f"accessible ({count} recent)",
            details={"sample_count": count},
        )

    def _check_experience_store(self) -> ComponentHealth:
        """Check experience store health."""
        if self._experience_store is None:
            return ComponentHealth(
                name="experience_store",
                status=ComponentStatus.UNCONFIGURED,
            )
        cases = self._experience_store.list_cases(limit=1)
        count = len(cases)
        return ComponentHealth(
            name="experience_store",
            status=ComponentStatus.HEALTHY,
            message=f"accessible ({count} recent)",
            details={"sample_count": count},
        )

    def _check_llm_service(self) -> ComponentHealth:
        """Check LLM service health.

        Only inspects configuration -- does not make API calls.
        """
        if self._llm_service is None:
            return ComponentHealth(
                name="llm_service",
                status=ComponentStatus.UNCONFIGURED,
            )
        model = getattr(self._llm_service, "_default_model", None)
        provider = getattr(self._llm_service, "_provider", None)
        details: dict[str, Any] = {}
        if model:
            details["model"] = model
        if provider:
            details["provider"] = provider
        message = f"configured ({model})" if model else "configured"
        return ComponentHealth(
            name="llm_service",
            status=ComponentStatus.HEALTHY,
            message=message,
            details=details,
        )
