# Context Assembler

**Version:** 1.0.4  
**Status:** Implemented

The Context Assembler is a **deterministic** component (with optional LLM planning) that retrieves and assembles context into a `ContextPacket`.

## Changelog

- **v1.0.2**: Added Context Packs, Source Descriptors, Retrieval Plans, and Quality Gates
- **v1.0.4**: Added Universal Entity Model, Experience Memory integration, and new quality gates

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     CONTEXT ASSEMBLER                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  INPUTS                                                          │
│  ├── intent (string)                                             │
│  ├── agent_profile.context_policy                                │
│  ├── project_id (optional)                                       │
│  └── current_time                                                │
│                                                                  │
│  RETRIEVAL SOURCES                                               │
│  ├── Vector Store (semantic search)                              │
│  ├── Document Store (recent notes)                               │
│  ├── Graph Store (related nodes/edges)                           │
│  ├── Task Store (open tasks)                                     │
│  └── Calendar (upcoming events)                                  │
│                                                                  │
│  OUTPUT                                                          │
│  └── ContextPacket (with retrieval_report)                       │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Assembler Interface

```python
class ContextAssembler:
    """Deterministic context assembly from memory stores."""
    
    def __init__(
        self,
        memory: MemoryCoordinator,
        embedding_service: EmbeddingService,
    ):
        self.memory = memory
        self.embeddings = embedding_service
    
    async def assemble(
        self,
        intent: str,
        context_policy: ContextPolicy,
        project_id: str | None = None,
        current_time: datetime | None = None,
    ) -> ContextPacket:
        """Assemble context for an intent."""
        
        current_time = current_time or datetime.utcnow()
        
        # Initialize tracking
        queries_run: list[QueryRecord] = []
        filters_applied: list[str] = []
        items: list[ContextItem] = []
        
        # 1. Semantic search
        semantic_items, semantic_queries = await self._semantic_retrieval(
            intent=intent,
            policy=context_policy,
            project_id=project_id,
        )
        items.extend(semantic_items)
        queries_run.extend(semantic_queries)
        
        # 2. Recent notes
        if context_policy.max_notes > 0:
            note_items, note_queries = await self._recent_notes(
                project_id=project_id,
                limit=context_policy.max_notes,
            )
            items.extend(note_items)
            queries_run.extend(note_queries)
        
        # 3. Open tasks
        if context_policy.max_tasks > 0:
            task_items, task_queries = await self._open_tasks(
                project_id=project_id,
                limit=context_policy.max_tasks,
            )
            items.extend(task_items)
            queries_run.extend(task_queries)
        
        # 4. Upcoming events
        if context_policy.max_events > 0:
            event_items, event_queries = await self._upcoming_events(
                current_time=current_time,
                limit=context_policy.max_events,
            )
            items.extend(event_items)
            queries_run.extend(event_queries)
        
        # 5. Graph neighbors
        graph_slice, graph_queries = await self._graph_context(
            seed_refs=[item.ref for item in items[:10]],  # Top 10 as seeds
            project_id=project_id,
        )
        queries_run.extend(graph_queries)
        
        # 6. Apply filters
        if project_id:
            filters_applied.append(f"project_id={project_id}")
        if context_policy.allowed_scopes:
            filters_applied.append(f"scopes={context_policy.allowed_scopes}")
        
        # 7. Deduplicate and rank
        items = self._deduplicate(items)
        items = self._rank_by_relevance(items)
        
        # 8. Apply budget limits
        items = self._apply_budget(items, context_policy)
        
        # 9. Apply redaction
        if context_policy.redaction_rules:
            items = self._apply_redaction(items, context_policy.redaction_rules)
        
        # Build retrieval report
        retrieval_report = RetrievalReport(
            queries_run=queries_run,
            filters_applied=filters_applied,
            items_considered=len(items),  # Before budget
            items_selected=len(items),    # After budget
            selection_strategy="relevance_ranked",
        )
        
        return ContextPacket(
            packet_id=generate_ulid(),
            intent=intent,
            project_id=project_id,
            generated_at=current_time,
            budget=ContextBudget(
                max_tokens=context_policy.max_tokens,
                max_items=50,
                retrieval_limits=RetrievalLimits(
                    max_notes=context_policy.max_notes,
                    max_tasks=context_policy.max_tasks,
                    max_events=context_policy.max_events,
                ),
            ),
            items=items,
            graph_slice=graph_slice,
            retrieval_report=retrieval_report,
        )
```

