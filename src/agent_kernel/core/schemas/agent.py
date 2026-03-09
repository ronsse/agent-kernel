"""Agent schemas - AgentProfile and related configuration types."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agent_kernel.core.schemas.base import KernelModel
from agent_kernel.core.schemas.context import ContextPolicy
from agent_kernel.core.schemas.llm import ReasoningEffort
from agent_kernel.core.schemas.plan import RiskLevel, SideEffect
from agent_kernel.core.schemas.thinking import ThinkingConfig


class ModelConfig(KernelModel):
    """LLM configuration for an agent.

    Supports multiple providers (OpenAI, Anthropic, Ollama, custom).
    """

    provider: str = "openai"  # openai | anthropic | ollama | custom
    model: str = "gpt-4o"
    temperature: float = Field(default=0.3, ge=0.0, le=1.0)
    max_tokens: int = 4096
    stop_sequences: list[str] = Field(default_factory=list)
    base_url: str | None = None  # Override for custom endpoints
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description="Reasoning effort level for thinking policy (OpenAI o-series, Anthropic extended thinking)",
    )


class PromptConfig(KernelModel):
    """Prompt serialization configuration for an agent."""

    format: Literal["markdown", "json", "toon", "mixed"] = "markdown"
    enable_toon: bool = True
    fallback_format: Literal["markdown", "json"] = "markdown"


class ApprovalPolicy(KernelModel):
    """Policy for action approval requirements."""

    require_approval_for: list[str] = Field(default_factory=list)  # Capability names
    auto_approve_side_effects: list[SideEffect] = Field(
        default_factory=lambda: [SideEffect.NONE, SideEffect.READ]
    )
    max_auto_approve_risk: RiskLevel = RiskLevel.LOW


class AgentProfile(KernelModel):
    """Strictly defines agent behavior without hard-coding.

    Each agent profile specifies:
    - Which LLM to use
    - Which capabilities are allowed
    - Context retrieval policy
    - Approval requirements
    - Thinking policy (v1.0.3)
    """

    agent_profile_id: str
    name: str
    description: str = ""
    engine: str = "custom"  # custom | langgraph | semantic_kernel
    llm_config: ModelConfig = Field(default_factory=ModelConfig)
    prompt_config: PromptConfig | None = Field(
        default=None,
        description="Optional prompt serialization configuration.",
    )
    allowed_capabilities: list[str] = Field(default_factory=list)
    context_policy: ContextPolicy = Field(default_factory=ContextPolicy)
    approval_policy: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    output_schema_version: str = "1.0.0"

    # Thinking policy (v1.0.3)
    thinking_config: ThinkingConfig | None = Field(
        default=None,
        description="Thinking policy configuration. If None, uses standard defaults.",
    )

    def can_use_capability(self, capability_name: str) -> bool:
        """Check if this agent can use a given capability."""
        # Match exact name or name without version
        base_name = capability_name.split("@")[0]
        return capability_name in self.allowed_capabilities or any(
            cap.startswith(base_name) for cap in self.allowed_capabilities
        )

    def requires_approval_for(self, capability_name: str) -> bool:
        """Check if a capability requires approval for this agent."""
        return capability_name in self.approval_policy.require_approval_for
