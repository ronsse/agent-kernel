"""Sliding-window rate limiter for tool capabilities.

Enforces per-capability rate limits defined in CapabilityDef.rate_limit.
Uses time-windowed deques for O(1) amortized check/record operations.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

import structlog

from agent_kernel.core.schemas.capability import RateLimit

logger = structlog.get_logger(__name__)

_MINUTE_WINDOW = 60.0
_HOUR_WINDOW = 3600.0


@dataclass
class RateLimitResult:
    """Result of a rate limit check.

    Attributes:
        allowed: Whether the request is allowed.
        wait_seconds: Seconds to wait before retrying (0.0 if allowed).
        current_minute_count: Number of calls in the current minute window.
        current_hour_count: Number of calls in the current hour window.
        limit_minute: Configured per-minute limit.
        limit_hour: Configured per-hour limit.
    """

    allowed: bool
    wait_seconds: float
    current_minute_count: int
    current_hour_count: int
    limit_minute: int
    limit_hour: int


class RateLimiter:
    """Sliding-window rate limiter for a single capability.

    Maintains two deques of monotonic timestamps to track calls
    within minute and hour windows.

    Usage:
        limiter = RateLimiter(config=RateLimit(max_calls_per_minute=10))
        result = limiter.check_and_record()
        if not result.allowed:
            await asyncio.sleep(result.wait_seconds)
    """

    def __init__(self, config: RateLimit) -> None:
        """Initialize rate limiter with the given configuration.

        Args:
            config: Rate limit thresholds for minute and hour windows.
        """
        self._config = config
        self._minute_window: deque[float] = deque()
        self._hour_window: deque[float] = deque()

    def _prune(self, now: float) -> None:
        """Remove expired timestamps from both windows."""
        minute_cutoff = now - _MINUTE_WINDOW
        while self._minute_window and self._minute_window[0] <= minute_cutoff:
            self._minute_window.popleft()

        hour_cutoff = now - _HOUR_WINDOW
        while self._hour_window and self._hour_window[0] <= hour_cutoff:
            self._hour_window.popleft()

    def check(self) -> RateLimitResult:
        """Check whether a new call would be allowed.

        Prunes expired entries and evaluates both minute and hour limits.

        Returns:
            RateLimitResult with allowed status and wait time if denied.
        """
        now = time.monotonic()
        self._prune(now)

        minute_count = len(self._minute_window)
        hour_count = len(self._hour_window)

        # Check minute limit
        if minute_count >= self._config.max_calls_per_minute:
            oldest = self._minute_window[0]
            wait = (oldest + _MINUTE_WINDOW) - now
            return RateLimitResult(
                allowed=False,
                wait_seconds=max(0.0, wait),
                current_minute_count=minute_count,
                current_hour_count=hour_count,
                limit_minute=self._config.max_calls_per_minute,
                limit_hour=self._config.max_calls_per_hour,
            )

        # Check hour limit
        if hour_count >= self._config.max_calls_per_hour:
            oldest = self._hour_window[0]
            wait = (oldest + _HOUR_WINDOW) - now
            return RateLimitResult(
                allowed=False,
                wait_seconds=max(0.0, wait),
                current_minute_count=minute_count,
                current_hour_count=hour_count,
                limit_minute=self._config.max_calls_per_minute,
                limit_hour=self._config.max_calls_per_hour,
            )

        return RateLimitResult(
            allowed=True,
            wait_seconds=0.0,
            current_minute_count=minute_count,
            current_hour_count=hour_count,
            limit_minute=self._config.max_calls_per_minute,
            limit_hour=self._config.max_calls_per_hour,
        )

    def record(self) -> None:
        """Record a call timestamp in both windows."""
        now = time.monotonic()
        self._minute_window.append(now)
        self._hour_window.append(now)

    def check_and_record(self) -> RateLimitResult:
        """Atomically check the limit and record if allowed.

        Returns:
            RateLimitResult. If allowed, the call is already recorded.
        """
        result = self.check()
        if result.allowed:
            self.record()
            # Update counts to reflect the newly recorded call
            result.current_minute_count += 1
            result.current_hour_count += 1
        return result

    def reset(self) -> None:
        """Clear all recorded timestamps."""
        self._minute_window.clear()
        self._hour_window.clear()


class RateLimiterRegistry:
    """Registry for per-capability rate limiters.

    Manages rate limiters for different tool capabilities,
    creating them on demand with provided or default configuration.

    Usage:
        registry = RateLimiterRegistry()
        limiter = registry.get_limiter("tasks.create@v1", config=rate_limit)
        result = limiter.check_and_record()
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._limiters: dict[str, RateLimiter] = {}

    def get_limiter(
        self,
        capability_name: str,
        config: RateLimit | None = None,
    ) -> RateLimiter:
        """Get or create a rate limiter for a capability.

        Args:
            capability_name: The capability identifier.
            config: Rate limit configuration. If the limiter already exists,
                this is ignored. If it does not exist and config is None,
                a default RateLimit() is used.

        Returns:
            RateLimiter instance for the capability.
        """
        if capability_name not in self._limiters:
            effective_config = config if config is not None else RateLimit()
            self._limiters[capability_name] = RateLimiter(effective_config)
            logger.debug(
                "rate_limiter_created",
                capability=capability_name,
                max_per_minute=effective_config.max_calls_per_minute,
                max_per_hour=effective_config.max_calls_per_hour,
            )
        return self._limiters[capability_name]

    def get_all_stats(self) -> dict[str, RateLimitResult]:
        """Get current rate limit status for all registered capabilities.

        Returns:
            Dict mapping capability names to their current RateLimitResult.
        """
        return {name: limiter.check() for name, limiter in self._limiters.items()}

    def reset(self, capability_name: str) -> bool:
        """Reset a specific rate limiter.

        Args:
            capability_name: The capability to reset.

        Returns:
            True if the limiter was found and reset, False otherwise.
        """
        if capability_name in self._limiters:
            self._limiters[capability_name].reset()
            return True
        return False

    def reset_all(self) -> None:
        """Reset all rate limiters."""
        for limiter in self._limiters.values():
            limiter.reset()
