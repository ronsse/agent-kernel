"""Integration tests for LLM service with real API calls.

These tests require valid API keys and make real API calls.
They are marked with pytest.mark.integration and skipped by default.

Run with: pytest tests/integration/test_llm_integration.py -v
"""

import os

import pytest

from agent_kernel.services.llm import (
    AnthropicLLMService,
    OpenAILLMService,
    create_llm_service,
)

# Skip if no API key available
pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)


class TestOpenAIIntegration:
    """Integration tests for OpenAI LLM service."""

    @pytest.mark.asyncio
    async def test_simple_generation(self):
        """Test a simple generation request."""
        service = OpenAILLMService()

        result = await service.generate(
            system_prompt="You are a helpful assistant. Be concise.",
            user_prompt="What is 2 + 2? Answer with just the number.",
            model="gpt-4o-mini",
            temperature=0.0,
            max_tokens=10,
        )

        assert "4" in result

    @pytest.mark.asyncio
    async def test_generation_with_metadata(self):
        """Test generation with full metadata."""
        service = OpenAILLMService()

        response = await service.generate_with_metadata(
            system_prompt="You are a helpful assistant.",
            user_prompt="Say 'hello' and nothing else.",
            model="gpt-4o-mini",
            temperature=0.0,
            max_tokens=20,
        )

        assert "hello" in response.content.lower()
        assert response.input_tokens > 0
        assert response.output_tokens > 0
        assert response.total_tokens > 0
        assert response.model == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_json_generation(self):
        """Test generating JSON output."""
        service = OpenAILLMService()

        result = await service.generate(
            system_prompt=(
                "You are a JSON generator. "
                "Always respond with valid JSON only, no markdown."
            ),
            user_prompt=(
                'Generate a JSON object with keys "name" and "age" '
                'for a person named Alice who is 30.'
            ),
            model="gpt-4o-mini",
            temperature=0.0,
            max_tokens=50,
        )

        import json
        parsed = json.loads(result)
        assert parsed["name"] == "Alice"
        assert parsed["age"] == 30

    @pytest.mark.asyncio
    async def test_streaming(self):
        """Test streaming generation."""
        service = OpenAILLMService()

        chunks = []
        async for chunk in service.stream(
            system_prompt="You are helpful.",
            user_prompt="Count from 1 to 5.",
            model="gpt-4o-mini",
            temperature=0.0,
            max_tokens=50,
        ):
            chunks.append(chunk)

        full_response = "".join(chunks)
        assert "1" in full_response
        assert "5" in full_response


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
class TestAnthropicIntegration:
    """Integration tests for Anthropic LLM service."""

    @pytest.mark.asyncio
    async def test_simple_generation(self):
        """Test a simple generation request."""
        service = AnthropicLLMService()

        result = await service.generate(
            system_prompt="You are a helpful assistant. Be concise.",
            user_prompt="What is 2 + 2? Answer with just the number.",
            model="claude-3-haiku-20240307",
            temperature=0.0,
            max_tokens=10,
        )

        assert "4" in result

    @pytest.mark.asyncio
    async def test_generation_with_metadata(self):
        """Test generation with full metadata."""
        service = AnthropicLLMService()

        response = await service.generate_with_metadata(
            system_prompt="You are a helpful assistant.",
            user_prompt="Say 'hello' and nothing else.",
            model="claude-3-haiku-20240307",
            temperature=0.0,
            max_tokens=20,
        )

        assert "hello" in response.content.lower()
        assert response.input_tokens > 0
        assert response.output_tokens > 0


class TestFactoryIntegration:
    """Integration tests for create_llm_service factory."""

    @pytest.mark.asyncio
    async def test_create_and_use_openai(self):
        """Test creating and using OpenAI service via factory."""
        service = create_llm_service(
            provider="openai",
            model="gpt-4o-mini",
        )

        result = await service.generate(
            system_prompt="Be concise.",
            user_prompt="What color is the sky? One word.",
            temperature=0.0,
            max_tokens=10,
        )

        assert "blue" in result.lower()
