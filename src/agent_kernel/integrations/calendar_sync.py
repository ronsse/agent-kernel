"""Calendar Sync Adapter - Abstract interface for external calendars.

Provides a pluggable interface for syncing calendar events between
the kernel and external calendar systems (Google Calendar, Outlook, etc.).

All external writes are approval-gated per the integration patterns:
- Creating events requires approval
- Updating events requires approval
- Reading events does not require approval

Example implementations:
- MemoryCalendarAdapter: In-memory adapter for testing
- ICSCalendarAdapter: Import from ICS files
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import structlog

from agent_kernel.core.schemas.base import utc_now

logger = structlog.get_logger(__name__)


class EventStatus(str, Enum):
    """Status of a calendar event."""

    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"
    CANCELLED = "cancelled"


class EventVisibility(str, Enum):
    """Visibility of a calendar event."""

    PUBLIC = "public"
    PRIVATE = "private"
    CONFIDENTIAL = "confidential"


class SyncOperation(str, Enum):
    """Type of sync operation."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    SKIP = "skip"


@dataclass
class CalendarEvent:
    """Representation of a calendar event.

    Maps between kernel format and external calendar format.
    """

    # Identity
    external_id: str  # ID in external calendar
    kernel_event_id: str | None = None  # Corresponding kernel ID
    calendar_id: str | None = None  # Which calendar this belongs to

    # Core fields
    title: str = ""
    description: str | None = None
    location: str | None = None

    # Timing
    start: datetime | None = None
    end: datetime | None = None
    all_day: bool = False
    timezone: str = "UTC"

    # Status
    status: EventStatus = EventStatus.CONFIRMED
    visibility: EventVisibility = EventVisibility.PUBLIC

    # Participants
    organizer: str | None = None
    attendees: list[str] = field(default_factory=list)

    # Recurrence
    recurrence_rule: str | None = None  # RRULE format
    recurring_event_id: str | None = None  # Parent event for instances

    # Links to kernel entities
    related_note_ids: list[str] = field(default_factory=list)
    related_task_ids: list[str] = field(default_factory=list)

    # Metadata
    tags: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Raw data from external system
    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> timedelta | None:
        """Get event duration."""
        if self.start and self.end:
            return self.end - self.start
        return None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "external_id": self.external_id,
            "kernel_event_id": self.kernel_event_id,
            "calendar_id": self.calendar_id,
            "title": self.title,
            "description": self.description,
            "location": self.location,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "all_day": self.all_day,
            "timezone": self.timezone,
            "status": self.status.value,
            "visibility": self.visibility.value,
            "organizer": self.organizer,
            "attendees": self.attendees,
            "recurrence_rule": self.recurrence_rule,
            "related_note_ids": self.related_note_ids,
            "related_task_ids": self.related_task_ids,
            "tags": self.tags,
        }


@dataclass
class SyncResult:
    """Result of a calendar sync operation."""

    success: bool
    operation: SyncOperation
    event: CalendarEvent | None = None
    kernel_event_id: str | None = None
    external_id: str | None = None
    error: str | None = None
    requires_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "operation": self.operation.value,
            "kernel_event_id": self.kernel_event_id,
            "external_id": self.external_id,
            "error": self.error,
            "requires_approval": self.requires_approval,
        }


@dataclass
class CalendarSyncSummary:
    """Summary of a batch calendar sync operation."""

    started_at: datetime
    completed_at: datetime | None = None
    total_events: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    pending_approval: int = 0
    results: list[SyncResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "total_events": self.total_events,
            "created": self.created,
            "updated": self.updated,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "failed": self.failed,
            "pending_approval": self.pending_approval,
        }


