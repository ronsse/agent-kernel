# Personal Assistant Workflow

Demonstrates a multi-step workflow with memory stores, tool capabilities,
and an approval gate demo.

## What It Demonstrates

- Setting up the full memory stack (document store, graph store, event log)
- Registering tool capabilities with stub handlers
- Multi-action plans with read-only and write side effects
- The approval flow: pending approval created, then resolved

## Key Concepts

| Concept | Description |
|---------|-------------|
| `CapabilityDef` | Defines a tool's schema, side effects, and policies |
| `ToolBroker` | Central gateway that enforces policies and logs execution |
| `ApprovalGate` | Manages pending approvals for gated actions |
| `SideEffect` | Classification of what an action does (none, read, local, external) |

## How It Works

### 1. Register Capabilities

Each tool gets a `CapabilityDef` with its schema and side effect level:

```python
registry.register(CapabilityDef(
    capability_name="tasks.list@v1",
    description="List open tasks",
    input_schema={"type": "object", "properties": {"status": {"type": "string"}}},
    output_schema={},
    side_effect_level=SideEffect.NONE,  # Read-only
))

registry.register(CapabilityDef(
    capability_name="summary.create@v1",
    description="Create a daily summary",
    input_schema={"type": "object", "properties": {"scope": {"type": "string"}}},
    output_schema={},
    side_effect_level=SideEffect.LOCAL_WRITE,  # Writes locally
))
```

### 2. Register Stub Handlers

```python
broker.local_adapter.register("tasks.list@v1", list_tasks)
broker.local_adapter.register("summary.create@v1", create_summary)
```

### 3. Create a Multi-Action Plan

The stub engine returns a plan with two actions:

```python
actions=[
    ActionRequest(
        capability_name="tasks.list@v1",
        args={"status": "open"},
        side_effect=SideEffect.NONE,
    ),
    ActionRequest(
        capability_name="summary.create@v1",
        args={"scope": "daily"},
        side_effect=SideEffect.LOCAL_WRITE,
        idempotency_key="summary_001",  # Required for writes
    ),
]
```

### 4. Approval Demo

The example also shows the approval flow:

```python
gate = ApprovalGate()
pending = gate.request_approval(
    action_id="demo_action",
    capability_name="notification.send@v1",
    args={"channel": "email", "message": "Hello"},
    trace_id=trace.trace_id,
    agent_profile_id="assistant",
)
gate.approve(pending.approval_id, approved_by="user", reason="Looks good")
```

## Configuration Files

### `configs/agent_profile.yaml`

Defines the agent's allowed capabilities and approval policy:

```yaml
agent_profile_id: assistant
name: Personal Assistant
allowed_capabilities:
  - tasks.list@v1
  - summary.create@v1
approval_policy:
  auto_approve_side_effects: [none, read, local]
```

### `configs/workflow.yaml`

Defines the workflow steps:

```yaml
workflow_id: daily_review
name: Daily Review
trigger:
  type: manual
steps:
  - assemble_context
  - propose_plan
  - validate
  - gate_approvals
  - execute
  - emit_trace
```

## Expected Output

```
=== Personal Assistant Example ===
Registered capabilities: tasks.list@v1, summary.create@v1
Plan has 2 actions:
  1. tasks.list@v1 (side_effect=none)
  2. summary.create@v1 (side_effect=local, idempotency_key=summary_001)

--- Executing plan ---
Trace ID: 01KK...
Outcome:  completed
Tool calls:
  tasks.list@v1 -> success (1ms)
  summary.create@v1 -> success (0ms)

--- Approval demo ---
Created pending approval: 01KK...
Approved! Token: <token>
```

## What to Explore Next

- [Multi-Agent Debate](multi-agent-debate.md) --- compare plans from multiple engines
- [Tool Workflow](tool-workflow.md) --- approval gates enforced by the executor
