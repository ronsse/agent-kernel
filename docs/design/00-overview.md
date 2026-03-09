# Design Overview

**Version:** 1.0.5
**Status:** Implementation Phase

---

## Version History

| Version | Description |
|---------|-------------|
| 1.0.0 | Initial kernel design |
| 1.0.1 | Schema versioning, LLM call tracing, graph ontology |
| 1.0.2 | Context packs, source descriptors, retrieval planning |
| 1.0.3 | Thinking policy, hierarchical embeddings, hybrid search |
| 1.0.4 | Universal Entity Model, Experience Memory, Retention |
| 1.0.5 | **Workflow triggers and on_complete chaining** |
| 1.2.0 | **LLM semantic cache, adaptive timeouts, success rate routing, cost anomaly detection, thinking metrics** |

---

## Goal Constraints

### Constraints

| Constraint | Description |
|------------|-------------|
| **Local-first** | Runs locally (at least initially) |
| **Trace logging** | Complete audit trail of all decisions |
| **Multi-agent orchestration** | Support for multiple agent profiles |
| **No direct MCP** | Tool Broker/Gateway API instead (MCP adapter later) |

### Trace Logging Requirements

Every run must capture:

- **Agent decisions**: Plans, reasoning summaries, citations
- **Tool calls**: Inputs/outputs, timing, errors
- **Context used**: What memory was retrieved and why
- **Approvals/denials**: Human-in-the-loop decisions

---

## Core Design Principles

### 1. Schemas are the Contract

Everything meaningful flows through typed Pydantic models. No unstructured data between components.

```
Intent → ContextPacket → Plan → ToolCallRecord → DecisionTrace
```

**Schema Evolution (v1.0.1):**
- All persisted schemas include `schema_version` and `kernel_version`
- Migrations handled by upcaster registry
- Additive changes only within minor versions

### 2. Separation of Reasoning vs. Execution

| Phase | Responsibility | Deterministic? |
|-------|---------------|----------------|
| **Reasoning** | LLM produces structured `Plan` | No (LLM output) |
| **Execution** | Executor validates and runs tools | Yes (code logic) |

This separation ensures:
- Plans can be validated before execution
- Approvals gate can inspect proposed actions
- Rollback is possible (plans are data, not side effects)

**Trust Boundary (v1.0.1):**
- Agent-provided `side_effect` and `requires_approval` are hints only
- Executor computes effective values from `CapabilityDef` + `AgentProfile`
- Both requested and effective values recorded in traces

### 3. Kernel Owns Memory + Traces

Agent frameworks (LangGraph, Semantic Kernel, etc.) must NOT be the source of truth.

| What | Owner |
|------|-------|
| Memory (docs, vectors, graph) | Kernel |
| Traces and event log | Kernel |
| Tool execution | Kernel (via Broker) |
| Plan generation | Agent Engine (pluggable) |
| Entity registry (v1.0.4) | Kernel |
| Experience memory (v1.0.4) | Kernel |
| Retention policy (v1.0.4) | Kernel |

### 4. Adapters Everywhere

Every external dependency should be behind an interface:

