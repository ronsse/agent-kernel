"""Skill script tool adapter for executing skill scripts."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.tools.adapters.base import ToolAdapter, ToolResult

logger = structlog.get_logger(__name__)


@dataclass
class SkillScriptCommand:
    """Configuration for a skill script execution."""

    command: list[str]
    working_dir: str | None = None
    timeout_override_ms: int | None = None
    env: dict[str, str] = field(default_factory=dict)


class SkillScriptAdapter(ToolAdapter):
    """Adapter that executes skill scripts with JSON stdin/stdout."""

    def __init__(self, default_timeout_ms: int = 30000) -> None:
        self._commands: dict[str, SkillScriptCommand] = {}
        self._default_timeout_ms = default_timeout_ms

    def register(self, capability_name: str, command: SkillScriptCommand) -> None:
        self._commands[capability_name] = command
        logger.info(
            "skill_script_registered",
            capability_name=capability_name,
            command=" ".join(command.command),
        )

    def supports(self, adapter_type: str) -> bool:
        return adapter_type == "skill_script"

    async def execute(
        self,
        capability_name: str,
        args: dict[str, Any],
        timeout_ms: int,
    ) -> ToolResult:
        cmd_config = self._commands.get(capability_name)
        if cmd_config is None:
            return ToolResult(
                success=False,
                output={},
                error=f"No script registered for {capability_name}",
                error_code="SCRIPT_NOT_REGISTERED",
            )

        timeout = cmd_config.timeout_override_ms or timeout_ms or self._default_timeout_ms
        payload = json.dumps(args, ensure_ascii=True)
        cwd = cmd_config.working_dir

        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd_config.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env={**cmd_config.env} if cmd_config.env else None,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=payload.encode("utf-8")),
                timeout=timeout / 1000.0,
            )

            exit_code = process.returncode or 0
            stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""

            if exit_code != 0:
                return ToolResult(
                    success=False,
                    output={
                        "stdout": stdout_text,
                        "stderr": stderr_text,
                        "exit_code": exit_code,
                    },
                    error=f"Script exited with code {exit_code}",
                    error_code=f"EXIT_{exit_code}",
                )

            output = self._parse_output(stdout_text, stderr_text)
            return ToolResult(success=True, output=output)

        except TimeoutError:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            return ToolResult(
                success=False,
                output={},
                error=f"Script timed out after {timeout}ms",
                error_code="TIMEOUT",
                retryable=True,
            )
        except FileNotFoundError as exc:
            return ToolResult(
                success=False,
                output={},
                error=f"Script not found: {exc}",
                error_code="SCRIPT_NOT_FOUND",
            )
        except PermissionError as exc:
            return ToolResult(
                success=False,
                output={},
                error=f"Permission denied: {exc}",
                error_code="PERMISSION_DENIED",
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                output={},
                error=str(exc),
                error_code="UNEXPECTED_ERROR",
            )

    def _parse_output(self, stdout_text: str, stderr_text: str) -> dict[str, Any]:
        stdout_text = stdout_text.strip()
        if not stdout_text:
            return {"stdout": "", "stderr": stderr_text}

        try:
            parsed = json.loads(stdout_text)
            if isinstance(parsed, dict):
                return parsed
            return {"result": parsed, "stderr": stderr_text}
        except json.JSONDecodeError:
            return {
                "stdout": stdout_text,
                "stderr": stderr_text,
            }
