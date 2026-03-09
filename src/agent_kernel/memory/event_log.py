"""Append-only event log for audit trail.

The event log is the source of truth timeline for the system.
All significant events are recorded here immutably.
"""

from __future__ import annotations

import fcntl
import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import structlog
from pydantic import Field

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import VersionedModel, utc_now

logger = structlog.get_logger(__name__)


class EventType(str, Enum):
    """Types of events recorded in the log."""

    # Trace events
    TRACE_CREATED = "trace.created"
    TRACE_COMPLETED = "trace.completed"

    # Tool events
    TOOL_CALL = "tool_call"
    TOOL_CALLED = "tool.called"
    TOOL_SUCCEEDED = "tool.succeeded"
    TOOL_FAILED = "tool.failed"

    # Task events
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_COMPLETED = "task.completed"

    # Note events
    NOTE_CREATED = "note.created"
    NOTE_UPDATED = "note.updated"

    # Approval events
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_GRANTED = "approval.granted"
    APPROVAL_DENIED = "approval.denied"

    # Workflow events
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"

    # System events
    SYSTEM_INITIALIZED = "system.initialized"
    CONFIG_CHANGED = "config.changed"

    # Notification events
    WORKFLOW_NOTIFICATION = "workflow.notification"

    # v1.2: LLM cache events
    LLM_CACHE_HIT = "llm_cache.hit"
    LLM_CACHE_MISS = "llm_cache.miss"

    # v1.2: Cost anomaly events
    COST_ANOMALY = "cost.anomaly"

    # v1.2: Experience mining events
    EXPERIENCE_CASE_CREATED = "experience.case_created"
    LESSON_CANDIDATE_GENERATED = "experience.lesson_candidate"

    # v1.0.6: Context graph events
    TRAJECTORY_CREATED = "context_graph.trajectory_created"
    KNOWLEDGE_CREATED = "context_graph.knowledge_created"
    KNOWLEDGE_UPDATED = "context_graph.knowledge_updated"
    KNOWLEDGE_COMPACTED = "context_graph.knowledge_compacted"
    KNOWLEDGE_PRUNED = "context_graph.knowledge_pruned"
    CO_OCCURRENCE_UPDATED = "context_graph.co_occurrence_updated"
    TYPE_DISCOVERED = "context_graph.type_discovered"


class Event(VersionedModel):
    """An immutable event record.

    Time semantics:
    - occurred_at: When the event happened in reality (e.g., file modified time)
    - recorded_at: When the kernel recorded this event

    This distinction is critical for:
    - File watcher events (file may have changed before we noticed)
    - Backfills (we may record events for past occurrences)
    - Replays (we record at current time but mark original occurrence)
    """

    event_id: str = Field(default_factory=generate_ulid)
    event_type: EventType
    source: str  # Component that emitted the event
    entity_id: str | None = None  # ID of related entity (trace, task, etc.)
    entity_type: str | None = None  # Type of related entity

    # Time semantics: occurred vs recorded
    occurred_at: datetime = Field(
        default_factory=utc_now,
        description="When the event actually happened in reality",
    )
    recorded_at: datetime = Field(
        default_factory=utc_now,
        description="When the kernel recorded this event",
    )

    # Event payload (supports 'data' as alias for backwards compatibility)
    payload: dict[str, Any] = Field(
        default_factory=dict,
        alias="data",
        description="Event-specific data",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra context (tags, correlation IDs, etc.)",
    )

    @property
    def data(self) -> dict[str, Any]:
        """Alias for payload (backwards compatibility)."""
        return self.payload

    @property
    def timestamp(self) -> datetime:
        """Alias for occurred_at (backwards compatibility)."""
        return self.occurred_at