---

## Retrieval Methods

### 1. Semantic Retrieval

```python
async def _semantic_retrieval(
    self,
    intent: str,
    policy: ContextPolicy,
    project_id: str | None = None,
) -> tuple[list[ContextItem], list[QueryRecord]]:
    """Retrieve items via semantic similarity."""
    
    started = datetime.utcnow()
    
    # Generate embedding for intent
    intent_vector = await self.embeddings.embed_text(intent)
    
    # Build filters
    filters = {}
    if project_id:
        filters["project_id"] = project_id
    
    # Query vector store
    results = await self.memory.vectors.query_embedding(
        vector=intent_vector,
        top_k=policy.max_tokens // 200,  # Rough estimate
        filters=filters,
        min_score=0.5,  # Relevance threshold
    )
    
    ended = datetime.utcnow()
    
    # Convert to ContextItems
    items = []
    for result in results:
        ref = ContextRef(
            ref_type=RefType(result.metadata.get("type", "doc")),
            ref_id=result.item_id,
            metadata=result.metadata,
        )
        items.append(ContextItem(
            ref=ref,
            excerpt=result.text or "",
            relevance_score=result.score,
            included_reason="semantic_match",
        ))
    
    query_record = QueryRecord(
        source="vector",
        query=intent[:100],  # Truncate for logging
        results_count=len(results),
        duration_ms=int((ended - started).total_seconds() * 1000),
    )
    
    return items, [query_record]
```

### 2. Recent Notes

```python
async def _recent_notes(
    self,
    project_id: str | None = None,
    limit: int = 10,
) -> tuple[list[ContextItem], list[QueryRecord]]:
    """Retrieve recently modified notes."""
    
    started = datetime.utcnow()
    
    filters = {"doc_type": "note"}
    if project_id:
        filters["project_id"] = project_id
    
    docs = await self.memory.documents.list_documents(
        filters=filters,
        limit=limit,
    )
    
    ended = datetime.utcnow()
    
    items = []
    for doc in docs:
        ref = ContextRef(
            ref_type=RefType.NOTE,
            ref_id=doc.doc_id,
            hash=doc.content_hash,
            metadata=doc.metadata,
        )
        items.append(ContextItem(
            ref=ref,
            excerpt=doc.content[:500],  # First 500 chars
            relevance_score=0.6,  # Recency bonus
            included_reason="recent_note",
        ))
    
    query_record = QueryRecord(
        source="document",
        query="recent_notes",
        results_count=len(docs),
        duration_ms=int((ended - started).total_seconds() * 1000),
    )
    
    return items, [query_record]
```

### 3. Open Tasks

```python
async def _open_tasks(
    self,
    project_id: str | None = None,
    limit: int = 20,
) -> tuple[list[ContextItem], list[QueryRecord]]:
    """Retrieve open tasks."""
    
    started = datetime.utcnow()
    
    filters = {"doc_type": "task", "status": "open"}
    if project_id:
        filters["project_id"] = project_id
    
    docs = await self.memory.documents.search_documents(
        filters=filters,
        limit=limit,
    )
    
    ended = datetime.utcnow()
    
    items = []
    for doc in docs:
        ref = ContextRef(
            ref_type=RefType.TASK,
            ref_id=doc.doc_id,
            metadata=doc.metadata,
        )
        # Prioritize by due date
        due_date = doc.metadata.get("due_date")
        relevance = 0.8 if due_date else 0.5
        
        items.append(ContextItem(
            ref=ref,
            excerpt=doc.content[:300],
            relevance_score=relevance,
            included_reason="open_task",
        ))
    
    query_record = QueryRecord(
        source="document",
        query="open_tasks",
        results_count=len(docs),
        duration_ms=int((ended - started).total_seconds() * 1000),
    )
    
    return items, [query_record]
```

### 4. Upcoming Events

