"""Critic Engine - Challenges plans for high-reliability tasks.

The CriticEngine provides a second opinion on plans, identifying:
- Potential issues or gaps
- Missing context that should be retrieved
- Risk flags that weren't identified
- Recommended changes to improve the plan
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from agent_kernel.core.schemas import (
    AgentProfile,
    ContextPacket,
    Plan,
)
from agent_kernel.services.llm import LLMService

logger = structlog.get_logger(__name__)


@dataclass
class Critique:
    """Result of critiquing a plan."""

    # Issues found in the plan
    issues: list[str] = field(default_factory=list)

    # Context that seems to be missing
    missing_context: list[str] = field(default_factory=list)

    # Risk flags identified
    risk_flags: list[str] = field(default_factory=list)

    # Recommended changes
    recommended_changes: list[str] = field(default_factory=list)

    # Confidence adjustment (-0.3 to +0.1)
    confidence_adjustment: float = 0.0

    # Whether the plan should be revised
    should_revise: bool = False

    # Summary of the critique
    summary: str = ""

    @property
    def has_issues(self) -> bool:
        """Check if critique found issues."""
        return len(self.issues) > 0 or len(self.risk_flags) > 0

    @property
    def issue_count(self) -> int:
        """Total number of issues found."""
        return len(self.issues) + len(self.risk_flags)

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/traces."""
        return {
            "issues": self.issues,
            "missing_context": self.missing_context,
            "risk_flags": self.risk_flags,
            "recommended_changes": self.recommended_changes,
            "confidence_adjustment": self.confidence_adjustment,
            "should_revise": self.should_revise,
            "summary": self.summary,
        }


