# Memory Subsystem

**Version:** 1.0.4  
**Status:** Implementation Phase

The memory subsystem provides persistent storage for all kernel data. It is **local-first** with pluggable backends.

## Version History

| Version | Additions |
|---------|-----------|
| 1.0.0 | Document, Vector, Graph stores |
| 1.0.1 | IndexStateStore for eventual consistency |
| 1.0.4 | **EntityStore** (universal entity registry), **ExperienceStore** (cases/lessons/playbooks) |

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      MEMORY SUBSYSTEM                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │  Document   │  │   Vector    │  │   Context   │              │
│  │   Store     │  │   Index     │  │   Graph     │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
│         │                │                │                      │
│         │                │                │                      │
│         └────────────────┼────────────────┘                      │
│                          │                                       │
│                          ▼                                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ Entity Store│  │  Event Log  │  │ Experience  │ (v1.0.4)     │
│  │  (v1.0.4)   │  │ (append-only)│  │   Store     │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 1) Document Store

**Purpose:** Stores raw content (notes, files, transcripts, summaries).

### Interface

```python
from abc import ABC, abstractmethod
from typing import Any

class DocumentStore(ABC):
    """Interface for document storage."""
    
    @abstractmethod
    async def put_document(
        self,
        doc_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> None:
        """Store a document."""
        ...
    
    @abstractmethod
    async def get_document(self, doc_id: str) -> Document | None:
        """Retrieve a document by ID."""
        ...
    
    @abstractmethod
    async def delete_document(self, doc_id: str) -> bool:
        """Delete a document. Returns True if existed."""
        ...
    
    @abstractmethod
    async def search_documents(
        self,
        query: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> list[Document]:
        """Search documents with optional FTS and filters."""
        ...
    
    @abstractmethod
    async def list_documents(
        self,
        filters: dict[str, Any] | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Document]:
        """List documents with pagination."""
        ...
```

### Document Model

```python
class Document(BaseModel):
    doc_id: str
    content: str
    content_hash: str  # SHA-256 of content
    doc_type: str  # "note", "transcript", "summary", etc.
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]
```

### What to Store

| Type | Description |
|------|-------------|
| Note snapshots | Versioned copies of notes |
| Task blocks | Extracted task content |
| Meeting transcripts | Audio transcriptions |
| Agent summaries | Generated summaries |
| Email content | Cached email bodies |

### Implementation: SQLite

```python
class SQLiteDocumentStore(DocumentStore):
    """SQLite-backed document store with FTS5."""
    
    # Table: documents
    # - doc_id TEXT PRIMARY KEY
    # - content TEXT
    # - content_hash TEXT
    # - doc_type TEXT
    # - created_at TEXT
    # - updated_at TEXT
    # - metadata JSON
    
    # FTS5 virtual table for full-text search
    # CREATE VIRTUAL TABLE documents_fts USING fts5(
    #     content,
    #     content='documents',
    #     content_rowid='rowid'
    # );
```

---

## 2) Vector Index

**Purpose:** Semantic retrieval via embeddings.

### Interface

```python
class VectorStore(ABC):
    """Interface for vector similarity search."""
    
    @abstractmethod
    async def upsert_embedding(
        self,
        item_id: str,
        vector: list[float],
        metadata: dict[str, Any],
        text: str | None = None,
    ) -> None:
        """Store or update an embedding."""
        ...
    
    @abstractmethod
    async def query_embedding(
        self,
        vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        min_score: float = 0.0,
    ) -> list[VectorResult]:
        """Find similar items."""
        ...
    
    @abstractmethod
    async def delete_embedding(self, item_id: str) -> bool:
        """Delete an embedding."""
        ...
    
    @abstractmethod
    async def get_embedding(self, item_id: str) -> list[float] | None:
        """Retrieve embedding vector for an item."""
        ...
```

### VectorResult Model

```python
class VectorResult(BaseModel):
    item_id: str
    score: float  # Similarity score
    metadata: dict[str, Any]
    text: str | None = None
```

### Implementation Options

| Backend | Use Case |
|---------|----------|
| **LanceDB** | Local-first, embedded, Rust-based (default) |
| **ChromaDB** | Alternative local option |
| **pgvector** | PostgreSQL integration |
| **Qdrant** | High-performance, production |

### LanceDB Implementation (Default)

