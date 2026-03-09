"""Quality Gate Runner - Deterministic plan validation.

Quality gates are deterministic validators that run after plan generation
and before execution. They check schema validity, citations, constraints,
and other requirements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import structlog

from agent_kernel.core.schemas import (
    AgentProfile,
    ContextPacket,
    Plan,
    SideEffect,
)
from agent_kernel.prompting.system_prompts import is_system_prompt_ref

logger = structlog.get_logger(__name__)


class GateSeverity(str, Enum):
    """Severity level for gate failures."""

    ERROR = "error"  # Blocks execution
    WARNING = "warning"  # Logged but doesn't block


@dataclass
class GateFailure:
    """A single gate failure."""

    gate_name: str
    message: str
    severity: GateSeverity
    details: dict = field(default_factory=dict)


@dataclass
class GateResult:
    """Result of running all quality gates."""

    passed: bool
    failures: list[GateFailure] = field(default_factory=list)
    warnings: list[GateFailure] = field(default_factory=list)
    confidence_adjustment: float = 0.0  # Adjustment to plan confidence
    should_escalate: bool = False
    escalation_reason: str | None = None

    @property
    def error_count(self) -> int:
        """Count of error-level failures."""
        return len(self.failures)

    @property
    def warning_count(self) -> int:
        """Count of warnings."""
        return len(self.warnings)


class QualityGateRunner:
    """Runs deterministic quality gates on plans.

    Gates are run in order. ERROR-level failures block execution.
    WARNING-level issues are logged but don't block.
    """

    def __init__(
        self,
        confidence_threshold: float = 0.7,
        require_idempotency_keys: bool = True,
        require_evidence_refs: bool = True,
        max_actions: int = 10,
    ) -> None:
        """Initialize the gate runner.

        Args:
            confidence_threshold: Minimum confidence for no escalation.
            require_idempotency_keys: Require keys for write actions.
            require_evidence_refs: Require evidence_refs for write actions.
            max_actions: Maximum actions allowed in a plan.
        """
        self._confidence_threshold = confidence_threshold
        self._require_idempotency_keys = require_idempotency_keys
        self._require_evidence_refs = require_evidence_refs
        self._max_actions = max_actions

        logger.info("quality_gate_runner_initialized")

    def validate(
        self,
        plan: Plan,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
    ) -> GateResult:
        """Run all quality gates on a plan.

        Args:
            plan: The plan to validate.
            context_packet: The context used for the plan.
            agent_profile: The agent's profile.

        Returns:
            GateResult with pass/fail status and details.
        """
        failures: list[GateFailure] = []
        warnings: list[GateFailure] = []
        confidence_adj = 0.0

        # Gate 1: Schema validity (plan structure)
        gate_result = self._gate_schema_validity(plan)
        failures.extend(gate_result[0])
        warnings.extend(gate_result[1])

        # Gate 2: Capability allowlist
        gate_result = self._gate_capability_allowlist(plan, agent_profile)
        failures.extend(gate_result[0])
        warnings.extend(gate_result[1])

        # Gate 3: Citation requirements
        gate_result = self._gate_citations(plan, context_packet, agent_profile)
        failures.extend(gate_result[0])
        warnings.extend(gate_result[1])

        # Gate 4: Idempotency keys for writes
        if self._require_idempotency_keys:
            gate_result = self._gate_idempotency_keys(plan)
            failures.extend(gate_result[0])
            warnings.extend(gate_result[1])

        # Gate 4c: Cap enforcement for grouped actions
        gate_result = self._gate_action_caps(plan)
        failures.extend(gate_result[0])
        warnings.extend(gate_result[1])

        # Gate 4b: Evidence refs for write actions (v1.0.1)
        if self._require_evidence_refs:
            gate_result = self._gate_evidence_refs(plan, context_packet)
            failures.extend(gate_result[0])
            warnings.extend(gate_result[1])

        # Gate 5: Action count limit
        gate_result = self._gate_action_count(plan)
        failures.extend(gate_result[0])
        warnings.extend(gate_result[1])

        # Gate 6: Context reference validity
        gate_result = self._gate_context_references(plan, context_packet)
        failures.extend(gate_result[0])
        warnings.extend(gate_result[1])
        if gate_result[1]:  # Warnings about missing refs
            confidence_adj -= 0.1

        # Gate 7: Confidence check
        gate_result = self._gate_confidence(plan)
        failures.extend(gate_result[0])
        warnings.extend(gate_result[1])

        # Determine if we should escalate
        should_escalate = False
        escalation_reason = None

        if failures:
            should_escalate = True
            escalation_reason = f"{len(failures)} gate failures"
        elif plan.confidence and plan.confidence < self._confidence_threshold:
            should_escalate = True
            escalation_reason = f"confidence {plan.confidence} below {self._confidence_threshold}"

        passed = len(failures) == 0

        result = GateResult(
            passed=passed,
            failures=failures,
            warnings=warnings,
            confidence_adjustment=confidence_adj,
            should_escalate=should_escalate,
            escalation_reason=escalation_reason,
        )

        logger.info(
            "quality_gates_completed",
            passed=passed,
            error_count=result.error_count,
            warning_count=result.warning_count,
            should_escalate=should_escalate,
        )

        return result

    def _gate_schema_validity(
        self, plan: Plan
    ) -> tuple[list[GateFailure], list[GateFailure]]:
        """Gate 1: Check plan schema validity."""
        failures = []
        warnings = []

        # Plan must have an intent
        if not plan.intent or not plan.intent.strip():
            failures.append(GateFailure(
                gate_name="schema_validity",
                message="Plan must have a non-empty intent",
                severity=GateSeverity.ERROR,
            ))

        # Plan must have a summary
        if not plan.summary or not plan.summary.strip():
            warnings.append(GateFailure(
                gate_name="schema_validity",
                message="Plan should have a summary",
                severity=GateSeverity.WARNING,
            ))

        # Each action must have valid capability name
        for action in plan.actions:
            if not action.capability_name or "@" not in action.capability_name:
                failures.append(GateFailure(
                    gate_name="schema_validity",
                    message=f"Invalid capability name: {action.capability_name}",
                    severity=GateSeverity.ERROR,
                    details={"action_id": action.action_id},
                ))

        return failures, warnings

    def _gate_capability_allowlist(
        self, plan: Plan, agent_profile: AgentProfile
    ) -> tuple[list[GateFailure], list[GateFailure]]:
        """Gate 2: Check all capabilities are allowed."""
        failures = []
        warnings = []

        for action in plan.actions:
            if not agent_profile.can_use_capability(action.capability_name):
                failures.append(GateFailure(
                    gate_name="capability_allowlist",
                    message=f"Capability '{action.capability_name}' not allowed",
                    severity=GateSeverity.ERROR,
                    details={
                        "action_id": action.action_id,
                        "capability": action.capability_name,
                        "agent": agent_profile.agent_profile_id,
                    },
                ))

        return failures, warnings

    def _gate_citations(
        self,
        plan: Plan,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
    ) -> tuple[list[GateFailure], list[GateFailure]]:
        """Gate 3: Check citation requirements."""
        failures = []
        warnings = []

        if not agent_profile.context_policy.must_cite:
            return failures, warnings

        # Only require citations if evidence context was provided
        evidence_items = [
            item for item in context_packet.items if not is_system_prompt_ref(item.ref)
        ]
        if len(evidence_items) == 0:
            return failures, warnings

        if not plan.context_refs_used:
            failures.append(GateFailure(
                gate_name="citations",
                message="Plan must cite context sources but has no citations",
                severity=GateSeverity.ERROR,
                details={"context_items": len(context_packet.items)},
            ))

        return failures, warnings

    def _gate_idempotency_keys(
        self, plan: Plan
    ) -> tuple[list[GateFailure], list[GateFailure]]:
        """Gate 4: Check idempotency keys for write actions."""
        failures = []
        warnings = []

        for action in plan.actions:
            if action.side_effect.is_write:
                if not action.idempotency_key:
                    failures.append(GateFailure(
                        gate_name="idempotency_keys",
                        message=f"Action '{action.action_id}' has side effects "
                                "but no idempotency_key",
                        severity=GateSeverity.ERROR,
                        details={
                            "action_id": action.action_id,
                            "side_effect": action.side_effect.value,
                        },
                    ))

        return failures, warnings

    def _gate_action_count(
        self, plan: Plan
    ) -> tuple[list[GateFailure], list[GateFailure]]:
        """Gate 5: Check action count is reasonable."""
        failures = []
        warnings = []

        if len(plan.actions) > self._max_actions:
            warnings.append(GateFailure(
                gate_name="action_count",
                message=f"Plan has {len(plan.actions)} actions "
                        f"(max recommended: {self._max_actions})",
                severity=GateSeverity.WARNING,
                details={"action_count": len(plan.actions)},
            ))

        return failures, warnings

    def _gate_action_caps(
        self, plan: Plan
    ) -> tuple[list[GateFailure], list[GateFailure]]:
        """Gate 4c: Enforce cap_group/cap_limit constraints."""
        failures: list[GateFailure] = []
        warnings: list[GateFailure] = []

        counts: dict[str, int] = {}
        limits: dict[str, int] = {}

        for action in plan.actions:
            if action.cap_group is None and action.cap_limit is None:
                continue

            if not action.cap_group or action.cap_limit is None:
                failures.append(GateFailure(
                    gate_name="action_caps",
                    message="cap_group and cap_limit must be set together",
                    severity=GateSeverity.ERROR,
                    details={"action_id": action.action_id},
                ))
                continue

            if action.cap_limit <= 0:
                failures.append(GateFailure(
                    gate_name="action_caps",
                    message="cap_limit must be greater than zero",
                    severity=GateSeverity.ERROR,
                    details={
                        "action_id": action.action_id,
                        "cap_group": action.cap_group,
                        "cap_limit": action.cap_limit,
                    },
                ))
                continue

            counts[action.cap_group] = counts.get(action.cap_group, 0) + 1
            existing_limit = limits.get(action.cap_group)
            if existing_limit is None:
                limits[action.cap_group] = action.cap_limit
            elif existing_limit != action.cap_limit:
                failures.append(GateFailure(
                    gate_name="action_caps",
                    message="Inconsistent cap_limit within cap_group",
                    severity=GateSeverity.ERROR,
                    details={
                        "cap_group": action.cap_group,
                        "existing_limit": existing_limit,
                        "cap_limit": action.cap_limit,
                    },
                ))

        for cap_group, count in counts.items():
            limit = limits.get(cap_group)
            if limit is None:
                continue
            if count > limit:
                failures.append(GateFailure(
                    gate_name="action_caps",
                    message="Action cap exceeded",
                    severity=GateSeverity.ERROR,
                    details={
                        "cap_group": cap_group,
                        "count": count,
                        "cap_limit": limit,
                    },
                ))

        return failures, warnings

    def _gate_context_references(
        self, plan: Plan, context_packet: ContextPacket
    ) -> tuple[list[GateFailure], list[GateFailure]]:
        """Gate 6: Check that cited references exist in context."""
        failures = []
        warnings = []

        if not plan.context_refs_used:
            return failures, warnings

        # Build set of available evidence ref IDs
        available_refs = {
            item.ref.ref_id
            for item in context_packet.items
            if not is_system_prompt_ref(item.ref)
        }
        prompt_refs = {
            item.ref.ref_id
            for item in context_packet.items
            if is_system_prompt_ref(item.ref)
        }

        for ref in plan.context_refs_used:
            if ref.ref_id in prompt_refs:
                warnings.append(GateFailure(
                    gate_name="context_references",
                    message=f"Cited system prompt ref '{ref.ref_id}' is not evidence",
                    severity=GateSeverity.WARNING,
                    details={
                        "ref_id": ref.ref_id,
                        "ref_type": ref.ref_type.value,
                    },
                ))
            elif ref.ref_id not in available_refs:
                warnings.append(GateFailure(
                    gate_name="context_references",
                    message=f"Cited reference '{ref.ref_id}' not in context",
                    severity=GateSeverity.WARNING,
                    details={
                        "ref_id": ref.ref_id,
                        "ref_type": ref.ref_type.value,
                    },
                ))

        return failures, warnings

    def _gate_confidence(
        self, plan: Plan
    ) -> tuple[list[GateFailure], list[GateFailure]]:
        """Gate 7: Check plan confidence level.

        Note: Confidence is treated as a soft signal, not a hard gate.
        Very low confidence (< 0.3) triggers a warning, not an error.
        Escalation based on confidence should be combined with other signals.
        """
        failures = []
        warnings = []

        if plan.confidence is not None:
            if plan.confidence < 0.3:
                # Changed from ERROR to WARNING per v1.0.1 design
                # Confidence alone shouldn't block; combine with other signals
                warnings.append(GateFailure(
                    gate_name="confidence",
                    message=f"Plan confidence ({plan.confidence}) is very low",
                    severity=GateSeverity.WARNING,
                    details={"confidence": plan.confidence},
                ))
            elif plan.confidence < self._confidence_threshold:
                warnings.append(GateFailure(
                    gate_name="confidence",
                    message=f"Plan confidence ({plan.confidence}) below threshold "
                            f"({self._confidence_threshold})",
                    severity=GateSeverity.WARNING,
                    details={
                        "confidence": plan.confidence,
                        "threshold": self._confidence_threshold,
                    },
                ))

        return failures, warnings

    def _gate_evidence_refs(
        self, plan: Plan, context_packet: ContextPacket
    ) -> tuple[list[GateFailure], list[GateFailure]]:
        """Gate 8: Check evidence_refs for write actions (v1.0.1).

        Write actions should link to context evidence that supports them.
        This enables audit trails and helps catch hallucinated actions.
        """
        failures = []
        warnings = []

        # Build set of available evidence ref IDs
        available_refs = {
            item.ref.ref_id
            for item in context_packet.items
            if not is_system_prompt_ref(item.ref)
        }

        for action in plan.actions:
            # Only check write actions
            if action.side_effect.is_read_only:
                continue

            if not action.evidence_refs:
                # Missing evidence is a warning, not a hard failure
                warnings.append(GateFailure(
                    gate_name="evidence_refs",
                    message=f"Write action '{action.action_id}' has no evidence_refs",
                    severity=GateSeverity.WARNING,
                    details={
                        "action_id": action.action_id,
                        "capability": action.capability_name,
                        "side_effect": action.side_effect.value,
                    },
                ))
            else:
                # Check that evidence_refs actually exist in context
                for ref_id in action.evidence_refs:
                    if ref_id not in available_refs:
                        warnings.append(GateFailure(
                            gate_name="evidence_refs",
                            message=f"Evidence ref '{ref_id}' for action "
                                    f"'{action.action_id}' not in context",
                            severity=GateSeverity.WARNING,
                            details={
                                "action_id": action.action_id,
                                "ref_id": ref_id,
                            },
                        ))

        return failures, warnings
