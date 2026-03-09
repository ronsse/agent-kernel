"""Tests for workflow retry logic."""


from agent_kernel.workflows.spec import RetryConfig


class TestRetryConfig:
    """Tests for RetryConfig."""

    def test_default_values(self):
        """Test default configuration."""
        config = RetryConfig()

        assert config.max_retries == 3
        assert config.initial_delay_seconds == 1.0
        assert config.max_delay_seconds == 60.0
        assert config.exponential_backoff is True
        assert config.backoff_multiplier == 2.0

    def test_get_delay_no_backoff(self):
        """Test delay calculation without backoff."""
        config = RetryConfig(
            initial_delay_seconds=2.0,
            exponential_backoff=False,
        )

        # All attempts should have the same delay
        assert config.get_delay(1) == 2.0
        assert config.get_delay(2) == 2.0
        assert config.get_delay(3) == 2.0

    def test_get_delay_with_backoff(self):
        """Test delay calculation with exponential backoff."""
        config = RetryConfig(
            initial_delay_seconds=1.0,
            exponential_backoff=True,
            backoff_multiplier=2.0,
            max_delay_seconds=60.0,
        )

        # Exponential increase: 1, 2, 4, 8, ...
        assert config.get_delay(1) == 1.0
        assert config.get_delay(2) == 2.0
        assert config.get_delay(3) == 4.0
        assert config.get_delay(4) == 8.0

    def test_get_delay_max_cap(self):
        """Test delay is capped at max."""
        config = RetryConfig(
            initial_delay_seconds=10.0,
            exponential_backoff=True,
            backoff_multiplier=2.0,
            max_delay_seconds=30.0,
        )

        # Should cap at 30
        assert config.get_delay(1) == 10.0
        assert config.get_delay(2) == 20.0
        assert config.get_delay(3) == 30.0  # Capped, not 40
        assert config.get_delay(4) == 30.0  # Still capped

    def test_custom_backoff_multiplier(self):
        """Test custom backoff multiplier."""
        config = RetryConfig(
            initial_delay_seconds=1.0,
            exponential_backoff=True,
            backoff_multiplier=3.0,
        )

        # Multiplier of 3: 1, 3, 9, 27
        assert config.get_delay(1) == 1.0
        assert config.get_delay(2) == 3.0
        assert config.get_delay(3) == 9.0

    def test_retryable_errors_default(self):
        """Test default retryable errors."""
        config = RetryConfig()

        assert "timeout" in config.retryable_errors
        assert "rate_limit" in config.retryable_errors
        assert "temporary" in config.retryable_errors

    def test_retryable_errors_custom(self):
        """Test custom retryable errors."""
        config = RetryConfig(
            retryable_errors=["connection", "server_error"],
        )

        assert "connection" in config.retryable_errors
        assert "server_error" in config.retryable_errors
        assert "timeout" not in config.retryable_errors
