"""Tests for sliding-window rate limiter."""

import time
from unittest.mock import patch

import pytest

from agent_kernel.core.schemas.capability import RateLimit
from agent_kernel.tools.rate_limiter import (
    RateLimiter,
    RateLimiterRegistry,
    RateLimitResult,
)


class TestRateLimiter:
    """Tests for RateLimiter."""

    def test_allows_within_limit(self):
        """Calls within the limit should all be allowed."""
        config = RateLimit(max_calls_per_minute=5, max_calls_per_hour=100)
        limiter = RateLimiter(config)

        for _ in range(5):
            result = limiter.check()
            assert result.allowed is True
            limiter.record()

    def test_denies_over_minute_limit(self):
        """Fourth call should be denied when minute limit is 3."""
        config = RateLimit(max_calls_per_minute=3, max_calls_per_hour=1000)
        limiter = RateLimiter(config)

        for _ in range(3):
            result = limiter.check_and_record()
            assert result.allowed is True

        result = limiter.check()
        assert result.allowed is False
        assert result.current_minute_count == 3
        assert result.limit_minute == 3

    def test_denies_over_hour_limit(self):
        """Fourth call should be denied when hour limit is 3."""
        config = RateLimit(max_calls_per_minute=1000, max_calls_per_hour=3)
        limiter = RateLimiter(config)

        for _ in range(3):
            result = limiter.check_and_record()
            assert result.allowed is True

        result = limiter.check()
        assert result.allowed is False
        assert result.current_hour_count == 3
        assert result.limit_hour == 3

    def test_wait_seconds_calculated(self):
        """Wait seconds should be positive when denied."""
        config = RateLimit(max_calls_per_minute=2, max_calls_per_hour=1000)
        limiter = RateLimiter(config)

        limiter.check_and_record()
        limiter.check_and_record()

        result = limiter.check()
        assert result.allowed is False
        assert result.wait_seconds > 0.0
        # Wait should be less than 60 seconds (the minute window)
        assert result.wait_seconds <= 60.0

    def test_sliding_window_expires(self):
        """Old entries should drop off and allow new calls."""
        config = RateLimit(max_calls_per_minute=2, max_calls_per_hour=1000)
        limiter = RateLimiter(config)

        # Record two calls at time 0
        base_time = 1000.0
        with patch("time.monotonic", return_value=base_time):
            limiter.record()
            limiter.record()

        # At time 0, we should be at limit
        with patch("time.monotonic", return_value=base_time):
            result = limiter.check()
            assert result.allowed is False

        # At time 61 (past the 60s window), old entries should have expired
        with patch("time.monotonic", return_value=base_time + 61.0):
            result = limiter.check()
            assert result.allowed is True
            assert result.current_minute_count == 0

    def test_check_and_record_atomic(self):
        """check_and_record should both check and record in one call."""
        config = RateLimit(max_calls_per_minute=2, max_calls_per_hour=1000)
        limiter = RateLimiter(config)

        result1 = limiter.check_and_record()
        assert result1.allowed is True
        assert result1.current_minute_count == 1

        result2 = limiter.check_and_record()
        assert result2.allowed is True
        assert result2.current_minute_count == 2

        # Third call should be denied
        result3 = limiter.check_and_record()
        assert result3.allowed is False
        assert result3.current_minute_count == 2

    def test_reset_clears_state(self):
        """After reset, limiter should allow calls again."""
        config = RateLimit(max_calls_per_minute=1, max_calls_per_hour=1000)
        limiter = RateLimiter(config)

        limiter.check_and_record()
        result = limiter.check()
        assert result.allowed is False

        limiter.reset()

        result = limiter.check()
        assert result.allowed is True


class TestRateLimiterRegistry:
    """Tests for RateLimiterRegistry."""

    def test_registry_creates_limiter(self):
        """Registry should create and cache limiters on demand."""
        registry = RateLimiterRegistry()
        config = RateLimit(max_calls_per_minute=10, max_calls_per_hour=100)

        limiter1 = registry.get_limiter("cap.a@v1", config=config)
        limiter2 = registry.get_limiter("cap.a@v1")

        assert limiter1 is limiter2

    def test_registry_different_capabilities(self):
        """Different capabilities should get independent limiters."""
        registry = RateLimiterRegistry()

        limiter_a = registry.get_limiter("cap.a@v1")
        limiter_b = registry.get_limiter("cap.b@v1")

        assert limiter_a is not limiter_b

    def test_registry_default_config(self):
        """Limiter without explicit config should use defaults."""
        registry = RateLimiterRegistry()
        limiter = registry.get_limiter("cap.default@v1")

        # Default RateLimit allows 60/min, 1000/hr
        result = limiter.check()
        assert result.allowed is True
        assert result.limit_minute == 60
        assert result.limit_hour == 1000

    def test_registry_get_all_stats(self):
        """Stats should reflect current state of all limiters."""
        registry = RateLimiterRegistry()
        config = RateLimit(max_calls_per_minute=2, max_calls_per_hour=100)

        limiter = registry.get_limiter("cap.a@v1", config=config)
        limiter.check_and_record()

        registry.get_limiter("cap.b@v1")

        stats = registry.get_all_stats()
        assert "cap.a@v1" in stats
        assert "cap.b@v1" in stats
        assert stats["cap.a@v1"].current_minute_count == 1
        assert stats["cap.b@v1"].current_minute_count == 0

    def test_registry_reset(self):
        """Resetting a specific limiter should clear only that limiter."""
        registry = RateLimiterRegistry()
        config = RateLimit(max_calls_per_minute=1, max_calls_per_hour=100)

        limiter_a = registry.get_limiter("cap.a@v1", config=config)
        limiter_b = registry.get_limiter("cap.b@v1", config=config)
        limiter_a.check_and_record()
        limiter_b.check_and_record()

        result = registry.reset("cap.a@v1")
        assert result is True

        assert limiter_a.check().allowed is True
        assert limiter_b.check().allowed is False

    def test_registry_reset_nonexistent(self):
        """Resetting a nonexistent limiter should return False."""
        registry = RateLimiterRegistry()
        assert registry.reset("nonexistent@v1") is False

    def test_registry_reset_all(self):
        """reset_all should clear all limiters."""
        registry = RateLimiterRegistry()
        config = RateLimit(max_calls_per_minute=1, max_calls_per_hour=100)

        limiter_a = registry.get_limiter("cap.a@v1", config=config)
        limiter_b = registry.get_limiter("cap.b@v1", config=config)
        limiter_a.check_and_record()
        limiter_b.check_and_record()

        registry.reset_all()

        assert limiter_a.check().allowed is True
        assert limiter_b.check().allowed is True
