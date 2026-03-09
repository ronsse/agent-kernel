# Build Order & Implementation Roadmap

**Version:** 1.0.4  
**Status:** Implemented (Phases 1-10)

This document defines the **order of implementation** to ensure each component can be tested independently.

## Changelog

- **v1.0.1**: Initial phases 1-6 defined
- **v1.0.2**: Added Phase 7 (Context Retrieval)
- **v1.0.3**: Added Phase 8 (Thinking Policy) and Phase 9 (Embeddings)
- **v1.0.4**: Added Phase 10 (Universal Entity Model & Experience Memory)

---

## Implementation Phases

```
┌─────────────────────────────────────────────────────────────────┐
│                    IMPLEMENTATION PHASES                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  PHASE 1: Foundation                                             │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  1.1 Core Schemas    (Pydantic models)                      ││
│  │  1.2 ID Generation   (ULID utilities)                       ││
│  │  1.3 Configuration   (Pydantic Settings)                    ││
│  │  1.4 Trace Store     (SQLite + JSONL)                       ││
│  │  1.5 Event Log       (Append-only)                          ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  PHASE 2: Tools & Execution                                      │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  2.1 Capability Registry   (JSON Schema loading)            ││
│  │  2.2 Tool Broker           (Validation + execution)         ││
│  │  2.3 Local Function Adapter (2-3 tools)                     ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  PHASE 3: Memory & Context                                       │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  3.1 Document Store     (SQLite)                            ││
│  │  3.2 Vector Store       (LanceDB)                           ││
│  │  3.3 Graph Store        (SQLite)                            ││
│  │  3.4 Context Assembler  (Retrieval logic)                   ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  PHASE 4: Agent Engines                                          │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  4.1 AgentEngine Interface                                  ││
│  │  4.2 CustomEngine        (LLM → Plan)                       ││
│  │  4.3 LLM Service         (OpenAI/Anthropic)                 ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  PHASE 5: Executor & Workflows                                   │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  5.1 Deterministic Executor                                 ││
│  │  5.2 Approval Gate                                          ││
│  │  5.3 Workflow Runner                                        ││
│  │  5.4 Scheduler                                              ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  PHASE 6: CLI & Polish                                           │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  6.1 CLI Commands                                           ││
│  │  6.2 First Workflow       (daily_checkin)                   ││
│  │  6.3 Documentation                                          ││
│  │  6.4 Testing & Refinement                                   ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Foundation

**Goal:** Establish core schemas and storage. Everything else depends on this.

### 1.1 Core Schemas

```
src/agent_kernel/core/schemas/
├── __init__.py
├── base.py          # Base model with ULID defaults
├── context.py       # ContextRef, ContextPacket, ContextItem
├── plan.py          # ActionRequest, Plan, RiskAssessment
├── trace.py         # ToolCallRecord, DecisionTrace, Outcome
└── agent.py         # AgentProfile, ContextPolicy, ApprovalPolicy
```

**Deliverables:**
- [ ] All Pydantic models from [01-schemas.md](01-schemas.md)
- [ ] JSON Schema export for each model
- [ ] Unit tests for model validation
- [ ] Example fixtures for testing

### 1.2 ID Generation

```python
# src/agent_kernel/core/ids.py

from ulid import ULID

def generate_ulid() -> str:
    """Generate a new ULID string."""
    return str(ULID())

def generate_prefixed_id(prefix: str) -> str:
    """Generate ID with prefix (e.g., 'trace_01HXYZ...')."""
    return f"{prefix}_{ULID()}"
```

### 1.3 Configuration

```python
# src/agent_kernel/core/config.py

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # Database
    database_url: str = "sqlite+aiosqlite:///./data/agent_kernel.db"
    
    # Vector store
    vector_store_type: str = "chroma"
    chroma_persist_directory: str = "./data/chroma"
    
    # LLM
    default_llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    
    # Tracing
    trace_store_path: str = "./data/traces"
    trace_jsonl_enabled: bool = True
    
    # Context
    context_max_tokens: int = 8000
    context_max_items: int = 50
```

### 1.4 Trace Store

```
src/agent_kernel/tracing/
├── __init__.py
├── trace_store.py   # Interface
└── sinks/
    ├── __init__.py
    ├── sqlite_sink.py
    └── jsonl_sink.py
