"""Capability handlers for external agent runner CLIs (Cursor, Claude, etc)."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import structlog
import yaml

from agent_kernel.core.config import get_settings
from agent_kernel.core.errors import ToolExecutionError
from agent_kernel.runners.base import RunnerAdapter
from agent_kernel.runners.claude_code import ClaudeCodeConfig, ClaudeCodeRunner
from agent_kernel.runners.cursor_cli import (
    DEFAULT_BIN_CANDIDATES,
    CursorCliConfig,
    CursorCliRunner,
)
from agent_kernel.runners.types import OutputFormat, RunnerRequest

logger = structlog.get_logger(__name__)


class RunnerRegistry:
    """Simple registry so Tool Broker handlers can route by runner_id."""

    def __init__(self) -> None:
        self._runners: dict[str, RunnerAdapter] = {}

    def register(self, runner: RunnerAdapter) -> None:
        self._runners[runner.runner_id] = runner

    def get(self, runner_id: str) -> RunnerAdapter:
        if runner_id not in self._runners:
            raise KeyError(f"Unknown runner_id: {runner_id}")
        return self._runners[runner_id]

    def list_runners(self) -> list[str]:
        return sorted(self._runners.keys())


_DEFAULT_REGISTRY: RunnerRegistry | None = None


def _load_cursor_config(config_path: Path) -> CursorCliConfig:
    """Load cursor runner config from YAML, if present."""
    if not config_path.exists():
        return CursorCliConfig()

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("cursor_runner_config_load_failed", path=str(config_path), error=str(exc))
        return CursorCliConfig()

    bin_candidates = tuple(data.get("bin_candidates", DEFAULT_BIN_CANDIDATES))
    bin_env_var = data.get("bin_env_var", "CURSOR_AGENT_BIN")
    default_output_format = data.get("default_output_format", "stream-json")
    default_timeout_ms = int(data.get("default_timeout_ms", 300_000))

    return CursorCliConfig(
        bin_env_var=bin_env_var,
        bin_candidates=bin_candidates,
        default_output_format=default_output_format,  # type: ignore[arg-type]
        default_timeout_ms=default_timeout_ms,
    )


def _load_claude_config(config_path: Path) -> ClaudeCodeConfig:
    """Load claude runner config from YAML, if present."""
    if not config_path.exists():
        return ClaudeCodeConfig()

    try:
        with config_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("claude_runner_config_load_failed", path=str(config_path), error=str(exc))  # noqa: E501
        return ClaudeCodeConfig()

    bin_candidates = tuple(data.get("bin_candidates", ("claude",)))
    bin_env_var = data.get("bin_env_var", "CLAUDE_CODE_BIN")
    default_output_format = data.get("default_output_format", "stream-json")
    default_timeout_ms = int(data.get("default_timeout_ms", 300_000))

    # Rate limit and budget settings
    rate_limit_max_retries = int(data.get("rate_limit_max_retries", 3))
    rate_limit_default_wait_seconds = int(data.get("rate_limit_default_wait_seconds", 60))
    default_max_budget_usd = (
        float(data["default_max_budget_usd"]) if "default_max_budget_usd" in data else None
    )

    return ClaudeCodeConfig(
        bin_env_var=bin_env_var,
        bin_candidates=bin_candidates,
        default_output_format=default_output_format,  # type: ignore[arg-type]
        default_timeout_ms=default_timeout_ms,
        rate_limit_max_retries=rate_limit_max_retries,
        rate_limit_default_wait_seconds=rate_limit_default_wait_seconds,
        default_max_budget_usd=default_max_budget_usd,
    )


def get_default_runner_registry() -> RunnerRegistry:
    """Get a shared runner registry configured from configs/runners."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is not None:
        return _DEFAULT_REGISTRY

    settings = get_settings()
    runners_dir = settings.configs_dir / "runners"

    registry = RunnerRegistry()
    if settings.cursor_runner_enabled:
        cursor_config = _load_cursor_config(runners_dir / "cursor.yaml")
        registry.register(CursorCliRunner(config=cursor_config))
    else:
        logger.warning(
            "cursor_runner_disabled",
            reason=settings.cursor_runner_disabled_reason or "disabled by config",
        )

    if settings.claude_runner_enabled:
        claude_config = _load_claude_config(runners_dir / "claude.yaml")
        registry.register(ClaudeCodeRunner(config=claude_config))
    else:
        logger.warning(
            "claude_runner_disabled",
            reason=settings.claude_runner_disabled_reason or "disabled by config",
        )

    _DEFAULT_REGISTRY = registry
    logger.info("runner_registry_initialized", runners=registry.list_runners())
    return registry


