# Core Schemas

**Version:** 1.1.3
**Status:** Implementation Phase

These are the canonical data contracts for the Agent Kernel. All components communicate through these typed Pydantic models.

## Version History

| Version | Additions |
|---------|-----------|
| 1.0.0 | Core schemas (ContextRef, Plan, DecisionTrace) |
| 1.0.1 | VersionedModel, LLMCallRecord, GraphNode/Edge, WorkflowRun |
| 1.0.2 | ContextPack, SourceDescriptor, RetrievalPlan |
| 1.0.3 | ThinkingConfig, RetrievalConfig |
| 1.0.4 | **EntityRef, EntityView, OutcomeEvaluation, ExperienceCase, LessonLearned, Playbook, RetentionPolicy** |
| 1.1.2 | **SkillManifest, SkillOrigin, SkillResourceRef, RefType.SKILL** |
| 1.1.3 | **TriggerType.WORKFLOW, WorkflowSpec.on_complete, WorkflowTrigger.source_workflow_id** |

---

## Schema Evolution (v1.0.1)

### Versioning Policy

All persisted schemas include version tracking:

```python
SCHEMA_VERSION = "1.0.1"

class VersionedModel(KernelModel):
    """Base for schemas requiring version tracking."""
    schema_version: str = Field(default=SCHEMA_VERSION)
    kernel_version: str = Field(default_factory=get_kernel_version)
```

**Rules:**
- Additive changes only within minor versions (new optional fields, never rename required)
- Breaking changes require new major version with explicit upcasters
- Upcasters run on load, before Pydantic validation

### Migration Registry

```python
# core/migrations.py
@register("1.0.0", "1.0.1")
def upcast_v1_0_0_to_v1_0_1(payload: dict) -> dict:
    """Migrate v1.0.0 payloads to v1.0.1."""
    # Add missing fields with defaults
    ...
```

---

## Schema Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        SCHEMA HIERARCHY                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Base Layer                                                      │
│  └── VersionedModel      (schema/kernel version tracking)       │
│                                                                  │
│  Context Layer                                                   │
│  ├── ContextRef          (reference to any source item)         │
│  └── ContextPacket       (bounded input to agent) [versioned]   │
│                                                                  │
│  Plan Layer                                                      │
│  ├── ActionRequest       (tool-like action + evidence_refs)     │
│  └── Plan                (structured agent output) [versioned]  │
│                                                                  │
│  Execution Layer                                                 │
│  ├── ToolCallRecord      (what ran + trust boundary) [versioned]│
│  ├── LLMCallRecord       (LLM interaction details) [versioned]  │
│  └── DecisionTrace       (complete auditable unit) [versioned]  │
│                                                                  │
│  Workflow Layer                                                  │
│  ├── WorkflowRun         (workflow lifecycle) [versioned]       │
│  └── ApprovalRequest     (pending approval) [versioned]         │
│                                                                  │
│  Graph Layer                                                     │
│  ├── GraphNode           (entity node) [versioned]              │
│  └── GraphEdge           (relationship edge) [versioned]        │
│                                                                  │
│  Configuration Layer                                             │
│  ├── AgentProfile        (agent behavior config)                │
│  └── CapabilityDef       (tool capability schema)               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## A) ContextRef

**Purpose:** Reference to any source item the agent used.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ref_type` | `RefType` | Yes | Type of reference |
| `ref_id` | `str` | Yes | Stable unique identifier |
| `uri` | `str` | No | File path, Obsidian link, URL |
| `hash` | `str` | No | Content hash for reproducibility |
| `metadata` | `dict` | No | Title, timestamps, tags |

### RefType Enum

```python
class RefType(str, Enum):
    NOTE = "note"
    TASK = "task"
    EVENT = "event"
    GRAPH_NODE = "graph_node"
    GRAPH_EDGE = "graph_edge"
    DOCUMENT = "doc"
    EMAIL = "email"
    SKILL = "skill"
    MEMORY = "memory"
    EXTERNAL = "external"
```

### Example

```json
{
  "ref_type": "note",
  "ref_id": "01HXYZ123ABC",
  "uri": "obsidian://open?vault=work&file=Projects/Agent%20Kernel",
  "hash": "sha256:a1b2c3...",
  "metadata": {
    "title": "Agent Kernel Project Notes",
    "created_at": "2024-01-15T10:30:00Z",
    "tags": ["project", "agent", "kernel"]
  }
}
```

---

## B) ContextPacket

**Purpose:** The bounded input an agent receives. Deterministically assembled.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `packet_id` | `str` | Yes | Unique identifier (ULID) |
| `intent` | `str` | Yes | The user intent or query |
| `project_id` | `str` | No | Optional project scope |
| `generated_at` | `datetime` | Yes | When context was assembled |
| `budget` | `ContextBudget` | Yes | Limits for context |
| `items` | `list[ContextItem]` | Yes | Retrieved context items |
| `graph_slice` | `GraphSlice` | No | Optional subgraph |
| `retrieval_report` | `RetrievalReport` | Yes | Debug info on what was retrieved |

### ContextBudget

```python
class ContextBudget(BaseModel):
    max_tokens: int = 8000
    max_items: int = 50
    retrieval_limits: RetrievalLimits
    
