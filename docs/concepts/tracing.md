# Tracing

Comprehensive trace logging is non-negotiable in Agent Kernel. Every workflow step produces an immutable `DecisionTrace` -- the complete audit record of what happened, why, and with what result.

## What Gets Captured

Every `DecisionTrace` includes:

| Category | Fields |
|----------|--------|
| **Identity** | `trace_id`, `run_id`, `workflow_id`, `agent_profile_id`, `engine_id` |
| **Input** | `intent`, `context_packet_id`, `context_refs_used` |
| **Reasoning** | `plan` (complete plan with summary, actions, citations, risk) |
| **Execution** | `tool_calls[]` (each with timing, I/O, status) |
| **LLM calls** | `llm_calls[]` (model, tokens, latency, stage) |
| **Governance** | `approvals[]` (approval/denial records) |
| **Result** | `outcome` (status, artifacts created) |
| **Provenance** | `config_hash`, `engine_version`, `kernel_version`, `prompt_hash` |

## DecisionTrace Anatomy

```python
trace = await executor.execute(plan=plan, context_packet=packet, agent_profile=profile)

# Identity
print(f"Trace: {trace.trace_id}")
print(f"Run:   {trace.run_id}")

# What was planned
print(f"Intent: {trace.intent}")
print(f"Plan:   {trace.plan.summary}")
print(f"Risk:   {trace.plan.risk.level.value}")

# What actually happened
for tc in trace.tool_calls:
    print(f"  Tool: {tc.capability_name}")
    print(f"    Status:   {tc.status.value}")
    print(f"    Duration: {tc.duration_ms}ms")
    print(f"    Side effect: {tc.effective_side_effect.value}")

# Final result
print(f"Outcome: {trace.outcome.status.value}")
print(f"Artifacts: {len(trace.outcome.artifacts)}")
```

## Trace Sinks

Traces are written to one or more **sinks** -- pluggable storage backends:

| Sink | Description |
|------|-------------|
| **SQLite** | Primary storage. Queryable, durable, local-first. |
| **JSONL** | Append-only log file. Good for backup and export. |
| **HTTP** (future) | Forward traces to external observability systems. |

The `MultiSinkTraceStore` writes to all configured sinks simultaneously:

```python
from agent_kernel.tracing import MultiSinkTraceStore
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink
from agent_kernel.tracing.sinks.jsonl_sink import JSONLTraceSink

store = MultiSinkTraceStore(sinks=[
    SQLiteTraceSink("data/traces/traces.db"),
    JSONLTraceSink("data/traces/traces.jsonl"),
])
```

## Querying Traces

The SQLite sink supports querying by time range, agent, workflow, and status:

```python
# Recent traces
traces = trace_store.query(limit=20)

# Filter by workflow
traces = trace_store.query(workflow_id="daily_review", limit=10)

# Filter by outcome
traces = trace_store.query(status="completed", limit=50)
```

## LLM Call Records

Every LLM interaction is captured as an `LLMCallRecord` within the trace:

```python
for llm_call in trace.llm_calls:
    print(f"  Stage: {llm_call.stage}")          # routing, propose_plan, critic, revise
    print(f"  Model: {llm_call.request.model}")
    print(f"  Tokens: {llm_call.response.usage.total_tokens}")
    print(f"  Latency: {llm_call.duration_ms}ms")
```

This enables cost tracking, latency analysis, and model comparison across runs.

## Provenance

Every trace includes provenance information for reproducibility:

- **config_hash** -- hash of the agent profile configuration
- **engine_version** -- which engine version produced the plan
- **kernel_version** -- which kernel version ran the execution
- **prompt_hash** -- hash of the prompt template used
- **prompt_parts** -- individual prompt component hashes

This means you can always trace back exactly which configuration, code version, and prompts produced a given result.

## Schema Versioning

Traces include `schema_version` and `kernel_version` fields. When the trace schema evolves, upcasters automatically migrate older traces on load:

```python
# Traces from older versions are automatically migrated
trace = trace_store.get("01HXYZ...")  # v1.0.0 trace loaded as v1.0.1
```

## Next Steps

- [Schema Contracts](schemas.md) -- the trace data model in detail
- [Architecture Guide](../guides/architecture.md) -- how tracing fits into the overall system
- [Thinking Escalation](../guides/thinking-escalation.md) -- how reasoning metadata is captured in traces
