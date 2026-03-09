# Context & Memory Taxonomy

**Version:** 1.0.0
**Status:** Implementation Phase

---

## Overview

The Agent Kernel uses a **9-layer context/memory taxonomy** to organize how agents access, store, and reason over information. Each layer serves a distinct purpose, has its own storage backend, and feeds into the `ContextPacket` assembly pipeline through specific access patterns.

This document is the canonical reference for understanding how context flows through the system.

---

## The 9 Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                    9-LAYER MEMORY TAXONOMY                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Layer 1: Agent Identity                                         │
│  └── AgentProfile YAML → loaded at workflow start               │
│                                                                  │
│  Layer 2: Procedural Knowledge (Skills)                          │
│  └── SkillStoreLocalFS → searched by intent                     │
│                                                                  │
│  Layer 3: Business/Domain Context                                │
│  └── DocumentStore + Context Packs → FTS + scope matching       │
│                                                                  │
│  Layer 4: Experiential Context                                   │
│  └── ExperienceStore → similar-case search, lesson lookup       │
│                                                                  │
│  Layer 5: Semantic Memory                                        │
│  └── GraphStore (KNOWLEDGE nodes) → keyword + freshness query   │
│                                                                  │
│  Layer 6: Episodic Memory                                        │
│  └── GraphStore (TRAJECTORY nodes) → similarity search          │
│                                                                  │
│  Layer 7: Working Memory                                         │
│  └── (future) session-scoped scratch data                       │
│                                                                  │
│  Layer 8: Environmental Context                                  │
│  └── HealthChecker + CapabilityRegistry → on-demand probes      │
│                                                                  │
│  Layer 9: Temporal Context                                       │
│  └── Recency scoring + CalendarAdapter → time-weighted queries  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Layer Reference

| # | Layer | What It Holds | Store | Access Pattern |
|---|-------|---------------|-------|----------------|
| 1 | **Agent Identity** | Per-agent config, capabilities, policies, thinking tier | `AgentProfile` YAML | Loaded by workflow runner at start |
| 2 | **Procedural Knowledge (Skills)** | Portable how-to guidance (SKILL.md), scripts, references | `SkillStoreLocalFS` (filesystem) | Search by intent, loaded as `ContextItem` |
| 3 | **Business/Domain Context** | Project specs, rules, conventions, documents, notes | `DocumentStore` + `Context Packs` | FTS + scope matching |
| 4 | **Experiential Context** | Cases, lessons learned, playbooks | `ExperienceStore` (SQLite) | Similar-case search, lesson lookup |
| 5 | **Semantic Memory** | Concepts, systems, insights, patterns, rules | `GraphStore` (KNOWLEDGE nodes) | `ContextGraphQuery.find_relevant_knowledge()` |
| 6 | **Episodic Memory** | Past decision trajectories + outcomes | `GraphStore` (TRAJECTORY nodes) | `ContextGraphQuery.find_similar_trajectories()` |
| 7 | **Working Memory** | Current session state, scratch data | *(not yet implemented)* | Deferred — documented as future layer |
| 8 | **Environmental Context** | Tool availability, system health, service status | `HealthChecker`, `CapabilityRegistry` | On-demand probes |
| 9 | **Temporal Context** | Deadlines, recency, calendar events | Recency scoring + `CalendarAdapter` | Time-weighted in graph queries |

---

## Layer Details

### Layer 1: Agent Identity

**What:** Per-agent configuration that defines behavior, capabilities, thinking tier, and policies.

**Storage:** YAML files in `configs/agents/`.

**Key schema:** `AgentProfile` (contains `ModelConfig`, `ContextPolicy`, `ApprovalPolicy`, `ThinkingConfig`).

**Access:** Loaded once at workflow start by `WorkflowRunner`. Not included in `ContextPacket` items — instead shapes *how* context is assembled (token budgets, retrieval limits, allowed scopes).

**Example:**
```yaml
agent_profile_id: deep_analyst
name: Deep Analyst
engine: custom
llm_config:
  provider: openai
  model: gpt-4o
  reasoning_effort: medium
thinking_config:
  mode: adaptive
  escalation:
    enabled: true
    start_tier: 1
    max_tier: 3
```

### Layer 2: Procedural Knowledge (Skills)

**What:** Portable, human-authored how-to guidance. Each skill is a directory with a `SKILL.md` file, optional reference docs, scripts, and assets.

