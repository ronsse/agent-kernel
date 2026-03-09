"""Tests for LLM circuit breaker integration."""

from __future__ import annotations

from agent_kernel.core.errors import LLMCircuitOpenError
from agent_kernel.tools.retry import CircuitBreaker


class TestLLMCircuitBreaker:
    """Test circuit breaker behavior for LLM services."""

    def test_circuit_starts_closed(self):
        """Circuit breaker starts in closed state."""
        cb = CircuitBreaker(failure_threshold=3, reset_timeout_ms=1000)
        assert cb.allow_request() is True

    def test_circuit_opens_after_threshold(self):
        """Circuit opens after failure threshold is reached."""
        cb = CircuitBreaker(failure_threshold=3, reset_timeout_ms=60000)

        # Record failures up to threshold
        cb.record_failure()
        assert cb.allow_request() is True
        cb.record_failure()
        assert cb.allow_request() is True
        cb.record_failure()
        # Circuit should be open now
        assert cb.allow_request() is False

    def test_circuit_resets_on_success(self):
        """Success resets the failure counter."""
        cb = CircuitBreaker(failure_threshold=3, reset_timeout_ms=60000)

        cb.record_failure()
        cb.record_failure()
        # 2 failures, but success resets
        cb.record_success()

        # Should still be closed
        assert cb.allow_request() is True

        # Need 3 more failures to open
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is True
        cb.record_failure()
        assert cb.allow_request() is False

    def test_llm_circuit_open_error(self):
        """LLMCircuitOpenError has correct attributes."""
        err = LLMCircuitOpenError("gpt-4o")
        assert err.model == "gpt-4o"
        assert err.code == "LLM_CIRCUIT_OPEN"
        assert "gpt-4o" in str(err)

    def test_circuit_breaker_can_be_reset(self):
        """Circuit breaker can be manually reset."""
        cb = CircuitBreaker(failure_threshold=2, reset_timeout_ms=60000)

        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is False

        cb.reset()
        assert cb.allow_request() is True
