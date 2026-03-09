# Executor & Workflow Runner

**Version:** 1.0.1  
**Status:** Implementation Phase

The Executor validates and runs Plans. The Workflow Runner orchestrates the complete flow from trigger to trace.

---

## Trust Boundary Enforcement (v1.0.1)

The executor enforces a strict trust boundary between agent-provided values and system-computed policy:

```
┌─────────────────────────────────────────────────────────────────┐
│                     TRUST BOUNDARY                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Agent-Provided (Non-Authoritative)                              │
│  ├── ActionRequest.side_effect        (hint only)               │
│  └── ActionRequest.requires_approval  (hint only)               │
│                                                                  │
│  System-Computed (Authoritative)                                 │
│  ├── CapabilityDef.side_effect_level                            │
│  ├── CapabilityDef.requires_approval_default                    │
│  └── AgentProfile.approval_policy                               │
│                                                                  │
│  Executor computes effective_* before gating/execution          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Effective Policy Algorithm

```python
def _compute_effective_policy(
    action: ActionRequest,
    capability: CapabilityDef,
    agent_profile: AgentProfile,
) -> tuple[SideEffect, bool]:
    """Compute authoritative policy values."""
    
    # Side effect from capability definition (authoritative)
    effective_side_effect = capability.side_effect_level
    
    # Approval from capability default + agent profile policy
    effective_requires_approval = capability.requires_approval_default
    
    if effective_side_effect not in agent_profile.approval_policy.auto_approve_side_effects:
        effective_requires_approval = True
    
    return effective_side_effect, effective_requires_approval