```
┌─────────────────────────────────────────────────────────────┐
│                     AGENT KERNEL                             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Agent Engine Interface                                      │
│  ├── CustomEngine                                            │
│  ├── LangGraphAdapter                                        │
│  └── SemanticKernelAdapter                                   │
│                                                              │
│  Vector Store Interface                                      │
│  ├── ChromaStore                                             │
│  ├── PgVectorStore                                           │
│  └── QdrantStore                                             │
│                                                              │
│  Graph Store Interface                                       │
│  ├── SQLiteGraph                                             │
│  └── Neo4jAdapter                                            │
│                                                              │
│  Tool Adapter Interface                                      │
│  ├── LocalFunctionAdapter                                    │
│  ├── HTTPAdapter                                             │
│  ├── SubprocessAdapter                                       │
│  └── MCPAdapter (future)                                     │
│                                                              │
│  Trace Sink Interface                                        │
│  ├── SQLiteSink                                              │
│  ├── JSONLSink                                               │
│  └── HTTPSink (future)                                       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## System Components

### 1. Kernel (Framework-Agnostic Core)

**This is what you build first.** It should run even if agents are stubbed.

| # | Component | Description |
|---|-----------|-------------|
| 1 | Identity & IDs | ULID generation, stable identifiers |
| 2 | Schema definitions | Pydantic models for all contracts |
| 3 | Memory subsystem | Document, vector, graph, event stores |
| 4 | Tool Broker | Capability registry + execution gateway |
| 5 | Context Assembler | Deterministic retrieval → ContextPacket |
| 6 | Deterministic Executor | Validate plan → execute → trace |
| 7 | Workflow Runner | State machine for multi-step flows |
| 8 | Scheduler / Triggers | Cron, file watch, events |
| 9 | Observability | Trace store and sinks |
| 10 | Config / Policy | YAML-driven configuration |
| 11 | CLI | Command-line interface |
| 12 | Entity Registry (v1.0.4) | Universal entity mapping + views |
| 13 | Experience Memory (v1.0.4) | Cases, lessons, playbooks |
| 14 | Retention Jobs (v1.0.4) | Trace compaction, vector pruning |
| 15 | LLM Semantic Cache (v1.2) | Tier-aware response caching with TTL |
| 16 | Adaptive Timeout Manager (v1.2) | Per-capability P99-based timeout tuning |
| 17 | Success Rate Router (v1.2) | Model routing by historical success rate |
| 18 | Cost Anomaly Detector (v1.2) | Rolling cost spike detection with alerts |
| 19 | Thinking Metrics (v1.2) | Aggregate escalation, tier, and cost analysis |

### 2. Agent Engines (Pluggable)

An "Agent Engine" is anything that can turn:

```
ContextPacket + AgentProfile → Plan
```

Examples:
- Your own `CustomEngine` implementation
- A LangGraph-based planner
- A Semantic Kernel planner
- A future "multi-agent debate" planner

**The kernel should not care which engine produced the plan**, as long as it validates against schema.

### 3. Tool Adapters (Pluggable)

Tools can be implemented behind the broker as:

| Adapter | Description |
|---------|-------------|
| Local Python function | Direct function call |
| Subprocess CLI | Shell command execution |
| HTTP/REST | REST API calls to services |
| MCP (future) | Model Context Protocol adapter |

---

## Data Flow

### Single Workflow Execution

```
┌─────────────────────────────────────────────────────────────────┐
│                        WORKFLOW RUNNER                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. TRIGGER                                                      │
│     └── Cron schedule, file change, manual, event, or workflow  │
│                                                                  │
│  2. ASSEMBLE CONTEXT                                             │
│     ├── Query memory stores (docs, vectors, graph)              │
│     ├── Apply context policy (limits, filters)                  │
│     └── Output: ContextPacket                                   │
│                                                                  │
│  3. PROPOSE PLAN                                                 │
│     ├── Select Agent Engine (per AgentProfile.engine)           │
│     ├── Engine receives ContextPacket                           │
│     └── Output: Plan (structured, with citations)               │
│                                                                  │
│  4. VALIDATE                                                     │
│     ├── Schema validation (Pydantic)                            │
│     ├── Check citations reference actual context                │
│     └── Verify actions use allowed capabilities                 │
│                                                                  │
│  5. GATE APPROVALS                                               │
│     ├── Check approval policy per action                        │
│     ├── If EXTERNAL_WRITE without approval → NEEDS_APPROVAL     │
│     └── Await human approval if required                        │
│                                                                  │
│  6. EXECUTE                                                      │
│     ├── Tool Broker executes each ActionRequest                 │
│     ├── Log ToolCallRecord per action                           │
│     └── Collect artifacts (created IDs, etc.)                   │
│                                                                  │
│  7. WRITE BACK                                                   │
│     ├── Update notes/tasks/graph with results                   │
│     └── Emit summary if configured                              │
│                                                                  │
│  8. EMIT TRACE                                                   │
│     ├── Create DecisionTrace with full context                  │
│     └── Write to all configured sinks                           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Multi-Agent Architecture