```python
class LanceDBVectorStore(VectorStore):
    """LanceDB-backed vector store (embedded, Rust-based)."""
    
    def __init__(
        self,
        db_path: str = "./data/lancedb",
        table_name: str = "embeddings",
    ):
        self.db = lancedb.connect(db_path)
        self.table_name = table_name
    
    async def upsert_embedding(
        self,
        item_id: str,
        vector: list[float],
        metadata: dict[str, Any],
        text: str | None = None,
    ) -> None:
        data = {
            "id": item_id,
            "vector": vector,
            "text": text or "",
            **metadata,
        }
        try:
            table = self.db.open_table(self.table_name)
            table.delete(f"id = '{item_id}'")
            table.add([data])
        except FileNotFoundError:
            self.db.create_table(self.table_name, [data])
    
    async def query_embedding(
        self,
        vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        min_score: float = 0.0,
    ) -> list[VectorResult]:
        table = self.db.open_table(self.table_name)
        results = table.search(vector).limit(top_k).to_list()
        return [
            VectorResult(
                item_id=r["id"],
                score=1.0 - r["_distance"],
                metadata={k: v for k, v in r.items() if k not in ["id", "vector", "_distance"]},
                text=r.get("text"),
            )
            for r in results
            if 1.0 - r["_distance"] >= min_score
        ]
```

**Why LanceDB:**
- Truly embedded (no separate process)
- Rust-based for performance
- Simple API
- Supports filtering during search
- Apache Arrow format for efficiency

### Embedding Strategy

```python
class EmbeddingService:
    """Service for generating embeddings."""
    
    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding for text."""
        ...
    
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        ...
```

Embedding providers:
- OpenAI `text-embedding-3-small`
- Anthropic (via Voyage)
- Local models (sentence-transformers)

---

## 3) Context Graph Store

**Purpose:** Store nodes and edges representing relationships between entities.

### Interface

```python
class GraphStore(ABC):
    """Interface for context graph storage."""
    
    @abstractmethod
    async def upsert_node(self, node: GraphNode) -> None:
        """Insert or update a node."""
        ...
    
    @abstractmethod
    async def upsert_edge(self, edge: GraphEdge) -> None:
        """Insert or update an edge."""
        ...
    
    @abstractmethod
    async def get_node(self, node_id: str) -> GraphNode | None:
        """Get a node by ID."""
        ...
    
    @abstractmethod
    async def get_subgraph(
        self,
        seed_ids: list[str],
        depth: int = 2,
        filters: GraphFilters | None = None,
        time_range: tuple[datetime, datetime] | None = None,
    ) -> Subgraph:
        """Get a subgraph starting from seed nodes."""
        ...
    
    @abstractmethod
    async def query_graph(
        self,
        query: GraphQuery,
    ) -> list[GraphNode | GraphEdge]:
        """Execute a graph query."""
        ...
    
    @abstractmethod
    async def delete_node(self, node_id: str, cascade: bool = False) -> bool:
        """Delete a node (optionally with edges)."""
        ...
```

### Graph Models (v1.0.1 Ontology)

```python
class NodeType(str, Enum):
    """v1 node types - extensible but stable core."""
    NOTE = "note"
    TAG = "tag"
    TASK = "task"
    PROJECT = "project"
    TRACE = "trace"
    CALENDAR_EVENT = "calendar_event"
    PERSON = "person"

class GraphNode(VersionedModel):
    node_id: str
    node_type: NodeType
    name: str
    properties: dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime
    # Provenance (v1.0.1)
    extracted_by: str | None = None  # e.g., "vault_indexer"
    source_ref: str | None = None

class EdgeType(str, Enum):
    """v1 edge types - semantic relationships."""
    NOTE_LINKS_TO_NOTE = "note_links_to_note"
    NOTE_TAGGED_WITH_TAG = "note_tagged_with_tag"
    NOTE_HAS_TASK = "note_has_task"
    TASK_BELONGS_TO_PROJECT = "task_belongs_to_project"
    TRACE_USED_CONTEXT = "trace_used_context"
    TRACE_PRODUCED_ARTIFACT = "trace_produced_artifact"

class GraphEdge(VersionedModel):
    edge_id: str
    edge_type: EdgeType
    source_id: str
    target_id: str
    properties: dict[str, Any] = {}
    # Confidence for auto-extractions (v1.0.1)
    confidence: float | None = None  # 0.0-1.0
    # Validity interval (v1.0.1)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    created_at: datetime
    # Provenance (v1.0.1)
    extracted_by: str | None = None
    source_ref: str | None = None

class Subgraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
```

> **Rule (v1.0.1):** Auto-generated edges should set `confidence`; human-authored edges may omit it. Edges with `valid_to` in the past are considered historical.

### Implementation: SQLite