```

**Deliverables:**
- [ ] `TraceStore` interface
- [ ] `SQLiteTraceSink` implementation
- [ ] `JSONLTraceSink` implementation
- [ ] `MultiSinkTraceStore` for combining sinks
- [ ] CLI command: `list_traces`, `show_trace`

### 1.5 Event Log

```
src/agent_kernel/memory/
├── __init__.py
└── event_log.py
```

**Deliverables:**
- [ ] `EventLog` interface
- [ ] `SQLiteEventLog` implementation
- [ ] Event types enum

---

## Phase 2: Tools & Execution

**Goal:** Tool broker working with 2-3 real tools.

### 2.1 Capability Registry

```
src/agent_kernel/tools/
├── __init__.py
├── registry.py      # Capability loading and validation
└── models.py        # CapabilityDef, RateLimit, etc.

configs/capabilities/
├── tasks.list@v1.yaml
├── tasks.create@v1.yaml
└── notes.search@v1.yaml
```

**Deliverables:**
- [ ] `CapabilityRegistry` class
- [ ] YAML/JSON capability loading
- [ ] Input/output schema validation
- [ ] 3 initial capability definitions

### 2.2 Tool Broker

```
src/agent_kernel/tools/
└── broker.py
```

**Deliverables:**
- [ ] `ToolBroker` class
- [ ] Input validation against schema
- [ ] Allowlist enforcement
- [ ] Approval gating
- [ ] `ToolCallRecord` logging

### 2.3 Local Function Adapter

```
src/agent_kernel/tools/adapters/
├── __init__.py
├── base.py          # ToolAdapter interface
└── local_function.py
```

**Deliverables:**
- [ ] `ToolAdapter` interface
- [ ] `LocalFunctionAdapter` implementation
- [ ] 3 registered functions:
  - `tasks.list@v1`
  - `tasks.create@v1`
  - `notes.search@v1`

---

## Phase 3: Memory & Context

**Goal:** Full memory subsystem with context assembly.

### 3.1 Document Store

```
src/agent_kernel/memory/
├── document_store.py
└── implementations/
    └── sqlite_docstore.py
```

**Deliverables:**
- [ ] `DocumentStore` interface
- [ ] `SQLiteDocumentStore` with FTS5

### 3.2 Vector Store

```
src/agent_kernel/memory/
├── vector_store.py
├── embedding_service.py
└── implementations/
    └── chroma_vector.py
```

**Deliverables:**
- [ ] `VectorStore` interface
- [ ] `ChromaVectorStore` implementation
- [ ] `EmbeddingService` (OpenAI embeddings)

### 3.3 Graph Store

```
src/agent_kernel/memory/
├── graph_store.py
└── implementations/
    └── sqlite_graph.py
