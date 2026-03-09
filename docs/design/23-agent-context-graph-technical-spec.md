# Agent Context Graph: Technical Specification

**Version:** 2.0
**Status:** Proposal
**Audience:** Staff/Principal Engineers

---

## 1. System Overview

### 1.1 Purpose

The Agent Context Graph captures agent decision traces, decomposes them into a traversable knowledge graph, and provides relevance-weighted retrieval for context assembly. It implements both semantic memory (accumulated knowledge) and episodic memory (decision trajectories) as graph structure, enabling agents to learn from past runs.

### 1.2 Architecture Position

The context graph sits between the Trace Store (writes) and the Context Assembler (reads). It is the connective tissue that transforms raw execution records into queryable institutional memory.

![C4 System Context](../diagrams/context-graph-system-context.mmd)

![Container View](../diagrams/context-graph-container.mmd)

### 1.3 Capability Map — Technical View

| ID | Capability | Implementing Class | File | Key Methods | Node/Edge Types |
|---|---|---|---|---|---|
| **C1** | Source Connectors | `ContextGraphIngestion`, `ManualExtractor` | `ingestion.py`, `extractors/manual_extractor.py` | `ingest_trace()`, `ingest_manual()` | All knowledge types |
| **C2** | Trace Capture | `TraceDecomposer`, `ContextGraphHooks` | `decomposer.py`, `hooks.py` | `decompose()`, `on_trace_completed()` | TRAJECTORY, DECISION_EVENT |
| **C3** | Entity Resolution | `TraceDecomposer._link_entities()` | `decomposer.py:220` | `_link_entities()`, `_create_trajectory_node()` | All entity types |
| **C4** | Context Graph Store | `GraphStore` (ABC), `LakebaseGraphStore` | `memory/graph_store.py` | `upsert_node/edge()`, `get_subgraph()`, `query()` | — |
| **C5** | Knowledge Distillation | `ExperienceBridge`, `DeterministicCompaction` | `experience_bridge.py`, `compaction.py` | `ingest_lesson()`, `compact()` | INSIGHT, PRACTICE, PATTERN, SUMMARY |
| **C6** | Query & Scoring | `ContextGraphQueryService`, `FreshnessCalculator` | `query.py`, `freshness.py` | `find_relevant_knowledge()`, `find_similar_trajectories()` | All queryable types |
| **C7** | Context Assembly | `ContextAssembler` | `context/assembler.py` | `assemble_with_thinking()` | Reads knowledge + trajectory |
| **C8** | Governance | — (future: Unity Catalog) | — | — | — |
| **C9** | Retention & Compaction | `RetentionExecutor` | `retention.py` | `run_full()`, `tier_knowledge_nodes()`, `compact_cold_nodes()` | SUMMARY, SUPERSEDES |
| **C10** | Observability | `TypeRegistry`, Event emissions | `types.py` | `record_type_usage()`, `get_stats()` | — |

---

## 2. Graph Ontology

Source: `src/agent_kernel/core/schemas/graph.py`

![Node Type Catalog](../diagrams/context-graph-ontology-nodes.mmd)

![Knowledge Relationships](../diagrams/context-graph-ontology-knowledge.mmd)

![Episodic Memory](../diagrams/context-graph-ontology-episodic.mmd)

### 2.1 Node Types

**Organizational Entities** — represent business objects:

| NodeType | Description | Created By |
|---|---|---|
| `NOTE` | Obsidian vault note | VaultIndexer |
| `TAG` | Tag from frontmatter or inline | VaultIndexer |
| `TASK` | Extracted TODO item | TaskParser |
| `PROJECT` | Project entity | VaultIndexer, manual |
| `PERSON` | Person entity | VaultIndexer |
| `CALENDAR_EVENT` | Calendar event | CalendarSyncService |
| `LABEL` | Semantic label for tasks | TaskSyncService |
| `SECTION` | Section within a project | TaskSyncService |

**Betting & Marketing Entities** — domain-specific business objects:

