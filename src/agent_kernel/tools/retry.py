"""Retry utilities for tool execution.

Provides exponential backoff retry logic with jitter for transient failures.
Integrates with the ToolBroker to enable automatic retries for retryable errors.

Usage:
    # As a utility function
    result = await retry_with_backoff(
        adapter.execute,
        capability_name, args, timeout_ms,
        config=RetryConfig(max_retries=3),
        is_retryable=lambda r: r.retryable,
    )

    # Or with the decorator (for simpler cases)
    @tool_handled(max_retries=3)
    async def my_tool_function(...):
        ...
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar

import structlog

if TYPE_CHECKING:
    from agent_kernel.tools.adapters.base import ToolResult

logger = structlog.get_logger(__name__)

T = TypeVar("T")


@dataclass
class RetryConfig:
    """Configuration for retry behavior.

    Attributes:
        max_retries: Maximum number of retry attempts (0 = no retries).
        base_delay_ms: Initial delay before first retry in milliseconds.
        max_delay_ms: Maximum delay between retries in milliseconds.
        exponential_base: Base for exponential backoff (delay = base^attempt * base_delay).
        jitter_factor: Random jitter factor (0.0-1.0). 0.2 means ±20% jitter.
        retry_on_timeout: Whether to retry on timeout errors.
    """

    max_retries: int = 3
    base_delay_ms: int = 1000  # 1 second
    max_delay_ms: int = 30000  # 30 seconds
    exponential_base: float = 2.0
    jitter_factor: float = 0.2
    retry_on_timeout: bool = True


@dataclass
class RetryStats:
    """Statistics about retry attempts.

    Attributes:
        total_attempts: Total number of execution attempts (1 = no retries).
        retries: Number of retry attempts (0 = succeeded first try).
        total_delay_ms: Total time spent in backoff delays.
        final_success: Whether the final attempt succeeded.
        errors: List of error messages from failed attempts.
    """

    total_attempts: int = 0
    retries: int = 0
    total_delay_ms: int = 0
    final_success: bool = False
    errors: list[str] = field(default_factory=list)


def calculate_backoff_delay(
    attempt: int,
    config: RetryConfig,
) -> float:
    """Calculate backoff delay for a retry attempt.

    Uses exponential backoff with jitter:
        delay = min(base_delay * (exponential_base ^ attempt), max_delay)
        delay = delay * (1 + random(-jitter, +jitter))

    Args:
        attempt: The retry attempt number (0-indexed).
        config: Retry configuration.

    Returns:
        Delay in seconds.
    """
    # Calculate exponential delay
    delay_ms = config.base_delay_ms * (config.exponential_base ** attempt)

    # Cap at maximum
    delay_ms = min(delay_ms, config.max_delay_ms)

    # Add jitter
    if config.jitter_factor > 0:
        jitter_range = delay_ms * config.jitter_factor
        jitter = random.uniform(-jitter_range, jitter_range)
        delay_ms = max(0, delay_ms + jitter)

    return delay_ms / 1000.0  # Convert to seconds


async def retry_with_backoff(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    config: RetryConfig | None = None,
    is_retryable: Callable[[T], bool] | None = None,
    on_retry: Callable[[int, T, float], None] | None = None,
    **kwargs: Any,
) -> tuple[T, RetryStats]:
    """Execute an async function with retry and exponential backoff.

    Args:
        func: Async function to execute.
        *args: Positional arguments for the function.
        config: Retry configuration. Defaults to RetryConfig().
        is_retryable: Function that takes the result and returns True if
            the operation should be retried. For ToolResult, this checks
            the retryable flag.
        on_retry: Optional callback called before each retry with
            (attempt, result, delay_seconds).
        **kwargs: Keyword arguments for the function.

    Returns:
        Tuple of (result, retry_stats).

    Example:
        result, stats = await retry_with_backoff(
            adapter.execute,
            capability_name, args, timeout_ms,
            config=RetryConfig(max_retries=3),
            is_retryable=lambda r: r.retryable and not r.success,
        )
    """
    config = config or RetryConfig()
    stats = RetryStats()

    # Default is_retryable checks for ToolResult.retryable
    if is_retryable is None:
        def is_retryable(result: Any) -> bool:
            return hasattr(result, "retryable") and result.retryable and not getattr(result, "success", True)

    last_result: T | None = None

    for attempt in range(config.max_retries + 1):
        stats.total_attempts = attempt + 1

        try:
            result = await func(*args, **kwargs)
            last_result = result

            # Check if we should retry
            should_retry = is_retryable(result) if is_retryable else False

            if not should_retry or attempt >= config.max_retries:
                # Either succeeded or exhausted retries
                stats.final_success = not should_retry
                if should_retry and hasattr(result, "error"):
                    stats.errors.append(str(getattr(result, "error", "Unknown error")))
                return result, stats

            # Record error and prepare for retry
            if hasattr(result, "error"):
                stats.errors.append(str(getattr(result, "error", "Unknown error")))

            # Calculate delay
            delay = calculate_backoff_delay(attempt, config)
            stats.retries += 1
            stats.total_delay_ms += int(delay * 1000)

            # Callback and logging
            if on_retry:
                on_retry(attempt + 1, result, delay)

            logger.info(
                "tool_retry_scheduled",
                attempt=attempt + 1,
                max_retries=config.max_retries,
                delay_seconds=round(delay, 2),
                error=getattr(result, "error", None),
            )

            # Wait before retry
            await asyncio.sleep(delay)

        except asyncio.CancelledError:
            # Don't retry on cancellation
            raise

        except Exception as e:
            # Unexpected exception - don't retry
            logger.error(
                "tool_retry_unexpected_error",
                attempt=attempt + 1,
                error=str(e),
            )
            raise

    # Should not reach here, but return last result if we do
    if last_result is not None:
        return last_result, stats

    raise RuntimeError("Retry loop completed without result")


def tool_handled(
    max_retries: int = 3,
    base_delay_ms: int = 1000,
    max_delay_ms: int = 30000,
    exponential_base: float = 2.0,
    jitter_factor: float = 0.2,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator for tool functions with automatic retry on retryable failures.

    This decorator wraps async functions that return ToolResult (or similar)
    with automatic retry logic using exponential backoff.

    Args:
        max_retries: Maximum retry attempts.
        base_delay_ms: Initial delay in milliseconds.
        max_delay_ms: Maximum delay in milliseconds.
        exponential_base: Exponential backoff base.
        jitter_factor: Random jitter factor (0.0-1.0).

    Returns:
        Decorated function that retries on retryable failures.

    Example:
        @tool_handled(max_retries=3)
        async def execute_with_retry(capability, args, timeout):
            return await adapter.execute(capability, args, timeout)
    """
    config = RetryConfig(
        max_retries=max_retries,
        base_delay_ms=base_delay_ms,
        max_delay_ms=max_delay_ms,
        exponential_base=exponential_base,
        jitter_factor=jitter_factor,
    )

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            result, stats = await retry_with_backoff(
                func,
                *args,
                config=config,
                **kwargs,
            )

            if stats.retries > 0:
                logger.info(
                    "tool_handled_completed",
                    total_attempts=stats.total_attempts,
                    retries=stats.retries,
                    total_delay_ms=stats.total_delay_ms,
                    final_success=stats.final_success,
                )

            return result

        return wrapper

    return decorator


