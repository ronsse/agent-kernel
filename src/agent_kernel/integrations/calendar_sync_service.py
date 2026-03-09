"""Calendar Sync Service - Orchestrates calendar synchronization.

Provides high-level calendar sync functionality:
1. Reads calendar events from external calendars
2. Creates graph nodes for events
3. Links events to related notes and tasks
4. Creates events in external calendars (approval-gated)

Integrates with the approval workflow for external writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.integrations.calendar_sync import (
    CalendarAdapter,
    CalendarEvent,
    CalendarSyncSummary,
    SyncOperation,
    SyncResult,
)

if TYPE_CHECKING:
    from agent_kernel.memory.graph_store import GraphStore

logger = structlog.get_logger(__name__)


@dataclass
class CalendarSyncState:
    """Tracks sync state for a calendar event."""

    kernel_event_id: str
    adapter_id: str
    external_id: str | None = None
    calendar_id: str | None = None
    last_synced_at: datetime | None = None
    sync_hash: str | None = None


@dataclass
class CalendarSyncConfig:
    """Configuration for calendar sync."""

    # Which adapters to sync from
    adapters: list[str] = field(default_factory=list)

    # Time range
    days_ahead: int = 30
    days_back: int = 7

    # Filters
    calendar_ids: list[str] | None = None


class CalendarSyncService:
    """Service for syncing calendar events with the kernel.

    Reads events from external calendars and creates graph nodes.
    Creating external events requires approval.
    """

    def __init__(
        self,
        graph_store: GraphStore,
        adapters: dict[str, CalendarAdapter] | None = None,
    ) -> None:
        """Initialize calendar sync service.

        Args:
            graph_store: Graph store for event nodes.
            adapters: Dict of adapter_id -> adapter instance.
        """
        self.graph_store = graph_store
        self._adapters: dict[str, CalendarAdapter] = adapters or {}
        self._sync_state: dict[str, CalendarSyncState] = {}

        logger.info(
            "calendar_sync_service_initialized",
            adapter_count=len(self._adapters),
        )

    def register_adapter(self, adapter: CalendarAdapter) -> None:
        """Register a calendar adapter.

        Args:
            adapter: Adapter to register.
        """
        self._adapters[adapter.adapter_id] = adapter
        logger.info(
            "calendar_adapter_registered",
            adapter_id=adapter.adapter_id,
            display_name=adapter.display_name,
        )

    def get_adapter(self, adapter_id: str) -> CalendarAdapter | None:
        """Get an adapter by ID."""
        return self._adapters.get(adapter_id)

    async def import_events(
        self,
        adapter_id: str,
        config: CalendarSyncConfig | None = None,
    ) -> CalendarSyncSummary:
        """Import events from external calendar into graph (PULL).

        This is a read operation - no approval required.

        Args:
            adapter_id: ID of adapter to import from.
            config: Optional sync configuration.

        Returns:
            CalendarSyncSummary with import results.
        """
        adapter = self._adapters.get(adapter_id)
        if not adapter:
            msg = f"Adapter not found: {adapter_id}"
            raise ValueError(msg)

        config = config or CalendarSyncConfig()
        summary = CalendarSyncSummary(started_at=utc_now())

        # Calculate time range
        now = utc_now()
        start = now - timedelta(days=config.days_back)
        end = now + timedelta(days=config.days_ahead)

        # Get events from external calendar
        calendar_ids = config.calendar_ids
        if calendar_ids:
            all_events: list[CalendarEvent] = []
            for cal_id in calendar_ids:
                events = await adapter.list_events(
                    calendar_id=cal_id,
                    start=start,
                    end=end,
                )
                all_events.extend(events)
        else:
            all_events = await adapter.list_events(start=start, end=end)

        summary.total_events = len(all_events)

        # Create graph nodes for each event
        for event in all_events:
            try:
                result = await self._import_event_to_graph(event, adapter_id)
                summary.results.append(result)
                if result.success:
                    if result.operation == SyncOperation.CREATE:
                        summary.created += 1
                    elif result.operation == SyncOperation.UPDATE:
                        summary.updated += 1
                else:
                    summary.failed += 1
            except Exception:
                logger.exception(
                    "event_import_failed",
                    external_id=event.external_id,
                )
                summary.failed += 1
                summary.results.append(
                    SyncResult(
                        success=False,
                        operation=SyncOperation.CREATE,
                        external_id=event.external_id,
                        error="Import failed",
                    )
                )

        summary.completed_at = utc_now()

        logger.info(
            "calendar_import_completed",
            adapter_id=adapter_id,
            total=summary.total_events,
            created=summary.created,
            updated=summary.updated,
            failed=summary.failed,
        )

        return summary

    async def _import_event_to_graph(
        self,
        event: CalendarEvent,
        adapter_id: str,
    ) -> SyncResult:
        """Import a single event to the graph store.

        Args:
            event: Event to import.
            adapter_id: Source adapter ID.

        Returns:
            SyncResult indicating success/failure.
        """
        # Check if we've seen this event before
        sync_key = f"{adapter_id}:{event.external_id}"
        existing_state = self._sync_state.get(sync_key)

        if existing_state:
            kernel_event_id = existing_state.kernel_event_id
            operation = SyncOperation.UPDATE
        else:
            kernel_event_id = f"event_{generate_ulid()}"
            operation = SyncOperation.CREATE

        # Create/update graph node
        node_id = f"calendar_event:{kernel_event_id}"
        self.graph_store.upsert_node(
            node_id=node_id,
            node_type=NodeType.CALENDAR_EVENT.value,
            properties={
                "event_id": kernel_event_id,
                "external_id": event.external_id,
                "adapter_id": adapter_id,
                "calendar_id": event.calendar_id,
                "title": event.title,
                "description": event.description,
                "location": event.location,
                "start": event.start.isoformat() if event.start else None,
                "end": event.end.isoformat() if event.end else None,
                "all_day": event.all_day,
                "timezone": event.timezone,
                "status": event.status.value,
                "organizer": event.organizer,
                "attendees": event.attendees,
                "tags": event.tags,
                "extracted_by": "calendar_sync_service",
            },
        )

        # Create edges to related notes
        for note_id in event.related_note_ids:
            self.graph_store.upsert_edge(
                source_id=node_id,
                target_id=f"note:{note_id}",
                edge_type=EdgeType.CALENDAR_EVENT_RELATED_TO_NOTE.value,
                properties={
                    "confidence": 1.0,
                    "extracted_by": "calendar_sync_service",
                },
            )

        # Create edges to related tasks
        for task_id in event.related_task_ids:
            self.graph_store.upsert_edge(
                source_id=node_id,
                target_id=f"task:{task_id}",
                edge_type=EdgeType.CALENDAR_EVENT_RELATED_TO_TASK.value,
                properties={
                    "confidence": 1.0,
                    "extracted_by": "calendar_sync_service",
                },
            )

        # Update sync state
        self._sync_state[sync_key] = CalendarSyncState(
            kernel_event_id=kernel_event_id,
            adapter_id=adapter_id,
            external_id=event.external_id,
            calendar_id=event.calendar_id,
            last_synced_at=utc_now(),
        )

        return SyncResult(
            success=True,
            operation=operation,
            event=event,
            kernel_event_id=kernel_event_id,
            external_id=event.external_id,
        )

    async def create_external_event(
        self,
        adapter_id: str,
        event: CalendarEvent,
        calendar_id: str | None = None,
    ) -> SyncResult:
        """Create an event in an external calendar (PUSH - approval required).

        This operation requires approval as it creates external side effects.

        Args:
            adapter_id: Target adapter.
            event: Event to create.
            calendar_id: Target calendar ID.

        Returns:
            SyncResult with external_id if successful.
        """
        adapter = self._adapters.get(adapter_id)
        if not adapter:
            return SyncResult(
                success=False,
                operation=SyncOperation.CREATE,
                error=f"Adapter not found: {adapter_id}",
            )

        # Check if approval is required
        if adapter.requires_approval_for_writes:
            return SyncResult(
                success=False,
                operation=SyncOperation.CREATE,
                kernel_event_id=event.kernel_event_id,
                requires_approval=True,
                error="Approval required for external calendar writes",
            )

        # Create the event
        result = await adapter.create_event(event, calendar_id)

        if result.success and result.external_id:
            # Update sync state
            sync_key = f"{adapter_id}:{result.external_id}"
            self._sync_state[sync_key] = CalendarSyncState(
                kernel_event_id=event.kernel_event_id or f"event_{generate_ulid()}",
                adapter_id=adapter_id,
                external_id=result.external_id,
                calendar_id=calendar_id,
                last_synced_at=utc_now(),
            )

            # Create graph node for the new event
            await self._import_event_to_graph(
                CalendarEvent(
                    external_id=result.external_id,
                    kernel_event_id=event.kernel_event_id,
                    calendar_id=calendar_id,
                    title=event.title,
                    description=event.description,
                    location=event.location,
                    start=event.start,
                    end=event.end,
                    all_day=event.all_day,
                    status=event.status,
                    attendees=event.attendees,
                ),
                adapter_id,
            )

        return result

    def get_sync_state(self, kernel_event_id: str) -> CalendarSyncState | None:
        """Get sync state for an event by kernel ID."""
        for state in self._sync_state.values():
            if state.kernel_event_id == kernel_event_id:
                return state
        return None


def create_calendar_event_action(
    event: CalendarEvent,
    adapter_id: str,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    """Create an action request for calendar event creation (for approval workflow).

    This creates a structured action that can be included in a Plan
    and executed via the Tool Broker with approval gates.

    Args:
        event: Event to create.
        adapter_id: Target adapter.
        calendar_id: Target calendar.

    Returns:
        Dict suitable for ActionRequest.
    """
    return {
        "capability_name": "calendar.create@v1",
        "args": {
            "adapter_id": adapter_id,
            "calendar_id": calendar_id,
            "event": event.to_dict(),
        },
    }
