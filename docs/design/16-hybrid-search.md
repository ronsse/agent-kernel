# 16. Hybrid Search (v1.0.3)

## Overview

Hybrid Search combines multiple search strategies to achieve optimal retrieval accuracy:

1. **Vector Search** - Semantic similarity via embeddings
2. **Keyword Search** - FTS5 exact/fuzzy matching
3. **Graph Expansion** - Relationship traversal
4. **Score Fusion** - Combining results from multiple sources

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      HYBRID SEARCH FLOW                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  User Query: "How does authentication work?"                            │
│                           │                                             │
│                           ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ STAGE 1: Summary Embeddings (Note-Level Relevance)              │   │
│  │                                                                  │   │
│  │ Query embedding → Cosine similarity vs summary embeddings       │   │
│  │ Filter: embedding_type = "summary"                               │   │
│  │ Result: Top-10 most relevant notes                               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                           │                                             │
│                           ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ STAGE 2: Graph Expansion                                         │   │
│  │                                                                  │   │
│  │ For each relevant note:                                          │   │
│  │   - Get linked notes (NOTE_LINKS_TO_NOTE edges)                  │   │
│  │   - Get notes with same tags (via tag nodes)                     │   │
│  │ Result: +N related notes (lower relevance score)                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                           │                                             │
│                           ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ STAGE 3: Chunk Embeddings (Passage Retrieval)                    │   │
│  │                                                                  │   │
│  │ For top-N notes, search their chunk embeddings                   │   │
│  │ Filter: embedding_type = "chunk", note_id IN (relevant_notes)    │   │
│  │ Result: Specific passages that answer the query                  │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                           │                                             │
│                           ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ STAGE 4: Keyword Search (FTS5)                                   │   │
│  │                                                                  │   │
│  │ Full-text search on document content                             │   │
│  │ Catches exact matches that embeddings might miss                 │   │
│  │ Result: Notes containing exact query terms                       │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                           │                                             │
│                           ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ STAGE 5: Score Fusion & Deduplication                            │   │
│  │                                                                  │   │
│  │ Combine all results:                                             │   │
│  │   - Deduplicate by note_id (keep chunks separate)                │   │
│  │   - Boost scores for items appearing in multiple sources         │   │
│  │   - Sort by final score                                          │   │
│  │ Result: Ranked list of relevant notes/passages                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Search Strategies

### 1. Hierarchical (Recommended)

The default strategy optimized for knowledge graph search.

```
Summary Embeddings → Graph Expansion → Chunk Embeddings → Keywords
```

**Best for:** General knowledge queries, finding related notes

### 2. Hybrid

All sources searched in parallel with score fusion.

```
Vector + Keyword + Graph (parallel) → Score Fusion
```

**Best for:** When you need results from all sources equally weighted

### 3. Vector Only

Pure semantic similarity search.

```
Query Embedding → Vector Store → Ranked Results
```

**Best for:** Concept-based queries, finding semantically similar content

### 4. Keyword Only

Pure full-text search (FTS5).

```
Query → FTS5 Match → Ranked Results
```

**Best for:** Exact phrase matches, finding specific terms

### 5. Graph Only

Relationship-based discovery.

```
Keyword Seeds → Graph Traversal → Related Nodes
```

**Best for:** Finding related content, exploring connections

---

## CLI Usage

### Basic Search

```bash
# Default hierarchical search
agent-kernel search "how does authentication work"

# Specify strategy
agent-kernel search "project architecture" --strategy hybrid
agent-kernel search "API endpoint" --strategy keyword

# Limit results
agent-kernel search "machine learning" --limit 5

# Show chunk-level results
agent-kernel search "database design" --chunks
```

### Strategy Options

| Strategy | Flag | Description |
|----------|------|-------------|
| Hierarchical | `--strategy hierarchical` | Summary → Graph → Chunks (default) |
| Hybrid | `--strategy hybrid` | All sources with score fusion |
| Vector | `--strategy vector` | Semantic similarity only |
| Keyword | `--strategy keyword` | FTS5 exact match only |
| Graph | `--strategy graph` | Relationship-based only |

---

## Configuration

### HybridSearchConfig

