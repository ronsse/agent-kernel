"""Document Store - raw content storage with full-text search."""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now

logger = structlog.get_logger(__name__)


class DocumentStore(ABC):
    """Abstract interface for document storage.

    Documents are raw content items (notes, files, transcripts, etc.)
    with metadata and optional full-text search.
    """

    @abstractmethod
    def put(
        self,
        doc_id: str | None,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a document.

        Args:
            doc_id: Optional document ID (generated if not provided).
            content: The document content.
            metadata: Optional metadata (title, tags, etc.).

        Returns:
            The document ID.
        """

    @abstractmethod
    def get(self, doc_id: str) -> dict[str, Any] | None:
        """Retrieve a document by ID.

        Args:
            doc_id: The document ID.

        Returns:
            Document dict with content and metadata, or None.
        """

    @abstractmethod
    def delete(self, doc_id: str) -> bool:
        """Delete a document.

        Args:
            doc_id: The document ID.

        Returns:
            True if deleted, False if not found.
        """

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search documents using full-text search.

        Args:
            query: Search query.
            limit: Maximum results.
            filters: Optional metadata filters.

        Returns:
            List of matching documents.
        """

    @abstractmethod
    def list_documents(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List documents with pagination.

        Args:
            limit: Maximum documents per page.
            offset: Number to skip.

        Returns:
            List of documents.
        """

    @abstractmethod
    def count(self) -> int:
        """Count total documents."""

    @abstractmethod
    def close(self) -> None:
        """Close the store."""

    def upsert_document(
        self,
        item_id: str | None,
        item_type: str | None,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Legacy alias for put using item-oriented naming."""
        metadata = dict(metadata or {})
        if item_type and "item_type" not in metadata:
            metadata["item_type"] = item_type
        if item_id is not None and "item_id" not in metadata:
            metadata["item_id"] = item_id
        return self.put(doc_id=item_id, content=content, metadata=metadata)

    def get_document(self, item_id: str) -> dict[str, Any] | None:
        """Legacy alias for get with item-oriented keys."""
        doc = self.get(item_id)
        if doc is None:
            return None
        metadata = doc.get("metadata") or {}
        doc_id = doc.get("doc_id", item_id)
        result = dict(doc)
        result["item_id"] = doc_id
        if "item_type" not in result and "item_type" in metadata:
            result["item_type"] = metadata.get("item_type")
        return result

    def delete_document(self, item_id: str) -> bool:
        """Legacy alias for delete."""
        return self.delete(item_id)


class SQLiteDocumentStore(DocumentStore):
    """SQLite implementation with FTS5 full-text search."""

    def __init__(self, db_path: str | Path) -> None:
        """Initialize SQLite document store.

        Args:
            db_path: Path to the SQLite database.
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        logger.info("sqlite_document_store_initialized", db_path=str(self._db_path))

    def _init_schema(self) -> None:
        """Initialize database schema with FTS5."""
        self._conn.executescript("""
            -- Main documents table
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- FTS5 virtual table for full-text search (standalone, not external content)
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                doc_id,
                content
            );

            CREATE INDEX IF NOT EXISTS idx_documents_created
                ON documents(created_at);
        """)
        self._conn.commit()

    def put(
        self,
        doc_id: str | None,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a document."""
        if doc_id is None:
            doc_id = generate_ulid()

        now = utc_now().isoformat()
        metadata = metadata or {}
        metadata_json = json.dumps(metadata)

        # Check if exists
        existing = self.get(doc_id)
        if existing:
            self._conn.execute(
                """
                UPDATE documents
                SET content = ?, metadata_json = ?, updated_at = ?
                WHERE doc_id = ?
                """,
                (content, metadata_json, now, doc_id),
            )
            # Update FTS
            self._conn.execute(
                "DELETE FROM documents_fts WHERE doc_id = ?",
                (doc_id,),
            )
            self._conn.execute(
                "INSERT INTO documents_fts (doc_id, content) VALUES (?, ?)",
                (doc_id, content),
            )
        else:
            self._conn.execute(
                """
                INSERT INTO documents (doc_id, content, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (doc_id, content, metadata_json, now, now),
            )
            # Insert into FTS
            self._conn.execute(
                "INSERT INTO documents_fts (doc_id, content) VALUES (?, ?)",
                (doc_id, content),
            )

        self._conn.commit()
        logger.debug("document_stored", doc_id=doc_id)
        return doc_id

    def get(self, doc_id: str) -> dict[str, Any] | None:
        """Retrieve a document by ID."""
        cursor = self._conn.execute(
            "SELECT * FROM documents WHERE doc_id = ?",
            (doc_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return {
            "doc_id": row["doc_id"],
            "content": row["content"],
            "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def delete(self, doc_id: str) -> bool:
        """Delete a document."""
        cursor = self._conn.execute(
            "DELETE FROM documents WHERE doc_id = ?",
            (doc_id,),
        )
        # Also delete from FTS
        self._conn.execute(
            "DELETE FROM documents_fts WHERE doc_id = ?",
            (doc_id,),
        )
        self._conn.commit()
        deleted = cursor.rowcount > 0
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
        """Search documents using FTS5 with SQL-pushed metadata filtering."""
        # Sanitize query for FTS5 - escape special characters and handle empty
        sanitized_query = self._sanitize_fts_query(query)
        if not sanitized_query:
            return []

        # Build SQL-pushed metadata filter conditions
        filter_conditions: list[str] = []
        filter_params: list[Any] = []
        complex_filters: dict[str, Any] = {}

        if filters:
            for key, value in filters.items():
                if isinstance(value, bool):
                    filter_conditions.append(
                        f"json_extract(d.metadata_json, '$.{key}') = ?"
                    )
                    filter_params.append(1 if value else 0)
                elif isinstance(value, (str, int, float)):
                    filter_conditions.append(
                        f"json_extract(d.metadata_json, '$.{key}') = ?"
                    )
                    filter_params.append(value)
                else:
                    # Complex types (list, dict) need Python-side filtering
                    complex_filters[key] = value

        # Build full query with optional filter conditions
        where_parts = ["documents_fts MATCH ?"]
        sql_params: list[Any] = [sanitized_query]

        if filter_conditions:
            where_parts.extend(filter_conditions)
            sql_params.extend(filter_params)

        sql_params.append(limit)
        where_clause = " AND ".join(where_parts)

        cursor = self._conn.execute(
            f"""
            SELECT d.*, bm25(documents_fts) as rank
            FROM documents d
            JOIN documents_fts fts ON d.doc_id = fts.doc_id
            WHERE {where_clause}
            ORDER BY rank
            LIMIT ?
            """,
            sql_params,
        )

        results = []
        for row in cursor.fetchall():
            doc = {
                "doc_id": row["doc_id"],
                "content": row["content"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
                "rank": row["rank"],
            }

            # Apply complex filters that can't be pushed to SQL
            if complex_filters:
                metadata = doc["metadata"]
                match = all(metadata.get(k) == v for k, v in complex_filters.items())
                if not match:
                    continue

            results.append(doc)

        return results

    def _sanitize_fts_query(self, query: str) -> str:
        """Sanitize a query string for FTS5 MATCH.

        FTS5 has special syntax requirements. This method:
        - Removes special characters that break FTS5 syntax
        - Wraps terms in quotes for phrase matching
        - Handles empty/whitespace-only queries
        """
        import re

        if not query or not query.strip():
            return ""

        # Replace newlines and tabs with spaces
        sanitized = query.replace("\n", " ").replace("\t", " ")

        # Remove FTS5 special operators that could cause syntax errors
        # These include: AND, OR, NOT, NEAR, and operators like * - + " ( )
        # We'll extract just the alphanumeric words
        words = re.findall(r"[a-zA-Z0-9]+", sanitized)

        if not words:
            return ""

        # Join with OR for a more flexible match
        # Each word is matched individually
        return " OR ".join(f'"{word}"' for word in words[:20])  # Limit to 20 terms

    def list_documents(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List documents with pagination."""
        cursor = self._conn.execute(
            """
            SELECT * FROM documents
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )

        return [
            {
                "doc_id": row["doc_id"],
                "content": row["content"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in cursor.fetchall()
        ]

    def count(self) -> int:
        """Count total documents."""
        cursor = self._conn.execute("SELECT COUNT(*) as cnt FROM documents")
        return cursor.fetchone()["cnt"]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
        logger.info("sqlite_document_store_closed")
