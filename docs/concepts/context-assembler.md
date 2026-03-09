# Context Assembler

The Context Assembler is a deterministic component that retrieves and assembles context into a `ContextPacket` -- the bounded input an agent engine receives. It queries memory stores, applies filters and budgets, and produces a reproducible context window.

## What a ContextPacket Contains

| Field | Description |
|-------|-------------|
| `intent` | The user intent or query |
| `budget` | Token and item limits for the context window |
| `items` | Retrieved context items with relevance scores |
| `retrieval_report` | Debug info: queries run, filters applied, items considered vs. selected |
| `graph_slice` | Optional subgraph of related entities |

## How Context Is Assembled

The assembler follows a multi-source retrieval pipeline:

```
Intent + Context Policy
         |
         v
  +-----------------+
  | Semantic Search  |  Vector store: find similar content
  +-----------------+
         |
  +-----------------+
  | Document Query   |  Document store: recent items, keyword match
  +-----------------+
         |
  +-----------------+
  | Graph Traversal  |  Graph store: related entities, edges
  +-----------------+
         |
  +-----------------+
  | Budget Filtering |  Apply token limits, item caps, deduplication
  +-----------------+
         |
         v
    ContextPacket
```

### 1. Semantic Search

The assembler embeds the intent and queries the vector store for semantically similar content. Results are scored by relevance.

### 2. Document Query

Recent documents and keyword-matched content are retrieved from the document store. Filters can scope by project, tags, or time range.

### 3. Graph Traversal

The graph store provides structural context -- related entities, edges, and subgraphs. This connects documents to tasks, people, projects, and other entities.

### 4. Budget Filtering

All retrieved items are merged, deduplicated, and trimmed to fit within the context budget:

```python
from agent_kernel.core.schemas import ContextBudget, RetrievalLimits

budget = ContextBudget(
    max_tokens=4000,       # Total token budget
    max_items=30,          # Maximum context items
    retrieval_limits=RetrievalLimits(
        max_notes=10,      # Cap on note items
        max_tasks=20,      # Cap on task items
        max_events=5,      # Cap on calendar events
        max_graph_nodes=50,  # Cap on graph nodes
    ),
)
```

## Context Policy

Each agent profile includes a `ContextPolicy` that controls retrieval:

```python
from agent_kernel.core.schemas import ContextPolicy

policy = ContextPolicy(
    max_tokens=4000,
    max_notes=10,
    max_tasks=20,
    max_events=5,
    must_cite=True,         # Agent must cite context in its plan
    allowed_scopes=["project_alpha"],  # Scope to specific projects
    redaction_rules=[],     # Fields to redact before sending to LLM
)
```

## Retrieval Report

Every `ContextPacket` includes a `RetrievalReport` for debugging and analysis:

```python
report = packet.retrieval_report
print(f"Queries run: {len(report.queries_run)}")
print(f"Items considered: {report.items_considered}")
print(f"Items selected: {report.items_selected}")
print(f"Strategy: {report.selection_strategy}")

for query in report.queries_run:
    print(f"  {query.source}: '{query.query}' -> {query.results_count} results ({query.duration_ms}ms)")
```

This makes context assembly transparent -- you can always see why specific items were included or excluded.

## Code Example

```python
from agent_kernel import ContextAssembler

assembler = ContextAssembler(
    memory=memory_coordinator,
    embedding_service=embedding_service,
)

packet = await assembler.assemble(
    intent="What tasks should I focus on today?",
    context_policy=profile.context_policy,
    project_id="my_project",
)

print(f"Context items: {len(packet.items)}")
for item in packet.items:
    print(f"  [{item.ref.ref_type}] {item.excerpt[:60]}... (score: {item.relevance_score:.2f})")
```

## Next Steps

- [Schema Contracts](schemas.md) -- the data models that flow through the assembler
- [Tracing](tracing.md) -- how context usage is recorded in traces
- [Thinking Escalation](../guides/thinking-escalation.md) -- how context assembly adapts to reasoning tiers