```

**Deliverables:**
- [ ] `GraphStore` interface
- [ ] `SQLiteGraphStore` implementation
- [ ] Subgraph queries

### 3.4 Context Assembler

```
src/agent_kernel/context/
├── __init__.py
├── assembler.py
├── ranking.py
└── policies.py
```

**Deliverables:**
- [ ] `ContextAssembler` class
- [ ] Multi-source retrieval
- [ ] Relevance ranking
- [ ] Budget application
- [ ] `RetrievalReport` generation

---

## Phase 4: Agent Engines

**Goal:** Working agent engine that produces valid Plans.

### 4.1 AgentEngine Interface

```
src/agent_kernel/engine/
├── __init__.py
├── agent_engine.py   # Protocol definition
└── registry.py       # Engine registry
```

### 4.2 CustomEngine

```
src/agent_kernel/engine/
└── custom_engine.py
```

**Deliverables:**
- [ ] `CustomEngine` implementation
- [ ] System/user prompt construction
- [ ] Structured output (Plan)
- [ ] Citation validation

### 4.3 LLM Service

```
src/agent_kernel/services/
├── __init__.py
└── llm_service.py
```

**Deliverables:**
- [ ] `LLMService` protocol
- [ ] `OpenAILLMService` implementation
- [ ] Structured output support
- [ ] Optional: `AnthropicLLMService`

---

## Phase 5: Executor & Workflows

**Goal:** Complete execution flow with workflows.

### 5.1 Deterministic Executor

```
src/agent_kernel/executor/
├── __init__.py
├── executor.py
└── validation.py
```

**Deliverables:**
- [ ] `DeterministicExecutor` class
- [ ] Plan validation
- [ ] Side-effect enforcement
- [ ] Artifact collection
- [ ] Trace writing

### 5.2 Approval Gate

```
src/agent_kernel/executor/
└── approval.py
```

**Deliverables:**
- [ ] `ApprovalGate` class
- [ ] Pending approval storage
- [ ] Approval/denial flow

### 5.3 Workflow Runner

```
src/agent_kernel/workflows/
├── __init__.py
├── runner.py
├── spec.py
└── loader.py
```

**Deliverables:**
- [ ] `WorkflowSpec` model
- [ ] `WorkflowRunner` class
- [ ] YAML workflow loading
- [ ] Step execution

### 5.4 Scheduler

```
src/agent_kernel/scheduler/
├── __init__.py
├── scheduler.py
└── triggers.py
```

**Deliverables:**
- [ ] Cron trigger support
- [ ] Manual trigger
- [ ] File watch trigger (optional)

---

## Phase 6: CLI & Polish

**Goal:** Usable CLI and first real workflow.

### 6.1 CLI Commands

```
src/agent_kernel/cli/
├── __init__.py
└── main.py
```

**Commands:**
- [ ] `init` - Initialize database
- [ ] `run-workflow <name>` - Execute a workflow
- [ ] `list-workflows` - List available workflows
- [ ] `list-traces` - List recent traces
- [ ] `show-trace <id>` - Show trace details
- [ ] `list-capabilities` - List registered tools
- [ ] `approve <id>` - Approve pending action

### 6.2 First Workflow

```yaml
# configs/workflows/daily_checkin.yaml
workflow_id: daily_checkin
name: Daily Check-in
trigger:
  type: manual
agent_profile_id: daily_review_agent
```

```yaml
# configs/agents/daily_review_agent.yaml
agent_profile_id: daily_review_agent
name: Daily Review Agent
engine: custom
model_config:
  provider: openai
  model: gpt-4o
  temperature: 0.3
allowed_capabilities:
  - tasks.list@v1
  - tasks.create@v1
  - notes.search@v1
```

**Deliverables:**
- [ ] Working `daily_checkin` workflow
- [ ] Agent profile configuration
- [ ] End-to-end test

### 6.3 Documentation

- [ ] Update README with usage examples
- [ ] API documentation
- [ ] Configuration guide

### 6.4 Testing & Refinement

- [ ] Integration tests for full workflow
- [ ] Performance benchmarks
- [ ] Error handling improvements

---

## Dependency Graph

```
                    ┌─────────────────┐
                    │  Core Schemas   │
                    │      (1.1)      │
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  Trace Store    │ │  Event Log      │ │  Config         │
│     (1.4)       │ │    (1.5)        │ │   (1.3)         │
└────────┬────────┘ └────────┬────────┘ └────────┬────────┘
         │                   │                   │
         └───────────────────┼───────────────────┘
                             ▼
                    ┌─────────────────┐
                    │  Tool Broker    │
                    │     (2.2)       │
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ Document Store  │ │  Vector Store   │ │  Graph Store    │
│     (3.1)       │ │    (3.2)        │ │    (3.3)        │
└────────┬────────┘ └────────┬────────┘ └────────┬────────┘
         │                   │                   │
         └───────────────────┼───────────────────┘
                             ▼
                    ┌─────────────────┐
                    │Context Assembler│
                    │     (3.4)       │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  Custom Engine  │
                    │     (4.2)       │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │    Executor     │
                    │     (5.1)       │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │Workflow Runner  │
                    │     (5.3)       │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │      CLI        │
                    │     (6.1)       │
                    └─────────────────┘
