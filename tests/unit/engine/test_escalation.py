"""Tests for escalation flow in ThinkingPolicyController."""

import pytest

from agent_kernel.core.schemas import AgentProfile, ContextPolicy
from agent_kernel.core.schemas.thinking import ADAPTIVE_THINKING
from agent_kernel.engine.thinking_policy import ThinkingPolicyController


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


@pytest.mark.asyncio
async def test_escalate_increases_tier(sample_agent_profile: AgentProfile):
    """Escalation should advance the tier and record the attempt."""
    controller = ThinkingPolicyController()
    profile = sample_agent_profile.model_copy(
        update={"thinking_config": ADAPTIVE_THINKING}
    )
    session = controller.create_session(profile)

    ok = await controller.escalate(
        session, "low_confidence", "Confidence below threshold"
    )

    assert ok is True
    assert session.current_tier == 2
    assert session.escalation_count == 1
    assert session.attempts


@pytest.mark.asyncio
async def test_escalate_blocked_at_max_tier(sample_agent_profile: AgentProfile):
    """Escalation should be blocked once max tier is reached."""
    controller = ThinkingPolicyController()
    profile = sample_agent_profile.model_copy(
        update={"thinking_config": ADAPTIVE_THINKING}
    )
    session = controller.create_session(profile)
    session.current_tier = session.config.escalation.max_tier

    ok = await controller.escalate(session, "low_confidence", "No escalation")

    assert ok is False
    assert session.current_tier == session.config.escalation.max_tier