```python
class SQLiteGraphStore(GraphStore):
    """SQLite-backed graph store."""
    
    # Table: nodes
    # - node_id TEXT PRIMARY KEY
    # - node_type TEXT
    # - name TEXT
    # - properties JSON
    # - created_at TEXT
    # - updated_at TEXT
    
    # Table: edges
    # - edge_id TEXT PRIMARY KEY
    # - edge_type TEXT
    # - source_id TEXT REFERENCES nodes(node_id)
    # - target_id TEXT REFERENCES nodes(node_id)
    # - properties JSON
    # - created_at TEXT
    
    # Indexes for traversal
    # CREATE INDEX idx_edges_source ON edges(source_id);
    # CREATE INDEX idx_edges_target ON edges(target_id);
```

### Future: Neo4j Adapter

```python
class Neo4jGraphStore(GraphStore):
    """Neo4j-backed graph store for production."""
    
    # Cypher queries for operations
    # MERGE (n:Node {node_id: $node_id}) ...
```

---

## 4) Event Log

**Purpose:** Append-only immutable log of all system events.

### Interface

```python
class EventLog(ABC):
    """Interface for append-only event log."""
    
    @abstractmethod
    async def append(self, event: Event) -> None:
        """Append an event (immutable)."""
        ...
    
    @abstractmethod
    async def query(
        self,
        event_types: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        entity_id: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Query events."""
        ...
    
    @abstractmethod
    async def stream(
        self,
        since: datetime | None = None,
    ) -> AsyncIterator[Event]:
        """Stream events (for real-time)."""
        ...
```

### Event Model

```python
class EventType(str, Enum):
    # Trace events
    TRACE_CREATED = "trace.created"
    TOOL_CALLED = "tool.called"
    TOOL_SUCCEEDED = "tool.succeeded"
    TOOL_FAILED = "tool.failed"
    
    # Entity events
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_COMPLETED = "task.completed"
    NOTE_CREATED = "note.created"
    NOTE_UPDATED = "note.updated"
    
    # Workflow events
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"
    
    # Approval events
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_DENIED = "approval.denied"

class Event(VersionedModel):
    event_id: str  # ULID
    event_type: EventType
    entity_id: str | None = None
    entity_type: str | None = None
    # Time semantics (v1.0.1)
    occurred_at: datetime  # When it happened in reality
    recorded_at: datetime  # When kernel recorded it
    payload: dict[str, Any]
    metadata: dict[str, Any] = {}  # Tags, correlation IDs
    trace_id: str | None = None
```

> **Time Semantics (v1.0.1):** `occurred_at` is when the event actually happened (e.g., file modified time). `recorded_at` is when the kernel logged it. This distinction is critical for file watchers, backfills, and replays.

### Implementation

```python
class SQLiteEventLog(EventLog):
    """SQLite-backed append-only event log."""
    
    # Table: events
    # - event_id TEXT PRIMARY KEY
    # - event_type TEXT
    # - timestamp TEXT
    # - entity_id TEXT
    # - entity_type TEXT
    # - payload JSON
    # - trace_id TEXT
    
    # No UPDATE or DELETE allowed
    
class JSONLEventLog(EventLog):
    """JSONL file-based event log (backup/export)."""
    
    # One JSON object per line
    # File: data/events/2024-01-15.jsonl
```

---

## 5) Memory Coordinator

**Purpose:** Unified interface for all memory operations.

```python
class MemoryCoordinator:
    """Coordinates all memory subsystem components."""
    
    def __init__(
        self,
        document_store: DocumentStore,
        vector_store: VectorStore,
        graph_store: GraphStore,
        event_log: EventLog,
        embedding_service: EmbeddingService,
    ):
        self.documents = document_store
        self.vectors = vector_store
        self.graph = graph_store
        self.events = event_log
        self.embeddings = embedding_service
    
    async def store_note(
        self,
        note_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> None:
        """Store a note across all relevant stores."""
        # 1. Store document
        await self.documents.put_document(note_id, content, metadata)
        
        # 2. Generate and store embedding
        vector = await self.embeddings.embed_text(content)
        await self.vectors.upsert_embedding(note_id, vector, metadata)
        
        # 3. Create graph node
        node = GraphNode(
            node_id=note_id,
            node_type=NodeType.NOTE,
            name=metadata.get("title", note_id),
            properties=metadata,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        await self.graph.upsert_node(node)
        
        # 4. Log event
        await self.events.append(Event(
            event_id=generate_ulid(),
            event_type=EventType.NOTE_CREATED,
            timestamp=datetime.utcnow(),
            entity_id=note_id,
            entity_type="note",
            payload={"title": metadata.get("title")},
        ))
```