```python
async def _upcoming_events(
    self,
    current_time: datetime,
    limit: int = 10,
) -> tuple[list[ContextItem], list[QueryRecord]]:
    """Retrieve upcoming calendar events."""
    
    started = datetime.utcnow()
    
    # Look ahead 7 days
    end_time = current_time + timedelta(days=7)
    
    filters = {
        "doc_type": "event",
        "start_time": {"$gte": current_time.isoformat()},
        "end_time": {"$lte": end_time.isoformat()},
    }
    
    docs = await self.memory.documents.search_documents(
        filters=filters,
        limit=limit,
    )
    
    ended = datetime.utcnow()
    
    items = []
    for doc in docs:
        ref = ContextRef(
            ref_type=RefType.EVENT,
            ref_id=doc.doc_id,
            metadata=doc.metadata,
        )
        items.append(ContextItem(
            ref=ref,
            excerpt=doc.content[:200],
            relevance_score=0.7,
            included_reason="upcoming_event",
        ))
    
    query_record = QueryRecord(
        source="document",
        query="upcoming_events",
        results_count=len(docs),
        duration_ms=int((ended - started).total_seconds() * 1000),
    )
    
    return items, [query_record]
```

### 5. Graph Context

```python
async def _graph_context(
    self,
    seed_refs: list[ContextRef],
    project_id: str | None = None,
) -> tuple[GraphSlice | None, list[QueryRecord]]:
    """Retrieve graph neighbors of seed nodes."""
    
    if not seed_refs:
        return None, []
    
    started = datetime.utcnow()
    
    seed_ids = [ref.ref_id for ref in seed_refs]
    
    filters = GraphFilters()
    if project_id:
        filters.node_types = [NodeType.PROJECT]
        seed_ids.append(project_id)
    
    subgraph = await self.memory.graph.get_subgraph(
        seed_ids=seed_ids,
        depth=2,
        filters=filters,
    )
    
    ended = datetime.utcnow()
    
    graph_slice = GraphSlice(
        nodes=subgraph.nodes,
        edges=subgraph.edges,
    ) if subgraph.nodes else None
    
    query_record = QueryRecord(
        source="graph",
        query=f"neighbors(seeds={len(seed_ids)}, depth=2)",
        results_count=len(subgraph.nodes),
        duration_ms=int((ended - started).total_seconds() * 1000),
    )
    
    return graph_slice, [query_record]
```

---

## Ranking and Filtering

### Relevance Ranking

```python
def _rank_by_relevance(
    self,
    items: list[ContextItem],
) -> list[ContextItem]:
    """Sort items by relevance score."""
    
    # Boost factors
    REASON_BOOSTS = {
        "semantic_match": 1.0,
        "open_task": 0.9,
        "recent_note": 0.7,
        "upcoming_event": 0.8,
        "graph_neighbor": 0.5,
    }
    
    for item in items:
        boost = REASON_BOOSTS.get(item.included_reason, 0.5)
        item.relevance_score *= boost
    
    return sorted(items, key=lambda x: x.relevance_score, reverse=True)
```

### Budget Application

```python
def _apply_budget(
    self,
    items: list[ContextItem],
    policy: ContextPolicy,
) -> list[ContextItem]:
    """Apply token and item budgets."""
    
    result = []
    token_count = 0
    type_counts = {"note": 0, "task": 0, "event": 0}
    
    for item in items:
        # Estimate tokens (rough: 1 token per 4 chars)
        item_tokens = len(item.excerpt) // 4
        
        # Check total budget
        if token_count + item_tokens > policy.max_tokens:
            break
        
        # Check type limits
        item_type = item.ref.ref_type.value
        if item_type == "note" and type_counts["note"] >= policy.max_notes:
            continue
        if item_type == "task" and type_counts["task"] >= policy.max_tasks:
            continue
        if item_type == "event" and type_counts["event"] >= policy.max_events:
            continue
        
        result.append(item)
        token_count += item_tokens
        if item_type in type_counts:
            type_counts[item_type] += 1
    
    return result
```

### Deduplication

```python
def _deduplicate(
    self,
    items: list[ContextItem],
) -> list[ContextItem]:
    """Remove duplicate items by ref_id."""
    
    seen = set()
    result = []
    
    for item in items:
        if item.ref.ref_id not in seen:
            seen.add(item.ref.ref_id)
            result.append(item)
    
    return result
```

---

## Context Policies

### Policy Examples

```yaml
# Agent with broad access
context_policy:
  max_tokens: 8000
  max_notes: 20
  max_tasks: 30
  max_events: 10
  must_cite: true
  allowed_scopes: []  # All projects

# Agent with limited scope
context_policy:
  max_tokens: 2000
  max_notes: 5
  max_tasks: 10
  max_events: 3
  must_cite: true
  allowed_scopes:
    - project_alpha
    - project_beta
  redaction_rules:
    - "email:*"
    - "phone:*"
```