class RetrievalLimits(BaseModel):
    max_notes: int = 20
    max_tasks: int = 30
    max_events: int = 10
    max_graph_nodes: int = 50
```

### ContextItem

```python
class ContextItem(BaseModel):
    ref: ContextRef
    excerpt: str  # Extracted text snippet
    summary: str | None = None  # Optional AI summary
    relevance_score: float = 0.0
    included_reason: str  # Why this was included
```

### RetrievalReport

```python
class RetrievalReport(BaseModel):
    queries_run: list[QueryRecord]
    filters_applied: list[str]
    items_considered: int
    items_selected: int
    selection_strategy: str
    
class QueryRecord(BaseModel):
    source: str  # "vector", "graph", "document"
    query: str
    results_count: int
    duration_ms: int
```

### Example

```json
{
  "packet_id": "01HXYZ456DEF",
  "intent": "What tasks should I focus on today?",
  "project_id": "agent_kernel",
  "generated_at": "2024-01-15T09:00:00Z",
  "budget": {
    "max_tokens": 4000,
    "max_items": 30,
    "retrieval_limits": {
      "max_notes": 10,
      "max_tasks": 20,
      "max_events": 5
    }
  },
  "items": [
    {
      "ref": {"ref_type": "task", "ref_id": "task_001"},
      "excerpt": "Implement ContextPacket schema",
      "relevance_score": 0.95,
      "included_reason": "Open task in current project"
    }
  ],
  "retrieval_report": {
    "queries_run": [
      {"source": "vector", "query": "tasks today", "results_count": 15, "duration_ms": 45}
    ],
    "filters_applied": ["project=agent_kernel", "status=open"],
    "items_considered": 47,
    "items_selected": 12,
    "selection_strategy": "relevance_ranked"
  }
}
```

---

## C) ActionRequest

**Purpose:** A tool-like action that can be executed deterministically.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `action_id` | `str` | Yes | Unique identifier (ULID) |
| `capability_name` | `str` | Yes | e.g., `tasks.create@v1` |
| `args` | `dict` | Yes | Arguments (validated against schema) |
| `side_effect` | `SideEffect` | Yes | Effect classification (agent hint only) |
| `requires_approval` | `bool` | Yes | Whether approval is needed (agent hint only) |
| `evidence_refs` | `list[str]` | Yes | Context refs supporting this action (v1.0.1) |
| `rollback_hint` | `str` | No | How to undo (if applicable) |
| `idempotency_key` | `str` | For writes | Required for write operations |
| `cap_group` | `str` | No | Optional cap group for deterministic limits |
| `cap_limit` | `int` | No | Maximum actions allowed for `cap_group` |

> **Trust Boundary (v1.0.1):** `side_effect` and `requires_approval` are **non-authoritative hints** from the agent. The executor computes effective values from `CapabilityDef` and `AgentProfile`.

### SideEffect Enum

```python
class SideEffect(str, Enum):
    NONE = "none"           # Read-only
    LOCAL_WRITE = "local"   # Local file/DB changes
    EXTERNAL_WRITE = "external"  # External API calls
```

### Example

```json
{
  "action_id": "01HXYZ789GHI",
  "capability_name": "tasks.create@v1",
  "args": {
    "title": "Review agent kernel design",
    "due_date": "2024-01-16",
    "priority": "high",
    "project_id": "agent_kernel"
  },
  "side_effect": "local",
  "requires_approval": false,
  "idempotency_key": "task_create_20240115_001"
}
```

---

## D) Plan

**Purpose:** The structured agent output. Strictly validated.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `plan_id` | `str` | Yes | Unique identifier (ULID) |
| `intent` | `str` | Yes | Original intent |
| `summary` | `str` | Yes | 1–5 sentence summary |
| `context_refs_used` | `list[ContextRef]` | Yes | Must cite sources |
| `actions` | `list[ActionRequest]` | Yes | Actions to execute |
| `risk` | `RiskAssessment` | Yes | Risk evaluation |
| `questions` | `list[str]` | No | Clarifying questions |
| `notes` | `str` | No | Short rationale (keep concise) |
| `validation` | `PlanValidation` | Yes | Self-check fields |

### RiskAssessment

```python
class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class RiskAssessment(BaseModel):
    level: RiskLevel
    reasons: list[str]