**Storage:** Filesystem via `SkillStoreLocalFS`. Skills are also synced to the knowledge graph as `NodeType.SKILL` nodes for cross-layer discovery.

**Key schemas:** `SkillManifest`, `SkillLoadResult`, `SkillResourceRef`, `SkillOrigin`.

**Access:** Searched by intent during context assembly. Matching skills are included as `ContextItem` with `RefType.SKILL`.

**Lifecycle:** Install → use → deprecate. Content-hash versioned.

### Layer 3: Business/Domain Context

**What:** Project specifications, rules, conventions, indexed documents, and Obsidian notes.

**Storage:** `DocumentStore` (SQLite FTS) for keyword search. `VectorStore` (LanceDB) for semantic search. `Context Packs` (YAML) for curated, high-priority bundles.

**Key schemas:** `ContextPack`, `ContextPackScope`, `ContextRef` with `RefType.DOCUMENT`.

**Access:** FTS keyword search + semantic vector search + context pack resolution. Pack items get priority boost (score 1.0). Results ranked by relevance and trimmed to budget.

### Layer 4: Experiential Context

**What:** Empirical knowledge mined from past agent decisions and outcomes. The learning loop: Traces → Evaluations → Cases → Lessons → Playbooks.

**Storage:** `ExperienceStore` (SQLite). Cases and lessons are also synced to the knowledge graph via `ExperienceBridge` as `NodeType.INSIGHT` and `NodeType.PRACTICE` nodes.

**Key schemas:** `ExperienceCase`, `LessonLearned`, `Playbook`, `OutcomeEvaluation`.

**Access:** Similar-case search by workflow/capability/label. Lesson lookup by scope. Included in context assembly as `ContextItem` with `RefType.CASE` and `RefType.LESSON`.

### Layer 5: Semantic Memory

**What:** Accumulated concepts, systems, insights, patterns, and rules extracted from agent interactions and manual input.

**Storage:** `GraphStore` — nodes with types: `DOMAIN`, `SYSTEM`, `CONCEPT`, `PRACTICE`, `INSIGHT`, `PATTERN`, `DATA_OBJECT`, `RULE`.

**Key schemas:** `GraphNode`, `KnowledgeNodeProperties`, `FreshnessScore`, `KnowledgeTier`.

**Access:** `ContextGraphQueryService.find_relevant_knowledge()` — keyword matching + freshness scoring + confidence filtering. Results included as `ContextItem` with `RefType.KNOWLEDGE`.

### Layer 6: Episodic Memory

**What:** Past decision trajectories — records of what the agent did, what entities it touched, and what outcomes resulted.

**Storage:** `GraphStore` — nodes with types: `TRAJECTORY`, `DECISION_EVENT`, `OBSERVATION`. Created by `TraceDecomposer` from `DecisionTrace` records.

**Key schemas:** `TrajectoryProperties`, `DecisionEventProperties`, `DecompositionResult`.

**Access:** `ContextGraphQueryService.find_similar_trajectories()` — intent similarity + outcome status filtering. Results included as `ContextItem` with `RefType.TRAJECTORY`.

### Layer 7: Working Memory (Future)

**What:** Ephemeral, session-scoped scratch data for multi-step reasoning within a single workflow run.

**Storage:** Not yet implemented. Planned as an in-memory store with optional persistence for long-running workflows.

**Design sketch:** A key-value store scoped to `run_id`, cleared on workflow completion. Would hold intermediate results, partial plans, and scratchpad data.

### Layer 8: Environmental Context

**What:** Current system state — which tools are available, which services are healthy, what the current capabilities are.

**Storage:** `HealthChecker` for service probes. `CapabilityRegistry` for tool availability. Not persisted — computed on demand.

**Access:** Probed at workflow start or on-demand. Informs `AgentProfile.allowed_capabilities` filtering and circuit breaker state.

### Layer 9: Temporal Context

**What:** Time-aware signals — deadlines, recency weighting, calendar events, freshness decay.

**Storage:** `CalendarAdapter` for calendar events. `FreshnessCalculator` for decay scoring in the context graph.

**Access:** Recency boost applied during ranking (`recency_days` in `RetrievalConfig`). Calendar events included via `CalendarSyncService`. Freshness scores factor into knowledge node relevance.

---

## Skill ↔ Context Delineation

Skills (Layer 2) are fundamentally different from domain context (Layer 3) and experience (Layer 4). Understanding these distinctions is critical for correct context assembly and MCP tool design.

