"""Deterministic Executor - validates and executes plans.

The executor is responsible for:
1. Validating plan schema and capabilities
2. Enforcing side-effect constraints
3. Gating approvals
4. Executing actions via Tool Broker
5. Collecting artifacts
6. Writing DecisionTrace
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.errors import (
    ApprovalDeniedError,
    ApprovalRequiredError,
    PlanValidationError,
)
from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas import (
    ActionRequest,
    AgentProfile,
    ApprovalRecord,
    CallStatus,
    CapabilityDef,
    ContextPacket,
    ContextRef,
    DecisionTrace,
    Outcome,
    OutcomeStatus,
    Plan,
    Provenance,
    RefType,
    SideEffect,
    SkillResourceRef,
    ToolCallRecord,
    normalize_side_effect_level,
)
from agent_kernel.core.schemas.base import get_kernel_version, utc_now
from agent_kernel.executor.approval import ApprovalGate
from agent_kernel.executor.policies import (
    ExternalAdapterAllowlist,
    load_external_adapter_allowlist,
)
from agent_kernel.memory.event_log import EventLog
from agent_kernel.prompting import PromptBundle, PromptRegistry
from agent_kernel.prompting.system_prompts import is_system_prompt_ref
from agent_kernel.tools.broker import ToolBroker
from agent_kernel.tracing.trace_store import TraceStore

logger = structlog.get_logger(__name__)


class DeterministicExecutor:
    """Executes plans deterministically with policy enforcement.

    The executor:
    - Validates plans before execution
    - Enforces approval policies
    - Executes actions via the Tool Broker
    - Records all activity in DecisionTrace
    """

    def __init__(
        self,
        tool_broker: ToolBroker,
        trace_store: TraceStore,
        approval_gate: ApprovalGate | None = None,
        event_log: EventLog | None = None,
        kernel_version: str | None = None,
        auto_approve_capabilities: list[str] | None = None,
        auto_approve_risk: str | None = None,
        interactive_approval: bool = False,
        dry_run: bool = False,
        prompt_registry: PromptRegistry | None = None,
        external_adapter_allowlist_path: str | Path | None = None,
        external_adapter_allowlist: ExternalAdapterAllowlist | None = None,
    ) -> None:
        """Initialize executor.

        Args:
            tool_broker: The tool broker for execution.
            trace_store: Store for writing traces.
            approval_gate: Optional approval gate for gating.
            event_log: Optional event log.
            kernel_version: Kernel version for provenance.
            auto_approve_capabilities: List of capabilities to auto-approve.
            auto_approve_risk: Auto-approve up to this risk level (none, low, medium, high).
            interactive_approval: If True, prompt user for approvals in real-time.
            dry_run: If True, don't execute actions, just show what would run.
        """
        self._broker = tool_broker
        self._trace_store = trace_store
        self._approval_gate = approval_gate or ApprovalGate(event_log)
        self._event_log = event_log
        self._kernel_version = kernel_version or get_kernel_version()
        self._auto_approve_capabilities = set(auto_approve_capabilities or [])
        self._auto_approve_risk = auto_approve_risk
        self._interactive_approval = interactive_approval
        self._dry_run = dry_run
        self._prompt_registry = prompt_registry or PromptRegistry()
        self._external_allowlist = (
            external_adapter_allowlist
            or load_external_adapter_allowlist(external_adapter_allowlist_path)
        )
        logger.info(
            "executor_initialized",
            auto_approve_capabilities=len(self._auto_approve_capabilities),
            auto_approve_risk=auto_approve_risk,
            interactive_approval=interactive_approval,
            dry_run=dry_run,
        )

    @staticmethod
    def _get_external_adapter_name(action: ActionRequest) -> str | None:
        """Extract External adapter name from action args."""
        for key in ("adapter_name", "adapter"):
            value = action.args.get(key)
            if value:
                return str(value)
        return None

    @property
    def approval_gate(self) -> ApprovalGate:
        """Get the approval gate."""
        return self._approval_gate

    def _compute_effective_policy(
        self,
        action: ActionRequest,
        capability: CapabilityDef,
        agent_profile: AgentProfile,
    ) -> tuple[SideEffect, bool]:
        """Compute effective policy values from authoritative sources.

        The agent's requested values are non-authoritative hints.
        The effective values are computed deterministically from:
        - CapabilityDef.side_effect_level
        - CapabilityDef.requires_approval_default
        - AgentProfile.approval_policy

        Args:
            action: The action request from the plan.
            capability: The capability definition.
            agent_profile: The agent profile.

        Returns:
            Tuple of (effective_side_effect, effective_requires_approval).
        """
        # Side effect is always from capability definition
        effective_side_effect = normalize_side_effect_level(
            capability.side_effect_level
        )

        # Approval requirement from capability + profile
        effective_requires_approval = capability.requires_approval_default

        # Check agent profile's approval policy
        if agent_profile.requires_approval_for(capability.capability_name):
            effective_requires_approval = True

        # Check side effect level against auto-approve policy
        if effective_side_effect not in agent_profile.approval_policy.auto_approve_side_effects:
            effective_requires_approval = True

        adapter_name = self._get_external_adapter_name(action)
        if adapter_name and self._external_allowlist:
            adapter_config = self._external_allowlist.get_adapter(adapter_name)
            if adapter_config and adapter_config.approval_required:
                effective_requires_approval = True

        return effective_side_effect, effective_requires_approval

    def _should_auto_approve_risk(self, side_effect: SideEffect) -> bool:
        """Check if action should be auto-approved based on risk level.

        Args:
            side_effect: The effective side effect level.

        Returns:
            True if should auto-approve.
        """
        if not self._auto_approve_risk:
            return False

        risk_levels = {
            "none": [SideEffect.NONE, SideEffect.READ],
            "low": [SideEffect.NONE, SideEffect.READ, SideEffect.WRITE, SideEffect.LOCAL_WRITE],
            "medium": [
                SideEffect.NONE,
                SideEffect.READ,
                SideEffect.WRITE,
                SideEffect.LOCAL_WRITE,
                SideEffect.EXECUTE,
                SideEffect.EXTERNAL_WRITE,
            ],
            "high": [
                SideEffect.NONE,
                SideEffect.READ,
                SideEffect.WRITE,
                SideEffect.LOCAL_WRITE,
                SideEffect.EXECUTE,
                SideEffect.EXTERNAL_WRITE,
            ],
        }

        allowed = risk_levels.get(self._auto_approve_risk.lower(), [])
        return side_effect in allowed

    def validate_plan(
        self,
        plan: Plan,
        agent_profile: AgentProfile,
        context_packet: ContextPacket | None = None,
    ) -> list[str]:
        """Validate a plan against agent profile.

        Args:
            plan: The plan to validate.
            agent_profile: The agent's profile.
            context_packet: Optional context packet for citation validation.

        Returns:
            List of validation errors (empty if valid).
        """
        errors = []

        # Check all capabilities are allowed
        for action in plan.actions:
            if not agent_profile.can_use_capability(action.capability_name):
                errors.append(
                    f"Capability '{action.capability_name}' not allowed for agent "
                    f"'{agent_profile.agent_profile_id}'"
                )
            adapter_name = self._get_external_adapter_name(action)
            if adapter_name and self._external_allowlist:
                adapter_config = self._external_allowlist.get_adapter(adapter_name)
                if adapter_config is None or not adapter_config.enabled:
                    errors.append(
                        f"External adapter '{adapter_name}' disabled by allowlist"
                    )

        # Check citations if required (only when evidence context was provided)
        if agent_profile.context_policy.must_cite and not plan.context_refs_used:
            if context_packet:
                evidence_items = [
                    item
                    for item in context_packet.items
                    if not is_system_prompt_ref(item.ref)
                ]
                if evidence_items:
                    errors.append(
                        "Plan must cite context sources but has no citations"
                    )

        # Check idempotency keys for writes
        for action in plan.actions:
            if action.side_effect.is_write and not action.idempotency_key:
                errors.append(
                    f"Action '{action.action_id}' has side effects but no "
                    "idempotency_key"
                )

        # Check cap limits for grouped actions
        cap_counts: dict[str, int] = {}
        cap_limits: dict[str, int] = {}
        for action in plan.actions:
            if action.cap_group is None and action.cap_limit is None:
                continue
            if not action.cap_group or action.cap_limit is None:
                errors.append(
                    f"Action '{action.action_id}' must set cap_group and cap_limit together"
                )
                continue
            if action.cap_limit <= 0:
                errors.append(
                    f"Action '{action.action_id}' has invalid cap_limit={action.cap_limit}"
                )
                continue
            cap_counts[action.cap_group] = cap_counts.get(action.cap_group, 0) + 1
            existing_limit = cap_limits.get(action.cap_group)
            if existing_limit is None:
                cap_limits[action.cap_group] = action.cap_limit
            elif existing_limit != action.cap_limit:
                errors.append(
                    f"Action '{action.action_id}' has inconsistent cap_limit for group "
                    f"'{action.cap_group}'"
                )

        for group, count in cap_counts.items():
            limit = cap_limits.get(group)
            if limit is None:
                continue
            if count > limit:
                errors.append(
                    f"Cap exceeded for '{group}': {count} > {limit}"
                )

        return errors

    async def execute(
        self,
        plan: Plan,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
        engine_id: str,
        run_id: str | None = None,
        workflow_id: str | None = None,
        approval_tokens: dict[str, str] | None = None,
        skill_usage_override: dict[str, Any] | None = None,
    ) -> DecisionTrace:
        """Execute a plan and create a trace.

        Args:
            plan: The plan to execute.
            context_packet: The context used.
            agent_profile: The agent profile.
            engine_id: The engine that generated the plan.
            run_id: Optional workflow run ID.
            workflow_id: Optional explicit workflow ID.
            approval_tokens: Pre-approved action tokens.

        Returns:
            DecisionTrace with execution results.
        """
        run_id = run_id or generate_ulid()
        workflow_id = workflow_id or ""
        trace_id = generate_ulid()
        approval_tokens = approval_tokens or {}
        auto_approve = approval_tokens.get("*") == "auto"
        tool_calls: list[ToolCallRecord] = []
        approvals: list[ApprovalRecord] = []
        artifacts: list[ContextRef] = []
        needs_approval = False

        logger.info(
            "executing_plan",
            trace_id=trace_id,
            plan_id=plan.plan_id,
            actions_count=len(plan.actions),
        )

        # 1. Validate plan
        validation_errors = self.validate_plan(plan, agent_profile, context_packet)
        if validation_errors:
            raise PlanValidationError(
                f"Plan validation failed: {'; '.join(validation_errors)}",
                errors=validation_errors,
            )

        # 2. Execute actions
        for action in plan.actions:
            # Get capability and compute effective policy
            capability = self._broker.registry.get_or_raise(action.capability_name)
            effective_side_effect, effective_requires_approval = self._compute_effective_policy(
                action, capability, agent_profile
            )

            approval_token = approval_tokens.get(action.action_id)
            if effective_requires_approval:
                # Check for pre-approved token
                token = approval_tokens.get(action.action_id)
                approval_granted = False
                approval_reason = None

                if token and self._approval_gate.validate_token(action.action_id, token):
                    # Already approved via token
                    approval_granted = True
                    approval_reason = "Pre-approved token"
                    approvals.append(ApprovalRecord(
                        action_id=action.action_id,
                        approved=True,
                        approved_by="pre_approved_token",
                        approved_at=utc_now(),
                        reason=approval_reason,
                    ))

                # Check auto-approve by capability name
                elif action.capability_name in self._auto_approve_capabilities:
                    approval_granted = True
                    approval_reason = f"Auto-approved (capability: {action.capability_name})"
                    # Generate approval token for broker
                    pending = self._approval_gate.request_approval(
                        action_id=action.action_id,
                        capability_name=action.capability_name,
                        args=action.args,
                        trace_id=trace_id,
                        agent_profile_id=agent_profile.agent_profile_id,
                    )
                    # Immediately approve to get token
                    self._approval_gate.approve(pending.approval_id, "auto_approve_capability", approval_reason)
                    approval_tokens[action.action_id] = pending.token
                    approvals.append(ApprovalRecord(
                        action_id=action.action_id,
                        approved=True,
                        approved_by="auto_approve_capability",
                        approved_at=utc_now(),
                        reason=approval_reason,
                    ))
                    logger.info(
                        "action_auto_approved_capability",
                        action_id=action.action_id,
                        capability=action.capability_name,
                    )

                # Check auto-approve by risk level
                elif self._auto_approve_risk and self._should_auto_approve_risk(effective_side_effect):
                    approval_granted = True
                    approval_reason = f"Auto-approved (risk <= {self._auto_approve_risk})"
                    # Generate approval token for broker
                    pending = self._approval_gate.request_approval(
                        action_id=action.action_id,
                        capability_name=action.capability_name,
                        args=action.args,
                        trace_id=trace_id,
                        agent_profile_id=agent_profile.agent_profile_id,
                    )
                    # Immediately approve to get token
                    self._approval_gate.approve(pending.approval_id, "auto_approve_risk", approval_reason)
                    approval_tokens[action.action_id] = pending.token
                    approvals.append(ApprovalRecord(
                        action_id=action.action_id,
                        approved=True,
                        approved_by="auto_approve_risk",
                        approved_at=utc_now(),
                        reason=approval_reason,
                    ))
                    logger.info(
                        "action_auto_approved_risk",
                        action_id=action.action_id,
                        risk=effective_side_effect.value,
                    )

                # Interactive approval prompt
                elif self._interactive_approval:
                    from agent_kernel.executor.interactive import prompt_for_approval

                    approval_granted, approval_reason = prompt_for_approval(
                        action=action,
                        capability_name=action.capability_name,
                        effective_side_effect=effective_side_effect,
                        args=action.args,
                    )

                    approvals.append(ApprovalRecord(
                        action_id=action.action_id,
                        approved=approval_granted,
                        approved_by="interactive_user",
                        approved_at=utc_now(),
                        reason=approval_reason,
                    ))

                    if not approval_granted:
                        # User denied - skip action
                        tool_calls.append(ToolCallRecord(
                            capability_name=action.capability_name,
                            status=CallStatus.DENIED,
                            input=action.args,
                            related_action_id=action.action_id,
                            requested_side_effect=action.side_effect,
                            requested_requires_approval=action.requires_approval,
                            effective_side_effect=effective_side_effect,
                            effective_requires_approval=effective_requires_approval,
                            idempotency_key=action.idempotency_key,
                        ))
                        logger.info(
                            "action_denied_interactively",
                            action_id=action.action_id,
                            reason=approval_reason,
                        )
                        continue
                else:
                    # Need approval but no mechanism to get it - request and skip
                    self._approval_gate.request_approval(
                        action_id=action.action_id,
                        capability_name=action.capability_name,
                        args=action.args,
                        trace_id=trace_id,
                        agent_profile_id=agent_profile.agent_profile_id,
                    )
                    needs_approval = True

                    # Create a skipped record
                    tool_calls.append(ToolCallRecord(
                        capability_name=action.capability_name,
                        status=CallStatus.SKIPPED,
                        input=action.args,
                        related_action_id=action.action_id,
                        requested_side_effect=action.side_effect,
                        requested_requires_approval=action.requires_approval,
                        effective_side_effect=effective_side_effect,
                        effective_requires_approval=effective_requires_approval,
                        idempotency_key=action.idempotency_key,
                    ))
                    continue

            # Execute the action (or skip if dry-run)
            if self._dry_run:
                # Dry-run mode - don't execute, just record
                tool_calls.append(ToolCallRecord(
                    capability_name=action.capability_name,
                    status=CallStatus.SKIPPED,
                    input=action.args,
                    output={"dry_run": True, "message": "Skipped in dry-run mode"},
                    related_action_id=action.action_id,
                    requested_side_effect=action.side_effect,
                    requested_requires_approval=action.requires_approval,
                    effective_side_effect=effective_side_effect,
                    effective_requires_approval=effective_requires_approval,
                    idempotency_key=action.idempotency_key,
                ))
                logger.debug(
                    "action_skipped_dry_run",
                    action_id=action.action_id,
                    capability=action.capability_name,
                )
            else:
                # Actually execute the action
                try:
                    record = await self._broker.execute(
                        capability_name=action.capability_name,
                        args=action.args,
                        agent_profile=agent_profile,
                        action_id=action.action_id,
                        approval_token=approval_tokens.get(action.action_id),
                    )
                    tool_calls.append(record)

                    # Extract artifacts from output
                    if record.status == CallStatus.SUCCESS:
                        new_artifacts = self._extract_artifacts(
                            action.capability_name,
                            record.output,
                        )
                        artifacts.extend(new_artifacts)

                except ApprovalRequiredError:
                    # Request approval
                    self._approval_gate.request_approval(
                        action_id=action.action_id,
                        capability_name=action.capability_name,
                        args=action.args,
                        trace_id=trace_id,
                        agent_profile_id=agent_profile.agent_profile_id,
                    )
                    needs_approval = True

                    tool_calls.append(ToolCallRecord(
                        capability_name=action.capability_name,
                        status=CallStatus.SKIPPED,
                        input=action.args,
                        related_action_id=action.action_id,
                        requested_side_effect=action.side_effect,
                        requested_requires_approval=action.requires_approval,
                        effective_side_effect=effective_side_effect,
                        effective_requires_approval=effective_requires_approval,
                        idempotency_key=action.idempotency_key,
                    ))

                except ApprovalDeniedError as e:
                    tool_calls.append(ToolCallRecord(
                        capability_name=action.capability_name,
                        status=CallStatus.DENIED,
                        input=action.args,
                        related_action_id=action.action_id,
                        requested_side_effect=action.side_effect,
                        requested_requires_approval=action.requires_approval,
                        effective_side_effect=effective_side_effect,
                        effective_requires_approval=effective_requires_approval,
                        idempotency_key=action.idempotency_key,
                    ))
                    approvals.append(ApprovalRecord(
                        action_id=action.action_id,
                        approved=False,
                        reason=e.reason,
                    ))

                except Exception as e:
                    logger.error(
                        "action_execution_failed",
                        action_id=action.action_id,
                        error=str(e),
                    )
                    # Record the failure but continue with other actions
                    tool_calls.append(ToolCallRecord(
                        capability_name=action.capability_name,
                        status=CallStatus.ERROR,
                        input=action.args,
                        related_action_id=action.action_id,
                        requested_side_effect=action.side_effect,
                        requested_requires_approval=action.requires_approval,
                        effective_side_effect=effective_side_effect,
                        effective_requires_approval=effective_requires_approval,
                        idempotency_key=action.idempotency_key,
                    ))

        # 3. Determine outcome
        outcome = self._determine_outcome(tool_calls, artifacts, needs_approval)

        # 4. Build provenance
        prompt_bundle = self._prompt_registry.compose_from_items(context_packet.items)
        provenance = self._build_provenance(plan, agent_profile, engine_id, prompt_bundle)

        # 5. Create trace
        skills_considered, skills_invoked, skills_loaded_files = self._build_skill_usage(
            context_packet,
            plan,
            tool_calls,
            skill_usage_override,
        )

        trace = DecisionTrace(
            trace_id=trace_id,
            run_id=run_id,
            workflow_id=workflow_id,
            agent_profile_id=agent_profile.agent_profile_id,
            engine_id=engine_id,
            intent=plan.intent,
            context_packet_id=context_packet.packet_id,
            plan=plan,
            tool_calls=tool_calls,
            approvals=approvals,
            outcome=outcome,
            provenance=provenance,
            skills_considered=skills_considered,
            skills_invoked=skills_invoked,
            skills_loaded_files=skills_loaded_files,
        )

        # 6. Write trace
        self._trace_store.write(trace)

        logger.info(
            "plan_executed",
            trace_id=trace_id,
            outcome=outcome.status.value,
            tool_calls_count=len(tool_calls),
            artifacts_count=len(artifacts),
        )

        return trace

    async def execute_actions(
        self,
        actions: list[ActionRequest],
        agent_profile: AgentProfile,
        approval_tokens: dict[str, str] | None = None,
    ) -> list[ToolCallRecord]:
        """Execute a list of actions without creating a trace.

        Intended for read-only preloading steps (e.g., skills.load).
        """
        approval_tokens = approval_tokens or {}
        auto_approve = approval_tokens.get("*") == "auto"
        tool_calls: list[ToolCallRecord] = []

        for action in actions:
            capability = self._broker.registry.get_or_raise(action.capability_name)
            effective_side_effect, effective_requires_approval = self._compute_effective_policy(
                action, capability, agent_profile
            )

            approval_token = approval_tokens.get(action.action_id)
            if effective_requires_approval:
                if not (auto_approve or approval_token):
                    raise ApprovalRequiredError(action.action_id, action.capability_name)
                if auto_approve and not approval_token:
                    approval_token = approval_tokens.get("*")

            record = await self._broker.execute(
                capability_name=action.capability_name,
                args=action.args,
                agent_profile=agent_profile,
                action_id=action.action_id,
                approval_token=approval_token,
            )
            record.requested_side_effect = action.side_effect
            record.requested_requires_approval = action.requires_approval
            record.effective_side_effect = effective_side_effect
            record.effective_requires_approval = effective_requires_approval
            tool_calls.append(record)

        return tool_calls

    def _build_skill_usage(
        self,
        context_packet: ContextPacket,
        plan: Plan,
        tool_calls: list[ToolCallRecord],
        override: dict[str, Any] | None,
    ) -> tuple[list[str], list[str], list[SkillResourceRef]]:
        considered = self._skills_from_context(context_packet)
        invoked = self._skills_from_plan(plan)
        loaded_files = self._skills_from_tool_calls(tool_calls)

        if override:
            considered = self._merge_skill_ids(considered, override.get("considered", []))
            invoked = self._merge_skill_ids(invoked, override.get("invoked", []))
            extra_files = override.get("loaded_files", [])
            loaded_files = self._merge_skill_files(loaded_files, extra_files)

        return considered, invoked, loaded_files

    def _skills_from_context(self, context_packet: ContextPacket) -> list[str]:
        skill_ids: list[str] = []
        for item in context_packet.items:
            if item.ref.ref_type != RefType.SKILL:
                continue
            skill_id = item.ref.metadata.get("skill_id") if isinstance(item.ref.metadata, dict) else None
            if not skill_id:
                skill_id = str(item.ref.ref_id).split(":", 1)[0]
            skill_ids.append(skill_id)
        return sorted(set(skill_ids))

    def _skills_from_plan(self, plan: Plan) -> list[str]:
        skill_ids: list[str] = []
        for action in plan.actions:
            if action.capability_name != "skills.load@v1":
                continue
            skill_id = action.args.get("skill_id")
            if skill_id:
                skill_ids.append(str(skill_id))
        return sorted(set(skill_ids))

    def _skills_from_tool_calls(self, tool_calls: list[ToolCallRecord]) -> list[SkillResourceRef]:
        resources: list[SkillResourceRef] = []
        for call in tool_calls:
            if call.capability_name != "skills.load@v1":
                continue
            payload = call.output.get("skill") if isinstance(call.output, dict) else None
            if not payload:
                continue
            for resource in payload.get("resources", []) if isinstance(payload, dict) else []:
                try:
                    resources.append(SkillResourceRef(**resource))
                except Exception:
                    continue
        return resources

    def _merge_skill_ids(self, base: list[str], extra: list[str]) -> list[str]:
        combined = list(base) + [str(item) for item in extra if item]
        return sorted(set(combined))

    def _merge_skill_files(
        self,
        base: list[SkillResourceRef],
        extra: list[Any],
    ) -> list[SkillResourceRef]:
        resources = list(base)
        for item in extra:
            if isinstance(item, SkillResourceRef):
                resources.append(item)
                continue
            if isinstance(item, dict):
                try:
                    resources.append(SkillResourceRef(**item))
                except Exception:
                    continue
        return resources

    def _extract_artifacts(
        self,
        capability_name: str,
        output: dict[str, Any],
    ) -> list[ContextRef]:
        """Extract created artifacts from tool output."""
        artifacts = []

        # Common patterns for created items
        if "task_id" in output:
            artifacts.append(ContextRef(
                ref_type=RefType.TASK,
                ref_id=output["task_id"],
                metadata={"title": output.get("title", "")},
            ))
        if "note_id" in output:
            artifacts.append(ContextRef(
                ref_type=RefType.NOTE,
                ref_id=output["note_id"],
                metadata={"title": output.get("title", "")},
            ))
        if "doc_id" in output:
            artifacts.append(ContextRef(
                ref_type=RefType.DOCUMENT,
                ref_id=output["doc_id"],
                metadata={"title": output.get("title", "")},
            ))

        return artifacts

    def _determine_outcome(
        self,
        tool_calls: list[ToolCallRecord],
        artifacts: list[ContextRef],
        needs_approval: bool,
    ) -> Outcome:
        """Determine execution outcome."""
        if needs_approval:
            return Outcome(
                status=OutcomeStatus.NEEDS_APPROVAL,
                artifacts=artifacts,
                summary="Some actions require approval before execution",
            )

        if not tool_calls:
            return Outcome(
                status=OutcomeStatus.COMPLETED,
                artifacts=artifacts,
                summary="No actions to execute",
            )

        success_count = sum(
            1 for tc in tool_calls if tc.status == CallStatus.SUCCESS
        )
        error_count = sum(
            1
            for tc in tool_calls
            if tc.status in {CallStatus.ERROR, CallStatus.FAILED}
        )
        denied_count = sum(
            1 for tc in tool_calls if tc.status == CallStatus.DENIED
        )

        if error_count == len(tool_calls):
            return Outcome(
                status=OutcomeStatus.FAILED,
                artifacts=artifacts,
                summary=f"All {error_count} actions failed",
            )

        if error_count > 0 or denied_count > 0:
            return Outcome(
                status=OutcomeStatus.PARTIAL,
                artifacts=artifacts,
                summary=f"{success_count} succeeded, {error_count} failed, {denied_count} denied",
            )

        return Outcome(
            status=OutcomeStatus.COMPLETED,
            artifacts=artifacts,
            summary=f"All {success_count} actions completed successfully",
        )

    def _build_provenance(
        self,
        plan: Plan,
        agent_profile: AgentProfile,
        engine_id: str,
        prompt_bundle: PromptBundle | None = None,
    ) -> Provenance:
        """Build provenance information."""
        prompt_hash = prompt_bundle.hash if prompt_bundle else None
        prompt_parts = prompt_bundle.to_provenance_parts() if prompt_bundle else []

        # Hash config
        config_json = agent_profile.model_dump_json()
        config_hash = hashlib.sha256(config_json.encode()).hexdigest()[:16]

        return Provenance(
            prompt_hash=prompt_hash,
            prompt_bundle_hash=prompt_hash,
            prompt_parts=prompt_parts,
            config_hash=config_hash,
            engine_version="1.0.0",
            kernel_version=self._kernel_version,
        )
