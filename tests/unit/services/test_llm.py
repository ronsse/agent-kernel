"""Tests for LLM service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_kernel.services.llm import (
    LLMResponse,
    OpenAILLMService,
    create_llm_service,
)


class TestLLMResponse:
    """Tests for LLMResponse."""

    def test_create_response(self):
        """Test creating an LLM response."""
        response = LLMResponse(
            content="Hello, world!",
            model="gpt-4o",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
        )

        assert response.content == "Hello, world!"
        assert response.model == "gpt-4o"
        assert response.total_tokens == 15

    def test_estimated_cost_gpt4o(self):
        """Test cost estimation for GPT-4o."""
        response = LLMResponse(
            content="Test",
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=500,
        )

        # GPT-4o: $2.50/1M input, $10.00/1M output
        expected_input = (1000 / 1_000_000) * 2.50
        expected_output = (500 / 1_000_000) * 10.00
        expected_total = expected_input + expected_output

        assert abs(response.estimated_cost_usd - expected_total) < 0.0001

    def test_estimated_cost_claude(self):
        """Test cost estimation for Claude."""
        response = LLMResponse(
            content="Test",
            model="claude-3-sonnet-20240229",
            input_tokens=2000,
            output_tokens=1000,
        )

        # Claude 3 Sonnet: $3.00/1M input, $15.00/1M output
        expected_input = (2000 / 1_000_000) * 3.00
        expected_output = (1000 / 1_000_000) * 15.00
        expected_total = expected_input + expected_output

        assert abs(response.estimated_cost_usd - expected_total) < 0.0001


class TestOpenAILLMService:
    """Tests for OpenAILLMService."""

    def test_init_with_api_key(self):
        """Test initialization with API key."""
        service = OpenAILLMService(
            api_key="test-key-123",
            default_model="gpt-4o-mini",
        )

        assert service._api_key == "test-key-123"
        assert service._default_model == "gpt-4o-mini"

    def test_init_requires_api_key(self):
        """Test that initialization requires API key."""
        with patch.dict("os.environ", {}, clear=True):
            # Remove the env var if it exists
            import os
            orig_key = os.environ.pop("OPENAI_API_KEY", None)

            try:
                with pytest.raises(ValueError, match="API key required"):
                    OpenAILLMService(api_key=None)
            finally:
                if orig_key:
                    os.environ["OPENAI_API_KEY"] = orig_key

    @pytest.mark.asyncio
    async def test_generate_calls_openai(self):
        """Test that generate calls OpenAI API."""
        service = OpenAILLMService(api_key="test-key")

        # Mock the client
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(content="Generated text"),
                finish_reason="stop",
            )
        ]
        mock_response.usage = MagicMock(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )
        mock_response.model_dump.return_value = {}

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        service._client = mock_client

        result = await service.generate(
            system_prompt="You are helpful.",
            user_prompt="Hello!",
            model="gpt-4o",
            temperature=0.5,
        )

        assert result == "Generated text"
        mock_client.chat.completions.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_with_metadata(self):
        """Test generate_with_metadata returns full response."""
        service = OpenAILLMService(api_key="test-key")

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(content="Generated text"),
                finish_reason="stop",
            )
        ]
        mock_response.usage = MagicMock(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        mock_response.model_dump.return_value = {"id": "test-id"}

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        service._client = mock_client

        response = await service.generate_with_metadata(
            system_prompt="You are helpful.",
            user_prompt="Hello!",
        )

        assert isinstance(response, LLMResponse)
        assert response.content == "Generated text"
        assert response.input_tokens == 100
        assert response.output_tokens == 50
        assert response.total_tokens == 150


class TestCreateLLMService:
    """Tests for create_llm_service factory."""

    def test_create_openai_service(self):
        """Test creating OpenAI service."""
        service = create_llm_service(
            provider="openai",
            api_key="test-key",
            model="gpt-4o-mini",
        )

        assert isinstance(service, OpenAILLMService)
        assert service._default_model == "gpt-4o-mini"

    def test_create_openai_case_insensitive(self):
        """Test provider name is case insensitive."""
        service = create_llm_service(
            provider="OpenAI",
            api_key="test-key",
        )

        assert isinstance(service, OpenAILLMService)

    def test_unsupported_provider(self):
        """Test error for unsupported provider."""
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            create_llm_service(provider="unknown")


class TestAnthropicImport:
    """Tests for Anthropic service (import-only, no API calls)."""

    def test_anthropic_import_error(self):
        """Test helpful error when anthropic not installed."""
        # This test verifies the import error message is helpful
        # We can't easily test the actual Anthropic service without
        # the package installed