| NodeType | Description | Created By |
|---|---|---|
| `BET` | Wagering event | Betting feed connector |
| `BET_LEG` | Individual leg within a bet | Betting feed connector |
| `PROMOTION` | Marketing promotion/bonus | SSOT Core tables, promo engine |
| `VALUE_TIER` | Customer value tier segment | SSOT Core tables |
| `KPI_METRIC` | Business KPI (handle, GGR, eGGR) | SSOT Core tables |
| `ODDS_FEED` | External odds data feed | Odds feed connector |
| `CAMPAIGN` | Marketing campaign | SSOT Core tables |

**Semantic Memory (Knowledge)** — accumulated concepts and insights:

| NodeType | Description | Property Model | Created By |
|---|---|---|---|
| `DOMAIN` | Business/technical domain | `DomainProperties` | Manual, ingestion |
| `SYSTEM` | Technical or business system | `SystemProperties` | Manual, ingestion |
| `CONCEPT` | Abstract concept | `ConceptProperties` | Manual, ingestion, distillation |
| `PRACTICE` | Business practice/procedure | `KnowledgeNodeProperties` | ExperienceBridge |
| `INSIGHT` | Learned heuristic | `InsightProperties` | ExperienceBridge |
| `PATTERN` | Recurring pattern across trajectories | `PatternProperties` | Co-occurrence analysis |
| `DATA_OBJECT` | Table, endpoint, data entity | `DataObjectProperties` | Manual, ingestion |
| `RULE` | Business rule or constraint | `KnowledgeNodeProperties` | Manual |

**Episodic Memory (Event Clock)** — decision trajectories:

| NodeType | Description | Property Model | Created By |
|---|---|---|---|
| `TRAJECTORY` | Agent walk through entity space | `TrajectoryProperties` | TraceDecomposer |
| `DECISION_EVENT` | Individual decision within trajectory | `DecisionEventProperties` | TraceDecomposer |
| `OBSERVATION` | Something observed/discovered | `KernelModel` | Future |
| `SUMMARY` | Compacted summary of other nodes | `SummaryProperties` | RetentionExecutor |

**External Entity Types** — from external sources:

| NodeType | Description |
|---|---|
| `MESSAGE`, `THREAD`, `EMAIL` | Communication entities |
| `TICKET`, `PULL_REQUEST` | Work tracking entities |
| `CAPABILITY` | Tool/capability reference |
| `SKILL` | Portable procedural guidance |

### 2.2 Edge Types

**Organizational edges:**

| EdgeType | Source → Target | Properties |
|---|---|---|
| `NOTE_LINKS_TO_NOTE` | Note → Note | — |
| `NOTE_TAGGED_WITH_TAG` | Note → Tag | — |
| `NOTE_HAS_TASK` | Note → Task | — |
| `NOTE_MENTIONS_PERSON` | Note → Person | — |
| `TASK_BELONGS_TO_PROJECT` | Task → Project | — |
| `TASK_HAS_LABEL` | Task → Label | — |
| `TASK_IN_SECTION` | Task → Section | — |
| `TASK_SUBTASK_OF` | Task → Parent Task | — |
| `TASK_CREATED_FROM` | Task → Source entity | — |
| `TASK_SYNCED_TO` | Task → External system | `adapter_id` |
| `PROJECT_CONTAINS_NOTE` | Project → Note | — |
| `PROJECT_HAS_SECTION` | Project → Section | — |
| `CALENDAR_EVENT_RELATED_TO_NOTE` | CalendarEvent → Note | — |
| `CALENDAR_EVENT_RELATED_TO_TASK` | CalendarEvent → Task | — |

**Betting & marketing edges:**

| EdgeType | Source → Target | Properties |
|---|---|---|
| `BET_HAS_LEG` | Bet → BetLeg | — |
| `BET_PLACED_BY_TIER` | Bet → ValueTier | — |
| `PROMOTION_TARGETS_TIER` | Promotion → ValueTier | — |
| `PROMOTION_PART_OF_CAMPAIGN` | Promotion → Campaign | — |
| `KPI_SEGMENTED_BY_TIER` | KpiMetric → ValueTier | — |
| `KPI_MEASURED_FOR_CAMPAIGN` | KpiMetric → Campaign | — |
| `ODDS_FEED_FEEDS_BET` | OddsFeed → Bet | — |
| `BET_LEG_SETTLES_VIA` | BetLeg → System | — |

**Trajectory edges (event clock):**

