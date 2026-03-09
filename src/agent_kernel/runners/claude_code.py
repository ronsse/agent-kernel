"""Claude Code CLI runner adapter.

Implements the runner contract for `claude --print` (non-interactive mode).
Parses Claude Code's stream-json event format.

Rate limit handling: when Claude Code hits a subscription rate limit,
the runner waits for the limit to refresh and retries rather than
failing immediately or incurring extra costs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import structlog

from agent_kernel.runners.base import RunnerAdapter
from agent_kernel.runners.types import OutputFormat, RunnerRequest, RunnerResponse

logger = structlog.get_logger(__name__)

DEFAULT_BIN_CANDIDATES = ("claude",)

# Tool names that only read (no filesystem side effects)
READ_ONLY_TOOLS = (
    "Read",
    "Glob",
    "Grep",
    "LS",
    "WebFetch",
    "WebSearch",
    "Task",
    "TodoRead",
)

# Guardrails to prevent trace/log blowups
MAX_EVENTS = 2_000
MAX_STDIO_CHARS = 32_000

# Rate limit retry defaults
DEFAULT_RATE_LIMIT_MAX_RETRIES = 3
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 60
MAX_RATE_LIMIT_WAIT_SECONDS = 600

# Patterns that indicate a rate limit hit (case-insensitive)
_RATE_LIMIT_PATTERNS = (
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"429", re.IGNORECASE),
    re.compile(r"quota.?exceeded", re.IGNORECASE),
    re.compile(r"usage.?limit", re.IGNORECASE),
    re.compile(r"try again.?\w* (\d+)", re.IGNORECASE),
)

# Pattern to extract wait time from error messages like "try again in 60 seconds"
_WAIT_TIME_PATTERN = re.compile(
    r"(?:retry|try again|wait|reset).{0,30}?(\d+)\s*(?:second|sec|s\b|minute|min|m\b)",
    re.IGNORECASE,
)


def _truncate(s: str, max_chars: int) -> tuple[str, bool]:
    if not s:
        return "", False
    if len(s) <= max_chars:
        return s, False
    return s[-max_chars:], True


def _safe_json_loads(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
        if isinstance(obj, dict):
            return obj
        return {"_non_dict_json": obj}
    except Exception:
        return None


def _is_rate_limited(stderr: str, stdout: str, exit_code: int) -> bool:
    """Detect if the run failed due to a rate limit."""
    if exit_code == 0:
        return False
    combined = f"{stderr}\n{stdout}"
    return any(p.search(combined) for p in _RATE_LIMIT_PATTERNS)


def _extract_wait_seconds(stderr: str, stdout: str) -> int:
    """Try to parse a wait duration from error output."""
    combined = f"{stderr}\n{stdout}"
    match = _WAIT_TIME_PATTERN.search(combined)
    if match:
        value = int(match.group(1))
        unit_text = combined[match.end() - 10 : match.end() + 5].lower()
        if "min" in unit_text:
            value *= 60
        return min(value, MAX_RATE_LIMIT_WAIT_SECONDS)
    return DEFAULT_RATE_LIMIT_WAIT_SECONDS


@dataclass(frozen=True)
class ClaudeCodeConfig:
    bin_env_var: str = "CLAUDE_CODE_BIN"
    bin_candidates: tuple[str, ...] = DEFAULT_BIN_CANDIDATES
    default_output_format: OutputFormat = "stream-json"
    default_timeout_ms: int = 300_000
    # Rate limit retry settings
    rate_limit_max_retries: int = DEFAULT_RATE_LIMIT_MAX_RETRIES
    rate_limit_default_wait_seconds: int = DEFAULT_RATE_LIMIT_WAIT_SECONDS
    # Default budget cap per invocation (None = no cap)
    default_max_budget_usd: float | None = None


class ClaudeCodeRunner(RunnerAdapter):
    """Claude Code CLI runner (non-interactive --print mode).

    Uses `claude --print --output-format stream-json` for headless execution.
    For read-only requests, restricts tools via --allowedTools.

    Rate limit handling:
        When Claude Code hits a rate limit (HTTP 429, quota exceeded, etc.),
        the runner waits for the limit to refresh and retries. This avoids
        failing workflows due to transient subscription caps and prevents
        incurring extra costs from immediate retries.
    """

    runner_id = "claude"

    def __init__(self, config: ClaudeCodeConfig | None = None) -> None:
        self.config = config or ClaudeCodeConfig()

    def _resolve_bin(self) -> str:
        env_bin = os.environ.get(self.config.bin_env_var)
        if env_bin:
            return env_bin

        for cand in self.config.bin_candidates:
            resolved = shutil.which(cand)
            if resolved:
                return resolved

        return self.config.bin_candidates[0]

    def _build_cmd(self, request: RunnerRequest) -> list[str]:
        cmd: list[str] = [self._resolve_bin(), "--print"]

        if request.output_format:
            cmd.extend(["--output-format", request.output_format])

        # Restrict to read-only tools when write is not allowed
        if not request.allow_write:
            cmd.extend(["--allowedTools", ",".join(READ_ONLY_TOOLS)])

        # Budget cap: prefer request-level, fall back to config default
        budget = request.max_budget_usd or self.config.default_max_budget_usd
        if budget is not None:
            cmd.extend(["--max-budget-usd", str(budget)])

        cmd.append(request.prompt)
        return cmd

    def run(self, request: RunnerRequest) -> RunnerResponse:
        """Execute Claude Code with rate limit wait-and-retry."""
        max_retries = self.config.rate_limit_max_retries

        for attempt in range(max_retries + 1):
            response = self._run_once(request)

            # Check for rate limit in the response
            stderr = response.logs.get("stderr_tail", "")
            stdout = response.logs.get("stdout_tail", "")
            exit_code = response.exit_code

            if not _is_rate_limited(stderr, stdout, exit_code):
                return response

            # Rate limited — decide whether to wait and retry
            if attempt >= max_retries:
                logger.warning(
                    "rate_limit_retries_exhausted",
                    runner_id=self.runner_id,
                    attempts=attempt + 1,
                )
                return response

            wait_seconds = _extract_wait_seconds(stderr, stdout)
            logger.info(
                "rate_limit_waiting",
                runner_id=self.runner_id,
                attempt=attempt + 1,
                wait_seconds=wait_seconds,
            )
            time.sleep(wait_seconds)

        return response  # Should not reach here, but satisfies type checker

    def _run_once(self, request: RunnerRequest) -> RunnerResponse:
        """Single execution attempt."""
        started = time.time()
        cmd = self._build_cmd(request)
        timeout_s = max(1.0, request.timeout_ms / 1000.0)

        proc = subprocess.Popen(
            cmd,
            cwd=request.workspace_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=(
                {**os.environ, **{k: str(v) for k, v in (request.metadata.get("env") or {}).items()}}
                if isinstance(request.metadata.get("env"), dict)
                else None
            ),
        )

        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
            status: str = "success" if proc.returncode == 0 else "error"
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            status = "timeout"

        ended = time.time()
        duration_ms = int((ended - started) * 1000)

        events: list[dict[str, Any]] = []
        result_text: str | None = None
        thread_id: str | None = None
        model: str | None = None
        total_cost_usd: float | None = None

        files_read: set[str] = set()
        files_written: set[str] = set()
        tool_calls: list[dict[str, Any]] = []

        # Parse stream-json (line-delimited JSON events)
        if request.output_format in ("stream-json", "json"):
            lines = (stdout or "").splitlines()
            for line in lines:
                ev = _safe_json_loads(line)
                if ev is None:
                    continue
                events.append(ev)
                if len(events) >= MAX_EVENTS:
                    break

            for ev in events:
                ev_type = ev.get("type")

                # System init: session_id and model
                if ev_type == "system" and ev.get("subtype") == "init":
                    if not thread_id:
                        thread_id = ev.get("session_id")
                    if not model:
                        model = ev.get("model")

                # Propagate session_id as thread_id
                if not thread_id:
                    thread_id = ev.get("session_id")

                # Result event: final text and cost
                if ev_type == "result":
                    if isinstance(ev.get("result"), str):
                        result_text = ev["result"]
                    if ev.get("total_cost_usd") is not None:
                        total_cost_usd = float(ev["total_cost_usd"])

                # Tool use: track file reads/writes
                if ev_type == "tool_use":
                    tool_name = ev.get("name", "")
                    tool_input = ev.get("input") or {}
                    path = tool_input.get("file_path") or tool_input.get("path")

                    if tool_name == "Read" and isinstance(path, str):
                        files_read.add(path)
                        tool_calls.append({"kind": "read", "tool": tool_name, "path": path})
                    elif tool_name in ("Write", "Edit", "NotebookEdit") and isinstance(path, str):
                        if request.allow_write:
                            files_written.add(path)
                        tool_calls.append({"kind": "write", "tool": tool_name, "path": path})
                    elif tool_name in ("Glob", "Grep", "LS"):
                        tool_calls.append({"kind": "search", "tool": tool_name})
                    else:
                        tool_calls.append({"kind": "other", "tool": tool_name})

        else:
            result_text = (stdout or "").strip()

        events_truncated = len(events) >= MAX_EVENTS
        stdout_tail, stdout_trunc = _truncate(stdout or "", MAX_STDIO_CHARS)
        stderr_tail, stderr_trunc = _truncate(stderr or "", MAX_STDIO_CHARS)

        tool_summary: dict[str, Any] = {
            "files_read": sorted(files_read),
            "files_written": sorted(files_written),
            "tool_calls_count": len(tool_calls),
            "tool_calls_sample": tool_calls[:50],
            "events_truncated": events_truncated,
        }
        if total_cost_usd is not None:
            tool_summary["total_cost_usd"] = total_cost_usd

        return RunnerResponse(
            runner_id=self.runner_id,
            status=status,  # type: ignore[arg-type]
            exit_code=int(proc.returncode or 0),
            duration_ms=duration_ms,
            output_format=request.output_format,  # type: ignore[arg-type]
            result_text=result_text,
            result_json=None,
            thread_id=thread_id,
            model=model,
            tool_summary=tool_summary,
            events=events,
            logs={
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
                "truncated": bool(stdout_trunc or stderr_trunc),
            },
        )
