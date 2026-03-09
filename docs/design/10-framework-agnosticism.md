# Framework Agnosticism Guidelines

> **Core Principle:** The kernel owns correctness, policy, tools, memory, and audit. Orchestration frameworks (LangGraph, etc.) are optional adapters that manage control flow only.

---

## Non-Negotiable Kernel Primitives

These components are **never delegated** to external frameworks:

| Component | Responsibility | Framework Role |
|-----------|---------------|----------------|
| **Schemas** | `ContextPacket`, `Plan`, `DecisionTrace`, `ToolCallRecord`, `AgentProfile` | Frameworks consume these; never define their own |
| **Tool Broker** | The ONLY component allowed to execute tools | Frameworks call broker; never call tools directly |
| **Deterministic Executor** | Validates plan, enforces policy, runs broker, logs trace | Frameworks trigger executor; never bypass it |
| **Trace Store** | Canonical audit trail (local DB + optional remote) | Frameworks may emit metadata; kernel writes traces |
| **Context Assembler** | Deterministic retrieval rules that build context packets | Frameworks request context; never build it themselves |

---

## Stable Interface Boundaries

### AgentEngine Interface (STABLE)

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

**Implementations:**
- `CustomEngine` - Direct LLM calls (current)
- `LangGraphPlanningEngine` - Graph-based planning (future)
- `SemanticKernelEngine` - Microsoft SK adapter (future)

### WorkflowRunner Interface (STABLE)

```python
class WorkflowRunner(Protocol):
    """Thin interface for workflow execution."""
    
    async def run(
        self,
        workflow_id: str,
        intent: str | None = None,
        **kwargs,
    ) -> WorkflowResult:
        """Execute workflow. Returns trace."""
```

**Implementations:**
- `LocalWorkflowRunner` - Simple Python control flow (current)
- `LangGraphWorkflowRunner` - Graph-based orchestration (future)

---

## When to Use Each Approach

### Use Simple Local Runner When:
- ✅ Flows are linear: assemble → plan → validate → execute → log
- ✅ Still stabilizing schemas and tool contracts
- ✅ Want maximal control and minimal dependencies
- ✅ Debugging/iterating on core logic

### Add LangGraph When:
- ⚡ 3–5+ workflows with complex control flow
- ⚡ Branching: "if risk high → request approval → else execute"
- ⚡ Loops: iterate until gates pass / context is sufficient
- ⚡ Multi-agent: planner ↔ critic ↔ executor coordination
- ⚡ Long-running: checkpointed state across sessions
- ⚡ Need built-in graph visualization for debugging

---

## What LangGraph Does NOT Replace

Even with LangGraph, you STILL own:

| Concern | Why Kernel Owns It |
|---------|-------------------|
| **Memory model** | Context graph + vector store + event log are your data |
| **Tool governance** | Allowlists, approval gates, rate limits, idempotency |
| **Trace/audit** | Portability requires your own canonical trace store |
| **Schema contracts** | Pydantic models are the API between all components |

---

## LangGraph Integration Pattern (When Ready)

Each LangGraph node is a **thin wrapper** around kernel functions:

```
┌─────────────────────────────────────────────────────────────┐
│                    LangGraph Graph                          │
├─────────────────────────────────────────────────────────────┤
│  assemble_context ──→ ContextAssembler.assemble()           │
│         │                                                   │
│         ▼                                                   │
│    propose_plan ────→ AgentEngine.propose()                 │
│         │                                                   │
│         ▼                                                   │
│   quality_gates ────→ Executor.validate_plan()              │
│         │                                                   │
│    ┌────┴────┐                                              │
│    ▼         ▼                                              │
│  approve   execute ─→ Executor.execute() → ToolBroker       │
│    │         │                                              │
│    └────┬────┘                                              │
│         ▼                                                   │
│      trace ─────────→ TraceStore.write()                    │
└─────────────────────────────────────────────────────────────┘
```

**Rule:** LangGraph manages control flow; Kernel manages correctness.

---

## Framework Swappability Checklist

Before adding any orchestration framework:

- [ ] Does it consume our schemas (not define its own)?
- [ ] Does it call Tool Broker (not tools directly)?
- [ ] Does it let Executor validate and execute plans?
- [ ] Does it let TraceStore write the canonical trace?
- [ ] Can we swap it out without changing business logic?
- [ ] Are workflow definitions in our YAML spec (not framework-specific)?

If any answer is "no", refactor the integration.

---

## Workflow Spec → Runner Compilation

To stay framework-swappable:

1. **Define workflows in our own declarative spec** (YAML)
2. **Implement multiple backends:**
   - `LocalRunnerBackend` - Simple Python control flow
   - `LangGraphBackend` - Compiles spec into LangGraph graph

This means:
- Workflow definitions are stable
- Can A/B test runners without refactoring
- Business logic never touches framework internals

---

## Summary Rules

1. **Kernel = Source of Truth** - Frameworks are adapters, not owners
2. **Schemas Cross Boundaries** - All data flows through Pydantic models
3. **Tool Broker is Sacred** - No tool execution outside the broker
4. **Traces are Portable** - Never depend on framework-specific logging
5. **Start Simple** - Add orchestration complexity only when proven needed
6. **Compile, Don't Couple** - Workflow specs compile to runners, not vice versa
