"""Tests for adaptive thinking policy controller."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from agent_kernel.core.schemas.thinking import (
    STANDARD_THINKING,
    ThinkingConfig,
    ThinkingTier,
)
from agent_kernel.engine.adaptive_thinking import (
    AdaptiveThinkingPolicyController,
    AdaptiveThinkingSession,
    ModelPerformanceStats,
    WorkflowPerformanceStats,
)


class TestWorkflowPerformanceStats:
    """Tests for WorkflowPerformanceStats."""

    def test_success_rate_calculation(self):
        """Test success rate property."""
        stats = WorkflowPerformanceStats(
            workflow_id="test",
            total_runs=100,
            successful_runs=75,
            failed_runs=25,
        )
        assert stats.success_rate == 0.75

    def test_success_rate_zero_runs(self):
        """Test success rate with no runs."""
        stats = WorkflowPerformanceStats(workflow_id="test", total_runs=0)
        assert stats.success_rate == 0.0

    def test_escalation_rate_calculation(self):
        """Test escalation rate property."""
        stats = WorkflowPerformanceStats(
            workflow_id="test",
            total_runs=100,
            escalation_count=40,
        )
        assert stats.escalation_rate == 0.4

    def test_escalation_rate_zero_runs(self):
        """Test escalation rate with no runs."""
        stats = WorkflowPerformanceStats(workflow_id="test", total_runs=0)
        assert stats.escalation_rate == 0.0


class TestModelPerformanceStats:
    """Tests for ModelPerformanceStats."""

    def test_success_rate_calculation(self):
        """Test success rate property."""
        stats = ModelPerformanceStats(
            model_id="gpt-4",
            total_calls=200,
            successful_calls=180,
            failed_calls=20,
        )
        assert stats.success_rate == 0.9

    def test_success_rate_zero_calls(self):
        """Test success rate with no calls."""
        stats = ModelPerformanceStats(model_id="gpt-4", total_calls=0)
        assert stats.success_rate == 0.0

    def test_avg_cost_per_call(self):
        """Test average cost calculation."""
        stats = ModelPerformanceStats(
            model_id="gpt-4",
            total_calls=100,
            total_cost_usd=5.0,
        )
        assert stats.avg_cost_per_call == 0.05

    def test_avg_cost_zero_calls(self):
        """Test average cost with no calls."""
        stats = ModelPerformanceStats(model_id="gpt-4", total_calls=0)
        assert stats.avg_cost_per_call == 0.0


class TestAdaptiveThinkingSession:
    """Tests for AdaptiveThinkingSession."""

    def test_session_creation_with_defaults(self):
        """Test session creation with default values."""
        session = AdaptiveThinkingSession(
            config=STANDARD_THINKING,
            current_tier=1,
        )
        assert session.workflow_id is None
        assert session.workflow_stats is None
        assert session.tier_adjustment == 0
        assert session.model_override is None
        assert session.timeout_adjustment_ms == 0

    def test_session_creation_with_workflow_context(self):
        """Test session creation with workflow context."""
        stats = WorkflowPerformanceStats(
            workflow_id="daily_checkin",
            total_runs=50,
            successful_runs=40,
        )
        session = AdaptiveThinkingSession(
            config=STANDARD_THINKING,
            current_tier=2,
            workflow_id="daily_checkin",
            workflow_stats=stats,
            tier_adjustment=1,
        )
        assert session.workflow_id == "daily_checkin"
        assert session.workflow_stats == stats
        assert session.tier_adjustment == 1


class TestAdaptiveThinkingPolicyController:
    """Tests for AdaptiveThinkingPolicyController."""

    @pytest.fixture
    def mock_agent_profile(self):
        """Create a mock agent profile."""
        profile = MagicMock()
        profile.thinking_config = STANDARD_THINKING
        return profile

    @pytest.fixture
    def controller(self):
        """Create a controller without trace store."""
        return AdaptiveThinkingPolicyController(
            trace_store=None,
            default_config=STANDARD_THINKING,
        )

    def test_controller_initialization(self):
        """Test controller initializes with default values."""
        controller = AdaptiveThinkingPolicyController()
        assert controller._trace_store is None
        assert controller._cache_ttl == 300
        assert controller._lookback_hours == 168
        assert controller._workflow_stats_cache == {}
        assert controller._model_stats_cache == {}

    def test_controller_initialization_with_custom_values(self):
        """Test controller with custom configuration."""
        mock_store = MagicMock()
        controller = AdaptiveThinkingPolicyController(
            trace_store=mock_store,
            default_config=STANDARD_THINKING,
            cache_ttl_seconds=600,
            lookback_hours=24,
        )
        assert controller._trace_store == mock_store
        assert controller._cache_ttl == 600
        assert controller._lookback_hours == 24

    def test_create_session_basic(self, controller, mock_agent_profile):
        """Test basic session creation."""
        session = controller.create_session(mock_agent_profile)

        assert isinstance(session, AdaptiveThinkingSession)
        assert session.workflow_id is None
        assert session.tier_adjustment == 0
        assert session.model_override is None

    def test_create_session_with_workflow_id(self, controller, mock_agent_profile):
        """Test session creation with workflow ID."""
        session = controller.create_session(
            mock_agent_profile,
            workflow_id="daily_checkin",
        )

        assert session.workflow_id == "daily_checkin"
        assert session.workflow_stats is None  # No stats in cache

    def test_create_session_with_cached_stats(self, controller, mock_agent_profile):
        """Test session uses cached workflow stats."""
        # Pre-populate cache
        controller._workflow_stats_cache["daily_checkin"] = WorkflowPerformanceStats(
            workflow_id="daily_checkin",
            total_runs=100,
            successful_runs=80,
            escalation_count=20,
        )

        session = controller.create_session(
            mock_agent_profile,
            workflow_id="daily_checkin",
        )

        assert session.workflow_stats is not None
        assert session.workflow_stats.total_runs == 100

    def test_tier_adjustment_for_high_escalation_workflow(
        self, controller, mock_agent_profile
    ):
        """Test tier adjustment when escalation rate is high."""
        # Pre-populate cache with high escalation workflow
        controller._workflow_stats_cache["problematic"] = WorkflowPerformanceStats(
            workflow_id="problematic",
            total_runs=100,
            escalation_count=50,  # 50% escalation rate
        )

        session = controller.create_session(
            mock_agent_profile,
            workflow_id="problematic",
        )

        assert session.tier_adjustment == 1
        assert session.current_tier == 2  # Started at 1, adjusted to 2

    def test_no_tier_adjustment_for_stable_workflow(
        self, controller, mock_agent_profile
    ):
        """Test no tier adjustment for stable workflow."""
        # Pre-populate cache with stable workflow
        controller._workflow_stats_cache["stable"] = WorkflowPerformanceStats(
            workflow_id="stable",
            total_runs=100,
            escalation_count=10,  # 10% escalation rate
        )

        session = controller.create_session(
            mock_agent_profile,
            workflow_id="stable",
        )

        assert session.tier_adjustment == 0
        assert session.current_tier == 1  # No adjustment

    def test_model_override_for_low_success_workflow(
        self, controller, mock_agent_profile
    ):
        """Test model override when workflow success rate is low."""
        # Pre-populate cache with low success workflow
        controller._workflow_stats_cache["failing"] = WorkflowPerformanceStats(
            workflow_id="failing",
            total_runs=100,
            successful_runs=50,  # 50% success rate
            model_success_rates={
                "gpt-4": 0.9,  # High success with this model
                "gpt-3.5": 0.4,  # Low success with this model
            },
        )

        session = controller.create_session(
            mock_agent_profile,
            workflow_id="failing",
        )

        assert session.model_override == "gpt-4"  # Best performing model

    def test_get_policy_basic(self, controller, mock_agent_profile):
        """Test basic policy retrieval."""
        session = controller.create_session(mock_agent_profile)
        policy = controller.get_policy(session)

        assert policy is not None
        assert policy.tier == session.current_tier

    def test_get_policy_with_model_override(self, controller, mock_agent_profile):
        """Test policy with model override applied."""
        # Pre-populate cache
        controller._workflow_stats_cache["failing"] = WorkflowPerformanceStats(
            workflow_id="failing",
            total_runs=100,
            successful_runs=50,
            model_success_rates={"claude-3-opus": 0.95},
        )

        session = controller.create_session(
            mock_agent_profile,
            workflow_id="failing",
        )

        assert session.model_override == "claude-3-opus"

        policy = controller.get_policy(session)
        assert policy.model_id == "claude-3-opus"

    def test_get_policy_timeout_adjustment(self, controller, mock_agent_profile):
        """Test timeout adjustment based on model stats."""
        session = controller.create_session(mock_agent_profile)

        # Get the policy to find which model is used
        policy = controller.get_policy(session)
        model_id = policy.model_id

        # Add model stats for the actual model used
        session.model_stats = {
            model_id: ModelPerformanceStats(
                model_id=model_id,
                total_calls=100,
                p99_latency_ms=5000,  # 5 second P99
            )
        }

        # Get policy again with stats in place
        policy = controller.get_policy(session)

        # Should have timeout adjustment (5000 * 1.2 = 6000)
        assert session.timeout_adjustment_ms == 6000

    def test_get_recommended_timeout_default(self, controller, mock_agent_profile):
        """Test default timeout when no stats available."""
        session = controller.create_session(mock_agent_profile)
        timeout = controller.get_recommended_timeout(session)

        assert timeout == 30000  # Default 30 seconds

    def test_get_recommended_timeout_with_stats(self, controller, mock_agent_profile):
        """Test timeout based on P99 latency."""
        session = controller.create_session(mock_agent_profile)
        session.timeout_adjustment_ms = 6000  # Pre-set

        timeout = controller.get_recommended_timeout(session)

        assert timeout == 6000


class TestAdaptiveControllerCacheRefresh:
    """Tests for cache refresh functionality."""

    @pytest.fixture
    def mock_trace_store(self):
        """Create a mock trace store."""
        return MagicMock()

    @pytest.fixture
    def controller_with_store(self, mock_trace_store):
        """Create controller with mock trace store."""
        return AdaptiveThinkingPolicyController(
            trace_store=mock_trace_store,
            cache_ttl_seconds=60,
        )

    def test_cache_not_refreshed_when_fresh(
        self, controller_with_store, mock_trace_store
    ):
        """Test cache is not refreshed if still fresh."""
        controller_with_store._cache_updated_at = datetime.now(timezone.utc)

        controller_with_store._refresh_cache_if_needed()

        mock_trace_store.list_traces.assert_not_called()

    def test_cache_refreshed_when_stale(
        self, controller_with_store, mock_trace_store
    ):
        """Test cache is refreshed when stale."""
        # Set cache to be stale
        controller_with_store._cache_updated_at = datetime.now(
            timezone.utc
        ) - timedelta(seconds=120)

        # Mock list_traces to return empty list
        mock_trace_store.list_traces = MagicMock(return_value=[])

        controller_with_store._refresh_cache_if_needed()

        assert mock_trace_store.list_traces.called

    def test_cache_refresh_handles_error(
        self, controller_with_store, mock_trace_store
    ):
        """Test cache refresh handles errors gracefully."""
        controller_with_store._cache_updated_at = None

        mock_trace_store.list_traces = MagicMock(
            side_effect=Exception("Database error")
        )

        # Should not raise
        controller_with_store._refresh_cache_if_needed()

    def test_cache_refresh_skipped_without_trace_store(self):
        """Test cache refresh is skipped without trace store."""
        controller = AdaptiveThinkingPolicyController(trace_store=None)
        controller._cache_updated_at = None

        # Should not raise
        controller._refresh_cache_if_needed()

        # Cache should still be empty
        assert controller._workflow_stats_cache == {}


class TestAdaptiveControllerThresholds:
    """Tests for adaptive behavior thresholds."""

    @pytest.fixture
    def mock_agent_profile(self):
        """Create a mock agent profile."""
        profile = MagicMock()
        profile.thinking_config = STANDARD_THINKING
        return profile

    def test_high_escalation_threshold(self, mock_agent_profile):
        """Test high escalation threshold triggers tier adjustment."""
        controller = AdaptiveThinkingPolicyController()
        controller._high_escalation_threshold = 0.3

        # Just below threshold
        controller._workflow_stats_cache["below"] = WorkflowPerformanceStats(
            workflow_id="below",
            total_runs=100,
            escalation_count=29,  # 29% - below 30%
        )
        session_below = controller.create_session(
            mock_agent_profile, workflow_id="below"
        )
        assert session_below.tier_adjustment == 0

        # At threshold
        controller._workflow_stats_cache["at"] = WorkflowPerformanceStats(
            workflow_id="at",
            total_runs=100,
            escalation_count=30,  # Exactly 30%
        )
        session_at = controller.create_session(mock_agent_profile, workflow_id="at")
        assert session_at.tier_adjustment == 0  # Not above threshold

        # Above threshold
        controller._workflow_stats_cache["above"] = WorkflowPerformanceStats(
            workflow_id="above",
            total_runs=100,
            escalation_count=31,  # 31% - above 30%
        )
        session_above = controller.create_session(
            mock_agent_profile, workflow_id="above"
        )
        assert session_above.tier_adjustment == 1

    def test_invalidate_cache_resets_timestamp(self):
        """Test that invalidate_cache sets _cache_updated_at to None."""
        controller = AdaptiveThinkingPolicyController()
        controller._cache_updated_at = datetime.now(timezone.utc)

        controller.invalidate_cache()

        assert controller._cache_updated_at is None

    def test_refresh_triggered_after_invalidation(self):
        """Test that cache refresh is triggered after invalidation."""
        mock_store = MagicMock()
        mock_store.list_traces = MagicMock(return_value=[])

        controller = AdaptiveThinkingPolicyController(
            trace_store=mock_store,
            cache_ttl_seconds=300,
        )

        # Set cache as fresh
        controller._cache_updated_at = datetime.now(timezone.utc)

        # Should NOT refresh — cache is fresh
        controller._refresh_cache_if_needed()
        mock_store.list_traces.assert_not_called()

        # Invalidate and try again
        controller.invalidate_cache()
        controller._refresh_cache_if_needed()

        # Now it should have refreshed
        mock_store.list_traces.assert_called_once()
        assert controller._cache_updated_at is not None

    def test_tier_adjustment_caps_at_tier_3(self, mock_agent_profile):
        """Test tier adjustment doesn't exceed tier 3."""
        controller = AdaptiveThinkingPolicyController()

        # Workflow with high escalation
        controller._workflow_stats_cache["high_esc"] = WorkflowPerformanceStats(
            workflow_id="high_esc",
            total_runs=100,
            escalation_count=50,
        )

        # Create config that starts at tier 3
        custom_config = MagicMock()
        custom_config.get_starting_tier.return_value = 3
        custom_config.escalation = MagicMock()
        custom_config.escalation.enabled = True
        custom_config.escalation.max_tier = 3
        custom_config.escalation.max_escalations = 2
        mock_agent_profile.thinking_config = custom_config

        session = controller.create_session(
            mock_agent_profile, workflow_id="high_esc"
        )

        # Should still be capped at 3
        assert session.current_tier == 3
