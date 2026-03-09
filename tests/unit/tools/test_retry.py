"""Tests for tool retry utilities."""

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


class TestRetryConfig:
    """Tests for RetryConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay_ms == 1000
        assert config.max_delay_ms == 30000
        assert config.exponential_base == 2.0
        assert config.jitter_factor == 0.2
        assert config.retry_on_timeout is True

    def test_custom_values(self):
        """Test custom configuration values."""
        config = RetryConfig(
            max_retries=5,
            base_delay_ms=500,
            max_delay_ms=10000,
            exponential_base=1.5,
            jitter_factor=0.1,
        )
        assert config.max_retries == 5
        assert config.base_delay_ms == 500


class TestCalculateBackoffDelay:
    """Tests for backoff delay calculation."""

    def test_exponential_growth(self):
        """Test that delay grows exponentially."""
        config = RetryConfig(base_delay_ms=1000, exponential_base=2.0, jitter_factor=0.0)

        delay_0 = calculate_backoff_delay(0, config)
        delay_1 = calculate_backoff_delay(1, config)
        delay_2 = calculate_backoff_delay(2, config)

        assert delay_0 == 1.0  # 1000ms = 1s
        assert delay_1 == 2.0  # 2000ms = 2s
        assert delay_2 == 4.0  # 4000ms = 4s

    def test_max_delay_cap(self):
        """Test that delay is capped at max_delay_ms."""
        config = RetryConfig(
            base_delay_ms=1000,
            max_delay_ms=5000,
            exponential_base=10.0,
            jitter_factor=0.0,
        )

        delay = calculate_backoff_delay(3, config)  # Would be 10^3 * 1000 = 1000000

        assert delay == 5.0  # Capped at 5000ms = 5s

    def test_jitter_adds_randomness(self):
        """Test that jitter adds randomness to delay."""
        config = RetryConfig(base_delay_ms=1000, jitter_factor=0.5)

        delays = [calculate_backoff_delay(0, config) for _ in range(100)]

        # With 50% jitter, delays should vary between 0.5s and 1.5s
        assert min(delays) < 1.0
        assert max(delays) > 1.0
        # All delays should be positive
        assert all(d > 0 for d in delays)

    def test_zero_jitter(self):
        """Test that zero jitter gives consistent delay."""
        config = RetryConfig(base_delay_ms=1000, jitter_factor=0.0)

        delays = [calculate_backoff_delay(0, config) for _ in range(10)]

        assert all(d == 1.0 for d in delays)


class TestRetryWithBackoff:
    """Tests for retry_with_backoff function."""

    @dataclass
    class MockResult:
        """Mock result for testing."""

        success: bool
        retryable: bool = False
        error: str | None = None

    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        """Test successful execution without retry."""
        mock_func = AsyncMock(return_value=self.MockResult(success=True))
        config = RetryConfig(max_retries=3)

        result, stats = await retry_with_backoff(
            mock_func,
            "arg1",
            "arg2",
            config=config,
        )

        assert result.success is True
        assert stats.total_attempts == 1
        assert stats.retries == 0
        assert stats.final_success is True
        mock_func.assert_called_once_with("arg1", "arg2")

    @pytest.mark.asyncio
    async def test_retry_on_retryable_failure(self):
        """Test retry on retryable failure."""
        # First call fails (retryable), second succeeds
        mock_func = AsyncMock(
            side_effect=[
                self.MockResult(success=False, retryable=True, error="Timeout"),
                self.MockResult(success=True),
            ]
        )
        config = RetryConfig(max_retries=3, base_delay_ms=10, jitter_factor=0.0)

        result, stats = await retry_with_backoff(
            mock_func,
            config=config,
        )

        assert result.success is True
        assert stats.total_attempts == 2
        assert stats.retries == 1
        assert stats.final_success is True
        assert mock_func.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_failure(self):
        """Test no retry on non-retryable failure."""
        mock_func = AsyncMock(
            return_value=self.MockResult(success=False, retryable=False, error="Bad input")
        )
        config = RetryConfig(max_retries=3)

        result, stats = await retry_with_backoff(
            mock_func,
            config=config,
        )

        assert result.success is False
        assert stats.total_attempts == 1
        assert stats.retries == 0
        assert stats.final_success is True  # Not retryable, so "succeeded" at determining outcome
        mock_func.assert_called_once()

    @pytest.mark.asyncio
    async def test_exhaust_retries(self):
        """Test exhausting all retries."""
        mock_func = AsyncMock(
            return_value=self.MockResult(success=False, retryable=True, error="Keep failing")
        )
        config = RetryConfig(max_retries=2, base_delay_ms=10, jitter_factor=0.0)

        result, stats = await retry_with_backoff(
            mock_func,
            config=config,
        )

        assert result.success is False
        assert stats.total_attempts == 3  # Initial + 2 retries
        assert stats.retries == 2
        assert stats.final_success is False
        assert mock_func.call_count == 3

    @pytest.mark.asyncio
    async def test_custom_is_retryable(self):
        """Test custom is_retryable function."""

        @dataclass
        class CustomResult:
            status_code: int

        mock_func = AsyncMock(
            side_effect=[
                CustomResult(status_code=500),  # Retryable
                CustomResult(status_code=200),  # Success
            ]
        )
        config = RetryConfig(max_retries=3, base_delay_ms=10, jitter_factor=0.0)

        result, stats = await retry_with_backoff(
            mock_func,
            config=config,
            is_retryable=lambda r: r.status_code >= 500,
        )

        assert result.status_code == 200
        assert stats.total_attempts == 2
        assert stats.retries == 1

    @pytest.mark.asyncio
    async def test_on_retry_callback(self):
        """Test on_retry callback is called."""
        mock_func = AsyncMock(
            side_effect=[
                self.MockResult(success=False, retryable=True, error="Fail 1"),
                self.MockResult(success=True),
            ]
        )
        config = RetryConfig(max_retries=3, base_delay_ms=10, jitter_factor=0.0)

        callback_calls = []

        def on_retry(attempt, result, delay):
            callback_calls.append((attempt, result.error, delay))

        await retry_with_backoff(
            mock_func,
            config=config,
            on_retry=on_retry,
        )

        assert len(callback_calls) == 1
        assert callback_calls[0][0] == 1  # Attempt number
        assert callback_calls[0][1] == "Fail 1"  # Error from result

    @pytest.mark.asyncio
    async def test_zero_retries(self):
        """Test with max_retries=0 (no retries)."""
        mock_func = AsyncMock(
            return_value=self.MockResult(success=False, retryable=True, error="Fail")
        )
        config = RetryConfig(max_retries=0)

        result, stats = await retry_with_backoff(
            mock_func,
            config=config,
        )

        assert result.success is False
        assert stats.total_attempts == 1
        assert stats.retries == 0
        mock_func.assert_called_once()


class TestToolHandledDecorator:
    """Tests for @tool_handled decorator."""

    @dataclass
    class MockResult:
        """Mock result for testing."""

        success: bool
        retryable: bool = False
        error: str | None = None

    @pytest.mark.asyncio
    async def test_decorator_basic(self):
        """Test basic decorator functionality."""

        @tool_handled(max_retries=2, base_delay_ms=10)
        async def my_tool():
            return self.MockResult(success=True)

        result = await my_tool()

        assert result.success is True

    @pytest.mark.asyncio
    async def test_decorator_with_retries(self):
        """Test decorator handles retries."""
        call_count = 0

        @tool_handled(max_retries=3, base_delay_ms=10, jitter_factor=0.0)
        async def failing_then_success():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return self.MockResult(success=False, retryable=True, error="Fail")
            return self.MockResult(success=True)

        result = await failing_then_success()

        assert result.success is True
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_decorator_preserves_function_metadata(self):
        """Test decorator preserves function name and docstring."""

        @tool_handled()
        async def documented_function():
            """This is a docstring."""
            pass

        assert documented_function.__name__ == "documented_function"
        assert "docstring" in documented_function.__doc__


class TestCircuitBreaker:
    """Tests for CircuitBreaker."""

    def test_initial_state_closed(self):
        """Test circuit starts in closed state."""
        breaker = CircuitBreaker()
        assert breaker.state == CircuitBreaker.CLOSED

    def test_allow_request_when_closed(self):
        """Test requests allowed when closed."""
        breaker = CircuitBreaker()
        assert breaker.allow_request() is True

    def test_opens_after_threshold_failures(self):
        """Test circuit opens after failure threshold."""
        breaker = CircuitBreaker(failure_threshold=3)

        breaker.record_failure()
        assert breaker.state == CircuitBreaker.CLOSED

        breaker.record_failure()
        assert breaker.state == CircuitBreaker.CLOSED

        breaker.record_failure()
        assert breaker.state == CircuitBreaker.OPEN

    def test_blocks_requests_when_open(self):
        """Test requests blocked when open."""
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.record_failure()

        assert breaker.state == CircuitBreaker.OPEN
        assert breaker.allow_request() is False

    def test_success_resets_failure_count(self):
        """Test success resets failure counter."""
        breaker = CircuitBreaker(failure_threshold=3)

        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()

        # Failure count should be reset
        breaker.record_failure()
        assert breaker.state == CircuitBreaker.CLOSED

    def test_transitions_to_half_open(self):
        """Test circuit transitions to half-open after timeout."""
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout_ms=50)

        breaker.record_failure()
        assert breaker.state == CircuitBreaker.OPEN

        # Wait for reset timeout
        import time

        time.sleep(0.1)

        # Should transition to half-open
        assert breaker.state == CircuitBreaker.HALF_OPEN

    def test_half_open_closes_on_success(self):
        """Test half-open closes after success threshold."""
        breaker = CircuitBreaker(
            failure_threshold=1, success_threshold=2, reset_timeout_ms=10
        )

        breaker.record_failure()
        import time

        time.sleep(0.02)

        assert breaker.state == CircuitBreaker.HALF_OPEN

        breaker.record_success()
        assert breaker.state == CircuitBreaker.HALF_OPEN  # Need 2 successes

        breaker.record_success()
        assert breaker.state == CircuitBreaker.CLOSED

    def test_half_open_reopens_on_failure(self):
        """Test half-open reopens on failure."""
        breaker = CircuitBreaker(failure_threshold=1, reset_timeout_ms=10)

        breaker.record_failure()
        import time

        time.sleep(0.02)

        assert breaker.state == CircuitBreaker.HALF_OPEN

        breaker.record_failure()
        assert breaker.state == CircuitBreaker.OPEN

    def test_manual_reset(self):
        """Test manual reset closes circuit."""
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.record_failure()

        assert breaker.state == CircuitBreaker.OPEN

        breaker.reset()
        assert breaker.state == CircuitBreaker.CLOSED


class TestCircuitBreakerRegistry:
    """Tests for CircuitBreakerRegistry."""

    def test_creates_breakers_on_demand(self):
        """Test breakers created when first accessed."""
        registry = CircuitBreakerRegistry()

        breaker1 = registry.get_breaker("capability.a@v1")
        breaker2 = registry.get_breaker("capability.b@v1")

        assert breaker1 is not breaker2
        assert breaker1.state == CircuitBreaker.CLOSED

    def test_returns_same_breaker(self):
        """Test same breaker returned for same capability."""
        registry = CircuitBreakerRegistry()

        breaker1 = registry.get_breaker("capability.a@v1")
        breaker2 = registry.get_breaker("capability.a@v1")

        assert breaker1 is breaker2

    def test_get_all_states(self):
        """Test getting all breaker states."""
        registry = CircuitBreakerRegistry(default_failure_threshold=1)

        registry.get_breaker("cap.a@v1")
        registry.get_breaker("cap.b@v1").record_failure()

        states = registry.get_all_states()

        assert states["cap.a@v1"] == CircuitBreaker.CLOSED
        assert states["cap.b@v1"] == CircuitBreaker.OPEN

    def test_get_open_circuits(self):
        """Test getting open circuits."""
        registry = CircuitBreakerRegistry(default_failure_threshold=1)

        registry.get_breaker("cap.a@v1")
        registry.get_breaker("cap.b@v1").record_failure()
        registry.get_breaker("cap.c@v1").record_failure()

        open_circuits = registry.get_open_circuits()

        assert set(open_circuits) == {"cap.b@v1", "cap.c@v1"}

    def test_reset_specific_breaker(self):
        """Test resetting a specific breaker."""
        registry = CircuitBreakerRegistry(default_failure_threshold=1)

        registry.get_breaker("cap.a@v1").record_failure()
        registry.get_breaker("cap.b@v1").record_failure()

        result = registry.reset("cap.a@v1")

        assert result is True
        assert registry.get_breaker("cap.a@v1").state == CircuitBreaker.CLOSED
        assert registry.get_breaker("cap.b@v1").state == CircuitBreaker.OPEN

    def test_reset_nonexistent_breaker(self):
        """Test resetting nonexistent breaker returns False."""
        registry = CircuitBreakerRegistry()

        result = registry.reset("nonexistent@v1")

        assert result is False

    def test_reset_all(self):
        """Test resetting all breakers."""
        registry = CircuitBreakerRegistry(default_failure_threshold=1)

        registry.get_breaker("cap.a@v1").record_failure()
        registry.get_breaker("cap.b@v1").record_failure()

        registry.reset_all()

        assert registry.get_breaker("cap.a@v1").state == CircuitBreaker.CLOSED
        assert registry.get_breaker("cap.b@v1").state == CircuitBreaker.CLOSED


class TestCircuitOpenError:
    """Tests for CircuitOpenError."""

    def test_error_message(self):
        """Test error contains capability name."""
        error = CircuitOpenError("tasks.create@v1")

        assert error.capability_name == "tasks.create@v1"
        assert "tasks.create@v1" in str(error)
