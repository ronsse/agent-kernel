"""Escalation Manager - Attempt → Gate → Escalate loop for plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from agent_kernel.core.schemas import AgentProfile, ContextPacket, Plan
from agent_kernel.engine.thinking_policy import (
    PriorAttempt,
    ThinkingPolicy,
    ThinkingPolicyController,
)
from agent_kernel.executor.quality_gates import GateResult, QualityGateRunner

logger = structlog.get_logger(__name__)


@dataclass
class EscalationAttempt:
    """Record of a single attempt."""

    tier: int
    policy: ThinkingPolicy
    plan: Plan | None
    gate_result: GateResult | None
    succeeded: bool
    error: str | None = None


@dataclass
class EscalationResult:
    """Result of escalation loop."""

    succeeded: bool
    final_plan: Plan | None
    total_escalations: int
    attempts: list[EscalationAttempt] = field(default_factory=list)
    reason: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "succeeded": self.succeeded,
            "total_attempts": len(self.attempts),
            "total_escalations": self.total_escalations,
            "final_tier": self.attempts[-1].tier if self.attempts else None,
            "attempt_tiers": [a.tier for a in self.attempts],
        }


class EscalationManager:
    """Manage plan escalation based on quality gates."""

    def __init__(
        self,
        policy_controller: ThinkingPolicyController | None = None,
        quality_gate_runner: QualityGateRunner | None = None,
        max_escalations: int = 2,
    ) -> None:
        self._policy_controller = policy_controller or ThinkingPolicyController()
        self._quality_gate_runner = quality_gate_runner or QualityGateRunner()
        self._max_escalations = max_escalations

    async def execute_with_escalation(
        self,
        engine: Any,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
    ) -> EscalationResult:
        attempts: list[EscalationAttempt] = []
        prior_attempts: list[PriorAttempt] = []
        total_escalations = 0

        policy = self._policy_controller.determine_initial_policy(
            intent=context_packet.intent,
            agent_profile=agent_profile,
            context_size=context_packet.budget.max_tokens if context_packet.budget else None,
        )

        best_plan: Plan | None = None
        best_tier = -1

        while True:
            gate_result: GateResult | None = None
            plan: Plan | None = None
            error: str | None = None
            succeeded = False

            try:
                plan = await engine.propose(context_packet, agent_profile)
                gate_result = self._quality_gate_runner.validate(
                    plan=plan,
                    context_packet=context_packet,
                    agent_profile=agent_profile,
                )
                succeeded = gate_result.passed
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                succeeded = False

            attempts.append(
                EscalationAttempt(
                    tier=policy.tier,
                    policy=policy,
                    plan=plan,
                    gate_result=gate_result,
                    succeeded=succeeded,
                    error=error,
                )
            )

            if plan is not None and policy.tier > best_tier:
                best_plan = plan
                best_tier = policy.tier

            if succeeded:
                return EscalationResult(
                    succeeded=True,
                    final_plan=plan,
                    total_escalations=total_escalations,
                    attempts=attempts,
                )

            prior_attempts.append(
                PriorAttempt(
                    tier=policy.tier,
                    succeeded=succeeded,
                    gate_failures=[f.gate_name for f in gate_result.failures]
                    if gate_result
                    else [],
                    error=error,
                    confidence=plan.confidence if plan else None,
                )
            )

            if total_escalations >= self._max_escalations:
                return EscalationResult(
                    succeeded=False,
                    final_plan=best_plan,
                    total_escalations=total_escalations,
                    attempts=attempts,
                    reason="Max escalations reached",
                )

            policy = self._policy_controller.determine_escalated_policy(
                prior_attempts,
            )
            if policy is None:
                return EscalationResult(
                    succeeded=False,
                    final_plan=best_plan,
                    total_escalations=total_escalations,
                    attempts=attempts,
                    reason="No higher tier available",
                )

            total_escalations += 1


async def execute_with_escalation(
    engine: Any,
    context_packet: ContextPacket,
    agent_profile: AgentProfile,
) -> EscalationResult:
    """Convenience function with defaults."""
    manager = EscalationManager()
    return await manager.execute_with_escalation(
        engine=engine,
        context_packet=context_packet,
        agent_profile=agent_profile,
    )