```

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     EXECUTION LAYER                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                    WORKFLOW RUNNER                          ││
│  │                                                             ││
│  │  1. Trigger ──▶ 2. Assemble ──▶ 3. Propose ──▶ 4. Validate ││
│  │                                                             ││
│  │  5. Gate ──▶ 6. Execute ──▶ 7. Write-back ──▶ 8. Trace     ││
│  │                                                             ││
│  └─────────────────────────────────────────────────────────────┘│
│                          │                                       │
│                          ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                 DETERMINISTIC EXECUTOR                      ││
│  │                                                             ││
│  │  • Validates Plan schema                                    ││
│  │  • Checks action allowlists                                 ││
│  │  • Gates approvals                                          ││
│  │  • Executes via Tool Broker                                 ││
│  │  • Collects artifacts                                       ││
│  │  • Writes DecisionTrace                                     ││
│  │                                                             ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Deterministic Executor

### Purpose

The executor is **deterministic** (no LLM calls). It:
1. Validates the Plan against schema
2. Enforces side-effect constraints
3. Gates approvals
4. Executes actions via Tool Broker
5. Collects artifacts
6. Writes the DecisionTrace

### Interface

```python
class DeterministicExecutor:
    """
    Validates and executes Plans deterministically.
    
    This is the enforcement layer - prevents "agent gone rogue".
    """
    
    def __init__(
        self,
        tool_broker: ToolBroker,
        trace_store: TraceStore,
        event_log: EventLog,
    ):
        self.broker = tool_broker
        self.traces = trace_store
        self.events = event_log
    
    async def execute(
        self,
        plan: Plan,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
        engine_metadata: EngineMetadata,
        approval_tokens: dict[str, str] | None = None,
    ) -> DecisionTrace:
        """
        Execute a plan and return the complete trace.
        
        Args:
            plan: The plan to execute
            context_packet: The context used to generate the plan
            agent_profile: Agent configuration
            engine_metadata: Info about which engine produced the plan
            approval_tokens: Pre-approved action tokens
        
        Returns:
            Complete DecisionTrace with all records
        """
        
        run_id = generate_ulid()
        approval_tokens = approval_tokens or {}
        
        # 1. Validate plan schema
        validation_errors = self._validate_plan(plan, agent_profile)
        if validation_errors:
            return self._create_failed_trace(
                run_id, plan, context_packet, agent_profile, engine_metadata,
                error="VALIDATION_FAILED",
                details=validation_errors,
            )
        
        # 2. Check for actions requiring approval
        pending_approvals = self._check_approvals(
            plan.actions,
            agent_profile,
            approval_tokens,
        )
        if pending_approvals:
            return self._create_pending_trace(
                run_id, plan, context_packet, agent_profile, engine_metadata,
                pending_approvals=pending_approvals,
            )
        
        # 3. Execute actions
        tool_calls: list[ToolCallRecord] = []
        approvals: list[ApprovalRecord] = []
        artifacts: list[ContextRef] = []
        
        for action in plan.actions:
            # Get approval token if available
            token = approval_tokens.get(action.action_id)
            
            # Execute via broker
            record = await self.broker.execute(
                action=action,
                agent_profile=agent_profile,
                approval_token=token,
            )
            tool_calls.append(record)
            
            # Track approvals
            if token:
                approvals.append(ApprovalRecord(
                    action_id=action.action_id,
                    approved=True,
                    approved_at=datetime.utcnow(),
                ))
            
            # Collect artifacts from successful calls
            if record.status == CallStatus.SUCCESS:
                artifacts.extend(
                    self._extract_artifacts(action, record.output)
                )
            
            # Stop on error if configured
            if record.status in (CallStatus.ERROR, CallStatus.DENIED):
                break
        
        # 4. Determine outcome
        outcome = self._determine_outcome(tool_calls, artifacts)
        
        # 5. Build provenance
        provenance = Provenance(
            config_hash=self._hash_config(agent_profile),
            engine_version=engine_metadata.version,
            kernel_version="1.0.0",
            prompt_hash=engine_metadata.prompt_hash,
        )
        
        # 6. Create trace
        trace = DecisionTrace(
            trace_id=generate_ulid(),
            run_id=run_id,
            agent_profile_id=agent_profile.agent_profile_id,
            engine_id=engine_metadata.engine_id,
            intent=plan.intent,
            timestamp=datetime.utcnow(),
            context_packet_id=context_packet.packet_id,
            plan=plan,
            tool_calls=tool_calls,
            approvals=approvals,
            outcome=outcome,
            provenance=provenance,
        )
        
        # 7. Persist trace
        await self.traces.write(trace)
        
        # 8. Emit event
        await self.events.append(Event(
            event_id=generate_ulid(),
            event_type=EventType.TRACE_CREATED,
            timestamp=datetime.utcnow(),
            entity_id=trace.trace_id,
            entity_type="trace",
            payload={
                "outcome": outcome.status.value,
                "actions_count": len(plan.actions),
                "success_count": sum(
                    1 for tc in tool_calls if tc.status == CallStatus.SUCCESS
                ),
            },
            trace_id=trace.trace_id,
        ))
        
        return trace
    
    def _validate_plan(
        self,
        plan: Plan,
        profile: AgentProfile,
    ) -> list[str]:
        """Validate plan against schema and policies."""
        
        errors = []
        
        # Check citations are present
        if profile.context_policy.must_cite and not plan.context_refs_used:
            errors.append("Plan must cite context sources")
        
        # Check actions use allowed capabilities
        allowed = set(profile.allowed_capabilities)
        for action in plan.actions:
            if action.capability_name not in allowed:
                errors.append(
                    f"Capability {action.capability_name} not allowed"
                )
        
        # Check idempotency keys for writes
        for action in plan.actions:
            if action.side_effect != SideEffect.NONE:
                if not action.idempotency_key:
                    errors.append(
                        f"Action {action.action_id} requires idempotency_key"
                    )
        
        return errors
    
    def _check_approvals(
        self,
        actions: list[ActionRequest],
        profile: AgentProfile,
        tokens: dict[str, str],
    ) -> list[ActionRequest]:
        """Return actions that need approval but don't have tokens."""
        
        pending = []
        
        for action in actions:
            needs_approval = (
                action.requires_approval or
                action.capability_name in profile.approval_policy.require_approval_for or
                (action.side_effect == SideEffect.EXTERNAL_WRITE and
                 SideEffect.EXTERNAL_WRITE not in profile.approval_policy.auto_approve_side_effects)
            )
            
            if needs_approval and action.action_id not in tokens:
                pending.append(action)
        
        return pending
    
    def _determine_outcome(
        self,
        tool_calls: list[ToolCallRecord],
        artifacts: list[ContextRef],
    ) -> Outcome:
        """Determine overall execution outcome."""
        
        if not tool_calls:
            return Outcome(status=OutcomeStatus.COMPLETED, artifacts=[])
        
        statuses = [tc.status for tc in tool_calls]
        
        if all(s == CallStatus.SUCCESS for s in statuses):
            return Outcome(
                status=OutcomeStatus.COMPLETED,
                artifacts=artifacts,
            )
        elif all(s in (CallStatus.ERROR, CallStatus.DENIED) for s in statuses):
            return Outcome(
                status=OutcomeStatus.FAILED,
                artifacts=[],
            )
        else:
            return Outcome(
                status=OutcomeStatus.PARTIAL,
                artifacts=artifacts,
            )
    
    def _extract_artifacts(
        self,
        action: ActionRequest,
        output: dict,
    ) -> list[ContextRef]:
        """Extract created artifacts from tool output."""
        
        artifacts = []
        
        # Common patterns for created items
        for key in ("task_id", "note_id", "event_id", "doc_id"):
            if key in output:
                ref_type = key.replace("_id", "")
                artifacts.append(ContextRef(
                    ref_type=RefType(ref_type),
                    ref_id=output[key],
                    metadata={"created_by": action.capability_name},
                ))
        
        return artifacts