class EventLog(ABC):
    """Abstract interface for append-only event storage."""

    @abstractmethod
    def append(self, event: Event) -> None:
        """Append an event to the log.

        Args:
            event: The event to record.
        """

    @abstractmethod
    def get_events(
        self,
        *,
        event_type: EventType | None = None,
        entity_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Query events from the log.

        Args:
            event_type: Filter by event type.
            entity_id: Filter by related entity.
            since: Filter events after this time.
            until: Filter events before this time.
            limit: Maximum events to return.

        Returns:
            List of matching events in chronological order.
        """

    @abstractmethod
    def count(
        self,
        *,
        event_type: EventType | None = None,
        since: datetime | None = None,
    ) -> int:
        """Count events matching criteria."""

    @abstractmethod
    def close(self) -> None:
        """Close the event log."""

    def emit(
        self,
        event_type: EventType,
        source: str,
        *,
        entity_id: str | None = None,
        entity_type: str | None = None,
        data: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> Event:
        """Convenience method to create and append an event.

        Args:
            event_type: Type of event.
            source: Component emitting the event.
            entity_id: ID of related entity.
            entity_type: Type of related entity.
            data: Event payload (deprecated, use payload).
            payload: Event payload.
            metadata: Extra context.
            occurred_at: When the event occurred (defaults to now).

        Returns:
            The created event.
        """
        # Support both 'data' (legacy) and 'payload' (new)
        event_payload = payload or data or {}
        event_occurred_at = occurred_at or utc_now()

        event = Event(
            event_type=event_type,
            source=source,
            entity_id=entity_id,
            entity_type=entity_type,
            occurred_at=event_occurred_at,
            recorded_at=utc_now(),
            payload=event_payload,
            metadata=metadata or {},
        )
        self.append(event)
        return event

    def log_event(
        self,
        *,
        event_type: str | EventType,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        source: str = "legacy",
        entity_id: str | None = None,
        entity_type: str | None = None,
    ) -> str:
        """Legacy helper for older call sites/tests."""
        if isinstance(event_type, EventType):
            event_type_value = event_type
        else:
            try:
                event_type_value = EventType(event_type)
            except ValueError:
                dotted = str(event_type).replace("_", ".")
                event_type_value = EventType(dotted)
        event = self.emit(
            event_type=event_type_value,
            source=source,
            entity_id=entity_id,
            entity_type=entity_type,
            payload=payload,
            metadata=metadata,
        )
        return event.event_id

    def get_recent_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Legacy helper returning recent events as dicts."""
        events = self.get_events(limit=limit)
        return [
            {
                "event_id": e.event_id,
                "event_type": e.event_type.value.replace(".", "_"),
                "source": e.source,
                "entity_id": e.entity_id,
                "entity_type": e.entity_type,
                "occurred_at": e.occurred_at,
                "recorded_at": e.recorded_at,
                "payload": e.payload,
                "metadata": e.metadata,
            }
            for e in events
        ]


class SQLiteEventLog(EventLog):
    """SQLite-backed append-only event log."""

    def __init__(self, db_path: str | Path) -> None:
        """Initialize SQLite event log.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        logger.info("sqlite_event_log_initialized", db_path=str(self._db_path))

    def _init_schema(self) -> None:
        """Initialize database schema."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                entity_id TEXT,
                entity_type TEXT,
                occurred_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
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
        self._conn.commit()

        # Migrate old schema if needed (add new columns)
        try:
            self._conn.execute("SELECT occurred_at FROM events LIMIT 1")
        except sqlite3.OperationalError:
            # Old schema - migrate by adding columns
            logger.info("migrating_event_log_schema")
            self._conn.executescript("""
                ALTER TABLE events ADD COLUMN occurred_at TEXT;
                ALTER TABLE events ADD COLUMN recorded_at TEXT;
                ALTER TABLE events ADD COLUMN payload_json TEXT;
                ALTER TABLE events ADD COLUMN schema_version TEXT;
                ALTER TABLE events ADD COLUMN kernel_version TEXT;

                UPDATE events SET
                    occurred_at = timestamp,
                    recorded_at = timestamp,
                    payload_json = data_json
                WHERE occurred_at IS NULL;
            """)
            self._conn.commit()

    def append(self, event: Event) -> None:
        """Append an event to the log."""
        self._conn.execute(
            """
            INSERT INTO events
            (event_id, event_type, source, entity_id, entity_type,
             occurred_at, recorded_at, payload_json, metadata_json,
             schema_version, kernel_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        self._conn.commit()
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
            conditions.append("event_type = ?")
            params.append(event_type.value)

        if entity_id:
            conditions.append("entity_id = ?")
            params.append(entity_id)

        if since:
            conditions.append("occurred_at >= ?")
            params.append(since.isoformat())

        if until:
            conditions.append("occurred_at <= ?")
            params.append(until.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        cursor = self._conn.execute(
            f"""
            SELECT * FROM events
            WHERE {where_clause}
            ORDER BY occurred_at ASC
            LIMIT ?
            """,
            params,
        )

        events = []
        for row in cursor.fetchall():
            events.append(self._row_to_event(row))
        return events

    def _row_to_event(self, row: sqlite3.Row) -> Event:
        """Convert database row to Event."""
        # Handle both old and new schema
        occurred_at_str = row["occurred_at"] if "occurred_at" in row.keys() else row.get("timestamp")
        recorded_at_str = row["recorded_at"] if "recorded_at" in row.keys() else row.get("timestamp")
        payload_json = row["payload_json"] if "payload_json" in row.keys() else row.get("data_json")

        return Event(
            event_id=row["event_id"],
            event_type=EventType(row["event_type"]),
            source=row["source"],
            entity_id=row["entity_id"],
            entity_type=row["entity_type"],
            occurred_at=datetime.fromisoformat(occurred_at_str) if occurred_at_str else utc_now(),
            recorded_at=datetime.fromisoformat(recorded_at_str) if recorded_at_str else utc_now(),
            payload=json.loads(payload_json or "{}"),
            metadata=json.loads(row["metadata_json"] or "{}"),
            schema_version=row["schema_version"] if "schema_version" in row.keys() else "1.0.0",
            kernel_version=row["kernel_version"] if "kernel_version" in row.keys() else "dev",
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
            conditions.append("event_type = ?")
            params.append(event_type.value)

        if since:
            conditions.append("occurred_at >= ?")
            params.append(since.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        cursor = self._conn.execute(
            f"SELECT COUNT(*) as cnt FROM events WHERE {where_clause}",
            params,
        )
        return cursor.fetchone()["cnt"]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
        logger.info("sqlite_event_log_closed")


class JSONLEventLog(EventLog):
    """JSONL file-backed append-only event log."""

    def __init__(self, file_path: str | Path) -> None:
        """Initialize JSONL event log.

        Args:
            file_path: Path to the JSONL file.
        """
        self._file_path = Path(file_path)
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("jsonl_event_log_initialized", file_path=str(self._file_path))

    def append(self, event: Event) -> None:
        """Append an event to the log."""
        line = event.model_dump_json() + "\n"

        with open(self._file_path, "a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(line)
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        logger.debug(
            "event_appended_to_jsonl",
            event_id=event.event_id,
            event_type=event.event_type.value,
        )

    def _read_all(self) -> list[Event]:
        """Read all events from file."""
        if not self._file_path.exists():
            return []

        events = []
        with open(self._file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(Event.model_validate_json(line))
        return events

    def get_events(
        self,
        *,
        event_type: EventType | None = None,
        entity_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Query events from the log.

        Note: This reads the entire file. For large logs, use SQLite.
        """
        events = self._read_all()

        # Apply filters
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if entity_id:
            events = [e for e in events if e.entity_id == entity_id]
        if since:
            events = [e for e in events if e.timestamp >= since]
        if until:
            events = [e for e in events if e.timestamp <= until]

        return events[:limit]

    def count(
        self,
        *,
        event_type: EventType | None = None,
        since: datetime | None = None,
    ) -> int:
        """Count events matching criteria."""
        events = self._read_all()

        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if since:
            events = [e for e in events if e.timestamp >= since]

        return len(events)

    def close(self) -> None:
        """No-op for JSONL (file handles are closed per-write)."""
        logger.info("jsonl_event_log_closed")
