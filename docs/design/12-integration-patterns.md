# Integration Patterns Guide

> **Core Principle:** Obsidian vault stays the source-of-truth. The agent system maintains derived indexes (graph + vector + task index + traces) that can always be rebuilt.

> **Flexibility Rule:** Integrations to external systems (Obsidian, Calendar, task managers) are NOT tied directly to the kernel but built as **extensible agents or workflows** that can be split out as needed.

---

## The Golden Path: Obsidian-First Automation

```
┌─────────────────────────────────────────────────────────────────┐
│                    OBSIDIAN VAULT (Canonical)                    │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      INDEXER DAEMON                              │
│  - Watches vault                                                 │
│  - Maintains: graph nodes/edges, vector chunks, task index      │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    ENRICHMENT WORKER                             │
│  - Auto tags/classification to auto.* namespace                  │
│  - Suggestions, NOT destructive edits                           │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   SYNTHESIS WORKFLOWS                            │
│  - Daily review, weekly review, stale TODO sweep                 │
│  - Run on schedule                                               │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│               EXTERNAL WRITES (Approval-Gated)                   │
│  - Calendar events (Google, Outlook, etc.)                       │
│  - Task sync (external task managers)                            │
│  - All emit DecisionTraces                                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Pattern: Source-of-Truth + Derived Indexes

When you create or edit a note in Obsidian, the system should:

1. **Detect** the change (file watcher)
2. **Ingest** the note (parse metadata/content)
3. **Update** derived representations:
   - Graph node + edges
   - Vector chunks/embeddings
   - Extracted tasks/entities
4. **Optionally Enrich** (LLM auto-tag/classify/summarize)
5. **Log** the entire run as a `DecisionTrace`

### Recommended Triggers (Use Both)

| Trigger | Purpose | Details |
|---------|---------|---------|
| **File Watcher** (Primary) | Real-time detection | Watch vault folder for create/modify/rename/delete. Debounce 10-30s after last modification. |
| **Reconciliation Job** (Safety Net) | Catch missed events | Nightly scan: compute file hash/mtime, ensure indexes match. Fixes missed watcher events, git rebases, bulk edits. |
| **Manual Command** (Optional) | User-initiated | "Send note to Agent System (Enrich now)" for immediate classification. |

---

## Data Store Separation

**Rule of thumb:**
- **Document Store** = "what it says"
- **Graph Store** = "what it is and how it connects"
- **Vector Store** = "what it means / similar to"

### 1. Document Store (Canonical Text Snapshot)

Store:
- Full markdown content
- Content hash
- Extracted metadata (frontmatter/properties, tags)

**Why:** Retrieval, reproducibility, and re-indexing.

### 2. Graph Store (Structure + Provenance)

**Do NOT store full note text in the graph.** Use it for structure and relationships only.

Graph nodes should include:
```yaml
node_type: Note
properties:
  note_id: "note_01J..."    # Stable ID (never changes)
  path: "/projects/agent-system/overview.md"
  title: "Agent System Overview"
  created_at: "2026-01-14T10:00:00Z"
  modified_at: "2026-01-14T15:30:00Z"
  tags: [project/agent-system, architecture]
  content_hash: "abc123..."  # Links to document store
```

Graph edges:
- `LINKS_TO` - Wikilinks between notes
- `TAGGED_WITH` - Note → Tag
- `MENTIONS` - Note → Entity (person, system)
- `HAS_TASK` - Note → Task
- `BELONGS_TO_PROJECT` - Note → Project

### 3. Vector Store (Semantic Search)

- Split note into chunks (headings/paragraph blocks)
- Embed chunks and store with `(note_id, chunk_id, offsets, hash)`
- Powers "find relevant notes" for context assembly

---

## Tags: Human vs Machine Separation

**Critical:** Separate human tags from machine tags to prevent vault pollution.

### Recommended Frontmatter Structure

```yaml
---
id: note_01JXYZ           # Stable ID (generate once, never change)
type: meeting             # Human or template-driven
project: agent-system
tags: [project/agent-system, meeting]  # Human tags

