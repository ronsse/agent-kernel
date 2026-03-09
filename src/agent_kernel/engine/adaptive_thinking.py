"""Adaptive Thinking Policy - trace-based optimization of reasoning strategies.

This module extends the ThinkingPolicyController with feedback loops that
use historical trace data to optimize:

1. Starting tier selection based on workflow/task success rates
2. Model routing based on historical model performance
3. Timeout tuning based on P99 latency
4. Escalation threshold adjustment based on escalation patterns

Usage:
    trace_store = SQLiteTraceSink("traces.db")
    adaptive_controller = AdaptiveThinkingPolicyController(
        trace_store=trace_store,
        default_config=STANDARD_THINKING,
    )

    # Create session with trace-informed starting tier
    session = adaptive_controller.create_session(agent_profile, workflow_id="daily_checkin")

    # Get policy with model routing
    policy = adaptive_controller.get_policy(session)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable

import structlog

from agent_kernel.core.schemas.llm import ReasoningEffort
from agent_kernel.core.schemas.thinking import (
    ADAPTIVE_THINKING,
    STANDARD_THINKING,
    EscalationTrigger,
    ThinkingConfig,
    ThinkingTier,
)
from agent_kernel.engine.thinking_policy import (
    ThinkingPolicy,
    ThinkingPolicyController,
    ThinkingSession,
)

if TYPE_CHECKING:
    from agent_kernel.core.schemas import AgentProfile
    from agent_kernel.tracing.trace_store import TraceStore

logger = structlog.get_logger(__name__)


@dataclass
class WorkflowPerformanceStats:
    """Performance statistics for a workflow type."""

    workflow_id: str
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    escalation_count: int = 0
    average_tier: float = 1.0
    model_success_rates: dict[str, float] = field(default_factory=dict)
    p99_latency_ms: float = 1000.0

    @property
    def success_rate(self) -> float:
        """Calculate overall success rate."""
        if self.total_runs == 0:
            return 0.0
        return self.successful_runs / self.total_runs

    @property
    def escalation_rate(self) -> float:
        """Calculate escalation rate."""
        if self.total_runs == 0:
            return 0.0
        return self.escalation_count / self.total_runs


@dataclass
class ModelPerformanceStats:
    """Performance statistics for a model."""

    model_id: str
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    avg_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_calls == 0:
            return 0.0
        return self.successful_calls / self.total_calls

    @property
    def avg_cost_per_call(self) -> float:
        """Calculate average cost per call."""
        if self.total_calls == 0:
            return 0.0
        return self.total_cost_usd / self.total_calls


@dataclass
class AdaptiveThinkingSession(ThinkingSession):
    """Extended thinking session with adaptive features.

    Includes workflow context and performance history for
    making trace-informed decisions.
    """

    # Workflow context
    workflow_id: str | None = None

    # Historical performance (loaded from traces)
    workflow_stats: WorkflowPerformanceStats | None = None
    model_stats: dict[str, ModelPerformanceStats] = field(default_factory=dict)

    # Adaptive adjustments applied
    tier_adjustment: int = 0
    model_override: str | None = None
    timeout_adjustment_ms: int = 0


    def to_dict(self) -> dict[str, Any]:
        """Serialize adaptive session for checkpoint persistence."""
        base = super().to_dict()
        base.update({
            "workflow_id": self.workflow_id,
            "tier_adjustment": self.tier_adjustment,
            "model_override": self.model_override,
            "timeout_adjustment_ms": self.timeout_adjustment_ms,
        })
        return base

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        config: ThinkingConfig,
        approval_callback: Callable[..., bool] | None = None,
    ) -> AdaptiveThinkingSession:
        """Restore an AdaptiveThinkingSession from checkpoint data."""
        session = cls(
            config=config,
            current_tier=data.get("current_tier", 1),
            escalation_count=data.get("escalation_count", 0),
            gate_failures=data.get("gate_failures", []),
            gate_warnings=data.get("gate_warnings", []),
            critic_issues=data.get("critic_issues", []),
            approval_granted=data.get("approval_granted", True),
            pending_approval_callback=approval_callback,
            workflow_id=data.get("workflow_id"),
            tier_adjustment=data.get("tier_adjustment", 0),
            model_override=data.get("model_override"),
            timeout_adjustment_ms=data.get("timeout_adjustment_ms", 0),
        )
        from agent_kernel.engine.thinking_policy import EscalationAttempt
        for a in data.get("attempts", []):
            session.attempts.append(
                EscalationAttempt(
                    tier=a["tier"],
                    trigger=a["trigger"],
                    success=a["success"],
                    details=a.get("details", ""),
                )
            )
        return session


class AdaptiveThinkingPolicyController(ThinkingPolicyController):
    """Thinking policy controller with trace-based optimization.

    Extends the base controller to use historical trace data for:
    - Starting tier selection (based on workflow escalation patterns)
    - Model routing (based on success rates per model)
    - Timeout tuning (based on P99 latency)
    - Escalation threshold adjustment

    The controller maintains a cache of performance stats that
    is refreshed periodically from the trace store.
    """

    def __init__(
        self,
        trace_store: TraceStore | None = None,
        default_config: ThinkingConfig | None = None,
        approval_callback: Callable[[str, ThinkingTier, ThinkingTier], bool] | None = None,
        cache_ttl_seconds: int = 300,  # 5 minutes
        lookback_hours: int = 168,  # 7 days
    ) -> None:
        """Initialize adaptive controller.

        Args:
            trace_store: Store for reading historical traces.
            default_config: Default thinking config.
            approval_callback: Optional callback for approval.
            cache_ttl_seconds: How long to cache performance stats.
            lookback_hours: How far back to look for trace data.
        """
        super().__init__(default_config, approval_callback)

        self._trace_store = trace_store
        self._cache_ttl = cache_ttl_seconds
        self._lookback_hours = lookback_hours

        # Performance stats cache
        self._workflow_stats_cache: dict[str, WorkflowPerformanceStats] = {}
        self._model_stats_cache: dict[str, ModelPerformanceStats] = {}
        self._cache_updated_at: datetime | None = None

        # Thresholds from config (configurable via EscalationConfig)
        esc_config = (default_config or ADAPTIVE_THINKING).escalation
        self._high_escalation_threshold = esc_config.high_escalation_rate_threshold
        self._low_success_threshold = esc_config.low_success_rate_threshold
        self._model_success_threshold = esc_config.model_success_threshold

        logger.info(
            "adaptive_thinking_controller_initialized",
            cache_ttl=cache_ttl_seconds,
            lookback_hours=lookback_hours,
            has_trace_store=trace_store is not None,
        )

    def invalidate_cache(self) -> None:
        """Invalidate the performance stats cache.

        Forces a refresh on the next call to ``_refresh_cache_if_needed``.
        Typically called after a workflow run completes so that subsequent
        runs see fresh trace data immediately.
        """
        self._cache_updated_at = None

    def _refresh_cache_if_needed(self) -> None:
        """Refresh performance stats cache if stale."""
        if self._trace_store is None:
            return

        now = datetime.now(timezone.utc)
        if (
            self._cache_updated_at is not None
            and (now - self._cache_updated_at).total_seconds() < self._cache_ttl
        ):
            return

        logger.debug("refreshing_performance_stats_cache")

        try:
            since = datetime.now(timezone.utc) - timedelta(hours=self._lookback_hours)
            traces = self._trace_store.list_traces(
                since=since,
                limit=1000,
            )
            self._compute_workflow_stats(traces)
            self._compute_model_stats(traces)
            self._cache_updated_at = now
        except Exception as e:
            logger.warning("failed_to_refresh_stats_cache", error=str(e))

    def _compute_workflow_stats(self, traces: list[Any]) -> None:
        """Compute workflow performance stats from traces.

        Args:
            traces: Pre-fetched trace list to aggregate.
        """
        try:
            # Aggregate by workflow_id
            workflow_data: dict[str, dict[str, Any]] = {}

            for trace in traces:
                workflow_id = trace.workflow_id or "unknown"

                if workflow_id not in workflow_data:
                    workflow_data[workflow_id] = {
                        "total": 0,
                        "successful": 0,
                        "failed": 0,
                        "escalations": 0,
                        "tier_sum": 0,
                        "model_counts": {},
                        "model_successes": {},
                    }

                data = workflow_data[workflow_id]
                data["total"] += 1

                # Check success
                from agent_kernel.core.schemas import OutcomeStatus
                if trace.outcome.status == OutcomeStatus.COMPLETED:
                    data["successful"] += 1
                elif trace.outcome.status == OutcomeStatus.FAILED:
                    data["failed"] += 1

                # Check escalation via reasoning metadata
                if trace.reasoning:
                    data["tier_sum"] += trace.reasoning.final_tier
                    if trace.reasoning.escalation_count > 0:
                        data["escalations"] += 1

                    # Track model performance
                    model_id = trace.reasoning.model_id
                    if model_id:
                        data["model_counts"][model_id] = data["model_counts"].get(model_id, 0) + 1
                        if trace.outcome.status == OutcomeStatus.COMPLETED:
                            data["model_successes"][model_id] = data["model_successes"].get(model_id, 0) + 1

            # Convert to WorkflowPerformanceStats
            for workflow_id, data in workflow_data.items():
                model_success_rates = {}
                for model_id, count in data["model_counts"].items():
                    successes = data["model_successes"].get(model_id, 0)
                    model_success_rates[model_id] = successes / count if count > 0 else 0.0

                self._workflow_stats_cache[workflow_id] = WorkflowPerformanceStats(
                    workflow_id=workflow_id,
                    total_runs=data["total"],
                    successful_runs=data["successful"],
                    failed_runs=data["failed"],
                    escalation_count=data["escalations"],
                    average_tier=data["tier_sum"] / data["total"] if data["total"] > 0 else 1.0,
                    model_success_rates=model_success_rates,
                )

            logger.debug(
                "workflow_stats_loaded",
                workflow_count=len(self._workflow_stats_cache),
            )

        except Exception as e:
            logger.warning("failed_to_load_workflow_stats", error=str(e))

    def _compute_model_stats(self, traces: list[Any]) -> None:
        """Compute model performance stats from traces.

        Args:
            traces: Pre-fetched trace list to aggregate.
        """
        try:
            # Aggregate by model
            model_data: dict[str, dict[str, Any]] = {}

            for trace in traces:
                if not trace.llm_calls:
                    continue

                for llm_call in trace.llm_calls:
                    model_id = llm_call.request.model
                    if model_id not in model_data:
                        model_data[model_id] = {
                            "total": 0,
                            "successful": 0,
                            "failed": 0,
                            "tokens": 0,
                            "cost": 0.0,
                            "latencies": [],
                        }

                    data = model_data[model_id]
                    data["total"] += 1

                    # Determine success: response has output text
                    if llm_call.response.output_text is not None:
                        data["successful"] += 1
                    else:
                        data["failed"] += 1

                    data["tokens"] += llm_call.total_tokens or 0
                    data["cost"] += llm_call.estimated_cost_usd or 0.0
                    if llm_call.duration_ms:
                        data["latencies"].append(llm_call.duration_ms)

            # Convert to ModelPerformanceStats
            for model_id, data in model_data.items():
                latencies = sorted(data["latencies"])
                p99_idx = int(len(latencies) * 0.99) if latencies else 0
                p99_latency = latencies[p99_idx] if latencies else 0.0
                avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

                self._model_stats_cache[model_id] = ModelPerformanceStats(
                    model_id=model_id,
                    total_calls=data["total"],
                    successful_calls=data["successful"],
                    failed_calls=data["failed"],
                    total_tokens=data["tokens"],
                    total_cost_usd=data["cost"],
                    avg_latency_ms=avg_latency,
                    p99_latency_ms=p99_latency,
                )

            logger.debug(
                "model_stats_loaded",
                model_count=len(self._model_stats_cache),
            )

        except Exception as e:
            logger.warning("failed_to_load_model_stats", error=str(e))

    def create_session(
        self,
        agent_profile: AgentProfile,
        workflow_id: str | None = None,
    ) -> AdaptiveThinkingSession:
        """Create an adaptive thinking session.

        Args:
            agent_profile: The agent profile.
            workflow_id: Optional workflow ID for context.

        Returns:
            AdaptiveThinkingSession with trace-informed configuration.
        """
        config = agent_profile.thinking_config or self._default_config
        base_tier = config.get_starting_tier()

        # Get workflow stats if available
        workflow_stats = None
        tier_adjustment = 0
        model_override = None

        if workflow_id and workflow_id in self._workflow_stats_cache:
            workflow_stats = self._workflow_stats_cache[workflow_id]

            # Adjust starting tier based on escalation patterns
            if workflow_stats.escalation_rate > self._high_escalation_threshold:
                # This workflow often escalates, start higher
                tier_adjustment = 1
                logger.info(
                    "adaptive_tier_adjustment",
                    workflow_id=workflow_id,
                    escalation_rate=workflow_stats.escalation_rate,
                    adjustment=tier_adjustment,
                )

            # Check if we should override model based on success rates
            if workflow_stats.success_rate < self._low_success_threshold:
                # Find best performing model for this workflow
                best_model = None
                best_rate = 0.0
                for model_id, rate in workflow_stats.model_success_rates.items():
                    if rate > best_rate and rate >= self._model_success_threshold:
                        best_model = model_id
                        best_rate = rate

                if best_model:
                    model_override = best_model
                    logger.info(
                        "adaptive_model_override",
                        workflow_id=workflow_id,
                        success_rate=workflow_stats.success_rate,
                        override_model=best_model,
                        model_success_rate=best_rate,
                    )

        # Apply tier adjustment
        adjusted_tier: ThinkingTier = min(max(base_tier + tier_adjustment, 1), 3)  # type: ignore

        session = AdaptiveThinkingSession(
            config=config,
            current_tier=adjusted_tier,
            workflow_id=workflow_id,
            workflow_stats=workflow_stats,
            model_stats=dict(self._model_stats_cache),
            tier_adjustment=tier_adjustment,
            model_override=model_override,
        )

        logger.debug(
            "adaptive_session_created",
            workflow_id=workflow_id,
            base_tier=base_tier,
            adjusted_tier=adjusted_tier,
            tier_adjustment=tier_adjustment,
            model_override=model_override,
        )

        return session

    def get_policy(self, session: ThinkingSession) -> ThinkingPolicy:
        """Get thinking policy with adaptive adjustments.

        Args:
            session: The thinking session.

        Returns:
            ThinkingPolicy with possible model overrides and timeout adjustments.
        """
        # Get base policy from parent
        policy = super().get_policy(session)

        # Apply adaptive adjustments if this is an adaptive session
        if isinstance(session, AdaptiveThinkingSession):
            # Apply model override if set
            if session.model_override:
                policy.model_id = session.model_override
                policy.escalation_reason = (
                    f"Model override from trace analysis: {session.model_override}"
                )
                logger.debug(
                    "policy_model_override_applied",
                    original_model=policy.model_id,
                    override_model=session.model_override,
                )

            # Adjust timeout based on P99 latency
            if session.model_stats:
                model_stats = session.model_stats.get(policy.model_id)
                if model_stats and model_stats.p99_latency_ms > 0:
                    # Add 20% buffer to P99
                    suggested_timeout = int(model_stats.p99_latency_ms * 1.2)
                    session.timeout_adjustment_ms = suggested_timeout

        return policy

    def get_recommended_timeout(self, session: ThinkingSession) -> int:
        """Get recommended timeout based on historical latency.

        Args:
            session: The thinking session.

        Returns:
            Recommended timeout in milliseconds.
        """
        default_timeout = 30000  # 30 seconds default

        if not isinstance(session, AdaptiveThinkingSession):
            return default_timeout

        if session.timeout_adjustment_ms > 0:
            return session.timeout_adjustment_ms

        # Look up model stats
        policy = self.get_policy(session)
        if policy.model_id in session.model_stats:
            stats = session.model_stats[policy.model_id]
            if stats.p99_latency_ms > 0:
                return int(stats.p99_latency_ms * 1.2)

        return default_timeout

    def get_model_recommendations(
        self,
        workflow_id: str | None = None,
        min_success_rate: float = 0.85,
    ) -> list[tuple[str, float]]:
        """Get recommended models based on success rates.

        Args:
            workflow_id: Optional workflow to filter by.
            min_success_rate: Minimum success rate threshold.

        Returns:
            List of (model_id, success_rate) tuples, sorted by rate descending.
        """
        recommendations = []

        if workflow_id and workflow_id in self._workflow_stats_cache:
            # Use workflow-specific rates
            workflow_stats = self._workflow_stats_cache[workflow_id]
            for model_id, rate in workflow_stats.model_success_rates.items():
                if rate >= min_success_rate:
                    recommendations.append((model_id, rate))
        else:
            # Use global model stats
            for model_id, stats in self._model_stats_cache.items():
                if stats.success_rate >= min_success_rate:
                    recommendations.append((model_id, stats.success_rate))

        # Sort by success rate descending
        recommendations.sort(key=lambda x: x[1], reverse=True)
        return recommendations

    def get_performance_summary(self) -> dict[str, Any]:
        """Get summary of performance stats.

        Returns:
            Dictionary with workflow and model performance summaries.
        """
        return {
            "cache_updated_at": (
                self._cache_updated_at.isoformat() if self._cache_updated_at else None
            ),
            "workflow_count": len(self._workflow_stats_cache),
            "model_count": len(self._model_stats_cache),
            "workflows": {
                wf_id: {
                    "success_rate": stats.success_rate,
                    "escalation_rate": stats.escalation_rate,
                    "total_runs": stats.total_runs,
                    "average_tier": stats.average_tier,
                }
                for wf_id, stats in self._workflow_stats_cache.items()
            },
            "models": {
                model_id: {
                    "success_rate": stats.success_rate,
                    "avg_latency_ms": stats.avg_latency_ms,
                    "p99_latency_ms": stats.p99_latency_ms,
                    "total_calls": stats.total_calls,
                    "total_cost_usd": stats.total_cost_usd,
                }
                for model_id, stats in self._model_stats_cache.items()
            },
        }


# Convenience function for creating adaptive controller
def create_adaptive_controller(
    trace_store: TraceStore | None = None,
    default_config: ThinkingConfig | None = None,
    **kwargs: Any,
) -> AdaptiveThinkingPolicyController:
    """Factory function to create an adaptive thinking controller.

    Args:
        trace_store: Optional trace store for historical data.
        default_config: Optional default thinking config.
        **kwargs: Additional arguments for the controller.

    Returns:
        Configured AdaptiveThinkingPolicyController.
    """
    return AdaptiveThinkingPolicyController(
        trace_store=trace_store,
        default_config=default_config or STANDARD_THINKING,
        **kwargs,
    )