```

### PlanValidation

```python
class PlanValidation(BaseModel):
    missing_info: list[str] = []
    assumptions: list[str] = []
```

### Example

```json
{
  "plan_id": "01HXYZ012JKL",
  "intent": "What tasks should I focus on today?",
  "summary": "Based on your open tasks and calendar, I recommend focusing on the agent kernel schema implementation. You have 3 high-priority tasks due today.",
  "context_refs_used": [
    {"ref_type": "task", "ref_id": "task_001"},
    {"ref_type": "note", "ref_id": "note_design"}
  ],
  "actions": [
    {
      "action_id": "01HXYZ789GHI",
      "capability_name": "tasks.prioritize@v1",
      "args": {"task_ids": ["task_001", "task_002"]},
      "side_effect": "local",
      "requires_approval": false
    }
  ],
  "risk": {
    "level": "low",
    "reasons": ["All actions are read-only or local writes"]
  },
  "notes": "Prioritized based on due dates and project context.",
  "validation": {
    "missing_info": [],
    "assumptions": ["Assuming you want to focus on agent_kernel project"]
  }
}
```

---

## E) ToolCallRecord

**Purpose:** What actually ran. Immutable execution record. Inherits from `VersionedModel`.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tool_call_id` | `str` | Yes | Unique identifier (ULID) |
| `capability_name` | `str` | Yes | e.g., `tasks.create@v1` |
| `started_at` | `datetime` | Yes | Execution start (UTC) |
| `ended_at` | `datetime` | Yes | Execution end (UTC) |
| `duration_ms` | `int` | Yes | Duration in milliseconds |
| `input` | `dict` | Yes | Input args (redacted as needed) |
| `output` | `dict` | Yes | Output (redacted as needed) |
| `status` | `CallStatus` | Yes | Execution status |
| `error` | `ErrorRecord` | No | Structured error if failed |
| `cost` | `CostRecord` | No | Tokens, $ estimate |
| `related_action_id` | `str` | Yes | Link to ActionRequest |
| `requested_side_effect` | `SideEffect` | No | Agent's requested side effect (v1.0.1) |
| `requested_requires_approval` | `bool` | No | Agent's requested approval (v1.0.1) |
| `effective_side_effect` | `SideEffect` | Yes | Computed from CapabilityDef (v1.0.1) |
| `effective_requires_approval` | `bool` | Yes | Computed from policy (v1.0.1) |
| `idempotency_key` | `str` | No | For deduplication of writes (v1.0.1) |
| `schema_version` | `str` | Yes | Schema version (inherited) |
| `kernel_version` | `str` | Yes | Kernel version (inherited) |

> **Trust Boundary (v1.0.1):** The executor MUST compute `effective_*` fields deterministically from `CapabilityDef.side_effect_level`, `CapabilityDef.requires_approval_default`, and `AgentProfile.approval_policy`. Agent-provided values are logged as `requested_*` for debugging but are never authoritative.

### CallStatus Enum

```python
class CallStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    DENIED = "denied"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
```

### ErrorRecord

```python
class ErrorRecord(BaseModel):
    code: str
    message: str
    details: dict | None = None
    retryable: bool = False
```

### CostRecord

```python
class CostRecord(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
```

---

## F) DecisionTrace

**Purpose:** The complete auditable unit of work. Inherits from `VersionedModel`.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `trace_id` | `str` | Yes | Unique identifier (ULID) |
| `run_id` | `str` | Yes | Workflow run identifier |
| `workflow_id` | `str` | Yes | Explicit workflow ID (v1.0.1) |
| `agent_profile_id` | `str` | Yes | Which agent profile |
| `engine_id` | `str` | Yes | Which engine produced plan |
| `intent` | `str` | Yes | Original intent |
| `timestamp` | `datetime` | Yes | When trace was created (UTC) |
| `context_packet_id` | `str` | Yes | Link to ContextPacket |
| `plan` | `Plan` | Yes | Embedded or referenced |
| `tool_calls` | `list[ToolCallRecord]` | Yes | All tool executions |
| `llm_calls` | `list[LLMCallRecord]` | Yes | All LLM interactions (v1.0.1) |
| `approvals` | `list[ApprovalRecord]` | No | Approval/denial records |
| `outcome` | `Outcome` | Yes | Final result |
| `provenance` | `Provenance` | Yes | Version/config info |
| `schema_version` | `str` | Yes | Schema version (inherited) |
| `kernel_version` | `str` | Yes | Kernel version (inherited) |

### ApprovalRecord

```python
class ApprovalRecord(BaseModel):
    action_id: str
    approved: bool
    approved_by: str | None = None
    approved_at: datetime | None = None
    reason: str | None = None
```

