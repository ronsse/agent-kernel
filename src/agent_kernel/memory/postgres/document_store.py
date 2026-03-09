"""PostgreSQL implementation of DocumentStore using Supabase.

Uses PostgreSQL's built-in tsvector/tsquery for full-text search,
replacing SQLite's FTS5.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.memory.document_store import DocumentStore
from agent_kernel.memory.postgres.connection import PostgresConnection, PostgresConnectionPool

logger = structlog.get_logger(__name__)


class PostgresDocumentStore(DocumentStore):
    """PostgreSQL implementation with tsvector full-text search."""

    def __init__(self, pool: PostgresConnectionPool) -> None:
        self._pool = pool
        self._init_schema()
        logger.info("postgres_document_store_initialized")

    def _init_schema(self) -> None:
        """Initialize database schema with tsvector FTS."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS documents (
                        doc_id TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        metadata_json JSONB NOT NULL DEFAULT '{}',
                        search_vector tsvector,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_documents_created
                        ON documents(created_at);

                    CREATE INDEX IF NOT EXISTS idx_documents_search
                        ON documents USING GIN(search_vector);

                    CREATE INDEX IF NOT EXISTS idx_documents_metadata
                        ON documents USING GIN(metadata_json);
                """)

    def put(
        self,
        doc_id: str | None,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a document with tsvector for FTS."""
        if doc_id is None:
            doc_id = generate_ulid()

        now = utc_now().isoformat()
        metadata = metadata or {}
        metadata_json = json.dumps(metadata)

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO documents
                        (doc_id, content, metadata_json, search_vector, created_at, updated_at)
                    VALUES (%s, %s, %s::jsonb, to_tsvector('english', %s), %s, %s)
                    ON CONFLICT (doc_id) DO UPDATE SET
                        content = EXCLUDED.content,
                        metadata_json = EXCLUDED.metadata_json,
                        search_vector = EXCLUDED.search_vector,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (doc_id, content, metadata_json, content, now, now),
                )

        logger.debug("document_stored", doc_id=doc_id)
        return doc_id

    def get(self, doc_id: str) -> dict[str, Any] | None:
        """Retrieve a document by ID."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT doc_id, content, metadata_json, created_at, updated_at
                    FROM documents WHERE doc_id = %s
                    """,
                    (doc_id,),
                )
                row = cur.fetchone()

        if row is None:
            return None

        return {
            "doc_id": row[0],
            "content": row[1],
            "metadata": row[2] if isinstance(row[2], dict) else json.loads(row[2]),
            "created_at": str(row[3]),
            "updated_at": str(row[4]),
        }

    def delete(self, doc_id: str) -> bool:
        """Delete a document."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM documents WHERE doc_id = %s",
                    (doc_id,),
                )
                deleted = cur.rowcount > 0

        if deleted:
            logger.debug("document_deleted", doc_id=doc_id)
        return deleted

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search documents using PostgreSQL tsquery."""
        if not query or not query.strip():
            return []

        # Build tsquery from words
        words = query.strip().split()
        tsquery = " | ".join(words[:20])

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT doc_id, content, metadata_json, created_at,
                           ts_rank(search_vector, plainto_tsquery('english', %s)) as rank
                    FROM documents
                    WHERE search_vector @@ plainto_tsquery('english', %s)
                    ORDER BY rank DESC
                    LIMIT %s
                    """,
                    (query, query, limit),
                )
                rows = cur.fetchall()

        results = []
        for row in rows:
            metadata = row[2] if isinstance(row[2], dict) else json.loads(row[2])

            # Apply metadata filters
            if filters:
                match = all(metadata.get(k) == v for k, v in filters.items())
                if not match:
                    continue

            results.append({
                "doc_id": row[0],
                "content": row[1],
                "metadata": metadata,
                "created_at": str(row[3]),
                "rank": float(row[4]),
            })

        return results

    def list_documents(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List documents with pagination."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT doc_id, content, metadata_json, created_at
                    FROM documents
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                rows = cur.fetchall()

        return [
            {
                "doc_id": row[0],
                "content": row[1],
                "metadata": row[2] if isinstance(row[2], dict) else json.loads(row[2]),
                "created_at": str(row[3]),
            }
            for row in rows
        ]

    def count(self) -> int:
        """Count total documents."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM documents")
                return cur.fetchone()[0]

    def close(self) -> None:
        """No-op; pool manages connections."""
        logger.info("postgres_document_store_closed")