auto:
  tags: [workflow, memory, mcp]
  class: "architecture"
  confidence: 0.82
  summary: "Discussed kernel/tool-broker design and Obsidian ingestion."
  entities:
    people: [Alice]
    systems: [Obsidian, Google Calendar]
  extracted_on: 2026-01-14
---
```

| Namespace | Who Sets It | Examples |
|-----------|-------------|----------|
| `tags:` | Human (you) | `#project/agent-system`, `#meeting`, `#inbox` |
| `auto.tags:` | LLM enrichment | `[workflow, architecture, memory]` |
| `auto.class:` | LLM classification | `"meeting"`, `"spec"`, `"daily"` |
| `auto.summary:` | LLM summary | Short description |
| `auto.entities:` | LLM extraction | People, systems, concepts |

---

## Enrichment Pipeline

### Three-Stage Processing

```
Note Changed
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 1: Deterministic Parse (Cheap, Immediate)            │
│  - File path, title, created/modified                       │
│  - Obsidian tags (#...)                                     │
│  - Properties/frontmatter                                   │
│  - Wikilinks [[...]]                                        │
│  - Headings, checkboxes                                     │
│  → Update graph edges (links, tags, tasks)                  │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 2: Embedding (Moderate Cost)                         │
│  - Chunk note by headings/paragraphs                        │
│  - Generate embeddings                                      │
│  - Store in vector store                                    │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│  STAGE 3: LLM Enrichment (Expensive, Async)                 │
│  - Classify note type (meeting/idea/reference/daily/spec)  │
│  - Propose tags (auto.tags)                                 │
│  - Detect project association                               │
│  - Extract tasks/action items                               │
│  - Generate summary and key claims                          │
│  - Optionally propose backlinks                             │
└─────────────────────────────────────────────────────────────┘
```

### Two Modes: Suggest vs Auto-Apply

| Mode | Behavior | Use For |
|------|----------|---------|
| **Suggest** (default) | Write `auto.*` fields only, require approval for human fields | Tags, moves, renames |
| **Auto-apply** (safe subset) | Automatic updates | `auto.*` fields, adding stable ID |

### Idempotency

Use `content_hash + enrichment_version`:
- If unchanged → skip re-processing
- If changed → write new `auto.extracted_on`, update indexes

---

## Stable IDs

**Paths change. Titles change. IDs should not.**

### Rules

1. If `id` missing in frontmatter → generate once (ULID) and write it
2. Maintain mapping table: `note_id ↔ path ↔ hash`
3. Never regenerate an ID for an existing note
4. Use `id` as the primary key in all indexes

### ID Format

```yaml
id: note_01JXYZABC123...   # Prefixed ULID
```

---

## Project Bundles

A **Project Bundle** is a deterministic context packet builder for a specific project:

```yaml
project_bundle:
  project_id: agent-system
  
  # Manually curated (highest priority)
  pinned_notes:
    - docs/design/00-overview.md
    - AGENTS.md
  
  # Dynamic sources
  recent_notes:
    max: 10
    days: 7
  
  open_tasks:
    max: 20
  
  upcoming_calendar:
    max: 5
    days: 7
  
  key_decisions:
    # Recent DecisionTrace artifacts
    max: 5
```

This provides consistent, high-signal context for agents without relying only on embeddings.

---

## Lifecycle States

Add lifecycle states to notes for better automation:

```yaml
state: inbox | active | evergreen | archived
```

**Automation rules:**
- Only auto-classify `inbox` notes
- Only show `active` notes in daily reviews
- Exclude `archived` from context assembly (unless explicit)

---

## TODO Patterns

### Pattern A: Obsidian Checkboxes as Source-of-Truth (Recommended)

1. Parse `- [ ]` items from notes
2. Create `Task` nodes in graph:
   - `task_id`: `(note_id + line_signature)`
   - `status`, `due_date` (if present), `tags`
3. Create edge: `TASK_CREATED_FROM → Note`

**Why:** Stays close to your writing workflow, extremely robust.

### Pattern B: Dual-Write to External System (Optional)

If you need reminders and mobile capture:
1. Keep tasks in Obsidian as source
2. Mirror "approved tasks" outward to external system
3. **Use approval gates** - don't mirror everything automatically

---

## External System Integration Patterns

