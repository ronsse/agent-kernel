"""Workflow Runner - executes workflow state machine.

The runner coordinates the full workflow:
1. Assemble context
2. Propose plan (via engine)
3. Validate plan
4. Gate approvals
5. Execute plan
6. Write back results
7. Emit trace

v1.0.3: Integrated ThinkingPolicyController for tier-based reasoning
with autonomous escalation and optional human-in-the-loop approval.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
import yaml

from agent_kernel.context.assembler import ContextAssembler
from agent_kernel.core.config import get_settings
from agent_kernel.core.errors import WorkflowExecutionError, WorkflowNotFoundError
from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas import (
    ActionRequest,
    AgentProfile,
    CallStatus,
    ContextItem,
    ContextBudget,
    ContextPacket,
    ContextRef,
    DecisionTrace,
    OutcomeStatus,
    Plan,
    RefType,
    SideEffect,
    SkillResourceRef,
    ToolCallRecord,
)
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.trace import ErrorRecord, ReasoningMetadata
from agent_kernel.core.schemas.workflow import (
    WorkflowRun,
    WorkflowRunStatus,
)
from agent_kernel.engine.agent_engine import AgentEngine
from agent_kernel.engine.thinking_policy import (
    ThinkingPolicy,
    ThinkingPolicyController,
    ThinkingSession,
)
from agent_kernel.executor.executor import DeterministicExecutor
from agent_kernel.memory.derivation_store import (
    DerivationMappingRecord,
    DerivationMappingStore,
    SuppressionRecord,
    SuppressionRegistry,
)
from agent_kernel.memory.event_log import EventLog, EventType
from agent_kernel.services.reserved_blocks import insert_reserved_block, update_reserved_block
from agent_kernel.services.vault_sync import run_vault_sync
from agent_kernel.core.schemas.note import ReservedBlockType
from agent_kernel.workflows.spec import EmptyCheck, OnError, RetryConfig, WorkflowSpec
from agent_kernel.workflows.config_cache import ConfigCache, StoreCache
from agent_kernel.workflows.store import (
    InMemoryWorkflowRunStore,
    WorkflowCheckpoint,
    WorkflowRunStore,
)

if TYPE_CHECKING:
    from agent_kernel.context_graph.hooks import ContextGraphHooks
    from agent_kernel.engine.critic import CriticEngine

logger = structlog.get_logger(__name__)


class WorkflowResult:
    """Result of a workflow execution."""

    def __init__(
        self,
        workflow_id: str,
        run_id: str,
        success: bool,
        trace: DecisionTrace | None = None,
        error: str | None = None,
        step_failed: str | None = None,
        status: WorkflowRunStatus = WorkflowRunStatus.COMPLETED,
        workflow_run: WorkflowRun | None = None,
    ) -> None:
        self.workflow_id = workflow_id
        self.run_id = run_id
        self.success = success
        self.trace = trace
        self.error = error
        self.step_failed = step_failed
        self.status = status
        self.workflow_run = workflow_run
        self.completed_at = utc_now()

    @property
    def needs_approval(self) -> bool:
        """Check if workflow is waiting for approval."""
        return self.status == WorkflowRunStatus.WAITING_APPROVAL


@dataclass
class CalendarSourceFilters:
    """Filtering rules for calendar sources."""

    exclude_all_day: bool = False
    exclude_title_prefixes: list[str] = field(default_factory=list)
    exclude_title_keywords: list[str] = field(default_factory=list)
    require_attendees_or_conference: bool = False
    require_zoom_link: bool = False


@dataclass
class DerivationCaps:
    """Per-run caps for derived actions."""

    max_create_per_run: int | None = None
    max_update_per_run: int | None = None


@dataclass
class TaskDerivationConfig:
    """Configuration for external task derivations."""

    adapter_id: str
    project_id: str | None
    project_name: str | None
    labels: list[str]
    task_kind: str
    default_priority: int
    caps: DerivationCaps


@dataclass
class MeetingNoteDerivationConfig:
    """Configuration for meeting note derivations."""

    vault_path: str
    meeting_folder: str
    template: str
    caps: DerivationCaps
    suppression_ttl_hours: int
    project_folder_map: dict[str, str] = field(default_factory=dict)


@dataclass
class CalendarSourceConfig:
    """Calendar source configuration."""

    source_id: str
    provider: str
    calendar_id: str
    purpose: str | None
    import_window_days: int
    filters: CalendarSourceFilters
    task_derivations: list[TaskDerivationConfig] = field(default_factory=list)
    meeting_note_derivations: list[MeetingNoteDerivationConfig] = field(default_factory=list)


@dataclass
class CalendarEventRecord:
    """Normalized calendar event record."""

    source_id: str
    provider: str
    calendar_id: str
    event_id: str
    title: str
    description: str | None
    start: datetime | None
    end: datetime | None
    all_day: bool
    status: str
    updated_at: datetime | None
    etag: str | None
    location: str | None
    attendees: list[str]
    conference_link: str | None
    raw: dict[str, Any]
    zoom_link: str | None = None


@dataclass
class DerivedActionContext:
    """Context needed to persist derivation mappings after execution."""

    source_system: str
    source_container_id: str
    source_item_id: str
    derivation_kind: str
    target_system: str
    target_item_id: str | None
    last_synced_etag: str | None


@dataclass
class CalendarDerivationState:
    """In-memory state for calendar derivation workflows."""

    sources: dict[str, CalendarSourceConfig] = field(default_factory=dict)
    events_by_source: dict[str, list[CalendarEventRecord]] = field(default_factory=dict)
    action_context: dict[str, DerivedActionContext] = field(default_factory=dict)
    task_projects_by_name: dict[str, str] = field(default_factory=dict)


class WorkflowRunner:
    """Executes workflows through their step sequence.

    Coordinates context assembly, plan generation, validation,
    execution, and tracing.

    v1.0.3: Supports ThinkingPolicyController for adaptive reasoning
    with automatic escalation and critic integration.
    """

    def __init__(
        self,
        context_assembler: ContextAssembler,
        executor: DeterministicExecutor,
        event_log: EventLog | None = None,
        configs_dir: str | Path = "configs",
        thinking_policy_controller: ThinkingPolicyController | None = None,
        critic_engine: CriticEngine | None = None,
        workflow_store: WorkflowRunStore | None = None,
        context_graph_hooks: ContextGraphHooks | None = None,
        trace_store: Any = None,
        cost_anomaly_detector: Any = None,
        experience_miner: Any = None,
    ) -> None:
        """Initialize workflow runner.

        Args:
            context_assembler: For assembling context.
            executor: For executing plans.
            event_log: Optional event log.
            configs_dir: Path to configs directory.
            thinking_policy_controller: Optional controller for thinking policy (v1.0.3).
                If not provided and trace_store is available, uses
                AdaptiveThinkingPolicyController for trace-based optimization.
            critic_engine: Optional critic engine for verification (v1.0.3).
            workflow_store: Optional persistent store for workflow runs and checkpoints.
                           If not provided, uses in-memory storage.
            context_graph_hooks: Optional hooks for trace → graph decomposition.
            trace_store: Optional trace store for adaptive thinking policy.
                When provided without an explicit thinking_policy_controller,
                enables trace-based tier adjustment and model routing.
            cost_anomaly_detector: Optional CostAnomalyDetector for flagging
                cost outliers after each trace.
            experience_miner: Optional ExperienceMiner for extracting experience
                cases from completed traces.
        """
        self._assembler = context_assembler
        self._executor = executor
        self._event_log = event_log
        self._configs_dir = Path(configs_dir)
        self._workflows: dict[str, WorkflowSpec] = {}
        self._agent_profiles: dict[str, AgentProfile] = {}
        self._engines: dict[str, AgentEngine] = {}

        # Workflow run storage (v1.1.7: persistent store support)
        self._workflow_store = workflow_store or InMemoryWorkflowRunStore()
        # Keep in-memory cache for backward compatibility
        self._workflow_runs: dict[str, WorkflowRun] = {}

        # v1.0.3: Thinking policy support
        # Use adaptive controller when trace_store available and no explicit controller
        if thinking_policy_controller is not None:
            self._thinking_controller = thinking_policy_controller
        elif trace_store is not None:
            from agent_kernel.engine.adaptive_thinking import (
                AdaptiveThinkingPolicyController,
            )
            self._thinking_controller = AdaptiveThinkingPolicyController(
                trace_store=trace_store,
            )
        else:
            self._thinking_controller = ThinkingPolicyController()
        self._critic_engine = critic_engine

        # Context graph hooks for trace → graph decomposition
        self._context_graph_hooks = context_graph_hooks

        # Cost anomaly detection (v1.2)
        self._cost_detector = cost_anomaly_detector

        # Experience mining (v1.2)
        self._experience_miner = experience_miner

        # v1.1.7: Configuration caching with mtime invalidation
        self._config_cache = ConfigCache(default_ttl=300.0)  # 5 minute default TTL
        self._store_cache = StoreCache()

        logger.info(
            "workflow_runner_initialized",
            store_type=type(self._workflow_store).__name__,
            thinking_controller_type=type(self._thinking_controller).__name__,
        )

    def register_engine(self, engine: AgentEngine) -> None:
        """Register an agent engine.

        Args:
            engine: The engine to register.
        """
        self._engines[engine.engine_id] = engine
        logger.debug("engine_registered", engine_id=engine.engine_id)

    def load_workflow(self, workflow_id: str) -> WorkflowSpec:
        """Load a workflow from YAML.

        Args:
            workflow_id: The workflow ID (filename without .yaml).

        Returns:
            Loaded WorkflowSpec.

        Raises:
            WorkflowNotFoundError: If workflow file not found.
        """
        yaml_path = self._configs_dir / "workflows" / f"{workflow_id}.yaml"
        if not yaml_path.exists():
            raise WorkflowNotFoundError(workflow_id)

        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        spec = WorkflowSpec(**data)
        self._workflows[workflow_id] = spec

        logger.info("workflow_loaded", workflow_id=workflow_id, name=spec.name)
        return spec

    def load_agent_profile(self, agent_profile_id: str) -> AgentProfile:
        """Load an agent profile from YAML.

        Args:
            agent_profile_id: The agent profile ID.

        Returns:
            Loaded AgentProfile.
        """
        yaml_path = self._configs_dir / "agents" / f"{agent_profile_id}.yaml"
        if not yaml_path.exists():
            raise WorkflowExecutionError(
                f"Agent profile not found: {agent_profile_id}",
                workflow_id="",
            )

        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        profile = AgentProfile(**data)
        self._agent_profiles[agent_profile_id] = profile

        logger.info("agent_profile_loaded", agent_profile_id=agent_profile_id)
        return profile

    def get_workflow_run(self, run_id: str) -> WorkflowRun | None:
        """Get a workflow run by ID.

        Args:
            run_id: The workflow run ID.

        Returns:
            WorkflowRun if found, None otherwise.
        """
        # Check in-memory cache first
        if run_id in self._workflow_runs:
            return self._workflow_runs[run_id]

        # Fall back to persistent store
        return self._workflow_store.get_run(run_id)

    def list_workflow_runs(
        self,
        workflow_id: str | None = None,
        status: WorkflowRunStatus | None = None,
        limit: int = 50,
    ) -> list[WorkflowRun]:
        """List workflow runs with optional filtering.

        Args:
            workflow_id: Filter by workflow definition ID.
            status: Filter by status.
            limit: Maximum number of results.

        Returns:
            List of matching workflow runs.
        """
        # Use persistent store for listing
        return self._workflow_store.list_runs(
            workflow_id=workflow_id,
            status=status,
            limit=limit,
        )

    def _create_workflow_run(
        self,
        workflow_id: str,
        run_id: str,
        intent: str,
    ) -> WorkflowRun:
        """Create and persist a new workflow run."""
        workflow_run = WorkflowRun(
            run_id=run_id,
            workflow_id=workflow_id,
            status=WorkflowRunStatus.RUNNING,
            intent=intent,
            started_at=utc_now(),
        )
        # Persist to store
        self._workflow_store.create_run(workflow_run)
        # Cache in memory for fast access during execution
        self._workflow_runs[run_id] = workflow_run
        return workflow_run

    def _update_workflow_run(
        self,
        run_id: str,
        status: WorkflowRunStatus | None = None,
        last_step: str | None = None,
        trace_id: str | None = None,
        error: ErrorRecord | None = None,
    ) -> WorkflowRun | None:
        """Update a workflow run."""
        # Try in-memory cache first, then store
        workflow_run = self._workflow_runs.get(run_id)
        if workflow_run is None:
            workflow_run = self._workflow_store.get_run(run_id)
            if workflow_run is None:
                return None
            # Cache it
            self._workflow_runs[run_id] = workflow_run

        # Create a new run with updated fields
        updates: dict = {}
        if status is not None:
            updates["status"] = status
        if last_step is not None:
            updates["last_step"] = last_step
        if trace_id is not None:
            updates["trace_ids"] = workflow_run.trace_ids + [trace_id]
        if error is not None:
            updates["error"] = error
        if status in (
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
        ):
            updates["ended_at"] = utc_now()

        # Update in place
        for key, value in updates.items():
            setattr(workflow_run, key, value)

        # Persist to store
        self._workflow_store.update_run(workflow_run)

        return workflow_run

    def _persist_pending_approvals(
        self,
        run_id: str,
        workflow_id: str,
        trace: DecisionTrace,
    ) -> None:
        """Copy pending approvals from executor's ApprovalGate to workflow store.

        This bridges the gap between the in-memory ApprovalGate (used by the
        executor) and the persistent WorkflowRunStore (used by the REST API).
        Without this, approvals created by the executor are invisible to the
        REST API's GET /approvals/pending endpoint.
        """
        from agent_kernel.core.schemas.workflow import (
            ApprovalRequest as WfApprovalRequest,
            ApprovalRequestStatus,
        )

        gate = self._executor.approval_gate
        pending_list = gate.list_pending()

        for pending in pending_list:
            try:
                wf_approval = WfApprovalRequest(
                    approval_id=pending.approval_id,
                    trace_id=trace.trace_id,
                    run_id=run_id,
                    workflow_id=workflow_id,
                    action_id=pending.action_id,
                    capability_name=pending.capability_name,
                    effective_side_effect=SideEffect.EXTERNAL_WRITE,
                    status=ApprovalRequestStatus.PENDING,
                    requested_at=pending.requested_at,
                    expires_at=pending.expires_at,
                    action_preview=pending.args,
                )
                self._workflow_store.create_approval_request(wf_approval)
                logger.debug(
                    "approval_persisted_to_store",
                    approval_id=pending.approval_id,
                    run_id=run_id,
                )
            except Exception as e:
                logger.error(
                    "approval_persist_failed",
                    approval_id=pending.approval_id,
                    error=str(e),
                )

    async def run(
        self,
        workflow_id: str,
        intent: str | None = None,
        project_id: str | None = None,
        approval_tokens: dict[str, str] | None = None,
    ) -> WorkflowResult:
        """Run a workflow.

        Args:
            workflow_id: The workflow to run.
            intent: Override the default intent.
            project_id: Optional project scope.
            approval_tokens: Pre-approved action tokens.

        Returns:
            WorkflowResult with execution details.
        """
        run_id = generate_ulid()

        # Load workflow spec
        spec = self._workflows.get(workflow_id)
        if spec is None:
            try:
                spec = self.load_workflow(workflow_id)
            except WorkflowNotFoundError:
                return WorkflowResult(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    success=False,
                    error=f"Workflow not found: {workflow_id}",
                    status=WorkflowRunStatus.FAILED,
                )

        # Load agent profile
        agent_profile = self._agent_profiles.get(spec.agent_profile_id)
        if agent_profile is None:
            try:
                agent_profile = self.load_agent_profile(spec.agent_profile_id)
            except WorkflowExecutionError as e:
                return WorkflowResult(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    success=False,
                    error=str(e),
                    status=WorkflowRunStatus.FAILED,
                )

        # Get engine
        engine = self._engines.get(agent_profile.engine)
        if engine is None:
            raise WorkflowExecutionError(
                f"Engine not registered: {agent_profile.engine}",
                workflow_id=workflow_id,
            )

        # Use workflow description as default intent
        intent = intent or spec.description or f"Run {spec.name}"

        # Empty-poll guard: skip workflow if pre-check finds no work
        if spec.empty_check is not None:
            skip = await self._run_empty_check(spec.empty_check, agent_profile)
            if skip:
                logger.info(
                    "workflow_skipped_empty_poll",
                    workflow_id=workflow_id,
                    check_capability=spec.empty_check.capability,
                )
                return WorkflowResult(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    success=True,
                    status=WorkflowRunStatus.COMPLETED,
                )

        # Create workflow run record
        workflow_run = self._create_workflow_run(workflow_id, run_id, intent)

        if self._event_log:
            self._event_log.emit(
                EventType.WORKFLOW_STARTED,
                source="workflow_runner",
                entity_id=run_id,
                entity_type="workflow_run",
                data={
                    "workflow_id": workflow_id,
                    "intent": intent,
                },
            )

        logger.info(
            "workflow_started",
            run_id=run_id,
            workflow_id=workflow_id,
            intent=intent,
        )

        context_packet: ContextPacket | None = None
        plan: Plan | None = None
        trace: DecisionTrace | None = None
        skill_usage_override: dict[str, Any] = {}
        calendar_state: CalendarDerivationState | None = None

        try:
            # Execute each step
            for step in spec.steps:
                try:
                    if step == "vault_sync":
                        await self._step_vault_sync(spec)
                    elif step == "assemble_context":
                        context_packet = await self._step_assemble_context(
                            intent,
                            agent_profile,
                            project_id,
                            workflow_id,
                        )

                    elif step == "import_calendar_events":
                        calendar_state = await self._step_import_calendar_events(
                            agent_profile
                        )

                    elif step == "derive_tasks_from_events":
                        if calendar_state is None:
                            raise WorkflowExecutionError(
                                "Cannot derive tasks without calendar events",
                                workflow_id,
                                step,
                            )
                        plan = await self._step_derive_tasks_from_events(
                            calendar_state, agent_profile
                        )
                        if context_packet is None:
                            context_packet = self._empty_context_packet(
                                intent, agent_profile
                            )

                    elif step == "derive_meeting_notes_from_events":
                        if calendar_state is None:
                            raise WorkflowExecutionError(
                                "Cannot derive meeting notes without calendar events",
                                workflow_id,
                                step,
                            )
                        plan = await self._step_derive_meeting_notes_from_events(
                            calendar_state, agent_profile
                        )
                        if context_packet is None:
                            context_packet = self._empty_context_packet(
                                intent, agent_profile
                            )

                    elif step == "refresh_meeting_notes_auto_block":
                        if calendar_state is None:
                            raise WorkflowExecutionError(
                                "Cannot refresh meeting notes without calendar events",
                                workflow_id,
                                step,
                            )
                        plan = await self._step_refresh_meeting_notes_auto_block(
                            calendar_state, agent_profile
                        )
                        if context_packet is None:
                            context_packet = self._empty_context_packet(
                                intent, agent_profile
                            )

                    elif step == "propose_plan":
                        if spec.skip_llm_planning and spec.deterministic_capability:
                            # Pass workflow metadata as capability args
                            det_args = self._build_deterministic_args(spec)
                            plan = self._build_deterministic_plan(
                                intent=intent,
                                capability=spec.deterministic_capability,
                                args=det_args,
                            )
                            if context_packet is None:
                                context_packet = self._empty_context_packet(
                                    intent, agent_profile
                                )
                            logger.info(
                                "deterministic_plan_bypass",
                                workflow_id=workflow_id,
                                capability=spec.deterministic_capability,
                            )
                        else:
                            if context_packet is None:
                                raise WorkflowExecutionError(
                                    "Cannot propose plan without context",
                                    workflow_id,
                                    step,
                                )
                            plan = await self._step_propose_plan(
                                context_packet,
                                agent_profile,
                                engine,
                            )
                            plan, context_packet, skill_usage_override = await self._maybe_replan_with_skills(
                                plan,
                                context_packet,
                                agent_profile,
                                engine,
                            )

                    elif step == "validate":
                        if plan is None:
                            raise WorkflowExecutionError(
                                "Cannot validate without plan",
                                workflow_id,
                                step,
                            )
                        await self._step_validate(
                            plan, agent_profile, context_packet
                        )

                    elif step == "gate_approvals":
                        # Approval gating is handled in executor
                        pass

                    elif step == "execute":
                        if plan is None or context_packet is None:
                            raise WorkflowExecutionError(
                                "Cannot execute without plan and context",
                                workflow_id,
                                step,
                            )
                        trace = await self._step_execute(
                            plan,
                            context_packet,
                            agent_profile,
                            engine.engine_id,
                            run_id,
                            workflow_id,
                            approval_tokens,
                            skill_usage_override,
                        )

                        # For task_enrichment workflow: if we fetched tasks, generate update actions
                        if workflow_id == "task_enrichment" and trace:
                            enrichment_plan, enrichment_packet = await self._maybe_replan_for_enrichment(
                                trace,
                                context_packet,
                                agent_profile,
                                engine,
                            )
                            if enrichment_plan and enrichment_plan.actions:
                                # Execute the enrichment plan
                                enrichment_trace = await self._step_execute(
                                    enrichment_plan,
                                    enrichment_packet,
                                    agent_profile,
                                    engine.engine_id,
                                    run_id,
                                    workflow_id,
                                    approval_tokens,
                                    skill_usage_override,
                                )
                                # Use the enrichment trace as the final trace
                                trace = enrichment_trace

                        if (
                            trace
                            and calendar_state is not None
                            and trace.outcome.status != OutcomeStatus.NEEDS_APPROVAL
                        ):
                            await self._apply_derivation_results(
                                trace, calendar_state
                            )

                        # Check if workflow needs approval
                        if trace.outcome.status == OutcomeStatus.NEEDS_APPROVAL:
                            self._update_workflow_run(
                                run_id,
                                status=WorkflowRunStatus.WAITING_APPROVAL,
                                last_step=step,
                                trace_id=trace.trace_id,
                            )
                            # Persist pending approvals from executor gate to store
                            self._persist_pending_approvals(
                                run_id, workflow_id, trace,
                            )
                            # Save checkpoint before returning for approval
                            step_index = spec.steps.index(step)
                            self._save_step_checkpoint(
                                run_id, step_index, step,
                                context_packet, plan, trace, skill_usage_override,
                                calendar_state,
                            )
                            return WorkflowResult(
                                workflow_id=workflow_id,
                                run_id=run_id,
                                success=False,
                                trace=trace,
                                status=WorkflowRunStatus.WAITING_APPROVAL,
                                workflow_run=workflow_run,
                            )

                    elif step == "write_back":
                        if trace:
                            await self._step_write_back(trace, spec)

                    elif step == "emit_trace":
                        # Trace is written in execute step
                        pass

                except Exception as e:
                    if spec.on_error == OnError.HALT:
                        raise WorkflowExecutionError(str(e), workflow_id, step)
                    if spec.on_error == OnError.CONTINUE:
                        logger.warning(
                            "step_failed_continuing",
                            step=step,
                            error=str(e),
                        )
                    elif spec.on_error == OnError.RETRY:
                        # Retry the step with backoff
                        retried = await self._retry_step(
                            step=step,
                            error=e,
                            retry_config=spec.retry,
                            workflow_id=workflow_id,
                            intent=intent,
                            agent_profile=agent_profile,
                            engine=engine,
                            project_id=project_id,
                            context_packet=context_packet,
                            plan=plan,
                            calendar_state=calendar_state,
                            run_id=run_id,
                            approval_tokens=approval_tokens,
                            skill_usage_override=skill_usage_override,
                        )
                        if not retried:
                            raise WorkflowExecutionError(
                                f"Step failed after retries: {e}",
                                workflow_id,
                                step,
                            )

            # Update workflow run as completed
            self._update_workflow_run(
                run_id,
                status=WorkflowRunStatus.COMPLETED,
                trace_id=trace.trace_id if trace else None,
            )

            # Clean up checkpoints on successful completion
            self._workflow_store.delete_checkpoints(run_id)

            if self._event_log:
                self._event_log.emit(
                    EventType.WORKFLOW_COMPLETED,
                    source="workflow_runner",
                    entity_id=run_id,
                    entity_type="workflow_run",
                    data={
                        "workflow_id": workflow_id,
                        "trace_id": trace.trace_id if trace else None,
                    },
                )

            # Extract experience case from trace
            if self._experience_miner is not None and trace:
                try:
                    self._experience_miner.extract_case(trace)
                except Exception:
                    logger.warning(
                        "experience_case_extraction_failed",
                        trace_id=trace.trace_id,
                        exc_info=True,
                    )

            logger.info(
                "workflow_completed",
                run_id=run_id,
                workflow_id=workflow_id,
                trace_id=trace.trace_id if trace else None,
            )

            return WorkflowResult(
                workflow_id=workflow_id,
                run_id=run_id,
                success=True,
                trace=trace,
                status=WorkflowRunStatus.COMPLETED,
                workflow_run=workflow_run,
            )

        except Exception as e:
            # Update workflow run as failed
            error_record = ErrorRecord(
                code="WORKFLOW_ERROR",
                message=str(e),
                retryable=False,
            )
            self._update_workflow_run(
                run_id,
                status=WorkflowRunStatus.FAILED,
                error=error_record,
            )

            if self._event_log:
                self._event_log.emit(
                    EventType.WORKFLOW_FAILED,
                    source="workflow_runner",
                    entity_id=run_id,
                    entity_type="workflow_run",
                    data={
                        "workflow_id": workflow_id,
                        "error": str(e),
                    },
                )

            logger.error(
                "workflow_failed",
                run_id=run_id,
                workflow_id=workflow_id,
                error=str(e),
            )

            step_failed = None
            if isinstance(e, WorkflowExecutionError):
                step_failed = e.step

            return WorkflowResult(
                workflow_id=workflow_id,
                run_id=run_id,
                success=False,
                error=str(e),
                step_failed=step_failed,
                status=WorkflowRunStatus.FAILED,
                workflow_run=workflow_run,
            )

    async def _retry_step(
        self,
        step: str,
        error: Exception,
        retry_config: RetryConfig,
        workflow_id: str,
        intent: str,
        agent_profile: AgentProfile,
        engine: AgentEngine,
        project_id: str | None,
        context_packet: ContextPacket | None,
        plan: Plan | None,
        calendar_state: CalendarDerivationState | None,
        run_id: str,
        approval_tokens: dict[str, str] | None,
        skill_usage_override: dict[str, Any] | None = None,
    ) -> bool:
        """Retry a failed step with exponential backoff.

        Args:
            step: The step that failed.
            error: The exception that occurred.
            retry_config: Retry configuration.
            workflow_id: Workflow ID for logging.
            intent: The original intent.
            agent_profile: Agent profile.
            engine: Agent engine.
            project_id: Project ID.
            context_packet: Current context packet.
            plan: Current plan.
            run_id: Run ID.
            approval_tokens: Approval tokens.

        Returns:
            True if retry succeeded, False otherwise.
        """
        # Check if error is retryable
        error_str = str(error).lower()
        is_retryable = any(
            keyword in error_str
            for keyword in retry_config.retryable_errors
        )

        if not is_retryable:
            logger.warning(
                "step_not_retryable",
                step=step,
                error=str(error),
            )
            return False

        for attempt in range(1, retry_config.max_retries + 1):
            delay = retry_config.get_delay(attempt)

            logger.info(
                "step_retry",
                step=step,
                attempt=attempt,
                max_retries=retry_config.max_retries,
                delay_seconds=delay,
            )

            await asyncio.sleep(delay)

            try:
                if step == "vault_sync":
                    await self._step_vault_sync(spec)
                    return True

                if step == "assemble_context":
                    await self._step_assemble_context(
                        intent,
                        agent_profile,
                        project_id,
                        workflow_id,
                    )
                    return True

                if step == "import_calendar_events":
                    await self._step_import_calendar_events(agent_profile)
                    return True

                if step == "derive_tasks_from_events":
                    if calendar_state is not None:
                        await self._step_derive_tasks_from_events(
                            calendar_state, agent_profile
                        )
                        return True

                if step == "derive_meeting_notes_from_events":
                    if calendar_state is not None:
                        await self._step_derive_meeting_notes_from_events(
                            calendar_state, agent_profile
                        )
                        return True

                if step == "refresh_meeting_notes_auto_block":
                    if calendar_state is not None:
                        await self._step_refresh_meeting_notes_auto_block(
                            calendar_state, agent_profile
                        )
                        return True

                if step == "propose_plan":
                    if context_packet:
                        await self._step_propose_plan(
                            context_packet,
                            agent_profile,
                            engine,
                        )
                        return True

                elif step == "validate":
                    if plan:
                        await self._step_validate(
                            plan, agent_profile, context_packet
                        )
                        return True

                elif step == "execute":
                    if plan and context_packet:
                        await self._step_execute(
                            plan,
                            context_packet,
                            agent_profile,
                            engine.engine_id,
                            run_id,
                            workflow_id,
                            approval_tokens,
                            skill_usage_override,
                        )
                        return True

                return True

            except Exception as retry_error:
                logger.warning(
                    "step_retry_failed",
                    step=step,
                    attempt=attempt,
                    error=str(retry_error),
                )

                if attempt == retry_config.max_retries:
                    return False

        return False

    def _derivation_db_path(self) -> Path:
        settings = get_settings()
        return settings.data_dir / "entities" / "entity_store.db"

    def _get_derivation_stores(
        self,
    ) -> tuple[DerivationMappingStore, SuppressionRegistry]:
        """Get derivation stores with connection caching.

        Uses store cache to avoid creating new connections on every call.
        """
        db_path = self._derivation_db_path()

        mapping_store = self._store_cache.get_or_create(
            "derivation_mapping",
            lambda: DerivationMappingStore(db_path),
        )
        suppression_registry = self._store_cache.get_or_create(
            "suppression_registry",
            lambda: SuppressionRegistry(db_path),
        )

        return mapping_store, suppression_registry

    def _load_calendar_sources(self) -> dict[str, CalendarSourceConfig]:
        """Load calendar sources with configuration caching.

        Uses config cache with file mtime tracking for automatic invalidation
        when the configuration file changes.
        """
        config_path = Path(self._configs_dir) / "integrations" / "calendar_sources.yaml"

        return self._config_cache.get_or_load(
            key="calendar_sources",
            file_path=config_path,
            loader_fn=lambda: self._parse_calendar_sources(config_path),
        )

    def _parse_calendar_sources(
        self, config_path: Path
    ) -> dict[str, CalendarSourceConfig]:
        """Parse calendar sources configuration from file."""
        if not config_path.exists():
            raise WorkflowExecutionError(
                f"Calendar sources config not found: {config_path}",
                workflow_id="",
                step="import_calendar_events",
            )

        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        sources: dict[str, CalendarSourceConfig] = {}

        for item in payload.get("sources", []):
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id", "")).strip()
            provider = str(item.get("provider", "")).strip()
            calendar_id = str(item.get("calendar_id", "")).strip()
            if not source_id or not provider or not calendar_id:
                logger.warning(
                    "calendar_source_missing_fields",
                    source_id=source_id,
                    provider=provider,
                    calendar_id=calendar_id,
                )
                continue

            filters_payload = item.get("filters", {}) or {}
            filters = CalendarSourceFilters(
                exclude_all_day=bool(filters_payload.get("exclude_all_day", False)),
                exclude_title_prefixes=list(
                    filters_payload.get("exclude_title_prefixes", [])
                ),
                exclude_title_keywords=list(
                    filters_payload.get("exclude_title_keywords", [])
                ),
                require_attendees_or_conference=bool(
                    filters_payload.get("require_attendees_or_conference", False)
                ),
                require_zoom_link=bool(
                    filters_payload.get("require_zoom_link", False)
                ),
            )

            import_window_days = int(item.get("import_window_days", 30))
            purpose = item.get("purpose")

            task_derivations: list[TaskDerivationConfig] = []
            meeting_note_derivations: list[MeetingNoteDerivationConfig] = []
            for derivation in item.get("derivations", []) or []:
                if not isinstance(derivation, dict):
                    continue
                derivation_type = derivation.get("type")
                caps_payload = derivation.get("caps", {}) or {}
                caps = DerivationCaps(
                    max_create_per_run=caps_payload.get("max_create_per_run"),
                    max_update_per_run=caps_payload.get("max_update_per_run"),
                )
                if derivation_type == "external_tasks":
                    task_derivations.append(TaskDerivationConfig(
                        adapter_id=str(derivation.get("adapter_id", "default")),
                        project_id=derivation.get("project_id"),
                        project_name=derivation.get("project_name"),
                        labels=list(derivation.get("labels", [])),
                        task_kind=str(derivation.get("task_kind", "event")),
                        default_priority=int(derivation.get("default_priority", 1)),
                        caps=caps,
                    ))
                elif derivation_type == "obsidian_meeting_notes":
                    vault_path = str(derivation.get("vault_path", "")).strip()
                    if "${OBSIDIAN_VAULT_PATH}" in vault_path:
                        settings = get_settings()
                        vault_path = vault_path.replace(
                            "${OBSIDIAN_VAULT_PATH}",
                            settings.obsidian_vault_path,
                        )
                    vault_path = os.path.expanduser(os.path.expandvars(vault_path))
                    meeting_note_derivations.append(MeetingNoteDerivationConfig(
                        vault_path=vault_path,
                        meeting_folder=str(derivation.get("meeting_folder", "")).strip(),
                        template=str(derivation.get("template", "")).strip(),
                        caps=caps,
                        suppression_ttl_hours=int(
                            derivation.get("suppression_ttl_hours", 24)
                        ),
                        project_folder_map=(
                            derivation.get("project_folder_map", {}) or {}
                        ),
                    ))

            sources[source_id] = CalendarSourceConfig(
                source_id=source_id,
                provider=provider,
                calendar_id=calendar_id,
                purpose=purpose,
                import_window_days=import_window_days,
                filters=filters,
                task_derivations=task_derivations,
                meeting_note_derivations=meeting_note_derivations,
            )

        return sources

    async def _step_vault_sync(self, spec: WorkflowSpec) -> None:
        logger.debug("step_vault_sync", workflow_id=spec.workflow_id)
        config = spec.vault_sync
        summary = await run_vault_sync(
            force=config.force,
            folder=config.folder,
            inject_ids=config.inject_ids,
            with_embeddings=config.with_embeddings,
            embedding_model=config.embedding_model,
            with_enrichment=config.with_enrichment,
            enrichment_model=config.enrichment_model,
            summarization_skip_override=config.summarization_skip,
            summarize_all=config.summarize_all,
        )
        logger.info(
            "vault_sync_completed",
            total_notes=summary.total_notes,
            created=summary.created,
            updated=summary.updated,
            unchanged=summary.unchanged,
            errors=summary.errors,
        )

    def _call_gws_calendar(
        self,
        calendar_id: str,
        time_min: str,
        time_max: str,
        max_results: int = 100,
    ) -> dict[str, Any]:
        """Call gws calendar events list via subprocess and return parsed JSON."""
        params = json.dumps({
            "calendarId": calendar_id,
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": max_results,
        })
        result = subprocess.run(
            ["gws", "calendar", "events", "list", "--params", params, "--format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"gws calendar events list failed: {result.stderr.strip()}"
            )
        return json.loads(result.stdout)

    async def _step_import_calendar_events(
        self,
        agent_profile: AgentProfile,
    ) -> CalendarDerivationState:
        logger.debug("step_import_calendar_events")
        sources = self._load_calendar_sources()
        state = CalendarDerivationState(sources=sources)

        now = utc_now()
        for source in sources.values():
            start = now
            end = now + timedelta(days=source.import_window_days)
            try:
                events_payload = self._call_gws_calendar(
                    calendar_id=source.calendar_id,
                    time_min=start.isoformat(),
                    time_max=end.isoformat(),
                )
            except Exception as exc:
                logger.warning(
                    "calendar_import_failed",
                    source_id=source.source_id,
                    error=str(exc),
                )
                events_payload = {}
            raw_events = self._extract_events_list(events_payload)
            normalized: list[CalendarEventRecord] = []
            for raw in raw_events:
                record = self._normalize_calendar_event(raw, source)
                if record is None:
                    continue
                if not self._event_passes_filters(record, source.filters):
                    continue
                normalized.append(record)
            state.events_by_source[source.source_id] = normalized
            logger.debug(
                "calendar_events_imported",
                source_id=source.source_id,
                count=len(normalized),
            )

        return state

    async def _step_derive_tasks_from_events(
        self,
        state: CalendarDerivationState,
        agent_profile: AgentProfile,
    ) -> Plan:
        logger.debug("step_derive_tasks_from_events")
        mapping_store, _ = self._get_derivation_stores()
        actions: list[ActionRequest] = []

        for source in state.sources.values():
            events = state.events_by_source.get(source.source_id, [])
            if not events:
                continue
            for derivation in source.task_derivations:
                project_id = await self._resolve_task_project_id(
                    derivation, state, agent_profile
                )
                create_count = 0
                update_count = 0
                for event in events:
                    mapping = mapping_store.get_mapping(
                        source_system=source.provider,
                        source_container_id=source.calendar_id,
                        source_item_id=event.event_id,
                        derivation_kind=f"external_task:{derivation.task_kind}",
                    )

                    if event.status == "cancelled":
                        if mapping:
                            cap_group, cap_limit = self._cap_fields(
                                derivation.caps.max_update_per_run,
                                self._cap_group("tasks.update", source.source_id),
                            )
                            action = ActionRequest(
                                capability_name="tasks.complete@v1",
                                args={"task_id": mapping.target_item_id},
                                side_effect=SideEffect.EXTERNAL_WRITE,
                                idempotency_key=self._task_idempotency_key(
                                    event, derivation.task_kind
                                )
                                + ":complete",
                                cap_group=cap_group,
                                cap_limit=cap_limit,
                            )
                            actions.append(action)
                            state.action_context[action.action_id] = DerivedActionContext(
                                source_system=source.provider,
                                source_container_id=source.calendar_id,
                                source_item_id=event.event_id,
                                derivation_kind=f"external_task:{derivation.task_kind}",
                                target_system="external",
                                target_item_id=mapping.target_item_id,
                                last_synced_etag=event.etag,
                            )
                        continue

                    if mapping and event.etag and mapping.last_synced_etag == event.etag:
                        continue

                    if mapping:
                        if (
                            derivation.caps.max_update_per_run is not None
                            and update_count >= derivation.caps.max_update_per_run
                        ):
                            continue
                        cap_group, cap_limit = self._cap_fields(
                            derivation.caps.max_update_per_run,
                            self._cap_group("tasks.update", source.source_id),
                        )
                        update_args = {
                            "task_id": mapping.target_item_id,
                            "content": event.title,
                            "description": self._task_description(event),
                            "priority": derivation.default_priority,
                            "labels": derivation.labels,
                        }
                        due_string = self._task_due_string(event)
                        if due_string:
                            update_args["due_string"] = due_string
                        action = ActionRequest(
                            capability_name="tasks.update@v1",
                            args=update_args,
                            side_effect=SideEffect.EXTERNAL_WRITE,
                            idempotency_key=self._task_idempotency_key(
                                event, derivation.task_kind
                            ),
                            cap_group=cap_group,
                            cap_limit=cap_limit,
                        )
                        actions.append(action)
                        update_count += 1
                        state.action_context[action.action_id] = DerivedActionContext(
                            source_system=source.provider,
                            source_container_id=source.calendar_id,
                            source_item_id=event.event_id,
                            derivation_kind=f"external_task:{derivation.task_kind}",
                            target_system="external",
                            target_item_id=mapping.target_item_id,
                            last_synced_etag=event.etag,
                        )
                        continue

                    if (
                        derivation.caps.max_create_per_run is not None
                        and create_count >= derivation.caps.max_create_per_run
                    ):
                        continue

                    cap_group, cap_limit = self._cap_fields(
                        derivation.caps.max_create_per_run,
                        self._cap_group("tasks.create", source.source_id),
                    )
                    create_args = {
                        "content": event.title,
                        "description": self._task_description(event),
                        "priority": derivation.default_priority,
                        "labels": derivation.labels,
                    }
                    if project_id:
                        create_args["project_id"] = project_id
                    due_date = self._task_due_date(event)
                    if due_date:
                        create_args["due_date"] = due_date
                    action = ActionRequest(
                        capability_name="tasks.create@v1",
                        args=create_args,
                        side_effect=SideEffect.EXTERNAL_WRITE,
                        idempotency_key=self._task_idempotency_key(
                            event, derivation.task_kind
                        ),
                        cap_group=cap_group,
                        cap_limit=cap_limit,
                    )
                    actions.append(action)
                    create_count += 1
                    state.action_context[action.action_id] = DerivedActionContext(
                        source_system=source.provider,
                        source_container_id=source.calendar_id,
                        source_item_id=event.event_id,
                        derivation_kind=f"external_task:{derivation.task_kind}",
                        target_system="external",
                        target_item_id=None,
                        last_synced_etag=event.etag,
                    )

        return Plan(
            intent="Derive tasks from calendar events",
            summary=f"Generated {len(actions)} task actions from calendar events.",
            actions=actions,
        )

    async def _step_derive_meeting_notes_from_events(
        self,
        state: CalendarDerivationState,
        agent_profile: AgentProfile,
    ) -> Plan:
        logger.debug("step_derive_meeting_notes_from_events")
        return await self._derive_meeting_notes(
            state=state,
            agent_profile=agent_profile,
            refresh_only=False,
        )

    async def _step_refresh_meeting_notes_auto_block(
        self,
        state: CalendarDerivationState,
        agent_profile: AgentProfile,
    ) -> Plan:
        logger.debug("step_refresh_meeting_notes_auto_block")
        return await self._derive_meeting_notes(
            state=state,
            agent_profile=agent_profile,
            refresh_only=True,
        )

    async def _derive_meeting_notes(
        self,
        *,
        state: CalendarDerivationState,
        agent_profile: AgentProfile,
        refresh_only: bool,
    ) -> Plan:
        mapping_store, suppression_registry = self._get_derivation_stores()
        actions: list[ActionRequest] = []
        now = utc_now()
        today = now.date()
        one_on_ones: list[CalendarEventRecord] = []
        group_links: list[str] = []
        seen_daily_events: set[str] = set()
        seen_group_links: set[str] = set()

        def add_group_link(note_path: str) -> None:
            link_path = note_path[:-3] if note_path.endswith(".md") else note_path
            link = f"[[{link_path}]]"
            if link in seen_group_links:
                return
            seen_group_links.add(link)
            group_links.append(link)

        for source in state.sources.values():
            events = state.events_by_source.get(source.source_id, [])
            if not events:
                continue
            for derivation in source.meeting_note_derivations:
                create_count = 0
                update_count = 0
                for event in events:
                    if event.status == "cancelled":
                        continue
                    if not self._is_meeting_accepted(event):
                        continue
                    if refresh_only and not self._event_starts_within(event, minutes=30):
                        continue

                    is_today = (
                        event.start is not None and event.start.date() == today
                    )
                    is_one_on_one = self._is_one_on_one_event(event)
                    event_key = f"{source.calendar_id}:{event.event_id}"
                    if is_one_on_one:
                        if (
                            not refresh_only
                            and is_today
                            and event_key not in seen_daily_events
                        ):
                            seen_daily_events.add(event_key)
                            one_on_ones.append(event)
                        continue

                    suppression_key = f"{source.calendar_id}:{event.event_id}"
                    suppression = suppression_registry.get_suppression(
                        source_system=source.provider,
                        source_item_id=suppression_key,
                        artifact_kind="obsidian_meeting_note",
                    )
                    if suppression and suppression.is_active(now):
                        continue

                    mapping = mapping_store.get_mapping(
                        source_system=source.provider,
                        source_container_id=source.calendar_id,
                        source_item_id=event.event_id,
                        derivation_kind="obsidian_meeting_note",
                    )

                    note_path: str | None = None
                    note_payload: dict[str, Any] | None = None
                    if mapping:
                        note_path = mapping.target_item_id
                        note_payload = await self._read_obsidian_note(
                            note_path, agent_profile
                        )
                        if note_payload is None:
                            suppressed_until = now + timedelta(
                                hours=derivation.suppression_ttl_hours
                            )
                            suppression_registry.put_suppression(
                                SuppressionRecord(
                                    source_system=source.provider,
                                    source_item_id=suppression_key,
                                    artifact_kind="obsidian_meeting_note",
                                    suppressed_until=suppressed_until,
                                    reason="note_missing",
                                )
                            )
                            mapping_store.delete_mapping(
                                source_system=source.provider,
                                source_container_id=source.calendar_id,
                                source_item_id=event.event_id,
                                derivation_kind="obsidian_meeting_note",
                            )
                            continue

                    if note_payload is None:
                        found = await self._find_meeting_note_by_frontmatter(
                            derivation.meeting_folder,
                            source.provider,
                            source.calendar_id,
                            event.event_id,
                            agent_profile,
                        )
                        if found:
                            note_path, note_payload = found

                    if note_payload is None and refresh_only:
                        continue

                    frontmatter = note_payload.get("frontmatter", {}) if note_payload else {}
                    merged_frontmatter = self._merge_frontmatter(
                        frontmatter,
                        self._meeting_frontmatter_update(event, source),
                    )
                    merged_frontmatter = self._ensure_meeting_tags(
                        merged_frontmatter, derivation
                    )

                    auto_block = self._meeting_auto_block(event)
                    note_body = note_payload.get("content", "") if note_payload else ""
                    updated_body = update_reserved_block(
                        note_body,
                        ReservedBlockType.MEETING_AUTO,
                        auto_block,
                    )

                    if not refresh_only and is_today and note_path:
                        add_group_link(note_path)

                    if note_payload is None:
                        if (
                            derivation.caps.max_create_per_run is not None
                            and create_count >= derivation.caps.max_create_per_run
                        ):
                            continue
                        cap_group, cap_limit = self._cap_fields(
                            derivation.caps.max_create_per_run,
                            self._cap_group(
                                "obsidian.meeting_note.create", source.source_id
                            ),
                        )
                        note_id = f"note_{generate_ulid()}"
                        merged_frontmatter.setdefault("id", note_id)
                        new_body = self._render_meeting_template(
                            derivation.template,
                            {
                                "NOTE_ID": note_id,
                                "PROVIDER": source.provider,
                                "CALENDAR_ID": source.calendar_id,
                                "EVENT_ID": event.event_id,
                                "ETAG": event.etag or "",
                                "SYNCED_AT": now.isoformat(),
                                "TITLE": event.title,
                                "START": self._format_event_time(event.start),
                                "END": self._format_event_time(event.end),
                                "LOCATION": event.location or "",
                                "ATTENDEES": ", ".join(event.attendees),
                                "CONFERENCE_LINK": event.zoom_link or event.conference_link or "",
                                "UPDATED_AT": self._format_event_time(event.updated_at),
                                "AUTO_PROJECT_SUGGESTION": "",
                                "AUTO_LINKS": "",
                            },
                        )
                        meeting_folder = self._resolve_meeting_folder(derivation, event)
                        note_path = self._meeting_note_path(
                            meeting_folder, event
                        )
                        note_path = await self._ensure_unique_note_path(
                            note_path, agent_profile
                        )
                        if not refresh_only and is_today:
                            add_group_link(note_path)
                        action = ActionRequest(
                            capability_name="obsidian.create@v1",
                            args={
                                "path": note_path,
                                "content": new_body,
                                "frontmatter": merged_frontmatter,
                            },
                            side_effect=SideEffect.LOCAL_WRITE,
                            idempotency_key=self._meeting_idempotency_key(event),
                            cap_group=cap_group,
                            cap_limit=cap_limit,
                        )
                        actions.append(action)
                        create_count += 1
                        state.action_context[action.action_id] = DerivedActionContext(
                            source_system=source.provider,
                            source_container_id=source.calendar_id,
                            source_item_id=event.event_id,
                            derivation_kind="obsidian_meeting_note",
                            target_system="obsidian",
                            target_item_id=None,
                            last_synced_etag=event.etag,
                        )
                        continue

                    if (
                        derivation.caps.max_update_per_run is not None
                        and update_count >= derivation.caps.max_update_per_run
                    ):
                        continue
                    cap_group, cap_limit = self._cap_fields(
                        derivation.caps.max_update_per_run,
                        self._cap_group(
                            "obsidian.meeting_note.update", source.source_id
                        ),
                    )

                    action = ActionRequest(
                        capability_name="obsidian.update@v1",
                        args={
                            "path": note_path,
                            "content": updated_body,
                            "frontmatter": merged_frontmatter,
                            "skip_if_no_change": True,
                        },
                        side_effect=SideEffect.LOCAL_WRITE,
                        idempotency_key=self._meeting_update_idempotency_key(event),
                        cap_group=cap_group,
                        cap_limit=cap_limit,
                    )
                    actions.append(action)
                    update_count += 1
                    state.action_context[action.action_id] = DerivedActionContext(
                        source_system=source.provider,
                        source_container_id=source.calendar_id,
                        source_item_id=event.event_id,
                        derivation_kind="obsidian_meeting_note",
                        target_system="obsidian",
                        target_item_id=note_path,
                        last_synced_etag=event.etag,
                    )

        if not refresh_only:
            daily_note = await self._get_daily_note(agent_profile, today)
            if isinstance(daily_note, dict) and daily_note.get("path"):
                meeting_block = self._build_daily_meetings_block(
                    one_on_ones=one_on_ones,
                    group_links=group_links,
                )
                daily_content = daily_note.get("content", "")
                updated_daily = insert_reserved_block(
                    daily_content,
                    ReservedBlockType.MEETING_TODAY,
                    meeting_block,
                    after_heading="Notes",
                )
                action = ActionRequest(
                    capability_name="obsidian.update@v1",
                    args={
                        "path": daily_note["path"],
                        "content": updated_daily,
                        "skip_if_no_change": True,
                    },
                    side_effect=SideEffect.LOCAL_WRITE,
                    idempotency_key=f"obsidian.daily.meetings::{today.isoformat()}",
                )
                actions.append(action)

        summary = (
            "Refreshed meeting note AUTO blocks."
            if refresh_only
            else "Created or updated meeting notes from calendar events."
        )
        return Plan(
            intent="Derive meeting notes from calendar events",
            summary=summary,
            actions=actions,
        )

    async def _apply_derivation_results(
        self,
        trace: DecisionTrace,
        state: CalendarDerivationState,
    ) -> None:
        mapping_store, _ = self._get_derivation_stores()
        now = utc_now()

        for call in trace.tool_calls:
            if call.status != CallStatus.SUCCESS:
                continue
            action_id = call.related_action_id
            if not action_id:
                continue
            context = state.action_context.get(action_id)
            if not context:
                continue

            target_item_id = context.target_item_id or self._extract_target_item_id(
                context.target_system, call.output
            )
            if not target_item_id:
                continue

            mapping_store.put_mapping(DerivationMappingRecord(
                source_system=context.source_system,
                source_container_id=context.source_container_id,
                source_item_id=context.source_item_id,
                derivation_kind=context.derivation_kind,
                target_system=context.target_system,
                target_item_id=target_item_id,
                last_synced_etag=context.last_synced_etag,
                last_synced_at=now,
            ))

    def _extract_events_list(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if "events" in payload and isinstance(payload["events"], list):
            return [e for e in payload["events"] if isinstance(e, dict)]
        if "items" in payload and isinstance(payload["items"], list):
            return [e for e in payload["items"] if isinstance(e, dict)]
        if "results" in payload and isinstance(payload["results"], list):
            return [e for e in payload["results"] if isinstance(e, dict)]
        return []

    def _normalize_calendar_event(
        self, raw: dict[str, Any], source: CalendarSourceConfig
    ) -> CalendarEventRecord | None:
        event_id = raw.get("id") or raw.get("event_id") or raw.get("uid")
        if not event_id:
            return None
        title = raw.get("summary") or raw.get("title") or "(untitled)"
        start, all_day = self._parse_event_time(raw.get("start"))
        end, _ = self._parse_event_time(raw.get("end"))
        raw_all_day = raw.get("all_day")
        if isinstance(raw_all_day, bool):
            all_day = raw_all_day
        elif not all_day and isinstance(raw.get("start"), str):
            # Handle formatted events that provide a date-only start string.
            if len(raw.get("start")) == 10:
                all_day = True
        status = str(raw.get("status", "confirmed")).lower()
        updated_at = self._parse_datetime(raw.get("updated") or raw.get("updated_at"))
        etag = raw.get("etag")
        location = raw.get("location")

        attendees_raw = raw.get("attendees", []) or []
        attendees: list[str] = []
        if isinstance(attendees_raw, list):
            for attendee in attendees_raw:
                if isinstance(attendee, dict):
                    label = (
                        attendee.get("email")
                        or attendee.get("display_name")
                        or attendee.get("displayName")
                    )
                    if label:
                        attendees.append(label)
                elif isinstance(attendee, str):
                    attendees.append(attendee)

        conference_link = raw.get("hangoutLink") or raw.get("hangout_link")
        if not conference_link:
            conference_data = raw.get("conferenceData") or raw.get("conference_data")
            if isinstance(conference_data, dict):
                entry_points = conference_data.get("entryPoints", []) or conference_data.get(
                    "entry_points", []
                )
                for entry in entry_points:
                    if isinstance(entry, dict) and entry.get("uri"):
                        conference_link = entry.get("uri")
                        break

        zoom_link = None
        for candidate in (raw.get("description"), location):
            zoom_link = self._extract_zoom_link(candidate)
            if zoom_link:
                break

        return CalendarEventRecord(
            source_id=source.source_id,
            provider=source.provider,
            calendar_id=source.calendar_id,
            event_id=str(event_id),
            title=str(title),
            description=raw.get("description"),
            start=start,
            end=end,
            all_day=all_day,
            status=status,
            updated_at=updated_at,
            etag=etag,
            location=location,
            attendees=attendees,
            conference_link=conference_link,
            zoom_link=zoom_link,
            raw=raw,
        )

    def _event_passes_filters(
        self, event: CalendarEventRecord, filters: CalendarSourceFilters
    ) -> bool:
        if filters.exclude_all_day and event.all_day:
            return False
        if filters.exclude_title_prefixes:
            title_lower = event.title.lower()
            for prefix in filters.exclude_title_prefixes:
                if title_lower.startswith(str(prefix).lower()):
                    return False
        if filters.exclude_title_keywords:
            title_lower = event.title.lower()
            for keyword in filters.exclude_title_keywords:
                if str(keyword).lower() in title_lower:
                    return False
        if filters.require_attendees_or_conference:
            if not event.attendees and not event.conference_link:
                return False
        if filters.require_zoom_link and not event.zoom_link:
            return False
        return True

    def _extract_zoom_link(self, text: str | None) -> str | None:
        if not text:
            return None
        match = re.search(
            r"https?://(?:[^\s/]+\.)?(?:zoom\.us|zoomgov\.com)/\S+",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        return match.group(0).rstrip(").,>")

    def _is_one_on_one_title(self, title: str) -> bool:
        cleaned = title.strip()
        if not cleaned:
            return False
        if re.search(r"\b1\s*:\s*1\b", cleaned, re.IGNORECASE):
            return True
        if re.match(r"^\s*[^:<>]+\s*:\s*[^:<>]+\s*$", cleaned):
            return True
        if re.match(r"^\s*[^<>]+\s*<>\s*[^<>]+\s*$", cleaned):
            return True
        return False

    def _is_one_on_one_event(self, event: CalendarEventRecord) -> bool:
        if event.attendees:
            return len(event.attendees) == 2
        return self._is_one_on_one_title(event.title)

    def _parse_event_time(
        self, payload: Any
    ) -> tuple[datetime | None, bool]:
        if isinstance(payload, dict):
            if payload.get("dateTime"):
                return self._parse_datetime(payload.get("dateTime")), False
            if payload.get("date"):
                return self._parse_date(payload.get("date")), True
        if isinstance(payload, str):
            return self._parse_datetime(payload), False
        return None, False

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not value or not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None

    def _parse_date(self, value: Any) -> datetime | None:
        if not value or not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None

    async def _resolve_task_project_id(
        self,
        derivation: TaskDerivationConfig,
        state: CalendarDerivationState,
        agent_profile: AgentProfile,
    ) -> str | None:
        if derivation.project_id:
            return derivation.project_id
        if not derivation.project_name:
            return None
        cached = state.task_projects_by_name.get(derivation.project_name)
        if cached:
            return cached

        action = ActionRequest(
            capability_name="tasks.projects.list@v1",
            args={},
            side_effect=SideEffect.NONE,
        )
        calls = await self._executor.execute_actions([action], agent_profile)
        projects = []
        if calls and isinstance(calls[0].output, dict):
            projects = calls[0].output.get("projects", []) or []
        for project in projects:
            if not isinstance(project, dict):
                continue
            name = project.get("name")
            project_id = project.get("id")
            if name and project_id:
                state.task_projects_by_name[str(name)] = str(project_id)
        return state.task_projects_by_name.get(derivation.project_name)

    def _task_description(self, event: CalendarEventRecord) -> str:
        lines = []
        if event.start:
            lines.append(f"When: {self._format_event_time(event.start)}")
        if event.location:
            lines.append(f"Where: {event.location}")
        if event.conference_link:
            lines.append(f"Link: {event.conference_link}")
        if event.description:
            trimmed = event.description.strip()
            if trimmed:
                lines.append(f"Notes: {trimmed[:300]}")
        return "\n".join(lines)

    def _task_due_date(self, event: CalendarEventRecord) -> str | None:
        if not event.start:
            return None
        return event.start.date().isoformat()

    def _task_due_string(self, event: CalendarEventRecord) -> str | None:
        if not event.start:
            return None
        return event.start.date().isoformat()

    def _task_idempotency_key(
        self, event: CalendarEventRecord, task_kind: str
    ) -> str:
        return (
            f"tasks.calendar_event::{event.calendar_id}::"
            f"{event.event_id}::{task_kind}"
        )

    def _meeting_idempotency_key(self, event: CalendarEventRecord) -> str:
        return f"obsidian.meeting_note::{event.calendar_id}::{event.event_id}"

    def _meeting_update_idempotency_key(self, event: CalendarEventRecord) -> str:
        etag = event.etag or "no-etag"
        return (
            f"obsidian.meeting_note.update::{event.calendar_id}::"
            f"{event.event_id}::{etag}"
        )

    async def _get_daily_note(
        self,
        agent_profile: AgentProfile,
        note_date: date | None = None,
    ) -> dict[str, Any] | None:
        args: dict[str, Any] = {}
        if note_date:
            args["date"] = note_date.isoformat()
        action = ActionRequest(
            capability_name="obsidian.daily@v1",
            args=args,
            side_effect=SideEffect.LOCAL_WRITE,
        )
        calls = await self._executor.execute_actions([action], agent_profile)
        if not calls:
            return None
        output = calls[0].output
        if isinstance(output, dict):
            note = output.get("note")
            if isinstance(note, dict):
                return note
        return None

    async def _read_obsidian_note(
        self, path: str, agent_profile: AgentProfile
    ) -> dict[str, Any] | None:
        action = ActionRequest(
            capability_name="obsidian.read@v1",
            args={"path": path},
            side_effect=SideEffect.NONE,
        )
        calls = await self._executor.execute_actions([action], agent_profile)
        if not calls:
            return None
        output = calls[0].output
        if isinstance(output, dict) and output.get("note"):
            return output["note"]
        return None

    async def _find_meeting_note_by_frontmatter(
        self,
        meeting_folder: str,
        provider: str,
        calendar_id: str,
        event_id: str,
        agent_profile: AgentProfile,
    ) -> tuple[str, dict[str, Any]] | None:
        list_action = ActionRequest(
            capability_name="obsidian.list@v1",
            args={"folder": meeting_folder, "recursive": True},
            side_effect=SideEffect.NONE,
        )
        calls = await self._executor.execute_actions([list_action], agent_profile)
        if not calls:
            return None
        paths = []
        if isinstance(calls[0].output, dict):
            paths = calls[0].output.get("notes", []) or []
        for path in paths:
            if not isinstance(path, str):
                continue
            note = await self._read_obsidian_note(path, agent_profile)
            if not note:
                continue
            frontmatter = note.get("frontmatter", {}) if isinstance(note, dict) else {}
            if self._frontmatter_matches_event(
                frontmatter, provider, calendar_id, event_id
            ):
                return path, note
        return None

    def _frontmatter_matches_event(
        self,
        frontmatter: dict[str, Any],
        provider: str,
        calendar_id: str,
        event_id: str,
    ) -> bool:
        auto = frontmatter.get("auto", {}) if isinstance(frontmatter, dict) else {}
        source = auto.get("source", {}) if isinstance(auto, dict) else {}
        calendar = source.get("calendar", {}) if isinstance(source, dict) else {}
        return (
            calendar.get("provider") == provider
            and calendar.get("calendar_id") == calendar_id
            and calendar.get("event_id") == event_id
        )

    def _meeting_frontmatter_update(
        self, event: CalendarEventRecord, source: CalendarSourceConfig
    ) -> dict[str, Any]:
        return {
            "auto": {
                "source": {
                    "calendar": {
                        "provider": source.provider,
                        "calendar_id": source.calendar_id,
                        "event_id": event.event_id,
                        "etag": event.etag,
                        "last_synced_at": utc_now().isoformat(),
                    }
                }
            }
        }

    def _ensure_meeting_tags(
        self,
        frontmatter: dict[str, Any],
        derivation: MeetingNoteDerivationConfig,
    ) -> dict[str, Any]:
        tags = frontmatter.get("tags", [])
        if isinstance(tags, str):
            tags = tags.split()
        if not isinstance(tags, list):
            tags = []

        desired_tags = ["meeting"]
        if "work" in derivation.meeting_folder.lower():
            desired_tags.append("work")
        for tag in desired_tags:
            if tag not in tags:
                tags.append(tag)
        frontmatter["tags"] = tags
        frontmatter.setdefault("type", "meeting")
        return frontmatter

    def _merge_frontmatter(
        self, base: dict[str, Any], updates: dict[str, Any]
    ) -> dict[str, Any]:
        merged = dict(base)
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_frontmatter(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _meeting_auto_block(self, event: CalendarEventRecord) -> str:
        lines = [
            "## Auto (Do not edit inside this block)",
            f"- Event updated: {self._format_event_time(event.updated_at)}",
            "- Suggested project: ",
            "- Suggested context links:",
            "  - ",
        ]
        return "\n".join(lines)

    def _build_daily_meetings_block(
        self,
        one_on_ones: list[CalendarEventRecord],
        group_links: list[str],
    ) -> str:
        lines = ["## Meetings"]
        if not one_on_ones and not group_links:
            lines.append("No meetings scheduled.")
            return "\n".join(lines)

        lines.append("")
        lines.append("### 1:1s")
        if one_on_ones:
            for event in one_on_ones:
                time = self._format_event_time(event.start)
                prefix = f"- {time} " if time else "- "
                lines.append(f"{prefix}{event.title}")
        else:
            lines.append("- None")

        lines.append("")
        lines.append("### Group Meetings")
        if group_links:
            for link in group_links:
                lines.append(f"- {link}")
        else:
            lines.append("- None")

        return "\n".join(lines)

    def _render_meeting_template(
        self, template_path: str, values: dict[str, str]
    ) -> str:
        template_file = Path(template_path)
        if not template_file.is_absolute():
            template_file = Path(self._configs_dir).parent / template_path
        content = template_file.read_text(encoding="utf-8")
        for key, value in values.items():
            content = content.replace(f"{{{{{key}}}}}", str(value))
        return content

    def _meeting_note_path(
        self, meeting_folder: str, event: CalendarEventRecord
    ) -> str:
        date_prefix = (
            event.start.strftime("%Y-%m-%d") if event.start else "meeting"
        )
        title = self._sanitize_filename(event.title or "meeting")
        filename = f"{date_prefix} {title}".strip()
        return f"{meeting_folder}/{filename}.md"

    def _resolve_meeting_folder(
        self,
        derivation: MeetingNoteDerivationConfig,
        event: CalendarEventRecord,
    ) -> str:
        base = derivation.meeting_folder
        title = event.title or ""
        project = self._project_from_mapping(title, derivation.project_folder_map)
        if not project:
            project = self._project_from_title(title)
        if not project:
            return base
        project = self._sanitize_folder_name(project)
        return f"{base}/{project}"

    def _project_from_mapping(
        self, title: str, project_folder_map: dict[str, str]
    ) -> str | None:
        title_lower = title.lower()
        for keyword, folder in project_folder_map.items():
            if keyword and keyword.lower() in title_lower:
                return folder or keyword
        return None

    def _project_from_title(self, title: str) -> str | None:
        match = re.match(r"^\\[(.+?)\\]", title)
        if match:
            return match.group(1).strip()
        match = re.match(r"^project\\s*:\\s*(.+)$", title, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    def _sanitize_folder_name(self, name: str, max_length: int = 60) -> str:
        sanitized = re.sub(r'[<>:"/\\\\|?*]', "", name).strip()
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length].rsplit(" ", 1)[0]
        return sanitized or "misc"

    async def _ensure_unique_note_path(
        self, path: str, agent_profile: AgentProfile
    ) -> str:
        note = await self._read_obsidian_note(path, agent_profile)
        if note is None:
            return path
        stem = Path(path).stem
        suffix = path.replace(stem, f"{stem}-1", 1)
        return suffix

    def _sanitize_filename(self, title: str, max_length: int = 60) -> str:
        name = re.sub(r'[<>:"/\\|?*]', "", title).strip()
        if len(name) > max_length:
            name = name[:max_length].rsplit(" ", 1)[0]
        return name or "meeting"

    def _format_event_time(self, value: datetime | None) -> str:
        if not value:
            return ""
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    def _cap_group(self, prefix: str, source_id: str) -> str:
        return f"{prefix}:{source_id}"

    def _cap_fields(
        self, limit: int | None, group: str
    ) -> tuple[str | None, int | None]:
        if limit is None:
            return None, None
        return group, limit

    def _empty_context_packet(
        self, intent: str, agent_profile: AgentProfile
    ) -> ContextPacket:
        return ContextPacket(
            intent=intent,
            budget=ContextBudget(max_tokens=agent_profile.context_policy.max_tokens),
            items=[],
        )

    def _build_deterministic_plan(
        self,
        intent: str,
        capability: str,
        args: dict[str, Any] | None = None,
    ) -> Plan:
        """Build a synthetic Plan for deterministic workflows.

        Creates a single-action plan targeting the given capability
        without any LLM call.
        """
        action_id = generate_ulid()
        return Plan(
            plan_id=generate_ulid(),
            intent=intent,
            summary=f"Deterministic execution of {capability}",
            context_refs_used=[],
            actions=[
                ActionRequest(
                    action_id=action_id,
                    capability_name=capability,
                    args=args or {},
                    side_effect=SideEffect.LOCAL_WRITE,
                    requires_approval=False,
                    evidence_refs=[],
                    idempotency_key=f"det_{action_id}",
                ),
            ],
            risk={"level": "low", "reasons": ["Deterministic capability"]},
            validation={"missing_info": [], "assumptions": []},
        )

    def _build_deterministic_args(self, spec: WorkflowSpec) -> dict[str, Any]:
        """Extract capability args from workflow metadata.

        Maps well-known metadata keys to capability input args:
        - metadata.target_projects → project_names (for task sync)
        """
        args: dict[str, Any] = {}
        metadata = spec.metadata

        # Map target_projects to project_names for task sync capabilities
        target_projects = metadata.get("target_projects")
        if target_projects and isinstance(target_projects, list):
            args["project_names"] = target_projects

        return args

    async def _run_empty_check(
        self, check: EmptyCheck, agent_profile: AgentProfile
    ) -> bool:
        """Run an empty-poll pre-check via ToolBroker.

        Returns True if the workflow should be skipped (result is empty).
        On any error, returns False (default to running the workflow).
        """
        try:
            action = ActionRequest(
                action_id=generate_ulid(),
                capability_name=check.capability,
                args=check.args,
                side_effect=SideEffect.NONE,
                requires_approval=False,
                evidence_refs=[],
            )
            record = await self._executor._broker.execute(
                capability_name=action.capability_name,
                args=action.args,
                agent_profile=agent_profile,
                action_id=action.action_id,
            )
            if record.status != CallStatus.SUCCESS:
                return False

            output = record.output
            if check.empty_key:
                value = output.get(check.empty_key, [])
            else:
                value = output

            # Check emptiness: empty list, empty dict, None, or falsy
            if isinstance(value, list):
                return len(value) == 0
            if isinstance(value, dict):
                return len(value) == 0
            return not value
        except Exception:
            logger.warning(
                "empty_check_failed_running_workflow",
                capability=check.capability,
                exc_info=True,
            )
            return False

    def _extract_target_item_id(
        self, target_system: str, output: dict[str, Any]
    ) -> str | None:
        if target_system == "external":
            task = output.get("task") if isinstance(output, dict) else None
            if isinstance(task, dict):
                return str(task.get("id") or task.get("task_id") or "")
        if target_system == "obsidian":
            if isinstance(output, dict):
                if output.get("path"):
                    return str(output.get("path"))
                note = output.get("note")
                if isinstance(note, dict) and note.get("path"):
                    return str(note.get("path"))
        return None

    def _event_starts_within(
        self, event: CalendarEventRecord, minutes: int
    ) -> bool:
        if not event.start:
            return False
        now = utc_now()
        window_end = now + timedelta(minutes=minutes)
        return now <= event.start <= window_end

    def _is_meeting_accepted(self, event: CalendarEventRecord) -> bool:
        if event.status in {"cancelled", "tentative"}:
            return False

        raw_attendees = []
        if isinstance(event.raw, dict):
            raw_attendees = event.raw.get("attendees", []) or []

        if isinstance(raw_attendees, list):
            for attendee in raw_attendees:
                if not isinstance(attendee, dict):
                    continue
                if not attendee.get("self"):
                    continue
                response = (
                    attendee.get("response_status")
                    or attendee.get("responseStatus")
                    or attendee.get("response_status")
                )
                if response:
                    return str(response).lower() == "accepted"

        return event.status not in {"tentative", "cancelled"}

    async def _step_assemble_context(
        self,
        intent: str,
        agent_profile: AgentProfile,
        project_id: str | None,
        workflow_id: str | None = None,
        thinking_session: ThinkingSession | None = None,
    ) -> ContextPacket:
        """Assemble context step.

        v1.0.3: Uses thinking config for tier-aware retrieval.
        """
        logger.debug("step_assemble_context", intent=intent)

        # Use thinking-config-aware retrieval if session exists
        if thinking_session is not None and agent_profile.thinking_config:
            retrieval_config = self._thinking_controller.get_retrieval_config(
                thinking_session
            )
            policy = self._thinking_controller.get_policy(thinking_session)
            return await self._assembler.assemble_with_thinking(
                intent=intent,
                agent_profile=agent_profile,
                retrieval_config=retrieval_config,
                max_context_tokens=policy.max_context_tokens,
                project_id=project_id,
                workflow_id=workflow_id,
            )

        return await self._assembler.assemble_async(
            intent=intent,
            policy=agent_profile.context_policy,
            project_id=project_id,
            workflow_id=workflow_id,
            agent_profile_id=agent_profile.agent_profile_id,
        )

    async def _step_propose_plan(
        self,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
        engine: AgentEngine,
        thinking_session: ThinkingSession | None = None,
    ) -> Plan:
        """Propose plan step.

        v1.0.3: Uses thinking policy for model/reasoning configuration.
        Passes thinking policy to engine so it can override LLM parameters.
        """
        logger.debug("step_propose_plan", engine_id=engine.engine_id)

        # Get policy from session if available
        policy: ThinkingPolicy | None = None
        if thinking_session:
            policy = self._thinking_controller.get_policy(thinking_session)
            logger.debug(
                "propose_with_thinking_policy",
                tier=policy.tier,
                tier_name=policy.tier_name,
                model=policy.model_id,
                reasoning_effort=policy.reasoning_effort,
            )

        return await engine.propose(context_packet, agent_profile, thinking_policy=policy)

    async def _maybe_replan_with_skills(
        self,
        plan: Plan,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
        engine: AgentEngine,
        thinking_policy: ThinkingPolicy | None = None,
    ) -> tuple[Plan, ContextPacket, dict[str, Any]]:
        skill_actions = self._extract_skill_load_actions(plan)
        if not skill_actions:
            return plan, context_packet, {}

        tool_calls = await self._executor.execute_actions(skill_actions, agent_profile)
        skill_items, loaded_files, invoked_ids = self._build_skill_context(tool_calls)
        updated_packet = self._append_skill_items(context_packet, skill_items)
        replan = await engine.propose(
            updated_packet, agent_profile, thinking_policy=thinking_policy
        )
        replan = self._strip_skill_load_actions(replan)

        considered = self._skills_from_context(updated_packet)
        usage = {
            "considered": considered,
            "invoked": invoked_ids,
            "loaded_files": loaded_files,
        }
        return replan, updated_packet, usage

    def _extract_skill_load_actions(self, plan: Plan) -> list[ActionRequest]:
        return [
            action
            for action in plan.actions
            if action.capability_name == "skills.load@v1"
        ]

    def _strip_skill_load_actions(self, plan: Plan) -> Plan:
        remaining = [
            action
            for action in plan.actions
            if action.capability_name != "skills.load@v1"
        ]
        if len(remaining) == len(plan.actions):
            return plan
        return plan.model_copy(update={"actions": remaining})

    def _build_skill_context(
        self,
        tool_calls: list[ToolCallRecord],
    ) -> tuple[list[ContextItem], list[SkillResourceRef], list[str]]:
        items: list[ContextItem] = []
        resources: list[SkillResourceRef] = []
        invoked: list[str] = []

        for call in tool_calls:
            if call.capability_name != "skills.load@v1":
                continue
            payload = call.output.get("skill") if isinstance(call.output, dict) else None
            if not payload or not isinstance(payload, dict):
                continue

            manifest = payload.get("manifest", {}) if isinstance(payload.get("manifest"), dict) else {}
            skill_id = manifest.get("skill_id") or call.input.get("skill_id")
            if skill_id:
                invoked.append(str(skill_id))

            name = manifest.get("name") or str(skill_id or "skill")
            origin = manifest.get("origin", {}) if isinstance(manifest.get("origin"), dict) else {}
            files = payload.get("files", {}) if isinstance(payload.get("files"), dict) else {}

            for path, content in files.items():
                excerpt = self._truncate_text(str(content), 1600)
                ref = ContextRef(
                    ref_type=RefType.SKILL,
                    ref_id=f"{skill_id}:{path}" if skill_id else str(path),
                    uri=origin.get("path"),
                    hash=origin.get("content_hash"),
                    metadata={
                        "title": name,
                        "name": name,
                        "skill_id": skill_id or "",
                        "file": path,
                        "origin_kind": origin.get("kind", ""),
                    },
                )
                items.append(
                    ContextItem(
                        ref=ref,
                        excerpt=excerpt,
                        summary=f"{name} ({path})",
                        relevance_score=0.9,
                        included_reason="skill_load",
                    )
                )

            for resource in payload.get("resources", []):
                try:
                    resources.append(SkillResourceRef(**resource))
                except Exception:
                    continue

        return items, resources, sorted(set(invoked))

    async def _maybe_replan_for_enrichment(
        self,
        trace: DecisionTrace,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
        engine: AgentEngine,
    ) -> tuple[Plan | None, ContextPacket]:
        """For task_enrichment workflow: if fetch actions succeeded, generate update actions.
        
        This checks if we successfully fetched tasks/projects/labels, adds that data to context,
        and generates a new plan with enrichment update actions.
        """
        # Check if we have successful fetch actions
        fetch_capabilities = {
            "tasks.list@v1",
            "tasks.projects.list@v1",
            "tasks.labels.list@v1",
        }
        
        fetch_results: dict[str, Any] = {}
        has_tasks = False
        
        for tool_call in trace.tool_calls:
            if tool_call.capability_name in fetch_capabilities:
                if tool_call.status == CallStatus.SUCCESS and isinstance(tool_call.output, dict):
                    fetch_results[tool_call.capability_name] = tool_call.output
                    if tool_call.capability_name == "tasks.list@v1":
                        tasks = tool_call.output.get("tasks", [])
                        if tasks:
                            has_tasks = True
        
        # Only replan if we have tasks to enrich
        if not has_tasks:
            logger.debug(
                "enrichment_replan_skipped",
                reason="no_tasks_fetched",
                fetch_capabilities_found=[tc.capability_name for tc in trace.tool_calls if tc.capability_name in fetch_capabilities],
            )
            return None, context_packet
        
        # Build context items from fetch results
        enrichment_items: list[ContextItem] = []
        
        # Add projects data
        if "tasks.projects.list@v1" in fetch_results:
            projects_data = fetch_results["tasks.projects.list@v1"]
            projects_json = self._truncate_text(str(projects_data), 2000)
            ref = ContextRef(
                ref_type=RefType.EXTERNAL,
                ref_id="tasks:projects",
                metadata={"source": "external", "type": "projects"},
            )
            enrichment_items.append(
                ContextItem(
                    ref=ref,
                    excerpt=projects_json,
                    summary="Available External projects",
                    relevance_score=0.95,
                    included_reason="task_enrichment_fetch",
                )
            )
        
        # Add labels data
        if "tasks.labels.list@v1" in fetch_results:
            labels_data = fetch_results["tasks.labels.list@v1"]
            labels_json = self._truncate_text(str(labels_data), 1000)
            ref = ContextRef(
                ref_type=RefType.EXTERNAL,
                ref_id="tasks:labels",
                metadata={"source": "external", "type": "labels"},
            )
            enrichment_items.append(
                ContextItem(
                    ref=ref,
                    excerpt=labels_json,
                    summary="Available External labels",
                    relevance_score=0.95,
                    included_reason="task_enrichment_fetch",
                )
            )
        
        # Add tasks data (most important)
        tasks_to_enrich: list[dict[str, Any]] = []
        if "tasks.list@v1" in fetch_results:
            tasks_data = fetch_results["tasks.list@v1"]
            tasks = tasks_data.get("tasks", [])
            # Limit to first 50 tasks to avoid context overflow
            tasks_to_enrich = tasks[:50]
            # Format tasks more clearly with task_id prominently displayed
            # Each task dict from ExternalTask.to_dict() has 'id' field
            formatted_tasks = []
            for task in tasks_to_enrich:
                if isinstance(task, dict):
                    task_id = task.get("id") or task.get("task_id") or task.get("task_id")
                    formatted_tasks.append({
                        "task_id": task_id,  # CRITICAL: This is the ID to use in update actions
                        "id": task_id,  # Also include as 'id' for clarity
                        "content": task.get("content") or task.get("title", ""),
                        "description": task.get("description", ""),
                        "labels": task.get("labels", []),
                        "priority": task.get("priority", 1),
                        "project_id": task.get("project_id"),
                        "due": task.get("due") or task.get("due_string"),
                    })
            # Create a clear summary showing task IDs
            task_ids_list = [t["task_id"] for t in formatted_tasks if t.get("task_id")]
            tasks_summary = f"Found {len(formatted_tasks)} tasks. Task IDs: {', '.join(task_ids_list[:10])}{'...' if len(task_ids_list) > 10 else ''}"
            tasks_json = self._truncate_text(
                json.dumps({
                    "summary": tasks_summary,
                    "tasks": formatted_tasks,
                    "count": len(formatted_tasks)
                }, indent=2),
                15000,  # Increased limit to show more task details
            )
            ref = ContextRef(
                ref_type=RefType.EXTERNAL,
                ref_id="tasks:tasks",
                metadata={
                    "source": "external",
                    "type": "tasks",
                    "count": len(tasks_to_enrich),
                    "total": len(tasks),
                },
            )
            enrichment_items.append(
                ContextItem(
                    ref=ref,
                    excerpt=tasks_json,
                    summary=f"External tasks to enrich ({len(tasks_to_enrich)} of {len(tasks)})",
                    relevance_score=1.0,
                    included_reason="task_enrichment_fetch",
                )
            )
        
        if not enrichment_items:
            logger.debug("enrichment_replan_skipped", reason="no_fetch_results")
            return None, context_packet
        
        # Append enrichment items to context packet
        updated_items = list(context_packet.items) + enrichment_items
        # Create explicit intent for enrichment - make it clear we need UPDATE actions only
        enrichment_intent = f"""Enrich {len(tasks_to_enrich)} External tasks with metadata.

