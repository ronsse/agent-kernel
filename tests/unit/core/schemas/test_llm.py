"""Unit tests for LLM call schemas."""


import pytest

from agent_kernel.core.schemas.base import SCHEMA_VERSION
from agent_kernel.core.schemas.llm import (
    LLMCallRecord,
    LLMRequest,
    LLMResponse,
)
from agent_kernel.core.schemas.trace import CostRecord


class TestLLMRequest:
    """Tests for LLMRequest schema."""

    def test_basic_creation(self) -> None:
        """Test creating a basic LLMRequest."""
        request = LLMRequest(
            model="gpt-4o",
            provider="openai",
            messages=[{"role": "user", "content": "Hello"}],
        )
        assert request.model == "gpt-4o"
        assert request.provider == "openai"
        assert len(request.messages) == 1
        assert request.temperature == 0.3  # Default
        assert request.reasoning_effort is None

    def test_with_reasoning_effort(self) -> None:
        """Test LLMRequest with reasoning effort parameter."""
        request = LLMRequest(
            model="o1-mini",
            provider="openai",
            messages=[],
            reasoning_effort="high",
        )
        assert request.reasoning_effort == "high"

    def test_temperature_validation(self) -> None:
        """Test temperature bounds validation."""
        with pytest.raises(ValueError):
            LLMRequest(
                model="gpt-4o",
                provider="openai",
                messages=[],
                temperature=3.0,  # Too high
            )


class TestLLMResponse:
    """Tests for LLMResponse schema."""

    def test_basic_creation(self) -> None:
        """Test creating a basic LLMResponse."""
        response = LLMResponse(
            model="gpt-4o",
            provider="openai",
            output_text="Hello!",
        )
        assert response.model == "gpt-4o"
        assert response.output_text == "Hello!"
        assert response.usage is None

    def test_with_usage(self) -> None:
        """Test LLMResponse with usage tracking."""
        usage = CostRecord(
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            estimated_cost_usd=0.01,
        )
        response = LLMResponse(
            model="gpt-4o",
            provider="openai",
            output_text="Response",
            usage=usage,
        )
        assert response.usage.total_tokens == 150
        assert response.usage.estimated_cost_usd == 0.01


class TestLLMCallRecord:
    """Tests for LLMCallRecord schema."""

    def test_basic_creation(self) -> None:
        """Test creating a basic LLMCallRecord."""
        request = LLMRequest(model="gpt-4o", provider="openai", messages=[])
        response = LLMResponse(model="gpt-4o", provider="openai")

        record = LLMCallRecord(
            trace_id="trace_123",
            stage="propose_plan",
            duration_ms=1000,
            request=request,
            response=response,
        )

        assert record.trace_id == "trace_123"
        assert record.stage == "propose_plan"
        assert record.duration_ms == 1000
        assert record.tier == 1  # Default
        assert record.schema_version == SCHEMA_VERSION

    def test_escalation_tracking(self) -> None:
        """Test escalation tracking fields."""
        request = LLMRequest(model="gpt-4o", provider="openai", messages=[])
        response = LLMResponse(model="gpt-4o", provider="openai")

        record = LLMCallRecord(
            trace_id="trace_123",
            stage="revise",
            duration_ms=500,
            request=request,
            response=response,
            tier=2,
            escalated_from="llm_call_001",
            escalation_reason="confidence below threshold",
        )

        assert record.tier == 2
        assert record.escalated_from == "llm_call_001"
        assert record.escalation_reason == "confidence below threshold"

    def test_total_tokens_property(self) -> None:
        """Test the total_tokens property."""
        request = LLMRequest(model="gpt-4o", provider="openai", messages=[])
        usage = CostRecord(total_tokens=500)
        response = LLMResponse(model="gpt-4o", provider="openai", usage=usage)

        record = LLMCallRecord(
            trace_id="trace_123",
            stage="propose_plan",
            duration_ms=100,
            request=request,
            response=response,
        )

        assert record.total_tokens == 500

    def test_estimated_cost_property(self) -> None:
        """Test the estimated_cost_usd property."""
        request = LLMRequest(model="gpt-4o", provider="openai", messages=[])
        usage = CostRecord(estimated_cost_usd=0.05)
        response = LLMResponse(model="gpt-4o", provider="openai", usage=usage)

        record = LLMCallRecord(
            trace_id="trace_123",
            stage="propose_plan",
            duration_ms=100,
            request=request,
            response=response,
        )

        assert record.estimated_cost_usd == 0.05