### Calendar: Draft → Approval → Create

Calendar providers (Google, Outlook, etc.) expose APIs for event management.

**Pattern:**
```
Agent proposes draft → You approve → Executor calls Calendar API
```

1. Agent proposes calendar block as draft artifact in trace
2. You approve (or auto-approve for low-risk rules)
3. Executor calls calendar API to create event
4. Trace records the action

**Why:** Avoids surprise calendar spam, keeps audit trail.

---

## NotebookLM Considerations

### What NotebookLM Does
- AI research tool for analyzing sources and synthesis
- Source-grounded Q&A
- Summarization across corpus
- Citations to sources

### What Your System Does (Different Focus)
- Workflow + automation engine
- Tasks, reminders, calendar integration
- Multi-agent runs with traces
- Programmatic, not just research UI

### Integration Decision Tree

| Scenario | Recommendation |
|----------|----------------|
| Want manual research surface for projects | NotebookLM can complement (export bundle, get synthesis, paste back) |
| Want programmatic integration | NotebookLM Enterprise has APIs for notebook management |
| Want cohesive, self-hosted | Build capabilities inside kernel (doc store + vector + graph + synthesis workflows) |

**Recommendation:** Building capabilities inside your kernel is the more cohesive path unless you specifically need NotebookLM's research UI.

---

## Event Queue Pattern

Even locally, structure processing like this:

```
File event → Enqueue NOTE_CHANGED(note_id, hash)
                          │
                          ▼
                   ┌──────────────┐
                   │ Job Queue    │
                   ├──────────────┤
                   │ IngestJob    │
                   │ GraphUpdateJob│
                   │ EmbedJob     │
                   │ EnrichJob    │
                   └──────────────┘
                          │
                          ▼
              Debounced Processing
              (rapid edits don't spam)
```

### Scheduled Synthesis Triggers

| Frequency | Job |
|-----------|-----|
| Daily | Find unclosed TODOs in notes updated last 7 days |
| Weekly | Summarize project progress + next actions |
| Monthly | Stale notes review |

---

## Implementation Checklist

### Phase 1: Core Indexing ✓
- [x] Implement file watcher trigger for vault (`VaultWatcher`)
- [x] Parse Obsidian frontmatter and wikilinks (`ObsidianVault`)
- [x] Generate and persist stable IDs (`note_01J...` format)
- [x] Create graph nodes for notes with v1.0.1 ontology (`NodeType.NOTE`)
- [x] Create edges from wikilinks (`EdgeType.NOTE_LINKS_TO_NOTE`)
- [x] Create edges from tags (`EdgeType.NOTE_TAGGED_WITH_TAG`)
- [x] Implement eventual consistency tracking (`IndexStateStore`)

### Phase 2: Enrichment ✓
- [x] Chunk notes for vector embedding (`VaultIndexer._chunk_content`)
- [x] Implement Stage 3 LLM enrichment workflow (`EnrichmentService`)
- [x] Write `auto.*` fields to frontmatter (`VaultIndexer._inject_auto_fields`)
- [x] Support "auto-apply" for stable IDs (safe subset)

### Phase 3: Task Integration ✓
- [x] Parse `- [ ]` checkboxes as tasks (`TaskParser`)
- [x] Create `Task` nodes with back-references (`NOTE_HAS_TASK` edge)
- [x] Integrate task extraction in VaultIndexer pipeline
- [x] Implement task sync workflow (`TaskSyncAdapter`, `TaskSyncService`)

### Phase 4: External Systems ✓
- [x] Implement approval-gated calendar creation (`CalendarAdapter`, `CalendarSyncService`)
- [ ] Implement concrete adapters (Google Calendar, etc.)

---

## Implementation Classes (v1.0.1)

### VaultIndexer

**Location:** `agent_kernel.services.vault_indexer`

Core class for indexing Obsidian notes into derived stores.