| EdgeType | Source → Target | Properties |
|---|---|---|
| `TRAJECTORY_TOUCHED` | Trajectory → Entity | `step_order` |
| `TRAJECTORY_DECIDED` | Trajectory → DecisionEvent | `step_order` |
| `TRAJECTORY_OBSERVED` | Trajectory → Observation | — |
| `TRAJECTORY_PRODUCED` | Trajectory → Artifact | — |
| `DECISION_ABOUT` | DecisionEvent → Entity | — |
| `PRECEDED_BY` | DecisionEvent → Prior Event | `step_order` |
| `SIMILAR_TO` | Trajectory → Trajectory | — |

**Knowledge edges (semantic memory):**

| EdgeType | Source → Target | Properties |
|---|---|---|
| `DOMAIN_CONTAINS` | Domain → System/Concept | — |
| `SYSTEM_INTEGRATES_WITH` | System ↔ System | — |
| `SYSTEM_HAS_DATA_OBJECT` | System → DataObject | — |
| `CONCEPT_RELATED_TO` | Concept ↔ Concept | — |
| `INSIGHT_ABOUT` | Insight → Entity | — |
| `INSIGHT_DERIVED_FROM` | Insight → Trajectory/Trace | — |
| `PATTERN_OBSERVED_IN` | Pattern → Trajectory | — |
| `PRACTICE_USES` | Practice → System/Tool | — |
| `RULE_CONSTRAINS` | Rule → Entity | — |

**Growth management edges:**

| EdgeType | Source → Target | Properties |
|---|---|---|
| `SUMMARY_OF` | Summary → Original nodes | — |
| `SUPERSEDES` | Newer → Older version | — |
| `CO_OCCURS_WITH` | Entity ↔ Entity | `weight`, `first_seen`, `last_seen` |

**Experience memory edges:**

| EdgeType | Source → Target |
|---|---|
| `TRACE_HAS_CASE` | Trace → ExperienceCase |
| `CASE_YIELDED_LESSON` | Case → Lesson |
| `LESSON_APPLIES_TO_CAPABILITY` | Lesson → Capability |
| `LESSON_APPLIES_TO_WORKFLOW` | Lesson → Workflow |
| `PLAYBOOK_DERIVED_FROM_LESSON` | Playbook → Lesson |
| `PLAYBOOK_APPLIES_TO_WORKFLOW` | Playbook → Workflow |

### 2.3 Property Schemas

All knowledge node property models inherit from `KnowledgeNodeProperties` (source: `core/schemas/knowledge.py`):

**Base fields (all knowledge nodes):**

| Field | Type | Description |
|---|---|---|
| `title` | `str` | Human-readable title |
| `description` | `str | None` | Longer description |
| `knowledge_source` | `KnowledgeSource` | How created: manual, trace, doc, import, compaction, inference |
| `source_refs` | `list[str]` | IDs of source entities |
| `confidence` | `float` (0-1) | 1.0 for manual, lower for extracted |
| `freshness` | `FreshnessScore` | Time-decay tracking |
| `tier` | `KnowledgeTier` | HOT / WARM / COLD |
| `tags` | `list[str]` | Machine tags |
| `created_by` | `str | None` | Agent profile ID or "user" |
| `superseded_by` | `str | None` | Node ID that supersedes this one |

**Specialized property models:** See `DomainProperties`, `SystemProperties`, `ConceptProperties`, `InsightProperties`, `PatternProperties`, `DataObjectProperties`, `SummaryProperties` in `core/schemas/knowledge.py`.

**Trajectory properties** (`TrajectoryProperties`):

| Field | Type | Description |
|---|---|---|
| `trace_id` | `str` | Link to full DecisionTrace |
| `agent_profile_id` | `str` | Agent that performed trajectory |
| `intent` | `str` | What the agent was trying to do |
| `workflow_id` | `str | None` | Workflow that triggered trajectory |
| `outcome_status` | `str` | completed / partial / failed |
| `outcome_summary` | `str | None` | Brief summary |
| `entities_touched` | `list[str]` | Ordered list of entity node_ids |
| `capabilities_used` | `list[str]` | Tool capabilities invoked |
| `step_count` | `int` | Number of decision events |
| `duration_ms` | `int` | Total duration |
| `reasoning_tier` | `int` | Thinking tier used (0-3) |

**Decision event properties** (`DecisionEventProperties`):