CRITICAL: External data has ALREADY been fetched and is provided in context.
DO NOT generate fetch actions (tasks.list@v1, tasks.projects.list@v1, tasks.labels.list@v1).
ONLY generate tasks.update@v1 actions - one per eligible task.

Generate AT LEAST 10-20 update actions to enrich tasks with labels, priority, due dates, and descriptions.
Focus on tasks in Inbox, tasks with few labels (0-2), tasks with no priority, and tasks needing organization.
Review all {len(tasks_to_enrich)} tasks and create update actions for eligible ones."""
        
        updated_packet = context_packet.model_copy(
            update={
                "items": updated_items,
                "packet_id": generate_ulid(),  # New packet ID for new context
                "intent": enrichment_intent,  # Override intent to be explicit about updates only
            }
        )
        
        # Generate new plan with update actions
        logger.info(
            "enrichment_replanning",
            tasks_count=len(tasks_to_enrich),
            enrichment_items=len(enrichment_items),
        )
        
        enrichment_plan = await engine.propose(updated_packet, agent_profile)
        
        # Filter out fetch actions from the new plan (keep only update actions)
        update_actions = [
            action
            for action in enrichment_plan.actions
            if action.capability_name not in fetch_capabilities
        ]
        
        if not update_actions:
            # Log what actions were generated for debugging
            generated_capabilities = [a.capability_name for a in enrichment_plan.actions]
            logger.warning(
                "enrichment_plan_no_updates",
                plan_actions=len(enrichment_plan.actions),
                generated_capabilities=generated_capabilities,
            )
            return None, updated_packet
        
        enrichment_plan = enrichment_plan.model_copy(update={"actions": update_actions})
        
        logger.info(
            "enrichment_plan_generated",
            update_actions=len(update_actions),
            total_plan_actions=len(enrichment_plan.actions),
            filtered_out=len(enrichment_plan.actions) - len(update_actions),
            plan_id=enrichment_plan.plan_id,
        )
        
        return enrichment_plan, updated_packet

    def _append_skill_items(
        self,
        context_packet: ContextPacket,
        skill_items: list[ContextItem],
    ) -> ContextPacket:
        if not skill_items:
            return context_packet

        new_items = skill_items + list(context_packet.items)
        report = context_packet.retrieval_report
        updated_report = report.model_copy(
            update={
                "items_considered": report.items_considered + len(skill_items),
                "items_selected": len(new_items),
                "filters_applied": list(report.filters_applied) + ["skills.load"],
            }
        )
        return context_packet.model_copy(
            update={"items": new_items, "retrieval_report": updated_report}
        )

    def _skills_from_context(self, context_packet: ContextPacket) -> list[str]:
        skill_ids: list[str] = []
        for item in context_packet.items:
            if item.ref.ref_type != RefType.SKILL:
                continue
            skill_id = item.ref.metadata.get("skill_id") if isinstance(item.ref.metadata, dict) else None
            if not skill_id:
                skill_id = str(item.ref.ref_id).split(":", 1)[0]
            if skill_id:
                skill_ids.append(str(skill_id))
        return sorted(set(skill_ids))

    def _truncate_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."

    async def _step_validate(
        self,
        plan: Plan,
        agent_profile: AgentProfile,
        context_packet: ContextPacket | None = None,
        thinking_session: ThinkingSession | None = None,
    ) -> tuple[bool, list[str]]:
        """Validate plan step.

        v1.0.3: Returns validation result for escalation decisions.
        """
        logger.debug("step_validate", plan_id=plan.plan_id)
        errors = self._executor.validate_plan(plan, agent_profile, context_packet)

        if errors:
            # Check if we should escalate instead of failing
            if thinking_session and thinking_session.can_escalate():
                logger.info(
                    "validation_failed_may_escalate",
                    errors=errors,
                    can_escalate=True,
                )
                return False, errors

            raise WorkflowExecutionError(
                f"Plan validation failed: {'; '.join(errors)}",
                "",
                "validate",
            )

        return True, []

    async def _step_execute(
        self,
        plan: Plan,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
        engine_id: str,
        run_id: str,
        workflow_id: str,
        approval_tokens: dict[str, str] | None,
        skill_usage_override: dict[str, Any] | None = None,
    ) -> DecisionTrace:
        """Execute plan step."""
        logger.debug("step_execute", plan_id=plan.plan_id)
        return await self._executor.execute(
            plan=plan,
            context_packet=context_packet,
            agent_profile=agent_profile,
            engine_id=engine_id,
            run_id=run_id,
            workflow_id=workflow_id,
            approval_tokens=approval_tokens,
            skill_usage_override=skill_usage_override,
        )

    async def resume(
        self,
        run_id: str,
        approval_tokens: dict[str, str],
    ) -> WorkflowResult:
        """Resume a workflow that was waiting for approval.

        Uses checkpoint system to restore state and resume from the
        last successful step instead of re-executing the entire workflow.

        Args:
            run_id: The workflow run ID to resume.
            approval_tokens: Approval tokens for pending actions.

        Returns:
            WorkflowResult with execution details.
        """
        # Try to get workflow run from memory or store
        workflow_run = self._workflow_runs.get(run_id)
        if workflow_run is None:
            workflow_run = self._workflow_store.get_run(run_id)

        if workflow_run is None:
            return WorkflowResult(
                workflow_id="",
                run_id=run_id,
                success=False,
                error=f"Workflow run not found: {run_id}",
                status=WorkflowRunStatus.FAILED,
            )

        if workflow_run.status != WorkflowRunStatus.WAITING_APPROVAL:
            return WorkflowResult(
                workflow_id=workflow_run.workflow_id,
                run_id=run_id,
                success=False,
                error=f"Workflow run is not waiting for approval: {workflow_run.status.value}",
                status=workflow_run.status,
                workflow_run=workflow_run,
            )

        # Load checkpoint
        checkpoint = self._workflow_store.get_checkpoint(run_id)

        # Update status to running
        self._update_workflow_run(run_id, status=WorkflowRunStatus.RUNNING)

        if checkpoint is None:
            # No checkpoint found, fall back to full re-run
            logger.warning(
                "resume_no_checkpoint_found",
                run_id=run_id,
                falling_back_to="full_rerun",
            )
            return await self.run(
                workflow_id=workflow_run.workflow_id,
                intent=workflow_run.intent,
                approval_tokens=approval_tokens,
            )

        # Resume from checkpoint
        logger.info(
            "resuming_from_checkpoint",
            run_id=run_id,
            step_index=checkpoint.step_index,
            step_name=checkpoint.step_name,
            resume_from_index=checkpoint.resume_from_index,
        )

        return await self._run_from_checkpoint(
            workflow_run=workflow_run,
            checkpoint=checkpoint,
            approval_tokens=approval_tokens,
        )

    async def _run_from_checkpoint(
        self,
        workflow_run: WorkflowRun,
        checkpoint: WorkflowCheckpoint,
        approval_tokens: dict[str, str],
    ) -> WorkflowResult:
        """Resume workflow execution from a checkpoint.

        Args:
            workflow_run: The workflow run to resume.
            checkpoint: The checkpoint to restore from.
            approval_tokens: Approval tokens for pending actions.

        Returns:
            WorkflowResult with execution details.
        """
        run_id = workflow_run.run_id
        workflow_id = workflow_run.workflow_id

        # Load workflow spec
        spec = self._workflows.get(workflow_id)
        if spec is None:
            spec = self.load_workflow(workflow_id)

        # Load agent profile
        agent_profile = self._agent_profiles.get(spec.agent_profile_id)
        if agent_profile is None:
            agent_profile = self.load_agent_profile(spec.agent_profile_id)

        # Get engine
        engine = self._engines.get(agent_profile.engine)
        if engine is None:
            raise WorkflowExecutionError(
                f"Engine not registered: {agent_profile.engine}",
                workflow_id=workflow_id,
            )

        intent = workflow_run.intent or spec.description or f"Run {spec.name}"

        # Restore state from checkpoint
        step_outputs = checkpoint.step_outputs

        context_packet: ContextPacket | None = step_outputs.get("context_packet")
        plan: Plan | None = step_outputs.get("plan")
        trace: DecisionTrace | None = step_outputs.get("trace")
        skill_usage_override: dict[str, Any] = step_outputs.get("skill_usage_override", {})

        # Restore calendar state if present
        calendar_state: CalendarDerivationState | None = None
        if checkpoint.state_json:
            try:
                import json
                state_data = json.loads(checkpoint.state_json)
                # Reconstruct CalendarDerivationState from serialized data
                calendar_state = self._restore_calendar_state(state_data)
            except Exception as e:
                logger.warning("failed_to_restore_calendar_state", error=str(e))

        # Resume from the next step
        resume_from = checkpoint.resume_from_index
        steps_to_run = spec.steps[resume_from:]

        logger.info(
            "checkpoint_state_restored",
            run_id=run_id,
            resume_from_step=resume_from,
            remaining_steps=len(steps_to_run),
            has_context=context_packet is not None,
            has_plan=plan is not None,
        )

        try:
            # Execute remaining steps
            for step_idx, step in enumerate(steps_to_run, start=resume_from):
                try:
                    # Execute step (same logic as in run())
                    if step == "vault_sync":
                        await self._step_vault_sync(spec)
                    elif step == "assemble_context":
                        context_packet = await self._step_assemble_context(
                            intent, agent_profile, None, workflow_id
                        )
                    elif step == "propose_plan":
                        if context_packet is None:
                            raise WorkflowExecutionError(
                                "Cannot propose plan without context",
                                workflow_id, step,
                            )
                        plan = await self._step_propose_plan(
                            context_packet, agent_profile, engine
                        )
                        plan, context_packet, skill_usage_override = await self._maybe_replan_with_skills(
                            plan, context_packet, agent_profile, engine
                        )
                    elif step == "validate":
                        if plan is None:
                            raise WorkflowExecutionError(
                                "Cannot validate without plan", workflow_id, step
                            )
                        await self._step_validate(plan, agent_profile, context_packet)
                    elif step == "execute":
                        if plan is None or context_packet is None:
                            raise WorkflowExecutionError(
                                "Cannot execute without plan and context",
                                workflow_id, step,
                            )
                        trace = await self._step_execute(
                            plan, context_packet, agent_profile,
                            agent_profile.engine, run_id, workflow_id,
                            approval_tokens, skill_usage_override,
                        )
                        # Check for approval needed
                        if trace.outcome.status == OutcomeStatus.NEEDS_APPROVAL:
                            self._update_workflow_run(
                                run_id,
                                status=WorkflowRunStatus.WAITING_APPROVAL,
                                last_step=step,
                                trace_id=trace.trace_id,
                            )
                            # Persist pending approvals from executor gate to store
                            self._persist_pending_approvals(
                                run_id, workflow_id, trace,
                            )
                            # Save checkpoint before returning
                            self._save_step_checkpoint(
                                run_id, step_idx, step,
                                context_packet, plan, trace, skill_usage_override,
                                calendar_state,
                            )
                            return WorkflowResult(
                                workflow_id=workflow_id,
                                run_id=run_id,
                                success=False,
                                trace=trace,
                                status=WorkflowRunStatus.WAITING_APPROVAL,
                                workflow_run=workflow_run,
                            )
                    elif step == "write_back":
                        if trace:
                            await self._step_write_back(trace, spec)
                    elif step == "emit_trace":
                        pass  # Trace already emitted

                    # Update last step
                    self._update_workflow_run(run_id, last_step=step)

                    # Save checkpoint after successful step
                    self._save_step_checkpoint(
                        run_id, step_idx, step,
                        context_packet, plan, trace, skill_usage_override,
                        calendar_state,
                    )

                except Exception as step_error:
                    if spec.on_error == OnError.HALT:
                        raise
                    elif spec.on_error == OnError.CONTINUE:
                        logger.warning("step_failed_continuing", step=step, error=str(step_error))
                    elif spec.on_error == OnError.RETRY:
                        # Retry logic would go here
                        raise

            # Workflow completed successfully
            self._update_workflow_run(run_id, status=WorkflowRunStatus.COMPLETED)

            # Clean up checkpoints
            self._workflow_store.delete_checkpoints(run_id)

            if self._event_log:
                self._event_log.emit(
                    EventType.WORKFLOW_COMPLETED,
                    source="workflow_runner",
                    entity_id=run_id,
                    entity_type="workflow_run",
                    data={"workflow_id": workflow_id},
                )

            return WorkflowResult(
                workflow_id=workflow_id,
                run_id=run_id,
                success=True,
                trace=trace,
                status=WorkflowRunStatus.COMPLETED,
                workflow_run=workflow_run,
            )

        except Exception as e:
            error_record = ErrorRecord(
                error_type=type(e).__name__,
                message=str(e),
                occurred_at=utc_now(),
            )
            self._update_workflow_run(
                run_id,
                status=WorkflowRunStatus.FAILED,
                error=error_record,
            )
            return WorkflowResult(
                workflow_id=workflow_id,
                run_id=run_id,
                success=False,
                error=str(e),
                status=WorkflowRunStatus.FAILED,
                workflow_run=workflow_run,
            )

    def _save_step_checkpoint(
        self,
        run_id: str,
        step_index: int,
        step_name: str,
        context_packet: ContextPacket | None,
        plan: Plan | None,
        trace: DecisionTrace | None,
        skill_usage_override: dict[str, Any],
        calendar_state: CalendarDerivationState | None,
    ) -> None:
        """Save a checkpoint after a step completes."""
        import json

        step_outputs = {
            "context_packet": context_packet,
            "plan": plan,
            "trace": trace,
            "skill_usage_override": skill_usage_override,
        }

        state_json = None
        if calendar_state:
            try:
                state_json = json.dumps(self._serialize_calendar_state(calendar_state))
            except Exception as e:
                logger.warning("failed_to_serialize_calendar_state", error=str(e))

        self._workflow_store.save_checkpoint(
            run_id=run_id,
            step_index=step_index,
            step_name=step_name,
            step_outputs=step_outputs,
            state_json=state_json,
        )

    def _serialize_calendar_state(self, state: CalendarDerivationState) -> dict:
        """Serialize CalendarDerivationState for checkpointing."""
        return {
            "sources": {k: self._serialize_source_config(v) for k, v in state.sources.items()},
            "events_by_source": {
                k: [self._serialize_event(e) for e in v]
                for k, v in state.events_by_source.items()
            },
            "action_context": {
                k: self._serialize_action_context(v)
                for k, v in state.action_context.items()
            },
            "task_projects_by_name": state.task_projects_by_name,
        }

    def _serialize_source_config(self, config: CalendarSourceConfig) -> dict:
        """Serialize CalendarSourceConfig."""
        return {
            "source_id": config.source_id,
            "adapter_id": config.adapter_id,
            "filters": {
                "exclude_all_day": config.filters.exclude_all_day,
                "exclude_title_prefixes": config.filters.exclude_title_prefixes,
                "exclude_title_keywords": config.filters.exclude_title_keywords,
                "require_attendees_or_conference": config.filters.require_attendees_or_conference,
                "require_zoom_link": config.filters.require_zoom_link,
            },
            "task_derivations": [
                {
                    "adapter_id": d.adapter_id,
                    "project_id": d.project_id,
                    "project_name": d.project_name,
                    "labels": d.labels,
                    "task_kind": d.task_kind,
                    "default_priority": d.default_priority,
                    "caps": {"max_create_per_run": d.caps.max_create_per_run, "max_update_per_run": d.caps.max_update_per_run},
                }
                for d in config.task_derivations
            ],
            "meeting_note_derivations": [
                {
                    "vault_path": d.vault_path,
                    "meeting_folder": d.meeting_folder,
                    "template": d.template,
                    "caps": {"max_create_per_run": d.caps.max_create_per_run, "max_update_per_run": d.caps.max_update_per_run},
                    "suppression_ttl_hours": d.suppression_ttl_hours,
                    "project_folder_map": d.project_folder_map,
                }
                for d in config.meeting_note_derivations
            ],
        }

    def _serialize_event(self, event: CalendarEventRecord) -> dict:
        """Serialize CalendarEventRecord."""
        return {
            "source_id": event.source_id,
            "provider": event.provider,
            "calendar_id": event.calendar_id,
            "event_id": event.event_id,
            "title": event.title,
            "description": event.description,
            "start": event.start.isoformat() if event.start else None,
            "end": event.end.isoformat() if event.end else None,
            "all_day": event.all_day,
            "status": event.status,
            "updated_at": event.updated_at.isoformat() if event.updated_at else None,
            "etag": event.etag,
            "location": event.location,
            "attendees": event.attendees,
            "conference_link": event.conference_link,
            "zoom_link": event.zoom_link,
            "raw": event.raw,
        }

    def _serialize_action_context(self, ctx: DerivedActionContext) -> dict:
        """Serialize DerivedActionContext."""
        return {
            "action_id": ctx.action_id,
            "source_event_id": ctx.source_event_id,
            "derivation_type": ctx.derivation_type,
            "idempotency_key": ctx.idempotency_key,
        }

    def _restore_calendar_state(self, data: dict) -> CalendarDerivationState:
        """Restore CalendarDerivationState from serialized data."""
        state = CalendarDerivationState()

        # Restore sources
        for source_id, source_data in data.get("sources", {}).items():
            filters = CalendarSourceFilters(
                exclude_all_day=source_data["filters"]["exclude_all_day"],
                exclude_title_prefixes=source_data["filters"]["exclude_title_prefixes"],
                exclude_title_keywords=source_data["filters"]["exclude_title_keywords"],
                require_attendees_or_conference=source_data["filters"]["require_attendees_or_conference"],
                require_zoom_link=source_data["filters"].get("require_zoom_link", False),
            )
            task_derivations = [
                TaskDerivationConfig(
                    adapter_id=d["adapter_id"],
                    project_id=d["project_id"],
                    project_name=d["project_name"],
                    labels=d["labels"],
                    task_kind=d["task_kind"],
                    default_priority=d["default_priority"],
                    caps=DerivationCaps(
                        max_create_per_run=d["caps"]["max_create_per_run"],
                        max_update_per_run=d["caps"]["max_update_per_run"],
                    ),
                )
                for d in source_data.get("task_derivations", [])
            ]
            meeting_note_derivations = [
                MeetingNoteDerivationConfig(
                    vault_path=d["vault_path"],
                    meeting_folder=d["meeting_folder"],
                    template=d["template"],
                    caps=DerivationCaps(
                        max_create_per_run=d["caps"]["max_create_per_run"],
                        max_update_per_run=d["caps"]["max_update_per_run"],
                    ),
                    suppression_ttl_hours=d["suppression_ttl_hours"],
                    project_folder_map=d.get("project_folder_map", {}),
                )
                for d in source_data.get("meeting_note_derivations", [])
            ]
            state.sources[source_id] = CalendarSourceConfig(
                source_id=source_data["source_id"],
                adapter_id=source_data["adapter_id"],
                filters=filters,
                task_derivations=task_derivations,
                meeting_note_derivations=meeting_note_derivations,
            )

        # Restore events
        for source_id, events_data in data.get("events_by_source", {}).items():
            state.events_by_source[source_id] = [
                CalendarEventRecord(
                    source_id=e.get("source_id", source_id),
                    provider=e.get("provider", ""),
                    calendar_id=e.get("calendar_id", ""),
                    event_id=e["event_id"],
                    title=e.get("title", ""),
                    description=e.get("description"),
                    start=datetime.fromisoformat(e["start"]) if e.get("start") else None,
                    end=datetime.fromisoformat(e["end"]) if e.get("end") else None,
                    all_day=bool(e.get("all_day", False)),
                    status=e.get("status", "confirmed"),
                    updated_at=(
                        datetime.fromisoformat(e["updated_at"])
                        if e.get("updated_at")
                        else None
                    ),
                    etag=e.get("etag"),
                    location=e.get("location"),
                    attendees=e.get("attendees", []),
                    conference_link=e.get("conference_link"),
                    zoom_link=e.get("zoom_link"),
                    raw=e.get("raw", {}),
                )
                for e in events_data
            ]

        # Restore action context
        for action_id, ctx_data in data.get("action_context", {}).items():
            state.action_context[action_id] = DerivedActionContext(
                action_id=ctx_data["action_id"],
                source_event_id=ctx_data["source_event_id"],
                derivation_type=ctx_data["derivation_type"],
                idempotency_key=ctx_data["idempotency_key"],
            )

        # Restore project cache
        state.task_projects_by_name = data.get("task_projects_by_name", {})

        return state

    async def _step_write_back(
        self,
        trace: DecisionTrace,
        spec: WorkflowSpec,
    ) -> None:
        """Write back results step."""
        logger.debug("step_write_back", trace_id=trace.trace_id)
        
        write_back_config = spec.write_back
        
        # Create summary note if configured
        if write_back_config.create_summary_note:
            await self._create_summary_note(trace, spec)
        
        # Update graph if configured
        if write_back_config.update_graph:
            await self._update_graph_from_trace(trace, spec)
        
        # Send notifications if configured
        if write_back_config.notify:
            await self._send_notifications(trace, spec)
    
    async def _send_notifications(
        self,
        trace: DecisionTrace,
        spec: WorkflowSpec,
    ) -> None:
        """Emit notification events for each configured target.

        Notifications are emitted as ``WORKFLOW_NOTIFICATION`` events in the
        event log. External delivery (Slack, email, etc.) can be handled by
        consumers of the event log.

        Args:
            trace: The completed decision trace.
            spec: Workflow spec containing notification targets.
        """
        if not self._event_log or not spec.write_back:
            return

        for target in spec.write_back.notify:
            try:
                self._event_log.emit(
                    EventType.WORKFLOW_NOTIFICATION,
                    source="workflow_runner",
                    entity_id=trace.trace_id,
                    entity_type="trace",
                    payload={
                        "target": target,
                        "workflow_id": spec.workflow_id,
                        "trace_id": trace.trace_id,
                        "outcome": trace.outcome.status.value,
                        "summary": trace.plan.summary if trace.plan else None,
                    },
                )
                logger.debug(
                    "notification_emitted",
                    target=target,
                    trace_id=trace.trace_id,
                )
            except Exception:
                logger.exception(
                    "notification_emit_failed",
                    target=target,
                    trace_id=trace.trace_id,
                )

    async def _create_summary_note(
        self,
        trace: DecisionTrace,
        spec: WorkflowSpec,
    ) -> None:
        """Create a summary note documenting the workflow execution."""
        from datetime import datetime, timezone
        import json

        def format_value(value: Any, max_length: int = 500) -> str:
            """Format a value for display, handling nested structures."""
            if value is None:
                return "(none)"
            if isinstance(value, str):
                if len(value) > max_length:
                    return value[:max_length] + "..."
                return value
            if isinstance(value, (list, dict)):
                try:
                    formatted = json.dumps(value, indent=2, default=str)
                    if len(formatted) > max_length:
                        # Try to show first few items
                        if isinstance(value, list) and len(value) > 0:
                            preview = json.dumps(value[:3], indent=2, default=str)
                            return f"{preview}\n... ({len(value)} total items)"
                        if isinstance(value, dict):
                            # Show first few keys
                            preview_keys = list(value.keys())[:5]
                            preview = {k: value[k] for k in preview_keys}
                            formatted = json.dumps(preview, indent=2, default=str)
                            if len(value) > 5:
                                return f"{formatted}\n... ({len(value)} total keys)"
                            return formatted
                        return formatted[:max_length] + "..."
                    return formatted
                except Exception:
                    return str(value)[:max_length]
            return str(value)[:max_length]

        def extract_key_outputs(output: dict[str, Any]) -> dict[str, Any]:
            """Extract the most important fields from output for display."""
            if not output:
                return {}

            # Common important keys to surface
            important_keys = [
                "count", "total", "created", "updated", "deleted", "skipped",
                "tasks", "projects", "events", "labels", "notes", "items",
                "success", "error", "message", "status", "changes",
                "synced_count", "failed_count", "result",
            ]

            result = {}
            for key in important_keys:
                if key in output:
                    val = output[key]
                    # For lists, show count and first few items
                    if isinstance(val, list):
                        result[key] = f"({len(val)} items)"
                        if len(val) > 0 and len(val) <= 5:
                            result[f"{key}_preview"] = val
                    else:
                        result[key] = val

            # If no important keys found, include all keys with truncation
            if not result:
                for key, val in list(output.items())[:10]:
                    if isinstance(val, list):
                        result[key] = f"({len(val)} items)"
                    elif isinstance(val, dict):
                        result[key] = f"({len(val)} keys)"
                    else:
                        result[key] = val

            return result

        # Build summary content
        summary_lines = [
            f"# Workflow Execution Summary: {spec.name}",
            "",
            f"**Workflow ID:** `{spec.workflow_id}`",
            f"**Trace ID:** `{trace.trace_id}`",
            f"**Agent:** `{trace.agent_profile_id}`",
            f"**Status:** {trace.outcome.status.value if hasattr(trace.outcome.status, 'value') else trace.outcome.status}",
            f"**Executed:** {datetime.now(timezone.utc).isoformat()}",
            "",
            "## Intent",
            "",
            trace.intent or "(no intent specified)",
            "",
        ]

        # Plan summary
        if trace.plan:
            summary_lines.extend([
                "## Plan Summary",
                "",
                trace.plan.summary or "(no plan summary)",
                "",
            ])

        # Actions executed with detailed input/output
        summary_lines.extend([
            "## Actions Executed",
            "",
        ])

        if trace.tool_calls:
            for i, tool_call in enumerate(trace.tool_calls, 1):
                status_str = tool_call.status.value if hasattr(tool_call.status, 'value') else str(tool_call.status)
                status_emoji = "✅" if status_str == "success" else "❌" if status_str in ("error", "failed") else "⚠️"

                summary_lines.append(f"### {i}. `{tool_call.capability_name}` {status_emoji}")
                summary_lines.append("")
                summary_lines.append(f"**Status:** {status_str}")
                if tool_call.duration_ms:
                    summary_lines.append(f"**Duration:** {tool_call.duration_ms}ms")
                summary_lines.append("")

                # Input parameters
                if tool_call.input:
                    summary_lines.append("**Input Parameters:**")
                    summary_lines.append("```yaml")
                    for key, val in tool_call.input.items():
                        formatted_val = format_value(val, max_length=200)
                        # Handle multi-line values
                        if "\n" in formatted_val:
                            summary_lines.append(f"{key}:")
                            for line in formatted_val.split("\n"):
                                summary_lines.append(f"  {line}")
                        else:
                            summary_lines.append(f"{key}: {formatted_val}")
                    summary_lines.append("```")
                    summary_lines.append("")

                # Output with key extraction
                if tool_call.output:
                    key_outputs = extract_key_outputs(tool_call.output)

                    summary_lines.append("**Output Summary:**")
                    summary_lines.append("```yaml")
                    for key, val in key_outputs.items():
                        formatted_val = format_value(val, max_length=300)
                        if "\n" in str(formatted_val):
                            summary_lines.append(f"{key}:")
                            for line in str(formatted_val).split("\n"):
                                summary_lines.append(f"  {line}")
                        else:
                            summary_lines.append(f"{key}: {formatted_val}")
                    summary_lines.append("```")
                    summary_lines.append("")

                    # For update operations, show changes
                    if "changes" in tool_call.output or "before" in tool_call.output or "updated" in tool_call.output:
                        changes = tool_call.output.get("changes", tool_call.output.get("updated", []))
                        if changes and isinstance(changes, list):
                            summary_lines.append("**Changes Made:**")
                            for change in changes[:10]:  # Limit to first 10
                                if isinstance(change, dict):
                                    item_id = change.get("id", change.get("task_id", change.get("item_id", "unknown")))
                                    before = change.get("before", {})
                                    after = change.get("after", {})
                                    summary_lines.append(f"- **{item_id}:**")
                                    if before and after:
                                        for key in set(list(before.keys()) + list(after.keys())):
                                            if before.get(key) != after.get(key):
                                                summary_lines.append(f"  - `{key}`: {before.get(key)} → {after.get(key)}")
                                    else:
                                        summary_lines.append(f"  - Changed: {format_value(change, 200)}")
                                else:
                                    summary_lines.append(f"- {format_value(change, 200)}")
                            if len(changes) > 10:
                                summary_lines.append(f"- ... and {len(changes) - 10} more changes")
                            summary_lines.append("")

                # Error details
                if tool_call.error:
                    summary_lines.append("**Error:**")
                    summary_lines.append(f"```")
                    if hasattr(tool_call.error, 'message'):
                        summary_lines.append(tool_call.error.message)
                    else:
                        summary_lines.append(str(tool_call.error))
                    summary_lines.append("```")
                    summary_lines.append("")
        else:
            summary_lines.append("(no actions executed)")
            summary_lines.append("")

        # Outcome
        summary_lines.extend([
            "## Outcome",
            "",
            f"**Status:** {trace.outcome.status.value if hasattr(trace.outcome.status, 'value') else trace.outcome.status}",
        ])

        if trace.outcome.artifacts:
            summary_lines.extend([
                "",
                "**Artifacts Created:**",
                "",
            ])
            for artifact in trace.outcome.artifacts:
                summary_lines.append(f"- {artifact.ref_id if hasattr(artifact, 'ref_id') else artifact}")

        # Note: Outcome doesn't have an error field - errors are in tool_calls
        if trace.outcome.summary and "error" in trace.outcome.summary.lower():
            summary_lines.extend([
                "",
                f"**Note:** {trace.outcome.summary}",
            ])

        if trace.outcome.summary:
            summary_lines.extend([
                "",
                "## Summary",
                "",
                trace.outcome.summary,
            ])

        summary_content = "\n".join(summary_lines)
        
        # Try to create Obsidian note first, fall back to document store
        try:
            # Check if obsidian.create capability is available
            capability = self._executor._broker.registry.get("obsidian.create@v1")
            if capability:
                settings = get_settings()
                if hasattr(settings, 'obsidian_vault_path') and settings.obsidian_vault_path:
                    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    
                    # Use note_template from metadata if provided, otherwise default path
                    note_path = None
                    if spec.metadata and "note_template" in spec.metadata:
                        template = spec.metadata["note_template"]
                        # Replace template variables
                        note_path = template.replace("{{date}}", date_str)
                        note_path = note_path.replace("{{workflow_id}}", spec.workflow_id)
                        note_path = note_path.replace("{{trace_id}}", trace.trace_id[:8])
                    else:
                        # Default: Workflows/Summaries/YYYY-MM-DD-workflow_id-summary.md
                        note_path = f"Workflows/Summaries/{date_str}-{spec.workflow_id}-summary.md"
                    
                    # Create a minimal agent profile for write-back operations
                    from agent_kernel.core.schemas.agent import AgentProfile, ApprovalPolicy, ContextPolicy, ModelConfig
                    write_back_profile = AgentProfile(
                        agent_profile_id="system_write_back",
                        name="System Write-Back",
                        engine="custom",
                        llm_config=ModelConfig(),
                        allowed_capabilities=["obsidian.create@v1"],
                        context_policy=ContextPolicy(),
                        approval_policy=ApprovalPolicy(require_approval_for=[]),
                    )
                    
                    # Execute via broker (internal write-back, auto-approved)
                    result = await self._executor._broker.execute(
                        capability_name="obsidian.create@v1",
                        args={
                            "path": note_path,
                            "content": summary_content,
                            "title": f"{spec.name} - {date_str}",
                            "tags": ["workflow", "summary", spec.workflow_id],
                            "frontmatter": {
                                "workflow_id": spec.workflow_id,
                                "trace_id": trace.trace_id,
                                "agent_profile_id": trace.agent_profile_id,
                                "status": str(trace.outcome.status),
                                "created": datetime.now(timezone.utc).isoformat(),
                            },
                        },
                        agent_profile=write_back_profile,
                        approval_token="auto",  # Auto-approve for internal write-back
                    )
                    
                    logger.info(
                        "summary_note_created_obsidian",
                        path=note_path,
                        trace_id=trace.trace_id,
                    )
                    return
        except Exception as e:
            logger.warning(
                "obsidian_note_creation_failed",
                trace_id=trace.trace_id,
                error=str(e),
                falling_back_to="document_store",
            )
        
        # Fall back to document store
        if self._assembler.document_store:
            try:
                note_id = f"workflow_summary_{trace.trace_id}"
                self._assembler.document_store.upsert_document(
                    item_id=note_id,
                    item_type="workflow_summary",
                    content=summary_content,
                    metadata={
                        "workflow_id": spec.workflow_id,
                        "trace_id": trace.trace_id,
                        "agent_profile_id": trace.agent_profile_id,
                        "status": str(trace.outcome.status),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "tags": ["workflow", "summary", spec.workflow_id],
                    },
                )
                logger.info(
                    "summary_note_created_document_store",
                    note_id=note_id,
                    trace_id=trace.trace_id,
                )
            except Exception as e:
                logger.error(
                    "summary_note_failed",
                    trace_id=trace.trace_id,
                    error=str(e),
                )
        else:
            logger.warning(
                "summary_note_skipped",
                trace_id=trace.trace_id,
                reason="no_document_store_or_obsidian",
            )
    
    async def _update_graph_from_trace(
        self,
        trace: DecisionTrace,
        spec: WorkflowSpec,
    ) -> None:
        """Update graph store with workflow execution results.

        If ContextGraphHooks are configured, delegates to the hooks
        for full trace decomposition (event clock). Otherwise falls
        back to simple artifact edge creation.
        """
        # Use context graph hooks for full decomposition when available
        if self._context_graph_hooks:
            try:
                await self._context_graph_hooks.on_trace_completed(
                    trace, success=True,
                )
                return
            except Exception as e:
                logger.error(
                    "context_graph_hooks_failed",
                    trace_id=trace.trace_id,
                    error=str(e),
                )
                # Fall through to simple graph update

        # Fallback: simple artifact edge creation
        if not self._assembler._graph_store:
            logger.debug("graph_update_skipped", reason="no_graph_store")
            return

        try:
            workflow_node_id = f"workflow_run:{trace.trace_id}"

            if trace.outcome.artifacts:
                for artifact in trace.outcome.artifacts:
                    artifact_id = artifact.ref_id if hasattr(artifact, 'ref_id') else str(artifact)
                    self._assembler._graph_store.upsert_edge(
                        source_id=workflow_node_id,
                        target_id=artifact_id,
                        edge_type="created_by",
                        properties={
                            "workflow_id": spec.workflow_id,
                            "trace_id": trace.trace_id,
                        },
                    )

            logger.debug(
                "graph_updated",
                trace_id=trace.trace_id,
                artifacts_count=len(trace.outcome.artifacts) if trace.outcome.artifacts else 0,
            )
        except Exception as e:
            logger.error(
                "graph_update_failed",
                trace_id=trace.trace_id,
                error=str(e),
            )

    def list_workflows(self) -> list[WorkflowSpec]:
        """List loaded workflows."""
        return list(self._workflows.values())

    def get_workflow(self, workflow_id: str) -> WorkflowSpec | None:
        """Get a workflow by ID."""
        return self._workflows.get(workflow_id)

    async def run_with_thinking(
        self,
        workflow_id: str,
        intent: str | None = None,
        project_id: str | None = None,
        approval_tokens: dict[str, str] | None = None,
    ) -> WorkflowResult:
        """Run a workflow with thinking policy and automatic escalation.

        v1.0.3: Uses ThinkingPolicyController for adaptive reasoning.
        Automatically escalates tiers when quality gates fail.

        Args:
            workflow_id: The workflow to run.
            intent: Override the default intent.
            project_id: Optional project scope.
            approval_tokens: Pre-approved action tokens.

        Returns:
            WorkflowResult with execution details and reasoning metadata.
        """
        run_id = generate_ulid()

        # Load workflow spec
        spec = self._workflows.get(workflow_id)
        if spec is None:
            try:
                spec = self.load_workflow(workflow_id)
            except WorkflowNotFoundError:
                return WorkflowResult(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    success=False,
                    error=f"Workflow not found: {workflow_id}",
                    status=WorkflowRunStatus.FAILED,
                )

        # Load agent profile
        agent_profile = self._agent_profiles.get(spec.agent_profile_id)
        if agent_profile is None:
            try:
                agent_profile = self.load_agent_profile(spec.agent_profile_id)
            except WorkflowExecutionError as e:
                return WorkflowResult(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    success=False,
                    error=str(e),
                    status=WorkflowRunStatus.FAILED,
                )

        # Get engine
        engine = self._engines.get(agent_profile.engine)
        if engine is None:
            raise WorkflowExecutionError(
                f"Engine not registered: {agent_profile.engine}",
                workflow_id=workflow_id,
            )

        # Use workflow description as default intent
        intent = intent or spec.description or f"Run {spec.name}"

        # Empty-poll guard: skip workflow if pre-check finds no work
        if spec.empty_check is not None:
            skip = await self._run_empty_check(spec.empty_check, agent_profile)
            if skip:
                logger.info(
                    "workflow_skipped_empty_poll",
                    workflow_id=workflow_id,
                    check_capability=spec.empty_check.capability,
                )
                return WorkflowResult(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    success=True,
                    status=WorkflowRunStatus.COMPLETED,
                )

        # Refresh adaptive stats cache if controller supports it
        from agent_kernel.engine.adaptive_thinking import (
            AdaptiveThinkingPolicyController,
        )
        if isinstance(self._thinking_controller, AdaptiveThinkingPolicyController):
            self._thinking_controller._refresh_cache_if_needed()
            thinking_session = self._thinking_controller.create_session(
                agent_profile, workflow_id=workflow_id
            )
        else:
            thinking_session = self._thinking_controller.create_session(agent_profile)

        # Create workflow run record
        workflow_run = self._create_workflow_run(workflow_id, run_id, intent)

        if self._event_log:
            self._event_log.emit(
                EventType.WORKFLOW_STARTED,
                source="workflow_runner",
                entity_id=run_id,
                entity_type="workflow_run",
                data={
                    "workflow_id": workflow_id,
                    "intent": intent,
                    "thinking_mode": agent_profile.thinking_config.mode
                    if agent_profile.thinking_config
                    else "standard",
                },
            )

        logger.info(
            "workflow_started_with_thinking",
            run_id=run_id,
            workflow_id=workflow_id,
            intent=intent,
            starting_tier=thinking_session.current_tier,
        )

        # Main escalation loop
        best_plan: Plan | None = None
        best_context: ContextPacket | None = None
        trace: DecisionTrace | None = None
        attempt_count = 0
        skill_usage_override: dict[str, Any] = {}

        # Deterministic bypass: skip escalation loop entirely
        if spec.skip_llm_planning and spec.deterministic_capability:
            det_args = self._build_deterministic_args(spec)
            plan = self._build_deterministic_plan(
                intent=intent,
                capability=spec.deterministic_capability,
                args=det_args,
            )
            context_packet = self._empty_context_packet(intent, agent_profile)
            best_plan = plan
            best_context = context_packet

            logger.info(
                "deterministic_plan_bypass",
                workflow_id=workflow_id,
                capability=spec.deterministic_capability,
            )

            # Execute directly — no escalation needed
            trace = await self._step_execute(
                plan,
                context_packet,
                agent_profile,
                engine.engine_id,
                run_id,
                workflow_id,
                approval_tokens,
                skill_usage_override,
            )

            # Add minimal reasoning metadata
            trace.reasoning = ReasoningMetadata(
                tier_used=0,
                model_id="deterministic",
                reasoning_effort="none",
                escalation_count=0,
                escalation_reasons=[],
                gate_failures=[],
                critic_used=False,
                total_reasoning_tokens=0,
            )

            # Write back and complete
            if trace:
                await self._step_write_back(trace, spec)

            self._update_workflow_run(
                run_id,
                status=WorkflowRunStatus.COMPLETED,
                trace_id=trace.trace_id if trace else None,
            )
            self._workflow_store.delete_checkpoints(run_id)

            return WorkflowResult(
                workflow_id=workflow_id,
                run_id=run_id,
                success=True,
                trace=trace,
                status=WorkflowRunStatus.COMPLETED,
                workflow_run=workflow_run,
            )

        while True:
            attempt_count += 1
            policy = self._thinking_controller.get_policy(thinking_session)

            logger.info(
                "thinking_attempt",
                attempt=attempt_count,
                tier=policy.tier,
                tier_name=policy.tier_name,
            )

            try:
                # Step 1: Assemble context with tier-appropriate settings
                context_packet = await self._step_assemble_context(
                    intent, agent_profile, project_id, workflow_id, thinking_session
                )
                best_context = context_packet

                # Step 2: Propose plan
                plan = await self._step_propose_plan(
                    context_packet, agent_profile, engine, thinking_session
                )
                plan, context_packet, skill_usage_override = await self._maybe_replan_with_skills(
                    plan,
                    context_packet,
                    agent_profile,
                    engine,
                    thinking_policy=policy,
                )
                best_plan = plan
                best_context = context_packet

                # Step 3: Validate
                is_valid, errors = await self._step_validate(
                    plan, agent_profile, context_packet, thinking_session
                )

                if not is_valid:
                    # Try to escalate
                    should_escalate, trigger, reason = (
                        self._thinking_controller.evaluate_for_escalation(
                            thinking_session,
                            plan=plan,
                        )
                    )

                    if should_escalate and trigger:
                        escalated = await self._thinking_controller.escalate(
                            thinking_session, trigger, reason
                        )
                        if escalated:
                            continue  # Try again with higher tier

                # Step 4: Run critic if configured
                if policy.run_critic and self._critic_engine:
                    critique = await self._critic_engine.critique(
                        plan, context_packet, agent_profile
                    )
                    thinking_session.critic_issues.extend(critique.issues)

                    if critique.should_revise:
                        should_escalate, trigger, reason = (
                            self._thinking_controller.evaluate_for_escalation(
                                thinking_session,
                                critique=critique,
                            )
                        )

                        if should_escalate and trigger:
                            escalated = await self._thinking_controller.escalate(
                                thinking_session, trigger, reason
                            )
                            if escalated:
                                continue  # Try again with higher tier

                # Step 5: Execute (success path)
                trace = await self._step_execute(
                    plan,
                    context_packet,
                    agent_profile,
                    engine.engine_id,
                    run_id,
                    workflow_id,
                    approval_tokens,
                    skill_usage_override,
                )

                # Add reasoning metadata to trace
                reasoning_metadata = thinking_session.to_reasoning_metadata()
                trace.reasoning = reasoning_metadata

                # Check for cost anomaly
                if self._cost_detector is not None:
                    anomaly = self._cost_detector.check(trace)
                    if anomaly:
                        logger.warning(
                            "cost_anomaly_detected",
                            trace_id=trace.trace_id,
                            current_cost=anomaly.current_cost,
                            rolling_mean=anomaly.rolling_mean,
                            deviation_factor=anomaly.deviation_factor,
                        )

                # Extract experience case from trace
                if self._experience_miner is not None and trace:
                    try:
                        self._experience_miner.extract_case(trace)
                    except Exception:
                        logger.warning(
                            "experience_case_extraction_failed",
                            trace_id=trace.trace_id,
                            exc_info=True,
                        )

                break  # Success

            except Exception as e:
                logger.warning(
                    "thinking_attempt_failed",
                    attempt=attempt_count,
                    error=str(e),
                )

                # Try to escalate on error
                should_escalate = thinking_session.can_escalate()
                if should_escalate:
                    escalated = await self._thinking_controller.escalate(
                        thinking_session,
                        "quality_gates_failed",
                        f"Attempt failed: {e}",
                    )
                    if escalated:
                        continue  # Try again with higher tier

                # Can't escalate, fail the workflow
                self._update_workflow_run(
                    run_id,
                    status=WorkflowRunStatus.FAILED,
                    error=ErrorRecord(
                        code="THINKING_EXHAUSTED",
                        message=str(e),
                        details={
                            "attempts": attempt_count,
                            "final_tier": thinking_session.current_tier,
                        },
                    ),
                )

                return WorkflowResult(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    success=False,
                    error=str(e),
                    status=WorkflowRunStatus.FAILED,
                    workflow_run=workflow_run,
                )

        # Success - write back and complete
        if trace:
            await self._step_write_back(trace, spec)

        self._update_workflow_run(
            run_id,
            status=WorkflowRunStatus.COMPLETED,
            trace_id=trace.trace_id if trace else None,
        )

        # Invalidate adaptive cache so next run sees fresh trace data
        from agent_kernel.engine.adaptive_thinking import (
            AdaptiveThinkingPolicyController,
        )
        if isinstance(self._thinking_controller, AdaptiveThinkingPolicyController):
            self._thinking_controller.invalidate_cache()

        if self._event_log:
            self._event_log.emit(
                EventType.WORKFLOW_COMPLETED,
                source="workflow_runner",
                entity_id=run_id,
                entity_type="workflow_run",
                data={
                    "workflow_id": workflow_id,
                    "trace_id": trace.trace_id if trace else None,
                    "thinking_attempts": attempt_count,
                    "final_tier": thinking_session.current_tier,
                    "escalations": thinking_session.escalation_count,
                },
            )

        logger.info(
            "workflow_completed_with_thinking",
            run_id=run_id,
            workflow_id=workflow_id,
            trace_id=trace.trace_id if trace else None,
            attempts=attempt_count,
            final_tier=thinking_session.current_tier,
            escalations=thinking_session.escalation_count,
        )

        return WorkflowResult(
            workflow_id=workflow_id,
            run_id=run_id,
            success=True,
            trace=trace,
            status=WorkflowRunStatus.COMPLETED,
            workflow_run=workflow_run,
        )
