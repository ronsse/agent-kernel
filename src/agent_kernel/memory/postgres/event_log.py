"""PostgreSQL implementation of EventLog."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.memory.event_log import Event, EventLog, EventType
from agent_kernel.memory.postgres.connection import PostgresConnection, PostgresConnectionPool

logger = structlog.get_logger(__name__)


class PostgresEventLog(EventLog):
    """PostgreSQL-backed append-only event log."""

    def __init__(self, pool: PostgresConnectionPool) -> None:
        self._pool = pool
        self._init_schema()
        logger.info("postgres_event_log_initialized")

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS events (
                        event_id TEXT PRIMARY KEY,
                        event_type TEXT NOT NULL,
                        source TEXT NOT NULL,
                        entity_id TEXT,
                        entity_type TEXT,
                        occurred_at TIMESTAMPTZ NOT NULL,
                        recorded_at TIMESTAMPTZ NOT NULL,
                        payload_json JSONB NOT NULL DEFAULT '{}',
                        metadata_json JSONB NOT NULL DEFAULT '{}',
                        schema_version TEXT,
                        kernel_version TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_events_occurred_at
                        ON events(occurred_at);
                    CREATE INDEX IF NOT EXISTS idx_events_recorded_at
                        ON events(recorded_at);
                    CREATE INDEX IF NOT EXISTS idx_events_type
                        ON events(event_type);
                    CREATE INDEX IF NOT EXISTS idx_events_entity
                        ON events(entity_id);
                    CREATE INDEX IF NOT EXISTS idx_events_source
                        ON events(source);
                """)

    def append(self, event: Event) -> None:
        """Append an event to the log."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO events
                        (event_id, event_type, source, entity_id, entity_type,
                         occurred_at, recorded_at, payload_json, metadata_json,
                         schema_version, kernel_version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                    """,
                    (
                        event.event_id,
                        event.event_type.value,
                        event.source,
                        event.entity_id,
                        event.entity_type,
                        event.occurred_at.isoformat(),
                        event.recorded_at.isoformat(),
                        json.dumps(event.payload),
                        json.dumps(event.metadata),
                        event.schema_version,
                        event.kernel_version,
                    ),
                )

        logger.debug(
            "event_appended",
            event_id=event.event_id,
            event_type=event.event_type.value,
        )

    def get_events(
        self,
        *,
        event_type: EventType | None = None,
        entity_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Query events from the log."""
        conditions: list[str] = []
        params: list[Any] = []

        if event_type:
            conditions.append("event_type = %s")
            params.append(event_type.value)

        if entity_id:
            conditions.append("entity_id = %s")
            params.append(entity_id)

        if since:
            conditions.append("occurred_at >= %s")
            params.append(since.isoformat())

        if until:
            conditions.append("occurred_at <= %s")
            params.append(until.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "TRUE"
        params.append(limit)

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT event_id, event_type, source, entity_id, entity_type,
                           occurred_at, recorded_at, payload_json, metadata_json,
                           schema_version, kernel_version
                    FROM events
                    WHERE {where_clause}
                    ORDER BY occurred_at ASC
                    LIMIT %s
                    """,
                    params,
                )
                rows = cur.fetchall()

        return [self._row_to_event(row) for row in rows]

    def _row_to_event(self, row: tuple) -> Event:
        """Convert database row to Event."""
        payload = row[7] if isinstance(row[7], dict) else json.loads(row[7] or "{}")
        metadata = row[8] if isinstance(row[8], dict) else json.loads(row[8] or "{}")

        occurred_at = row[5]
        if isinstance(occurred_at, str):
            occurred_at = datetime.fromisoformat(occurred_at)

        recorded_at = row[6]
        if isinstance(recorded_at, str):
            recorded_at = datetime.fromisoformat(recorded_at)

        return Event(
            event_id=row[0],
            event_type=EventType(row[1]),
            source=row[2],
            entity_id=row[3],
            entity_type=row[4],
            occurred_at=occurred_at or utc_now(),
            recorded_at=recorded_at or utc_now(),
            payload=payload,
            metadata=metadata,
            schema_version=row[9] or "1.0.0",
            kernel_version=row[10] or "dev",
        )

    def count(
        self,
        *,
        event_type: EventType | None = None,
        since: datetime | None = None,
    ) -> int:
        """Count events matching criteria."""
        conditions: list[str] = []
        params: list[Any] = []

        if event_type:
            conditions.append("event_type = %s")
            params.append(event_type.value)

        if since:
            conditions.append("occurred_at >= %s")
            params.append(since.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "TRUE"

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) FROM events WHERE {where_clause}",
                    params,
                )
                return cur.fetchone()[0]

    def close(self) -> None:
        """No-op; pool manages connections."""
        logger.info("postgres_event_log_closed")
