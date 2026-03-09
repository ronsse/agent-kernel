# 17. Universal Context System (v1.0.4)

This document describes the v1.0.4 upgrade that transforms the kernel from an Obsidian-first knowledge system to a **universal context system** capable of ingesting context from any source.

---

## Overview

The v1.0.4 patch adds three foundational building blocks:

1. **Universal Entity Model** - Everything becomes an `Entity` with `{source_id, entity_type, entity_id}`
2. **Experience Memory** - Track decisions → outcomes → lessons for learning
3. **Retention & Compaction** - Policy-driven growth management

---

## 1. Universal Entity Model

### Problem
The v1.0.3 system was note-centric. To work with Slack messages, emails, calendar events, tickets, etc., we needed a generalized entity model.

### Solution

#### EntityRef Schema
```python
class EntityRef(VersionedModel):
    source_id: str      # "obsidian", "slack", "outlook", "github"
    entity_type: str    # "note", "message", "email", "event", "ticket"
    entity_id: str      # stable ID within source
    uri: str | None     # canonical URI/path
    canonical_id: str | None  # kernel-owned global ID (ent_{ulid})
    canonical_hash: str | None  # content hash
    occurred_at: datetime | None
    recorded_at: datetime | None
    metadata: dict[str, Any]
```

#### EntityView Schema
Entities can have multiple views for different retrieval purposes:

```python
class EntityViewType(str, Enum):
    SUMMARY = "summary"           # Entity-level summary
    CHUNK = "chunk"               # Passage/chunk
    TITLE = "title"
    METADATA = "metadata"
    THREAD_SUMMARY = "thread_summary"
    TRANSCRIPT = "transcript"
    DECISION_SUMMARY = "decision_summary"
    LESSON = "lesson"
    PLAYBOOK = "playbook"
```

### ContextRef Updates
The `ContextRef` schema now includes optional entity fields:
- `entity: EntityRef | None` - Full entity reference (preferred)
- `source_id: str | None`
- `entity_type: str | None`
- `entity_id: str | None`

Upcasting rules automatically populate these from legacy `RefType` values.

### Vector ID Convention
```
{canonical_id or entity_id}:{view_type}:{segment_id?}
```

Example: `ent_01ABC:summary` or `ent_01ABC:chunk:0`

---

## 2. Entity Storage

### EntityStore Interface
```python
class EntityStore(ABC):
    def register_entity(entity: EntityRef) -> str: ...
    def get_entity(canonical_id: str) -> EntityRef | None: ...
    def get_entity_by_source(source_id, entity_type, entity_id) -> EntityRef | None: ...
    def put_view(view: EntityView) -> None: ...
    def get_view(view_id: str) -> EntityView | None: ...
    def record_access(canonical_id: str) -> None: ...
```

### Database Tables

```sql
-- Entity mapping
CREATE TABLE entity_map (
    canonical_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    uri TEXT,
    canonical_hash TEXT,
    occurred_at TEXT,
    recorded_at TEXT NOT NULL,
    metadata_json TEXT,
    last_accessed_at TEXT,
    access_count_30d INTEGER DEFAULT 0,
    UNIQUE(source_id, entity_type, entity_id)
);

-- Entity views
CREATE TABLE entity_views (
    view_id TEXT PRIMARY KEY,
    canonical_id TEXT NOT NULL,
    view_type TEXT NOT NULL,
    segment_id TEXT,
    content TEXT,
    content_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT
);
```

---

## 3. Experience Memory

### Design Goals
- Persist user feedback about outcomes (good/bad)
- Mine lessons from traces + evaluations
- Retrieve similar cases during future runs
- Keep deterministic enforcement (lessons influence planning, not tool execution)

### Schemas

#### OutcomeEvaluation
```python
class OutcomeLabel(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    REGRESSION = "regression"
    UNKNOWN = "unknown"

class FailureCategory(str, Enum):
    MISRETRIEVAL = "misretrieval"
    MISPLANNING = "misplanning"
    TOOL_ERROR = "tool_error"
    POLICY_BLOCK = "policy_block"
    HALLUCINATION = "hallucination"
    UX = "ux"
    OTHER = "other"

class OutcomeEvaluation(VersionedModel):
    evaluation_id: str
    trace_id: str
    label: OutcomeLabel
    rating: int | None  # 1-5
    failure_category: FailureCategory | None
    feedback: str | None
    evaluator: str  # "user", "auto", "review_agent"
```

#### ExperienceCase
Compacted, retrievable case memory:

```python
class ExperienceCase(VersionedModel):
    case_id: str
    trace_id: str
    intent: str
    context_summary: str | None
    plan_summary: str | None
    outcome_summary: str | None
    workflow_id: str | None
    agent_profile_id: str | None
    capability_names: list[str]
    sources_used: list[str]
    entity_types_used: list[str]
    label: OutcomeLabel
    rating: int | None
    failure_category: FailureCategory | None
```

#### LessonLearned
```python
class LessonScope(BaseModel):
    workflow_id: str | None
    capability_name: str | None
    entity_type: str | None
    project_id: str | None

class LessonLearned(VersionedModel):
    lesson_id: str
    title: str
    lesson_text: str
    scope: LessonScope
    source_trace_ids: list[str]
    source_case_ids: list[str]
    confidence: float  # 0.0-1.0
    status: Literal["active", "deprecated", "candidate"]
```

### Learning Loop
```
Traces → Evaluations → Cases → Lessons → Playbooks
```

**Hard rule:** All auto-generated lessons start as `status="candidate"`.

---

## 4. Playbooks

Playbooks are versioned behavioral patterns that define:
- What context must be present
- Expected output formats
- Known pitfalls and verification steps
- Suggested reasoning tier

