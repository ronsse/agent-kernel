"""Trace Analysis Adapter - Tool implementations for trace analysis.

Provides capabilities for querying, summarizing, and diagnosing decision traces
to enable self-analysis and debugging workflows.

These functions work with the TraceStore to:
- Query and filter traces by various criteria
- Analyze patterns across multiple traces
- Diagnose errors and performance issues
- Provide insights and recommendations

All operations are read-only and do not require approval.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean, median
from time import perf_counter
from typing import Any

import structlog

from agent_kernel.core.config import get_settings
from agent_kernel.core.schemas.trace import CallStatus, DecisionTrace, OutcomeStatus
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink
from agent_kernel.tracing.trace_store import TraceStore

logger = structlog.get_logger(__name__)

# Module-level singleton for the trace store
_trace_store: TraceStore | None = None


def _get_trace_store() -> TraceStore:
    """Get or create the trace store singleton."""
    global _trace_store
    if _trace_store is None:
        settings = get_settings()
        trace_db = Path(settings.data_dir) / "traces" / "traces.db"
        _trace_store = SQLiteTraceSink(trace_db)
    return _trace_store


# ─────────────────────────────────────────────────────────────────
# Capability: trace_analysis.query@v1
# ─────────────────────────────────────────────────────────────────


def query_traces(
    limit: int = 20,
    offset: int = 0,
    agent_profile_id: str | None = None,
    workflow_id: str | None = None,
    status: str | None = None,
    has_errors: bool | None = None,
    since_hours: int | None = None,
    trace_ids: list[str] | None = None,
    sort_by: str = "timestamp",
) -> dict[str, Any]:
    """Query decision traces with filtering.

    Args:
        limit: Maximum number of traces to return
        offset: Number of traces to skip (pagination)
        agent_profile_id: Filter by agent profile
        workflow_id: Filter by workflow
        status: Filter by outcome status
        has_errors: Filter traces with/without errors
        since_hours: Only traces from last N hours
        trace_ids: Specific trace IDs to retrieve
        sort_by: How to sort results (timestamp, duration, tool_count)

    Returns:
        Dict with traces list and metadata including query_time_ms.
    """
    # Start timing the query
    start_time = perf_counter()

    store = _get_trace_store()

    # Calculate time range if specified
    since = None
    if since_hours:
        since = datetime.utcnow() - timedelta(hours=since_hours)

    # If specific trace IDs provided, fetch them directly
    if trace_ids:
        traces = []
        for trace_id in trace_ids:
            trace = store.get(trace_id)
            if trace:
                traces.append(trace)
        total_count = len(traces)
    else:
        # List traces with filters
        traces = store.list_traces(
            limit=limit,
            offset=offset,
            agent_profile_id=agent_profile_id,
            workflow_id=workflow_id,
            since=since,
        )

        # Apply additional filters
        if status:
            try:
                status_enum = OutcomeStatus(status)
                traces = [t for t in traces if t.outcome.status == status_enum]
            except ValueError:
                pass

        if has_errors is not None:
            traces = [t for t in traces if t.has_errors() == has_errors]

        # Count total before pagination
        total_count = store.count(
            agent_profile_id=agent_profile_id,
            since=since,
        )

    # Sort if requested
    if sort_by == "duration":
        traces.sort(key=lambda t: t.total_duration_ms(), reverse=True)
    elif sort_by == "tool_count":
        traces.sort(key=lambda t: len(t.tool_calls), reverse=True)
    # timestamp is default and already sorted by store

    # Format results
    results = []
    for trace in traces:
        error_count = sum(
            1
            for tc in trace.tool_calls
            if tc.status in {CallStatus.ERROR, CallStatus.FAILED}
        )

        results.append({
            "trace_id": trace.trace_id,
            "run_id": trace.run_id,
            "workflow_id": trace.workflow_id,
            "agent_profile_id": trace.agent_profile_id,
            "intent": trace.intent,
            "timestamp": trace.timestamp.isoformat(),
            "outcome_status": trace.outcome.status.value,
            "tool_call_count": len(trace.tool_calls),
            "error_count": error_count,
            "total_duration_ms": trace.total_duration_ms(),
            "plan_summary": trace.plan.reasoning,
        })

    filter_applied = {
        "agent_profile_id": agent_profile_id,
        "workflow_id": workflow_id,
        "status": status,
        "has_errors": has_errors,
        "since_hours": since_hours,
    }

    # Calculate query time
    end_time = perf_counter()
    query_time_ms = int((end_time - start_time) * 1000)

    return {
        "traces": results,
        "total_count": total_count,
        "filter_applied": filter_applied,
        "query_time_ms": query_time_ms,
    }


# ─────────────────────────────────────────────────────────────────
# Capability: trace_analysis.summarize@v1
# ─────────────────────────────────────────────────────────────────


def _build_time_series(
    traces: list[DecisionTrace],
    since_hours: int,
) -> list[dict[str, Any]]:
    """Build time series data from traces.

    Groups traces by time period and calculates metrics over time.

    Args:
        traces: List of traces to analyze
        since_hours: Time range being analyzed

    Returns:
        List of time periods with metrics
    """
    if not traces:
        return []

    # Determine time bucket size based on range
    # < 6 hours: bucket by hour
    # < 7 days: bucket by day
    # >= 7 days: bucket by week
    if since_hours <= 6:
        bucket_hours = 1
        time_format = "%Y-%m-%d %H:00"
    elif since_hours <= 168:  # 7 days
        bucket_hours = 24
        time_format = "%Y-%m-%d"
    else:
        bucket_hours = 168  # 1 week
        time_format = "%Y-W%W"

    # Group traces by time bucket
    time_buckets: dict[str, list[DecisionTrace]] = defaultdict(list)

    for trace in traces:
        # Round timestamp down to bucket
        bucket_time = trace.timestamp.strftime(time_format)
        time_buckets[bucket_time].append(trace)

    # Calculate metrics for each bucket
    time_series_data = []
    for bucket_time in sorted(time_buckets.keys()):
        bucket_traces = time_buckets[bucket_time]

        # Calculate bucket metrics
        total = len(bucket_traces)
        completed = sum(
            1
            for t in bucket_traces
            if t.outcome.status == OutcomeStatus.COMPLETED
        )
        failed = sum(
            1
            for t in bucket_traces
            if t.outcome.status == OutcomeStatus.FAILED
        )
        errors = sum(1 for t in bucket_traces if t.has_errors())
        durations = [t.total_duration_ms() for t in bucket_traces]

        time_series_data.append({
            "time": bucket_time,
            "total_traces": total,
            "completed": completed,
            "failed": failed,
            "errors": errors,
            "avg_duration_ms": int(mean(durations)) if durations else 0,
            "max_duration_ms": max(durations) if durations else 0,
            "success_rate": completed / total if total > 0 else 0,
        })

    return time_series_data


def summarize_traces(
    trace_ids: list[str] | None = None,
    agent_profile_id: str | None = None,
    workflow_id: str | None = None,
    since_hours: int = 24,
    focus: str = "all",
) -> dict[str, Any]:
    """Summarize patterns and statistics across traces.

    Args:
        trace_ids: Specific traces to summarize
        agent_profile_id: Summarize traces for an agent
        workflow_id: Summarize traces for a workflow
        since_hours: Summarize traces from last N hours
        focus: What aspect to focus on (errors, performance, decisions, tool_usage, all)

    Returns:
        Dict with summary statistics and insights.
    """
    store = _get_trace_store()

    # Get traces
    if trace_ids:
        traces = [store.get(tid) for tid in trace_ids if store.get(tid)]
    else:
        since = datetime.utcnow() - timedelta(hours=since_hours)
        traces = store.list_traces(
            limit=1000,  # Reasonable limit for analysis
            agent_profile_id=agent_profile_id,
            workflow_id=workflow_id,
            since=since,
        )

    if not traces:
        return {
            "summary": {"total_traces": 0, "time_range": f"last {since_hours} hours"},
            "performance": {},
            "outcomes": {},
            "errors": {},
            "tool_usage": {},
            "reasoning": {},
            "insights": ["No traces found for the specified criteria."],
        }

    # Calculate summary stats
    agent_profiles = list({t.agent_profile_id for t in traces})
    workflows = list({t.workflow_id for t in traces if t.workflow_id})

    time_range = f"last {since_hours} hours"
    if traces:
        first = min(t.timestamp for t in traces)
        last = max(t.timestamp for t in traces)
        time_range = f"{first.isoformat()} to {last.isoformat()}"

    # Performance analysis
    durations = [t.total_duration_ms() for t in traces]
    tool_counts = [len(t.tool_calls) for t in traces]

    performance = {
        "avg_duration_ms": mean(durations) if durations else 0,
        "median_duration_ms": median(durations) if durations else 0,
        "max_duration_ms": max(durations) if durations else 0,
        "avg_tool_calls": mean(tool_counts) if tool_counts else 0,
    }

    # Outcome analysis
    outcome_counts: dict[str, int] = defaultdict(int)
    for trace in traces:
        outcome_counts[trace.outcome.status.value] += 1

    success_count = outcome_counts.get("completed", 0)
    success_rate = success_count / len(traces) if traces else 0

    outcomes = {
        "completed": outcome_counts.get("completed", 0),
        "failed": outcome_counts.get("failed", 0),
        "partial": outcome_counts.get("partial", 0),
        "needs_approval": outcome_counts.get("needs_approval", 0),
        "success_rate": success_rate,
    }

    # Error analysis
    all_errors = []
    for trace in traces:
        for tc in trace.tool_calls:
            if tc.status in {CallStatus.ERROR, CallStatus.FAILED} and tc.error:
                all_errors.append(tc.error.message)

    error_counter = Counter(all_errors)
    common_errors = [
        {"error_message": msg, "count": count}
        for msg, count in error_counter.most_common(10)
    ]

    total_traces_with_errors = sum(1 for t in traces if t.has_errors())
    error_rate = total_traces_with_errors / len(traces) if traces else 0

    errors = {
        "total_errors": len(all_errors),
        "error_rate": error_rate,
        "common_errors": common_errors,
    }

    # Tool usage analysis
    capability_usage: Counter[str] = Counter()
    capability_successes: dict[str, int] = defaultdict(int)
    capability_totals: dict[str, int] = defaultdict(int)

    for trace in traces:
        for tc in trace.tool_calls:
            capability_usage[tc.capability_name] += 1
            capability_totals[tc.capability_name] += 1
            if tc.status == CallStatus.SUCCESS:
                capability_successes[tc.capability_name] += 1

    most_used = [
        {"capability": cap, "count": count}
        for cap, count in capability_usage.most_common(10)
    ]

    success_rates = {
        cap: capability_successes[cap] / capability_totals[cap]
        for cap in capability_totals
    }

    tool_usage = {
        "most_used_capabilities": most_used,
        "capability_success_rates": success_rates,
    }

    # Reasoning analysis (if available)
    reasoning_traces = [t for t in traces if t.reasoning]
    reasoning = {}

    if reasoning_traces:
        avg_tier = mean(t.reasoning.final_tier for t in reasoning_traces)
        escalation_rate = (
            sum(1 for t in reasoning_traces if t.reasoning.escalation_count > 0)
            / len(reasoning_traces)
        )
        critic_usage_rate = (
            sum(1 for t in reasoning_traces if t.reasoning.critic_used)
            / len(reasoning_traces)
        )

        reasoning = {
            "avg_tier": avg_tier,
            "escalation_rate": escalation_rate,
            "critic_usage_rate": critic_usage_rate,
        }

    # Generate insights
    insights = []
    if success_rate < 0.7:
        insights.append(
            f"Low success rate ({success_rate:.1%}). Investigate common errors."
        )
    if error_rate > 0.3:
        insights.append(f"High error rate ({error_rate:.1%}). Review error patterns.")
    if performance["avg_duration_ms"] > 30000:
        insights.append(
            "High average duration (>30s). Consider performance optimization."
        )
    if reasoning.get("escalation_rate", 0) > 0.5:
        insights.append(
            "High escalation rate. Consider improving initial tier quality gates."
        )

    if not insights:
        insights.append("System operating within normal parameters.")

    # Build time series data (group by hour/day depending on time range)
    time_series = _build_time_series(traces, since_hours)

    return {
        "summary": {
            "total_traces": len(traces),
            "time_range": time_range,
            "agent_profiles": agent_profiles,
            "workflows": workflows,
        },
        "performance": performance,
        "outcomes": outcomes,
        "errors": errors,
        "tool_usage": tool_usage,
        "reasoning": reasoning,
        "insights": insights,
        "time_series": time_series,
    }


# ─────────────────────────────────────────────────────────────────
# Capability: trace_analysis.diagnose@v1
# ─────────────────────────────────────────────────────────────────


def diagnose_traces(
    trace_id: str | None = None,
    symptom: str | None = None,
    workflow_id: str | None = None,
    agent_profile_id: str | None = None,
    since_hours: int = 24,
    include_context: bool = True,
) -> dict[str, Any]:
    """Diagnose issues and anomalies in traces.

    Args:
        trace_id: Specific trace to diagnose
        symptom: The symptom to investigate
        workflow_id: Diagnose recent failures for a workflow
        agent_profile_id: Diagnose issues for an agent
        since_hours: Look back N hours for pattern analysis
        include_context: Include context details in diagnosis

    Returns:
        Dict with diagnosis, root cause, and recommendations.
    """
    store = _get_trace_store()

    # Get affected traces
    if trace_id:
        trace = store.get(trace_id)
        affected_traces = [trace] if trace else []
    else:
        since = datetime.utcnow() - timedelta(hours=since_hours)
        affected_traces = store.list_traces(
            limit=500,
            agent_profile_id=agent_profile_id,
            workflow_id=workflow_id,
            since=since,
        )

        # Filter by symptom
        if symptom == "failure":
            affected_traces = [
                t for t in affected_traces if t.outcome.status == OutcomeStatus.FAILED
            ]
        elif symptom == "error":
            affected_traces = [t for t in affected_traces if t.has_errors()]
        elif symptom == "slow_performance":
            # Define slow as >30s
            affected_traces = [
                t for t in affected_traces if t.total_duration_ms() > 30000
            ]
        elif symptom == "approval_denied":
            affected_traces = [
                t for t in affected_traces if any(not a.approved for a in t.approvals)
            ]

    if not affected_traces:
        return {
            "diagnosis": {
                "symptom": symptom or "unknown",
                "affected_traces": 0,
                "frequency": "isolated",
            },
            "root_cause": {
                "category": "unknown",
                "description": "No affected traces found.",
                "evidence": [],
            },
            "error_details": [],
            "performance_analysis": {},
            "recommendations": [],
            "related_traces": [],
        }

    # Analyze timing
    timestamps = [t.timestamp for t in affected_traces]
    first_occurrence = min(timestamps)
    last_occurrence = max(timestamps)

    # Determine frequency
    frequency = "isolated"
    if len(affected_traces) > 10:
        frequency = "persistent"
    elif len(affected_traces) > 5:
        frequency = "frequent"
    elif len(affected_traces) > 1:
        frequency = "occasional"

    # Collect error details
    error_details = []
    error_categories: Counter[str] = Counter()

    for trace in affected_traces:
        for tc in trace.tool_calls:
            if tc.status in {CallStatus.ERROR, CallStatus.FAILED} and tc.error:
                error_details.append({
                    "trace_id": trace.trace_id,
                    "timestamp": tc.started_at.isoformat() if tc.started_at else "",
                    "error_code": tc.error.code,
                    "error_message": tc.error.message,
                    "capability": tc.capability_name,
                })
                error_categories[tc.error.code] += 1

    # Determine root cause category
    root_cause_category = "unknown"
    root_cause_desc = "Unable to determine root cause."
    evidence = []

    if error_categories:
        most_common_error = error_categories.most_common(1)[0]
        error_code = most_common_error[0]
        error_count = most_common_error[1]

        evidence.append(
            f"Most common error: {error_code} ({error_count} occurrences)"
        )

        if "timeout" in error_code.lower():
            root_cause_category = "timeout"
            root_cause_desc = "Tool calls are timing out."
        elif "connection" in error_code.lower() or "network" in error_code.lower():
            root_cause_category = "external_service"
            root_cause_desc = "External service connection issues."
        elif "validation" in error_code.lower() or "schema" in error_code.lower():
            root_cause_category = "planning_error"
            root_cause_desc = "Plan validation failures."
        else:
            root_cause_category = "tool_error"
            root_cause_desc = f"Tool execution errors: {error_code}"

    # Performance analysis
    durations = [t.total_duration_ms() for t in affected_traces]
    avg_affected = mean(durations) if durations else 0

    # Get baseline for comparison (successful traces)
    since = datetime.utcnow() - timedelta(hours=since_hours)
    all_traces = store.list_traces(
        limit=500,
        agent_profile_id=agent_profile_id,
        workflow_id=workflow_id,
        since=since,
    )
    successful_traces = [
        t for t in all_traces if t.outcome.status == OutcomeStatus.COMPLETED
    ]
    normal_durations = [t.total_duration_ms() for t in successful_traces]
    avg_normal = mean(normal_durations) if normal_durations else 0

    # Find slowest step
    slowest_capability = ""
    max_avg_duration = 0

    capability_durations: dict[str, list[int]] = defaultdict(list)
    for trace in affected_traces:
        for tc in trace.tool_calls:
            capability_durations[tc.capability_name].append(tc.duration_ms)

    for cap, durs in capability_durations.items():
        avg_dur = mean(durs)
        if avg_dur > max_avg_duration:
            max_avg_duration = avg_dur
            slowest_capability = cap

    performance_analysis = {
        "avg_duration_normal_ms": avg_normal,
        "avg_duration_affected_ms": avg_affected,
        "slowest_step": slowest_capability,
    }

    # Generate recommendations
    recommendations = []

    if root_cause_category == "timeout":
        recommendations.append({
            "priority": "high",
            "action": "Increase timeout values for affected capabilities",
            "rationale": "Tool calls are consistently timing out.",
        })
    elif root_cause_category == "external_service":
        recommendations.append({
            "priority": "high",
            "action": "Check external service health and connectivity",
            "rationale": "Connection failures indicate external service issues.",
        })
    elif root_cause_category == "planning_error":
        recommendations.append({
            "priority": "medium",
            "action": "Review agent profile and capability schemas",
            "rationale": "Plans are failing validation checks.",
        })

    if avg_affected > avg_normal * 2:
        recommendations.append({
            "priority": "medium",
            "action": "Investigate performance regression",
            "rationale": f"Affected traces are {avg_affected/avg_normal:.1f}x slower than normal.",
        })

    if frequency in ["frequent", "persistent"]:
        recommendations.append({
            "priority": "critical",
            "action": "Immediate investigation required",
            "rationale": f"{frequency.capitalize()} issue affecting multiple traces.",
        })

    # Related traces
    related_trace_ids = [t.trace_id for t in affected_traces[:10]]

    return {
        "diagnosis": {
            "symptom": symptom or "general",
            "affected_traces": len(affected_traces),
            "first_occurrence": first_occurrence.isoformat(),
            "last_occurrence": last_occurrence.isoformat(),
            "frequency": frequency,
        },
        "root_cause": {
            "category": root_cause_category,
            "description": root_cause_desc,
            "evidence": evidence,
        },
        "error_details": error_details[:20],  # Limit to 20 most recent
        "performance_analysis": performance_analysis,
        "recommendations": recommendations,
        "related_traces": related_trace_ids,
    }