### Outcome

```python
class OutcomeStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    NEEDS_APPROVAL = "needs_approval"
    CANCELLED = "cancelled"

class Outcome(BaseModel):
    status: OutcomeStatus
    artifacts: list[ContextRef] = []  # Created items
    summary: str | None = None
```

### Provenance

```python
class PromptPartRef(BaseModel):
    prompt_id: str
    hash: str
    layer: str | None = None
    path: str | None = None

class Provenance(BaseModel):
    prompt_hash: str | None = None
    prompt_bundle_hash: str | None = None
    prompt_parts: list[PromptPartRef] = []
    config_hash: str
    git_commit: str | None = None
    engine_version: str
    kernel_version: str
```

---

## G) LLMCallRecord (v1.0.1)

**Purpose:** Detailed record of LLM interactions for debugging, tuning, and cost tracking.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `llm_call_id` | `str` | Yes | Unique identifier (ULID) |
| `trace_id` | `str` | Yes | Parent trace |
| `stage` | `str` | Yes | `routing`, `propose_plan`, `critic`, `revise`, `other` |
| `started_at` | `datetime` | Yes | When call started (UTC) |
| `ended_at` | `datetime` | Yes | When call ended (UTC) |
| `duration_ms` | `int` | Yes | Duration in milliseconds |
| `request` | `LLMRequest` | Yes | Request details |
| `response` | `LLMResponse` | Yes | Response details |
| `request_hash` | `str` | No | Hash for reproducibility |
| `response_hash` | `str` | No | Hash for dedup |
| `escalated_from_id` | `str` | No | Previous call if escalated |

### LLMRequest

```python
class LLMRequest(KernelModel):
    model: str  # e.g., "gpt-4o"
    provider: str  # e.g., "openai"
    messages: list[dict[str, Any]]  # Redacted before persistence
    temperature: float = 0.3
    max_tokens: int | None = None
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    response_schema_name: str | None = None  # e.g., "Plan"
```

### LLMResponse

```python
class LLMResponse(KernelModel):
    model: str
    provider: str
    output_text: str | None = None
    parsed: dict[str, Any] | None = None  # Structured output
    finish_reason: str | None = None
    usage: CostRecord | None = None
    latency_ms: int | None = None
```

---

## H) WorkflowRun (v1.0.1)

**Purpose:** Tracks workflow lifecycle for debugging and resume capability.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `run_id` | `str` | Yes | Unique identifier (ULID) |
| `workflow_id` | `str` | Yes | Which workflow |
| `status` | `WorkflowRunStatus` | Yes | Current status |
| `intent` | `str` | No | Intent being processed |
| `started_at` | `datetime` | No | When started (UTC) |
| `ended_at` | `datetime` | No | When ended (UTC) |
| `last_step` | `str` | No | Last completed step |
| `retry_count` | `int` | Yes | Retry attempts |
| `error` | `ErrorRecord` | No | Error if failed |
| `trace_ids` | `list[str]` | Yes | Related trace IDs |

### WorkflowRunStatus

```python
class WorkflowRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

---

## H.1) Workflow Triggers and Chaining (v1.1.3)

**Purpose:** Enable workflows to trigger other workflows, supporting consolidation and composition.

### TriggerType Enum

```python
class TriggerType(str, Enum):
    MANUAL = "manual"       # CLI/API trigger
    CRON = "cron"           # Time-based schedule
    EVENT = "event"         # System event trigger
    FILE_WATCH = "file_watch"  # File system changes
    WORKFLOW = "workflow"   # Triggered by another workflow's completion
```

### WorkflowTrigger

```python
class WorkflowTrigger(KernelModel):
    type: TriggerType = TriggerType.MANUAL
    schedule: str | None = None           # Cron expression for cron trigger
    event_type: str | None = None         # Event type for event trigger
    path: str | None = None               # Path for file_watch trigger
    source_workflow_id: str | None = None # Workflow ID for workflow trigger
    on_success_only: bool = True          # Only trigger on successful completion
```

### WorkflowSpec.on_complete

```python
class WorkflowSpec(KernelModel):
    # ... existing fields ...
    on_complete: list[str] = []  # Workflow IDs to trigger on completion
```

### Workflow Chaining Patterns

There are two ways to chain workflows:

#### 1. Workflow Trigger Type

A workflow declares it should be triggered when another workflow completes:

```yaml
# cleanup_workflow.yaml
workflow_id: cleanup_workflow
trigger:
  type: workflow
  source_workflow_id: data_processing
  on_success_only: true  # Only trigger if source succeeded
```

#### 2. Declarative on_complete Chain

A workflow declares what should run after it completes:

```yaml
# data_processing.yaml
workflow_id: data_processing
trigger:
  type: cron
  schedule: "0 6 * * *"