```

---

## Handoff Prompt

Copy/paste this prompt to start implementation:

> Build a local agent kernel with strict schema contracts.
>
> **Phase 1 (Start Here):**
> Implement Pydantic schemas in `src/agent_kernel/core/schemas/`:
> - ContextRef, ContextPacket, ContextItem
> - ActionRequest, Plan, RiskAssessment, PlanValidation
> - ToolCallRecord, DecisionTrace, Outcome, ApprovalRecord, Provenance
> - AgentProfile, ContextPolicy, ApprovalPolicy, ModelConfig
>
> Implement TraceStore (SQLite) and EventLog (append-only) in `src/agent_kernel/tracing/` and `src/agent_kernel/memory/`.
>
> **Phase 2:**
> Implement CapabilityRegistry loading JSON schema per capability from `configs/capabilities/`.
> Implement ToolBroker that validates inputs/outputs, enforces AgentProfile allowlists and approval policy, executes tools via LocalFunctionAdapter, and logs ToolCallRecord.
>
> **Phase 3:**
> Implement DocumentStore, VectorStore, GraphStore in `src/agent_kernel/memory/`.
> Implement ContextAssembler that returns ContextPacket with retrieval report.
>
> **Phase 4:**
> Implement AgentEngine interface and CustomEngine that wraps LLM calls and forces JSON Plan output.
>
> **Phase 5:**
> Implement DeterministicExecutor that validates Plan, gates side effects, executes ActionRequests through ToolBroker, and writes DecisionTrace.
> Implement WorkflowRunner that executes: assemble_context → propose_plan → validate → execute → write-back.
>
> **Phase 6:**
> Provide CLI commands: `init`, `run-workflow <name>`, `list-traces`, `show-trace <id>`.
>
> Ensure everything is framework-agnostic so future adapters can plug in.

---

## Timeline Estimate

| Phase | Duration | Dependencies |
|-------|----------|--------------|
| Phase 1 | 2-3 days | None |
| Phase 2 | 2-3 days | Phase 1 |
| Phase 3 | 3-4 days | Phase 1, 2 |
| Phase 4 | 2-3 days | Phase 3 |
| Phase 5 | 3-4 days | Phase 2, 3, 4 |
| Phase 6 | 2-3 days | Phase 5 |

**Total:** ~2-3 weeks for MVP

---

## Success Criteria

### Phase 1 Complete When:
- [x] All schemas validate correctly
- [x] Traces persist to SQLite
- [x] Events log to append-only store
- [x] `list_traces` CLI works

### Phase 2 Complete When:
- [x] 3 capabilities registered
- [x] Broker validates inputs
- [x] Broker logs ToolCallRecords
- [x] Allowlist enforcement works

### Phase 3 Complete When:
- [x] Documents store and retrieve
- [x] Vector search returns results
- [x] Graph traversal works
- [x] `ContextPacket` assembles correctly

### Phase 4 Complete When:
- [x] Engine produces valid `Plan`
- [x] Citations reference actual context
- [x] Actions use allowed capabilities

### Phase 5 Complete When:
- [x] Executor writes complete traces
- [x] Approval gating works
- [x] Workflow runs end-to-end

### Phase 6 Complete When:
- [x] All CLI commands work
- [x] `daily_checkin` workflow completes
- [x] Documentation is complete

---

## Phase 7: Context Retrieval (v1.0.2)

**Goal:** Enhanced context retrieval with packs, source descriptors, and quality gates.

### 7.1 Context Packs

```
configs/context_packs/
├── vault_rules.yaml
├── task_workflow.yaml
└── project_workflow.yaml

src/agent_kernel/context/
└── pack_resolver.py
```

**Deliverables:**
- [x] `ContextPack` and `ContextPackSelector` schemas
- [x] `ContextPackResolver` with YAML loading
- [x] Scope-based pack resolution

### 7.2 Source Descriptors

```
configs/sources/
├── obsidian.yaml
├── tasks.yaml
├── calendar.yaml
└── graph.yaml