---

## 6) Index State Store (v1.0.1)

**Purpose:** Track indexing status across stores for eventual consistency.

### Problem

When the VaultIndexer processes a note, it updates multiple stores (Document, Graph, Vector). These updates are not atomic, so a note may be:
- In the Document Store but not yet in the Graph
- In the Graph but not yet embedded in the Vector Store
- Marked as stale because content changed

The Index State Store tracks this explicitly, enabling:
- Context Assembler to prefer fully-indexed items
- Reconciliation jobs to find incomplete indexing
- Debugging and observability

### Interface

```python
class IndexStateStore:
    """SQLite-backed store for entity index states."""
    
    def get(self, entity_id: str) -> EntityIndexState | None:
        """Get index state for an entity."""
        ...
    
    def get_by_path(self, source_path: str) -> EntityIndexState | None:
        """Get index state by source file path."""
        ...
    
    def save(self, state: EntityIndexState) -> None:
        """Save or update an index state."""
        ...
    
    def update_doc_status(
        self, entity_id: str, status: IndexStatus, error: str | None = None
    ) -> None:
        """Update document store indexing status."""
        ...
    
    def update_graph_status(
        self, entity_id: str, status: IndexStatus, error: str | None = None
    ) -> None:
        """Update graph store indexing status."""
        ...
    
    def update_vector_status(
        self, entity_id: str, status: IndexStatus, error: str | None = None
    ) -> None:
        """Update vector store indexing status."""
        ...
    
    def mark_stale(self, entity_id: str, new_content_hash: str) -> None:
        """Mark an entity as stale (content changed)."""
        ...
    
    def list_pending(
        self, entity_type: str | None = None, limit: int = 100
    ) -> list[EntityIndexState]:
        """List entities that need indexing."""
        ...
    
    def get_statistics(self) -> dict[str, Any]:
        """Get indexing statistics by type."""
        ...
```

### Models

```python
class IndexStatus(str, Enum):
    """Status of indexing for an entity."""
    PENDING = "pending"      # Not yet indexed
    INDEXING = "indexing"    # Currently being indexed
    INDEXED = "indexed"      # Successfully indexed
    FAILED = "failed"        # Indexing failed
    STALE = "stale"          # Content changed, needs re-indexing

class EntityIndexState:
    """Indexing state for a single entity."""
    entity_id: str
    entity_type: str  # "note", "task", etc.
    source_path: str | None = None
    content_hash: str | None = None
    
    # Per-store status
    doc_status: IndexStatus = IndexStatus.PENDING
    graph_status: IndexStatus = IndexStatus.PENDING
    vector_status: IndexStatus = IndexStatus.PENDING
    
    # Timestamps
    doc_indexed_at: datetime | None = None
    graph_indexed_at: datetime | None = None
    vector_indexed_at: datetime | None = None
    
    # Error tracking
    last_error: str | None = None
    error_count: int = 0
    
    @property
    def is_fully_indexed(self) -> bool:
        """True if indexed in all stores."""
        return (
            self.doc_status == IndexStatus.INDEXED
            and self.graph_status == IndexStatus.INDEXED
            and self.vector_status == IndexStatus.INDEXED
        )
    
    @property
    def needs_indexing(self) -> bool:
        """True if any store needs (re)indexing."""
        return any(
            s in (IndexStatus.PENDING, IndexStatus.STALE)
            for s in [self.doc_status, self.graph_status, self.vector_status]
        )
```

### SQLite Schema

```sql
CREATE TABLE index_states (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    source_path TEXT,
    content_hash TEXT,
    doc_indexed_at TEXT,
    graph_indexed_at TEXT,
    vector_indexed_at TEXT,
    enriched_at TEXT,
    doc_status TEXT NOT NULL DEFAULT 'pending',
    graph_status TEXT NOT NULL DEFAULT 'pending',
    vector_status TEXT NOT NULL DEFAULT 'pending',
    index_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error TEXT,
    error_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_index_states_type ON index_states(entity_type);
CREATE INDEX idx_index_states_path ON index_states(source_path);
CREATE INDEX idx_index_states_fully_indexed 
    ON index_states(doc_status, graph_status, vector_status);
```

### Usage in VaultIndexer

