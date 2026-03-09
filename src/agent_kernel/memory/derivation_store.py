"""Derivation mapping + suppression registry stores (v1.1.6).

Provides lightweight persistence for derived artifacts to prevent duplication
and respect user suppression preferences.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.schemas.base import SCHEMA_VERSION, get_kernel_version, utc_now

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DerivationMappingRecord:
    """Mapping from a source item to its derived artifact."""

    source_system: str
    source_container_id: str
    source_item_id: str
    derivation_kind: str
    target_system: str
    target_item_id: str
    last_synced_etag: str | None = None
    last_synced_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class SuppressionRecord:
    """Suppression record for derived artifacts."""

    source_system: str
    source_item_id: str
    artifact_kind: str
    suppressed_until: datetime
    reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def is_active(self, now: datetime | None = None) -> bool:
        """Return True if suppression is still active."""
        now = now or utc_now()
        return self.suppressed_until > now


class DerivationMappingStore:
    """SQLite-backed store for derivation mappings."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
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
            conn.commit()

    def get_mapping(
        self,
        *,
        source_system: str,
        source_container_id: str,
        source_item_id: str,
        derivation_kind: str,
    ) -> DerivationMappingRecord | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM derivation_mappings
                WHERE source_system = ?
                  AND source_container_id = ?
                  AND source_item_id = ?
                  AND derivation_kind = ?
                """,
                (source_system, source_container_id, source_item_id, derivation_kind),
            ).fetchone()
        return self._row_to_mapping(row) if row else None

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
            conditions.append("source_system = ?")
            params.append(source_system)
        if source_container_id:
            conditions.append("source_container_id = ?")
            params.append(source_container_id)
        if source_item_id:
            conditions.append("source_item_id = ?")
            params.append(source_item_id)
        if derivation_kind:
            conditions.append("derivation_kind = ?")
            params.append(derivation_kind)

        where_clause = " AND ".join(conditions)
        if where_clause:
            where_clause = "WHERE " + where_clause

        params.append(limit)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT * FROM derivation_mappings
                {where_clause}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_mapping(row) for row in rows]

    def list_by_target(
        self,
        *,
        target_system: str,
        target_item_id: str | None = None,
        limit: int = 200,
    ) -> list[DerivationMappingRecord]:
        conditions = ["target_system = ?"]
        params: list[Any] = [target_system]
        if target_item_id:
            conditions.append("target_item_id = ?")
            params.append(target_item_id)

        where_clause = " AND ".join(conditions)
        params.append(limit)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT * FROM derivation_mappings
                WHERE {where_clause}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_mapping(row) for row in rows]

    def put_mapping(self, record: DerivationMappingRecord) -> None:
        now = utc_now()
        created_at = record.created_at or now
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO derivation_mappings (
                    source_system,
                    source_container_id,
                    source_item_id,
                    derivation_kind,
                    target_system,
                    target_item_id,
                    last_synced_etag,
                    last_synced_at,
                    created_at,
                    updated_at,
                    schema_version,
                    kernel_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    source_system,
                    source_container_id,
                    source_item_id,
                    derivation_kind
                ) DO UPDATE SET
                    target_system=excluded.target_system,
                    target_item_id=excluded.target_item_id,
                    last_synced_etag=excluded.last_synced_etag,
                    last_synced_at=excluded.last_synced_at,
                    updated_at=excluded.updated_at,
                    schema_version=excluded.schema_version,
                    kernel_version=excluded.kernel_version
                """,
                (
                    record.source_system,
                    record.source_container_id,
                    record.source_item_id,
                    record.derivation_kind,
                    record.target_system,
                    record.target_item_id,
                    record.last_synced_etag,
                    record.last_synced_at.isoformat()
                    if record.last_synced_at
                    else None,
                    created_at.isoformat(),
                    now.isoformat(),
                    SCHEMA_VERSION,
                    get_kernel_version(),
                ),
            )
            conn.commit()

    def delete_mapping(
        self,
        *,
        source_system: str,
        source_container_id: str,
        source_item_id: str,
        derivation_kind: str,
    ) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                DELETE FROM derivation_mappings
                WHERE source_system = ?
                  AND source_container_id = ?
                  AND source_item_id = ?
                  AND derivation_kind = ?
                """,
                (source_system, source_container_id, source_item_id, derivation_kind),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def _row_to_mapping(row: sqlite3.Row) -> DerivationMappingRecord:
        return DerivationMappingRecord(
            source_system=row["source_system"],
            source_container_id=row["source_container_id"],
            source_item_id=row["source_item_id"],
            derivation_kind=row["derivation_kind"],
            target_system=row["target_system"],
            target_item_id=row["target_item_id"],
            last_synced_etag=row["last_synced_etag"],
            last_synced_at=(
                datetime.fromisoformat(row["last_synced_at"])
                if row["last_synced_at"]
                else None
            ),
            created_at=(
                datetime.fromisoformat(row["created_at"])
                if row["created_at"]
                else None
            ),
            updated_at=(
                datetime.fromisoformat(row["updated_at"])
                if row["updated_at"]
                else None
            ),
        )


class SuppressionRegistry:
    """SQLite-backed suppression registry."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
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
            conn.commit()

    def get_suppression(
        self, *, source_system: str, source_item_id: str, artifact_kind: str
    ) -> SuppressionRecord | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM suppression_registry
                WHERE source_system = ?
                  AND source_item_id = ?
                  AND artifact_kind = ?
                """,
                (source_system, source_item_id, artifact_kind),
            ).fetchone()
        return self._row_to_suppression(row) if row else None

    def put_suppression(self, record: SuppressionRecord) -> None:
        now = utc_now()
        created_at = record.created_at or now
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO suppression_registry (
                    source_system,
                    source_item_id,
                    artifact_kind,
                    suppressed_until,
                    reason,
                    created_at,
                    updated_at,
                    schema_version,
                    kernel_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_system, source_item_id, artifact_kind) DO UPDATE SET
                    suppressed_until=excluded.suppressed_until,
                    reason=excluded.reason,
                    updated_at=excluded.updated_at,
                    schema_version=excluded.schema_version,
                    kernel_version=excluded.kernel_version
                """,
                (
                    record.source_system,
                    record.source_item_id,
                    record.artifact_kind,
                    record.suppressed_until.isoformat(),
                    record.reason,
                    created_at.isoformat(),
                    now.isoformat(),
                    SCHEMA_VERSION,
                    get_kernel_version(),
                ),
            )
            conn.commit()

    def delete_suppression(
        self, *, source_system: str, source_item_id: str, artifact_kind: str
    ) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                DELETE FROM suppression_registry
                WHERE source_system = ?
                  AND source_item_id = ?
                  AND artifact_kind = ?
                """,
                (source_system, source_item_id, artifact_kind),
            )
            conn.commit()
            return cursor.rowcount > 0

    def clear_expired(self, now: datetime | None = None) -> int:
        now = now or utc_now()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                DELETE FROM suppression_registry
                WHERE suppressed_until <= ?
                """,
                (now.isoformat(),),
            )
            conn.commit()
            return cursor.rowcount

    @staticmethod
    def _row_to_suppression(row: sqlite3.Row) -> SuppressionRecord:
        return SuppressionRecord(
            source_system=row["source_system"],
            source_item_id=row["source_item_id"],
            artifact_kind=row["artifact_kind"],
            suppressed_until=datetime.fromisoformat(row["suppressed_until"]),
            reason=row["reason"],
            created_at=(
                datetime.fromisoformat(row["created_at"])
                if row["created_at"]
                else None
            ),
            updated_at=(
                datetime.fromisoformat(row["updated_at"])
                if row["updated_at"]
                else None
            ),
        )
