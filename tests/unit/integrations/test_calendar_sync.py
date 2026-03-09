"""Tests for Calendar Sync Adapter and Service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_kernel.integrations.calendar_sync import (
    CalendarEvent,
    EventStatus,
    EventVisibility,
    MemoryCalendarAdapter,
    SyncOperation,
)


class TestCalendarEvent:
    """Tests for CalendarEvent dataclass."""

    def test_default_values(self) -> None:
        """Test default initialization."""
        event = CalendarEvent(external_id="ext_123")
        assert event.external_id == "ext_123"
        assert event.kernel_event_id is None
        assert event.title == ""
        assert event.status == EventStatus.CONFIRMED
        assert event.visibility == EventVisibility.PUBLIC
        assert event.all_day is False
        assert event.attendees == []

    def test_with_all_fields(self) -> None:
        """Test event with all fields populated."""
        start = datetime(2026, 1, 20, 10, 0, tzinfo=UTC)
        end = datetime(2026, 1, 20, 11, 0, tzinfo=UTC)

        event = CalendarEvent(
            external_id="ext_456",
            kernel_event_id="event_abc123",
            title="Team Meeting",
            description="Weekly sync",
            location="Conference Room A",
            start=start,
            end=end,
            status=EventStatus.CONFIRMED,
            attendees=["alice@example.com", "bob@example.com"],
            related_note_ids=["note_xyz"],
        )
        assert event.external_id == "ext_456"
        assert event.title == "Team Meeting"
        assert event.duration == timedelta(hours=1)

    def test_duration_property(self) -> None:
        """Test duration calculation."""
        start = datetime(2026, 1, 20, 9, 0, tzinfo=UTC)
        end = datetime(2026, 1, 20, 10, 30, tzinfo=UTC)

        event = CalendarEvent(
            external_id="ext_789",
            start=start,
            end=end,
        )
        expected_duration = timedelta(hours=1, minutes=30)
        assert event.duration == expected_duration

    def test_duration_none_when_missing_times(self) -> None:
        """Test duration is None when times are missing."""
        event = CalendarEvent(external_id="ext_abc")
        assert event.duration is None

    def test_to_dict(self) -> None:
        """Test conversion to dictionary."""
        start = datetime(2026, 1, 20, 10, 0, tzinfo=UTC)
        event = CalendarEvent(
            external_id="ext_xyz",
            title="Test Event",
            start=start,
            tags=["work"],
        )
        d = event.to_dict()
        assert d["external_id"] == "ext_xyz"
        assert d["title"] == "Test Event"
        assert d["tags"] == ["work"]
        assert d["status"] == "confirmed"


class TestMemoryCalendarAdapter:
    """Tests for the in-memory calendar adapter."""

    @pytest.fixture
    def adapter(self) -> MemoryCalendarAdapter:
        """Create a memory adapter."""
        return MemoryCalendarAdapter()

    @pytest.mark.asyncio
    async def test_properties(self, adapter: MemoryCalendarAdapter) -> None:
        """Test adapter properties."""
        assert adapter.adapter_id == "memory"
        assert adapter.display_name == "In-Memory Calendar"
        assert adapter.requires_approval_for_writes is False

    @pytest.mark.asyncio
    async def test_connection(self, adapter: MemoryCalendarAdapter) -> None:
        """Test connection always succeeds."""
        assert await adapter.test_connection() is True

    @pytest.mark.asyncio
    async def test_list_calendars(self, adapter: MemoryCalendarAdapter) -> None:
        """Test listing calendars."""
        calendars = await adapter.list_calendars()
        assert len(calendars) == 1
        assert calendars[0]["id"] == "primary"

    @pytest.mark.asyncio
    async def test_create_event(self, adapter: MemoryCalendarAdapter) -> None:
        """Test creating an event."""
        event = CalendarEvent(
            external_id="",
            kernel_event_id="event_123",
            title="New Meeting",
            start=datetime(2026, 1, 20, 10, 0, tzinfo=UTC),
            end=datetime(2026, 1, 20, 11, 0, tzinfo=UTC),
        )
        result = await adapter.create_event(event)

        assert result.success is True
        assert result.operation == SyncOperation.CREATE
        assert result.external_id is not None
        assert result.external_id.startswith("mem_event_")
        assert result.kernel_event_id == "event_123"

    @pytest.mark.asyncio
    async def test_get_event(self, adapter: MemoryCalendarAdapter) -> None:
        """Test getting an event by ID."""
        event = CalendarEvent(
            external_id="",
            title="Get me",
            start=datetime(2026, 1, 20, 10, 0, tzinfo=UTC),
        )
        result = await adapter.create_event(event)
        external_id = result.external_id

        retrieved = await adapter.get_event(external_id)
        assert retrieved is not None
        assert retrieved.external_id == external_id
        assert retrieved.title == "Get me"

    @pytest.mark.asyncio
    async def test_get_nonexistent_event(
        self, adapter: MemoryCalendarAdapter
    ) -> None:
        """Test getting a nonexistent event."""
        event = await adapter.get_event("nonexistent")
        assert event is None

    @pytest.mark.asyncio
    async def test_update_event(self, adapter: MemoryCalendarAdapter) -> None:
        """Test updating an event."""
        event = CalendarEvent(
            external_id="",
            title="Original Title",
        )
        result = await adapter.create_event(event)
        external_id = result.external_id

        updated_event = CalendarEvent(
            external_id=external_id,
            title="Updated Title",
            location="New Location",
        )
        update_result = await adapter.update_event(updated_event)

        assert update_result.success is True
        assert update_result.operation == SyncOperation.UPDATE

        retrieved = await adapter.get_event(external_id)
        assert retrieved is not None
        assert retrieved.title == "Updated Title"
        assert retrieved.location == "New Location"

    @pytest.mark.asyncio
    async def test_update_nonexistent_event(
        self, adapter: MemoryCalendarAdapter
    ) -> None:
        """Test updating a nonexistent event."""
        event = CalendarEvent(external_id="nonexistent", title="Won't work")
        result = await adapter.update_event(event)

        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_delete_event(self, adapter: MemoryCalendarAdapter) -> None:
        """Test deleting an event."""
        event = CalendarEvent(external_id="", title="Delete me")
        result = await adapter.create_event(event)
        external_id = result.external_id

        delete_result = await adapter.delete_event(external_id)

        assert delete_result.success is True
        assert delete_result.operation == SyncOperation.DELETE

        retrieved = await adapter.get_event(external_id)
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_list_events(self, adapter: MemoryCalendarAdapter) -> None:
        """Test listing all events."""
        events_to_create = ["Event 1", "Event 2", "Event 3"]
        for title in events_to_create:
            await adapter.create_event(
                CalendarEvent(external_id="", title=title)
            )

        events = await adapter.list_events()
        assert len(events) == len(events_to_create)

    @pytest.mark.asyncio
    async def test_list_events_with_time_filter(
        self, adapter: MemoryCalendarAdapter
    ) -> None:
        """Test listing events with time range filter."""
        base = datetime(2026, 1, 20, tzinfo=UTC)

        # Create events at different times
        await adapter.create_event(
            CalendarEvent(
                external_id="",
                title="Past Event",
                start=base - timedelta(days=10),
                end=base - timedelta(days=10, hours=-1),
            )
        )
        await adapter.create_event(
            CalendarEvent(
                external_id="",
                title="Current Event",
                start=base,
                end=base + timedelta(hours=1),
            )
        )
        await adapter.create_event(
            CalendarEvent(
                external_id="",
                title="Future Event",
                start=base + timedelta(days=10),
                end=base + timedelta(days=10, hours=1),
            )
        )

        # Filter for "current" time range
        events = await adapter.list_events(
            start=base - timedelta(days=1),
            end=base + timedelta(days=1),
        )

        assert len(events) == 1
        assert events[0].title == "Current Event"

    @pytest.mark.asyncio
    async def test_sync_from_external(
        self, adapter: MemoryCalendarAdapter
    ) -> None:
        """Test sync_from_external helper."""
        # Create event in the future (within 30-day sync range)
        future_start = datetime.now(UTC) + timedelta(days=1)
        await adapter.create_event(
            CalendarEvent(
                external_id="",
                title="Event",
                start=future_start,
                end=future_start + timedelta(hours=1),
            )
        )

        events = await adapter.sync_from_external()
        assert len(events) == 1