on_complete:
  - cleanup_workflow
  - notification_workflow
```

### Chaining Example

```yaml
# Daily workflow chain:
# vault_sync → enrichment → summary

# 1. First workflow - scheduled
workflow_id: daily_vault_sync
trigger:
  type: cron
  schedule: "0 6 * * *"
on_complete:
  - daily_enrichment

# 2. Second workflow - triggered by first
workflow_id: daily_enrichment
trigger:
  type: workflow
  source_workflow_id: daily_vault_sync
on_complete:
  - daily_summary

# 3. Third workflow - triggered by second
workflow_id: daily_summary
trigger:
  type: workflow
  source_workflow_id: daily_enrichment
```

### Scheduler Methods

The Scheduler tracks workflow dependencies and provides helper methods:

```python
# Get workflows triggered when this workflow completes
scheduler.get_workflow_triggers("source_workflow") -> ["target_a", "target_b"]

# Get workflows that trigger this workflow
scheduler.get_triggered_by("target_workflow") -> ["source_a", "source_b"]

# Manually handle completion (called automatically by scheduler)
await scheduler.handle_workflow_completed(
    workflow_id="source_workflow",
    success=True,
    run_id="run_123",
    trace_id="trace_456",
)
```

### Behavior Notes

1. **Deduplication**: If a workflow is listed in both `on_complete` and as a `workflow` trigger, it's only triggered once
2. **Failure handling**: `on_success_only=True` (default) skips triggers when the source workflow fails
3. **Cascading**: Triggered workflows can themselves trigger other workflows (chains can be arbitrarily deep)
4. **Disable control**: Disabled workflows (via `scheduler.disable_job()`) won't be triggered

---

## I) ApprovalRequest (v1.0.1)

**Purpose:** Durable record for pending approvals, enabling workflow resume.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `approval_id` | `str` | Yes | Unique identifier (ULID) |
| `trace_id` | `str` | Yes | Related trace |
| `run_id` | `str` | Yes | Related workflow run |
| `workflow_id` | `str` | Yes | Workflow being paused |
| `action_id` | `str` | Yes | Action needing approval |
| `capability_name` | `str` | Yes | What capability |
| `effective_side_effect` | `SideEffect` | Yes | Computed side effect |
| `status` | `ApprovalRequestStatus` | Yes | Current status |
| `requested_at` | `datetime` | Yes | When requested (UTC) |
| `resolved_at` | `datetime` | No | When resolved (UTC) |
| `resolver` | `str` | No | Who resolved |
| `reason` | `str` | No | Approval/denial reason |
| `action_preview` | `dict` | No | Redacted args summary for UI |

### ApprovalRequestStatus

```python
class ApprovalRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
```

---

## J) Graph Ontology (v1.0.6)

**Purpose:** Typed nodes and edges for the context graph with provenance tracking.

### NodeType

```python
class NodeType(str, Enum):
    # Core types (v1.0.1)
    NOTE = "note"
    TAG = "tag"
    TASK = "task"
    PROJECT = "project"
    TRACE = "trace"
    CALENDAR_EVENT = "calendar_event"
    PERSON = "person"

    # Experience memory (v1.0.4)
    CASE = "case"
    EVALUATION = "evaluation"
    LESSON = "lesson"
    PLAYBOOK = "playbook"
    SKILL = "skill"

    # Entity-based types (v1.0.4)
    MESSAGE = "message"
    THREAD = "thread"
    EMAIL = "email"
    TICKET = "ticket"
    PULL_REQUEST = "pull_request"
    CAPABILITY = "capability"

    # Task management (v1.0.5)
    LABEL = "label"
    SECTION = "section"

    # Business knowledge — semantic memory (v1.0.6)
    DOMAIN = "domain"
    SYSTEM = "system"
    CONCEPT = "concept"
    PRACTICE = "practice"
    INSIGHT = "insight"
    PATTERN = "pattern"
    DATA_OBJECT = "data_object"
    RULE = "rule"

    # Event clock — episodic memory (v1.0.6)
    TRAJECTORY = "trajectory"
    DECISION_EVENT = "decision_event"
    OBSERVATION = "observation"
    SUMMARY = "summary"
