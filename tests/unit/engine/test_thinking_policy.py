"""Tests for ThinkingPolicyController."""

import pytest

from agent_kernel.core.schemas import AgentProfile, ContextPolicy
from agent_kernel.core.schemas.retrieval import CoverageGateResult, RetrievalQualityReport
from agent_kernel.core.schemas.thinking import (
    ADAPTIVE_THINKING,
    STANDARD_THINKING,
    EscalationConfig,
    ThinkingConfig,
    ThinkingTierConfig,
)
from agent_kernel.engine.thinking_policy import (
    EscalationTrigger,
    ThinkingPolicy,
    ThinkingPolicyController,
)


@pytest.fixture
def sample_agent_profile() -> AgentProfile:
    """Create a sample agent profile."""
    return AgentProfile(
        agent_profile_id="test_agent",
        name="Test Agent",
        engine="custom",
        allowed_capabilities=["tasks.list@v1"],
        context_policy=ContextPolicy(max_tokens=4000),
    )


class TestThinkingTierConfig:
    """Tests for ThinkingTierConfig."""

    def test_create_from_dict(self):
        """Test creating tier from dictionary."""
        data = {
            "name": "deep",
            "description": "Deep analysis",
            "model": "gpt-4o",
            "reasoning_effort": "high",
            "max_tokens": 4000,
            "use_critic": True,
            "max_revisions": 3,
            "max_context_tokens": 8000,
        }

        tier = ThinkingTierConfig.model_validate({"tier": 2, **data})

        assert tier.name == "deep"
        assert tier.description == "Deep analysis"
        assert tier.model == "gpt-4o"
        assert tier.reasoning_effort == "high"
        assert tier.max_tokens == 4000
        assert tier.use_critic is True
        assert tier.max_revisions == 3

    def test_create_with_defaults(self):
        """Test creating tier with minimal config."""
        data = {"model": "gpt-4o-mini"}

        tier = ThinkingTierConfig.model_validate({"tier": 0, "name": "tier_0", **data})

        assert tier.name == "tier_0"
        assert tier.reasoning_effort == "medium"
        assert tier.use_critic is False
        assert tier.max_revisions == 2


class TestThinkingPolicy:
    """Tests for ThinkingPolicy."""

    def test_policy_fields(self):
        """Test policy fields are set correctly."""
        policy = ThinkingPolicy(
            tier=2,
            tier_name="deep",
            model_id="gpt-4o",
            reasoning_effort="high",
            max_tokens=4000,
            temperature=0.3,
            run_critic=True,
            escalation_reason="low confidence",
        )

        assert policy.tier == 2
        assert policy.tier_name == "deep"
        assert policy.model_id == "gpt-4o"
        assert policy.reasoning_effort == "high"
        assert policy.max_tokens == 4000
        assert policy.run_critic is True
        assert policy.escalation_reason == "low confidence"


class TestThinkingPolicyController:
    """Tests for ThinkingPolicyController."""

    def test_default_session_uses_standard_thinking(
        self, sample_agent_profile: AgentProfile
    ):
        """Test controller uses STANDARD_THINKING by default."""
        controller = ThinkingPolicyController()
        session = controller.create_session(sample_agent_profile)

        assert session.current_tier == STANDARD_THINKING.get_starting_tier()

    def test_custom_default_config(self, sample_agent_profile: AgentProfile):
        """Test controller uses provided default config."""
        custom_config = ThinkingConfig(
            tiers={
                0: ThinkingTierConfig(
                    tier=0,
                    name="fast",
                    model="gpt-4o-mini",
                    reasoning_effort="low",
                    max_tokens=500,
                ),
                1: ThinkingTierConfig(
                    tier=1,
                    name="standard",
                    model="gpt-4o",
                    reasoning_effort="medium",
                    max_tokens=2000,
                ),
            }
        )
        controller = ThinkingPolicyController(default_config=custom_config)
        session = controller.create_session(sample_agent_profile)
        policy = controller.get_policy(session)

        assert policy.tier_name == "standard"
        assert policy.model_id == "gpt-4o"

    def test_deep_mode_starts_at_max_tier(
        self, sample_agent_profile: AgentProfile
    ):
        """Test deep mode starts at max tier."""
        deep_config = ThinkingConfig(mode="deep")
        controller = ThinkingPolicyController(default_config=deep_config)
        session = controller.create_session(sample_agent_profile)

        assert session.current_tier == deep_config.escalation.max_tier

    def test_evaluate_for_escalation_quality_gates(
        self, sample_agent_profile: AgentProfile
    ):
        """Test escalation triggered by quality gate failures."""
        profile = sample_agent_profile.model_copy(
            update={"thinking_config": ADAPTIVE_THINKING}
        )
        controller = ThinkingPolicyController()
        session = controller.create_session(profile)
        report = RetrievalQualityReport(
            mode="baseline",
            gate_results=[
                CoverageGateResult(gate="coverage", passed=False, severity="error")
            ],
        )

        should_escalate, trigger, reason = controller.evaluate_for_escalation(
            session, quality_report=report
        )

        assert should_escalate is True
        assert trigger == "quality_gates_failed"
        assert "Quality gates failed" in reason

    def test_evaluate_for_escalation_low_confidence(
        self, sample_agent_profile: AgentProfile
    ):
        """Test escalation triggered by low confidence."""
        profile = sample_agent_profile.model_copy(
            update={"thinking_config": ADAPTIVE_THINKING}
        )
        controller = ThinkingPolicyController()
        session = controller.create_session(profile)

        should_escalate, trigger, reason = controller.evaluate_for_escalation(
            session, confidence=0.5
        )

        assert should_escalate is True
        assert trigger == "low_confidence"
        assert "Confidence" in reason

    @pytest.mark.asyncio
    async def test_escalate_advances_tier(
        self, sample_agent_profile: AgentProfile
    ):
        """Test escalation advances tier when allowed."""
        profile = sample_agent_profile.model_copy(
            update={"thinking_config": ADAPTIVE_THINKING}
        )
        controller = ThinkingPolicyController()
        session = controller.create_session(profile)

        ok = await controller.escalate(
            session, "low_confidence", "Confidence below threshold"
        )

        assert ok is True
        assert session.current_tier == 2


class TestEscalationTrigger:
    """Tests for EscalationTrigger configuration."""

    def test_adaptive_triggers_include_quality_gates(self):
        """Adaptive config should include quality-gate escalation."""
        assert "quality_gates_failed" in ADAPTIVE_THINKING.escalation.triggers
        assert "low_confidence" in ADAPTIVE_THINKING.escalation.triggers

    def test_custom_escalation_config(self):
        """Custom escalation config respects explicit triggers."""
        custom = EscalationConfig(
            enabled=True,
            triggers=["low_confidence"],
            confidence_threshold=0.9,
        )
        assert custom.triggers == ["low_confidence"]
        assert custom.confidence_threshold == 0.9
