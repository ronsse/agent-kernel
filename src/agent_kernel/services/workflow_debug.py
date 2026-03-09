"""Workflow debug info collector.

Aggregates diagnostic data for a workflow run from multiple stores
into a single object for CLI rendering or JSON export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent_kernel.core.schemas.trace import CallStatus, DecisionTrace
from agent_kernel.core.schemas.workflow import ApprovalRequest, WorkflowRun
from agent_kernel.memory.event_log import Event, EventLog
from agent_kernel.tracing.trace_store import TraceStore
from agent_kernel.workflows.store import WorkflowCheckpoint, WorkflowRunStore


@dataclass
class WorkflowDebugInfo:
    """Consolidated debug information for a workflow run."""

    run: WorkflowRun
    traces: list[DecisionTrace] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    checkpoint: WorkflowCheckpoint | None = None
    pending_approvals: list[ApprovalRequest] = field(default_factory=list)
    tool_call_summary: dict[str, Any] = field(default_factory=dict)
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "run": self.run.model_dump(mode="json"),
            "traces": [t.model_dump(mode="json") for t in self.traces],
            "events": [e.model_dump(mode="json") for e in self.events],
            "checkpoint": (
                {
                    "run_id": self.checkpoint.run_id,
                    "step_index": self.checkpoint.step_index,
                    "step_name": self.checkpoint.step_name,
                }
                if self.checkpoint
                else None
            ),
            "pending_approvals": [
                a.model_dump(mode="json") for a in self.pending_approvals
            ],
            "tool_call_summary": self.tool_call_summary,
            "duration_ms": self.duration_ms,
        }


def collect_debug_info(
    run_id: str,
    workflow_store: WorkflowRunStore,
    trace_store: TraceStore,
    event_log: EventLog,
) -> WorkflowDebugInfo:
    """Collect all diagnostic data for a workflow run.

    Args:
        run_id: The workflow run ID.
        workflow_store: Store for workflow runs and checkpoints.
        trace_store: Store for decision traces.
        event_log: Event log for timeline events.

    Returns:
        WorkflowDebugInfo with all collected data.

    Raises:
        ValueError: If the run_id is not found.
    """
    run = workflow_store.get_run(run_id)
    if run is None:
        msg = f"Workflow run not found: {run_id}"
        raise ValueError(msg)

    # Collect traces matching this run's trace_ids
    traces: list[DecisionTrace] = []
    if run.trace_ids:
        all_traces = trace_store.list_traces(limit=100)
        trace_id_set = set(run.trace_ids)
        traces = [t for t in all_traces if t.trace_id in trace_id_set]

    # Collect events for this run
    events = event_log.get_events(entity_id=run_id, limit=200)

    # Get checkpoint if any
    checkpoint = workflow_store.get_checkpoint(run_id)

    # Get pending approvals
    pending_approvals = workflow_store.get_pending_approvals(run_id)

    # Compute tool call summary from traces
    tool_call_summary = _compute_tool_call_summary(traces)

    # Compute duration
    duration_ms = _compute_duration_ms(run)

    return WorkflowDebugInfo(
        run=run,
        traces=traces,
        events=events,
        checkpoint=checkpoint,
        pending_approvals=pending_approvals,
        tool_call_summary=tool_call_summary,
        duration_ms=duration_ms,
    )


def _compute_tool_call_summary(traces: list[DecisionTrace]) -> dict[str, Any]:
    """Compute aggregate tool call stats from traces."""
    total = 0
    successes = 0
    failures = 0
    total_duration = 0

    for trace in traces:
        for tc in trace.tool_calls:
            total += 1
            total_duration += tc.duration_ms
            if tc.status == CallStatus.SUCCESS:
                successes += 1
            elif tc.status in {CallStatus.ERROR, CallStatus.FAILED}:
                failures += 1

    if total == 0:
        return {}

    return {
        "total": total,
        "successes": successes,
        "failures": failures,
        "success_rate": round(successes / total, 3),
        "avg_duration_ms": round(total_duration / total, 1),
    }


def _compute_duration_ms(run: WorkflowRun) -> int | None:
    """Compute wall-time duration for a run."""
    if run.started_at is None:
        return None

    end = run.ended_at or datetime.now(UTC)
    # Handle naive datetimes (treat as UTC)
    start = run.started_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)

    delta = end - start
    return int(delta.total_seconds() * 1000)
