"""LLM Call schemas - LLMRequest, LLMResponse, LLMCallRecord.

These schemas provide first-class LLM call tracing for debugging,
cost tracking, and thinking policy analysis.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import KernelModel, VersionedModel, utc_now
from agent_kernel.core.schemas.trace import CostRecord

# Reasoning effort levels for thinking policy
ReasoningEffort = Literal["none", "low", "medium", "high"]


class LLMRequest(KernelModel):
    """Request sent to an LLM provider.

    Captures the full request parameters for tracing and replay.
    Messages may be redacted before persistence per privacy policy.
    """

    model: str = Field(description="Model identifier (e.g., 'gpt-4o')")
    provider: str = Field(description="Provider name (e.g., 'openai', 'anthropic')")
    messages: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Chat messages (may be redacted/hashed before persistence)",
    )
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, description="Maximum output tokens")
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description="Reasoning effort level for thinking policy (OpenAI o-series)",
    )
    response_schema_name: str | None = Field(
        default=None,
        description="Name of expected response schema (e.g., 'Plan')",
    )
    stop_sequences: list[str] = Field(
        default_factory=list,
        description="Stop sequences for generation",
    )
    extra_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific extra parameters",
    )


class LLMResponse(KernelModel):
    """Response from an LLM provider.

    Captures the response content and metadata for analysis.
    """

    model: str = Field(description="Actual model used (may differ from request)")
    provider: str = Field(description="Provider name")
    output_text: str | None = Field(
        default=None,
        description="Raw text output from the model",
    )
    parsed: dict[str, Any] | None = Field(
        default=None,
        description="Structured output if response schema was used",
    )
    finish_reason: str | None = Field(
        default=None,
        description="Why generation stopped (e.g., 'stop', 'length', 'tool_calls')",
    )
    usage: CostRecord | None = Field(
        default=None,
        description="Token usage and cost information",
    )
    latency_ms: int | None = Field(
        default=None,
        description="Response latency in milliseconds",
    )
    raw_response: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw provider response (may be redacted)",
    )


class LLMCallRecord(VersionedModel):
    """Record of an LLM call for tracing and analysis.

    Captures the full request/response cycle with timing and metadata.
    Essential for:
    - Debugging "why did it plan that"
    - Tuning escalation policies
    - Cost tracking
    - Evals and analysis
    """

    llm_call_id: str = Field(default_factory=generate_ulid)
    trace_id: str = Field(description="Associated DecisionTrace ID")
    stage: Literal["routing", "propose_plan", "critic", "revise", "other"] = Field(
        default="propose_plan",
        description="Stage in the reasoning pipeline",
    )
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime = Field(default_factory=utc_now)
    duration_ms: int = Field(default=0, ge=0)
    request: LLMRequest = Field(description="The request sent to the LLM")
    response: LLMResponse = Field(description="The response received from the LLM")
    request_hash: str | None = Field(
        default=None,
        description="Hash of request content for deduplication/privacy",
    )
    response_hash: str | None = Field(
        default=None,
        description="Hash of response content for integrity checking",
    )
    tier: int = Field(
        default=1,
        description="Thinking tier used (0=routing, 1=standard, 2=deep, 3=deep+critic)",
    )
    escalated_from: str | None = Field(
        default=None,
        description="ID of previous LLMCallRecord if this was an escalation",
    )
    escalation_reason: str | None = Field(
        default=None,
        description="Reason for escalation if applicable",
    )

    @property
    def total_tokens(self) -> int:
        """Get total tokens used in this call."""
        if self.response.usage and self.response.usage.total_tokens:
            return self.response.usage.total_tokens
        return 0

    @property
    def estimated_cost_usd(self) -> float:
        """Get estimated cost in USD."""
        if self.response.usage and self.response.usage.estimated_cost_usd:
            return self.response.usage.estimated_cost_usd
        return 0.0
