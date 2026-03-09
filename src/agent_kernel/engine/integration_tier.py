"""Integration Tier Router - Selects appropriate integration depth.

The tier router implements a decision tree that selects the right level
of kernel integration for each workflow/task:

- Tier 1 (RULE_BASED): No kernel, keyword matching, static rules
- Tier 2 (KERNEL_LITE): Vector search, experience memory, no graph
- Tier 3 (FULL_KERNEL): Full context assembly, graph expansion

This avoids over-engineering simple tasks while providing rich context
for synthesis workflows that need it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from agent_kernel.core.schemas import AgentProfile
    from agent_kernel.memory.experience_hooks import ExperienceMemoryHooks
    from agent_kernel.workflows.spec import WorkflowSpec

logger = structlog.get_logger(__name__)


class IntegrationTier(IntEnum):
    """Integration depth levels."""

    RULE_BASED = 1  # No kernel, keyword matching, static rules
    KERNEL_LITE = 2  # Vector search, experience memory, no graph
    FULL_KERNEL = 3  # Full context assembly, graph expansion


@dataclass
class TierDecision:
    """Result of tier selection."""

    tier: IntegrationTier
    reason: str
    skip_llm_planning: bool = False  # If True, use deterministic execution
    use_vector_search: bool = False
    use_experience_memory: bool = False
    use_graph_expansion: bool = False
    retrieval_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class TierRule:
    """A rule for tier selection."""

    name: str
    tier: IntegrationTier
    keywords: list[str] = field(default_factory=list)
    workflow_patterns: list[str] = field(default_factory=list)
    task_patterns: list[str] = field(default_factory=list)
    skip_llm: bool = False


# Default tier rules
DEFAULT_TIER_RULES: list[TierRule] = [
    # Tier 1: Rule-based (deterministic sync operations)
    TierRule(
        name="sync_workflows",
        tier=IntegrationTier.RULE_BASED,
        workflow_patterns=[
            r".*_sync$",
            r".*_completed_sync$",
            r".*_to_graph_sync$",
        ],
        skip_llm=True,  # These should use deterministic sync
    ),
    TierRule(
        name="recurring_tasks",
        tier=IntegrationTier.RULE_BASED,
        task_patterns=[r"recurring", r"daily", r"weekly", r"monthly"],
        keywords=["recurring", "scheduled", "reminder"],
    ),
    TierRule(
        name="obvious_routing",
        tier=IntegrationTier.RULE_BASED,
        keywords=[
            "MARTEK",
            "Stargate",
            "elk",
            "hunt",
            "deer",
            "antelope",
            "pheasant",
        ],
    ),
    # Tier 2: Kernel-lite (vector search, experience memory)
    TierRule(
        name="research_tasks",
        tier=IntegrationTier.KERNEL_LITE,
        keywords=["research", "look up", "explore", "investigate", "find out"],
    ),
    TierRule(
        name="ambiguous_triage",
        tier=IntegrationTier.KERNEL_LITE,
        workflow_patterns=[r"inbox_triage", r".*_enrichment$"],
    ),
    # Tier 3: Full kernel (graph expansion, synthesis)
    TierRule(
        name="synthesis_workflows",
        tier=IntegrationTier.FULL_KERNEL,
        workflow_patterns=[
            r"daily_checkin",
            r"weekly_review",
            r"meeting_prep",
            r"project_review",
        ],
    ),
    TierRule(
        name="cross_reference_tasks",
        tier=IntegrationTier.FULL_KERNEL,
        keywords=["related to", "similar to", "in context of", "for meeting"],
    ),
]


class IntegrationTierRouter:
    """Routes workflows/tasks to appropriate integration tier.

    The router uses a decision tree based on:
    1. Explicit workflow configuration (if set)
    2. Experience-based escalation (if enabled)
    3. Workflow ID pattern matching
    4. Task content keyword matching
    5. Default fallback
    """

    def __init__(
        self,
        rules: list[TierRule] | None = None,
        default_tier: IntegrationTier = IntegrationTier.RULE_BASED,
        experience_hooks: ExperienceMemoryHooks | None = None,
        enable_experience_escalation: bool = True,
    ) -> None:
        """Initialize the tier router.

        Args:
            rules: Custom tier rules (defaults to DEFAULT_TIER_RULES).
            default_tier: Fallback tier when no rules match.
            experience_hooks: Optional experience memory for escalation decisions.
            enable_experience_escalation: Whether to use experience for escalation.
        """
        self._rules = rules or DEFAULT_TIER_RULES
        self._default_tier = default_tier
        self._experience_hooks = experience_hooks
        self._enable_experience_escalation = enable_experience_escalation
        logger.info(
            "integration_tier_router_initialized",
            rules_count=len(self._rules),
            default_tier=default_tier.name,
            experience_enabled=experience_hooks is not None,
        )

    def select_tier(
        self,
        workflow_id: str | None = None,
        workflow_spec: WorkflowSpec | None = None,
        task_content: str | None = None,
        agent_profile: AgentProfile | None = None,
    ) -> TierDecision:
        """Select the appropriate integration tier.

        Args:
            workflow_id: The workflow identifier.
            workflow_spec: The workflow specification (if available).
            task_content: Task content for keyword matching.
            agent_profile: Agent profile for configuration hints.

        Returns:
            TierDecision with tier selection and configuration.
        """
        # 1. Check explicit workflow configuration
        if workflow_spec and hasattr(workflow_spec, "integration_tier"):
            explicit_tier = getattr(workflow_spec, "integration_tier", None)
            if explicit_tier:
                tier = IntegrationTier(explicit_tier)
                return self._build_decision(
                    tier=tier,
                    reason=f"Explicit workflow config: {tier.name}",
                )

        # 2. Check experience-based escalation
        if (
            self._experience_hooks
            and self._enable_experience_escalation
            and workflow_id
        ):
            # First determine what tier rules would give us
            rule_tier = self._get_tier_from_rules(workflow_id, task_content)

            # Check if experience suggests escalation
            should_escalate, escalation_reason = (
                self._experience_hooks.should_escalate_tier(
                    workflow_id=workflow_id,
                    current_tier=rule_tier.value if rule_tier else self._default_tier.value,
                )
            )
            if should_escalate:
                current = rule_tier or self._default_tier
                escalated = IntegrationTier(min(current.value + 1, 3))
                logger.info(
                    "experience_based_escalation",
                    workflow_id=workflow_id,
                    from_tier=current.name,
                    to_tier=escalated.name,
                    reason=escalation_reason,
                )
                return self._build_decision(
                    tier=escalated,
                    reason=f"Experience escalation: {escalation_reason}",
                )

        # 3. Match workflow patterns
        if workflow_id:
            for rule in self._rules:
                for pattern in rule.workflow_patterns:
                    if re.match(pattern, workflow_id):
                        return self._build_decision(
                            tier=rule.tier,
                            reason=f"Workflow pattern match: {rule.name}",
                            skip_llm=rule.skip_llm,
                        )

        # 4. Match task content keywords
        if task_content:
            content_lower = task_content.lower()
            for rule in self._rules:
                for keyword in rule.keywords:
                    if keyword.lower() in content_lower:
                        return self._build_decision(
                            tier=rule.tier,
                            reason=f"Keyword match '{keyword}': {rule.name}",
                            skip_llm=rule.skip_llm,
                        )

        # 5. Check agent profile for hints
        if agent_profile:
            thinking_config = getattr(agent_profile, "thinking_config", None)
            if thinking_config:
                mode = getattr(thinking_config, "mode", None)
                if mode == "deep":
                    return self._build_decision(
                        tier=IntegrationTier.FULL_KERNEL,
                        reason="Agent profile: deep thinking mode",
                    )

        # 6. Default fallback
        return self._build_decision(
            tier=self._default_tier,
            reason="Default tier",
        )

    def _get_tier_from_rules(
        self,
        workflow_id: str | None,
        task_content: str | None,
    ) -> IntegrationTier | None:
        """Get the tier that would be selected by rules alone (no escalation).

        Args:
            workflow_id: Workflow identifier.
            task_content: Task content for keyword matching.

        Returns:
            The tier from rule matching, or None if no match.
        """
        # Match workflow patterns
        if workflow_id:
            for rule in self._rules:
                for pattern in rule.workflow_patterns:
                    if re.match(pattern, workflow_id):
                        return rule.tier

        # Match task content keywords
        if task_content:
            content_lower = task_content.lower()
            for rule in self._rules:
                for keyword in rule.keywords:
                    if keyword.lower() in content_lower:
                        return rule.tier

        return None

    def _build_decision(
        self,
        tier: IntegrationTier,
        reason: str,
        skip_llm: bool = False,
    ) -> TierDecision:
        """Build a TierDecision with appropriate settings for the tier.

        Args:
            tier: The selected integration tier.
            reason: Why this tier was selected.
            skip_llm: Whether to skip LLM planning.

        Returns:
            Configured TierDecision.
        """
        if tier == IntegrationTier.RULE_BASED:
            return TierDecision(
                tier=tier,
                reason=reason,
                skip_llm_planning=skip_llm,
                use_vector_search=False,
                use_experience_memory=False,
                use_graph_expansion=False,
                retrieval_config={
                    "semantic_search": False,
                    "graph_expansion": False,
                    "recency_boost": False,
                },
            )
        elif tier == IntegrationTier.KERNEL_LITE:
            return TierDecision(
                tier=tier,
                reason=reason,
                skip_llm_planning=False,
                use_vector_search=True,
                use_experience_memory=True,
                use_graph_expansion=False,
                retrieval_config={
                    "semantic_search": True,
                    "graph_expansion": False,
                    "recency_boost": True,
                    "recency_days": 14,
                },
            )
        else:  # FULL_KERNEL
            return TierDecision(
                tier=tier,
                reason=reason,
                skip_llm_planning=False,
                use_vector_search=True,
                use_experience_memory=True,
                use_graph_expansion=True,
                retrieval_config={
                    "semantic_search": True,
                    "graph_expansion": True,
                    "graph_expansion_hops": 2,
                    "recency_boost": True,
                    "recency_days": 7,
                },
            )

    def add_rule(self, rule: TierRule) -> None:
        """Add a custom tier rule.

        Args:
            rule: The rule to add.
        """
        self._rules.insert(0, rule)  # Priority to custom rules
        logger.debug("tier_rule_added", rule_name=rule.name, tier=rule.tier.name)

    def set_experience_hooks(self, hooks: ExperienceMemoryHooks) -> None:
        """Set the experience hooks for escalation decisions.

        Args:
            hooks: The experience memory hooks instance.
        """
        self._experience_hooks = hooks
        logger.debug("experience_hooks_set")

    def get_experience_context(
        self,
        workflow_id: str,
        intent: str | None = None,
        capability_names: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Get experience context for enriching tier 2+ workflows.

        Args:
            workflow_id: The workflow being executed.
            intent: Current intent for semantic matching.
            capability_names: Capabilities that will be used.

        Returns:
            Experience context dict if hooks are configured, None otherwise.
        """
        if not self._experience_hooks:
            return None

        context = self._experience_hooks.get_similar_experiences(
            workflow_id=workflow_id,
            intent=intent,
            capability_names=capability_names,
        )

        return {
            "similar_cases_count": len(context.similar_cases),
            "success_rate": context.success_rate,
            "common_failures": [f.value for f in context.common_failure_categories],
            "top_cases": [
                {
                    "case_id": m.case.case_id,
                    "label": m.case.label.value,
                    "score": m.score,
                    "intent": m.case.intent[:100] if m.case.intent else None,
                }
                for m in context.similar_cases[:3]
            ],
        }

    def get_tier_stats(self) -> dict[str, Any]:
        """Get statistics about tier configuration.

        Returns:
            Dict with tier rule counts and configuration.
        """
        tier_counts = {tier.name: 0 for tier in IntegrationTier}
        for rule in self._rules:
            tier_counts[rule.tier.name] += 1

        return {
            "total_rules": len(self._rules),
            "rules_per_tier": tier_counts,
            "default_tier": self._default_tier.name,
        }


# Module-level singleton
_router: IntegrationTierRouter | None = None


def get_tier_router() -> IntegrationTierRouter:
    """Get the global tier router instance."""
    global _router
    if _router is None:
        _router = IntegrationTierRouter()
    return _router


def select_integration_tier(
    workflow_id: str | None = None,
    task_content: str | None = None,
    **kwargs: Any,
) -> TierDecision:
    """Convenience function to select integration tier.

    Args:
        workflow_id: The workflow identifier.
        task_content: Task content for matching.
        **kwargs: Additional arguments for select_tier.

    Returns:
        TierDecision with tier selection.
    """
    return get_tier_router().select_tier(
        workflow_id=workflow_id,
        task_content=task_content,
        **kwargs,
    )
