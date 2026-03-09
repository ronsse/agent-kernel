"""Cursor CLI runner adapter.

Implements the v1.0.7 runner contract and parses Cursor's output formats.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from agent_kernel.runners.base import RunnerAdapter
from agent_kernel.runners.types import OutputFormat, RunnerRequest, RunnerResponse


DEFAULT_BIN_CANDIDATES = ("agent", "cursor-agent", "cursor_agent")

# Guardrails to prevent trace/log blowups
MAX_EVENTS = 2_000
MAX_STDIO_CHARS = 32_000


def _truncate(s: str, max_chars: int) -> tuple[str, bool]:
    if s is None:
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


def _extract_nested(d: dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


@dataclass(frozen=True)
class CursorCliConfig:
    bin_env_var: str = "CURSOR_AGENT_BIN"
    bin_candidates: tuple[str, ...] = DEFAULT_BIN_CANDIDATES
    default_output_format: OutputFormat = "stream-json"
    default_timeout_ms: int = 300_000


class CursorCliRunner(RunnerAdapter):
    """Cursor Agent CLI runner.

    Notes:
    - Uses print mode (-p/--print) for automation.
    - Uses --force when request.allow_write is True (Cursor applies file edits).
    - Supports --resume <thread id> to continue prior context.
    """

    runner_id = "cursor"

    def __init__(self, config: CursorCliConfig | None = None) -> None:
        self.config = config or CursorCliConfig()

    def _resolve_bin(self) -> str:
        # Explicit override
        env_bin = os.environ.get(self.config.bin_env_var)
        if env_bin:
            return env_bin

        # Search PATH
        for cand in self.config.bin_candidates:
            resolved = shutil.which(cand)
            if resolved:
                return resolved

        # Fall back to first candidate (may still work if caller provides full path)
        return self.config.bin_candidates[0]

    def _build_prompt(self, request: RunnerRequest) -> str:
        parts: list[str] = []

        # Cursor slash commands (best-effort)
        if request.mode in ("ask", "plan"):
            parts.append(f"/{request.mode}")

        if request.model:
            # Cursor supports /model <name> (best-effort)
            parts.append(f"/model {request.model}")

        parts.append(request.prompt)
        return "\n".join(parts)

    def _build_cmd(self, request: RunnerRequest) -> list[str]:
        cmd: list[str] = [self._resolve_bin(), "--print"]

        # Output format
        if request.output_format:
            cmd.extend(["--output-format", request.output_format])

        # Resume prior thread
        if request.resume_thread_id:
            cmd.extend(["--resume", request.resume_thread_id])

        # Apply edits locally (Cursor uses --force for headless apply)
        if request.allow_write:
            cmd.append("--force")

        cmd.append(self._build_prompt(request))
        return cmd

    def run(self, request: RunnerRequest) -> RunnerResponse:
        started = time.time()
        cmd = self._build_cmd(request)

        # NOTE: Cursor CLI has known cases where --print can occasionally hang.
        # We use a hard timeout and kill the process to avoid orphaned runs.
        timeout_s = max(1.0, request.timeout_ms / 1000.0)

        # Use merged stdout+stderr? We keep separate for now; communicate() avoids deadlocks.
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

        # Parse output
        events: list[dict[str, Any]] = []
        result_json: dict[str, Any] | None = None
        result_text: str | None = None
        thread_id: str | None = None
        model: str | None = None

        files_read: set[str] = set()
        files_written: set[str] = set()
        tool_calls: list[dict[str, Any]] = []

        if request.output_format in ("stream-json", "json"):
            # Try line-delimited JSON parsing first (works for stream-json)
            lines = (stdout or "").splitlines()
            for line in lines:
                ev = _safe_json_loads(line)
                if ev is None:
                    continue
                events.append(ev)
                if len(events) >= MAX_EVENTS:
                    break

            # If json format but stdout isn't line-delimited json, attempt full parse
            if request.output_format == "json" and not events:
                try:
                    result_json = json.loads((stdout or "").strip())
                    if isinstance(result_json, dict):
                        events = [result_json]
                    else:
                        result_json = {"_non_dict_json": result_json}
                        events = [result_json]
                except Exception:
                    pass

            # Extract thread id / model from any event
            for ev in events:
                if not thread_id:
                    thread_id = (
                        ev.get("thread_id")
                        or ev.get("session_id")
                        or ev.get("conversation_id")
                        or ev.get("chat_id")
                    )
                if not model:
                    # Cursor sometimes emits model under system.init events
                    model = ev.get("model") or _extract_nested(ev, "model") or _extract_nested(ev, "system.model")
                    if not model:
                        # common path in docs snippet: {type:"system", subtype:"init", model:"..."}
                        if ev.get("type") == "system" and ev.get("subtype") == "init":
                            model = ev.get("model")

                # Result text extraction
                if ev.get("type") == "result":
                    # Common: {"type":"result","result":"..."}
                    if isinstance(ev.get("result"), str):
                        result_text = ev["result"]
                    elif isinstance(ev.get("content"), str):
                        result_text = ev["content"]

                # Accumulate assistant text (best-effort)
                if ev.get("type") == "assistant":
                    # docs show .message.content[0].text
                    delta = _extract_nested(ev, "message.content.0.text")
                    if isinstance(delta, str) and delta:
                        result_text = (result_text or "") + delta

                # Tool call summary (best-effort)
                if ev.get("type") == "tool_call":
                    subtype = ev.get("subtype")
                    tc = ev.get("tool_call") or {}
                    # readToolCall
                    if isinstance(tc, dict) and "readToolCall" in tc:
                        args_path = _extract_nested(tc, "readToolCall.args.path")
                        if isinstance(args_path, str):
                            files_read.add(args_path)
                        tool_calls.append({"kind": "read", "subtype": subtype, "path": args_path})
                    # writeToolCall
                    if isinstance(tc, dict) and "writeToolCall" in tc:
                        args_path = _extract_nested(tc, "writeToolCall.args.path")
                        if isinstance(args_path, str):
                            # If allow_write is False, treat as "proposed"; if True, "written".
                            if request.allow_write and subtype == "completed":
                                files_written.add(args_path)
                        tool_calls.append({"kind": "write", "subtype": subtype, "path": args_path})

            # Best-effort "final json" if single object
            if request.output_format == "json" and events:
                # prefer last object that looks like result
                for ev in reversed(events):
                    if isinstance(ev, dict) and ev.get("type") == "result":
                        result_json = ev
                        break
                if result_json is None:
                    result_json = events[-1]

        else:
            # text output
            result_text = (stdout or "").strip()

        # Truncate event list if needed
        events_truncated = len(events) >= MAX_EVENTS

        # Truncate stdout/stderr tails for trace logging
        stdout_tail, stdout_trunc = _truncate(stdout or "", MAX_STDIO_CHARS)
        stderr_tail, stderr_trunc = _truncate(stderr or "", MAX_STDIO_CHARS)

        tool_summary: dict[str, Any] = {
            "files_read": sorted(files_read),
            "files_written": sorted(files_written),
            "tool_calls_count": len(tool_calls),
            "tool_calls_sample": tool_calls[:50],
            "events_truncated": events_truncated,
        }

        return RunnerResponse(
            runner_id=self.runner_id,
            status=status,  # type: ignore[arg-type]
            exit_code=int(proc.returncode or 0),
            duration_ms=duration_ms,
            output_format=request.output_format,  # type: ignore[arg-type]
            result_text=result_text,
            result_json=result_json,
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
