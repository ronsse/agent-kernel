"""Tools subsystem - capability registry and tool broker.

This module provides:
- CapabilityRegistry: Load and manage tool capability definitions
- ToolBroker: Validate, execute, and log tool calls
- Tool adapters: Local function, HTTP, subprocess, MCP
- Retry utilities: Exponential backoff and circuit breaker
- Rate limiting: Per-capability sliding-window rate limits
- Idempotency: Deduplication of tool executions
"""

from agent_kernel.tools.adaptive_timeout import (
    AdaptiveTimeoutManager,
    CapabilityLatencyStats,
)
from agent_kernel.tools.broker import ToolBroker
from agent_kernel.tools.idempotency import IdempotencyResult, IdempotencyStore
from agent_kernel.tools.rate_limiter import (
    RateLimiter,
    RateLimiterRegistry,
    RateLimitResult,
)
from agent_kernel.tools.registry import CapabilityRegistry
from agent_kernel.tools.retry import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitOpenError,
    RetryConfig,
    RetryStats,
    calculate_backoff_delay,
    retry_with_backoff,
    tool_handled,
)

__all__ = [
    "CapabilityRegistry",
    "ToolBroker",
    # Retry utilities
    "RetryConfig",
    "RetryStats",
    "calculate_backoff_delay",
    "retry_with_backoff",
    "tool_handled",
    # Circuit breaker
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CircuitOpenError",
    # Adaptive timeout (v1.2)
    "AdaptiveTimeoutManager",
    "CapabilityLatencyStats",
    # Rate limiting (v1.2)
    "RateLimiter",
    "RateLimiterRegistry",
    "RateLimitResult",
    # Idempotency (v1.2)
    "IdempotencyStore",
    "IdempotencyResult",
]