class CircuitBreaker:
    """Circuit breaker pattern for preventing cascading failures.

    States:
        CLOSED: Normal operation, requests pass through
        OPEN: Failing, requests are rejected immediately
        HALF_OPEN: Testing recovery, limited requests allowed

    Usage:
        breaker = CircuitBreaker(failure_threshold=5, reset_timeout_ms=30000)

        if breaker.allow_request():
            try:
                result = await execute_tool()
                breaker.record_success()
            except Exception:
                breaker.record_failure()
        else:
            # Circuit is open, fail fast
            raise CircuitOpenError()
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        reset_timeout_ms: int = 30000,
    ) -> None:
        """Initialize circuit breaker.

        Args:
            failure_threshold: Consecutive failures before opening circuit.
            success_threshold: Consecutive successes in half-open to close circuit.
            reset_timeout_ms: Time to wait before transitioning from open to half-open.
        """
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.reset_timeout_ms = reset_timeout_ms

        self._state = self.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: datetime | None = None

    @property
    def state(self) -> str:
        """Get current circuit state, auto-transitioning if needed."""
        if self._state == self.OPEN and self._should_attempt_reset():
            self._state = self.HALF_OPEN
            self._success_count = 0
        return self._state

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self._last_failure_time is None:
            return True
        elapsed_ms = (datetime.now(timezone.utc) - self._last_failure_time).total_seconds() * 1000
        return elapsed_ms >= self.reset_timeout_ms

    def allow_request(self) -> bool:
        """Check if a request should be allowed through.

        Returns:
            True if the request can proceed, False if circuit is open.
        """
        state = self.state  # This may trigger state transition
        return state != self.OPEN

    def record_success(self) -> None:
        """Record a successful execution."""
        if self._state == self.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                logger.info(
                    "circuit_breaker_closed",
                    success_count=self._success_count,
                )
                self._state = self.CLOSED
                self._failure_count = 0
        else:
            self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed execution."""
        self._failure_count += 1
        self._last_failure_time = datetime.now(timezone.utc)

        if self._state == self.HALF_OPEN:
            # Any failure in half-open reopens the circuit
            logger.warning(
                "circuit_breaker_reopened",
                failure_count=self._failure_count,
            )
            self._state = self.OPEN

        elif self._failure_count >= self.failure_threshold:
            logger.warning(
                "circuit_breaker_opened",
                failure_count=self._failure_count,
                threshold=self.failure_threshold,
            )
            self._state = self.OPEN

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._state = self.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None