class CriticEngine:
    """Challenges plans to identify gaps and issues.

    The CriticEngine is used in high-reliability scenarios where
    a second opinion on plans is valuable. It:
    - Reviews the plan against the context
    - Identifies potential issues
    - Suggests improvements
    - Adjusts confidence estimates

    v1.0.3: Integrated with ThinkingConfig for tier-aware verification.
    """

    def __init__(
        self,
        llm_service: LLMService | None = None,
        model_id: str = "gpt-4o",
        use_stub: bool = False,
        max_revisions: int = 2,
    ) -> None:
        """Initialize the critic engine.

        Args:
            llm_service: LLM service for generating critiques.
            model_id: Model to use for critique.
            use_stub: If True, use stub critiques (for testing).
            max_revisions: Maximum revision loops (v1.0.3).
        """
        self._llm = llm_service
        self._model_id = model_id
        self._use_stub = use_stub
        self._max_revisions = max_revisions

        logger.info(
            "critic_engine_initialized",
            model_id=model_id,
            use_stub=use_stub,
            max_revisions=max_revisions,
        )

    @classmethod
    def from_thinking_config(
        cls,
        llm_service: LLMService | None,
        agent_profile: AgentProfile,
    ) -> "CriticEngine":
        """Create a CriticEngine from an agent's thinking config.

        Args:
            llm_service: LLM service for generating critiques.
            agent_profile: Agent profile with thinking_config.

        Returns:
            Configured CriticEngine instance.
        """
        if agent_profile.thinking_config:
            verification = agent_profile.thinking_config.verification
            model_id = verification.critic_model or agent_profile.llm_config.model
            max_revisions = verification.max_revisions
        else:
            model_id = agent_profile.llm_config.model
            max_revisions = 2

        return cls(
            llm_service=llm_service,
            model_id=model_id,
            max_revisions=max_revisions,
        )

    async def critique(
        self,
        plan: Plan,
        context_packet: ContextPacket,
        agent_profile: AgentProfile | None = None,
    ) -> Critique:
        """Critique a plan to identify issues.

        Args:
            plan: The plan to critique.
            context_packet: The context used for the plan.
            agent_profile: Optional agent profile for context.

        Returns:
            Critique with issues and recommendations.
        """
        if self._use_stub or self._llm is None:
            return self._create_stub_critique(plan, context_packet)

        try:
            return await self._generate_critique(
                plan, context_packet, agent_profile
            )
        except Exception as e:
            logger.error("critique_generation_failed", error=str(e))
            return self._create_fallback_critique(str(e))

    async def _generate_critique(
        self,
        plan: Plan,
        context_packet: ContextPacket,
        agent_profile: AgentProfile | None,
    ) -> Critique:
        """Generate a critique using the LLM."""
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(plan, context_packet, agent_profile)

        response = await self._llm.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            model=self._model_id,
            temperature=0.3,
            max_tokens=2000,
        )

        return self._parse_critique_response(response.content)

    def _build_system_prompt(self) -> str:
        """Build the system prompt for critique."""
        return """You are a critical reviewer of agent plans. Your job is to:
1. Identify potential issues, gaps, or problems in the plan
2. Flag any risks that weren't adequately addressed
3. Note any missing context that should have been considered
4. Suggest specific improvements

Be constructive but thorough. Focus on:
- Logical gaps or inconsistencies
- Missing edge cases
- Overly ambitious scope
- Unclear or vague actions
- Security or privacy concerns
- Missing validation or verification steps

Respond in JSON format:
{
    "issues": ["issue 1", "issue 2"],
    "missing_context": ["what's missing"],
    "risk_flags": ["risk 1"],
    "recommended_changes": ["change 1"],
    "confidence_adjustment": -0.1,
    "should_revise": true,
    "summary": "Brief summary of the critique"
}"""

    def _build_user_prompt(
        self,
        plan: Plan,
        context_packet: ContextPacket,
        agent_profile: AgentProfile | None,
    ) -> str:
        """Build the user prompt with plan and context."""
        lines = []

        lines.append("## Plan to Review")
        lines.append(f"Intent: {plan.intent}")
        lines.append(f"Summary: {plan.summary}")

        if plan.actions:
            lines.append("\n### Actions:")
            for i, action in enumerate(plan.actions, 1):
                lines.append(f"{i}. {action.capability_name}")
                lines.append(f"   Args: {action.args}")

        if plan.context_refs_used:
            lines.append(f"\n### Citations: {len(plan.context_refs_used)} sources")

        if plan.risk.reasons:
            lines.append(f"\n### Risk Assessment: {plan.risk.level.value}")
            for reason in plan.risk.reasons:
                lines.append(f"- {reason}")

        if plan.validation.assumptions:
            lines.append("\n### Assumptions:")
            for assumption in plan.validation.assumptions:
                lines.append(f"- {assumption}")

        lines.append("\n## Context Provided")
        lines.append(f"Intent: {context_packet.intent}")
        lines.append(f"Items: {len(context_packet.items)}")

        if agent_profile:
            lines.append(f"\n## Agent: {agent_profile.name}")
            lines.append(
                f"Allowed capabilities: {agent_profile.allowed_capabilities}"
            )

        lines.append("\n## Your Task")
        lines.append("Review this plan and identify any issues, gaps, or risks.")

        return "\n".join(lines)

    def _parse_critique_response(self, content: str) -> Critique:
        """Parse the LLM response into a Critique."""
        import json

        try:
            # Try to extract JSON from the response
            json_str = self._extract_json(content)
            data = json.loads(json_str)

            return Critique(
                issues=data.get("issues", []),
                missing_context=data.get("missing_context", []),
                risk_flags=data.get("risk_flags", []),
                recommended_changes=data.get("recommended_changes", []),
                confidence_adjustment=data.get("confidence_adjustment", 0.0),
                should_revise=data.get("should_revise", False),
                summary=data.get("summary", ""),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("critique_parse_failed", error=str(e))
            return Critique(
                issues=["Failed to parse critique response"],
                summary=content[:200] if content else "",
            )

    def _extract_json(self, content: str) -> str:
        """Extract JSON from LLM response."""
        # Try to find JSON in code blocks
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            if end > start:
                return content[start:end].strip()

        if "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            if end > start:
                return content[start:end].strip()

        # Try to find raw JSON
        if "{" in content:
            start = content.find("{")
            end = content.rfind("}") + 1
            if end > start:
                return content[start:end]

        return content

    def _create_stub_critique(
        self,
        plan: Plan,
        context_packet: ContextPacket,
    ) -> Critique:
        """Create a stub critique for testing."""
        issues = []
        risk_flags = []
        missing_context = []
        confidence_adj = 0.0

        # Check for empty plan
        if not plan.actions:
            issues.append("Plan has no actions")
            confidence_adj -= 0.1

        # Check for missing citations
        if context_packet.items and not plan.context_refs_used:
            issues.append("Plan doesn't cite any context items")
            confidence_adj -= 0.1

        # Check for high-risk actions
        for action in plan.actions:
            if action.requires_approval:
                risk_flags.append(
                    f"Action '{action.capability_name}' requires approval"
                )

        # Check for assumptions without verification
        if plan.validation.assumptions:
            missing_context.append(
                "Plan has assumptions that should be verified"
            )

        should_revise = len(issues) > 0 or confidence_adj < -0.1

        return Critique(
            issues=issues,
            missing_context=missing_context,
            risk_flags=risk_flags,
            recommended_changes=[],
            confidence_adjustment=confidence_adj,
            should_revise=should_revise,
            summary="Stub critique generated" if not issues else "Issues found",
        )

    def _create_fallback_critique(self, error: str) -> Critique:
        """Create a fallback critique when generation fails."""
        return Critique(
            issues=[f"Critique generation failed: {error}"],
            confidence_adjustment=-0.2,
            should_revise=True,
            summary="Fallback critique due to error",
        )