### Schema
```python
class PlaybookSelector(BaseModel):
    workflow_id: str | None
    project_id: str | None
    intent_contains: list[str]
    capability_names: list[str]

class Playbook(VersionedModel):
    playbook_id: str
    name: str
    selectors: list[PlaybookSelector]
    required_entity_types: list[str]
    required_sources: list[str]
    output_format_refs: list[ContextRef]
    checklist: list[str]
    pitfalls: list[str]
    recommended_thinking_tier: int | None
    status: Literal["active", "deprecated", "candidate"]
```

### PlaybookResolver
Finds applicable playbooks based on:
- Workflow ID
- Project ID
- Intent keywords
- Capabilities being used

---

## 5. Retention & Compaction

### Retention Policy
```yaml
retention:
  traces:
    hot_days: 14      # Full traces
    warm_days: 90     # Compacted summaries
    cold_days: 365    # Cases/lessons only
  vectors:
    keep_summary_embeddings_days: 3650  # ~10 years
    keep_chunk_embeddings_days: 180
    max_total_vectors: 500000
  graph:
    keep_human_edges_days: 3650
    prune_auto_edges_below_confidence: 0.55
    prune_auto_edges_older_than_days: 365
```

### Compaction Jobs

| Job | Purpose |
|-----|---------|
| `TraceCompactorJob` | Compress old traces → ExperienceCase |
| `VectorPrunerJob` | Drop stale chunk embeddings |
| `GraphPrunerJob` | Remove low-confidence auto edges |
| `CacheJanitorJob` | Enforce cache TTL and size limits |

---

## 6. Graph Ontology Extensions

### New Node Types
```python
CASE = "case"
EVALUATION = "evaluation"
LESSON = "lesson"
PLAYBOOK = "playbook"
MESSAGE = "message"
THREAD = "thread"
EMAIL = "email"
TICKET = "ticket"
PULL_REQUEST = "pull_request"
CAPABILITY = "capability"
```

### New Edge Types
```python
TRACE_HAS_CASE = "trace_has_case"
TRACE_HAS_EVALUATION = "trace_has_evaluation"
CASE_YIELDED_LESSON = "case_yielded_lesson"
LESSON_APPLIES_TO_CAPABILITY = "lesson_applies_to_capability"
LESSON_APPLIES_TO_WORKFLOW = "lesson_applies_to_workflow"
PLAYBOOK_DERIVED_FROM_LESSON = "playbook_derived_from_lesson"
ENTITY_RELATED_TO_ENTITY = "entity_related_to"
```

---

## 7. Quality Gates

### New Gates (v1.0.4)

| Gate | Purpose |
|------|---------|
| `SourceConstraintEnforcementGate` | Ensures no forbidden content is stored |
| `ExperienceWarningGate` | Injects warnings from similar failures |
| `PlaybookCoverageGate` | Verifies playbook requirements are met |

---

## 8. CLI Commands

### Experience Commands
```bash
# Rate a trace
agent-kernel rate-trace <trace_id> --label success --rating 5
agent-kernel rate-trace <trace_id> --label failure --category misretrieval

# List evaluations
agent-kernel list-evals --since 7d
agent-kernel list-evals --label failure

# View case
agent-kernel show-case <case_id>

# List lessons
agent-kernel list-lessons --status active
```

### Retention Commands
```bash
# Compact old traces
agent-kernel compact-traces --older-than 14d
agent-kernel compact-traces --dry-run

# Prune vectors
agent-kernel prune-vectors --dry-run
agent-kernel prune-vectors --execute

# View status
agent-kernel retention-status
```

---

## 9. Migration Notes

### Backward Compatibility
- Existing `ContextRef` continues to work
- Vector metadata supports both legacy (`note_id`, `embedding_type`) and entity-based fields
- `HybridSearchService` supports both filter formats

### Upcasting
- `RefType.NOTE` → `source_id="obsidian"`, `entity_type="note"`
- `RefType.TASK` → `source_id="tasks"`, `entity_type="task"`
- `RefType.EVENT` → `source_id="calendar"`, `entity_type="calendar_event"`

---

## 10. Files Created/Modified

### New Files
| File | Purpose |
|------|---------|
| `core/schemas/entity.py` | EntityRef, EntityView, EntityViewType |
| `core/schemas/experience.py` | OutcomeEvaluation, ExperienceCase, LessonLearned, Playbook |
| `core/schemas/retention.py` | RetentionPolicy and sub-policies |
| `memory/entity_store.py` | EntityStore interface and SQLite implementation |
| `memory/experience_store.py` | ExperienceStore interface and SQLite implementation |
| `context/playbook_resolver.py` | PlaybookResolver for matching playbooks |
| `services/retention_jobs.py` | Compaction and pruning jobs |
| `configs/retention.yaml` | Default retention policy |
| `configs/playbooks/daily_checkin.yaml` | Sample playbook |

### Modified Files
| File | Changes |
|------|---------|
| `core/schemas/context.py` | Added entity fields to ContextRef |
| `core/schemas/graph.py` | Added experience node/edge types |
| `services/hybrid_search.py` | Entity-based search support |
| `context/gates.py` | Experience/playbook quality gates |
| `cli/main.py` | Experience and retention commands |

---

## References

- [Design Patch v1.0.4: Universal Context System](../AGENT_KERNEL_DESIGN_PATCH_v1.0.4_UNIVERSAL_ENTITY_EXPERIENCE_RETENTION.md)
- [12. Integration Patterns](12-integration-patterns.md)
- [14. Embedding Strategy](14-embedding-strategy.md)
