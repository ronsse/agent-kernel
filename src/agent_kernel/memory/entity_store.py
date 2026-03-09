"""Entity Store - storage for universal entity model (v1.0.4).

Provides:
- Entity mapping: (source_id, entity_type, entity_id) ↔ canonical_id
- Entity views: Multiple representations of entities for retrieval
- Access tracking: For retention decisions

The entity store is the central registry for all entities across sources.
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import SCHEMA_VERSION, get_kernel_version, utc_now
from agent_kernel.core.schemas.entity import EntityRef, EntityView, EntityViewType

logger = structlog.get_logger(__name__)


class EntityStore(ABC):
    """Abstract interface for entity storage."""

    @abstractmethod
    def register_entity(self, entity: EntityRef) -> str:
        """Register an entity and return its canonical_id.
        
        If the entity already exists (by source_id/entity_type/entity_id),
        returns the existing canonical_id.
        """
        ...

    @abstractmethod
    def get_entity(self, canonical_id: str) -> EntityRef | None:
        """Get an entity by canonical_id."""
        ...

    @abstractmethod
    def get_entity_by_source(
        self,
        source_id: str,
        entity_type: str,
        entity_id: str,
    ) -> EntityRef | None:
        """Get an entity by source identifiers."""
        ...

    @abstractmethod
    def list_entities(
        self,
        source_id: str | None = None,
        entity_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EntityRef]:
        """List entities with optional filtering."""
        ...

    @abstractmethod
    def delete_entity(self, canonical_id: str) -> bool:
        """Delete an entity and all its views."""
        ...

    @abstractmethod
    def put_view(self, view: EntityView) -> None:
        """Store or update an entity view."""
        ...

    @abstractmethod
    def get_view(self, view_id: str) -> EntityView | None:
        """Get a view by ID."""
        ...

    @abstractmethod
    def list_views(
        self,
        canonical_id: str | None = None,
        view_type: EntityViewType | None = None,
        limit: int = 100,
    ) -> list[EntityView]:
        """List views for an entity or by type."""
        ...

    @abstractmethod
    def delete_views(self, canonical_id: str) -> int:
        """Delete all views for an entity. Returns count deleted."""
        ...

    @abstractmethod
    def record_access(self, canonical_id: str) -> None:
        """Record an access event for retention tracking."""
        ...

    @abstractmethod
    def get_access_stats(self, canonical_id: str) -> dict[str, Any]:
        """Get access statistics for an entity."""
        ...


class SQLiteEntityStore(EntityStore):
    """SQLite-backed entity store implementation."""

    def __init__(self, db_path: str | Path) -> None:
        """Initialize the SQLite entity store.
        
        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entity_map (
                    canonical_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    uri TEXT,
                    canonical_hash TEXT,
                    occurred_at TEXT,
                    recorded_at TEXT NOT NULL,
                    metadata_json TEXT,
                    last_accessed_at TEXT,
                    access_count_30d INTEGER DEFAULT 0,
                    schema_version TEXT NOT NULL,
                    kernel_version TEXT NOT NULL,
                    UNIQUE(source_id, entity_type, entity_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity_source 
                ON entity_map(source_id, entity_type)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity_recorded 
                ON entity_map(recorded_at)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS entity_views (
                    view_id TEXT PRIMARY KEY,
                    canonical_id TEXT NOT NULL,
                    view_type TEXT NOT NULL,
                    segment_id TEXT,
                    content TEXT,
                    content_hash TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT,
                    schema_version TEXT NOT NULL,
                    kernel_version TEXT NOT NULL,
                    FOREIGN KEY (canonical_id) REFERENCES entity_map(canonical_id)
                        ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity_views_canonical 
                ON entity_views(canonical_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity_views_type 
                ON entity_views(view_type)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS derivation_mappings (
                    source_system TEXT NOT NULL,
                    source_container_id TEXT NOT NULL,
                    source_item_id TEXT NOT NULL,
                    derivation_kind TEXT NOT NULL,
                    target_system TEXT NOT NULL,
                    target_item_id TEXT NOT NULL,
                    last_synced_etag TEXT,
                    last_synced_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    kernel_version TEXT NOT NULL,
                    UNIQUE(
                        source_system,
                        source_container_id,
                        source_item_id,
                        derivation_kind
                    )
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_derivation_source
                ON derivation_mappings(
                    source_system,
                    source_container_id,
                    source_item_id
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_derivation_target
                ON derivation_mappings(target_system, target_item_id)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS suppression_registry (
                    source_system TEXT NOT NULL,
                    source_item_id TEXT NOT NULL,
                    artifact_kind TEXT NOT NULL,
                    suppressed_until TEXT NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    kernel_version TEXT NOT NULL,
                    UNIQUE(source_system, source_item_id, artifact_kind)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_suppression_expiry
                ON suppression_registry(suppressed_until)
            """)

            # Enable foreign key constraints
            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()

    def register_entity(self, entity: EntityRef) -> str:
        """Register an entity and return its canonical_id."""
        # Check if entity already exists
        existing = self.get_entity_by_source(
            entity.source_id, entity.entity_type, entity.entity_id
        )
        if existing and existing.canonical_id:
            return existing.canonical_id

        # Generate canonical_id if not provided
        canonical_id = entity.canonical_id or f"ent_{generate_ulid()}"
        now = utc_now()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO entity_map (
                    canonical_id, source_id, entity_type, entity_id,
                    uri, canonical_hash, occurred_at, recorded_at,
                    metadata_json, last_accessed_at, schema_version, kernel_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canonical_id,
                    entity.source_id,
                    entity.entity_type,
                    entity.entity_id,
                    entity.uri,
                    entity.canonical_hash,
                    entity.occurred_at.isoformat() if entity.occurred_at else None,
                    (entity.recorded_at or now).isoformat(),
                    json.dumps(entity.metadata) if entity.metadata else None,
                    now.isoformat(),
                    SCHEMA_VERSION,
                    get_kernel_version(),
                ),
            )
            conn.commit()

        logger.debug(
            "Registered entity",
            canonical_id=canonical_id,
            source_id=entity.source_id,
            entity_type=entity.entity_type,
        )
        return canonical_id

    def get_entity(self, canonical_id: str) -> EntityRef | None:
        """Get an entity by canonical_id."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM entity_map WHERE canonical_id = ?",
                (canonical_id,),
            ).fetchone()

        if not row:
            return None

        return self._row_to_entity(row)

    def get_entity_by_source(
        self,
        source_id: str,
        entity_type: str,
        entity_id: str,
    ) -> EntityRef | None:
        """Get an entity by source identifiers."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM entity_map 
                WHERE source_id = ? AND entity_type = ? AND entity_id = ?
                """,
                (source_id, entity_type, entity_id),
            ).fetchone()

        if not row:
            return None

        return self._row_to_entity(row)

    def list_entities(
        self,
        source_id: str | None = None,
        entity_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EntityRef]:
        """List entities with optional filtering."""
        conditions = []
        params: list[Any] = []

        if source_id:
            conditions.append("source_id = ?")
            params.append(source_id)
        if entity_type:
            conditions.append("entity_type = ?")
            params.append(entity_type)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.extend([limit, offset])

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT * FROM entity_map 
                {where_clause}
                ORDER BY recorded_at DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()

        return [self._row_to_entity(row) for row in rows]

    def delete_entity(self, canonical_id: str) -> bool:
        """Delete an entity and all its views."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            cursor = conn.execute(
                "DELETE FROM entity_map WHERE canonical_id = ?",
                (canonical_id,),
            )
            conn.commit()
            deleted = cursor.rowcount > 0

        if deleted:
            logger.debug("Deleted entity", canonical_id=canonical_id)

        return deleted

    def put_view(self, view: EntityView) -> None:
        """Store or update an entity view."""
        now = utc_now()

        # Ensure entity is registered
        canonical_id = view.entity.canonical_id
        if not canonical_id:
            canonical_id = self.register_entity(view.entity)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO entity_views (
                    view_id, canonical_id, view_type, segment_id,
                    content, content_hash, created_at, updated_at,
                    metadata_json, schema_version, kernel_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    view.view_id,
                    canonical_id,
                    view.view_type.value,
                    view.segment_id,
                    view.content,
                    view.content_hash,
                    view.created_at.isoformat(),
                    now.isoformat(),
                    json.dumps(view.metadata) if view.metadata else None,
                    SCHEMA_VERSION,
                    get_kernel_version(),
                ),
            )
            conn.commit()

        logger.debug(
            "Stored entity view",
            view_id=view.view_id,
            canonical_id=canonical_id,
            view_type=view.view_type.value,
        )

    def get_view(self, view_id: str) -> EntityView | None:
        """Get a view by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM entity_views WHERE view_id = ?",
                (view_id,),
            ).fetchone()

        if not row:
            return None

        return self._row_to_view(row)

    def list_views(
        self,
        canonical_id: str | None = None,
        view_type: EntityViewType | None = None,
        limit: int = 100,
    ) -> list[EntityView]:
        """List views for an entity or by type."""
        conditions = []
        params: list[Any] = []

        if canonical_id:
            conditions.append("canonical_id = ?")
            params.append(canonical_id)
        if view_type:
            conditions.append("view_type = ?")
            params.append(view_type.value)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT * FROM entity_views 
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [self._row_to_view(row) for row in rows]

    def delete_views(self, canonical_id: str) -> int:
        """Delete all views for an entity."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM entity_views WHERE canonical_id = ?",
                (canonical_id,),
            )
            conn.commit()
            return cursor.rowcount

    def record_access(self, canonical_id: str) -> None:
        """Record an access event for retention tracking."""
        now = utc_now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE entity_map 
                SET last_accessed_at = ?, access_count_30d = access_count_30d + 1
                WHERE canonical_id = ?
                """,
                (now.isoformat(), canonical_id),
            )
            conn.commit()

    def get_access_stats(self, canonical_id: str) -> dict[str, Any]:
        """Get access statistics for an entity."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT last_accessed_at, access_count_30d 
                FROM entity_map WHERE canonical_id = ?
                """,
                (canonical_id,),
            ).fetchone()

        if not row:
            return {}

        return {
            "last_accessed_at": row["last_accessed_at"],
            "access_count_30d": row["access_count_30d"] or 0,
        }

    def _row_to_entity(self, row: sqlite3.Row) -> EntityRef:
        """Convert a database row to EntityRef."""
        return EntityRef(
            source_id=row["source_id"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            uri=row["uri"],
            canonical_id=row["canonical_id"],
            canonical_hash=row["canonical_hash"],
            occurred_at=(
                datetime.fromisoformat(row["occurred_at"])
                if row["occurred_at"]
                else None
            ),
            recorded_at=(
                datetime.fromisoformat(row["recorded_at"])
                if row["recorded_at"]
                else None
            ),
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        )

    def _row_to_view(self, row: sqlite3.Row) -> EntityView:
        """Convert a database row to EntityView."""
        # We need to fetch the entity to construct the view
        entity = self.get_entity(row["canonical_id"])
        if not entity:
            # Fallback: create minimal entity ref
            entity = EntityRef(
                source_id="unknown",
                entity_type="unknown",
                entity_id=row["canonical_id"],
                canonical_id=row["canonical_id"],
            )

        return EntityView(
            view_id=row["view_id"],
            entity=entity,
            view_type=EntityViewType(row["view_type"]),
            segment_id=row["segment_id"],
            content=row["content"],
            content_hash=row["content_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        )

    def count_entities(self, source_id: str | None = None) -> int:
        """Count entities, optionally by source."""
        with sqlite3.connect(self.db_path) as conn:
            if source_id:
                result = conn.execute(
                    "SELECT COUNT(*) FROM entity_map WHERE source_id = ?",
                    (source_id,),
                ).fetchone()
            else:
                result = conn.execute("SELECT COUNT(*) FROM entity_map").fetchone()
            return result[0] if result else 0

    def count_views(self, view_type: EntityViewType | None = None) -> int:
        """Count views, optionally by type."""
        with sqlite3.connect(self.db_path) as conn:
            if view_type:
                result = conn.execute(
                    "SELECT COUNT(*) FROM entity_views WHERE view_type = ?",
                    (view_type.value,),
                ).fetchone()
            else:
                result = conn.execute("SELECT COUNT(*) FROM entity_views").fetchone()
            return result[0] if result else 0