---

## Retrieval Report

The `RetrievalReport` is critical for **debugging** why certain context was included:

```python
class RetrievalReport(BaseModel):
    """Debug information about context retrieval."""
    
    queries_run: list[QueryRecord]
    filters_applied: list[str]
    items_considered: int  # Before budget cuts
    items_selected: int    # After budget cuts
    selection_strategy: str
    
class QueryRecord(BaseModel):
    source: str       # "vector", "document", "graph"
    query: str        # What was queried
    results_count: int
    duration_ms: int
```

This allows tracing back: "Why did the agent see this note but not that one?"

---

## Context Packs (v1.0.2)

Context Packs are curated sets of documents/rules attached to context based on selectors.

### Structure

```python
class ContextPackSelector(BaseModel):
    """Criteria for when a pack applies."""
    scopes: list[str] = []           # project/workflow IDs
    ref_types: list[RefType] = []    # note, task, event
    keywords: list[str] = []         # intent keywords

class ContextPack(BaseModel):
    """Curated set of documents attached to context."""
    pack_id: str
    name: str
    description: str
    priority: int = 50               # 0-100, higher = more important
    selector: ContextPackSelector
    include_policy: str = "relevance" # always, relevance, never
    refs: list[ContextRef] = []
```

### Pack Resolution

```python
from agent_kernel.context import ContextPackResolver

resolver = ContextPackResolver(pack_dir="configs/context_packs/")
packs = resolver.resolve_for_scope(
    workflow_id="daily_checkin",
    intent="Review my tasks for today"
)
```

---

## Source Descriptors (v1.0.2)

Source Descriptors define available fields, operators, and constraints for each data source.

```python
class SourceDescriptor(BaseModel):
    """Describes a data source for retrieval."""
    source_id: str
    description: str
    fields: list[FieldDescriptor]
    constraints: SourceConstraint

class FieldDescriptor(BaseModel):
    """A queryable field in a source."""
    name: str
    type: str  # string, int, datetime, list
    allowed_ops: list[str]  # eq, contains, gt, lt, in

class SourceConstraint(BaseModel):
    """What this source can/cannot do."""
    can_store_text: bool = True
    allowed_entity_types: list[str] = []
```

### Example Source Config

```yaml
# configs/sources/obsidian.yaml
source_id: obsidian
description: "Obsidian vault notes"
fields:
  - name: path
    type: string
    allowed_ops: [eq, contains, prefix]
  - name: tags
    type: list
    allowed_ops: [contains, in]
  - name: modified_at
    type: datetime
    allowed_ops: [gt, lt, gte, lte]
constraints:
  can_store_text: true
  allowed_entity_types: [note]
```

---

## Retrieval Planning (v1.0.2)

### Retrieval Plan Schema

```python
class RetrievalPlan(BaseModel):
    """Optional LLM-produced plan for context retrieval."""
    plan_id: str
    directives: list[RetrievalDirective]
    rationale: str
    estimated_tokens: int

class RetrievalDirective(BaseModel):
    """A single retrieval action."""
    source_id: str
    strategy: str  # semantic, keyword, graph, recency
    filters: list[RetrievalFilter]
    limit: int = 10
    priority: int = 50
```

### Planners

```python
# Deterministic baseline
class BaselineRetrievalPlanner:
    def plan(self, intent, context_policy) -> RetrievalPlan

# LLM-powered (opt-in)
class InstructedRetrievalPlanner:
    def plan(self, intent, context_policy, source_descriptors) -> RetrievalPlan
```

---

## Retrieval Gates (v1.0.2+)

Quality gates ensure context meets requirements before use.

### Gate Interface

```python
class RetrievalGate(ABC):
    @abstractmethod
    def check(self, context_packet, context_policy) -> CoverageGateResult
```

### Standard Gates

| Gate | Purpose |
|------|---------|
| `PackPresenceGate` | Required packs are present |
| `SchemaAwareFiltersGate` | Filters match source descriptors |
| `CoverageGate` | Minimum items per category |
| `RecencyGate` | Content is fresh enough |
| `ParityGate` | Index is up-to-date |