```

### EdgeType

```python
class EdgeType(str, Enum):
    # Note relationships (v1.0.1)
    NOTE_LINKS_TO_NOTE = "note_links_to_note"
    NOTE_TAGGED_WITH_TAG = "note_tagged_with_tag"
    NOTE_HAS_TASK = "note_has_task"
    NOTE_MENTIONS_PERSON = "note_mentions_person"

    # Task relationships (v1.0.1)
    TASK_BELONGS_TO_PROJECT = "task_belongs_to_project"
    TASK_BLOCKED_BY_TASK = "task_blocked_by_task"
    TASK_ASSIGNED_TO_PERSON = "task_assigned_to_person"

    # Trace relationships (v1.0.1)
    TRACE_USED_CONTEXT = "trace_used_context"
    TRACE_PRODUCED_ARTIFACT = "trace_produced_artifact"

    # Calendar relationships (v1.0.1)
    CALENDAR_EVENT_RELATED_TO_NOTE = "calendar_event_related_to_note"
    CALENDAR_EVENT_RELATED_TO_TASK = "calendar_event_related_to_task"

    # Project relationships (v1.0.1)
    PROJECT_CONTAINS_NOTE = "project_contains_note"

    # Experience memory (v1.0.4)
    TRACE_HAS_CASE = "trace_has_case"
    TRACE_HAS_EVALUATION = "trace_has_evaluation"
    CASE_YIELDED_LESSON = "case_yielded_lesson"
    LESSON_APPLIES_TO_CAPABILITY = "lesson_applies_to_capability"
    LESSON_APPLIES_TO_WORKFLOW = "lesson_applies_to_workflow"
    LESSON_APPLIES_TO_ENTITY_TYPE = "lesson_applies_to_entity_type"
    PLAYBOOK_DERIVED_FROM_LESSON = "playbook_derived_from_lesson"
    PLAYBOOK_APPLIES_TO_WORKFLOW = "playbook_applies_to_workflow"

    # Cross-entity (v1.0.4)
    ENTITY_RELATED_TO_ENTITY = "entity_related_to"
    ENTITY_MENTIONS_ENTITY = "entity_mentions"

    # Task management (v1.0.5)
    TASK_HAS_LABEL = "task_has_label"
    TASK_IN_SECTION = "task_in_section"
    TASK_SUBTASK_OF = "task_subtask_of"
    TASK_CREATED_FROM = "task_created_from"
    PROJECT_HAS_SECTION = "project_has_section"
    TASK_SYNCED_TO = "task_synced_to"

    # Trajectory — event clock (v1.0.6)
    TRAJECTORY_TOUCHED = "trajectory_touched"
    TRAJECTORY_DECIDED = "trajectory_decided"
    TRAJECTORY_OBSERVED = "trajectory_observed"
    TRAJECTORY_PRODUCED = "trajectory_produced"
    DECISION_ABOUT = "decision_about"
    PRECEDED_BY = "preceded_by"
    SIMILAR_TO = "similar_to"

    # Knowledge — semantic memory (v1.0.6)
    DOMAIN_CONTAINS = "domain_contains"
    SYSTEM_INTEGRATES_WITH = "system_integrates_with"
    SYSTEM_HAS_DATA_OBJECT = "system_has_data_object"
    CONCEPT_RELATED_TO = "concept_related_to"
    INSIGHT_ABOUT = "insight_about"
    INSIGHT_DERIVED_FROM = "insight_derived_from"
    PATTERN_OBSERVED_IN = "pattern_observed_in"
    PRACTICE_USES = "practice_uses"
    RULE_CONSTRAINS = "rule_constrains"

    # Growth management (v1.0.6)
    SUMMARY_OF = "summary_of"
    SUPERSEDES = "supersedes"

    # Co-occurrence (v1.0.6)
    CO_OCCURS_WITH = "co_occurs_with"

    # Context curation (v1.1.4)
    EFFECTIVE_FOR = "effective_for"
```

### GraphNode

```python
class GraphNode(VersionedModel):
    node_id: str
    node_type: NodeType
    name: str
    properties: dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime
    # Provenance
    extracted_by: str | None = None  # e.g., "vault_indexer"
    source_ref: str | None = None
```

### GraphEdge

```python
class GraphEdge(VersionedModel):
    edge_id: str
    edge_type: EdgeType
    source_id: str
    target_id: str
    properties: dict[str, Any] = {}
    # Confidence for auto-extractions
    confidence: float | None = None  # 0.0-1.0
    # Validity interval
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    created_at: datetime
    # Provenance
    extracted_by: str | None = None
    source_ref: str | None = None
```

> **Rule:** Auto-generated edges should set `confidence`; human-authored edges may omit it.

---

## K) AgentProfile

**Purpose:** Strictly defines agent behavior without hard-coding. References a `thinking_policy` for reasoning tier control (v1.0.1).

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `agent_profile_id` | `str` | Yes | Unique identifier |
| `name` | `str` | Yes | Display name |
| `engine` | `str` | Yes | `custom`, `langgraph`, etc. |
| `llm_config` | `ModelConfig` | Yes | LLM settings |
| `prompt_config` | `PromptConfig` | No | Prompt serialization config |
| `allowed_capabilities` | `list[str]` | Yes | Capability allowlist |
| `context_policy` | `ContextPolicy` | Yes | Context limits/rules |
| `approval_policy` | `ApprovalPolicy` | Yes | Approval requirements |
| `output_schema_version` | `str` | Yes | Schema version |

### ModelConfig

```python
class ModelConfig(BaseModel):
    provider: str  # "openai", "anthropic", etc.
    model: str
    temperature: float = 0.3
    max_tokens: int = 4096
    stop_sequences: list[str] = []
