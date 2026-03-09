# Deterministic Executor

The Executor is the enforcement layer between agent reasoning and tool execution. It is **deterministic** -- it makes no LLM calls. Its job is to validate plans, enforce policies, execute actions through the Tool Broker, and produce immutable `DecisionTrace` records.

## Separation of Reasoning and Execution

| Phase | Responsibility | Deterministic? |
|-------|---------------|----------------|
| **Reasoning** | LLM produces a structured `Plan` | No (LLM output) |
| **Execution** | Executor validates and runs tools | Yes (code logic) |

This separation ensures:

- Plans can be inspected before any side effects occur
- Approval gates can review proposed actions
- Rollback is possible because plans are data, not side effects
- The audit trail captures both what was proposed and what actually happened

## Plan Validation Pipeline

Before executing any action, the executor validates:

1. **Schema validation** -- the `Plan` must conform to its Pydantic schema
2. **Citation check** -- if the agent profile requires citations (`must_cite: true`), the plan must reference context sources
3. **Allowlist check** -- every action must use a capability from the agent profile's `allowed_capabilities` list
4. **Idempotency check** -- write actions must include an `idempotency_key`

If validation fails, the executor returns a failed trace with detailed error messages -- no actions are executed.

## Approval Gating

After validation, the executor checks each action's approval requirements:

```python
# Effective approval is computed from:
# 1. CapabilityDef.requires_approval_default
# 2. AgentProfile.approval_policy
# 3. Side effect level

# If approval is required but no token provided:
#   -> trace status = NEEDS_APPROVAL
#   -> pending ApprovalRequest created
```

When an action requires approval:

1. An `ApprovalRequest` is persisted (survives restarts)
2. The workflow pauses with status `WAITING_APPROVAL`
3. A human approves or denies via CLI or API
4. The workflow resumes from its checkpoint

## Execution

For each approved action, the executor:

1. Sends the action to the Tool Broker
2. Receives a `ToolCallRecord` with status, timing, and output
3. Collects artifacts from successful calls
4. Stops on error if configured to halt on failure

## Outcome Determination

After all actions execute, the outcome is determined:

| Condition | Outcome |
|-----------|---------|
| All actions succeeded | `completed` |
| All actions failed or denied | `failed` |
| Mix of success and failure | `partial` |
| Actions pending approval | `needs_approval` |

## Code Example

```python
from agent_kernel import DeterministicExecutor, ToolBroker, CapabilityRegistry
from agent_kernel import SQLiteEventLog
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink

# Create executor with stores
executor = DeterministicExecutor(
    tool_broker=ToolBroker(registry=CapabilityRegistry()),
    trace_store=SQLiteTraceSink(":memory:"),
    event_log=SQLiteEventLog(":memory:"),
)

# Execute a plan (after engine generates it)
trace = await executor.execute(
    plan=plan,
    context_packet=context_packet,
    agent_profile=profile,
)

# The trace contains the complete audit trail
print(f"Status: {trace.outcome.status.value}")
print(f"Tool calls: {len(trace.tool_calls)}")
for tc in trace.tool_calls:
    print(f"  {tc.capability_name}: {tc.status.value} ({tc.duration_ms}ms)")
```

## Workflow Runner

The `WorkflowRunner` orchestrates the full lifecycle:

1. **Trigger** -- cron schedule, file change, manual, or event
2. **Assemble context** -- query memory stores, build `ContextPacket`
3. **Propose plan** -- agent engine generates a `Plan`
4. **Validate** -- executor checks schema, citations, allowlists
5. **Gate approvals** -- block if approval needed
6. **Execute** -- run actions through Tool Broker
7. **Write back** -- update memory with results
8. **Emit trace** -- persist the complete `DecisionTrace`

Workflow runs persist to SQLite, with step checkpoints for resumption after approval or failure.

## Next Steps

- [Context Assembler](context-assembler.md) -- how context packets are built
- [Trust Boundaries](../guides/trust-boundaries.md) -- the trust model between agents and the executor
- [Tracing](tracing.md) -- how traces are stored and queried
