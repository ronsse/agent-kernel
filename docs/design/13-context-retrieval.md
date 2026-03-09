# 13. Context Retrieval (v1.0.3)

Version: 1.0.3
Last Updated: 2026-01-26

---

## Overview

Context Retrieval is the process of gathering relevant information from multiple sources
(Obsidian notes, tasks, calendar events, graph relationships) and assembling it into
a `ContextPacket` for agent reasoning.

v1.0.2 introduces a flexible, schema-aware retrieval system with:

1. **Context Packs** - Curated sets of rules/specs that are consistently included
2. **Source Descriptors** - Schema definitions for each data source
3. **Retrieval Planning** - Structured plans for what to retrieve
4. **Coverage Gates** - Quality checks before packing

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Intent + Scope                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Stage 1: Resolve Packs                       │
│  ┌─────────────────────┐    ┌─────────────────────────────────┐ │
│  │ ContextPackResolver │◄───│ configs/context_packs/*.yaml    │ │
│  └─────────────────────┘    └─────────────────────────────────┘ │
│           │                                                     │
│           ▼                                                     │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Matching packs (sorted by priority)                     │    │
│  └─────────────────────────────────────────────────────────┘    │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Stage 2: Create Plan                          │
│  ┌─────────────────────┐    ┌─────────────────────────────────┐ │
│  │ RetrievalPlanner    │◄───│ SourceRegistry (source schemas) │ │
│  └─────────────────────┘    └─────────────────────────────────┘ │
│           │                                                     │
│           ▼                                                     │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ RetrievalPlan (directives with validated filters)       │    │
│  └─────────────────────────────────────────────────────────┘    │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Stage 3: Execute                              │
│  ┌─────────────────────┐    ┌─────────────────────────────────┐ │
│  │ RetrievalExecutor   │◄───│ Doc/Vector/Graph Stores         │ │
│  └─────────────────────┘    └─────────────────────────────────┘ │
│           │                                                     │
│           ▼                                                     │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ ContextItems (with relevance scores, excerpts)          │    │
│  └─────────────────────────────────────────────────────────┘    │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Stage 4: Quality Gates                         │
│  ┌─────────────────────┐                                        │
│  │ RetrievalGateRunner │                                        │
│  └─────────────────────┘                                        │
│           │                                                     │
│           ▼                                                     │
│  ┌──────────────────┬──────────────────┬──────────────────┐     │
│  │ PackPresenceGate │ CoverageGate     │ ParityGate       │     │
│  └──────────────────┴──────────────────┴──────────────────┘     │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Output                                    │
│  ┌─────────────────────┐    ┌─────────────────────────────────┐ │
│  │ ContextPacket       │    │ RetrievalQualityReport          │ │
│  └─────────────────────┘    └─────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## Context Packs

Context Packs formalize "system specifications" - vault rules, project conventions,
workflow guidelines - that should be consistently included in context.

### Schema

```python
class ContextPackSelector(KernelModel):
    vault_id: str | None
    project_id: str | None
    workflow_id: str | None
    agent_profile_id: str | None
    path_globs: list[str]

class ContextPack(VersionedModel):
    pack_id: str
    name: str
    description: str | None
    priority: int  # Lower = higher priority
    selectors: list[ContextPackSelector]
    refs: list[ContextRef]
    include_policy: Literal["always", "relevance", "manual"]
    max_tokens: int | None
```

### Configuration

Context packs are defined in `configs/context_packs/*.yaml`:

```yaml
# configs/context_packs/vault_rules.yaml
pack_id: vault_rules
name: Vault Rules
priority: 10
include_policy: always

selectors:
  - vault_id: personal_vault

refs:
  - ref_type: spec
    ref_id: spec_core_guidelines
    uri: "obsidian:///Agent-Rules/core.md"
    metadata:
      title: "Core Agent Guidelines"
```

Context packs can also pin reusable skills to a workflow by selector, using
`skills:///` URIs so those SKILL documents are always present in context.

Prompt packs use the same mechanism with `metadata.kind=system_prompt` and
`prompts:///` URIs; engines compile them into system prompts and exclude them
from citations.

### Include Policies

| Policy | Behavior |
|--------|----------|
| `always` | Always included, regardless of selectors |
| `relevance` | Included if any selector matches scope |
| `manual` | Never auto-included, must be explicitly requested |

---

## Source Descriptors

Source Descriptors define the schema of each data source - what fields exist,
what operators are allowed, and what constraints apply.

### Schema

```python
class FieldDescriptor(KernelModel):
    name: str                    # e.g., "path", "tags", "frontmatter.project"
    type: FieldType             # string, number, boolean, datetime, enum, list_string
    allowed_ops: list[FilterOp]  # eq, neq, gt, lt, contains, prefix, ...
    description: str | None
    examples: list[str]

class SourceConstraint(KernelModel):
    can_store_text: bool
    max_retention_days: int | None
    allowed_entity_types: list[str]
    requires_live_fetch: bool

class SourceDescriptor(VersionedModel):
    source_id: str
    description: str
    fields: list[FieldDescriptor]
    constraints: SourceConstraint
```

### Configuration

Source descriptors are defined in `configs/sources/*.yaml`:

```yaml
# configs/sources/obsidian.yaml
source_id: obsidian
description: "Obsidian vault notes"

fields:
  - name: path
    type: string
    allowed_ops: [eq, prefix, contains]
    
  - name: tags
    type: list_string
    allowed_ops: [contains, any_in, all_in]
    
  - name: modified_at
    type: datetime
    allowed_ops: [gt, lt, gte, lte]

constraints:
  can_store_text: true
  allowed_entity_types: [note]
```

### Built-in Sources

| Source | Description | Live Fetch |
|--------|-------------|------------|
| `obsidian` | Obsidian vault notes | No |
| `graph` | Knowledge graph nodes/edges | No |
| `tasks` | Extracted task entities | No |
| `calendar` | Calendar events | Yes |
| `slack` | Slack messages | Yes |
| `skills` | Skill manifests and SKILL.md docs | No |

---

## Retrieval Planning

Retrieval plans define what to retrieve and how to filter results.

### Schema

```python
class RetrievalFilter(KernelModel):
    field: str
    op: str
    value: Any

class RetrievalDirective(KernelModel):
    directive_id: str
    source_id: str
    entity_type: str
    query: str | None
    filters: list[RetrievalFilter]
    top_k: int
    min_score: float | None
    recency_boost: bool
    reason: str | None

class RetrievalPlan(VersionedModel):
    retrieval_plan_id: str
    intent: str
    mode: Literal["baseline", "instructed", "iterative"]
    packs_used: list[str]
    directives: list[RetrievalDirective]
    assumptions: list[str]
```

### Planners

#### BaselineRetrievalPlanner (Deterministic)

The baseline planner generates standard directives without LLM calls:

1. Semantic search on notes (if vector store available)
2. Recent notes within time window
3. Open tasks
4. Tasks due soon
5. Upcoming calendar events
6. Graph neighbor expansion (if project specified)

```python
planner = BaselineRetrievalPlanner(source_registry=registry)
plan = await planner.plan(scope, packs, policy)
```

#### InstructedRetrievalPlanner (LLM-Powered)

The instructed planner uses an LLM to generate schema-aware retrieval plans:

1. Receives intent + source descriptors + context packs
2. Generates structured RetrievalPlan
3. Validates all filters against SourceRegistry
4. Falls back to baseline if LLM fails

```python
planner = InstructedRetrievalPlanner(llm_service, source_registry)
plan = await planner.plan(scope, packs, policy)
```

---

## Coverage Gates

Gates verify retrieval quality before packing the ContextPacket.

### Built-in Gates

| Gate | Purpose | Severity |
|------|---------|----------|
| `PackPresenceGate` | Required packs are represented | warning |
| `SchemaAwareFiltersGate` | Filters are valid for source | error |
| `CoverageGate` | Adequate entity type coverage | warning |
| `RecencyGate` | Recent items when requested | warning |
| `ParityGate` | Indexes are not stale | warning |

### Schema

```python
class CoverageGateResult(KernelModel):
    gate: str
    passed: bool
    severity: Literal["info", "warning", "error"]
    details: str | None

class RetrievalQualityReport(KernelModel):
    mode: str
    packs_included: list[str]
    directives_executed: int
    candidates_considered: int
    items_selected: int
    gate_results: list[CoverageGateResult]
    warnings: list[str]
```

### Usage

```python
gate_runner = RetrievalGateRunner(
    source_registry=registry,
    index_state_store=index_state,
)

quality = gate_runner.run(items, packs, plan)

if quality.has_errors:
    # Handle error-level gate failures
    pass
elif quality.has_warnings:
    # Log warnings but proceed
    pass
```

---

## ContextAssembler Integration

The refactored ContextAssembler uses all new components:

```python
async def assemble_async(
    self,
    intent: str,
    policy: ContextPolicy,
    vault_id: str | None = None,
    project_id: str | None = None,
    workflow_id: str | None = None,
    path: str | None = None,
) -> ContextPacket:
    # Step 1: Resolve context packs
    packs = self._pack_resolver.resolve(scope)
    
    # Step 2: Create retrieval plan
    plan = await self._planner.plan(scope, packs, policy)
    
    # Step 3: Execute directives
    result = await self._executor.execute(plan)
    
    # Step 4: Add pack refs as high-priority items
    items = pack_items + result.all_items
    
    # Step 5: Deduplicate + rank
    items = self._deduplicate_items(items)
    items = self._rank_items(items, packs)
    
    # Step 6: Run coverage gates
    quality = self._gate_runner.run(items, packs, plan)
    
    # Step 7: Apply budget limits
    items = items[:budget.max_items]
    
    # Step 8: Pack and return
    return ContextPacket(
        items=items,
        context_packs=[p.pack_id for p in packs],
        retrieval_mode=plan.mode,
        retrieval_report=RetrievalReport(
            retrieval_plan_id=plan.retrieval_plan_id,
            quality=quality,
        ),
    )
```

---

## CLI Commands

### List Context Packs

```bash
agent-kernel list-context-packs
```

Shows all configured context packs with priority and policy.

### Show Context Pack

```bash
agent-kernel show-context-pack vault_rules
```

Shows details of a specific pack including selectors and refs.

### List Sources

```bash
agent-kernel list-sources
```

Shows all configured source descriptors.

### Show Source

```bash
agent-kernel show-source obsidian
```

Shows details of a specific source including fields and constraints.

### Explain Retrieval

```bash
agent-kernel explain-retrieval <packet_id>
```

Shows how context was retrieved for a specific packet.

---

## Mapping to Obsidian Vault

Your existing `Agent-Rules/` folder in your Obsidian vault maps to Context Packs:

| Vault File | Context Pack | Policy |
|------------|--------------|--------|
| `Agent-Rules/core.md` | `vault_rules` | always |
| `Agent-Rules/note-organization.md` | `vault_rules` | always |
| `Agent-Rules/task-management.md` | `task_workflow` | relevance |
| `Agent-Rules/project-management.md` | `project_workflow` | relevance |
| `Agent-Rules/daily-notes.md` | `daily_notes` | relevance |

---

## Summary

1. **Context Packs** formalize which rules/specs to include
2. **Source Descriptors** enable schema-aware retrieval
3. **Baseline Planner** is fast and deterministic
4. **Instructed Planner** handles complex constraints
5. **Gates** ensure retrieval quality
6. **Parity** keeps derived indexes in sync with canonical content

---

# v1.0.3 Additions: ThinkingConfig Integration

> Added in v1.0.3 - Intelligent graph traversal tied to thinking tiers.

## Overview

v1.0.3 adds `assemble_with_thinking()` which uses the agent's `ThinkingConfig` to enable
intelligent, multi-hop context retrieval:

```
Question → Semantic Search → Matched Nodes → Graph Traversal → Related Items
```

This enables the system to find not just directly matching content, but also
**related** content through graph relationships.

## RetrievalConfig

The `ThinkingConfig.retrieval` section controls retrieval behavior:

```yaml
thinking_config:
  retrieval:
    semantic_search: true       # Vector similarity search
    keyword_search: true        # FTS keyword matching
    graph_expansion: true       # Follow edges from matched nodes
    graph_expansion_hops: 2     # How many relationship levels
    recency_boost: true         # Prioritize recent items
    recency_days: 14            # Definition of "recent"
    iterative_retrieval: true   # Allow LLM to request more context
    max_retrieval_iterations: 3 # Max rounds of follow-up retrieval
```

## Graph Expansion Flow

When `graph_expansion: true`, the assembler:

```
┌─────────────────────────────────────────────────────────────────┐
│  1. INITIAL RETRIEVAL                                           │
│     • semantic_search → vector store query                      │
│     • keyword_search → document store FTS                       │
│     Result: Initial matched items with node IDs                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  2. GRAPH EXPANSION                                             │
│     • Extract node IDs from matched items                       │
│     • Call graph_store.get_subgraph(seeds, depth=hops)          │
│     • Uses recursive CTE for O(1) query complexity              │
│     Result: Related nodes within N hops                         │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  3. MERGE & RANK                                                │
│     • Convert graph nodes to ContextItems                       │
│     • Merge with initial results (deduplicate)                  │
│     • Rank by relevance (direct matches > graph expansion)      │
│     Result: Comprehensive context with relationships            │
└─────────────────────────────────────────────────────────────────┘
```

## Example: Work Context Q&A

The `work_context_agent` uses graph expansion for comprehensive answers:

```yaml
# configs/agents/work_context_agent.yaml
thinking_config:
  mode: adaptive
  retrieval:
    semantic_search: true
    keyword_search: true
    graph_expansion: true       # Enable traversal
    graph_expansion_hops: 2     # 2 levels of relationships
    iterative_retrieval: true   # Allow follow-up queries
```

**Query:** "What's the status of the API migration project?"

**Retrieval Flow:**
1. Semantic search finds: "API Migration Design Doc" (node: `note_123`)
2. Graph expansion from `note_123`:
   - Hop 1: `person:sarah` (author), `project:backend-v2` (linked)
   - Hop 2: `note:sarah-api-notes`, `task:migration-phase-2`
3. Context now includes related meetings, people's notes, linked tasks

## Code Implementation

### ContextAssembler.assemble_with_thinking()

```python
async def assemble_with_thinking(
    self,
    intent: str,
    agent_profile: AgentProfile,
    retrieval_config: dict | RetrievalConfig | None = None,
    max_context_tokens: int | None = None,
    ...
) -> ContextPacket:
    # Extract retrieval config from thinking_config
    if retrieval_config is None and agent_profile.thinking_config:
        retrieval_config = agent_profile.thinking_config.retrieval

    # 1. Keyword search (if enabled)
    if retrieval_opts.get("keyword_search"):
        items.extend(self._search_documents(intent, ...))

    # 2. Semantic search (if enabled)
    if retrieval_opts.get("semantic_search"):
        items.extend(self._search_vectors(embedding, ...))

    # 3. Graph expansion (if enabled)
    if retrieval_opts.get("graph_expansion") and self._graph_store:
        # Extract seeds from retrieved items
        expand_seeds = [item.ref.ref_id for item in items
                        if item.ref.ref_type in (RefType.NOTE, RefType.DOCUMENT)]

        if expand_seeds:
            hops = retrieval_opts.get("graph_expansion_hops", 1)
            graph_slice = self._graph_store.get_subgraph(expand_seeds[:10], depth=hops)
            items.extend(self._graph_slice_to_items(graph_slice))
```

## Agent Tool Capabilities

In addition to automatic retrieval, agents can make ad-hoc queries using these capabilities:

| Capability | Purpose | Use Case |
|------------|---------|----------|
| `graph.query@v1` | Query nodes by type/properties | "Find all people in project X" |
| `graph.neighbors@v1` | N-hop traversal from seeds | "What's connected to this meeting?" |
| `vector.search@v1` | Semantic similarity search | "Find notes similar to this topic" |

These are useful when `iterative_retrieval: true` allows the agent to request more context.

### Capability Definitions

```yaml
# configs/capabilities/graph.query@v1.yaml
capability_name: graph.query@v1
adapter_type: local_function
adapter_config:
  module: agent_kernel.tools.adapters.graph_adapter
  function: graph_query

input_schema:
  properties:
    node_type: { type: string }
    properties: { type: object }
    limit: { type: integer, default: 50 }
```

```yaml
# configs/capabilities/graph.neighbors@v1.yaml
capability_name: graph.neighbors@v1
input_schema:
  properties:
    seed_ids: { type: array, items: { type: string } }
    depth: { type: integer, default: 2, min: 1, max: 4 }
    edge_types: { type: array, items: { type: string } }
  required: [seed_ids]
```

```yaml
# configs/capabilities/vector.search@v1.yaml
capability_name: vector.search@v1
input_schema:
  properties:
    query: { type: string }      # Text to embed and search
    embedding: { type: array }   # Or pre-computed vector
    top_k: { type: integer, default: 10 }
    filters: { type: object }
```

## Configuration by Thinking Mode

| Mode | Graph Expansion | Iterative Retrieval | Use Case |
|------|-----------------|---------------------|----------|
| `standard` | Off | Off | Simple tasks, fast response |
| `adaptive` | On (2 hops) | Optional | Q&A, research, analysis |
| `deep` | On (3 hops) | On | Complex investigation |

## Performance Considerations

### Graph Store Optimization

The `SQLiteGraphStore.get_subgraph()` uses a recursive CTE:

```sql
WITH RECURSIVE traversal(node_id, depth) AS (
    -- Base case: seed nodes
    SELECT node_id, 0 FROM nodes WHERE node_id IN (...)
    UNION
    -- Recursive: follow edges
    SELECT ... FROM traversal t JOIN edges e ON ...
    WHERE t.depth < ?
)
```

**Performance:** O(1) queries vs O(N) for iterative traversal.

### Budget Limits

Graph expansion respects context budget:
- `max_tokens`: Total token limit
- `max_notes`, `max_tasks`: Per-type limits
- Seeds limited to 10 nodes to prevent explosion

## Files Added/Modified in v1.0.3

| File | Change |
|------|--------|
| `context/assembler.py` | Added `assemble_with_thinking()` method |
| `core/schemas/thinking.py` | `RetrievalConfig` with graph options |
| `memory/graph_store.py` | Optimized `get_subgraph()` with recursive CTE |
| `tools/adapters/graph_adapter.py` | **New** - Graph query/neighbors functions |
| `tools/adapters/vector_adapter.py` | **New** - Vector search function |
| `configs/capabilities/graph.*.yaml` | **New** - Graph capability definitions |
| `configs/capabilities/vector.*.yaml` | **New** - Vector capability definition |
