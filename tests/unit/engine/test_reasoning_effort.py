"""Tests for reasoning_effort wiring through the engine to LLM service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_kernel.core.schemas import AgentProfile, ContextPacket, ModelConfig
from agent_kernel.engine.custom_engine import CustomEngine


class TestReasoningEffortWiring:
    """Tests for reasoning_effort being passed from agent profile to LLM."""

    @pytest.fixture
    def mock_llm_service(self):
        """Create a mock LLM service."""
        service = AsyncMock()
        service.generate = AsyncMock(
            return_value='{"summary": "Test plan", "actions": [], "context_refs_used": [], "risk": {"level": "low", "reasons": []}, "questions": [], "notes": "", "validation": {"missing_info": [], "assumptions": []}}'
        )
        return service

    @pytest.fixture
    def context_packet(self):
        """Create a minimal context packet."""
        return ContextPacket(
            intent="Test intent",
            items=[],
        )

    @pytest.fixture
    def agent_profile_with_reasoning(self):
        """Create an agent profile with reasoning_effort set."""
        return AgentProfile(
            agent_profile_id="test_agent",
            name="Test Agent",
            llm_config=ModelConfig(
                model="gpt-4o",
                temperature=0.3,
                max_tokens=2000,
                reasoning_effort="high",
            ),
            allowed_capabilities=["test.cap@v1"],
        )

    @pytest.fixture
    def agent_profile_no_reasoning(self):
        """Create an agent profile without reasoning_effort."""
        return AgentProfile(
            agent_profile_id="test_agent",
            name="Test Agent",
            llm_config=ModelConfig(
                model="gpt-4o",
                temperature=0.3,
                max_tokens=2000,
            ),
            allowed_capabilities=["test.cap@v1"],
        )

    @pytest.mark.asyncio
    async def test_reasoning_effort_passed_to_llm_service(
        self,
        mock_llm_service,
        context_packet,
        agent_profile_with_reasoning,
    ):
        """Test that reasoning_effort from agent profile is passed to LLM."""
        engine = CustomEngine(llm_service=mock_llm_service)

        await engine.propose(context_packet, agent_profile_with_reasoning)

        # Verify generate was called with reasoning_effort
        mock_llm_service.generate.assert_called_once()
        call_kwargs = mock_llm_service.generate.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "high"
        assert call_kwargs["model"] == "gpt-4o"
        assert call_kwargs["temperature"] == 0.3
        assert call_kwargs["max_tokens"] == 2000

    @pytest.mark.asyncio
    async def test_no_reasoning_effort_passes_none(
        self,
        mock_llm_service,
        context_packet,
        agent_profile_no_reasoning,
    ):
        """Test that None reasoning_effort is passed when not set."""
        engine = CustomEngine(llm_service=mock_llm_service)

        await engine.propose(context_packet, agent_profile_no_reasoning)

        # Verify generate was called with reasoning_effort=None
        mock_llm_service.generate.assert_called_once()
        call_kwargs = mock_llm_service.generate.call_args.kwargs
        assert call_kwargs["reasoning_effort"] is None

    @pytest.mark.asyncio
    async def test_reasoning_effort_levels(
        self,
        mock_llm_service,
        context_packet,
    ):
        """Test all reasoning effort levels are passed correctly."""
        engine = CustomEngine(llm_service=mock_llm_service)

        for level in ["none", "low", "medium", "high"]:
            mock_llm_service.generate.reset_mock()

            profile = AgentProfile(
                agent_profile_id="test_agent",
                name="Test Agent",
                llm_config=ModelConfig(
                    model="o1-preview",  # Model that supports reasoning
                    reasoning_effort=level,
                ),
                allowed_capabilities=["test.cap@v1"],
            )

            await engine.propose(context_packet, profile)

            call_kwargs = mock_llm_service.generate.call_args.kwargs
            assert call_kwargs["reasoning_effort"] == level, f"Failed for level: {level}"


class TestModelConfigReasoningEffort:
    """Tests for ModelConfig reasoning_effort field."""

    def test_model_config_default_no_reasoning(self):
        """Test that ModelConfig defaults to no reasoning_effort."""
        config = ModelConfig()
        assert config.reasoning_effort is None

    def test_model_config_with_reasoning(self):
        """Test ModelConfig with reasoning_effort set."""
        config = ModelConfig(
            model="o1-preview",
            reasoning_effort="high",
        )
        assert config.reasoning_effort == "high"

    def test_model_config_serialization(self):
        """Test ModelConfig serializes reasoning_effort correctly."""
        config = ModelConfig(
            model="gpt-4o",
            reasoning_effort="medium",
        )
        data = config.model_dump()
        assert data["reasoning_effort"] == "medium"

    def test_model_config_from_dict(self):
        """Test ModelConfig can be created from dict with reasoning_effort."""
        data = {
            "provider": "openai",
            "model": "o1-preview",
            "temperature": 0.5,
            "max_tokens": 8192,
            "reasoning_effort": "high",
        }
        config = ModelConfig(**data)
        assert config.reasoning_effort == "high"
        assert config.model == "o1-preview"


class TestAnthropicReasoningEffort:
    """Tests for Anthropic extended thinking integration."""

    def test_thinking_budget_mapping(self):
        """Test reasoning_effort maps to correct budget tokens."""
        from agent_kernel.services.llm import AnthropicLLMService

        # Check the mapping exists and has correct values
        assert AnthropicLLMService.THINKING_BUDGET_MAP["none"] == 0
        assert AnthropicLLMService.THINKING_BUDGET_MAP["low"] == 0
        assert AnthropicLLMService.THINKING_BUDGET_MAP["medium"] == 4000
        assert AnthropicLLMService.THINKING_BUDGET_MAP["high"] == 10000

    @pytest.mark.asyncio
    async def test_anthropic_extended_thinking_enabled(self):
        """Test that extended thinking is enabled for high reasoning_effort."""
        with patch("agent_kernel.services.llm.AnthropicLLMService._get_client") as mock_get:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="Test response")]
            mock_response.usage = MagicMock(input_tokens=10, output_tokens=20)
            mock_response.stop_reason = "end_turn"
            mock_response.model_dump = MagicMock(return_value={})
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_client

            from agent_kernel.services.llm import AnthropicLLMService

            # Create service with a mock API key
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                service = AnthropicLLMService()

            await service.generate(
                system_prompt="You are helpful",
                user_prompt="Hello",
                reasoning_effort="high",
            )

            # Verify thinking parameter was passed
            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert "thinking" in call_kwargs
            assert call_kwargs["thinking"]["type"] == "enabled"
            assert call_kwargs["thinking"]["budget_tokens"] == 10000
            # Temperature must be 1.0 for extended thinking
            assert call_kwargs["temperature"] == 1.0

    @pytest.mark.asyncio
    async def test_anthropic_no_extended_thinking_for_low(self):
        """Test that extended thinking is disabled for low reasoning_effort."""
        with patch("agent_kernel.services.llm.AnthropicLLMService._get_client") as mock_get:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="Test response")]
            mock_response.usage = MagicMock(input_tokens=10, output_tokens=20)
            mock_response.stop_reason = "end_turn"
            mock_response.model_dump = MagicMock(return_value={})
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_client

            from agent_kernel.services.llm import AnthropicLLMService

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                service = AnthropicLLMService()

            await service.generate(
                system_prompt="You are helpful",
                user_prompt="Hello",
                reasoning_effort="low",
            )

            # Verify thinking parameter was NOT passed
            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert "thinking" not in call_kwargs
            # Temperature should be the default (0.3)
            assert call_kwargs["temperature"] == 0.3
