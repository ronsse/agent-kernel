"""PostgreSQL implementation of DerivationMappingStore and SuppressionRegistry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from agent_kernel.core.schemas.base import SCHEMA_VERSION, get_kernel_version, utc_now
from agent_kernel.memory.derivation_store import (
    DerivationMappingRecord,
    SuppressionRecord,
)
from agent_kernel.memory.postgres.connection import PostgresConnection, PostgresConnectionPool

logger = structlog.get_logger(__name__)


class PostgresDerivationMappingStore:
    """PostgreSQL-backed store for derivation mappings."""

    def __init__(self, pool: PostgresConnectionPool) -> None:
        self._pool = pool
        self._init_schema()
        logger.info("postgres_derivation_mapping_store_initialized")

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS derivation_mappings (
                        source_system TEXT NOT NULL,
                        source_container_id TEXT NOT NULL,
                        source_item_id TEXT NOT NULL,
                        derivation_kind TEXT NOT NULL,
                        target_system TEXT NOT NULL,
                        target_item_id TEXT NOT NULL,
                        last_synced_etag TEXT,
                        last_synced_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        schema_version TEXT NOT NULL,
                        kernel_version TEXT NOT NULL,
                        UNIQUE(source_system, source_container_id, source_item_id, derivation_kind)
                    );

                    CREATE INDEX IF NOT EXISTS idx_pg_derivation_source
                        ON derivation_mappings(source_system, source_container_id, source_item_id);
                    CREATE INDEX IF NOT EXISTS idx_pg_derivation_target
                        ON derivation_mappings(target_system, target_item_id);
                """)

    def get_mapping(
        self,
        *,
        source_system: str,
        source_container_id: str,
        source_item_id: str,
        derivation_kind: str,
    ) -> DerivationMappingRecord | None:
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM derivation_mappings
                    WHERE source_system = %s AND source_container_id = %s
                      AND source_item_id = %s AND derivation_kind = %s
                    """,
                    (source_system, source_container_id, source_item_id, derivation_kind),
                )
                row = cur.fetchone()
                if not row:
                    return None
                columns = [desc[0] for desc in cur.description]
                return self._row_to_mapping(dict(zip(columns, row)))

    def list_by_source(
        self,
        *,
        source_system: str | None = None,
        source_container_id: str | None = None,
        source_item_id: str | None = None,
        derivation_kind: str | None = None,
        limit: int = 200,
    ) -> list[DerivationMappingRecord]:
        conditions: list[str] = []
        params: list[Any] = []
        if source_system:
            conditions.append("source_system = %s")
            params.append(source_system)
        if source_container_id:
            conditions.append("source_container_id = %s")
            params.append(source_container_id)
        if source_item_id:
            conditions.append("source_item_id = %s")
            params.append(source_item_id)
        if derivation_kind:
            conditions.append("derivation_kind = %s")
            params.append(derivation_kind)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM derivation_mappings
                    {where_clause}
                    ORDER BY updated_at DESC LIMIT %s
                    """,
                    params,
                )
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
        return [self._row_to_mapping(dict(zip(columns, row))) for row in rows]

    def list_by_target(
        self,
        *,
        target_system: str,
        target_item_id: str | None = None,
        limit: int = 200,
    ) -> list[DerivationMappingRecord]:
        conditions = ["target_system = %s"]
        params: list[Any] = [target_system]
        if target_item_id:
            conditions.append("target_item_id = %s")
            params.append(target_item_id)

        where_clause = " AND ".join(conditions)
        params.append(limit)

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM derivation_mappings
                    WHERE {where_clause}
                    ORDER BY updated_at DESC LIMIT %s
                    """,
                    params,
                )
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
        return [self._row_to_mapping(dict(zip(columns, row))) for row in rows]

    def put_mapping(self, record: DerivationMappingRecord) -> None:
        now = utc_now()
        created_at = record.created_at or now

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO derivation_mappings (
                        source_system, source_container_id, source_item_id,
                        derivation_kind, target_system, target_item_id,
                        last_synced_etag, last_synced_at,
                        created_at, updated_at, schema_version, kernel_version
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (source_system, source_container_id, source_item_id, derivation_kind)
                    DO UPDATE SET
                        target_system = EXCLUDED.target_system,
                        target_item_id = EXCLUDED.target_item_id,
                        last_synced_etag = EXCLUDED.last_synced_etag,
                        last_synced_at = EXCLUDED.last_synced_at,
                        updated_at = EXCLUDED.updated_at,
                        schema_version = EXCLUDED.schema_version,
                        kernel_version = EXCLUDED.kernel_version
                    """,
                    (
                        record.source_system, record.source_container_id,
                        record.source_item_id, record.derivation_kind,
                        record.target_system, record.target_item_id,
                        record.last_synced_etag,
                        record.last_synced_at.isoformat() if record.last_synced_at else None,
                        created_at.isoformat(), now.isoformat(),
                        SCHEMA_VERSION, get_kernel_version(),
                    ),
                )

    def delete_mapping(
        self,
        *,
        source_system: str,
        source_container_id: str,
        source_item_id: str,
        derivation_kind: str,
    ) -> bool:
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM derivation_mappings
                    WHERE source_system = %s AND source_container_id = %s
                      AND source_item_id = %s AND derivation_kind = %s
                    """,
                    (source_system, source_container_id, source_item_id, derivation_kind),
                )
                return cur.rowcount > 0

    @staticmethod
    def _row_to_mapping(row: dict[str, Any]) -> DerivationMappingRecord:
        last_synced_at = row.get("last_synced_at")
        if isinstance(last_synced_at, str):
            last_synced_at = datetime.fromisoformat(last_synced_at)

        created_at = row.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        updated_at = row.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        return DerivationMappingRecord(
            source_system=row["source_system"],
            source_container_id=row["source_container_id"],
            source_item_id=row["source_item_id"],
            derivation_kind=row["derivation_kind"],
            target_system=row["target_system"],
            target_item_id=row["target_item_id"],
            last_synced_etag=row.get("last_synced_etag"),
            last_synced_at=last_synced_at,
            created_at=created_at,
            updated_at=updated_at,
        )


class PostgresSuppressionRegistry:
    """PostgreSQL-backed suppression registry."""

    def __init__(self, pool: PostgresConnectionPool) -> None:
        self._pool = pool
        self._init_schema()
        logger.info("postgres_suppression_registry_initialized")

    def _init_schema(self) -> None:
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS suppression_registry (
                        source_system TEXT NOT NULL,
                        source_item_id TEXT NOT NULL,
                        artifact_kind TEXT NOT NULL,
                        suppressed_until TIMESTAMPTZ NOT NULL,
                        reason TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        schema_version TEXT NOT NULL,
                        kernel_version TEXT NOT NULL,
                        UNIQUE(source_system, source_item_id, artifact_kind)
                    );

                    CREATE INDEX IF NOT EXISTS idx_pg_suppression_expiry
                        ON suppression_registry(suppressed_until);
                """)

    def get_suppression(
        self, *, source_system: str, source_item_id: str, artifact_kind: str
    ) -> SuppressionRecord | None:
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM suppression_registry
                    WHERE source_system = %s AND source_item_id = %s AND artifact_kind = %s
                    """,
                    (source_system, source_item_id, artifact_kind),
                )
                row = cur.fetchone()
                if not row:
                    return None
                columns = [desc[0] for desc in cur.description]
                return self._row_to_suppression(dict(zip(columns, row)))

    def put_suppression(self, record: SuppressionRecord) -> None:
        now = utc_now()
        created_at = record.created_at or now

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO suppression_registry (
                        source_system, source_item_id, artifact_kind,
                        suppressed_until, reason,
                        created_at, updated_at, schema_version, kernel_version
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (source_system, source_item_id, artifact_kind)
                    DO UPDATE SET
                        suppressed_until = EXCLUDED.suppressed_until,
                        reason = EXCLUDED.reason,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        record.source_system, record.source_item_id,
                        record.artifact_kind, record.suppressed_until.isoformat(),
                        record.reason, created_at.isoformat(), now.isoformat(),
                        SCHEMA_VERSION, get_kernel_version(),
                    ),
                )

    def delete_suppression(
        self, *, source_system: str, source_item_id: str, artifact_kind: str
    ) -> bool:
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM suppression_registry
                    WHERE source_system = %s AND source_item_id = %s AND artifact_kind = %s
                    """,
                    (source_system, source_item_id, artifact_kind),
                )
                return cur.rowcount > 0

    def clear_expired(self, now: datetime | None = None) -> int:
        now = now or utc_now()
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM suppression_registry WHERE suppressed_until <= %s",
                    (now.isoformat(),),
                )
                return cur.rowcount

    @staticmethod
    def _row_to_suppression(row: dict[str, Any]) -> SuppressionRecord:
        suppressed_until = row["suppressed_until"]
        if isinstance(suppressed_until, str):
            suppressed_until = datetime.fromisoformat(suppressed_until)

        created_at = row.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        updated_at = row.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        return SuppressionRecord(
            source_system=row["source_system"],
            source_item_id=row["source_item_id"],
            artifact_kind=row["artifact_kind"],
            suppressed_until=suppressed_until,
            reason=row.get("reason"),
            created_at=created_at,
            updated_at=updated_at,
        )