```

### PromptConfig

```python
class PromptConfig(BaseModel):
    format: Literal["markdown", "json", "toon", "mixed"] = "markdown"
    enable_toon: bool = True
    fallback_format: Literal["markdown", "json"] = "markdown"
```

### ContextPolicy

```python
class ContextPolicy(BaseModel):
    max_tokens: int = 4000
    max_notes: int = 10
    max_tasks: int = 20
    max_events: int = 5
    must_cite: bool = True
    allowed_scopes: list[str] = []  # Project IDs, empty = all
    redaction_rules: list[str] = []
```

### ApprovalPolicy

```python
class ApprovalPolicy(BaseModel):
    require_approval_for: list[str] = []  # Capability names
    auto_approve_side_effects: list[SideEffect] = [SideEffect.NONE]
    max_auto_approve_risk: RiskLevel = RiskLevel.LOW
```

---

## L) CapabilityDef

**Purpose:** Defines a tool capability's schema and policies. The canonical source for trust boundary values.

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `capability_name` | `str` | Yes | e.g., `tasks.create@v1` |
| `description` | `str` | Yes | What the tool does |
| `input_schema` | `dict` | Yes | JSON Schema for args |
| `output_schema` | `dict` | Yes | JSON Schema for return |
| `side_effect_level` | `SideEffect` | Yes | Effect classification |
| `requires_approval_default` | `bool` | Yes | Default approval requirement |
| `timeout_ms` | `int` | Yes | Execution timeout |
| `rate_limit` | `RateLimit` | No | Rate limiting config |
| `redaction_policy` | `RedactionPolicy` | No | What to redact in logs |

### Example (YAML)

```yaml
# configs/capabilities/tasks.create@v1.yaml
capability_name: tasks.create@v1
description: Create a new task in the task management system
input_schema:
  type: object
  required: [title]
  properties:
    title:
      type: string
      maxLength: 200
    description:
      type: string
    due_date:
      type: string
      format: date
    priority:
      type: string
      enum: [low, medium, high]
    project_id:
      type: string
output_schema:
  type: object
  properties:
    task_id:
      type: string
    created_at:
      type: string
      format: date-time
side_effect_level: local
requires_approval_default: false
timeout_ms: 5000
```

### Legacy Config Aliases (v1.0.1)

The config loader supports legacy YAML field names with deprecation warnings:

| Legacy | Canonical |
|--------|-----------|
| `name` | `capability_name` |
| `side_effect` | `side_effect_level` |
| `requires_approval` | `requires_approval_default` |
| `adapter_type` | `adapter.type` |

---

## Schema Relationships

```
                    ┌─────────────────┐
                    │  AgentProfile   │
                    │  (config)       │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
     │ContextPolicy │ │ApprovalPolicy│ │ ModelConfig  │
     └──────────────┘ └──────────────┘ └──────────────┘
              │
              ▼
     ┌──────────────────┐
     │  ContextPacket   │ ◀── ContextAssembler
     │  (input)         │
     └────────┬─────────┘
              │
              ▼
     ┌──────────────────┐
     │      Plan        │ ◀── AgentEngine
     │  (agent output)  │
     └────────┬─────────┘
              │
    ┌─────────┴─────────┐
    │                   │
    ▼                   ▼
┌──────────┐    ┌──────────────┐
│ContextRef│    │ActionRequest │
│ (cited)  │    │ (to execute) │
└──────────┘    └──────┬───────┘
                       │
                       ▼
              ┌──────────────────┐
              │  ToolCallRecord  │ ◀── ToolBroker
              │  (executed)      │
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │  DecisionTrace   │ ◀── Complete audit
              │  (final record)  │
              └──────────────────┘
```

---

## JSON Schema Export

All schemas should be exportable as JSON Schema for:
- Capability input/output validation
- API documentation
- External tool integration

```python
from agent_kernel.core.schemas import Plan

# Export JSON Schema
schema = Plan.model_json_schema()
```

---

## M) Universal Entity Model (v1.0.4)

**Purpose:** Generalize context beyond notes to any source (Slack, email, etc.).

### EntityRef

```python
class EntityRef(VersionedModel):
    source_id: str      # "obsidian", "slack", "outlook", "github"
    entity_type: str    # "note", "message", "email", "event"
    entity_id: str      # stable ID within source
    uri: str | None     # canonical URI/path
    canonical_id: str | None  # kernel-owned global ID (ent_{ulid})
    canonical_hash: str | None
    occurred_at: datetime | None
    recorded_at: datetime | None
    metadata: dict[str, Any]
