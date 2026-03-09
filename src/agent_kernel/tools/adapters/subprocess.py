"""Subprocess Tool Adapter - execute tools via shell commands."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass, field
from typing import Any

import structlog

from agent_kernel.tools.adapters.base import ToolAdapter, ToolResult

logger = structlog.get_logger(__name__)


@dataclass
class SubprocessCommand:
    """Configuration for a subprocess command."""

    command: str  # Command template with {arg} placeholders
    shell: bool = False  # Use shell execution
    working_dir: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout_override_ms: int | None = None
    capture_stderr: bool = True
    allowed_exit_codes: list[int] = field(default_factory=lambda: [0])


class SubprocessToolAdapter(ToolAdapter):
    """Adapter that executes tools via subprocess/shell commands.

    Commands are registered by capability name with templates
    that can include argument placeholders.

    Security: By default, only explicitly registered commands
    can be executed. Shell mode is disabled by default.
    """

    def __init__(
        self,
        allowed_commands: list[str] | None = None,
        default_timeout_ms: int = 30000,
        default_working_dir: str | None = None,
    ) -> None:
        """Initialize subprocess adapter.

        Args:
            allowed_commands: Allowlist of command prefixes (security).
            default_timeout_ms: Default timeout for commands.
            default_working_dir: Default working directory.
        """
        self._commands: dict[str, SubprocessCommand] = {}
        self._allowed_commands = set(allowed_commands or [])
        self._default_timeout_ms = default_timeout_ms
        self._default_working_dir = default_working_dir

    def register(
        self,
        capability_name: str,
        command: SubprocessCommand,
    ) -> None:
        """Register a subprocess command for a capability.

        Args:
            capability_name: The capability name.
            command: The command configuration.
        """
        self._commands[capability_name] = command

        # Add command to allowlist
        base_cmd = command.command.split()[0] if command.command else ""
        if base_cmd:
            self._allowed_commands.add(base_cmd)

        logger.info(
            "subprocess_command_registered",
            capability_name=capability_name,
            command=command.command[:50],
        )

    def register_simple(
        self,
        capability_name: str,
        command: str,
        shell: bool = False,
    ) -> None:
        """Register a simple command.

        Args:
            capability_name: The capability name.
            command: The command string (with {arg} placeholders).
            shell: Whether to use shell execution.
        """
        self.register(
            capability_name,
            SubprocessCommand(command=command, shell=shell),
        )

    def unregister(self, capability_name: str) -> None:
        """Unregister a command.

        Args:
            capability_name: The capability to unregister.
        """
        if capability_name in self._commands:
            del self._commands[capability_name]
            logger.debug(
                "subprocess_command_unregistered",
                capability_name=capability_name,
            )

    def has_command(self, capability_name: str) -> bool:
        """Check if a command is registered.

        Args:
            capability_name: The capability name.

        Returns:
            True if command is registered.
        """
        return capability_name in self._commands

    def supports(self, adapter_type: str) -> bool:
        """Check if this adapter supports the given type."""
        return adapter_type == "subprocess"

    def _is_command_allowed(self, command: str) -> bool:
        """Check if a command is in the allowlist.

        Args:
            command: The command string.

        Returns:
            True if allowed.
        """
        if not self._allowed_commands:
            return True  # No restrictions

        # Check if command starts with an allowed prefix
        parts = shlex.split(command) if command else []
        if not parts:
            return False

        base_cmd = parts[0]
        return base_cmd in self._allowed_commands

    def _format_command(
        self,
        template: str,
        args: dict[str, Any],
    ) -> str:
        """Format command template with arguments.

        Args:
            template: Command template with {arg} placeholders.
            args: Arguments to substitute.

        Returns:
            Formatted command string.
        """
        # Escape arguments to prevent injection
        safe_args = {}
        for key, value in args.items():
            if isinstance(value, str):
                # Quote string arguments
                safe_args[key] = shlex.quote(str(value))
            elif isinstance(value, bool):
                safe_args[key] = "true" if value else "false"
            elif isinstance(value, (int, float)):
                safe_args[key] = str(value)
            elif isinstance(value, list):
                # Join list items with spaces, quoting each
                safe_args[key] = " ".join(shlex.quote(str(v)) for v in value)
            else:
                safe_args[key] = shlex.quote(str(value))

        return template.format(**safe_args)

    async def execute(
        self,
        capability_name: str,
        args: dict[str, Any],
        timeout_ms: int,
    ) -> ToolResult:
        """Execute a subprocess command.

        Args:
            capability_name: The capability to execute.
            args: The input arguments.
            timeout_ms: Maximum execution time.

        Returns:
            ToolResult with command output or error.
        """
        cmd_config = self._commands.get(capability_name)
        if cmd_config is None:
            return ToolResult(
                success=False,
                output={},
                error=f"No command registered for {capability_name}",
                error_code="COMMAND_NOT_REGISTERED",
            )

        # Format command with arguments
        try:
            command = self._format_command(cmd_config.command, args)
        except KeyError as e:
            return ToolResult(
                success=False,
                output={},
                error=f"Missing required argument: {e}",
                error_code="MISSING_ARGUMENT",
            )

        # Security check
        if not self._is_command_allowed(command):
            logger.warning(
                "subprocess_command_blocked",
                capability_name=capability_name,
                command=command[:50],
            )
            return ToolResult(
                success=False,
                output={},
                error="Command not in allowlist",
                error_code="COMMAND_NOT_ALLOWED",
            )

        # Determine timeout
        effective_timeout = cmd_config.timeout_override_ms or timeout_ms
        timeout_seconds = effective_timeout / 1000.0

        # Determine working directory
        cwd = cmd_config.working_dir or self._default_working_dir

        # Merge environment
        import os
        env = {**os.environ, **cmd_config.env} if cmd_config.env else None

        logger.debug(
            "subprocess_executing",
            capability_name=capability_name,
            command=command[:100],
            shell=cmd_config.shell,
        )

        try:
            if cmd_config.shell:
                # Shell execution
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=(
                        asyncio.subprocess.PIPE
                        if cmd_config.capture_stderr
                        else asyncio.subprocess.DEVNULL
                    ),
                    cwd=cwd,
                    env=env,
                )
            else:
                # Direct execution (safer)
                cmd_parts = shlex.split(command)
                process = await asyncio.create_subprocess_exec(
                    *cmd_parts,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=(
                        asyncio.subprocess.PIPE
                        if cmd_config.capture_stderr
                        else asyncio.subprocess.DEVNULL
                    ),
                    cwd=cwd,
                    env=env,
                )

            # Wait for completion with timeout
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )

            exit_code = process.returncode or 0
            stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""

            logger.debug(
                "subprocess_completed",
                capability_name=capability_name,
                exit_code=exit_code,
                stdout_len=len(stdout_text),
                stderr_len=len(stderr_text),
            )

            # Check exit code
            if exit_code not in cmd_config.allowed_exit_codes:
                return ToolResult(
                    success=False,
                    output={
                        "stdout": stdout_text,
                        "stderr": stderr_text,
                        "exit_code": exit_code,
                    },
                    error=f"Command exited with code {exit_code}",
                    error_code=f"EXIT_{exit_code}",
                    retryable=False,
                )

            return ToolResult(
                success=True,
                output={
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "exit_code": exit_code,
                },
            )

        except TimeoutError:
            # Kill the process if still running
            if process and process.returncode is None:
                process.kill()
                await process.wait()

            logger.warning(
                "subprocess_timeout",
                capability_name=capability_name,
                timeout_ms=effective_timeout,
            )
            return ToolResult(
                success=False,
                output={},
                error=f"Command timed out after {effective_timeout}ms",
                error_code="TIMEOUT",
                retryable=True,
            )

        except FileNotFoundError as e:
            logger.warning(
                "subprocess_not_found",
                capability_name=capability_name,
                error=str(e),
            )
            return ToolResult(
                success=False,
                output={},
                error=f"Command not found: {e}",
                error_code="COMMAND_NOT_FOUND",
            )

        except PermissionError as e:
            logger.warning(
                "subprocess_permission_denied",
                capability_name=capability_name,
                error=str(e),
            )
            return ToolResult(
                success=False,
                output={},
                error=f"Permission denied: {e}",
                error_code="PERMISSION_DENIED",
            )

        except Exception as e:
            logger.error(
                "subprocess_error",
                capability_name=capability_name,
                error=str(e),
                exc_info=True,
            )
            return ToolResult(
                success=False,
                output={},
                error=str(e),
                error_code="SUBPROCESS_ERROR",
            )
