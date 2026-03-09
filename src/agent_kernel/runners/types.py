"""Runner request/response contracts for external agent runners."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


OutputFormat = Literal["text", "json", "stream-json"]


@dataclass(frozen=True)
class RunnerRequest:
    """Framework-agnostic request to an external agent runner.

    Keep this schema narrow and stable; add new fields only when compatible
    across all runners.
    """

    runner_id: str
    workspace_path: str
    prompt: str

    mode: Literal["default", "ask", "plan"] = "default"
    model: str | None = None
    output_format: OutputFormat = "stream-json"
    resume_thread_id: str | None = None

    # Execution controls
    timeout_ms: int = 300_000
    # For "apply" capability, the Tool Broker sets allow_write=True.
    allow_write: bool = False
    # Maximum dollar amount to spend on API calls (passed to runner if supported)
    max_budget_usd: float | None = None

    # Opaque correlation metadata (task_id, plan_id, trace_id, etc)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunnerResponse:
    """Response from a runner execution."""

    runner_id: str
    status: Literal["success", "error", "timeout"]
    exit_code: int
    duration_ms: int
    output_format: OutputFormat

    # Best-effort result extraction
    result_text: str | None = None
    result_json: dict[str, Any] | None = None
    thread_id: str | None = None
    model: str | None = None

    # Best-effort tool summaries (files read/written, etc)
    tool_summary: dict[str, Any] = field(default_factory=dict)

    # Raw/parsed data for tracing (should be truncated by implementation)
    events: list[dict[str, Any]] = field(default_factory=list)
    logs: dict[str, Any] = field(default_factory=dict)
