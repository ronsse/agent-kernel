"""Tests for ThinkingSession to_dict / from_dict persistence."""

from __future__ import annotations

from agent_kernel.core.schemas.thinking import ADAPTIVE_THINKING, ThinkingConfig
from agent_kernel.engine.adaptive_thinking import AdaptiveThinkingSession
from agent_kernel.engine.thinking_policy import (
    EscalationAttempt,
    ThinkingSession,
)


class TestThinkingSessionPersistence:
    def test_round_trip(self):
        config = ThinkingConfig(mode="adaptive")
        session = ThinkingSession(
            config=config,
            current_tier=2,
            escalation_count=1,
            gate_failures=["coverage_gate"],
            gate_warnings=["low_recall"],
            critic_issues=["missing_citation"],
        )
        session.attempts.append(
            EscalationAttempt(
                tier=1,
                trigger="quality_gates_failed",
                success=True,
                details="Gate failures triggered escalation",
            )
        )

        data = session.to_dict()
        restored = ThinkingSession.from_dict(data, config)

        assert restored.current_tier == 2
        assert restored.escalation_count == 1
        assert restored.gate_failures == ["coverage_gate"]
        assert restored.gate_warnings == ["low_recall"]
        assert restored.critic_issues == ["missing_citation"]
        assert len(restored.attempts) == 1
        assert restored.attempts[0].tier == 1
        assert restored.attempts[0].trigger == "quality_gates_failed"
        assert restored.attempts[0].success is True

    def test_callback_excluded(self):
        config = ThinkingConfig(mode="standard")
        callback = lambda reason, current, target: True  # noqa: E731
        session = ThinkingSession(
            config=config,
            pending_approval_callback=callback,
        )
        data = session.to_dict()
        assert "pending_approval_callback" not in data

    def test_defaults_on_missing_keys(self):
        config = ThinkingConfig(mode="standard")
        data = {}
        restored = ThinkingSession.from_dict(data, config)
        assert restored.current_tier == 1
        assert restored.escalation_count == 0
        assert restored.gate_failures == []
        assert restored.attempts == []
        assert restored.approval_granted is True

    def test_from_dict_with_approval_callback(self):
        config = ThinkingConfig(mode="standard")
        callback = lambda r, c, t: False  # noqa: E731
        data = {"current_tier": 2}
        restored = ThinkingSession.from_dict(data, config, approval_callback=callback)
        assert restored.pending_approval_callback is callback


class TestAdaptiveThinkingSessionPersistence:
    def test_round_trip_with_adaptive_fields(self):
        config = ADAPTIVE_THINKING
        session = AdaptiveThinkingSession(
            config=config,
            current_tier=2,
            escalation_count=1,
            workflow_id="daily_checkin",
            tier_adjustment=1,
            model_override="gpt-4o",
            timeout_adjustment_ms=5000,
        )
        session.attempts.append(
            EscalationAttempt(
                tier=1,
                trigger="low_confidence",
                success=True,
                details="Confidence below threshold",
            )
        )

        data = session.to_dict()
        assert data["workflow_id"] == "daily_checkin"
        assert data["tier_adjustment"] == 1
        assert data["model_override"] == "gpt-4o"
        assert data["timeout_adjustment_ms"] == 5000

        restored = AdaptiveThinkingSession.from_dict(data, config)
        assert restored.workflow_id == "daily_checkin"
        assert restored.tier_adjustment == 1
        assert restored.model_override == "gpt-4o"
        assert restored.timeout_adjustment_ms == 5000
        assert restored.current_tier == 2
        assert len(restored.attempts) == 1

    def test_adaptive_defaults(self):
        config = ADAPTIVE_THINKING
        data = {}
        restored = AdaptiveThinkingSession.from_dict(data, config)
        assert restored.workflow_id is None
        assert restored.tier_adjustment == 0
        assert restored.model_override is None
        assert restored.timeout_adjustment_ms == 0
