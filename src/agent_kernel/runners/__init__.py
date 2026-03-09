"""Runner adapters for external agent CLIs (Cursor, Claude, etc.)."""

from agent_kernel.runners.base import RunnerAdapter
from agent_kernel.runners.claude_code import ClaudeCodeConfig, ClaudeCodeRunner
from agent_kernel.runners.cursor_cli import CursorCliConfig, CursorCliRunner
from agent_kernel.runners.types import OutputFormat, RunnerRequest, RunnerResponse

__all__ = [
    "RunnerAdapter",
    "CursorCliRunner",
    "CursorCliConfig",
    "ClaudeCodeRunner",
    "ClaudeCodeConfig",
    "RunnerRequest",
    "RunnerResponse",
    "OutputFormat",
]