```python
from agent_kernel.services import VaultIndexer, IndexStateStore
from agent_kernel.tools.builtin.obsidian import ObsidianVault

# Create indexer with all stores
indexer = VaultIndexer(
    vault=ObsidianVault("/path/to/vault"),
    document_store=doc_store,
    graph_store=graph_store,
    vector_store=vector_store,
    embedding_service=embed_service,
    index_state_store=IndexStateStore("data/index_state.db"),  # v1.0.1
    enrichment_service=enrichment_service,  # v1.0.1: LLM enrichment
    enable_enrichment=True,                  # v1.0.1: Enable auto.* fields
)

# Index a single note
result = await indexer.index_note("projects/my-note.md")
# -> IndexResult(note_id="note_01J...", action="created", graph_updated=True)

# Index entire vault
summary = await indexer.index_folder(force=False)
# -> IndexSummary(total=150, created=5, updated=10, unchanged=135)

# Reconcile indexes with vault
results = await indexer.reconcile(dry_run=True)
# -> {"missing_in_index": [...], "orphaned_in_index": [...]}
```

**Key Features:**
- Generates stable IDs (`note_01J...`) and persists to frontmatter
- Uses v1.0.1 graph ontology (`NodeType`, `EdgeType`)
- Adds provenance metadata (`extracted_by: vault_indexer`)
- Tracks index state for eventual consistency
- Writeback safety: checks mtime before modifying files
- LLM enrichment: generates `auto.*` tags, classification, and summary
- Task extraction: parses checkboxes into graph nodes

### VaultWatcher

**Location:** `agent_kernel.services.vault_watcher`

Watches vault for changes and triggers indexing with debounce.

```python
from agent_kernel.services import VaultWatcher, create_vault_watcher

# Create watcher with stores
watcher = VaultWatcher(
    vault_path="/path/to/vault",
    document_store=doc_store,
    graph_store=graph_store,
    vector_store=vector_store,
    embedding_service=embed_service,
    index_state_store=index_state_store,
    debounce_seconds=10.0,    # Wait for file stability
    batch_interval=30.0,       # Process accumulated changes every 30s
)

# Register callback for index completion
watcher.on_index_complete(lambda summary: print(f"Indexed {summary.total_notes}"))

# Start watching (runs in background)
await watcher.start()

# Run full sync (e.g., on startup)
summary = await watcher.full_sync(force=False)

# Stop watching
await watcher.stop()
```

**Key Features:**
- Polling-based file watcher (cross-platform)
- Debouncing to prevent rapid-edit spam
- Batch processing of accumulated changes
- Filters to `.md` files, ignores `.obsidian/` and `.trash/`

### IndexStateStore

**Location:** `agent_kernel.services.index_state`

Tracks indexing status across stores for eventual consistency.

```python
from agent_kernel.services import IndexStateStore, IndexStatus

store = IndexStateStore("data/index_state.db")

# Check if note is fully indexed
state = store.get("note_01JXYZ...")
if state and state.is_fully_indexed:
    print("Note ready for context assembly")

# List notes needing reindexing
pending = store.list_pending(entity_type="note", limit=50)
for state in pending:
    print(f"{state.entity_id}: doc={state.doc_status}, graph={state.graph_status}")

# Get statistics
stats = store.get_statistics()
# -> {"total": 150, "fully_indexed": 140, "needs_indexing": 8, "failed": 2}
```

### EnrichmentService (v1.0.1)

**Location:** `agent_kernel.services.enrichment`

LLM-powered enrichment for auto.* metadata fields.

```python
from agent_kernel.services import EnrichmentService, EnrichmentResult
from agent_kernel.services import create_llm_service

# Create enrichment service
llm = create_llm_service(provider="openai")
enricher = EnrichmentService(
    llm_service=llm,
    classifications=["meeting", "architecture", "notes", "reference"],
    max_content_length=4000,
    temperature=0.3,
)

# Enrich a single note
result = await enricher.enrich(
    content="# Architecture Discussion\n\nWe discussed the kernel design...",
    title="Kernel Architecture",
    existing_tags=["project/agent-kernel"],
)

# Result contains auto.* fields
print(result.auto_tags)       # ["architecture", "design"]
print(result.auto_class)      # "architecture"
print(result.auto_summary)    # "Discussion of kernel design patterns"
print(result.tag_confidence)  # 0.85

# Convert to frontmatter format
frontmatter = result.to_frontmatter()
# -> {"tags": ["architecture", "design"], "class": "architecture", ...}
```