| Dimension | Skills (Layer 2) | Domain Context (Layer 3) | Experience (Layer 4) |
|-----------|-------------------|--------------------------|----------------------|
| **Nature** | Procedural (how-to) | Declarative (what/why) | Empirical (what worked) |
| **Scope** | Cross-project, portable | Project-scoped | Workflow-scoped |
| **Storage** | Filesystem (SKILL.md) | SQLite (DocumentStore) | SQLite (ExperienceStore) |
| **Authoring** | Human-written or published from playbooks | Human-written or ingested | Auto-mined from traces |
| **Versioning** | Content hash | Document hash | Immutable (append-only) |
| **Lifecycle** | Install → use → deprecate | Create → index → archive | Extract → validate → promote |
| **Graph sync** | `NodeType.SKILL` via `SkillGraphSync` | `NodeType.NOTE` / `NodeType.DOCUMENT` via `VaultIndexer` | `NodeType.INSIGHT` / `NodeType.PRACTICE` via `ExperienceBridge` |
| **Context assembly** | `SkillStore.search()` → `ContextItem(RefType.SKILL)` | `DocumentStore.search()` → `ContextItem(RefType.DOCUMENT)` | `ExperienceStore.find_similar_cases()` → `ContextItem(RefType.CASE)` |

### When to Use Each

- **Skill:** "How do I create a Mermaid diagram?" → procedural guidance
- **Domain Context:** "What are the schema contracts for this project?" → project specs
- **Experience:** "What happened last time we ran this workflow?" → past outcomes

---

## Data Flow: Context Assembly Pipeline

Each layer feeds into `ContextPacket` assembly through a defined path:

```
┌─────────────────────────────────────────────────────────────────┐
│                    CONTEXT ASSEMBLY PIPELINE                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Intent + AgentProfile                                           │
│       │                                                          │
│       ▼                                                          │
│  ┌──────────────────────┐                                        │
│  │  Context Pack Resolve │ ──► Layer 3 (curated bundles)        │
│  └──────────┬───────────┘                                        │
│             ▼                                                    │
│  ┌──────────────────────┐                                        │
│  │  Skill Search         │ ──► Layer 2 (procedural guidance)    │
│  └──────────┬───────────┘                                        │
│             ▼                                                    │
│  ┌──────────────────────┐                                        │
│  │  Keyword Search (FTS) │ ──► Layer 3 (documents/notes)        │
│  └──────────┬───────────┘                                        │
│             ▼                                                    │
│  ┌──────────────────────┐                                        │
│  │  Semantic Search      │ ──► Layer 3 (vector similarity)      │
│  └──────────┬───────────┘                                        │
│             ▼                                                    │
│  ┌──────────────────────┐                                        │
│  │  Graph Expansion      │ ──► Layer 5/6 (knowledge/episodic)   │
│  └──────────┬───────────┘                                        │
│             ▼                                                    │
│  ┌──────────────────────┐                                        │
│  │  Context Graph Search │ ──► Layer 5 (semantic) + 6 (episodic)│
│  └──────────┬───────────┘                                        │
│             ▼                                                    │
│  ┌──────────────────────┐                                        │
│  │  Experience Retrieval │ ──► Layer 4 (cases + lessons)        │
│  └──────────┬───────────┘                                        │
│             ▼                                                    │
│  Deduplicate → Rank → Budget Trim → ContextPacket               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Assembly Methods

| Method | Layers Used | When |
|--------|-------------|------|
| `assemble()` | 2, 3 | Legacy sync path |
| `assemble_async()` | 2, 3 | Full async with packs and gates |
| `assemble_with_thinking()` | 2, 3, 4, 5, 6 | Thinking-tier-aware with experience |

### Scoring Hierarchy

Items from different layers receive different base relevance scores to maintain priority:

| Source | Base Score | Reason |
|--------|-----------|--------|
| Context Pack items | 1.0 | Curated, highest priority |
| Skill manifests | 0.6 → 0.2 (ranked) | Procedural guidance |
| Document search | FTS rank | Direct keyword relevance |
| Semantic search | Cosine similarity | Meaning-based |
| Knowledge nodes | score × 0.8 | Slightly below direct search |
| Trajectory nodes | score × 0.7 | Past context, lower weight |
| Experience cases | score × 0.5 | Background guidance |
| Experience lessons | score × 0.5 | Background guidance |

---

## MCP Tool Mapping

The `agent-kernel` MCP server exposes tools organized by taxonomy layer:

```
┌─────────────────────────────────────────────────────────────────┐
│                    MCP TOOL → LAYER MAPPING                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  memory.*     → Layer 3 (DocumentStore + VectorStore)            │
│  ├── memory.search    (keyword / semantic / hybrid search)      │
│  ├── memory.store     (store a document)                        │
│  └── memory.delete    (delete a document)                       │
│                                                                  │
│  knowledge.*  → Layer 5 (GraphStore - knowledge nodes)           │
│  ├── knowledge.query  (relevance-weighted knowledge search)     │
│  ├── knowledge.add    (add a knowledge node)                    │
│  └── knowledge.relate (create an edge between nodes)            │
│                                                                  │
│  experience.* → Layer 4 (ExperienceStore)                        │
│  ├── experience.cases     (search experience cases)             │
│  ├── experience.lessons   (list lessons learned)                │
│  └── experience.playbooks (list playbooks)                      │
│                                                                  │
│  skill.*      → Layer 2 (SkillStoreLocalFS)                      │
│  ├── skill.search  (search skills by intent)                    │
│  ├── skill.load    (load full skill content)                    │
│  └── skill.list    (list all skill manifests)                   │
│                                                                  │
│  context.*    → Cross-layer (ContextAssembler)                   │
│  ├── context.assemble (full multi-layer context assembly)       │
│  └── context.graph    (direct graph query)                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### MCP Server Architecture