```python
class VaultIndexer:
    def __init__(
        self,
        vault: ObsidianVault,
        document_store: DocumentStore | None = None,
        graph_store: GraphStore | None = None,
        vector_store: VectorStore | None = None,
        embedding_service: EmbeddingService | None = None,
        index_state_store: IndexStateStore | None = None,  # v1.0.1
    ):
        ...
    
    async def index_note(self, path: str, force: bool = False) -> IndexResult:
        # Initialize index state
        if self.index_state_store:
            existing = self.index_state_store.get(note_id)
            if existing and existing.content_hash != content_hash:
                self.index_state_store.mark_stale(note_id, content_hash)
            else:
                self.index_state_store.save(EntityIndexState(
                    entity_id=note_id,
                    entity_type="note",
                    source_path=path,
                    content_hash=content_hash,
                    doc_status=IndexStatus.INDEXING,
                    graph_status=IndexStatus.INDEXING,
                ))
        
        # Index to stores, updating status after each...
```

### Context Assembler Integration

The Context Assembler can use Index State to prefer fully-indexed items:

```python
class ContextAssembler:
    async def assemble(self, intent: str) -> ContextPacket:
        # Semantic search
        results = await self.vector_store.query(intent)
        
        # Filter to fully indexed items if index_state_store available
        if self.index_state_store:
            results = [
                r for r in results
                if (state := self.index_state_store.get(r.entity_id))
                and state.is_fully_indexed
            ]
        
        # Continue assembly...
```

---

## Configuration

### Environment Variables

```bash
# Database
DATABASE_URL=sqlite+aiosqlite:///./data/agent_kernel.db

# Vector Store
VECTOR_STORE_TYPE=lancedb
LANCEDB_PATH=./data/lancedb

# Graph Store
GRAPH_STORE_TYPE=sqlite

# Event Log
EVENT_LOG_PATH=./data/events
```

### Directory Structure

```
data/
├── agent_kernel.db      # SQLite database
├── lancedb/             # LanceDB persistence
├── events/              # JSONL event logs
│   ├── 2024-01-15.jsonl
│   └── 2024-01-16.jsonl
└── documents/           # Optional file storage
```

---

---

## 7) Entity Store (v1.0.4)

**Purpose:** Universal entity registry for multi-source context.

### Interface

```python
class EntityStore(ABC):
    def register_entity(entity: EntityRef) -> str:
        """Register entity, return canonical_id."""
    
    def get_entity(canonical_id: str) -> EntityRef | None:
        """Get by canonical_id."""
    
    def get_entity_by_source(source_id, entity_type, entity_id) -> EntityRef | None:
        """Get by source identifiers."""
    
    def put_view(view: EntityView) -> None:
        """Store entity view."""
    
    def record_access(canonical_id: str) -> None:
        """Track access for retention."""
```

### SQLite Tables

```sql
CREATE TABLE entity_map (
    canonical_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    uri TEXT,
    canonical_hash TEXT,
    last_accessed_at TEXT,
    access_count_30d INTEGER DEFAULT 0,
    UNIQUE(source_id, entity_type, entity_id)
);

CREATE TABLE entity_views (
    view_id TEXT PRIMARY KEY,
    canonical_id TEXT NOT NULL,
    view_type TEXT NOT NULL,
    segment_id TEXT,
    content TEXT,
    content_hash TEXT
);
```

---

## 8) Experience Store (v1.0.4)

**Purpose:** Store outcomes, cases, lessons, and playbooks for learning.

### Interface

```python
class ExperienceStore(ABC):
    # Evaluations
    def put_evaluation(evaluation: OutcomeEvaluation) -> None
    def get_evaluations_for_trace(trace_id: str) -> list[OutcomeEvaluation]
    
    # Cases
    def put_case(case: ExperienceCase) -> None
    def find_similar_cases(workflow_id, capability_names, label) -> list[ExperienceCase]
    
    # Lessons
    def put_lesson(lesson: LessonLearned) -> None
    def list_lessons(scope, status) -> list[LessonLearned]
    def activate_lesson(lesson_id: str) -> bool
    
    # Playbooks
    def put_playbook(playbook: Playbook) -> None
    def find_playbooks(workflow_id, capability_names, intent_keywords) -> list[Playbook]
```

### Learning Loop

```
Traces → OutcomeEvaluation → ExperienceCase → LessonLearned → Playbook
```

**Hard rule:** All auto-generated lessons start as `status="candidate"`.

---

## Related Documents

- [00-overview.md](00-overview.md) - Design principles
- [01-schemas.md](01-schemas.md) - Core data contracts
- [04-context.md](04-context.md) - Context Assembler (uses memory)
- [12-integration-patterns.md](12-integration-patterns.md) - Obsidian integration using VaultIndexer
- [17-universal-context-system.md](17-universal-context-system.md) - Full v1.0.4 spec