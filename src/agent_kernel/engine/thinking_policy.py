"""ThinkingPolicyController - Manages thinking tiers and escalation.

The ThinkingPolicyController decides how much cognition to buy for each task.
It implements the "Attempt → Gate → Escalate" pattern for efficient reasoning.

Key responsibilities:
- Determine starting tier based on intent and risk
- Manage automatic escalation based on quality gate results
- Coordinate critic passes when configured
- Track reasoning metadata for traces
- Optionally require human approval for escalation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

import structlog

from agent_kernel.core.schemas.thinking import (
    STANDARD_THINKING,
    EscalationTrigger as EscalationTriggerType,
    ThinkingConfig,
    ThinkingTier as ThinkingTierLevel,
    ThinkingTierConfig,
)
from agent_kernel.core.schemas.trace import ReasoningMetadata

if TYPE_CHECKING:
    from agent_kernel.core.schemas import AgentProfile, ContextPacket, Plan
    from agent_kernel.core.schemas.retrieval import RetrievalQualityReport
    from agent_kernel.engine.critic import Critique

logger = structlog.get_logger(__name__)


class ReasoningEffort(str, Enum):
    """Legacy reasoning effort enum for compatibility."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

@dataclass
class ThinkingTier:
    """Legacy thinking tier configuration (compatibility)."""

    tier: int
    name: str
    description: str
    model: str
    reasoning_effort: ReasoningEffort
    max_tokens: int
    run_critic: bool
    generate_candidates: int

    @classmethod
    def from_dict(cls, data: dict[str, Any], tier_index: int) -> ThinkingTier:
        """Create tier config from dictionary with defaults."""
        name = data.get("name", f"tier_{tier_index}")
        description = data.get("description", "")
        model = data.get("model", "gpt-4o")
        effort_str = str(data.get("reasoning_effort", "medium")).lower()
        effort_map = {
            "none": ReasoningEffort.NONE,
            "low": ReasoningEffort.LOW,
            "medium": ReasoningEffort.MEDIUM,
            "high": ReasoningEffort.HIGH,
        }
        reasoning_effort = effort_map.get(effort_str, ReasoningEffort.MEDIUM)
        max_tokens = int(data.get("max_tokens", 2000))
        run_critic = bool(data.get("run_critic", False))
        generate_candidates = int(data.get("generate_candidates", 1))

        return cls(
            tier=tier_index,
            name=name,
            description=description,
            model=model,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
            run_critic=run_critic,
            generate_candidates=generate_candidates,
        )


@dataclass
class PriorAttempt:
    """Legacy escalation attempt summary for compatibility."""

    tier: int
    succeeded: bool
    gate_failures: list[str] = field(default_factory=list)
    error: str | None = None
    confidence: float | None = None


@dataclass
class EscalationTrigger:
    """Legacy escalation trigger configuration (compatibility)."""

    schema_validation_failed: bool = True
    quality_gates_failed: bool = True
    confidence_below_threshold: float = 0.7
    risk_level_high: bool = True
    explicit_deep_analysis: bool = True


@dataclass
class ThinkingPolicy:
    """Output of ThinkingPolicyController - what settings to use for this attempt."""

    # Model configuration
    model_id: str
    reasoning_effort: ReasoningEffort
    max_tokens: int

    # Current tier info
    tier: ThinkingTierLevel
    tier_name: str
    temperature: float = 0.3

    # Verification settings
    run_critic: bool = False
    run_critic_pass: bool = False
    critic_model: str | None = None
    max_revisions: int = 2
    generate_candidates: int = 1

    # Context budget
    max_context_tokens: int = 4000

    # Escalation info
    escalation_reason: str | None = None
    escalated_from: ThinkingTier | None = None

    # Approval requirements
    requires_approval_to_escalate: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.reasoning_effort, str):
            try:
                self.reasoning_effort = ReasoningEffort(self.reasoning_effort.lower())
            except ValueError:
                self.reasoning_effort = ReasoningEffort.MEDIUM
        if self.run_critic_pass and not self.run_critic:
            self.run_critic = True

    def to_dict(self) -> dict[str, Any]:
        """Legacy serialization for tests."""
        return {
            "tier": int(self.tier) if isinstance(self.tier, int) else self.tier,
            "tier_name": self.tier_name,
            "model_id": self.model_id,
            "reasoning_effort": self.reasoning_effort.value
            if hasattr(self.reasoning_effort, "value")
            else str(self.reasoning_effort),
            "max_tokens": self.max_tokens,
            "run_critic_pass": self.run_critic_pass,
            "generate_candidates": self.generate_candidates,
            "escalation_reason": self.escalation_reason,
        }


