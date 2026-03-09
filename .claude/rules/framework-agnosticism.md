---
paths:
  - "src/agent_kernel/engine/**"
  - "src/agent_kernel/workflows/**"
---

# Framework Agnosticism Rules

## Core Principle

**The kernel owns correctness, policy, tools, memory, and audit. Orchestration frameworks (LangGraph, etc.) are optional adapters that manage control flow only.**

## Hard Rules

### Kernel Primitives are Non-Negotiable

**NEVER delegate these to external frameworks:**

| Primitive | Rule |
|-----------|------|
| **Schemas** | Frameworks consume `ContextPacket`, `Plan`, `DecisionTrace`; never define their own |
| **Tool Broker** | The ONLY component allowed to execute tools; frameworks call broker |
| **Executor** | Validates plan, enforces policy, logs trace; frameworks never bypass |
| **Trace Store** | Canonical audit trail; frameworks emit metadata, kernel writes traces |
| **Context Assembler** | Builds context packets; frameworks request, never build themselves |

### Stable Interface Boundaries

**Hold these two interfaces stable to enable framework swapping:**

```python
# AgentEngine: plan generation
async def propose(context_packet, agent_profile) -> Plan

# WorkflowRunner: workflow execution
async def run(workflow_id, intent, **kwargs) -> WorkflowResult
```

**Implementations are pluggable:**
- `CustomEngine` / `LangGraphPlanningEngine` / `SemanticKernelEngine`
- `LocalWorkflowRunner` / `LangGraphWorkflowRunner`

## Decision Framework

### Start WITHOUT orchestration frameworks when:
- Still stabilizing schemas and tool contracts
- Flows are linear: assemble -> plan -> validate -> execute -> log
- Want maximal control and minimal dependencies

### ADD LangGraph (or similar) when:
- 3-5+ workflows with branching, loops, or retries
- Multi-agent coordination (planner <-> critic <-> executor)
- Long-running checkpointed state across sessions
- Need graph visualization for debugging complex flows

## Integration Checklist

Before adding any orchestration framework, verify:

- [ ] Framework consumes our schemas (not its own)
- [ ] Framework calls Tool Broker (not tools directly)
- [ ] Executor validates and executes all plans
- [ ] TraceStore writes the canonical trace
- [ ] Workflow definitions stay in our YAML spec
- [ ] Framework can be swapped without changing business logic

If any answer is "no", refactor the integration.

## Summary

1. **Kernel = Source of Truth** -- Frameworks are adapters, not owners
2. **Schemas Cross Boundaries** -- All data flows through Pydantic models
3. **Tool Broker is Sacred** -- No tool execution outside the broker
4. **Traces are Portable** -- Never depend on framework-specific logging
5. **Start Simple** -- Add orchestration complexity only when proven needed
6. **Compile, Don't Couple** -- Workflow specs compile to runners, not vice versa

**Reference:** See `docs/design/10-framework-agnosticism.md` for detailed patterns and examples.
