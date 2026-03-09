# 14. Embedding Strategy (v1.0.3)

## Overview

This document defines the chunking and embedding strategy for semantic search within the Agent Kernel. It covers the tradeoffs between different approaches and our recommended implementation.

---

## Embedding Approaches Comparison

### Option 1: Full Document Embedding

Embed the entire document as a single vector.

| Aspect | Evaluation |
|--------|------------|
| **Pros** | Simple, one vector per note, captures overall meaning |
| **Cons** | Context window limits (~8K tokens), loses detail in long docs, poor for passage retrieval |
| **Best For** | Short notes (<500 words), document-level similarity |

### Option 2: Chunk-Only Embedding

Split documents into overlapping chunks, embed each chunk separately.

| Aspect | Evaluation |
|--------|------------|
| **Pros** | Handles long documents, finds specific passages, standard RAG approach |
| **Cons** | Many vectors per doc (cost), noisy matches on irrelevant chunks, loses document context |
| **Best For** | RAG applications needing exact quotes, long-form documents |

### Option 3: Summary-Only Embedding

Generate an LLM summary, embed only the summary.

| Aspect | Evaluation |
|--------|------------|
| **Pros** | One vector per note, captures essence, fast retrieval |
| **Cons** | Loses detail, requires LLM call for summary, summary quality varies |
| **Best For** | Note-level relevance ranking, "find related notes" queries |

### Option 4: Hierarchical Embedding (Recommended)

Embed both summary AND chunks, use summary for ranking and chunks for retrieval.

| Aspect | Evaluation |
|--------|------------|
| **Pros** | Best accuracy, fast note ranking + precise passage retrieval, flexible |
| **Cons** | Higher cost (LLM for summary + more vectors), more complex query flow |
| **Best For** | Production systems, deep thinking agents, high-accuracy requirements |

---

## Our Approach: Hierarchical Embedding

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Note Content                                    │
│                                                                         │
│  Frontmatter (YAML)          │  Body (Markdown)                         │
│  ─────────────────           │  ────────────────                         │
│  id: note_01J...             │  # Meeting Notes                         │
│  tags: [project/x, meeting]  │                                          │
│  auto:                       │  Discussed the architecture...           │
│    tags: [architecture]      │                                          │
│    summary: "Architecture    │  ## Action Items                         │
│      meeting discussing..."  │  - [ ] Review PR #123                    │
│                              │  - [x] Update docs                       │
└─────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         EMBEDDING PIPELINE                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  1. SUMMARY EMBEDDING (1 per note)                                      │
│     ├── Source: auto.summary (LLM-generated)                            │
│     ├── Vector ID: {note_id}:summary                                    │
│     ├── Purpose: Note-level relevance ranking                           │
│     └── Query: "Find notes about X"                                     │
│                                                                         │
│  2. CHUNK EMBEDDINGS (N per note, optional)                             │
│     ├── Source: Body text split into ~500 token chunks                  │
│     ├── Overlap: 50 tokens between chunks                               │
│     ├── Vector ID: {note_id}:chunk_{i}                                  │
│     ├── Purpose: Passage retrieval within a note                        │
│     └── Query: "Find the exact part that mentions Y"                    │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Query Flow

```
User Query: "How did we decide on the database architecture?"
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STAGE 1: Note-Level Ranking (Summary Embeddings)                       │
│                                                                         │
│  Query embedding → Cosine similarity vs summary embeddings              │
│  Result: Top-10 most relevant notes                                     │
│                                                                         │
│  Notes: [architecture-meeting.md, db-design.md, system-overview.md]     │
└─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STAGE 2: Graph Expansion (Optional)                                    │
│                                                                         │
│  For each relevant note:                                                │
│    - Get linked notes (NOTE_LINKS_TO_NOTE edges)                        │
│    - Get notes with same tags (via tag nodes)                           │
│                                                                         │
│  Expanded set: +5 related notes                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STAGE 3: Passage Retrieval (Chunk Embeddings)                          │
│                                                                         │
│  For top-N notes, search their chunk embeddings                         │
│  Result: Specific passages that answer the query                        │
│                                                                         │
│  Passages: ["We decided on SQLite for...", "The schema uses..."]        │
└─────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  STAGE 4: Hybrid Merge + Rerank                                         │
│                                                                         │
│  Combine:                                                               │
│    - Vector search results (semantic)                                   │
│    - Keyword search results (FTS5)                                      │
│    - Graph-expanded notes                                               │
│                                                                         │
│  Rerank with cross-encoder or LLM scoring                               │
│  Return: Final ranked context                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Chunking Strategy

### Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Chunk size** | ~500 tokens | Balance between context and specificity |
| **Overlap** | 50 tokens | Preserve context at boundaries |
| **Separator priority** | `\n\n`, `\n`, `. `, ` ` | Respect document structure |
| **Min chunk size** | 100 tokens | Avoid tiny, meaningless chunks |

### Implementation

```python
def chunk_content(content: str, chunk_size: int = 500, overlap: int = 50) -> list[dict]:
    """Split content into overlapping chunks.
    
    Returns:
        List of {"text": str, "start_offset": int, "end_offset": int}
    """
    # Prefer splitting at paragraph boundaries
    # Fall back to sentence boundaries
    # Last resort: word boundaries