```

---

## Workflow Runner

### Purpose

The Workflow Runner orchestrates the complete execution flow:
1. Trigger detection
2. Context assembly
3. Plan generation
4. Validation
5. Approval gating
6. Execution
7. Write-back
8. Trace emission

### Workflow Specification

```python
class WorkflowSpec(BaseModel):
    """Declarative workflow specification."""
    
    workflow_id: str
    name: str
    description: str | None = None
    trigger: TriggerSpec
    agent_profile_id: str
    steps: list[WorkflowStep] = [
        WorkflowStep.ASSEMBLE_CONTEXT,
        WorkflowStep.PROPOSE_PLAN,
        WorkflowStep.VALIDATE,
        WorkflowStep.GATE_APPROVALS,
        WorkflowStep.EXECUTE,
        WorkflowStep.WRITE_BACK,
        WorkflowStep.EMIT_TRACE,
    ]
    on_error: ErrorStrategy = ErrorStrategy.HALT
    write_back: WriteBackConfig | None = None

class WorkflowStep(str, Enum):
    ASSEMBLE_CONTEXT = "assemble_context"
    PROPOSE_PLAN = "propose_plan"
    VALIDATE = "validate"
    GATE_APPROVALS = "gate_approvals"
    EXECUTE = "execute"
    WRITE_BACK = "write_back"
    EMIT_TRACE = "emit_trace"

class TriggerSpec(BaseModel):
    type: TriggerType
    schedule: str | None = None  # Cron expression
    watch_path: str | None = None  # File path
    event_types: list[str] | None = None  # Event types

class TriggerType(str, Enum):
    CRON = "cron"
    FILE_WATCH = "file_watch"
    EVENT = "event"
    MANUAL = "manual"

class ErrorStrategy(str, Enum):
    HALT = "halt"
    CONTINUE = "continue"
    RETRY = "retry"

class WriteBackConfig(BaseModel):
    create_summary_note: bool = False
    update_graph: bool = True
    notify: list[str] = []  # Notification channels
