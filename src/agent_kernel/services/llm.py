"""LLM Service - Language model completions.

Provides a unified interface for calling various LLM providers:
- OpenAI (GPT-4, GPT-4o, etc.)
- Anthropic (Claude 3, Claude 3.5, etc.)
- Custom endpoints (OpenAI-compatible)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class LLMResponse:
    """Response from an LLM completion."""

    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = "stop"
    raw_response: dict = field(default_factory=dict)

    @property
    def estimated_cost_usd(self) -> float:
        """Estimate cost based on token counts.

        Uses approximate pricing - actual costs may vary.
        """
        # Approximate pricing per 1M tokens (input/output)
        pricing = {
            "gpt-4o": (2.50, 10.00),
            "gpt-4o-mini": (0.15, 0.60),
            "gpt-4-turbo": (10.00, 30.00),
            "gpt-4": (30.00, 60.00),
            "gpt-3.5-turbo": (0.50, 1.50),
            "claude-3-opus": (15.00, 75.00),
            "claude-3-sonnet": (3.00, 15.00),
            "claude-3-haiku": (0.25, 1.25),
            "claude-3-5-sonnet": (3.00, 15.00),
            "claude-sonnet-4": (3.00, 15.00),
            "gpt-4": (30.00, 60.00),
        }

        # Find matching model or use default
        model_lower = self.model.lower()
        input_price, output_price = (5.00, 15.00)  # Default pricing

        for model_key, prices in pricing.items():
            if model_key in model_lower:
                input_price, output_price = prices
                break

        input_cost = (self.input_tokens / 1_000_000) * input_price
        output_cost = (self.output_tokens / 1_000_000) * output_price

        return input_cost + output_cost


class LLMService(ABC):
    """Abstract base class for LLM services."""

    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        stop_sequences: list[str] | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        """Generate a completion from the LLM.

        Args:
            system_prompt: System/instruction prompt.
            user_prompt: User message/query.
            model: Model to use (provider-specific).
            temperature: Sampling temperature (0.0-2.0).
            max_tokens: Maximum tokens to generate.
            stop_sequences: Optional stop sequences.
            reasoning_effort: Reasoning effort level ("low", "medium", "high") for supported models.

        Returns:
            Generated text content.
        """

    @abstractmethod
    async def generate_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        stop_sequences: list[str] | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """Generate a completion with full metadata.

        Args:
            system_prompt: System/instruction prompt.
            user_prompt: User message/query.
            model: Model to use (provider-specific).
            temperature: Sampling temperature (0.0-2.0).
            max_tokens: Maximum tokens to generate.
            stop_sequences: Optional stop sequences.
            reasoning_effort: Reasoning effort level ("low", "medium", "high") for supported models.

        Returns:
            LLMResponse with content and metadata.
        """

    async def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream a completion from the LLM.

        Default implementation falls back to non-streaming.

        Args:
            system_prompt: System/instruction prompt.
            user_prompt: User message/query.
            model: Model to use.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens.

        Yields:
            Text chunks as they're generated.
        """
        result = await self.generate(
            system_prompt,
            user_prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        yield result


class OpenAILLMService(LLMService):
    """OpenAI LLM service using the openai package."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str = "gpt-4o",
        organization: str | None = None,
        circuit_breaker: Any | None = None,
    ) -> None:
        """Initialize OpenAI service.

        Args:
            api_key: OpenAI API key (or OPENAI_API_KEY env var).
            base_url: Custom base URL for API.
            default_model: Default model to use.
            organization: Optional organization ID.
            circuit_breaker: Optional CircuitBreaker for failure protection.
        """
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OpenAI API key required. Set OPENAI_API_KEY or pass api_key."
            )

        self._base_url = base_url
        self._default_model = default_model
        self._organization = organization
        self._client = None
        self._circuit_breaker = circuit_breaker

        logger.info(
            "openai_llm_service_initialized",
            default_model=default_model,
            has_base_url=bool(base_url),
        )

    def _get_client(self):
        """Lazy-load the OpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError(
                    "openai package required. Install with: pip install openai"
                )

            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                organization=self._organization,
            )
        return self._client

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        stop_sequences: list[str] | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        """Generate a completion from OpenAI."""
        response = await self.generate_with_metadata(
            system_prompt,
            user_prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stop_sequences=stop_sequences,
            reasoning_effort=reasoning_effort,
        )
        return response.content

    async def generate_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        stop_sequences: list[str] | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """Generate a completion with metadata from OpenAI."""
        from agent_kernel.core.errors import LLMCircuitOpenError

        # Check circuit breaker before making API call
        if self._circuit_breaker is not None and not self._circuit_breaker.allow_request():
            raise LLMCircuitOpenError(model or self._default_model)

        client = self._get_client()
        model = model or self._default_model

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        logger.debug(
            "openai_request",
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )

        request_args = {
            "model": model,
            "messages": messages,
        }
        # Suppress temperature for reasoning models (o1, o3, gpt-5, o3-mini)
        suppress_temp = model.startswith(("gpt-5", "o1", "o3", "o3-mini"))
        if not suppress_temp:
            request_args["temperature"] = temperature
        if stop_sequences:
            request_args["stop"] = stop_sequences
        # gpt-5 uses max_completion_tokens; all others use max_tokens
        if model.startswith("gpt-5"):
            request_args["max_completion_tokens"] = max_tokens
        else:
            request_args["max_tokens"] = max_tokens

        # Add reasoning effort for models that support it
        if reasoning_effort and model.startswith(("o1", "o3", "o3-mini")):
            request_args["reasoning_effort"] = reasoning_effort

        try:
            response = await client.chat.completions.create(**request_args)
        except Exception:
            if self._circuit_breaker is not None:
                self._circuit_breaker.record_failure()
            raise

        # Record success
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_success()

        content = response.choices[0].message.content or ""
        usage = response.usage

        logger.info(
            "openai_response",
            model=model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            finish_reason=response.choices[0].finish_reason,
        )

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            finish_reason=response.choices[0].finish_reason or "stop",
            raw_response=response.model_dump(),
        )

    async def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream a completion from OpenAI."""
        client = self._get_client()
        model = model or self._default_model

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        request_args = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        suppress_temp = model.startswith(("gpt-5", "o1", "o3", "o3-mini"))
        if not suppress_temp:
            request_args["temperature"] = temperature
        if model.startswith("gpt-5"):
            request_args["max_completion_tokens"] = max_tokens
        else:
            request_args["max_tokens"] = max_tokens

        stream = await client.chat.completions.create(**request_args)

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class AnthropicLLMService(LLMService):
    """Anthropic LLM service using the anthropic package."""

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "claude-sonnet-4-20250514",
        circuit_breaker: Any | None = None,
    ) -> None:
        """Initialize Anthropic service.

        Args:
            api_key: Anthropic API key (or ANTHROPIC_API_KEY env var).
            default_model: Default model to use.
            circuit_breaker: Optional CircuitBreaker for failure protection.
        """
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Anthropic API key required. "
                "Set ANTHROPIC_API_KEY or pass api_key."
            )

        self._default_model = default_model
        self._client = None
        self._circuit_breaker = circuit_breaker

        logger.info(
            "anthropic_llm_service_initialized",
            default_model=default_model,
        )

    def _get_client(self):
        """Lazy-load the Anthropic client."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError:
                raise ImportError(
                    "anthropic package required. "
                    "Install with: pip install anthropic"
                )

            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    # Mapping from reasoning_effort to Anthropic extended thinking budget
    THINKING_BUDGET_MAP = {
        "none": 0,
        "low": 0,
        "medium": 4000,
        "high": 10000,
    }

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        stop_sequences: list[str] | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        """Generate a completion from Anthropic."""
        response = await self.generate_with_metadata(
            system_prompt,
            user_prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stop_sequences=stop_sequences,
            reasoning_effort=reasoning_effort,
        )
        return response.content

    async def generate_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        stop_sequences: list[str] | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """Generate a completion with metadata from Anthropic."""
        from agent_kernel.core.errors import LLMCircuitOpenError

        # Check circuit breaker before making API call
        if self._circuit_breaker is not None and not self._circuit_breaker.allow_request():
            raise LLMCircuitOpenError(model or self._default_model)

        client = self._get_client()
        model = model or self._default_model

        logger.debug(
            "anthropic_request",
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )

        # Build request args
        request_args: dict[str, Any] = {
            "model": model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "max_tokens": max_tokens,
        }

        # Add optional parameters
        if stop_sequences:
            request_args["stop_sequences"] = stop_sequences

        # Extended thinking for supported models (claude-3.5 and above)
        budget_tokens = self.THINKING_BUDGET_MAP.get(reasoning_effort or "none", 0)
        if budget_tokens > 0:
            # Extended thinking requires temperature=1 (Anthropic requirement)
            request_args["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget_tokens,
            }
            # Temperature must be 1 when using extended thinking
            request_args["temperature"] = 1.0
        else:
            request_args["temperature"] = temperature

        try:
            response = await client.messages.create(**request_args)
        except Exception:
            if self._circuit_breaker is not None:
                self._circuit_breaker.record_failure()
            raise

        # Record success
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_success()

        # Extract text from content blocks (skip thinking blocks)
        content = ""
        thinking_content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text
            elif hasattr(block, "thinking"):
                # Extended thinking block - capture but don't include in output
                thinking_content = block.thinking

        logger.info(
            "anthropic_response",
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            used_extended_thinking=bool(thinking_content),
        )

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=(
                response.usage.input_tokens + response.usage.output_tokens
            ),
            finish_reason=response.stop_reason or "end_turn",
            raw_response=response.model_dump(),
        )

    async def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream a completion from Anthropic."""
        client = self._get_client()
        model = model or self._default_model

        async with client.messages.stream(
            model=model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        ) as stream:
            async for text in stream.text_stream:
                yield text


class CachedLLMService(LLMService):
    """LLM service wrapper that adds tier-aware semantic caching.

    Delegates to an inner LLMService and caches responses keyed by
    prompt hash, model, tier, and reasoning effort.
    """

    def __init__(
        self,
        inner: LLMService,
        cache: Any,  # LLMSemanticCache (avoid circular import)
        event_log: Any | None = None,
    ) -> None:
        from agent_kernel.services.llm_cache import LLMSemanticCache

        self._inner = inner
        self._cache: LLMSemanticCache = cache
        self._event_log = event_log
        self._tier: int = 1
        self._effort: str = "medium"
        # Expose default model from inner service
        self._default_model = getattr(inner, "_default_model", "gpt-4o")

    def set_tier_context(self, tier: int, reasoning_effort: str) -> None:
        """Set the current tier context for cache key computation."""
        self._tier = tier
        self._effort = reasoning_effort

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        stop_sequences: list[str] | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        response = await self.generate_with_metadata(
            system_prompt,
            user_prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stop_sequences=stop_sequences,
            reasoning_effort=reasoning_effort,
        )
        return response.content

    async def generate_with_metadata(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        stop_sequences: list[str] | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        effort = reasoning_effort or self._effort
        resolved_model = model or self._default_model
        prompt_hash = self._cache.compute_prompt_hash(system_prompt, user_prompt)

        # Cache lookup
        entry = self._cache.lookup(
            prompt_hash, resolved_model, self._tier, effort
        )
        if entry is not None:
            logger.debug(
                "llm_cache_hit",
                model=resolved_model,
                tier=self._tier,
                effort=effort,
            )
            if self._event_log:
                self._event_log.emit(
                    "llm_cache.hit",
                    source="cached_llm_service",
                    payload={"model": resolved_model, "tier": self._tier},
                )
            return LLMResponse(
                content=entry.response_content,
                model=entry.response_model,
                input_tokens=entry.input_tokens,
                output_tokens=entry.output_tokens,
                total_tokens=entry.total_tokens,
                finish_reason=entry.finish_reason,
            )

        # Cache miss — call inner
        logger.debug(
            "llm_cache_miss",
            model=resolved_model,
            tier=self._tier,
            effort=effort,
        )
        if self._event_log:
            self._event_log.emit(
                "llm_cache.miss",
                source="cached_llm_service",
                payload={"model": resolved_model, "tier": self._tier},
            )

        response = await self._inner.generate_with_metadata(
            system_prompt,
            user_prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stop_sequences=stop_sequences,
            reasoning_effort=reasoning_effort,
        )

        # Store in cache
        self._cache.store(
            prompt_hash, resolved_model, self._tier, effort, response
        )

        return response

    async def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Stream bypasses cache — delegate directly."""
        async for chunk in self._inner.stream(
            system_prompt,
            user_prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk


def create_llm_service(
    provider: str = "openai",
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> LLMService:
    """Create an LLM service for the specified provider.

    Args:
        provider: Provider name (openai, anthropic).
        api_key: Optional API key (falls back to env vars).
        model: Optional default model.
        base_url: Optional custom base URL (OpenAI only).

    Returns:
        Configured LLM service.

    Raises:
        ValueError: If provider is not supported.
    """
    provider_lower = provider.lower()

    if provider_lower == "openai":
        return OpenAILLMService(
            api_key=api_key,
            base_url=base_url,
            default_model=model or "gpt-4o",
        )
    if provider_lower == "anthropic":
        return AnthropicLLMService(
            api_key=api_key,
            default_model=model or "claude-sonnet-4-20250514",
        )
    raise ValueError(
        f"Unsupported LLM provider: {provider}. "
        "Supported: openai, anthropic"
    )