def _ensure_runner_enabled(capability_name: str, runner_id: str | None = None) -> None:
    settings = get_settings()
    if runner_id == "claude" and not settings.claude_runner_enabled:
        reason = settings.claude_runner_disabled_reason or "disabled by config"
        msg = f"Claude Code runner disabled: {reason}"
        raise ToolExecutionError(msg, capability_name=capability_name)
    if runner_id == "cursor" and not settings.cursor_runner_enabled:
        reason = settings.cursor_runner_disabled_reason or "disabled by config"
        msg = f"Cursor runner disabled: {reason}"
        raise ToolExecutionError(msg, capability_name=capability_name)


def _build_request(args: dict[str, Any], allow_write: bool, registry: RunnerRegistry) -> RunnerRequest:
    """Build a RunnerRequest using runner defaults where applicable."""
    runner_id = args["runner_id"]
    runner = registry.get(runner_id)

    output_format: OutputFormat | None = args.get("output_format")
    timeout_ms = args.get("timeout_ms")

    # Apply runner-specific defaults
    if isinstance(runner, (CursorCliRunner, ClaudeCodeRunner)):
        if output_format is None:
            output_format = runner.config.default_output_format
        if timeout_ms is None:
            if allow_write:
                timeout_ms = max(runner.config.default_timeout_ms, 600_000)
            else:
                timeout_ms = runner.config.default_timeout_ms

    # Fallback defaults if still missing
    if output_format is None:
        output_format = "stream-json"
    if timeout_ms is None:
        timeout_ms = 600_000 if allow_write else 300_000

    # Budget cap: prefer args, fall back to runner config default
    max_budget_usd = args.get("max_budget_usd")
    if max_budget_usd is None and isinstance(runner, ClaudeCodeRunner):
        max_budget_usd = runner.config.default_max_budget_usd

    return RunnerRequest(
        runner_id=runner_id,
        workspace_path=args["workspace_path"],
        prompt=args["prompt"],
        mode=args.get("mode", "default"),
        model=args.get("model"),
        output_format=output_format,
        resume_thread_id=args.get("resume_thread_id"),
        timeout_ms=int(timeout_ms),
        allow_write=allow_write,
        max_budget_usd=max_budget_usd,
        metadata=args.get("metadata", {}) or {},
    )


# Capability handlers (LocalFunctionAdapter targets)

def agent_runner_run_v1(**args: Any) -> dict[str, Any]:
    """Capability: dev.agent_runner.run@v1 (read-only)."""
    _ensure_runner_enabled("dev.agent_runner.run@v1", args.get("runner_id"))
    registry = get_default_runner_registry()
    req = _build_request(args, allow_write=False, registry=registry)
    runner = registry.get(req.runner_id)
    resp = runner.run(req)
    return asdict(resp)


def agent_runner_apply_v1(**args: Any) -> dict[str, Any]:
    """Capability: dev.agent_runner.apply@v1 (write/apply)."""
    _ensure_runner_enabled("dev.agent_runner.apply@v1", args.get("runner_id"))
    registry = get_default_runner_registry()
    req = _build_request(args, allow_write=True, registry=registry)
    runner = registry.get(req.runner_id)
    resp = runner.run(req)
    return asdict(resp)