class CircuitBreakerRegistry:
    """Registry for per-capability circuit breakers.

    Manages circuit breakers for different tool capabilities,
    allowing independent failure isolation.

    Usage:
        registry = CircuitBreakerRegistry()

        # Get or create breaker for a capability
        breaker = registry.get_breaker("tasks.create@v1")

        if breaker.allow_request():
            # Execute tool
            ...
    """

    def __init__(
        self,
        default_failure_threshold: int = 5,
        default_success_threshold: int = 2,
        default_reset_timeout_ms: int = 30000,
    ) -> None:
        """Initialize registry with default breaker configuration."""
        self._breakers: dict[str, CircuitBreaker] = {}
        self._default_failure_threshold = default_failure_threshold
        self._default_success_threshold = default_success_threshold
        self._default_reset_timeout_ms = default_reset_timeout_ms

    def get_breaker(self, capability_name: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a capability.

        Args:
            capability_name: The capability identifier.

        Returns:
            CircuitBreaker instance for the capability.
        """
        if capability_name not in self._breakers:
            self._breakers[capability_name] = CircuitBreaker(
                failure_threshold=self._default_failure_threshold,
                success_threshold=self._default_success_threshold,
                reset_timeout_ms=self._default_reset_timeout_ms,
            )
        return self._breakers[capability_name]

    def get_all_states(self) -> dict[str, str]:
        """Get current state of all circuit breakers.

        Returns:
            Dict mapping capability names to their circuit states.
        """
        return {name: breaker.state for name, breaker in self._breakers.items()}

    def get_open_circuits(self) -> list[str]:
        """Get list of capabilities with open circuits.

        Returns:
            List of capability names where circuit is open.
        """
        return [
            name
            for name, breaker in self._breakers.items()
            if breaker.state == CircuitBreaker.OPEN
        ]

    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        for breaker in self._breakers.values():
            breaker.reset()

    def reset(self, capability_name: str) -> bool:
        """Reset a specific circuit breaker.

        Args:
            capability_name: The capability to reset.

        Returns:
            True if breaker was found and reset, False otherwise.
        """
        if capability_name in self._breakers:
            self._breakers[capability_name].reset()
            return True
        return False


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is open and rejecting requests."""

    def __init__(self, capability_name: str) -> None:
        self.capability_name = capability_name
        super().__init__(f"Circuit breaker open for: {capability_name}")