```

### EntityView

```python
class EntityViewType(str, Enum):
    SUMMARY = "summary"
    CHUNK = "chunk"
    TITLE = "title"
    THREAD_SUMMARY = "thread_summary"
    LESSON = "lesson"
    PLAYBOOK = "playbook"

class EntityView(VersionedModel):
    view_id: str
    entity: EntityRef
    view_type: EntityViewType
    segment_id: str | None
    content: str | None
    content_hash: str | None
```

### ContextRef Updates

`ContextRef` now includes optional entity fields for v1.0.4 compatibility:
- `entity: EntityRef | None` - Full entity reference
- `source_id`, `entity_type`, `entity_id` - Upcasted from RefType

---

## N) Experience Memory (v1.0.4)

**Purpose:** Learn from decisions and outcomes.

### OutcomeEvaluation

```python
class OutcomeLabel(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    REGRESSION = "regression"

class FailureCategory(str, Enum):
    MISRETRIEVAL = "misretrieval"
    MISPLANNING = "misplanning"
    TOOL_ERROR = "tool_error"
    HALLUCINATION = "hallucination"
    # ... others

class OutcomeEvaluation(VersionedModel):
    evaluation_id: str
    trace_id: str
    label: OutcomeLabel
    rating: int | None  # 1-5
    failure_category: FailureCategory | None
    feedback: str | None
    evaluator: str  # "user", "auto"
```

### ExperienceCase

```python
class ExperienceCase(VersionedModel):
    case_id: str
    trace_id: str
    intent: str
    context_summary: str | None
    plan_summary: str | None
    outcome_summary: str | None
    workflow_id: str | None
    capability_names: list[str]
    sources_used: list[str]
    entity_types_used: list[str]
    label: OutcomeLabel
```

### LessonLearned

```python
class LessonScope(BaseModel):
    workflow_id: str | None
    capability_name: str | None
    entity_type: str | None
    project_id: str | None

class LessonLearned(VersionedModel):
    lesson_id: str
    title: str
    lesson_text: str
    scope: LessonScope
    source_case_ids: list[str]
    confidence: float  # 0.0-1.0
    status: Literal["active", "deprecated", "candidate"]
```

### Playbook

```python
class Playbook(VersionedModel):
    playbook_id: str
    name: str
    selectors: list[PlaybookSelector]
    required_entity_types: list[str]
    required_sources: list[str]
    checklist: list[str]
    pitfalls: list[str]
    recommended_thinking_tier: int | None
    status: Literal["active", "deprecated", "candidate"]
```

---

## O) Skills (v1.1.2)

**Purpose:** Portable procedural guidance (SKILL.md + references), loaded as
optional context.

```python
class SkillOrigin(VersionedModel):
    kind: Literal["local", "git", "registry"]
    repo: str | None
    ref: str | None
    path: str | None
    installed_at: datetime
    content_hash: str

class SkillManifest(VersionedModel):
    skill_id: str
    name: str
    description: str
    license: str | None
    compatibility: str | None
    allowed_tools: list[str] | None
    metadata: dict[str, str]
    origin: SkillOrigin

class SkillResourceRef(VersionedModel):
    path: str
    kind: Literal["skill_md", "reference", "asset", "script"]
    hash: str
    bytes: int | None

class SkillLoadResult(VersionedModel):
    manifest: SkillManifest
    resources: list[SkillResourceRef]
    files: dict[str, str]
```

---

## P) Retention Policy (v1.0.4)

**Purpose:** Control data lifecycle and growth.

```python
class RetentionPolicy(BaseModel):
    traces: TraceRetentionPolicy
    vectors: VectorRetentionPolicy
    graph: GraphRetentionPolicy
    events: EventLogRetentionPolicy

class TraceRetentionPolicy(BaseModel):
    hot_days: int = 14      # Full traces
    warm_days: int = 90     # Compacted summaries
    cold_days: int = 365    # Cases/lessons only

class VectorRetentionPolicy(BaseModel):
    keep_summary_embeddings_days: int = 3650
    keep_chunk_embeddings_days: int = 180
    max_total_vectors: int = 500000
```

---

## Related Documents

- [00-overview.md](00-overview.md) - Design principles
- [02-memory.md](02-memory.md) - Memory subsystem
- [03-tools.md](03-tools.md) - Tool Broker
- [17-universal-context-system.md](17-universal-context-system.md) - Full v1.0.4 spec