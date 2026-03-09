"""Local function adapter - execute Python functions as tools."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from agent_kernel.tools.adapters.base import ToolAdapter, ToolResult

logger = structlog.get_logger(__name__)

# Type alias for tool functions
ToolFunction = Callable[..., dict[str, Any] | Coroutine[Any, Any, dict[str, Any]]]


class LocalFunctionAdapter(ToolAdapter):
    """Adapter that executes local Python functions.

    Functions are registered by capability name and called
    directly with the provided arguments.
    """

    def __init__(self) -> None:
        """Initialize the adapter with empty function registry."""
        self._functions: dict[str, ToolFunction] = {}

    def register(
        self,
        capability_name: str,
        func: ToolFunction,
    ) -> None:
        """Register a function for a capability.

        Args:
            capability_name: The capability name (e.g., "tasks.create@v1").
            func: The function to execute. Can be sync or async.
                  Must accept kwargs and return dict.
        """
        self._functions[capability_name] = func
        logger.info(
            "local_function_registered",
            capability_name=capability_name,
            function=func.__name__,
        )

    def unregister(self, capability_name: str) -> None:
        """Unregister a function.

        Args:
            capability_name: The capability name to unregister.
        """
        if capability_name in self._functions:
            del self._functions[capability_name]
            logger.debug("local_function_unregistered", capability_name=capability_name)

    def has_function(self, capability_name: str) -> bool:
        """Check if a function is registered for a capability.

        Args:
            capability_name: The capability name.

        Returns:
            True if a function is registered.
        """
        return capability_name in self._functions

    def supports(self, adapter_type: str) -> bool:
        """Check if this adapter supports the given type."""
        return adapter_type in ("local", "local_function")

    async def execute(
        self,
        capability_name: str,
        args: dict[str, Any],
        timeout_ms: int,
    ) -> ToolResult:
        """Execute a registered function.

        Args:
            capability_name: The capability to execute.
            args: The input arguments.
            timeout_ms: Maximum execution time in milliseconds.

        Returns:
            ToolResult with function output or error.
        """
        func = self._functions.get(capability_name)
        if func is None:
            return ToolResult(
                success=False,
                output={},
                error=f"No function registered for {capability_name}",
                error_code="FUNCTION_NOT_REGISTERED",
            )

        timeout_seconds = timeout_ms / 1000.0

        try:
            # Check if function is async
            if asyncio.iscoroutinefunction(func):
                result = await asyncio.wait_for(
                    func(**args),
                    timeout=timeout_seconds,
                )
            else:
                # Run sync function in executor to avoid blocking
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: func(**args)),
                    timeout=timeout_seconds,
                )

            return ToolResult(
                success=True,
                output=result,
            )

        except TimeoutError:
            logger.warning(
                "local_function_timeout",
                capability_name=capability_name,
                timeout_ms=timeout_ms,
            )
            return ToolResult(
                success=False,
                output={},
                error=f"Function execution timed out after {timeout_ms}ms",
                error_code="TIMEOUT",
                retryable=True,
            )

        except Exception as e:
            logger.error(
                "local_function_error",
                capability_name=capability_name,
                error=str(e),
                exc_info=True,
            )
            return ToolResult(
                success=False,
                output={},
                error=str(e),
                error_code="EXECUTION_ERROR",
                retryable=False,
            )