**Key Features:**
- Generates `auto.tags`, `auto.class`, `auto.summary` from content
- Uses structured JSON prompts for reliable parsing
- Normalizes tags to lowercase-hyphenated format
- Rejects classifications not in allowed list
- Handles markdown code blocks in LLM responses
- Batch enrichment with configurable concurrency

**Integration with VaultIndexer:**
```python
# Enable enrichment in VaultIndexer
indexer = VaultIndexer(
    vault=vault,
    document_store=doc_store,
    graph_store=graph_store,
    enrichment_service=enricher,
    enable_enrichment=True,  # Enable LLM enrichment
)

# index_note() will now also:
# 1. Call enrichment_service.enrich() on content
# 2. Write auto.* fields to frontmatter
# 3. Update index state with enriched_at timestamp
result = await indexer.index_note("path/to/note.md")
print(result.enriched)    # True
print(result.auto_tags)   # ["architecture", "design"]
```

### TaskParser (v1.0.1)

**Location:** `agent_kernel.services.task_parser`

Extracts TODO items from markdown checkboxes with metadata.

```python
from agent_kernel.services import TaskParser, extract_tasks

# Parse tasks from content
parser = TaskParser(note_id="note_01JXYZ...")
tasks = parser.parse(content)

for task in tasks:
    print(f"{task.text} - {task.status.value}")
    print(f"  Due: {task.due_date}, Priority: {task.priority.value}")
    print(f"  Tags: {task.tags}, Contexts: {task.contexts}")

# Convenience function
tasks = extract_tasks(content, note_id="note_01JXYZ...")
```

**Supported Formats:**
- Checkboxes: `- [ ]` (incomplete), `- [x]` (complete), `* [ ]`
- Due dates: `📅 2026-01-15`, `due:2026-01-15`, `[due:: 2026-01-15]`
- Priority: `🔺` high, `🔸` medium, `🔻` low, or `(A)`, `(B)`, `(C)`
- Tags: `#tag-name` in task text
- Contexts: `@context` in task text

**Graph Integration:**
When VaultIndexer processes a note, it automatically:
1. Extracts all tasks using TaskParser
2. Creates `Task` nodes in the graph (`NodeType.TASK`)
3. Creates `NOTE_HAS_TASK` edges linking notes to tasks
4. Creates tag edges for any `#tags` in task text

### Graph Ontology (v1.0.1)

**Location:** `agent_kernel.core.schemas.graph`

Typed enums for graph nodes and edges:

```python
from agent_kernel.core.schemas.graph import NodeType, EdgeType

# Node types
NodeType.NOTE          # Obsidian note
NodeType.TAG           # Tag from frontmatter or inline
NodeType.TASK          # Extracted TODO item
NodeType.PROJECT       # Project entity
NodeType.TRACE         # DecisionTrace
NodeType.CALENDAR_EVENT
NodeType.PERSON

# Edge types
EdgeType.NOTE_LINKS_TO_NOTE       # [[wikilink]]
EdgeType.NOTE_TAGGED_WITH_TAG     # #tag or tags: []
EdgeType.NOTE_HAS_TASK            # Contains - [ ] item
EdgeType.TASK_BELONGS_TO_PROJECT
EdgeType.TRACE_USED_CONTEXT       # Trace -> Note (input)
EdgeType.TRACE_PRODUCED_ARTIFACT  # Trace -> Note (output)
```

### TaskSyncAdapter (v1.0.1)

**Location:** `agent_kernel.integrations.task_sync`

Abstract adapter for syncing tasks to external systems (Linear, Jira, etc.).

```python
from agent_kernel.integrations import (
    TaskSyncAdapter,
    TaskSyncService,
    MemoryTaskAdapter,
    ExternalTask,
)

# Create adapter (or implement your own)
adapter = MemoryTaskAdapter()  # For testing
# adapter = LinearTaskAdapter(api_token="...")  # Example

# Create sync service with graph store
sync_service = TaskSyncService(
    graph_store=graph_store,
)
sync_service.register_adapter(adapter)

# Sync tasks to external system
summary = await sync_service.sync_to_adapter("memory")
print(f"Created: {summary.created}, Updated: {summary.updated}")

# For approval-gated external writes, use the workflow:
# result = await workflow_runner.run("task_sync", approval_tokens={...})
```

