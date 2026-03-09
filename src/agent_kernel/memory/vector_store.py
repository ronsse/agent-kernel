"""Vector Store - semantic search with embeddings.

Provides multiple implementations:
- SQLiteVectorStore: Simple O(N) linear scan for small datasets (<10K vectors)
- LanceDBVectorStore: HNSW-indexed O(log N) search for production scale

Usage:
    # For small datasets (development, testing)
    store = SQLiteVectorStore("vectors.db")

    # For production (install: pip install lancedb)
    store = LanceDBVectorStore("vectors.lance")

    # Factory function (auto-selects based on availability)
    store = create_vector_store("vectors", prefer_lancedb=True)
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    import pyarrow as pa

logger = structlog.get_logger(__name__)

# Check for LanceDB availability
try:
    import lancedb
    import pyarrow as pa
    import pandas as pd

    LANCEDB_AVAILABLE = True
except ImportError:
    LANCEDB_AVAILABLE = False
    lancedb = None  # type: ignore[assignment]
    pa = None  # type: ignore[assignment]
    pd = None  # type: ignore[assignment]


class VectorStore(ABC):
    """Abstract interface for vector storage and similarity search."""

    @abstractmethod
    def upsert(
        self,
        item_id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert or update a vector.

        Args:
            item_id: Unique identifier for the item.
            vector: The embedding vector.
            metadata: Optional metadata.
        """

    @abstractmethod
    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query for similar vectors.

        Args:
            vector: Query vector.
            top_k: Number of results.
            filters: Optional metadata filters.

        Returns:
            List of matches with item_id, score, metadata.
        """

    @abstractmethod
    def delete(self, item_id: str) -> bool:
        """Delete a vector by ID."""

    @abstractmethod
    def get(self, item_id: str) -> dict[str, Any] | None:
        """Get a vector by ID."""

    @abstractmethod
    def count(self) -> int:
        """Count total vectors."""

    @abstractmethod
    def close(self) -> None:
        """Close the store."""

    def upsert_vector(
        self,
        item_id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Legacy alias for upsert."""
        self.upsert(item_id=item_id, vector=vector, metadata=metadata)

    def get_vector(self, item_id: str) -> dict[str, Any] | None:
        """Legacy alias for get."""
        return self.get(item_id)

    def delete_vector(self, item_id: str) -> bool:
        """Legacy alias for delete."""
        return self.delete(item_id)

    def search(
        self,
        vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Legacy alias for query."""
        return self.query(vector=vector, top_k=top_k, filters=filters)


class SQLiteVectorStore(VectorStore):
    """SQLite-based vector store with cosine similarity.

    This is a simple implementation for local use. For production,
    consider using a dedicated vector DB like Chroma, Qdrant, or pgvector.
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialize SQLite vector store.

        Args:
            db_path: Path to SQLite database.
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        logger.info("sqlite_vector_store_initialized", db_path=str(self._db_path))

    def _init_schema(self) -> None:
        """Initialize database schema."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS vectors (
                item_id TEXT PRIMARY KEY,
                vector_blob BLOB NOT NULL,
                dimensions INTEGER NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_vectors_created
                ON vectors(created_at);
        """)
        self._conn.commit()

    def upsert(
        self,
        item_id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert or update a vector."""
        import numpy as np

        vector_array = np.array(vector, dtype=np.float32)
        vector_blob = vector_array.tobytes()
        dimensions = len(vector)
        metadata_json = json.dumps(metadata or {})

        self._conn.execute(
            """
            INSERT OR REPLACE INTO vectors
            (item_id, vector_blob, dimensions, metadata_json)
            VALUES (?, ?, ?, ?)
            """,
            (item_id, vector_blob, dimensions, metadata_json),
        )
        self._conn.commit()
        logger.debug("vector_upserted", item_id=item_id, dimensions=dimensions)

    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query for similar vectors using cosine similarity.

        Pushes simple metadata filters to SQL to reduce rows loaded
        into Python for cosine similarity computation.
        """
        import numpy as np

        query_vector = np.array(vector, dtype=np.float32)
        query_norm = np.linalg.norm(query_vector)

        if query_norm == 0:
            return []

        # Build SQL-pushed filter conditions for simple types
        where_parts = ["1=1"]
        filter_params: list[Any] = []
        complex_filters: dict[str, Any] = {}

        if filters:
            for key, value in filters.items():
                if isinstance(value, bool):
                    where_parts.append(
                        f"json_extract(metadata_json, '$.{key}') = ?"
                    )
                    filter_params.append(1 if value else 0)
                elif isinstance(value, (str, int, float)):
                    where_parts.append(
                        f"json_extract(metadata_json, '$.{key}') = ?"
                    )
                    filter_params.append(value)
                else:
                    # Complex types (list, dict) need Python-side filtering
                    complex_filters[key] = value

        where_clause = " AND ".join(where_parts)
        cursor = self._conn.execute(
            "SELECT item_id, vector_blob, dimensions, metadata_json"
            f" FROM vectors WHERE {where_clause}",
            filter_params,
        )

        results = []
        for row in cursor.fetchall():
            stored_vector = np.frombuffer(row["vector_blob"], dtype=np.float32)
            metadata = json.loads(row["metadata_json"])

            # Apply complex filters that can't be pushed to SQL
            if complex_filters:
                match = all(metadata.get(k) == v for k, v in complex_filters.items())
                if not match:
                    continue

            # Compute cosine similarity
            stored_norm = np.linalg.norm(stored_vector)
            if stored_norm == 0:
                continue

            similarity = float(np.dot(query_vector, stored_vector) / (query_norm * stored_norm))

            results.append({
                "item_id": row["item_id"],
                "score": similarity,
                "metadata": metadata,
            })

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def get(self, item_id: str) -> dict[str, Any] | None:
        """Get a vector by ID."""
        import numpy as np

        cursor = self._conn.execute(
            "SELECT * FROM vectors WHERE item_id = ?",
            (item_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return {
            "item_id": row["item_id"],
            "vector": np.frombuffer(row["vector_blob"], dtype=np.float32).tolist(),
            "dimensions": row["dimensions"],
            "metadata": json.loads(row["metadata_json"]),
        }

    def delete(self, item_id: str) -> bool:
        """Delete a vector by ID."""
        cursor = self._conn.execute(
            "DELETE FROM vectors WHERE item_id = ?",
            (item_id,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def count(self) -> int:
        """Count total vectors."""
        cursor = self._conn.execute("SELECT COUNT(*) as cnt FROM vectors")
        return cursor.fetchone()["cnt"]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
        logger.info("sqlite_vector_store_closed")


class LanceDBVectorStore(VectorStore):
    """LanceDB-based vector store with HNSW index for O(log N) search.

    This is the recommended implementation for production use with large
    datasets (10K+ vectors). Uses LanceDB's native HNSW index for efficient
    approximate nearest neighbor search.

    Requires: pip install lancedb pyarrow

    Performance characteristics:
        - Insert: O(log N) amortized
        - Query: O(log N) with HNSW index
        - Memory: Disk-based with memory-mapped access
        - Scales to millions of vectors
    """

    # LanceDB table schema
    TABLE_NAME = "vectors"

    def __init__(
        self,
        db_path: str | Path,
        *,
        metric: str = "cosine",
        num_partitions: int = 256,
        num_sub_vectors: int = 96,
    ) -> None:
        """Initialize LanceDB vector store.

        Args:
            db_path: Path to LanceDB database directory.
            metric: Distance metric ("cosine", "L2", "dot").
            num_partitions: Number of IVF partitions for indexing.
            num_sub_vectors: Number of sub-vectors for PQ compression.
        """
        if not LANCEDB_AVAILABLE:
            msg = (
                "LanceDB is not installed. Install with: pip install lancedb pyarrow\n"
                "Or use SQLiteVectorStore for small datasets."
            )
            raise ImportError(msg)

        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._metric = metric
        self._num_partitions = num_partitions
        self._num_sub_vectors = num_sub_vectors

        # Connect to LanceDB
        self._db = lancedb.connect(str(self._db_path))
        self._table = self._get_or_create_table()
        self._dimensions: int | None = None

        logger.info(
            "lancedb_vector_store_initialized",
            db_path=str(self._db_path),
            metric=metric,
        )

    def _get_or_create_table(self) -> Any:
        """Get existing table or return None (created on first upsert)."""
        # Use table_names() (returns plain list[str]) or list_tables() (newer API)
        existing_tables: list[str] = []
        if hasattr(self._db, 'table_names'):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                existing_tables = list(self._db.table_names())
        elif hasattr(self._db, 'list_tables'):
            # list_tables() returns ListTablesResponse — extract table names
            response = self._db.list_tables()
            if hasattr(response, 'tables'):
                existing_tables = [str(t) for t in response.tables]
            else:
                existing_tables = [str(t) for t in response]
        if self.TABLE_NAME in existing_tables:
            return self._db.open_table(self.TABLE_NAME)
        return None

    def _ensure_table(self, dimensions: int) -> Any:
        """Ensure table exists with correct schema."""
        if self._table is not None:
            return self._table

        # Create table with schema on first insert
        schema = pa.schema([
            pa.field("item_id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dimensions)),
            pa.field("metadata_json", pa.string()),
        ])

        self._table = self._db.create_table(
            self.TABLE_NAME,
            schema=schema,
            mode="overwrite",
        )
        self._dimensions = dimensions
        logger.info("lancedb_table_created", dimensions=dimensions)
        return self._table

    def upsert(
        self,
        item_id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert or update a vector.

        Uses LanceDB's merge_insert for efficient upserts.
        """
        dimensions = len(vector)
        table = self._ensure_table(dimensions)

        # Prepare data
        data = [{
            "item_id": item_id,
            "vector": vector,
            "metadata_json": json.dumps(metadata or {}),
        }]

        # Use merge_insert for upsert semantics
        try:
            table.merge_insert("item_id").when_matched_update_all().when_not_matched_insert_all().execute(data)
        except Exception:
            # Fallback: delete then insert for older LanceDB versions
            try:
                table.delete(f'item_id = "{item_id}"')
            except Exception:
                pass  # Item may not exist
            table.add(data)

        logger.debug("vector_upserted", item_id=item_id, dimensions=dimensions)

    def upsert_batch(
        self,
        items: list[tuple[str, list[float], dict[str, Any] | None]],
    ) -> None:
        """Batch upsert multiple vectors efficiently.

        Args:
            items: List of (item_id, vector, metadata) tuples.
        """
        if not items:
            return

        dimensions = len(items[0][1])
        table = self._ensure_table(dimensions)

        # Prepare batch data
        data = [
            {
                "item_id": item_id,
                "vector": vector,
                "metadata_json": json.dumps(metadata or {}),
            }
            for item_id, vector, metadata in items
        ]

        # Delete existing items
        item_ids = [item[0] for item in items]
        try:
            id_list = ", ".join(f'"{id}"' for id in item_ids)
            table.delete(f"item_id IN ({id_list})")
        except Exception:
            pass  # Items may not exist

        # Batch insert
        table.add(data)
        logger.debug("vectors_batch_upserted", count=len(items))

    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query for similar vectors using HNSW index.

        Performance: O(log N) with HNSW index vs O(N) with linear scan.
        """
        if self._table is None:
            return []

        # Build query
        query_builder = self._table.search(vector).limit(top_k).metric(self._metric)

        # Execute search
        try:
            results_df = query_builder.to_pandas()
        except Exception as e:
            logger.warning("lancedb_query_failed", error=str(e))
            return []

        if results_df.empty:
            return []

        # Process results
        results = []
        for _, row in results_df.iterrows():
            metadata = json.loads(row["metadata_json"])

            # Apply metadata filters in Python (LanceDB filter syntax is limited)
            if filters:
                match = all(metadata.get(k) == v for k, v in filters.items())
                if not match:
                    continue

            # LanceDB returns distance, convert to similarity for cosine
            distance = row.get("_distance", 0.0)
            if self._metric == "cosine":
                # Cosine distance to similarity: sim = 1 - dist
                score = 1.0 - distance
            elif self._metric == "dot":
                # Dot product: higher is better (already a similarity)
                score = -distance  # LanceDB returns negative dot product as distance
            else:
                # L2: convert to similarity-like score
                score = 1.0 / (1.0 + distance)

            results.append({
                "item_id": row["item_id"],
                "score": float(score),
                "metadata": metadata,
            })

        return results

    def get(self, item_id: str) -> dict[str, Any] | None:
        """Get a vector by ID."""
        if self._table is None:
            return None

        try:
            # Use to_pandas and filter - most reliable across LanceDB versions
            results_df = self._table.to_pandas()
            results_df = results_df[results_df["item_id"] == item_id]

            if results_df.empty:
                return None

            row = results_df.iloc[0]
            return {
                "item_id": row["item_id"],
                "vector": list(row["vector"]),
                "dimensions": len(row["vector"]),
                "metadata": json.loads(row["metadata_json"]),
            }
        except Exception as e:
            logger.warning("lancedb_get_failed", item_id=item_id, error=str(e))
            return None

    def delete(self, item_id: str) -> bool:
        """Delete a vector by ID."""
        if self._table is None:
            return False

        try:
            self._table.delete(f'item_id = "{item_id}"')
            return True
        except Exception as e:
            logger.warning("lancedb_delete_failed", item_id=item_id, error=str(e))
            return False

    def count(self) -> int:
        """Count total vectors."""
        if self._table is None:
            return 0

        try:
            return self._table.count_rows()
        except Exception:
            # Fallback
            return len(self._table.to_pandas())

    def create_index(self, *, force: bool = False) -> None:
        """Create or rebuild the HNSW index for faster queries.

        Should be called after bulk inserts for optimal performance.

        Args:
            force: If True, recreate index even if it exists.
        """
        if self._table is None:
            logger.warning("lancedb_no_table_for_index")
            return

        try:
            self._table.create_index(
                metric=self._metric,
                num_partitions=self._num_partitions,
                num_sub_vectors=self._num_sub_vectors,
                replace=force,
            )
            logger.info(
                "lancedb_index_created",
                metric=self._metric,
                num_partitions=self._num_partitions,
            )
        except Exception as e:
            # Index may already exist
            logger.debug("lancedb_index_creation_skipped", reason=str(e))

    def close(self) -> None:
        """Close the LanceDB connection."""
        # LanceDB doesn't require explicit close, but we log for consistency
        logger.info("lancedb_vector_store_closed")


def create_vector_store(
    db_path: str | Path,
    *,
    prefer_lancedb: bool = True,
    **kwargs: Any,
) -> VectorStore:
    """Factory function to create a vector store.

    Automatically selects the best available implementation.

    Args:
        db_path: Base path for the database (extension added automatically).
        prefer_lancedb: If True, use LanceDB when available.
        **kwargs: Additional arguments passed to the store constructor.

    Returns:
        VectorStore instance (LanceDBVectorStore or SQLiteVectorStore).

    Example:
        # Auto-select best available
        store = create_vector_store("data/vectors")

        # Force SQLite
        store = create_vector_store("data/vectors", prefer_lancedb=False)
    """
    db_path = Path(db_path)

    if prefer_lancedb and LANCEDB_AVAILABLE:
        lance_path = db_path.with_suffix(".lance")
        logger.info("creating_lancedb_vector_store", path=str(lance_path))
        return LanceDBVectorStore(lance_path, **kwargs)

    sqlite_path = db_path.with_suffix(".db")
    if prefer_lancedb and not LANCEDB_AVAILABLE:
        logger.warning(
            "lancedb_not_available_falling_back_to_sqlite",
            install_hint="pip install lancedb pyarrow",
        )
    logger.info("creating_sqlite_vector_store", path=str(sqlite_path))
    return SQLiteVectorStore(sqlite_path)