### v1.0.4 Quality Gates

| Gate | Purpose |
|------|---------|
| `SourceConstraintEnforcementGate` | Validates source constraints |
| `ExperienceWarningGate` | Injects warnings from similar failed cases |
| `PlaybookCoverageGate` | Ensures relevant playbooks are loaded |

### Gate Runner

```python
from agent_kernel.context import RetrievalGateRunner

runner = RetrievalGateRunner(gates=[
    PackPresenceGate(required_packs=["vault_rules"]),
    CoverageGate(min_notes=5),
    ExperienceWarningGate(experience_store),
    PlaybookCoverageGate(playbook_resolver),
])

report = runner.run_all(context_packet, context_policy)
if not report.passed:
    # Handle failures
```

---

## Universal Entity Model (v1.0.4)

The Context Assembler now works with `EntityRef` and `EntityView` for multi-source context.

### Entity-Aware Retrieval

```python
# ContextRef now includes entity reference
class ContextRef(BaseModel):
    ref_type: RefType
    ref_id: str
    entity_ref: EntityRef | None = None  # v1.0.4
    embedding_vector: list[float] | None = None  # v1.0.4
```

### Entity Store Integration

```python
# Register entities from any source
entity_store.register_entity(EntityRef(
    source_id="slack",
    entity_type="message",
    entity_id="C123-1234567890.123456"
))
```

---

## Experience Memory Integration (v1.0.4)

The assembler can inject lessons and warnings from past experiences.

### Experience-Aware Context

```python
# Query similar experiences during assembly
similar_cases = experience_store.find_similar_cases(
    workflow_id="daily_checkin",
    label="negative"  # Find past failures
)

# Inject as context items
for case in similar_cases:
    items.append(ContextItem(
        ref=ContextRef(ref_type=RefType.LESSON, ref_id=case.case_id),
        excerpt=case.summary,
        included_reason="experience_warning"
    ))
```

### Playbook Resolution

```python
# Find relevant playbooks
playbooks = playbook_resolver.resolve_playbooks(
    workflow_id="daily_checkin",
    capability_names=["tasks.list", "notes.search"]
)

# Add to context packet
context_packet.context_packs.extend(playbooks)
```

---

## ThinkingConfig Integration (v1.0.3+)

The assembler respects `ThinkingConfig` for dynamic retrieval strategies.

```python
async def assemble_async(
    self,
    intent: str,
    context_policy: ContextPolicy,
    thinking_config: ThinkingConfig | None = None,  # v1.0.3
) -> ContextPacket:
    """Assemble context with thinking-aware retrieval."""
    
    if thinking_config:
        # Use tier-specific settings
        tier = thinking_config.tiers.get(thinking_config.current_tier)
        if tier and tier.retrieval_strategy:
            # Apply semantic search if enabled
            if tier.retrieval_strategy.semantic_search:
                items.extend(await self._search_vectors(intent))
            # Apply graph expansion if enabled
            if tier.retrieval_strategy.graph_expansion:
                items.extend(await self._get_graph_context(items[:10]))
```

---

## Hybrid Search (v1.0.4)

For complex queries, use the `HybridSearchService`:

```python
from agent_kernel.services import HybridSearchService

hybrid = HybridSearchService(
    document_store=docs,
    vector_store=vectors,
    graph_store=graph,
    entity_store=entities,
    embedding_service=embeddings,
)

results = await hybrid.search(
    query="project planning strategies",
    strategy="hierarchical",  # summary → chunks
    limit=20
)
```

### Search Strategies

| Strategy | Description |
|----------|-------------|
| `hierarchical` | Search summaries first, then chunks |
| `hybrid` | Combine vector + keyword + graph |
| `vector` | Semantic similarity only |
| `keyword` | Full-text search only |
| `graph` | Traverse relationships only |

---

## Related Documents

- [00-overview.md](00-overview.md) - Design principles
- [01-schemas.md](01-schemas.md) - ContextPacket schema
- [02-memory.md](02-memory.md) - Memory subsystem
- [11-thinking-policy.md](11-thinking-policy.md) - Thinking tiers
- [14-embedding-strategy.md](14-embedding-strategy.md) - Embedding approach
- [16-hybrid-search.md](16-hybrid-search.md) - Hybrid search details
- [17-universal-context-system.md](17-universal-context-system.md) - Full v1.0.4 spec