"""Base interface for tool adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    """Result from tool execution."""

    success: bool
    output: dict[str, Any]
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False


class ToolAdapter(ABC):
    """Abstract base for tool adapters.

    Adapters handle the actual execution of tool capabilities.
    Different adapters handle different execution methods:
    - LocalFunctionAdapter: Python functions
    - HTTPAdapter: REST API calls
    - SubprocessAdapter: CLI commands
    - MCPAdapter: MCP protocol (future)
    """

    @abstractmethod
    async def execute(
        self,
        capability_name: str,
        args: dict[str, Any],
        timeout_ms: int,
    ) -> ToolResult:
        """Execute a tool capability.

        Args:
            capability_name: The capability being executed.
            args: The input arguments (already validated).
            timeout_ms: Maximum execution time.

        Returns:
            ToolResult with success status and output/error.
        """

    @abstractmethod
    def supports(self, adapter_type: str) -> bool:
        """Check if this adapter supports the given type.

        Args:
            adapter_type: The adapter type (e.g., "local", "http").

        Returns:
            True if this adapter handles the type.
        """
