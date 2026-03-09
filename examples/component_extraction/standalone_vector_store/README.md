# Standalone Vector Store Example

This example shows how to extract **just the vector store** from the agent kernel for use in a standalone application.

## What's Extracted

- ✅ VectorStore interface
- ✅ SQLiteVectorStore implementation
- ✅ Minimal core schemas (only what's needed)
- ❌ No workflows, agents, executors, or other components

## Use Case

Add semantic search to any application without bringing in the full agent kernel.

## Installation

```bash
# Install minimal dependencies
pip install -r requirements.txt
```

**requirements.txt:**
```
pydantic>=2.0
numpy>=1.24
```

**Total size:** ~50MB (vs 500MB for full agent-kernel)

## File Structure

```
standalone_vector_store/
├── requirements.txt
├── src/
│   ├── vector_store.py          # Extracted from agent-kernel
│   ├── schemas.py               # Minimal schemas needed
│   └── utils.py                 # ID generation, timestamps
├── example.py                   # Working demo
└── tests/
    └── test_vector_store.py
```

## Example Usage

### Basic Operations

```python
from src.vector_store import SQLiteVectorStore
import numpy as np

# Create store
store = SQLiteVectorStore("data/embeddings.db")
await store.create_table()

# Add vectors
await store.upsert(
    item_id="doc_001",
    vector=np.random.rand(1536).tolist(),
    metadata={"title": "Introduction to AI", "category": "tech"},
)

# Search
results = await store.query(
    query_vector=np.random.rand(1536).tolist(),
    top_k=10,
    filters={"category": "tech"},
)

for result in results:
    print(f"{result.item_id}: {result.score}")
```

### Semantic Search Application

```python
import asyncio
from src.vector_store import SQLiteVectorStore
from openai import AsyncOpenAI

client = AsyncOpenAI()

class SemanticSearch:
    def __init__(self, db_path: str):
        self.store = SQLiteVectorStore(db_path)

    async def initialize(self):
        await self.store.create_table()

    async def index_document(self, doc_id: str, text: str, metadata: dict):
        """Index a document for semantic search."""
        # Get embedding
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        embedding = response.data[0].embedding

        # Store in vector DB
        await self.store.upsert(
            item_id=doc_id,
            vector=embedding,
            metadata=metadata,
        )

    async def search(self, query: str, top_k: int = 10) -> list:
        """Search for similar documents."""
        # Get query embedding
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=query,
        )
        query_embedding = response.data[0].embedding

        # Search
        return await self.store.query(
            query_vector=query_embedding,
            top_k=top_k,
        )

# Usage
async def main():
    search = SemanticSearch("data/docs.db")
    await search.initialize()

    # Index documents
    await search.index_document(
        "doc_001",
        "Python is a high-level programming language",
        {"category": "programming"},
    )

    await search.index_document(
        "doc_002",
        "Machine learning is a subset of AI",
        {"category": "ai"},
    )

    # Search
    results = await search.search("What is Python?", top_k=5)
    for result in results:
        print(f"{result.item_id}: {result.score}")

asyncio.run(main())
```

## What Was Changed

### Original (agent-kernel)

```python
# src/agent_kernel/memory/vector_store.py
from agent_kernel.core.schemas.base import KernelModel
from agent_kernel.core.ids import generate_ulid

class VectorQueryResult(KernelModel):
    item_id: str
    score: float
    ...
```

### Extracted (standalone)

```python
# src/vector_store.py
from pydantic import BaseModel
import ulid

class VectorQueryResult(BaseModel):
    item_id: str
    score: float
    ...

def generate_ulid() -> str:
    return str(ulid.new())
```

**Changes:**
- `KernelModel` → `BaseModel` (no dependency on agent-kernel)
- `generate_ulid` copied locally (2 lines of code)
- Removed unused schema fields

## Performance

**Same as full agent-kernel:**
- SQLite-backed (same implementation)
- Cosine similarity (same algorithm)
- Top-k sorting (same)

**Limitations (also same):**
- O(N) linear scan (loads all vectors)
- Practical limit: ~10K vectors
- For more, upgrade to LanceDB (see `standalone_vector_store_lancedb/`)

## Migration Path

If you later need full agent capabilities:

1. Install full package:
   ```bash
   pip install agent-kernel-memory
   ```

2. Update imports:
   ```python
   # Before
   from src.vector_store import SQLiteVectorStore

   # After
   from agent_kernel_memory import SQLiteVectorStore
   ```

3. Database is compatible (same schema)

## Testing

```bash
pytest tests/test_vector_store.py
```

Tests verify:
- ✅ Create table
- ✅ Upsert vectors
- ✅ Query with top-k
- ✅ Metadata filtering
- ✅ Delete vectors

## Real-World Applications

This standalone vector store is suitable for:

1. **Document Search** - Add semantic search to docs site
2. **Recommendation System** - Similar products/content
3. **Duplicate Detection** - Find near-duplicate items
4. **Clustering** - Group similar items
5. **Anomaly Detection** - Find outliers

All without needing workflows, agents, or LLM integration.

## Next Steps

- See `standalone_vector_store_lancedb/` for 100x faster version
- See `trading_system/` for complete extraction example
- See `../COMPONENT_EXTRACTION_GUIDE.md` for full documentation