src/agent_kernel/context/
└── source_registry.py
```

**Deliverables:**
- [x] `SourceDescriptor` and `FieldDescriptor` schemas
- [x] `SourceRegistry` with YAML loading
- [x] Filter validation against source schemas

### 7.3 Retrieval Planning

```
src/agent_kernel/context/
├── planner.py
└── executor.py
```

**Deliverables:**
- [x] `RetrievalPlan` and `RetrievalDirective` schemas
- [x] `BaselineRetrievalPlanner` (deterministic)
- [x] `InstructedRetrievalPlanner` (LLM-powered)
- [x] `RetrievalExecutor` for plan execution

### 7.4 Quality Gates

```
src/agent_kernel/context/
└── gates.py
```

**Deliverables:**
- [x] `RetrievalGate` interface
- [x] `PackPresenceGate`, `CoverageGate`, `RecencyGate`, `ParityGate`
- [x] `RetrievalGateRunner` orchestrator

---

## Phase 8: Thinking Policy (v1.0.3)

**Goal:** Autonomous escalation of reasoning effort and context retrieval.

### 8.1 Thinking Config Schema

```
src/agent_kernel/core/schemas/
└── thinking.py
```

**Deliverables:**
- [x] `ThinkingConfig`, `ThinkingTierConfig` schemas
- [x] `RetrievalStrategyConfig`, `VerificationConfig`, `EscalationConfig`
- [x] Predefined presets: `STANDARD_THINKING`, `DEEP_THINKING`, `ADAPTIVE_THINKING`

### 8.2 Thinking Policy Controller

```
src/agent_kernel/engine/
├── thinking_policy.py
└── escalation.py
```

**Deliverables:**
- [x] `ThinkingPolicyController` class
- [x] `ThinkingSession` state tracking
- [x] `EscalationManager` for attempt → gate → escalate flow
- [x] Integration with `AgentProfile.thinking_config`

### 8.3 Critic Engine

```
src/agent_kernel/engine/
└── critic.py
```

**Deliverables:**
- [x] `CriticEngine` for plan verification
- [x] Integration with `deep_with_critic` tier

---

## Phase 9: Embeddings & Hybrid Search (v1.0.3)

**Goal:** Hierarchical embeddings and multi-strategy search.

### 9.1 Embedding Service

```
src/agent_kernel/services/
└── embedding.py
```

**Deliverables:**
- [x] `EmbeddingService` interface
- [x] `OpenAIEmbeddingService` implementation
- [x] Chunking with metadata

### 9.2 Vault Indexer with Embeddings

```
src/agent_kernel/services/
└── vault_indexer.py
```

**Deliverables:**
- [x] Summary embedding generation
- [x] Chunk embedding generation
- [x] Edge deletion tracking
- [x] LLM enrichment integration

### 9.3 Hybrid Search Service

```
src/agent_kernel/services/
└── hybrid_search.py
```

**Deliverables:**
- [x] `HybridSearchService` class
- [x] Strategies: hierarchical, hybrid, vector, keyword, graph
- [x] CLI `search` command

---

## Phase 10: Universal Entity Model & Experience Memory (v1.0.4)

**Goal:** Generalized context from any source, learning from outcomes.

### 10.1 Entity Store

```
src/agent_kernel/core/schemas/
└── entity.py

src/agent_kernel/memory/
└── entity_store.py
```

**Deliverables:**
- [x] `EntityRef`, `EntityView`, `EntityViewType` schemas
- [x] `EntityStore` interface
- [x] `SQLiteEntityStore` implementation

### 10.2 Experience Memory

```
src/agent_kernel/core/schemas/
└── experience.py

src/agent_kernel/memory/
└── experience_store.py
```

**Deliverables:**
- [x] `OutcomeEvaluation`, `ExperienceCase`, `LessonLearned`, `Playbook` schemas
- [x] `ExperienceStore` interface
- [x] `SQLiteExperienceStore` implementation
- [x] CLI commands: `rate-trace`, `list-evals`, `list-lessons`

### 10.3 Playbook System

```
configs/playbooks/
└── daily_checkin.yaml

src/agent_kernel/context/
└── playbook_resolver.py
```

**Deliverables:**
- [x] `PlaybookResolver` class
- [x] Sample playbook configuration
- [x] Integration with context assembly

### 10.4 Retention & Compaction

```
src/agent_kernel/core/schemas/
└── retention.py

src/agent_kernel/services/
└── retention_jobs.py

configs/
└── retention.yaml
```

**Deliverables:**
- [x] `RetentionPolicy`, `TraceRetentionPolicy`, `VectorRetentionPolicy` schemas
- [x] `TraceCompactorJob`, `VectorPrunerJob`, `GraphPrunerJob`, `CacheJanitorJob`
- [x] CLI commands: `compact-traces`, `prune-vectors`, `retention-status`

### 10.5 v1.0.4 Quality Gates

```
src/agent_kernel/context/
└── gates.py  (updated)
```

**Deliverables:**
- [x] `SourceConstraintEnforcementGate`
- [x] `ExperienceWarningGate`
- [x] `PlaybookCoverageGate`

---

## Phase 10 Complete When:
- [x] Entity store registers multi-source entities
- [x] Experience store tracks evaluations and lessons
- [x] Playbook resolver loads and matches playbooks
- [x] Retention jobs run without errors
- [x] New quality gates integrated into gate runner