```python
@dataclass
class HybridSearchConfig:
    # Strategy
    strategy: SearchStrategy = SearchStrategy.HIERARCHICAL

    # Limits
    max_results: int = 20          # Final result limit
    summary_limit: int = 10        # Top-N summaries in stage 1
    chunk_limit: int = 5           # Chunks per note in stage 3
    graph_depth: int = 1           # Hops for graph expansion

    # Weights for score fusion
    vector_weight: float = 0.6     # Semantic similarity weight
    keyword_weight: float = 0.3    # Keyword match weight
    graph_weight: float = 0.1      # Graph expansion weight

    # Filters
    embedding_type_filter: str | None = None  # "summary" or "chunk"
    note_ids_filter: list[str] | None = None  # Restrict to specific notes

    # Reranking
    enable_reranking: bool = False
    rerank_model: str | None = None
```

---

## Score Fusion

Results from different sources are combined using weighted score fusion:

### Weight Distribution

| Source | Default Weight | Rationale |
|--------|----------------|-----------|
| Vector (semantic) | 0.6 | Primary signal for relevance |
| Keyword (FTS5) | 0.3 | Exact matches are important |
| Graph (relations) | 0.1 | Context from relationships |

### Fusion Algorithm

```python
# Items appearing in multiple sources get boosted
note_scores: dict[str, float] = {}
for result in all_results:
    if result.note_id in note_scores:
        note_scores[result.note_id] += result.score  # Accumulate
    else:
        note_scores[result.note_id] = result.score
```

---

## Vector Store Filtering

The vector store supports metadata filtering for hierarchical search:

```python
# Search only summaries
summary_results = vector_store.query(
    embedding,
    top_k=10,
    filters={"embedding_type": "summary"},
)

# Search chunks for a specific note
chunk_results = vector_store.query(
    embedding,
    top_k=5,
    filters={
        "embedding_type": "chunk",
        "note_id": "note_01J...",
    },
)
```

### Metadata Fields

| Field | Values | Description |
|-------|--------|-------------|
| `embedding_type` | `"summary"`, `"chunk"` | Type of embedding |
| `note_id` | `"note_01J..."` | Parent note ID |
| `path` | `"folder/file.md"` | Note path in vault |
| `title` | `"Note Title"` | Note title |

---

## Integration with Context Assembler

The `HybridSearchService` can be used by the `ContextAssembler` for context retrieval:

```python
from agent_kernel.services.hybrid_search import (
    HybridSearchService,
    HybridSearchConfig,
    SearchStrategy,
)

# Initialize
search_service = HybridSearchService(
    document_store=doc_store,
    vector_store=vec_store,
    graph_store=graph_store,
    embedding_service=embed_service,
)

# Search
config = HybridSearchConfig(
    strategy=SearchStrategy.HIERARCHICAL,
    max_results=20,
)
results = await search_service.search("query", config)

# Use results in context assembly
for result in results.results:
    print(f"{result.title}: {result.score:.2f}")
```

---

## Finding Similar Notes

The service includes a method to find notes similar to a given note:

```python
# Find notes similar to a specific note
similar = await search_service.search_similar_notes(
    note_id="note_01J...",
    limit=10,
)

for result in similar.results:
    print(f"Similar: {result.title} (score: {result.score:.2f})")
```

This uses the note's summary embedding to find semantically similar notes.

---

## Performance Considerations

### Current Implementation (SQLite)

- Loads all vectors into memory for similarity computation
- Suitable for < 10,000 vectors
- Query time: O(n) where n = total vectors

### Scaling Options

| Scale | Recommendation |
|-------|----------------|
| < 1K vectors | Current SQLite implementation |
| 1K - 100K | Add ANN index (FAISS, Annoy) |
| > 100K | Dedicated vector DB (Pinecone, Weaviate, Qdrant) |

### Optimization Tips

1. **Use summary-first search** - Reduces vectors to search
2. **Filter by embedding_type** - Skip irrelevant vectors
3. **Cache query embeddings** - Reuse for similar queries
4. **Batch similar queries** - Reduce embedding API calls

---

## References

- [HybridSearchService](../../src/agent_kernel/services/hybrid_search.py)
- [Vector Store](../../src/agent_kernel/memory/vector_store.py)
- [Document Store](../../src/agent_kernel/memory/document_store.py)
- [Graph Store](../../src/agent_kernel/memory/graph_store.py)
- [Embedding Strategy](./14-embedding-strategy.md)