```

### Workflow Runner Implementation

```python
class WorkflowRunner:
    """
    Executes workflows as state machines.
    """
    
    def __init__(
        self,
        context_assembler: ContextAssembler,
        engine_registry: EngineRegistry,
        executor: DeterministicExecutor,
        profile_loader: AgentProfileLoader,
        memory: MemoryCoordinator,
        event_log: EventLog,
    ):
        self.assembler = context_assembler
        self.engines = engine_registry
        self.executor = executor
        self.profiles = profile_loader
        self.memory = memory
        self.events = event_log
    
    async def run(
        self,
        workflow: WorkflowSpec,
        intent: str | None = None,
        project_id: str | None = None,
        approval_tokens: dict[str, str] | None = None,
    ) -> WorkflowResult:
        """
        Execute a workflow.
        
        Args:
            workflow: Workflow specification
            intent: Override intent (for manual triggers)
            project_id: Optional project scope
            approval_tokens: Pre-approved actions
        
        Returns:
            WorkflowResult with trace and status
        """
        
        run_id = generate_ulid()
        
        # Emit workflow started event
        await self.events.append(Event(
            event_id=generate_ulid(),
            event_type=EventType.WORKFLOW_STARTED,
            timestamp=datetime.utcnow(),
            entity_id=run_id,
            entity_type="workflow_run",
            payload={"workflow_id": workflow.workflow_id},
        ))
        
        try:
            # 1. Load agent profile
            profile = self.profiles.get(workflow.agent_profile_id)
            
            # 2. Determine intent
            if not intent:
                intent = self._default_intent(workflow)
            
            # 3. Execute steps
            context_packet: ContextPacket | None = None
            plan: Plan | None = None
            trace: DecisionTrace | None = None
            
            for step in workflow.steps:
                if step == WorkflowStep.ASSEMBLE_CONTEXT:
                    context_packet = await self.assembler.assemble(
                        intent=intent,
                        context_policy=profile.context_policy,
                        project_id=project_id,
                    )
                
                elif step == WorkflowStep.PROPOSE_PLAN:
                    engine = self.engines.get(profile.engine)
                    plan = await engine.propose(context_packet, profile)
                
                elif step == WorkflowStep.VALIDATE:
                    # Executor handles validation
                    pass
                
                elif step == WorkflowStep.GATE_APPROVALS:
                    # Executor handles approval gating
                    pass
                
                elif step == WorkflowStep.EXECUTE:
                    engine = self.engines.get(profile.engine)
                    engine_metadata = EngineMetadata(
                        engine_id=engine.engine_id,
                        version=engine.version,
                        model_provider=profile.model_config.provider,
                        model_name=profile.model_config.model,
                    )
                    
                    trace = await self.executor.execute(
                        plan=plan,
                        context_packet=context_packet,
                        agent_profile=profile,
                        engine_metadata=engine_metadata,
                        approval_tokens=approval_tokens,
                    )
                
                elif step == WorkflowStep.WRITE_BACK:
                    if workflow.write_back and trace:
                        await self._write_back(workflow.write_back, trace)
                
                elif step == WorkflowStep.EMIT_TRACE:
                    # Already done in executor
                    pass
            
            # Emit workflow completed event
            await self.events.append(Event(
                event_id=generate_ulid(),
                event_type=EventType.WORKFLOW_COMPLETED,
                timestamp=datetime.utcnow(),
                entity_id=run_id,
                entity_type="workflow_run",
                payload={
                    "workflow_id": workflow.workflow_id,
                    "trace_id": trace.trace_id if trace else None,
                    "outcome": trace.outcome.status.value if trace else None,
                },
            ))
            
            return WorkflowResult(
                run_id=run_id,
                workflow_id=workflow.workflow_id,
                status="completed",
                trace=trace,
            )
        
        except Exception as e:
            # Emit workflow failed event
            await self.events.append(Event(
                event_id=generate_ulid(),
                event_type=EventType.WORKFLOW_FAILED,
                timestamp=datetime.utcnow(),
                entity_id=run_id,
                entity_type="workflow_run",
                payload={
                    "workflow_id": workflow.workflow_id,
                    "error": str(e),
                },
            ))
            
            if workflow.on_error == ErrorStrategy.HALT:
                raise
            
            return WorkflowResult(
                run_id=run_id,
                workflow_id=workflow.workflow_id,
                status="failed",
                error=str(e),
            )
    
    async def _write_back(
        self,
        config: WriteBackConfig,
        trace: DecisionTrace,
    ) -> None:
        """Write results back to memory."""
        
        if config.create_summary_note:
            await self.memory.store_note(
                note_id=f"summary_{trace.trace_id}",
                content=self._format_summary(trace),
                metadata={
                    "type": "workflow_summary",
                    "trace_id": trace.trace_id,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
        
        if config.update_graph:
            # Update graph with execution results
            for artifact in trace.outcome.artifacts:
                await self.memory.graph.upsert_edge(GraphEdge(
                    edge_id=generate_ulid(),
                    edge_type=EdgeType.RELATED_TO,
                    source_id=trace.trace_id,
                    target_id=artifact.ref_id,
                    properties={"relationship": "created_by"},
                    created_at=datetime.utcnow(),
                ))
    
    def _default_intent(self, workflow: WorkflowSpec) -> str:
        """Generate default intent for workflow."""
        
        return f"Execute {workflow.name} workflow"
    
    def _format_summary(self, trace: DecisionTrace) -> str:
        """Format trace as summary note."""
        
        return f"""# Workflow Summary

**Intent:** {trace.intent}
**Status:** {trace.outcome.status.value}
**Time:** {trace.timestamp.isoformat()}

## Plan Summary
{trace.plan.summary}

## Actions Executed
{chr(10).join(f'- {tc.capability_name}: {tc.status.value}' for tc in trace.tool_calls)}

## Artifacts Created
{chr(10).join(f'- {a.ref_type}: {a.ref_id}' for a in trace.outcome.artifacts)}
"""


class WorkflowResult(BaseModel):
    """Result of workflow execution."""
    
    run_id: str
    workflow_id: str
    status: str  # "completed", "failed", "pending_approval"
    trace: DecisionTrace | None = None
    pending_approvals: list[ActionRequest] | None = None
    error: str | None = None
```

---

## Workflow Definitions

### Example: Daily Check-in

```yaml
# configs/workflows/daily_checkin.yaml
workflow_id: daily_checkin
name: Daily Check-in
description: Review tasks and plan the day

trigger:
  type: cron
  schedule: "0 9 * * 1-5"  # 9 AM weekdays

agent_profile_id: daily_review_agent

steps:
  - assemble_context
  - propose_plan
  - validate
  - gate_approvals
  - execute
  - write_back
  - emit_trace

on_error: halt

write_back:
  create_summary_note: true
  update_graph: true
```

### Example: Weekly Review

```yaml
# configs/workflows/weekly_review.yaml
workflow_id: weekly_review
name: Weekly Review
description: Comprehensive weekly review and planning

trigger:
  type: cron
  schedule: "0 17 * * 5"  # 5 PM Friday

agent_profile_id: project_manager_agent

steps:
  - assemble_context
  - propose_plan
  - validate
  - gate_approvals
  - execute
  - write_back
  - emit_trace

write_back:
  create_summary_note: true
  update_graph: true
```

### Example: Note Watcher

```yaml
# configs/workflows/note_watcher.yaml
workflow_id: note_watcher
name: Note Watcher
description: Process new notes and extract tasks

trigger:
  type: file_watch
  watch_path: "${OBSIDIAN_VAULT_PATH}/Inbox"

agent_profile_id: task_extractor_agent

steps:
  - assemble_context
  - propose_plan
  - execute
  - write_back
  - emit_trace
```

---

## WorkflowRun Lifecycle (v1.0.1)

Workflows now persist their state via `WorkflowRun` records:

```python
class WorkflowRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

### State Transitions

```
QUEUED → RUNNING → COMPLETED
                 → FAILED
                 → WAITING_APPROVAL → RUNNING (on approval)
                                    → CANCELLED (on denial/expiry)
```

### WorkflowRun Persistence

Workflow runs persist to SQLite via `SQLiteWorkflowRunStore` at `data/workflows/workflows.db`.
The store also manages step checkpoints and approval requests.

```python
# On workflow start
workflow_run = WorkflowRun(
    run_id=generate_ulid(),
    workflow_id=workflow.workflow_id,
    status=WorkflowRunStatus.RUNNING,
    started_at=utc_now(),
    intent=intent,
)
workflow_store.create_run(workflow_run)

# After each step — checkpoint saved for resumption
workflow_store.save_checkpoint(run_id, step_index, step_name, step_outputs)

# On approval needed
workflow_run.status = WorkflowRunStatus.WAITING_APPROVAL
workflow_store.update_run(workflow_run)

# On resume after approval — loads checkpoint, skips completed steps
checkpoint = workflow_store.get_checkpoint(run_id)
workflow_run.status = WorkflowRunStatus.RUNNING
workflow_store.update_run(workflow_run)

# On completion — checkpoints cleaned up
workflow_store.delete_checkpoints(run_id)
```

---

## Approval Flow (v1.0.1)

When actions require approval, the system creates durable `ApprovalRequest` records:

```python
class ApprovalGate:
    """Handles approval requests and tokens."""
    
    def __init__(self, event_log: EventLog):
        self.events = event_log
        self._pending: dict[str, PendingApproval] = {}
    
    async def request_approval(
        self,
        action: ActionRequest,
        trace_id: str,
    ) -> str:
        """Request approval for an action."""
        
        approval_id = generate_ulid()
        
        self._pending[approval_id] = PendingApproval(
            approval_id=approval_id,
            action=action,
            trace_id=trace_id,
            requested_at=datetime.utcnow(),
        )
        
        await self.events.append(Event(
            event_id=generate_ulid(),
            event_type=EventType.APPROVAL_REQUESTED,
            timestamp=datetime.utcnow(),
            entity_id=approval_id,
            entity_type="approval",
            payload={
                "action_id": action.action_id,
                "capability": action.capability_name,
            },
            trace_id=trace_id,
        ))
        
        return approval_id
    
    async def grant_approval(
        self,
        approval_id: str,
        approved_by: str,
    ) -> str:
        """Grant approval and return token."""
        
        pending = self._pending.get(approval_id)
        if not pending:
            raise ValueError(f"No pending approval: {approval_id}")
        
        token = generate_ulid()
        
        await self.events.append(Event(
            event_id=generate_ulid(),
            event_type=EventType.APPROVAL_GRANTED,
            timestamp=datetime.utcnow(),
            entity_id=approval_id,
            entity_type="approval",
            payload={"approved_by": approved_by},
            trace_id=pending.trace_id,
        ))
        
        del self._pending[approval_id]
        return token
    
    async def deny_approval(
        self,
        approval_id: str,
        denied_by: str,
        reason: str,
    ) -> None:
        """Deny an approval request."""
        
        pending = self._pending.get(approval_id)
        if not pending:
            raise ValueError(f"No pending approval: {approval_id}")
        
        await self.events.append(Event(
            event_id=generate_ulid(),
            event_type=EventType.APPROVAL_DENIED,
            timestamp=datetime.utcnow(),
            entity_id=approval_id,
            entity_type="approval",
            payload={"denied_by": denied_by, "reason": reason},
            trace_id=pending.trace_id,
        ))
        
        del self._pending[approval_id]


class PendingApproval(BaseModel):
    approval_id: str
    action: ActionRequest
    trace_id: str
    requested_at: datetime
```

---

## Approval Persistence (v1.0.1)

Approvals are now persisted as `ApprovalRequest` entities in SQLite:

```python
class ApprovalRequest(VersionedModel):
    approval_id: str
    trace_id: str
    run_id: str
    workflow_id: str
    action_id: str
    capability_name: str
    effective_side_effect: SideEffect  # From CapabilityDef
    status: ApprovalRequestStatus  # pending/approved/denied/expired
    requested_at: datetime
    resolved_at: datetime | None
    resolver: str | None
    reason: str | None
    action_preview: dict  # Redacted args for UI
```

### ApprovalStore Interface

```python
class ApprovalStore(ABC):
    @abstractmethod
    def save(self, approval: ApprovalRequest) -> None: ...
    
    @abstractmethod
    def get(self, approval_id: str) -> ApprovalRequest | None: ...
    
    @abstractmethod
    def approve(self, approval_id: str, resolver: str, reason: str | None) -> ApprovalRequest | None: ...
    
    @abstractmethod
    def deny(self, approval_id: str, resolver: str, reason: str) -> ApprovalRequest | None: ...
    
    @abstractmethod
    def list_pending(self, workflow_id: str | None) -> list[ApprovalRequest]: ...
```

### Workflow Resume

When an approval is resolved:

1. `ApprovalRequest.status` is updated via `ApprovalStore.approve()`
2. `WorkflowRunner.resume(run_id, approval_tokens)` is called
3. Checkpoint is loaded from `SQLiteWorkflowRunStore`
4. Workflow continues from checkpoint (skips completed steps)
5. Remaining actions are executed with approval tokens

```python
async def resume(self, run_id: str, approval_tokens: dict[str, str]) -> WorkflowResult:
    """Resume a workflow after approval.

    Uses checkpoint system to restore state and resume from the
    last successful step instead of re-executing the entire workflow.
    """
    workflow_run = self._workflow_store.get_run(run_id)

    if workflow_run.status != WorkflowRunStatus.WAITING_APPROVAL:
        return WorkflowResult(error="Not waiting for approval", ...)

    checkpoint = self._workflow_store.get_checkpoint(run_id)
    if checkpoint:
        return await self._run_from_checkpoint(workflow_run, checkpoint, approval_tokens)
    else:
        # No checkpoint, fall back to full re-run
        return await self.run(workflow_id=workflow_run.workflow_id, ...)
```

**CLI Integration:**

- `agent-kernel approve <id>` — approves and auto-resumes the workflow
- `agent-kernel approve <id> --no-resume` — approves without resuming
- `agent-kernel resume-workflow <run_id>` — manually resume a paused workflow
- `agent-kernel list-runs` — view workflow run status and history

**Persistence:**

All workflow runs persist to `data/workflows/workflows.db` via `SQLiteWorkflowRunStore`.
Both `run-workflow` and `run-workflow-thinking` CLI commands use persistent storage.
Checkpoints are saved after each step and deleted on completion.

---

## Related Documents

- [00-overview.md](00-overview.md) - Design principles
- [01-schemas.md](01-schemas.md) - DecisionTrace, Plan schemas
- [03-tools.md](03-tools.md) - Tool Broker
- [05-engines.md](05-engines.md) - Agent engines
- [07-tracing.md](07-tracing.md) - Trace storage