```

### What Gets Chunked

| Element | Included in Chunks? | Rationale |
|---------|---------------------|-----------|
| **Body text** | ✅ Yes | Core searchable content |
| **Wiki links `[[...]]`** | ✅ Yes (as text) | Context for relationships |
| **Code blocks** | ✅ Yes | May contain relevant info |
| **Frontmatter** | ❌ No | Stored as metadata, not embedded |
| **Auto-generated summary** | ❌ No (separate embedding) | Used for note-level ranking |

---

## Tag Handling

### Human Tags vs. Auto Tags

| Tag Type | Source | Storage | In Embeddings? | In Graph? |
|----------|--------|---------|----------------|-----------|
| **Human tags** | Frontmatter `tags:` | YAML | ❌ No | ✅ Yes |
| **Auto tags** | LLM enrichment | `auto.tags` | ❌ No | ✅ Yes |
| **Inline tags** | `#tag` in body | Parsed | ✅ Yes (in text) | ✅ Yes |

### Why Tags Aren't Embedded Directly

1. **Short text** → Embeddings are less meaningful for 1-3 word strings
2. **Categorical nature** → Tags are discrete categories, not semantic concepts
3. **Graph captures relationships** → Co-occurrence computable from edges
4. **Exact matching works** → Tag queries are usually exact, not fuzzy

### When to Consider Tag Embeddings

- If you need "find tags semantically similar to X"
- If you have descriptive tag names (e.g., "machine-learning-optimization")
- Solution: Embed `tag_name + description` together

---

## Edge Deletion Tracking

When notes are updated, edges must be synchronized:

### Current Implementation

```python
# After creating all current edges, delete stale ones
deleted_tags = graph_store.delete_edges_from_source(
    source_id=node_id,
    edge_type=EdgeType.NOTE_TAGGED_WITH_TAG.value,
    exclude_targets=current_tag_targets,  # Keep these
)
```

### Edge Types Tracked

| Edge Type | Deletion Behavior |
|-----------|-------------------|
| `NOTE_TAGGED_WITH_TAG` | Delete if tag removed from frontmatter |
| `NOTE_LINKS_TO_NOTE` | Delete if link removed from content |
| `NOTE_HAS_TASK` | Delete if task checkbox removed |
| `TASK_TAGGED_WITH_TAG` | Delete if tag removed from task |

---

## Cost Considerations

### Embedding Costs (OpenAI text-embedding-3-small)

| Scenario | Notes | Vectors | Approx. Cost |
|----------|-------|---------|--------------|
| Summary only | 1,000 | 1,000 | ~$0.02 |
| Chunks only (5/note) | 1,000 | 5,000 | ~$0.10 |
| Hierarchical | 1,000 | 6,000 | ~$0.12 |

### Summary Generation Costs (GPT-4o-mini)

| Scenario | Notes | Tokens | Approx. Cost |
|----------|-------|--------|--------------|
| Generate summaries | 1,000 | ~500K | ~$0.15 |

### Total Initial Cost

For 1,000 notes with hierarchical embedding:
- Summary generation: ~$0.15
- Embeddings: ~$0.12
- **Total: ~$0.27**

### Incremental Updates

Only re-embed when `content_hash` changes:
- Typical daily change rate: 1-5% of notes
- Incremental cost: ~$0.01/day

---

## Vector Store Schema

```sql
CREATE TABLE vectors (
    item_id TEXT PRIMARY KEY,      -- e.g., "note_01J...:summary"
    vector_blob BLOB NOT NULL,     -- Serialized float array
    dimensions INTEGER NOT NULL,   -- e.g., 1536
    metadata_json TEXT,            -- {"note_id": "...", "type": "summary", ...}
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### Item ID Conventions

| Type | Format | Example |
|------|--------|---------|
| Summary | `{note_id}:summary` | `note_01J...:summary` |
| Chunk | `{note_id}:chunk_{i}` | `note_01J...:chunk_0` |

---

## Implementation Phases

### Phase 1: ✅ Graph + Documents (DONE)
- Notes indexed in document store (FTS5)
- Graph with tags, links, tasks
- Edge deletion tracking

### Phase 2: ✅ Auto-Tagging (DONE)
- LLM generates `auto.tags` based on content
- Store in `auto:` frontmatter block
- Add auto-tags to graph as edges (with `source: "auto"`)

### Phase 3: ✅ Summary Generation (DONE)
- LLM generates `auto.summary` for each note (always, not optional)
- Summaries are semantic-rich for better embedding
- Store in `auto:` frontmatter block

### Phase 4: ✅ Hierarchical Embeddings (DONE)
- Embed summaries as `{note_id}:summary` (1 per note)
- Embed chunks as `{note_id}:chunk_{i}` (N per note)
- Metadata includes `embedding_type: "summary"` or `"chunk"`
- Incremental updates on content change

### Phase 5: ✅ Hybrid Search (DONE)
- `HybridSearchService` combines vector + keyword + graph
- Hierarchical strategy: Summary → Graph expansion → Chunks
- Score fusion for results appearing in multiple sources
- CLI command: `agent-kernel search "query"`

---

## Configuration

```yaml
# configs/embedding.yaml
embedding:
  model: text-embedding-3-small
  dimensions: 1536
  
chunking:
  chunk_size: 500
  overlap: 50
  min_chunk_size: 100
  
strategy:
  embed_summaries: true
  embed_chunks: true  # Set to false for summary-only
  
enrichment:
  generate_summaries: true
  generate_auto_tags: true
  model: gpt-4o-mini
```

---

## References

- [OpenAI Embedding Guide](https://platform.openai.com/docs/guides/embeddings)
- [Chunking Strategies](https://www.pinecone.io/learn/chunking-strategies/)
- [Hierarchical Retrieval Paper](https://arxiv.org/abs/2401.13391)
