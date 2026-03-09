"""Tests for custom engine."""

import json
from unittest.mock import AsyncMock

import pytest
from agent_kernel.core.schemas import (
    AgentProfile,
    ContextPacket,
    PromptConfig,
    RiskLevel,
)
from agent_kernel.engine.custom_engine import CustomEngine
from agent_kernel.engine.thinking_policy import ReasoningEffort, ThinkingPolicy


class TestCustomEngine:
    """Tests for CustomEngine."""

    def test_engine_properties(self):
        """Test engine ID and version."""
        engine = CustomEngine(engine_id="test_engine", version="2.0.0")

        assert engine.engine_id == "test_engine"
        assert engine.version == "2.0.0"

    def test_default_properties(self):
        """Test default engine properties."""
        engine = CustomEngine()

        assert engine.engine_id == "custom"
        assert engine.version == "1.0.0"

    @pytest.mark.asyncio
    async def test_propose_stub_plan(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test proposing a stub plan without LLM."""
        engine = CustomEngine()

        plan = await engine.propose(sample_context_packet, sample_agent_profile)

        assert plan is not None
        assert plan.intent == sample_context_packet.intent
        assert "stub" in plan.summary.lower() or "Stub" in plan.summary
        assert len(plan.questions) > 0  # Should ask about LLM config
        assert plan.risk.level == RiskLevel.LOW

    @pytest.mark.asyncio
    async def test_propose_uses_context_refs(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that stub plan includes context refs."""
        engine = CustomEngine()

        plan = await engine.propose(sample_context_packet, sample_agent_profile)

        # Stub plan should include some context refs
        if sample_context_packet.items:
            assert len(plan.context_refs_used) > 0

    def test_extract_json_direct(self):
        """Test JSON extraction from direct JSON."""
        engine = CustomEngine()

        json_text = '{"summary": "Test", "actions": []}'
        result = engine._extract_json(json_text)

        assert result["summary"] == "Test"

    def test_extract_json_code_block(self):
        """Test JSON extraction from code block."""
        engine = CustomEngine()

        text = """Here's the plan:

```json
{"summary": "From code block", "actions": []}
```

That's the plan."""

        result = engine._extract_json(text)
        assert result["summary"] == "From code block"

    def test_extract_json_embedded(self):
        """Test JSON extraction from embedded object."""
        engine = CustomEngine()

        text = """Based on the context, here is my response:
{"summary": "Embedded JSON", "actions": [], "risk": {"level": "low"}}
This completes the plan."""

        result = engine._extract_json(text)
        assert result["summary"] == "Embedded JSON"

    def test_render_context_uses_prompt_config(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that prompt config uses serializer output."""
        engine = CustomEngine()
        profile = sample_agent_profile.model_copy(deep=True)
        profile.prompt_config = PromptConfig(format="json")

        rendered, fmt = engine._render_context(sample_context_packet, profile)

        assert fmt == "json"
        payload = json.loads(rendered)
        assert payload["intent"] == sample_context_packet.intent

    def test_render_context_fallback_when_toon_disabled(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test fallback to markdown when TOON is disabled."""
        engine = CustomEngine()
        profile = sample_agent_profile.model_copy(deep=True)
        profile.prompt_config = PromptConfig(
            format="toon",
            enable_toon=False,
            fallback_format="markdown",
        )

        rendered, fmt = engine._render_context(sample_context_packet, profile)

        assert fmt == "markdown"
        assert "INTENT:" in rendered

    @pytest.mark.asyncio
    async def test_propose_with_thinking_policy_overrides_llm_config(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that thinking_policy overrides agent_profile.llm_config."""
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=json.dumps({
            "summary": "Plan from thinking policy",
            "context_refs_used": [],
            "actions": [{
                "capability_name": "tasks.list@v1",
                "args": {},
                "side_effect": "none",
                "requires_approval": False,
            }],
            "risk": {"level": "low", "reasons": []},
            "questions": [],
            "notes": "test",
            "validation": {"missing_info": [], "assumptions": []},
        }))

        engine = CustomEngine(llm_service=mock_llm)

        policy = ThinkingPolicy(
            model_id="gpt-5-turbo",
            reasoning_effort=ReasoningEffort.HIGH,
            max_tokens=8000,
            temperature=0.1,
            tier=2,
            tier_name="deep",
        )

        plan = await engine.propose(
            sample_context_packet,
            sample_agent_profile,
            thinking_policy=policy,
        )

        assert plan is not None
        assert plan.summary == "Plan from thinking policy"

        # Verify LLM was called with thinking_policy values, not profile values
        call_kwargs = mock_llm.generate.call_args
        assert call_kwargs.kwargs["model"] == "gpt-5-turbo"
        assert call_kwargs.kwargs["temperature"] == 0.1
        assert call_kwargs.kwargs["max_tokens"] == 8000
        assert call_kwargs.kwargs["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_propose_without_thinking_policy_uses_profile(
        self,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test that without thinking_policy, agent_profile.llm_config is used."""
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=json.dumps({
            "summary": "Plan from profile",
            "context_refs_used": [],
            "actions": [{
                "capability_name": "tasks.list@v1",
                "args": {},
                "side_effect": "none",
                "requires_approval": False,
            }],
            "risk": {"level": "low", "reasons": []},
            "questions": [],
            "notes": "test",
            "validation": {"missing_info": [], "assumptions": []},
        }))

        engine = CustomEngine(llm_service=mock_llm)

        plan = await engine.propose(
            sample_context_packet,
            sample_agent_profile,
        )

        assert plan is not None

        # Verify LLM was called with profile values
        call_kwargs = mock_llm.generate.call_args
        llm_cfg = sample_agent_profile.llm_config
        assert call_kwargs.kwargs["model"] == llm_cfg.model
        assert call_kwargs.kwargs["temperature"] == llm_cfg.temperature
        assert call_kwargs.kwargs["max_tokens"] == llm_cfg.max_tokens
