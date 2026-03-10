"""Index State tracking for eventual consistency.

Tracks the indexing status of each entity across stores:
- Document Store
- Graph Store
- Vector Store

This enables the Context Assembler to prefer fully-indexed items
and helps with reconciliation and debugging.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.schemas.base import utc_now

logger = structlog.get_logger(__name__)


class IndexStatus(str, Enum):
    """Status of indexing for an entity."""

    PENDING = "pending"  # Not yet indexed
    INDEXING = "indexing"  # Currently being indexed
    INDEXED = "indexed"  # Successfully indexed
    FAILED = "failed"  # Indexing failed
    STALE = "stale"  # Content changed, needs re-indexing


@dataclass
class EntityIndexState:
    """Indexing state for a single entity.

    Tracks when each store was last indexed and whether the entity
    is fully indexed across all stores.
    """

    entity_id: str
    entity_type: str  # "note", "task", etc.
    source_path: str | None = None  # For file-based entities
    content_hash: str | None = None  # For change detection

    # Indexing timestamps
    doc_indexed_at: datetime | None = None
    graph_indexed_at: datetime | None = None
    vector_indexed_at: datetime | None = None
    enriched_at: datetime | None = None

    # Status per store
    doc_status: IndexStatus = IndexStatus.PENDING
    graph_status: IndexStatus = IndexStatus.PENDING
    vector_status: IndexStatus = IndexStatus.PENDING

    # Version tracking
    index_version: int = 1  # Increment on schema changes
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    # Error tracking
    last_error: str | None = None
    error_count: int = 0

    @property
    def is_fully_indexed(self) -> bool:
        """Check if entity is indexed in all stores."""
        return (
            self.doc_status == IndexStatus.INDEXED
            and self.graph_status == IndexStatus.INDEXED
            and self.vector_status == IndexStatus.INDEXED
        )

    @property
    def needs_indexing(self) -> bool:
        """Check if entity needs any indexing."""
        return (
            self.doc_status in (IndexStatus.PENDING, IndexStatus.STALE)
            or self.graph_status in (IndexStatus.PENDING, IndexStatus.STALE)
            or self.vector_status in (IndexStatus.PENDING, IndexStatus.STALE)
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "source_path": self.source_path,
            "content_hash": self.content_hash,
            "doc_indexed_at": (
                self.doc_indexed_at.isoformat() if self.doc_indexed_at else None
            ),
            "graph_indexed_at": (
                self.graph_indexed_at.isoformat() if self.graph_indexed_at else None
            ),
            "vector_indexed_at": (
                self.vector_indexed_at.isoformat() if self.vector_indexed_at else None
            ),
            "enriched_at": (self.enriched_at.isoformat() if self.enriched_at else None),
            "doc_status": self.doc_status.value,
            "graph_status": self.graph_status.value,
            "vector_status": self.vector_status.value,
            "index_version": self.index_version,
            "is_fully_indexed": self.is_fully_indexed,
            "needs_indexing": self.needs_indexing,
            "last_error": self.last_error,
            "error_count": self.error_count,
        }


class IndexStateStore:
    """SQLite-backed store for entity index states.

    Provides eventual consistency tracking for the memory subsystem.
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialize the index state store.

        Args:
            db_path: Path to SQLite database file.
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize database schema."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS index_states (
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

            CREATE INDEX IF NOT EXISTS idx_index_states_type
                ON index_states(entity_type);
            CREATE INDEX IF NOT EXISTS idx_index_states_path
                ON index_states(source_path);
            CREATE INDEX IF NOT EXISTS idx_index_states_doc_status
                ON index_states(doc_status);
            CREATE INDEX IF NOT EXISTS idx_index_states_fully_indexed
                ON index_states(doc_status, graph_status, vector_status);
        """)
        self._conn.commit()

    def get(self, entity_id: str) -> EntityIndexState | None:
        """Get index state for an entity.

        Args:
            entity_id: The entity ID.

        Returns:
            EntityIndexState if found, None otherwise.
        """
        cursor = self._conn.execute(
            "SELECT * FROM index_states WHERE entity_id = ?",
            (entity_id,),
        )
        row = cursor.fetchone()
        if row:
            return self._row_to_state(row)
        return None

    def get_by_path(self, source_path: str) -> EntityIndexState | None:
        """Get index state by source path.

        Args:
            source_path: The source file path.

        Returns:
            EntityIndexState if found, None otherwise.
        """
        cursor = self._conn.execute(
            "SELECT * FROM index_states WHERE source_path = ?",
            (source_path,),
        )
        row = cursor.fetchone()
        if row:
            return self._row_to_state(row)
        return None

    def save(self, state: EntityIndexState) -> None:
        """Save or update an index state.

        Args:
            state: The state to save.
        """
        state.updated_at = utc_now()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO index_states (
                entity_id, entity_type, source_path, content_hash,
                doc_indexed_at, graph_indexed_at, vector_indexed_at, enriched_at,
                doc_status, graph_status, vector_status, index_version,
                created_at, updated_at, last_error, error_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.entity_id,
                state.entity_type,
                state.source_path,
                state.content_hash,
                state.doc_indexed_at.isoformat() if state.doc_indexed_at else None,
                state.graph_indexed_at.isoformat() if state.graph_indexed_at else None,
                state.vector_indexed_at.isoformat()
                if state.vector_indexed_at
                else None,
                state.enriched_at.isoformat() if state.enriched_at else None,
                state.doc_status.value,
                state.graph_status.value,
                state.vector_status.value,
                state.index_version,
                state.created_at.isoformat(),
                state.updated_at.isoformat(),
                state.last_error,
                state.error_count,
            ),
        )
        self._conn.commit()

    def update_doc_status(
        self,
        entity_id: str,
        status: IndexStatus,
        error: str | None = None,
    ) -> None:
        """Update document store indexing status.

        Args:
            entity_id: The entity ID.
            status: New status.
            error: Error message if failed.
        """
        now = utc_now().isoformat()
        indexed_at = now if status == IndexStatus.INDEXED else None

        # fmt: off
        self._conn.execute(
            """
            UPDATE index_states SET
                doc_status = ?,
                doc_indexed_at = COALESCE(?, doc_indexed_at),
                updated_at = ?,
                last_error = CASE WHEN ? IS NOT NULL THEN ? ELSE last_error END,
                error_count = CASE WHEN ? = 'failed' THEN error_count + 1 ELSE error_count END
            WHERE entity_id = ?
            """,  # noqa: E501
            (status.value, indexed_at, now, error, error, status.value, entity_id),
        )
        # fmt: on
        self._conn.commit()

    def update_graph_status(
        self,
        entity_id: str,
        status: IndexStatus,
        error: str | None = None,
    ) -> None:
        """Update graph store indexing status.

        Args:
            entity_id: The entity ID.
            status: New status.
            error: Error message if failed.
        """
        now = utc_now().isoformat()
        indexed_at = now if status == IndexStatus.INDEXED else None

        # fmt: off
        self._conn.execute(
            """
            UPDATE index_states SET
                graph_status = ?,
                graph_indexed_at = COALESCE(?, graph_indexed_at),
                updated_at = ?,
                last_error = CASE WHEN ? IS NOT NULL THEN ? ELSE last_error END,
                error_count = CASE WHEN ? = 'failed' THEN error_count + 1 ELSE error_count END
            WHERE entity_id = ?
            """,  # noqa: E501
            (status.value, indexed_at, now, error, error, status.value, entity_id),
        )
        # fmt: on
        self._conn.commit()

    def update_vector_status(
        self,
        entity_id: str,
        status: IndexStatus,
        error: str | None = None,
    ) -> None:
        """Update vector store indexing status.

        Args:
            entity_id: The entity ID.
            status: New status.
            error: Error message if failed.
        """
        now = utc_now().isoformat()
        indexed_at = now if status == IndexStatus.INDEXED else None

        # fmt: off
        self._conn.execute(
            """
            UPDATE index_states SET
                vector_status = ?,
                vector_indexed_at = COALESCE(?, vector_indexed_at),
                updated_at = ?,
                last_error = CASE WHEN ? IS NOT NULL THEN ? ELSE last_error END,
                error_count = CASE WHEN ? = 'failed' THEN error_count + 1 ELSE error_count END
            WHERE entity_id = ?
            """,  # noqa: E501
            (status.value, indexed_at, now, error, error, status.value, entity_id),
        )
        # fmt: on
        self._conn.commit()

    def mark_stale(self, entity_id: str, new_content_hash: str) -> None:
        """Mark an entity as stale (content changed).

        Args:
            entity_id: The entity ID.
            new_content_hash: New content hash.
        """
        self._conn.execute(
            """
            UPDATE index_states SET
                content_hash = ?,
                doc_status = 'stale',
                graph_status = 'stale',
                vector_status = 'stale',
                updated_at = ?
            WHERE entity_id = ?
            """,
            (new_content_hash, utc_now().isoformat(), entity_id),
        )
        self._conn.commit()

    def list_pending(
        self,
        entity_type: str | None = None,
        limit: int = 100,
    ) -> list[EntityIndexState]:
        """List entities that need indexing.

        Args:
            entity_type: Filter by entity type.
            limit: Maximum number to return.

        Returns:
            List of states needing indexing.
        """
        query = """
            SELECT * FROM index_states
            WHERE (doc_status IN ('pending', 'stale')
                OR graph_status IN ('pending', 'stale')
                OR vector_status IN ('pending', 'stale'))
        """
        params: list[Any] = []

        if entity_type:
            query += " AND entity_type = ?"
            params.append(entity_type)

        query += " ORDER BY updated_at ASC LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(query, params)
        return [self._row_to_state(row) for row in cursor.fetchall()]

    def list_fully_indexed(
        self,
        entity_type: str | None = None,
        limit: int = 100,
    ) -> list[EntityIndexState]:
        """List fully indexed entities.

        Args:
            entity_type: Filter by entity type.
            limit: Maximum number to return.

        Returns:
            List of fully indexed states.
        """
        query = """
            SELECT * FROM index_states
            WHERE doc_status = 'indexed'
              AND graph_status = 'indexed'
              AND vector_status = 'indexed'
        """
        params: list[Any] = []

        if entity_type:
            query += " AND entity_type = ?"
            params.append(entity_type)

        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(query, params)
        return [self._row_to_state(row) for row in cursor.fetchall()]

    def get_statistics(self) -> dict[str, Any]:
        """Get indexing statistics.

        Returns:
            Dict with statistics.
        """
        # fmt: off
        cursor = self._conn.execute(
            """
            SELECT
                entity_type,
                COUNT(*) as total,
                SUM(CASE WHEN doc_status = 'indexed' AND graph_status = 'indexed'
                    AND vector_status = 'indexed' THEN 1 ELSE 0 END) as fully_indexed,
                SUM(CASE WHEN doc_status IN ('pending', 'stale')
                    OR graph_status IN ('pending', 'stale')
                    OR vector_status IN ('pending', 'stale')
                    THEN 1 ELSE 0 END) as needs_indexing,
                SUM(CASE WHEN doc_status = 'failed'
                    OR graph_status = 'failed'
                    OR vector_status = 'failed' THEN 1 ELSE 0 END) as failed
            FROM index_states
            GROUP BY entity_type
            """
        )
        # fmt: on

        stats = {
            "by_type": {},
            "total": 0,
            "fully_indexed": 0,
            "needs_indexing": 0,
            "failed": 0,
        }

        for row in cursor.fetchall():
            entity_type = row["entity_type"]
            stats["by_type"][entity_type] = {
                "total": row["total"],
                "fully_indexed": row["fully_indexed"],
                "needs_indexing": row["needs_indexing"],
                "failed": row["failed"],
            }
            stats["total"] += row["total"]
            stats["fully_indexed"] += row["fully_indexed"]
            stats["needs_indexing"] += row["needs_indexing"]
            stats["failed"] += row["failed"]

        return stats

    def list_by_source_path(self, source_path: str) -> list[EntityIndexState]:
        """List entities by source path.

        Args:
            source_path: The source file path to search for.

        Returns:
            List of matching states.
        """
        cursor = self._conn.execute(
            "SELECT * FROM index_states WHERE source_path = ?",
            (source_path,),
        )
        return [self._row_to_state(row) for row in cursor.fetchall()]

    def list_by_entity_type(
        self,
        entity_type: str,
        limit: int = 10000,
    ) -> list[EntityIndexState]:
        """List all entities of a given type.

        Args:
            entity_type: Entity type to filter by.
            limit: Maximum number to return.

        Returns:
            List of matching states.
        """
        cursor = self._conn.execute(
            "SELECT * FROM index_states WHERE entity_type = ? LIMIT ?",
            (entity_type, limit),
        )
        return [self._row_to_state(row) for row in cursor.fetchall()]

    def delete(self, entity_id: str) -> bool:
        """Delete an index state.

        Args:
            entity_id: The entity ID.

        Returns:
            True if deleted.
        """
        cursor = self._conn.execute(
            "DELETE FROM index_states WHERE entity_id = ?",
            (entity_id,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def _row_to_state(self, row: sqlite3.Row) -> EntityIndexState:
        """Convert a database row to EntityIndexState."""
        return EntityIndexState(
            entity_id=row["entity_id"],
            entity_type=row["entity_type"],
            source_path=row["source_path"],
            content_hash=row["content_hash"],
            doc_indexed_at=(
                datetime.fromisoformat(row["doc_indexed_at"])
                if row["doc_indexed_at"]
                else None
            ),
            graph_indexed_at=(
                datetime.fromisoformat(row["graph_indexed_at"])
                if row["graph_indexed_at"]
                else None
            ),
            vector_indexed_at=(
                datetime.fromisoformat(row["vector_indexed_at"])
                if row["vector_indexed_at"]
                else None
            ),
            enriched_at=(
                datetime.fromisoformat(row["enriched_at"])
                if row["enriched_at"]
                else None
            ),
            doc_status=IndexStatus(row["doc_status"]),
            graph_status=IndexStatus(row["graph_status"]),
            vector_status=IndexStatus(row["vector_status"]),
            index_version=row["index_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_error=row["last_error"],
            error_count=row["error_count"],
        )