@dataclass
class EscalationAttempt:
    """Record of an escalation attempt."""

    tier: ThinkingTierLevel
    trigger: EscalationTriggerType
    success: bool
    details: str = ""


@dataclass
class ThinkingSession:
    """Tracks state across multiple thinking attempts within one decision.

    This is used by the ThinkingPolicyController to track escalation
    history and make informed decisions about further escalation.
    """

    # Configuration
    config: ThinkingConfig

    # State
    current_tier: ThinkingTierLevel = 1
    attempts: list[EscalationAttempt] = field(default_factory=list)
    gate_failures: list[str] = field(default_factory=list)
    gate_warnings: list[str] = field(default_factory=list)
    critic_issues: list[str] = field(default_factory=list)
    escalation_count: int = 0

    # Approval tracking
    pending_approval_callback: Callable[..., bool] | None = None
    approval_granted: bool = True

    def can_escalate(self) -> bool:
        """Check if further escalation is possible."""
        if not self.config.escalation.enabled:
            return False
        if self.current_tier >= self.config.escalation.max_tier:
            return False
        if self.escalation_count >= self.config.escalation.max_escalations:
            return False
        return True

    def record_attempt(
        self,
        trigger: EscalationTriggerType,
        success: bool,
        details: str = "",
    ) -> None:
        """Record an escalation attempt."""
        self.attempts.append(
            EscalationAttempt(
                tier=self.current_tier,
                trigger=trigger,
                success=success,
                details=details,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize session state for checkpoint persistence.

        Excludes non-serializable fields (e.g., approval callback).
        """
        return {
            "current_tier": self.current_tier,
            "escalation_count": self.escalation_count,
            "gate_failures": list(self.gate_failures),
            "gate_warnings": list(self.gate_warnings),
            "critic_issues": list(self.critic_issues),
            "approval_granted": self.approval_granted,
            "attempts": [
                {
                    "tier": a.tier,
                    "trigger": a.trigger,
                    "success": a.success,
                    "details": a.details,
                }
                for a in self.attempts
            ],
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        config: ThinkingConfig,
        approval_callback: Callable[..., bool] | None = None,
    ) -> ThinkingSession:
        """Restore a ThinkingSession from checkpoint data."""
        session = cls(
            config=config,
            current_tier=data.get("current_tier", 1),
            escalation_count=data.get("escalation_count", 0),
            gate_failures=data.get("gate_failures", []),
            gate_warnings=data.get("gate_warnings", []),
            critic_issues=data.get("critic_issues", []),
            approval_granted=data.get("approval_granted", True),
            pending_approval_callback=approval_callback,
        )
        for a in data.get("attempts", []):
            session.attempts.append(
                EscalationAttempt(
                    tier=a["tier"],
                    trigger=a["trigger"],
                    success=a["success"],
                    details=a.get("details", ""),
                )
            )
        return session

    def to_reasoning_metadata(self) -> ReasoningMetadata:
        """Convert session to ReasoningMetadata for traces."""
        tier_config = self.config.get_tier_config(self.current_tier)
        return ReasoningMetadata(
            initial_tier=self.config.get_starting_tier(),
            final_tier=self.current_tier,
            tier_name=tier_config.name,
            model_id=tier_config.model,
            reasoning_effort=tier_config.reasoning_effort,
            total_attempts=len(self.attempts) + 1,
            escalation_count=self.escalation_count,
            escalation_reasons=[a.details for a in self.attempts if a.success],
            gate_failures=self.gate_failures,
            gate_warnings=self.gate_warnings,
            critic_used=tier_config.use_critic,
            critic_issues=self.critic_issues,
        )


class ThinkingPolicyController:
    """Controls thinking policy decisions for agent reasoning.

    The controller implements the "Attempt → Gate → Escalate" pattern:
    1. Start with a baseline tier (usually tier 1)
    2. Generate a plan
    3. Run quality gates
    4. If gates fail and escalation is allowed, escalate to higher tier
    5. Repeat until success or max tier reached

    Key features:
    - Autonomous escalation based on quality gate results
    - Optional human-in-the-loop approval for escalation
    - Critic integration for high-reliability tasks
    - Full tracing of reasoning decisions
    """

    def __init__(
        self,
        default_config: ThinkingConfig | None = None,
        approval_callback: Callable[[str, ThinkingTier, ThinkingTier], bool]
        | None = None,
        config_path: str | None = None,
    ) -> None:
        """Initialize the controller.

        Args:
            default_config: Default thinking config when agent doesn't specify one.
            approval_callback: Optional callback for human-in-the-loop approval.
                Signature: (reason, current_tier, target_tier) -> approved
            config_path: Optional legacy YAML config path for tests.
        """
        self._default_config = default_config or STANDARD_THINKING
        self._approval_callback = approval_callback
        self.available_tiers: dict[int, ThinkingTier] = {}
        self.escalation_triggers = EscalationTrigger()

        if config_path:
            self._load_legacy_config(config_path)
        else:
            self._load_default_legacy_tiers()

        logger.info(
            "thinking_policy_controller_initialized",
            default_mode=self._default_config.mode,
        )

    def create_session(
        self,
        agent_profile: AgentProfile,
    ) -> ThinkingSession:
        """Create a new thinking session for a decision.

        Args:
            agent_profile: The agent profile (may contain thinking_config).

        Returns:
            ThinkingSession for tracking escalation state.
        """
        config = agent_profile.thinking_config or self._default_config
        starting_tier = config.get_starting_tier()

        session = ThinkingSession(
            config=config,
            current_tier=starting_tier,
        )

        logger.debug(
            "thinking_session_created",
            mode=config.mode,
            starting_tier=starting_tier,
            max_tier=config.escalation.max_tier,
        )

        return session

    def _load_default_legacy_tiers(self) -> None:
        self.available_tiers = {
            0: ThinkingTier.from_dict(
                {
                    "name": "fast",
                    "description": "Fast responses",
                    "model": "gpt-4o-mini",
                    "reasoning_effort": "low",
                    "max_tokens": 500,
                    "run_critic": False,
                    "generate_candidates": 1,
                },
                0,
            ),
            1: ThinkingTier.from_dict(
                {
                    "name": "standard",
                    "description": "Standard reasoning",
                    "model": "gpt-4o",
                    "reasoning_effort": "medium",
                    "max_tokens": 2000,
                    "run_critic": False,
                    "generate_candidates": 1,
                },
                1,
            ),
            2: ThinkingTier.from_dict(
                {
                    "name": "deep",
                    "description": "Deep analysis",
                    "model": "gpt-4o",
                    "reasoning_effort": "high",
                    "max_tokens": 4000,
                    "run_critic": False,
                    "generate_candidates": 1,
                },
                2,
            ),
            3: ThinkingTier.from_dict(
                {
                    "name": "critical",
                    "description": "Critical review",
                    "model": "gpt-4o",
                    "reasoning_effort": "high",
                    "max_tokens": 8000,
                    "run_critic": True,
                    "generate_candidates": 1,
                },
                3,
            ),
        }

    def _load_legacy_config(self, config_path: str) -> None:
        import yaml

        with open(config_path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}

        tiers = config.get("tiers", {})
        self.available_tiers = {
            int(tier_index): ThinkingTier.from_dict(tier_data, int(tier_index))
            for tier_index, tier_data in tiers.items()
        }

        triggers = config.get("escalation_triggers", {})
        self.escalation_triggers = EscalationTrigger(
            schema_validation_failed=triggers.get(
                "schema_validation_failed", True
            ),
            quality_gates_failed=triggers.get("quality_gates_failed", True),
            confidence_below_threshold=triggers.get(
                "confidence_below_threshold", 0.7
            ),
            risk_level_high=triggers.get("risk_level_high", True),
            explicit_deep_analysis=triggers.get("explicit_deep_analysis", True),
        )

    def get_tier_info(self, tier: int) -> ThinkingTier | None:
        return self.available_tiers.get(tier)

    def determine_initial_policy(
        self,
        intent: str,
        agent_profile: AgentProfile,
        risk_level: str | None = None,
        context_size: int | None = None,
    ) -> ThinkingPolicy:
        intent_lower = intent.lower()
        chosen_tier = 1

        if self.escalation_triggers.explicit_deep_analysis:
            if "deep analysis" in intent_lower or "deep" in intent_lower:
                chosen_tier = 2

        if risk_level == "high" and self.escalation_triggers.risk_level_high:
            chosen_tier = max(chosen_tier, 1)

        if context_size and context_size >= 4000:
            chosen_tier = max(chosen_tier, 1)

        tier_info = self.available_tiers.get(chosen_tier)
        if tier_info is None and self.available_tiers:
            tier_info = self.available_tiers[max(self.available_tiers.keys())]
            chosen_tier = tier_info.tier

        if tier_info is None:
            tier_info = ThinkingTier.from_dict({"model": "gpt-4o"}, chosen_tier)

        return ThinkingPolicy(
            tier=chosen_tier,
            tier_name=tier_info.name,
            model_id=tier_info.model,
            reasoning_effort=tier_info.reasoning_effort,
            max_tokens=tier_info.max_tokens,
            run_critic_pass=tier_info.run_critic,
            generate_candidates=tier_info.generate_candidates,
            escalation_reason=None,
            temperature=0.3,
        )

    def determine_escalated_policy(
        self,
        prior_attempts: list[PriorAttempt],
        max_tier: int | None = None,
    ) -> ThinkingPolicy | None:
        if not prior_attempts:
            tier_info = self.available_tiers.get(1) or (
                self.available_tiers[max(self.available_tiers.keys())]
                if self.available_tiers
                else None
            )
            if tier_info is None:
                return None
            return ThinkingPolicy(
                tier=tier_info.tier,
                tier_name=tier_info.name,
                model_id=tier_info.model,
                reasoning_effort=tier_info.reasoning_effort,
                max_tokens=tier_info.max_tokens,
                run_critic_pass=tier_info.run_critic,
                generate_candidates=tier_info.generate_candidates,
                escalation_reason=None,
                temperature=0.3,
            )

        last = prior_attempts[-1]
        current_tier = last.tier
        allowed_max = max_tier if max_tier is not None else max(self.available_tiers.keys())

        if current_tier >= allowed_max:
            return None

        if last.succeeded:
            if (
                last.confidence is not None
                and last.confidence < self.escalation_triggers.confidence_below_threshold
            ):
                reason = f"Confidence {last.confidence:.2f} below threshold"
            else:
                return None
        else:
            if last.gate_failures:
                reason = "gate failures"
            elif last.error:
                reason = "error"
            else:
                reason = "failed attempt"

        next_tier = min(current_tier + 1, allowed_max)
        tier_info = self.available_tiers.get(next_tier)
        if tier_info is None:
            return None

        return ThinkingPolicy(
            tier=next_tier,
            tier_name=tier_info.name,
            model_id=tier_info.model,
            reasoning_effort=tier_info.reasoning_effort,
            max_tokens=tier_info.max_tokens,
            run_critic_pass=tier_info.run_critic,
            generate_candidates=tier_info.generate_candidates,
            escalation_reason=reason,
            temperature=0.3,
        )

    def get_policy(self, session: ThinkingSession) -> ThinkingPolicy:
        """Get the current thinking policy for a session.

        Args:
            session: The thinking session.

        Returns:
            ThinkingPolicy with model and reasoning settings.
        """
        tier_config = session.config.get_tier_config(session.current_tier)

        # Check if we need critic at this tier
        run_critic = tier_config.use_critic or session.config.verification.use_critic
        critic_model = (
            tier_config.critic_model or session.config.verification.critic_model
        )

        policy = ThinkingPolicy(
            model_id=tier_config.model,
            reasoning_effort=tier_config.reasoning_effort,
            max_tokens=tier_config.max_tokens,
            temperature=tier_config.temperature,
            tier=session.current_tier,
            tier_name=tier_config.name,
            run_critic=run_critic,
            critic_model=critic_model,
            max_revisions=tier_config.max_revisions,
            max_context_tokens=tier_config.max_context_tokens,
            requires_approval_to_escalate=tier_config.requires_approval_to_escalate,
        )

        logger.debug(
            "policy_generated",
            tier=session.current_tier,
            tier_name=tier_config.name,
            model=tier_config.model,
            reasoning_effort=tier_config.reasoning_effort,
            run_critic=run_critic,
        )

        return policy

    def evaluate_for_escalation(
        self,
        session: ThinkingSession,
        plan: Plan | None = None,
        quality_report: RetrievalQualityReport | None = None,
        critique: Critique | None = None,
        confidence: float | None = None,
    ) -> tuple[bool, EscalationTrigger | None, str]:
        """Evaluate whether escalation should happen based on evidence.

        Args:
            session: The thinking session.
            plan: The generated plan (if any).
            quality_report: Quality report from retrieval gates.
            critique: Critique from critic engine (if used).
            confidence: Confidence score from plan.

        Returns:
            Tuple of (should_escalate, trigger, reason)
        """
        if not session.can_escalate():
            return False, None, "Cannot escalate (at max tier or max attempts)"

        triggers = session.config.escalation.triggers
        reason = ""
        trigger: EscalationTriggerType | None = None

        # Check quality gate failures
        if quality_report and quality_report.has_errors:
            if "quality_gates_failed" in triggers:
                trigger = "quality_gates_failed"
                reason = f"Quality gates failed: {quality_report.warnings[:2]}"
                session.gate_failures.extend(quality_report.warnings)

        # Check low confidence
        if confidence is not None:
            if (
                "low_confidence" in triggers
                and confidence < session.config.escalation.confidence_threshold
            ):
                trigger = "low_confidence"
                reason = f"Confidence {confidence:.2f} below threshold {session.config.escalation.confidence_threshold}"

        # Check critic rejection
        if critique and critique.should_revise:
            if "critic_rejection" in triggers:
                trigger = "critic_rejection"
                reason = f"Critic found issues: {critique.summary}"
                session.critic_issues.extend(critique.issues)

        # Check plan validation (schema issues)
        if plan and not plan.is_valid():
            if "schema_validation_failed" in triggers:
                trigger = "schema_validation_failed"
                reason = "Plan failed schema validation"

        if trigger:
            logger.info(
                "escalation_triggered",
                trigger=trigger,
                reason=reason,
                current_tier=session.current_tier,
            )
            return True, trigger, reason

        return False, None, "No escalation triggers matched"

    async def request_escalation_approval(
        self,
        session: ThinkingSession,
        reason: str,
        target_tier: ThinkingTier,
    ) -> bool:
        """Request approval for escalation (human-in-the-loop).

        Args:
            session: The thinking session.
            reason: Reason for escalation.
            target_tier: The tier we want to escalate to.

        Returns:
            True if approved, False otherwise.
        """
        # Check if approval is required
        current_config = session.config.get_tier_config(session.current_tier)
        target_config = session.config.get_tier_config(target_tier)

        needs_approval = (
            current_config.requires_approval_to_escalate
            or (target_tier == 3 and session.config.escalation.require_approval_for_tier_3)
            or session.config.escalation.require_approval_to_escalate
        )

        if not needs_approval:
            return True

        # If we have a callback, use it
        if self._approval_callback:
            logger.info(
                "requesting_escalation_approval",
                current_tier=session.current_tier,
                target_tier=target_tier,
                reason=reason,
            )

            approved = self._approval_callback(reason, session.current_tier, target_tier)

            logger.info(
                "escalation_approval_result",
                approved=approved,
                current_tier=session.current_tier,
                target_tier=target_tier,
            )

            return approved

        # No callback and approval required - log warning and deny
        logger.warning(
            "escalation_approval_required_but_no_callback",
            current_tier=session.current_tier,
            target_tier=target_tier,
        )
        return False

    async def escalate(
        self,
        session: ThinkingSession,
        trigger: EscalationTriggerType,
        reason: str,
    ) -> bool:
        """Attempt to escalate to the next tier.

        Args:
            session: The thinking session.
            trigger: What triggered the escalation.
            reason: Human-readable reason.

        Returns:
            True if escalation succeeded, False if blocked.
        """
        if not session.can_escalate():
            session.record_attempt(trigger, success=False, details="Cannot escalate")
            return False

        target_tier: ThinkingTierLevel = min(session.current_tier + 1, 3)  # type: ignore

        # Request approval if needed
        approved = await self.request_escalation_approval(session, reason, target_tier)

        if not approved:
            session.record_attempt(
                trigger, success=False, details="Escalation not approved"
            )
            return False

        # Perform escalation
        old_tier = session.current_tier
        session.current_tier = target_tier
        session.escalation_count += 1
        session.record_attempt(trigger, success=True, details=reason)

        logger.info(
            "escalation_performed",
            from_tier=old_tier,
            to_tier=target_tier,
            trigger=trigger,
            reason=reason,
            escalation_count=session.escalation_count,
        )

        return True

    def get_retrieval_config(self, session: ThinkingSession) -> dict[str, Any]:
        """Get retrieval configuration for current tier.

        Higher tiers typically get more aggressive retrieval.
        """
        config = session.config.retrieval
        tier = session.current_tier

        # Scale retrieval based on tier
        base_config = {
            "semantic_search": config.semantic_search,
            "keyword_search": config.keyword_search,
            "graph_expansion": config.graph_expansion,
            "graph_expansion_hops": config.graph_expansion_hops,
            "recency_boost": config.recency_boost,
            "recency_days": config.recency_days,
            "iterative_retrieval": config.iterative_retrieval,
        }

        # Enhance for higher tiers
        if tier >= 2:
            base_config["graph_expansion"] = True
            base_config["graph_expansion_hops"] = max(
                config.graph_expansion_hops, 2
            )

        if tier >= 3:
            base_config["iterative_retrieval"] = True

        return base_config

    def get_quality_gates_config(self, session: ThinkingSession) -> dict[str, Any]:
        """Get quality gates configuration for current tier."""
        config = session.config.gates

        return {
            "coverage_gate": config.coverage_gate,
            "recency_gate": config.recency_gate,
            "parity_gate": config.parity_gate,
            "pack_presence_gate": config.pack_presence_gate,
            "schema_aware_gate": config.schema_aware_gate,
            "min_notes": config.min_notes,
            "min_tasks": config.min_tasks,
            "min_events": config.min_events,
        }

    def should_run_critic(self, session: ThinkingSession) -> bool:
        """Check if critic should run at current tier."""
        tier_config = session.config.get_tier_config(session.current_tier)
        return tier_config.use_critic or session.config.verification.use_critic

    def get_max_revisions(self, session: ThinkingSession) -> int:
        """Get max revisions for current tier."""
        tier_config = session.config.get_tier_config(session.current_tier)
        return tier_config.max_revisions
