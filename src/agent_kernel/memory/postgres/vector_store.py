"""PostgreSQL implementation of VectorStore using pgvector.

Uses the pgvector extension (enabled by default in Supabase) for
efficient vector similarity search with IVFFlat or HNSW indexes.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from agent_kernel.memory.postgres.connection import PostgresConnection, PostgresConnectionPool
from agent_kernel.memory.vector_store import VectorStore

logger = structlog.get_logger(__name__)


class PostgresVectorStore(VectorStore):
    """PostgreSQL + pgvector implementation of VectorStore.

    Uses cosine similarity operator (<=> for cosine distance).
    Supabase has pgvector enabled by default.
    """

    def __init__(
        self,
        pool: PostgresConnectionPool,
        *,
        dimensions: int = 1536,
    ) -> None:
        self._pool = pool
        self._dimensions = dimensions
        self._init_schema()
        logger.info(
            "postgres_vector_store_initialized",
            dimensions=dimensions,
        )

    def _init_schema(self) -> None:
        """Initialize schema with pgvector extension and table."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                # Enable pgvector extension
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS vectors (
                        item_id TEXT PRIMARY KEY,
                        embedding vector({self._dimensions}),
                        metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    );

                    CREATE INDEX IF NOT EXISTS idx_vectors_created
                        ON vectors(created_at);

                    CREATE INDEX IF NOT EXISTS idx_vectors_metadata
                        ON vectors USING GIN(metadata_json);
                """)

                # Create HNSW index for cosine similarity if not exists
                # HNSW provides O(log N) search performance
                cur.execute(f"""
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_indexes
                            WHERE indexname = 'idx_vectors_embedding_hnsw'
                        ) THEN
                            CREATE INDEX idx_vectors_embedding_hnsw
                                ON vectors
                                USING hnsw (embedding vector_cosine_ops)
                                WITH (m = 16, ef_construction = 64);
                        END IF;
                    END
                    $$;
                """)

    def upsert(
        self,
        item_id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert or update a vector."""
        metadata_json = json.dumps(metadata or {})
        vector_str = "[" + ",".join(str(v) for v in vector) + "]"

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO vectors (item_id, embedding, metadata_json)
                    VALUES (%s, %s::vector, %s::jsonb)
                    ON CONFLICT (item_id) DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        metadata_json = EXCLUDED.metadata_json
                    """,
                    (item_id, vector_str, metadata_json),
                )

        logger.debug("vector_upserted", item_id=item_id, dimensions=len(vector))

    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query for similar vectors using cosine distance.

        Uses pgvector's <=> operator (cosine distance).
        Score = 1 - distance (to match cosine similarity convention).
        """
        vector_str = "[" + ",".join(str(v) for v in vector) + "]"

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        item_id,
                        metadata_json,
                        1 - (embedding <=> %s::vector) AS score
                    FROM vectors
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vector_str, vector_str, top_k),
                )
                rows = cur.fetchall()

        results = []
        for row in rows:
            metadata = row[1] if isinstance(row[1], dict) else json.loads(row[1])

            if filters:
                match = all(metadata.get(k) == v for k, v in filters.items())
                if not match:
                    continue

            results.append({
                "item_id": row[0],
                "score": float(row[2]),
                "metadata": metadata,
            })

        return results

    def delete(self, item_id: str) -> bool:
        """Delete a vector by ID."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM vectors WHERE item_id = %s",
                    (item_id,),
                )
                return cur.rowcount > 0

    def count(self) -> int:
        """Count total vectors."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM vectors")
                return cur.fetchone()[0]

    def close(self) -> None:
        """No-op; pool manages connections."""
        logger.info("postgres_vector_store_closed")