### AgentEngine Interface

```python
class AgentEngine(Protocol):
    """Pluggable engine that produces Plans from context."""
    
    def propose(
        self,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
    ) -> Plan:
        """Generate a plan from context."""
        ...
    
    def revise(
        self,
        plan: Plan,
        observations: list[str],
    ) -> Plan:
        """Optionally revise a plan based on feedback."""
        ...
```

### Multi-Agent Orchestrator

Even with multiple agents, there is **one executor** running tools:

| Pattern | Description |
|---------|-------------|
| **Router** | Choose which agent profile handles an intent |
| **Delegation** | One plan can include `delegate_to_agent` action |
| **Critique** | Optional second agent validates plan before execution |

```
┌─────────────────────────────────────────────────────────────┐
│                    ORCHESTRATOR                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Intent: "Plan my week"                                      │
│           │                                                  │
│           ▼                                                  │
│  ┌─────────────────┐                                         │
│  │  ROUTER AGENT   │ ──▶ Selects: project_manager_agent     │
│  └─────────────────┘                                         │
│           │                                                  │
│           ▼                                                  │
│  ┌─────────────────┐                                         │
│  │ PROJECT MANAGER │ ──▶ Plan with 3 actions                │
│  └─────────────────┘                                         │
│           │                                                  │
│           ▼                                                  │
│  ┌─────────────────┐                                         │
│  │ CRITIQUE AGENT  │ ──▶ Validates plan (optional)          │
│  └─────────────────┘                                         │
│           │                                                  │
│           ▼                                                  │
│  ┌─────────────────┐                                         │
│  │    EXECUTOR     │ ──▶ Runs tools via Broker              │
│  └─────────────────┘                                         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Why Build Your Own Kernel?

### Frameworks Change Quickly

LangChain, LangGraph, Semantic Kernel, AutoGen, CrewAI—each has opinions about:
- Memory management
- Tool calling
- Tracing/observability
- State management

By building a **thin kernel with strict contracts**, you can:
1. Swap frameworks without rewriting the system
2. Maintain consistent tracing across all engines
3. Enforce security policies uniformly
4. Own your data and audit trail

### What Frameworks Are Good At

Use frameworks for what they do well:
- **LangGraph**: Complex state machines, branching logic
- **Semantic Kernel**: Prompt templating, planner patterns
- **Custom**: Full control, simple use cases

But keep them as **pluggable adapters** behind your `AgentEngine` interface.

---

## Related Documents

### Core Architecture
- [01-schemas.md](01-schemas.md) - Core data contracts
- [02-memory.md](02-memory.md) - Memory subsystem
- [03-tools.md](03-tools.md) - Tool Broker and capabilities
- [04-context.md](04-context.md) - Context Assembler
- [05-engines.md](05-engines.md) - Agent engines
- [06-executor.md](06-executor.md) - Executor and workflows
- [07-tracing.md](07-tracing.md) - Tracing and observability
- [08-build-order.md](08-build-order.md) - Implementation roadmap

### Advanced Features
- [10-framework-agnosticism.md](10-framework-agnosticism.md) - Framework swapping
- [11-thinking-policy.md](11-thinking-policy.md) - Reasoning tiers and escalation
- [12-integration-patterns.md](12-integration-patterns.md) - Obsidian integration
- [13-context-retrieval.md](13-context-retrieval.md) - Retrieval planning and gates
- [14-embedding-strategy.md](14-embedding-strategy.md) - Hierarchical embeddings
- [15-auto-tagging.md](15-auto-tagging.md) - LLM enrichment
- [16-hybrid-search.md](16-hybrid-search.md) - Multi-strategy search
- [17-universal-context-system.md](17-universal-context-system.md) - **v1.0.4: Entity model, experience memory, retention**
