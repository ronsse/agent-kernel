# Schema Contracts

Every meaningful piece of data in Agent Kernel flows through a typed Pydantic model. These schemas are the contracts between components -- they define what data looks like, what fields are required, and how components communicate.

## The Data Flow

```
Intent --> ContextPacket --> Plan --> ToolCallRecord --> DecisionTrace
```

Each arrow represents a schema boundary. No unstructured data crosses these boundaries.

## Key Schemas

### ContextRef

A reference to any source item the agent used -- a document, task, event, graph node, or external resource.

```python
from agent_kernel.core.schemas import ContextRef, RefType

ref = ContextRef(
    ref_type=RefType.DOCUMENT,
    ref_id="doc_01HXYZ123ABC",
    uri="https://example.com/doc/123",
    hash="sha256:a1b2c3...",
    metadata={"title": "Design Overview", "tags": ["architecture"]},
)
```

| Field | Type | Description |
|-------|------|-------------|
| `ref_type` | `RefType` | Type of reference (note, task, event, doc, etc.) |
| `ref_id` | `str` | Stable unique identifier |
| `uri` | `str` (optional) | File path, URL, or link |
| `hash` | `str` (optional) | Content hash for reproducibility |
| `metadata` | `dict` (optional) | Title, timestamps, tags |

### ContextPacket

The bounded input an agent receives. Deterministically assembled from memory stores.

```python
from agent_kernel.core.schemas import (
    ContextPacket, ContextBudget, RetrievalLimits, RetrievalReport,
)

packet = ContextPacket(
    intent="Summarize recent project activity",
    budget=ContextBudget(
        max_tokens=4000,
        max_items=30,
        retrieval_limits=RetrievalLimits(max_notes=10, max_tasks=20),
    ),
    items=[],  # Populated by ContextAssembler
    retrieval_report=RetrievalReport(
        queries_run=[], filters_applied=[],
        items_considered=0, items_selected=0,
        selection_strategy="relevance_ranked",
    ),
)
```

Key fields: `intent`, `budget` (token and item limits), `items` (retrieved context), and `retrieval_report` (debugging info on what was retrieved and why).

### Plan

The structured output from an agent engine. Every plan must cite its sources and declare risk.

```python
from agent_kernel.core.schemas import (
    Plan, RiskAssessment, PlanValidation, ActionRequest, SideEffect,
)

plan = Plan(
    intent="Create a summary document",
    summary="Will create a summary based on 3 retrieved documents.",
    context_refs_used=[ref],  # Must cite sources
    actions=[
        ActionRequest(
            capability_name="documents.create@v1",
            args={"title": "Summary", "content": "..."},
            side_effect=SideEffect.LOCAL_WRITE,
            requires_approval=False,
            evidence_refs=["doc_01HXYZ123ABC"],
            idempotency_key="create_summary_001",
        ),
    ],
    risk=RiskAssessment(level="low", reasons=["Local write only"]),
    validation=PlanValidation(assumptions=["Documents are up to date"]),
)
```

Key fields: `actions` (what to do), `context_refs_used` (cited sources), `risk` (assessment), and `validation` (self-check).

### ActionRequest

A tool-like action to be executed. Each action references a registered capability and includes evidence refs linking back to context.

| Field | Type | Description |
|-------|------|-------------|
| `capability_name` | `str` | Registered capability (e.g., `tasks.create@v1`) |
| `args` | `dict` | Arguments validated against the capability schema |
| `side_effect` | `SideEffect` | `none`, `local`, or `external` (agent hint) |
| `requires_approval` | `bool` | Whether approval is needed (agent hint) |
| `evidence_refs` | `list[str]` | Context refs supporting this action |
| `idempotency_key` | `str` | Required for write operations |

!!! note "Trust Boundary"
    The `side_effect` and `requires_approval` fields on `ActionRequest` are **agent hints only**. The executor computes authoritative values from `CapabilityDef` and `AgentProfile`. See [Trust Boundaries](../guides/trust-boundaries.md).

### ToolCallRecord

An immutable record of what actually ran. Created by the Tool Broker after executing an action.

| Field | Type | Description |
|-------|------|-------------|
| `capability_name` | `str` | What capability was called |
| `started_at` / `ended_at` | `datetime` | Execution timing |
| `duration_ms` | `int` | How long it took |
| `input` / `output` | `dict` | Arguments and results (redacted as needed) |
| `status` | `CallStatus` | `success`, `error`, `denied`, `skipped`, `timeout` |
| `effective_side_effect` | `SideEffect` | System-computed side effect level |
| `effective_requires_approval` | `bool` | System-computed approval requirement |

### DecisionTrace

The complete auditable unit of work. Every workflow step produces one trace.

```python
# After execution, inspect the trace
print(f"Trace:   {trace.trace_id}")
print(f"Intent:  {trace.intent}")
print(f"Outcome: {trace.outcome.status.value}")
print(f"Actions: {len(trace.tool_calls)} tool calls")
print(f"Plan:    {trace.plan.summary}")
```

A `DecisionTrace` contains: the original intent, the context packet used, the complete plan with citations, all tool call records, approval records, the outcome (completed/partial/failed), and provenance information (config hash, engine version, kernel version).

## Schema Versioning

All persisted schemas include `schema_version` and `kernel_version` fields. Migrations are handled by an upcaster registry:

- **Additive changes only** within minor versions (new optional fields)
- **Breaking changes** require a new major version with explicit upcasters
- Upcasters run on load, before Pydantic validation

## Next Steps

- [Tool Broker](tool-broker.md) -- how actions are executed through the governance layer
- [Executor](executor.md) -- how plans are validated and run
- [Tracing](tracing.md) -- how traces are stored and queried
