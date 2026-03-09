"""PostgreSQL implementation of EntityStore."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import SCHEMA_VERSION, get_kernel_version, utc_now
from agent_kernel.core.schemas.entity import EntityRef, EntityView, EntityViewType
from agent_kernel.memory.entity_store import EntityStore
from agent_kernel.memory.postgres.connection import PostgresConnection, PostgresConnectionPool

logger = structlog.get_logger(__name__)


class PostgresEntityStore(EntityStore):
    """PostgreSQL-backed entity store implementation."""

    def __init__(self, pool: PostgresConnectionPool) -> None:
        self._pool = pool
        self._init_schema()
        logger.info("postgres_entity_store_initialized")

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS entity_map (
                        canonical_id TEXT PRIMARY KEY,
                        source_id TEXT NOT NULL,
                        entity_type TEXT NOT NULL,
                        entity_id TEXT NOT NULL,
                        uri TEXT,
                        canonical_hash TEXT,
                        occurred_at TIMESTAMPTZ,
                        recorded_at TIMESTAMPTZ NOT NULL,
                        metadata_json JSONB,
                        last_accessed_at TIMESTAMPTZ,
                        access_count_30d INTEGER DEFAULT 0,
                        schema_version TEXT NOT NULL,
                        kernel_version TEXT NOT NULL,
                        UNIQUE(source_id, entity_type, entity_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_entity_source
                        ON entity_map(source_id, entity_type);
                    CREATE INDEX IF NOT EXISTS idx_entity_recorded
                        ON entity_map(recorded_at);

                    CREATE TABLE IF NOT EXISTS entity_views (
                        view_id TEXT PRIMARY KEY,
                        canonical_id TEXT NOT NULL
                            REFERENCES entity_map(canonical_id) ON DELETE CASCADE,
                        view_type TEXT NOT NULL,
                        segment_id TEXT,
                        content TEXT,
                        content_hash TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        metadata_json JSONB,
                        schema_version TEXT NOT NULL,
                        kernel_version TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_entity_views_canonical
                        ON entity_views(canonical_id);
                    CREATE INDEX IF NOT EXISTS idx_entity_views_type
                        ON entity_views(view_type);
                """)

    def register_entity(self, entity: EntityRef) -> str:
        """Register an entity and return its canonical_id."""
        existing = self.get_entity_by_source(
            entity.source_id, entity.entity_type, entity.entity_id
        )
        if existing and existing.canonical_id:
            return existing.canonical_id

        canonical_id = entity.canonical_id or f"ent_{generate_ulid()}"
        now = utc_now()

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO entity_map (
                        canonical_id, source_id, entity_type, entity_id,
                        uri, canonical_hash, occurred_at, recorded_at,
                        metadata_json, last_accessed_at, schema_version, kernel_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                    ON CONFLICT (source_id, entity_type, entity_id) DO NOTHING
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

        logger.debug(
            "entity_registered",
            canonical_id=canonical_id,
            source_id=entity.source_id,
        )
        return canonical_id

    def get_entity(self, canonical_id: str) -> EntityRef | None:
        """Get an entity by canonical_id."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM entity_map WHERE canonical_id = %s",
                    (canonical_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                columns = [desc[0] for desc in cur.description]
                return self._row_to_entity(dict(zip(columns, row)))

    def get_entity_by_source(
        self,
        source_id: str,
        entity_type: str,
        entity_id: str,
    ) -> EntityRef | None:
        """Get an entity by source identifiers."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM entity_map
                    WHERE source_id = %s AND entity_type = %s AND entity_id = %s
                    """,
                    (source_id, entity_type, entity_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                columns = [desc[0] for desc in cur.description]
                return self._row_to_entity(dict(zip(columns, row)))

    def list_entities(
        self,
        source_id: str | None = None,
        entity_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EntityRef]:
        """List entities with optional filtering."""
        conditions: list[str] = []
        params: list[Any] = []

        if source_id:
            conditions.append("source_id = %s")
            params.append(source_id)
        if entity_type:
            conditions.append("entity_type = %s")
            params.append(entity_type)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.extend([limit, offset])

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM entity_map
                    {where_clause}
                    ORDER BY recorded_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()

        return [self._row_to_entity(dict(zip(columns, row))) for row in rows]

    def delete_entity(self, canonical_id: str) -> bool:
        """Delete an entity and all its views (cascade)."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM entity_map WHERE canonical_id = %s",
                    (canonical_id,),
                )
                deleted = cur.rowcount > 0

        if deleted:
            logger.debug("entity_deleted", canonical_id=canonical_id)
        return deleted

    def put_view(self, view: EntityView) -> None:
        """Store or update an entity view."""
        now = utc_now()
        canonical_id = view.entity.canonical_id
        if not canonical_id:
            canonical_id = self.register_entity(view.entity)

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO entity_views (
                        view_id, canonical_id, view_type, segment_id,
                        content, content_hash, created_at, updated_at,
                        metadata_json, schema_version, kernel_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (view_id) DO UPDATE SET
                        content = EXCLUDED.content,
                        content_hash = EXCLUDED.content_hash,
                        updated_at = EXCLUDED.updated_at,
                        metadata_json = EXCLUDED.metadata_json
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

        logger.debug(
            "entity_view_stored",
            view_id=view.view_id,
            canonical_id=canonical_id,
        )

    def get_view(self, view_id: str) -> EntityView | None:
        """Get a view by ID."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM entity_views WHERE view_id = %s",
                    (view_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                columns = [desc[0] for desc in cur.description]
                return self._row_to_view(dict(zip(columns, row)))

    def list_views(
        self,
        canonical_id: str | None = None,
        view_type: EntityViewType | None = None,
        limit: int = 100,
    ) -> list[EntityView]:
        """List views for an entity or by type."""
        conditions: list[str] = []
        params: list[Any] = []

        if canonical_id:
            conditions.append("canonical_id = %s")
            params.append(canonical_id)
        if view_type:
            conditions.append("view_type = %s")
            params.append(view_type.value)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.append(limit)

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM entity_views
                    {where_clause}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()

        return [self._row_to_view(dict(zip(columns, row))) for row in rows]

    def delete_views(self, canonical_id: str) -> int:
        """Delete all views for an entity."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM entity_views WHERE canonical_id = %s",
                    (canonical_id,),
                )
                return cur.rowcount

    def record_access(self, canonical_id: str) -> None:
        """Record an access event for retention tracking."""
        now = utc_now()
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE entity_map
                    SET last_accessed_at = %s, access_count_30d = access_count_30d + 1
                    WHERE canonical_id = %s
                    """,
                    (now.isoformat(), canonical_id),
                )

    def get_access_stats(self, canonical_id: str) -> dict[str, Any]:
        """Get access statistics for an entity."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT last_accessed_at, access_count_30d
                    FROM entity_map WHERE canonical_id = %s
                    """,
                    (canonical_id,),
                )
                row = cur.fetchone()

        if not row:
            return {}

        return {
            "last_accessed_at": str(row[0]) if row[0] else None,
            "access_count_30d": row[1] or 0,
        }

    def _row_to_entity(self, row: dict[str, Any]) -> EntityRef:
        """Convert a database row dict to EntityRef."""
        metadata = row.get("metadata_json")
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}
        elif metadata is None:
            metadata = {}

        occurred_at = row.get("occurred_at")
        if isinstance(occurred_at, str):
            occurred_at = datetime.fromisoformat(occurred_at)

        recorded_at = row.get("recorded_at")
        if isinstance(recorded_at, str):
            recorded_at = datetime.fromisoformat(recorded_at)

        return EntityRef(
            source_id=row["source_id"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            uri=row.get("uri"),
            canonical_id=row["canonical_id"],
            canonical_hash=row.get("canonical_hash"),
            occurred_at=occurred_at,
            recorded_at=recorded_at,
            metadata=metadata,
        )

    def _row_to_view(self, row: dict[str, Any]) -> EntityView:
        """Convert a database row dict to EntityView."""
        entity = self.get_entity(row["canonical_id"])
        if not entity:
            entity = EntityRef(
                source_id="unknown",
                entity_type="unknown",
                entity_id=row["canonical_id"],
                canonical_id=row["canonical_id"],
            )

        metadata = row.get("metadata_json")
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}
        elif metadata is None:
            metadata = {}

        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        updated_at = row["updated_at"]
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        return EntityView(
            view_id=row["view_id"],
            entity=entity,
            view_type=EntityViewType(row["view_type"]),
            segment_id=row.get("segment_id"),
            content=row.get("content"),
            content_hash=row.get("content_hash"),
            created_at=created_at,
            updated_at=updated_at,
            metadata=metadata,
        )