```
Claude Code / MCP Client
        │
        ▼  (stdio)
┌─────────────────────────┐
│   agent-kernel MCP      │
│                         │
│  StoreFactory creates:  │
│  ├── DocumentStore      │
│  ├── VectorStore        │
│  ├── GraphStore         │
│  ├── ExperienceStore    │
│  └── SkillStoreLocalFS  │
│                         │
│  Tool handlers delegate │
│  to kernel stores       │
└─────────────────────────┘
```

---

## Integration Architecture

### Graph Sync Points

Several components maintain derived representations in the knowledge graph:

| Component | Source | Graph Node Types | Sync Trigger |
|-----------|--------|-----------------|--------------|
| `VaultIndexer` | Obsidian notes | `NOTE`, `TAG`, `TASK` | File watch / reconciliation |
| `TraceDecomposer` | DecisionTrace | `TRAJECTORY`, `DECISION_EVENT` | Post-execution hook |
| `ExperienceBridge` | ExperienceStore | `INSIGHT`, `PRACTICE` | Periodic sync |
| `SkillGraphSync` | SkillStore | `SKILL` | On-demand / startup |
| `ContextGraphIngestion` | Manual / various | All knowledge types | API call |

### Cross-Layer Interactions

```
Layer 2 (Skills)
    │
    ├── SkillGraphSync ──► Layer 5 (SKILL nodes in graph)
    │
    └── Playbook promotion ──► Layer 4 (Playbook → Skill)

Layer 4 (Experience)
    │
    ├── ExperienceBridge ──► Layer 5 (INSIGHT nodes)
    │                    ──► Layer 5 (PRACTICE nodes)
    │
    └── ExperienceMiner ◄── Layer 6 (traces → cases)

Layer 6 (Episodic)
    │
    └── TraceDecomposer ──► TRAJECTORY, DECISION_EVENT nodes
                       ──► CO_OCCURS_WITH edges (structural learning)
```

---

## Future: Working Memory (Layer 7)

Working memory is the only unimplemented layer. Design considerations:

1. **Scope:** Per-`run_id`, cleared on workflow completion
2. **Interface:** Key-value store with typed values
3. **Persistence:** Optional checkpoint to SQLite for long-running workflows
4. **Access:** Available to engines during `propose()` and to executor during `execute()`
5. **Use cases:**
   - Intermediate results in multi-step reasoning
   - Scratch data for iterative refinement
   - Session state across escalation attempts

---

## Related Documents

- [00-overview.md](00-overview.md) - Design principles and component overview
- [01-schemas.md](01-schemas.md) - Core data contracts
- [02-memory.md](02-memory.md) - Memory subsystem details
- [03-tools.md](03-tools.md) - Tool Broker and capabilities
- [04-context.md](04-context.md) - Context Assembler
- [11-thinking-policy.md](11-thinking-policy.md) - Thinking tiers and escalation
- [12-integration-patterns.md](12-integration-patterns.md) - Obsidian integration
- [17-universal-context-system.md](17-universal-context-system.md) - Entity model, experience memory
