# Framework Agnosticism

> **Core Principle:** The kernel owns correctness, policy, tools, memory, and audit. Orchestration frameworks (LangGraph, Semantic Kernel, etc.) are optional adapters that manage control flow only.

## Non-Negotiable Kernel Primitives

These components are **never delegated** to external frameworks:

| Component | Responsibility | Framework Role |
|-----------|---------------|----------------|
| **Schemas** | `ContextPacket`, `Plan`, `DecisionTrace`, `ToolCallRecord`, `AgentProfile` | Frameworks consume these; never define their own |
| **Tool Broker** | The ONLY component allowed to execute tools | Frameworks call the broker; never call tools directly |
| **Deterministic Executor** | Validates plan, enforces policy, runs broker, logs trace | Frameworks trigger the executor; never bypass it |
| **Trace Store** | Canonical audit trail (local DB + optional remote) | Frameworks may emit metadata; kernel writes traces |
| **Context Assembler** | Deterministic retrieval rules that build context packets | Frameworks request context; never build it themselves |

## Stable Interfaces

### AgentEngine Interface

The engine interface is intentionally thin:

```python
class AgentEngine(Protocol):
    """Thin interface for plan generation."""

    engine_id: str

    async def propose(
        self,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
    ) -> Plan:
        """Generate a plan from context. MUST NOT call tools."""
```

Implementations include:

- `CustomEngine` -- direct LLM calls
- A future `LangGraphPlanningEngine` -- graph-based planning
- A future `SemanticKernelEngine` -- Microsoft SK adapter

### WorkflowRunner Interface

```python
class WorkflowRunner(Protocol):
    async def run(
        self,
        workflow_id: str,
        intent: str | None = None,
        **kwargs,
    ) -> WorkflowResult:
        """Execute workflow. Returns trace."""
```

Implementations can be a simple Python runner or a LangGraph-based orchestrator.

## When to Use Each Approach

### Use the Local Runner When

- Flows are linear: assemble -> plan -> validate -> execute -> log
- Still stabilizing schemas and tool contracts
- Want maximum control and minimal dependencies
- Debugging or iterating on core logic

### Add a Framework (e.g., LangGraph) When

- 3-5+ workflows with complex branching logic
- Conditional paths: "if risk high -> request approval -> else execute"
- Iteration loops: retry until quality gates pass
- Multi-agent coordination: planner <-> critic <-> executor
- Long-running: checkpointed state across sessions
- Need built-in graph visualization for debugging

## What Frameworks Do NOT Replace

Even with an orchestration framework, you still own:

| Concern | Why the Kernel Owns It |
|---------|----------------------|
| **Memory model** | Context graph, vector store, and event log are your data |
| **Tool governance** | Allowlists, approval gates, rate limits, idempotency |
| **Trace / audit** | Portability requires your own canonical trace store |
| **Schema contracts** | Pydantic models are the API between all components |

## Integration Pattern

When adding a framework, each node wraps a kernel function:

```
LangGraph Node         Kernel Function
--------------         ---------------
assemble_context  -->  ContextAssembler.assemble()
propose_plan      -->  AgentEngine.propose()
quality_gates     -->  Executor.validate_plan()
execute           -->  Executor.execute() -> ToolBroker
trace             -->  TraceStore.write()
```

**Rule:** The framework manages control flow; the kernel manages correctness.

## Workflow Spec -> Runner Compilation

To stay framework-swappable:

1. **Define workflows in your own declarative spec** (YAML)
2. **Implement multiple backends:**
    - `LocalRunnerBackend` -- simple Python control flow
    - `LangGraphBackend` -- compiles spec into a LangGraph graph

This means:

- Workflow definitions are stable across backend changes
- You can A/B test runners without refactoring
- Business logic never touches framework internals

## Swappability Checklist

Before adding any orchestration framework, verify:

- [ ] Does it consume our schemas (not define its own)?
- [ ] Does it call Tool Broker (not tools directly)?
- [ ] Does it let the Executor validate and execute plans?
- [ ] Does it let TraceStore write the canonical trace?
- [ ] Can we swap it out without changing business logic?
- [ ] Are workflow definitions in our YAML spec (not framework-specific)?

If any answer is "no", refactor the integration.

## Summary

1. **Kernel = Source of Truth** -- frameworks are adapters, not owners
2. **Schemas Cross Boundaries** -- all data flows through Pydantic models
3. **Tool Broker is Sacred** -- no tool execution outside the broker
4. **Traces are Portable** -- never depend on framework-specific logging
5. **Start Simple** -- add orchestration complexity only when proven needed
6. **Compile, Don't Couple** -- workflow specs compile to runners, not vice versa

## Next Steps

- [Architecture Guide](architecture.md) -- system overview and data flow
- [Trust Boundaries](trust-boundaries.md) -- how policies are enforced regardless of framework