class CalendarAdapter(ABC):
    """Abstract adapter for syncing with external calendars.

    Implementations should:
    1. Override connection/authentication in __init__
    2. Implement all abstract methods
    3. Map between CalendarEvent and the external format

    All write operations require approval by default.
    Read operations do not require approval.

    Circuit breaker support:
        Pass a CircuitBreaker instance to protect against cascading failures
        from unreachable external systems. Use _check_circuit() before API
        calls and _record_circuit_result() after.
    """

    _circuit_breaker: Any | None = None

    def set_circuit_breaker(self, breaker: Any) -> None:
        """Attach a circuit breaker to this adapter.

        Args:
            breaker: A CircuitBreaker instance from tools.retry.
        """
        self._circuit_breaker = breaker

    def _check_circuit(self) -> bool:
        """Check if the circuit breaker allows a request.

        Returns:
            True if the request is allowed (or no breaker is set).
        """
        if self._circuit_breaker is None:
            return True
        return self._circuit_breaker.allow_request()

    def _record_circuit_success(self) -> None:
        """Record a successful external call."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_success()

    def _record_circuit_failure(self) -> None:
        """Record a failed external call."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_failure()

    @property
    @abstractmethod
    def adapter_id(self) -> str:
        """Unique identifier (e.g., 'google', 'outlook')."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name."""

    @property
    def requires_approval_for_writes(self) -> bool:
        """Whether write operations require approval.

        Calendar writes should ALWAYS require approval (external side effects).
        """
        return True

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test that the adapter can connect to the external system."""

    @abstractmethod
    async def list_calendars(self) -> list[dict[str, str]]:
        """List available calendars.

        Returns:
            List of dicts with 'id' and 'name' keys.
        """

    @abstractmethod
    async def list_events(
        self,
        calendar_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        max_results: int = 100,
    ) -> list[CalendarEvent]:
        """List events from the external calendar.

        Args:
            calendar_id: Optional calendar filter.
            start: Start of time range.
            end: End of time range.
            max_results: Maximum events to return.

        Returns:
            List of CalendarEvent objects.
        """

    @abstractmethod
    async def get_event(
        self,
        external_id: str,
        calendar_id: str | None = None,
    ) -> CalendarEvent | None:
        """Get a single event by external ID."""

    @abstractmethod
    async def create_event(
        self,
        event: CalendarEvent,
        calendar_id: str | None = None,
    ) -> SyncResult:
        """Create an event in the external calendar.

        Args:
            event: Event to create.
            calendar_id: Target calendar (uses default if not specified).

        Returns:
            SyncResult with external_id if successful.
        """

    @abstractmethod
    async def update_event(
        self,
        event: CalendarEvent,
        calendar_id: str | None = None,
    ) -> SyncResult:
        """Update an event in the external calendar."""

    @abstractmethod
    async def delete_event(
        self,
        external_id: str,
        calendar_id: str | None = None,
    ) -> SyncResult:
        """Delete an event from the external calendar."""

    async def sync_from_external(
        self,
        calendar_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[CalendarEvent]:
        """Sync events from external calendar (PULL - no approval needed).

        Args:
            calendar_id: Calendar to sync from.
            start: Start of time range (default: now).
            end: End of time range (default: 30 days from now).

        Returns:
            List of events from external calendar.
        """
        if start is None:
            start = utc_now()
        if end is None:
            end = start + timedelta(days=30)

        return await self.list_events(
            calendar_id=calendar_id,
            start=start,
            end=end,
        )


class MemoryCalendarAdapter(CalendarAdapter):
    """In-memory calendar adapter for testing.

    Stores events in memory without any external system.
    """

    def __init__(self) -> None:
        """Initialize in-memory adapter."""
        self._events: dict[str, CalendarEvent] = {}
        self._calendars: list[dict[str, str]] = [
            {"id": "primary", "name": "Primary Calendar"},
        ]
        self._counter = 0

    @property
    def adapter_id(self) -> str:
        """Return adapter ID."""
        return "memory"

    @property
    def display_name(self) -> str:
        """Return display name."""
        return "In-Memory Calendar"

    @property
    def requires_approval_for_writes(self) -> bool:
        """Memory adapter doesn't require approval."""
        return False

    async def test_connection(self) -> bool:
        """Always returns True for memory adapter."""
        return True

    async def list_calendars(self) -> list[dict[str, str]]:
        """List in-memory calendars."""
        return self._calendars

    async def list_events(
        self,
        calendar_id: str | None = None,  # noqa: ARG002
        start: datetime | None = None,
        end: datetime | None = None,
        max_results: int = 100,
    ) -> list[CalendarEvent]:
        """List events in memory."""
        events = list(self._events.values())

        # Filter by time range
        if start:
            events = [e for e in events if e.start and e.start >= start]
        if end:
            events = [e for e in events if e.end and e.end <= end]

        # Limit results
        return events[:max_results]

    async def get_event(
        self,
        external_id: str,
        calendar_id: str | None = None,  # noqa: ARG002
    ) -> CalendarEvent | None:
        """Get an event by ID."""
        return self._events.get(external_id)

    async def create_event(
        self,
        event: CalendarEvent,
        calendar_id: str | None = None,
    ) -> SyncResult:
        """Create an event in memory."""
        self._counter += 1
        external_id = f"mem_event_{self._counter}"

        new_event = CalendarEvent(
            external_id=external_id,
            kernel_event_id=event.kernel_event_id,
            calendar_id=calendar_id or "primary",
            title=event.title,
            description=event.description,
            location=event.location,
            start=event.start,
            end=event.end,
            all_day=event.all_day,
            timezone=event.timezone,
            status=event.status,
            visibility=event.visibility,
            organizer=event.organizer,
            attendees=event.attendees,
            related_note_ids=event.related_note_ids,
            related_task_ids=event.related_task_ids,
            tags=event.tags,
            created_at=utc_now(),
        )

        self._events[external_id] = new_event

        return SyncResult(
            success=True,
            operation=SyncOperation.CREATE,
            event=new_event,
            kernel_event_id=event.kernel_event_id,
            external_id=external_id,
        )

    async def update_event(
        self,
        event: CalendarEvent,
        calendar_id: str | None = None,  # noqa: ARG002
    ) -> SyncResult:
        """Update an event in memory."""
        if event.external_id not in self._events:
            return SyncResult(
                success=False,
                operation=SyncOperation.UPDATE,
                kernel_event_id=event.kernel_event_id,
                external_id=event.external_id,
                error="Event not found",
            )

        existing = self._events[event.external_id]
        existing.title = event.title
        existing.description = event.description
        existing.location = event.location
        existing.start = event.start
        existing.end = event.end
        existing.all_day = event.all_day
        existing.status = event.status
        existing.attendees = event.attendees
        existing.updated_at = utc_now()

        return SyncResult(
            success=True,
            operation=SyncOperation.UPDATE,
            event=existing,
            kernel_event_id=event.kernel_event_id,
            external_id=event.external_id,
        )

    async def delete_event(
        self,
        external_id: str,
        calendar_id: str | None = None,  # noqa: ARG002
    ) -> SyncResult:
        """Delete an event from memory."""
        if external_id not in self._events:
            return SyncResult(
                success=False,
                operation=SyncOperation.DELETE,
                external_id=external_id,
                error="Event not found",
            )

        del self._events[external_id]

        return SyncResult(
            success=True,
            operation=SyncOperation.DELETE,
            external_id=external_id,
        )