**Key Features:**
- Abstract `TaskSyncAdapter` base class for external systems
- `MemoryTaskAdapter` for testing
- `TaskSyncService` orchestrates sync with graph store
- Approval-gated external writes (adapters set `requires_approval_for_writes`)
- Maps between kernel task format and external format
- Tracks sync state for idempotency

**Implementing a New Adapter:**
```python
from agent_kernel.integrations import TaskSyncAdapter, ExternalTask, SyncResult

class LinearTaskAdapter(TaskSyncAdapter):
    @property
    def adapter_id(self) -> str:
        return "linear"

    @property
    def display_name(self) -> str:
        return "Linear"

    async def create_task(self, task: ExternalTask) -> SyncResult:
        # Call Linear API...
        pass

    # ... implement other abstract methods
```

### CalendarAdapter (v1.0.1)

**Location:** `agent_kernel.integrations.calendar_sync`

Abstract adapter for syncing calendar events (Google Calendar, etc.).

```python
from agent_kernel.integrations import (
    CalendarAdapter,
    CalendarSyncService,
    MemoryCalendarAdapter,
    CalendarEvent,
)

# Create adapter (or implement your own)
adapter = MemoryCalendarAdapter()  # For testing
# adapter = GoogleCalendarAdapter(credentials=...)  # Future

# Create sync service with graph store
sync_service = CalendarSyncService(graph_store=graph_store)
sync_service.register_adapter(adapter)

# Import events from external calendar (PULL - no approval)
summary = await sync_service.import_events("memory")
print(f"Imported: {summary.created} events")

# Create event in external calendar (PUSH - requires approval)
event = CalendarEvent(
    external_id="",
    title="Team Meeting",
    start=datetime(2026, 1, 20, 10, 0),
    end=datetime(2026, 1, 20, 11, 0),
)
result = await sync_service.create_external_event("google", event)
if result.requires_approval:
    print("Event creation needs user approval")
```

**Key Features:**
- Abstract `CalendarAdapter` base class
- `MemoryCalendarAdapter` for testing
- `CalendarSyncService` orchestrates sync with graph store
- **All external writes require approval** (creates CALENDAR_EVENT nodes)
- Links events to related notes and tasks via graph edges
- Time range filtering for efficient sync

**Approval Workflow:**

Calendar writes are always approval-gated because they create external side effects:

```python
# Agent proposes calendar creation
action = create_calendar_event_action(event, adapter_id="google")
plan = Plan(actions=[ActionRequest(**action)])

# Executor requires approval
trace = await executor.execute(plan, context, profile, engine_id)
if trace.outcome.status == OutcomeStatus.NEEDS_APPROVAL:
    # Show user the pending approval
    pending = approval_gate.list_pending()
    # ... user approves ...
    approval_gate.approve(pending[0].approval_id)
```

### Writeback Safety

The `VaultIndexer._inject_stable_id()` implements safe frontmatter modification:

1. **Read with mtime check** - Record file mtime before reading
2. **Parse existing frontmatter** - Use safe YAML loader
3. **Preserve structure** - Add `id` as first field, keep key order
4. **Verify mtime unchanged** - Abort if file modified during processing
5. **Write with safe YAML** - Block style, unicode support

```python
# Before
---
title: My Note
tags: [project]
---

# After (id added at top)
---
id: note_01JXYZABC...
title: My Note
tags: [project]
---
```

---

## Summary Rules

1. **Obsidian = Source of Truth** - Derived indexes can always be rebuilt
2. **Separate Human from Machine** - Use `auto.*` namespace for LLM metadata
3. **Stable IDs** - Never rely on paths or titles as identifiers
4. **Approval Gates for External Writes** - Calendar, external tasks, etc.
5. **Debounce + Idempotency** - Prevent processing storms
6. **Trace Everything** - All enrichment runs emit `DecisionTrace`
7. **Pluggable Integrations** - Not tied to kernel, built as workflows/agents
8. **Track Index State** - Use `IndexStateStore` for eventual consistency (v1.0.1)