| Field | Type | Description |
|---|---|---|
| `trace_id` | `str` | Link to parent trace |
| `step_order` | `int` | Position in sequence (0-indexed) |
| `action_type` | `str` | tool_call / approval / plan_step |
| `capability_name` | `str | None` | Tool capability used |
| `input_summary` | `str | None` | Truncated input (max 200 chars) |
| `output_summary` | `str | None` | Truncated output (max 200 chars) |
| `status` | `str` | success / error / denied / skipped / timeout |
| `duration_ms` | `int` | Duration of this action |

---

## 3. Trace Decomposition (Event Clock)

Source: `src/agent_kernel/context_graph/decomposer.py` (365 lines)

### 3.1 Algorithm

The `TraceDecomposer.decompose()` method executes 9 steps to transform a `DecisionTrace` into graph structure:

![Decomposition Flow](../diagrams/context-graph-decomposition.mmd)

| Step | Operation | Graph Writes |
|---|---|---|
| 1 | Create TRAJECTORY node from trace metadata | 1 node |
| 2 | Extract entities from `plan.context_refs_used` | 0+ nodes (if entity doesn't exist) |
| 3 | Create `TRAJECTORY_TOUCHED` edges to each entity | N edges |
| 4 | Create DECISION_EVENT nodes (one per `ToolCallRecord`) | M nodes |
| 5 | Create `TRAJECTORY_DECIDED` edges to each event | M edges |
| 6 | Create `DECISION_ABOUT` edges from events to affected entities | Variable |
| 7 | Create `PRECEDED_BY` causal chain between events | M-1 edges |
| 8 | Create `TRAJECTORY_PRODUCED` edges to outcome artifacts | P edges |
| 9 | Update `CO_OCCURS_WITH` weights for entity pairs | C(N,2) edges |

**ID generation:**
- Trajectory: `trajectory:{trace_id}`
- Decision event: `decision_event:{trace_id}:{step_order}`
- Entity references: `{ref_type}:{ref_id}`

### 3.2 Co-occurrence Algorithm (Structural Learning)

Every pair of entities that appeared together in the same trajectory gets a `CO_OCCURS_WITH` edge (source: `decomposer.py:293-350`).

```
For each pair (entity_a, entity_b) in entities_touched:
    Normalize order: source = min(a, b), target = max(a, b)
    If CO_OCCURS_WITH edge exists between source → target:
        Increment weight += 1
        Update last_seen timestamp
    Else:
        Create edge with weight=1, first_seen, last_seen
```

This is the **structural learning mechanism**: informed walks produce co-occurrence statistics that encode organizational structure without explicit declarations.

### 3.3 Hooks Integration

The `ContextGraphHooks` class (`hooks.py`) provides the integration point:

| Hook | Trigger | Effect |
|---|---|---|
| `on_trace_completed(trace, success)` | After trace is persisted | Calls `ingestion.ingest_trace()` → `decomposer.decompose()` |
| `on_lesson_created(lesson)` | After experience lesson mined | Calls `ingestion.ingest_lesson()` → INSIGHT node |
| `on_playbook_created(playbook)` | After playbook created | Calls `ingestion.ingest_playbook()` → PRACTICE node |

---

## 4. Knowledge Ingestion

### 4.1 Multi-Source Orchestrator

Source: `src/agent_kernel/context_graph/ingestion.py` (263 lines)

`ContextGraphIngestion` is the single entry point for all graph writes:

| Method | Source | Creates |
|---|---|---|
| `ingest_trace(trace)` | DecisionTrace | TRAJECTORY + DECISION_EVENTs + edges (delegates to TraceDecomposer) |
| `ingest_lesson(lesson)` | LessonLearned | INSIGHT node + INSIGHT_ABOUT edges |
| `ingest_playbook(playbook)` | Playbook | PRACTICE node + PRACTICE_USES edges |
| `ingest_knowledge_node(node_type, properties)` | Manual/API | Any knowledge node type |

### 4.2 Experience Bridge

Source: `src/agent_kernel/context_graph/experience_bridge.py` (234 lines)

![Knowledge Distillation Pipeline](../diagrams/context-graph-distillation.mmd)

The `ExperienceBridge` syncs experience memory records to the context graph:

- **Lessons → INSIGHT nodes:** Each `LessonLearned` becomes an INSIGHT node with `knowledge_source=trace`, confidence from lesson confidence, and `INSIGHT_ABOUT` edges to relevant capabilities/workflows.
- **Playbooks → PRACTICE nodes:** Each `Playbook` becomes a PRACTICE node with `PRACTICE_USES` edges to required systems/tools.

### 4.3 Manual Entry

Source: `src/agent_kernel/context_graph/extractors/manual_extractor.py` (119 lines)

The `ManualExtractor` handles direct knowledge creation via the MCP `knowledge.add` tool:

- Validates property schemas against the target `NodeType`
- Sets `knowledge_source=manual`, `confidence=1.0`
- Creates edges for specified relationships

---

## 5. Query and Scoring

Source: `src/agent_kernel/context_graph/query.py` (418 lines)

### 5.1 ContextGraphQueryService

| Method | Signature | Purpose |
|---|---|---|
| `query(q)` | `ContextGraphQuery → ContextGraphQueryResult` | General relevance-weighted search |
| `find_relevant_knowledge(intent, limit)` | `str, int → list[ScoredNode]` | Keyword search over knowledge nodes |
| `find_similar_trajectories(intent, limit)` | `str, int → list[ScoredNode]` | Episodic memory search |
| `get_domain_context(domain_id, depth)` | `str, int → TypedGraphSlice` | Subgraph retrieval for a domain |
| `get_entity_history(entity_node_id)` | `str → list[ScoredNode]` | Event clock: trajectories that touched entity |
| `record_access(node_id)` | `str → None` | Update freshness on context inclusion |

### 5.2 Scoring Algorithms

**Knowledge node scoring** (`_score_node`, `query.py:331-390`):

```
relevance = keyword_score * freshness_score * confidence
```

Where:
- `keyword_score` = (matching keywords / total keywords) — title and description searched
- `freshness_score` = `FreshnessScore.effective_relevance(now)` — time-decay formula
- `confidence` = node's confidence value (0-1)

Filters applied before scoring:
- Tier filter: exclude COLD unless `include_cold=True`
- Confidence threshold: `min_confidence`
- Freshness threshold: `min_freshness`
- Tag intersection: at least one matching tag

**Trajectory scoring** (`find_similar_trajectories`, `query.py:150-210`):

```
relevance = (keyword_score * 0.6) + (recency_score * 0.3) + success_bonus
```

Where:
- `keyword_score` = keyword overlap with trajectory intent and outcome summary
- `recency_score` = `0.5 ^ (days_ago / 30.0)` — 30-day half-life exponential decay
- `success_bonus` = 0.1 if `outcome_status == "completed"`, else 0.0

### 5.3 Integration with Context Assembler

The `ContextAssembler.assemble_with_thinking()` calls the query service when graph expansion is enabled (Tier 2+):

| Source | Assembler Weight | `RefType` |
|---|---|---|
| Knowledge nodes | `score * 0.8` | `KNOWLEDGE` |
| Trajectory nodes | `score * 0.7` | `TRAJECTORY` |

Results are merged with document search, skills, and experience records, then deduplicated, ranked, and trimmed to budget.

---

## 6. Freshness and Tiering

Source: `src/agent_kernel/context_graph/freshness.py` (83 lines)

### 6.1 FreshnessScore Formula

Defined in `core/schemas/knowledge.py:49-108`:

```
effective_relevance = base_relevance * (1 - decay_rate) ^ days_since_last_touch
```

Where:
- `base_relevance`: initial relevance (default 1.0)
- `decay_rate`: per-day decay (default 0.01)
- `last_touch`: `max(last_accessed_at, last_reinforced_at)`

**Pinned nodes** are exempt from decay — always return `base_relevance`.

### 6.2 FreshnessCalculator Methods

| Method | Effect |
|---|---|
| `effective_relevance(freshness, now)` | Calculate current relevance with decay |
| `record_access(freshness)` | Update `last_accessed_at` and increment `access_count` |
| `record_reinforcement(freshness)` | Update `last_reinforced_at` (node validated/confirmed) |
| `determine_tier(freshness, hot_days, warm_days, now)` | Classify as HOT/WARM/COLD |

### 6.3 KnowledgeTier

| Tier | Condition | Description |
|---|---|---|
| `HOT` | `days_since_touch <= hot_days` (default 90) | Recently accessed, high relevance |
| `WARM` | `hot_days < days_since_touch <= hot_days + warm_days` (default 365) | Not recently accessed but still relevant |
| `COLD` | `days_since_touch > hot_days + warm_days` | Old, low access, candidate for compaction/pruning |

Pinned nodes are always classified as HOT regardless of access time.

---

## 7. Retention and Compaction

Source: `src/agent_kernel/context_graph/retention.py` (381 lines)

### 7.1 RetentionExecutor — 5-Phase Pipeline

![Retention Pipeline](../diagrams/context-graph-retention.mmd)

`RetentionExecutor.run_full()` executes five phases in order:

| Phase | Method | Description |
|---|---|---|
| 1 | `tier_knowledge_nodes()` | Reclassify all knowledge nodes as HOT/WARM/COLD |
| 2 | `prune_low_quality()` | Delete COLD nodes below confidence and relevance thresholds |
| 3 | `prune_auto_edges()` | Remove auto-extracted edges below confidence threshold |
| 4 | `compact_cold_nodes()` | Group COLD nodes by type, create SUMMARY nodes |
| 5 | `compact_old_trajectories()` | Remove decision event detail from old trajectories |

Returns a `RetentionReport` with counts:
- `TieringResult`: hot/warm/cold counts, transition count
- `PruneResult`: nodes and edges pruned
- `CompactionResult`: nodes compacted, summaries created, trajectories compacted
- `freshness_updated`: count of freshness scores recalculated

### 7.2 Compaction Strategies

Source: `src/agent_kernel/context_graph/compaction.py` (189 lines)

**DeterministicCompaction** — groups COLD knowledge nodes by type and merges:
- Creates a SUMMARY node with `SummaryProperties`
- Concatenates titles and descriptions from source nodes
- Aggregates source references and tags
- Sets `knowledge_source=compaction`
- Creates `SUMMARY_OF` edges to each original
- Marks originals with `superseded_by=summary_id`

Threshold: batches of 5+ COLD nodes per type.

**TrajectoryCompaction** — removes decision event detail from old trajectories:
- Deletes DECISION_EVENT nodes and their edges
- Preserves the TRAJECTORY node and its `TRAJECTORY_TOUCHED` edges
- Sets `compacted=True` on the trajectory properties

Threshold: configurable via `RetentionPolicy.trajectories.compact_after_days`.

### 7.3 RetentionPolicy Configuration

Referenced from `core/schemas/retention.py`:

```python
class KnowledgeRetentionPolicy:
    hot_days: int = 90
    warm_days: int = 365
    prune_low_confidence_below: float = 0.3
    prune_low_relevance_below: float = 0.05
    pinned_exempt: bool = True

class TrajectoryRetentionPolicy:
    compact_after_days: int = 90

class RetentionPolicy:
    knowledge: KnowledgeRetentionPolicy
    trajectories: TrajectoryRetentionPolicy
```

---

## 8. Type Registry

Source: `src/agent_kernel/context_graph/types.py` (163 lines)

The `TypeRegistry` tracks discovered node and edge types at runtime — a schema-from-output pattern:

| Method | Purpose |
|---|---|
| `record_type_usage(type_name, kind, context)` | Record that a type was used |
| `get_registered_types(kind)` | Get all registered types (node or edge) |
| `get_stats()` | Usage statistics per type |

This allows the system to discover which node/edge types are actually in use, independent of the enum definitions.

---

## 9. Lakebase Schema Design

This is a greenfield deployment on managed PostgreSQL. The schema is designed for the adjacency-list graph model with JSONB properties and native PostgreSQL features.

### 9.1 Design Rationale

We chose Lakebase because the context graph is data infrastructure and belongs in the same ecosystem as our SSOT:

| Decision | Rationale |
|---|---|
| **Adjacency-list model** | Two tables (nodes, edges) — simple, proven for our access patterns |
| **JSONB properties** | Schema-flexible per node type while keeping the table structure fixed |
| **GIN indexes** | Containment queries (`@>`) on properties are O(log n) with GIN |
| **TIMESTAMPTZ** | Real timestamp comparisons instead of string sorting |
| **ON DELETE CASCADE** | Automatic edge cleanup when nodes are removed |
| **ON CONFLICT upsert** | Idempotent writes for concurrent agent operations |
| **Unity Catalog** | Same governance as our SSOT — row-level access, lineage, audit |

### 9.2 Schema DDL

```sql
-- Nodes table with JSONB properties
CREATE TABLE nodes (
    node_id    TEXT PRIMARY KEY,
    node_type  TEXT NOT NULL,
    properties JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Edges table with explicit confidence and temporal columns
CREATE TABLE edges (
    edge_id    TEXT PRIMARY KEY,
    source_id  TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    target_id  TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    edge_type  TEXT NOT NULL,
    properties JSONB NOT NULL DEFAULT '{}',
    confidence REAL,
    valid_from TIMESTAMPTZ,
    valid_to   TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Type indexes
CREATE INDEX idx_nodes_type ON nodes(node_type);
CREATE INDEX idx_edges_source ON edges(source_id);
CREATE INDEX idx_edges_target ON edges(target_id);
CREATE INDEX idx_edges_type ON edges(edge_type);

-- JSONB GIN indexes for containment queries
CREATE INDEX idx_nodes_props ON nodes USING GIN (properties);
CREATE INDEX idx_edges_props ON edges USING GIN (properties);

-- Composite indexes for common query patterns
CREATE INDEX idx_edges_source_type ON edges(source_id, edge_type);
CREATE INDEX idx_edges_target_type ON edges(target_id, edge_type);

-- Knowledge tier queries
CREATE INDEX idx_nodes_tier ON nodes(node_type, (properties->>'tier'));

-- Edge deduplication
CREATE UNIQUE INDEX idx_edges_unique_triple
    ON edges(source_id, target_id, edge_type);

-- Auto-update timestamp trigger
CREATE OR REPLACE FUNCTION update_node_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER nodes_updated_at
    BEFORE UPDATE ON nodes
    FOR EACH ROW
    EXECUTE FUNCTION update_node_timestamp();
```

### 9.3 Key SQL Patterns

| Operation | SQL Pattern |
|---|---|
| Property text access | `properties->>'title'` |
| Property JSON access | `properties->'freshness'` |
| Containment check | `properties @> '{"tier":"hot"}'::jsonb` |
| Nested access | `properties->'freshness'->>'decay_rate'` |
| Upsert node | `INSERT INTO nodes ... ON CONFLICT (node_id) DO UPDATE SET ...` |
| Upsert edge | `INSERT INTO edges ... ON CONFLICT (source_id, target_id, edge_type) DO UPDATE SET ...` |
| Batch fetch | `WHERE node_id = ANY($1::text[])` |
| Subgraph CTE | `WITH RECURSIVE traversal ...` (native support, better optimizer) |
| Boolean in JSON | `(properties->>'pinned')::boolean` |
| Timestamp compare | Native `TIMESTAMPTZ` comparison |

### 9.4 LakebaseGraphStore Implementation Notes

**Connection pool:**
```python
pool = await asyncpg.create_pool(
    dsn=lakebase_dsn,
    min_size=2,
    max_size=10,
    command_timeout=30,
)
```

**Upsert pattern:**
```sql
INSERT INTO nodes (node_id, node_type, properties, created_at, updated_at)
VALUES ($1, $2, $3::jsonb, now(), now())
ON CONFLICT (node_id)
DO UPDATE SET
    node_type = EXCLUDED.node_type,
    properties = EXCLUDED.properties,
    updated_at = now();
```

**Edge upsert with unique triple:**
```sql
INSERT INTO edges (edge_id, source_id, target_id, edge_type, properties, created_at)
VALUES ($1, $2, $3, $4, $5::jsonb, now())
ON CONFLICT (source_id, target_id, edge_type)
DO UPDATE SET
    properties = EXCLUDED.properties;
```

**Batch operations:**
```sql
SELECT * FROM nodes WHERE node_id = ANY($1::text[]);

SELECT * FROM edges
WHERE source_id = ANY($1::text[])
   OR target_id = ANY($1::text[]);
```

**Transactions:** All multi-step operations (decomposition, compaction) use explicit transactions for atomicity.

### 9.5 Sync Tables for Delta Analytics

Lakebase Sync Tables replicate PostgreSQL tables to Delta Lake:

```sql
CREATE SYNC TABLE catalog.schema.graph_nodes
USING LAKEBASE
OPTIONS (
    source_table = 'nodes',
    connection = 'lakebase_connection'
);
```

This enables Spark SQL analytics on graph data, cross-joins with other Delta tables, and dashboard queries without impacting the operational store.

---

## 10. Observability and Metrics

### 10.1 Events Emitted

| Event Type | When | Payload |
|---|---|---|
| `TRAJECTORY_CREATED` | After trace decomposition | `trace_id`, `decision_events`, `entities_linked`, `co_occurrences` |
| `KNOWLEDGE_NODE_CREATED` | After knowledge ingestion | `node_id`, `node_type`, `knowledge_source` |
| `RETENTION_COMPLETED` | After retention run | `TieringResult`, `PruneResult`, `CompactionResult` |

### 10.2 Graph Health Metrics

| Metric | Source | Target |
|---|---|---|
| Total nodes | `graph_store.count_nodes()` | Monitor growth rate |
| Total edges | `graph_store.count_edges()` | Monitor growth rate |
| Nodes by type | `TypeRegistry.get_stats()` | Distribution analysis |
| Tier distribution | Retention executor | HOT/WARM/COLD balance |
| Query latency p50/p99 | `ContextGraphQueryResult.query_time_ms` | < 50ms / < 200ms |
| Decomposition time | Event payload | Monitor processing overhead |

---

## 11. File Reference

| File | Lines | Purpose |
|---|---|---|
| `context_graph/__init__.py` | 31 | Module exports |
| `context_graph/decomposer.py` | 365 | TraceDecomposer: trace → graph (C2, C5) |
| `context_graph/query.py` | 418 | ContextGraphQueryService: relevance-weighted retrieval (C6) |
| `context_graph/retention.py` | 381 | RetentionExecutor: tier, prune, compact pipeline (C9) |
| `context_graph/ingestion.py` | 263 | ContextGraphIngestion: multi-source orchestrator (C1, C3) |
| `context_graph/experience_bridge.py` | 234 | ExperienceBridge: lessons/playbooks → graph (C5) |
| `context_graph/compaction.py` | 189 | Deterministic + trajectory compaction (C5, C9) |
| `context_graph/types.py` | 163 | TypeRegistry: discovered type tracking (C10) |
| `context_graph/hooks.py` | 131 | ContextGraphHooks: wire trace events → ingestion (C2) |
| `context_graph/extractors/manual_extractor.py` | 119 | ManualExtractor: direct knowledge entry (C1) |
| `context_graph/extractors/trace_extractor.py` | 92 | TraceExtractor: entity extraction from traces (C3) |
| `context_graph/freshness.py` | 83 | FreshnessCalculator: time-decay utilities (C6, C9) |
| `memory/graph_store.py` | ~930 | GraphStore ABC + LakebaseGraphStore (C4) |
| `core/schemas/graph.py` | ~300 | NodeType, EdgeType, GraphNode, GraphEdge |
| `core/schemas/knowledge.py` | ~370 | FreshnessScore, property models, DecompositionResult |
| **Total** | **~4,070** | |

---

## 12. Related Documents

These documents provide additional detail. Content is not duplicated here.

| Document | Relevant Content |
|---|---|
| [White Paper](21-agent-context-graph-whitepaper.md) | L1: Business case and capability model |
| [POC Proposal](22-agent-context-graph-poc.md) | L2: Greenfield POC with MarTek, success criteria, capability cards |
| [Core Schemas](01-schemas.md) Section J | Graph ontology Pydantic models (GraphNode, GraphEdge) |
| [Core Schemas](01-schemas.md) Section N | Experience memory schemas (ExperienceCase, LessonLearned) |
| [Memory Subsystem](02-memory.md) | Memory store architecture |
| [Context Assembler](04-context.md) | Context assembly internals |
| [Trace Analysis](09-trace-analysis.md) | Trace decomposition design rationale |
| [Universal Context System](17-universal-context-system.md) | Entity model, experience memory |
| [Context Memory Taxonomy](18-context-memory-taxonomy.md) | 9-layer memory taxonomy |
| [Design Overview](00-overview.md) | Agent Kernel design principles |
