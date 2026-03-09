"""Thinking Policy schemas - ThinkingConfig and related types.

These schemas define how agents should think, including:
- Reasoning effort tiers
- Retrieval strategies
- Verification patterns
- Escalation policies
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agent_kernel.core.schemas.base import KernelModel


# Thinking tier levels
ThinkingTier = Literal[0, 1, 2, 3]

# Escalation triggers
EscalationTrigger = Literal[
    "schema_validation_failed",
    "quality_gates_failed",
    "low_confidence",
    "high_risk",
    "critic_rejection",
    "explicit_request",
    "timeout",
]


class ThinkingTierConfig(KernelModel):
    """Configuration for a single thinking tier.

    Each tier defines a combination of model, reasoning effort,
    and optional verification strategies.
    """

    tier: ThinkingTier = Field(description="Tier number (0-3)")
    name: str = Field(description="Human-readable tier name")
    description: str = Field(default="", description="What this tier is for")

    # Model configuration
    model: str = Field(default="gpt-4o", description="Model to use for this tier")
    reasoning_effort: Literal["none", "low", "medium", "high"] = Field(
        default="medium",
        description="Reasoning effort parameter for the model",
    )
    max_tokens: int = Field(default=2000, ge=100, description="Max output tokens")
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)

    # Verification options
    use_critic: bool = Field(
        default=False,
        description="Whether to run critic pass at this tier",
    )
    critic_model: str | None = Field(
        default=None,
        description="Model for critic (if different from main model)",
    )
    max_revisions: int = Field(
        default=2,
        ge=0,
        description="Max revision loops with critic",
    )

    # Context budget for this tier
    max_context_tokens: int = Field(
        default=4000,
        ge=100,
        description="Maximum context tokens at this tier",
    )

    # Approval requirement at this tier
    requires_approval_to_escalate: bool = Field(
        default=False,
        description="Require human approval before escalating FROM this tier",
    )


class RetrievalConfig(KernelModel):
    """Configuration for which retrieval strategies to use.

    These are toggles that control how context is gathered.
    """

    # Search strategies
    semantic_search: bool = Field(
        default=True,
        description="Use embedding-based semantic search",
    )
    keyword_search: bool = Field(
        default=True,
        description="Use FTS keyword search",
    )
    graph_expansion: bool = Field(
        default=False,
        description="Follow graph relationships to expand context",
    )
    graph_expansion_hops: int = Field(
        default=1,
        ge=1,
        le=3,
        description="Number of hops for graph expansion",
    )

    # Boosting
    recency_boost: bool = Field(
        default=True,
        description="Boost recently modified items",
    )
    recency_days: int = Field(
        default=7,
        description="Days to consider 'recent'",
    )

    # Iterative retrieval
    iterative_retrieval: bool = Field(
        default=False,
        description="Allow LLM to request more context mid-reasoning",
    )
    max_retrieval_iterations: int = Field(
        default=3,
        ge=1,
        description="Maximum retrieval iterations if iterative",
    )


class VerificationConfig(KernelModel):
    """Configuration for verification/validation strategies."""

    # Critic pattern
    use_critic: bool = Field(
        default=False,
        description="Use solver+critic two-pass pattern",
    )
    critic_model: str | None = Field(
        default=None,
        description="Model for critic (None = use same model)",
    )
    max_revisions: int = Field(
        default=2,
        ge=0,
        description="Max revision loops with critic",
    )
    require_critic_approval: bool = Field(
        default=False,
        description="Require human approval if critic finds issues",
    )

    # Multi-candidate pattern
    generate_candidates: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Number of candidate plans to generate (1=single pass)",
    )


class EscalationConfig(KernelModel):
    """Configuration for automatic thinking escalation."""

    enabled: bool = Field(
        default=True,
        description="Whether automatic escalation is enabled",
    )
    start_tier: ThinkingTier = Field(
        default=1,
        description="Default starting tier",
    )
    max_tier: ThinkingTier = Field(
        default=2,
        description="Maximum tier to escalate to",
    )
    max_escalations: int = Field(
        default=2,
        ge=0,
        description="Maximum number of escalation attempts",
    )

    # Which triggers cause escalation
    triggers: list[EscalationTrigger] = Field(
        default_factory=lambda: [
            "schema_validation_failed",
            "quality_gates_failed",
            "low_confidence",
        ],
        description="Conditions that trigger escalation",
    )

    # Confidence threshold
    confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Confidence below this triggers escalation",
    )

    # Adaptive behavior thresholds
    high_escalation_rate_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="If workflow escalation rate exceeds this, start at higher tier",
    )
    low_success_rate_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="If workflow success rate is below this, consider model change",
    )
    model_success_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Minimum model success rate for routing recommendations",
    )

    # Human-in-the-loop options
    require_approval_to_escalate: bool = Field(
        default=False,
        description="Require human approval before each escalation",
    )
    require_approval_for_tier_3: bool = Field(
        default=False,
        description="Always require approval before using tier 3",
    )


class QualityGatesConfig(KernelModel):
    """Configuration for quality gates."""

    # Which gates to run
    coverage_gate: bool = Field(
        default=True,
        description="Check for minimum coverage of entity types",
    )
    recency_gate: bool = Field(
        default=True,
        description="Check for recent items when requested",
    )
    parity_gate: bool = Field(
        default=True,
        description="Check index freshness against canonical source",
    )
    pack_presence_gate: bool = Field(
        default=True,
        description="Check that required context packs are included",
    )
    schema_aware_gate: bool = Field(
        default=True,
        description="Validate filters against source schemas",
    )

    # Coverage requirements
    min_notes: int = Field(default=1, ge=0)
    min_tasks: int = Field(default=0, ge=0)
    min_events: int = Field(default=0, ge=0)


class ThinkingConfig(KernelModel):
    """Master configuration for agent thinking behavior.

    This is attached to AgentProfile and controls:
    - How the agent retrieves context
    - How deeply the agent reasons
    - How the agent verifies its work
    - When and how the agent escalates

    All features are configurable and composable.
    """

    # Overall mode
    mode: Literal["standard", "deep", "adaptive"] = Field(
        default="standard",
        description=(
            "standard: fixed tier reasoning, "
            "deep: always use max tier, "
            "adaptive: auto-escalate based on evidence"
        ),
    )

    # Tier definitions
    tiers: dict[int, ThinkingTierConfig] = Field(
        default_factory=lambda: {
            0: ThinkingTierConfig(
                tier=0,
                name="routing",
                description="Classification, routing, simple extraction",
                model="gpt-4o-mini",
                reasoning_effort="low",
                max_tokens=500,
            ),
            1: ThinkingTierConfig(
                tier=1,
                name="standard",
                description="Normal planning, most tasks",
                model="gpt-4o",
                reasoning_effort="medium",
                max_tokens=2000,
            ),
            2: ThinkingTierConfig(
                tier=2,
                name="deep",
                description="Complex analysis, ambiguous tasks",
                model="gpt-4o",
                reasoning_effort="high",
                max_tokens=4000,
            ),
            3: ThinkingTierConfig(
                tier=3,
                name="deep_with_critic",
                description="High stakes, requires verification",
                model="gpt-4o",
                reasoning_effort="high",
                max_tokens=4000,
                use_critic=True,
            ),
        },
        description="Tier configurations",
    )

    # Sub-configurations
    retrieval: RetrievalConfig = Field(
        default_factory=RetrievalConfig,
        description="Retrieval strategy configuration",
    )
    verification: VerificationConfig = Field(
        default_factory=VerificationConfig,
        description="Verification/critic configuration",
    )
    escalation: EscalationConfig = Field(
        default_factory=EscalationConfig,
        description="Escalation policy configuration",
    )
    gates: QualityGatesConfig = Field(
        default_factory=QualityGatesConfig,
        description="Quality gate configuration",
    )

    def get_tier_config(self, tier: ThinkingTier) -> ThinkingTierConfig:
        """Get configuration for a specific tier."""
        if tier in self.tiers:
            return self.tiers[tier]
        # Fallback to standard tier
        return self.tiers.get(1, ThinkingTierConfig(tier=1, name="standard"))

    def get_starting_tier(self) -> ThinkingTier:
        """Get the starting tier based on mode."""
        if self.mode == "deep":
            return self.escalation.max_tier
        return self.escalation.start_tier

    def should_escalate(
        self,
        current_tier: ThinkingTier,
        trigger: EscalationTrigger,
        confidence: float | None = None,
    ) -> bool:
        """Determine if escalation should happen based on trigger."""
        if not self.escalation.enabled:
            return False

        if current_tier >= self.escalation.max_tier:
            return False

        if trigger not in self.escalation.triggers:
            return False

        if trigger == "low_confidence" and confidence is not None:
            return confidence < self.escalation.confidence_threshold

        return True


# Predefined thinking configs for common use cases
STANDARD_THINKING = ThinkingConfig(
    mode="standard",
    escalation=EscalationConfig(enabled=False),
)

DEEP_THINKING = ThinkingConfig(
    mode="deep",
    escalation=EscalationConfig(
        enabled=True,
        start_tier=2,
        max_tier=3,
    ),
    verification=VerificationConfig(use_critic=True),
    retrieval=RetrievalConfig(
        graph_expansion=True,
        semantic_search=True,
    ),
)

ADAPTIVE_THINKING = ThinkingConfig(
    mode="adaptive",
    escalation=EscalationConfig(
        enabled=True,
        start_tier=1,
        max_tier=3,
        triggers=[
            "schema_validation_failed",
            "quality_gates_failed",
            "low_confidence",
            "high_risk",
        ],
    ),
    retrieval=RetrievalConfig(
        semantic_search=True,
        graph_expansion=True,
    ),
)
