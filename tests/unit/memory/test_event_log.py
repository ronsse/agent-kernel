"""Tests for event log."""

from datetime import timedelta

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.memory.event_log import Event, EventType, SQLiteEventLog


class TestEventType:
    """Tests for EventType enum."""

    def test_all_event_types_exist(self):
        """Verify expected event types exist."""
        expected_types = [
            "TRACE_CREATED",
            "TOOL_CALLED",
            "TASK_CREATED",
            "APPROVAL_REQUESTED",
            "WORKFLOW_STARTED",
        ]
        for type_name in expected_types:
            assert hasattr(EventType, type_name)


class TestEvent:
    """Tests for Event schema."""

    def test_create_event(self):
        """Test creating an event."""
        event = Event(
            event_type=EventType.TASK_CREATED,
            source="task_service",
            entity_id="task_123",
            entity_type="task",
            data={"title": "New task"},
        )

        assert event.event_type == EventType.TASK_CREATED
        assert event.source == "task_service"
        assert event.entity_id == "task_123"
        assert event.event_id is not None


class TestSQLiteEventLog:
    """Tests for SQLiteEventLog."""

    def test_append_and_get(self, event_log: SQLiteEventLog):
        """Test appending and retrieving events."""
        event = Event(
            event_type=EventType.TASK_CREATED,
            source="test",
            entity_id="task_1",
            entity_type="task",
            data={"title": "Test task"},
        )

        event_log.append(event)

        events = event_log.get_events(entity_id="task_1")
        assert len(events) == 1
        assert events[0].event_id == event.event_id

    def test_get_events_by_type(self, event_log: SQLiteEventLog):
        """Test filtering events by type."""
        event_log.append(Event(
            event_type=EventType.TASK_CREATED,
            source="test",
        ))
        event_log.append(Event(
            event_type=EventType.NOTE_CREATED,
            source="test",
        ))
        event_log.append(Event(
            event_type=EventType.TASK_CREATED,
            source="test",
        ))

        task_events = event_log.get_events(event_type=EventType.TASK_CREATED)
        assert len(task_events) == 2

        note_events = event_log.get_events(event_type=EventType.NOTE_CREATED)
        assert len(note_events) == 1

    def test_count_events(self, event_log: SQLiteEventLog):
        """Test counting events."""
        for i in range(5):
            event_log.append(Event(
                event_type=EventType.TOOL_CALLED,
                source="test",
            ))

        count = event_log.count(event_type=EventType.TOOL_CALLED)
        assert count == 5

    def test_emit_convenience(self, event_log: SQLiteEventLog):
        """Test emit convenience method."""
        event = event_log.emit(
            EventType.WORKFLOW_STARTED,
            source="workflow_runner",
            entity_id="run_123",
            entity_type="workflow_run",
            data={"workflow_id": "daily_checkin"},
        )

        assert event.event_type == EventType.WORKFLOW_STARTED
        assert event.entity_id == "run_123"

        # Verify it was stored
        events = event_log.get_events(entity_id="run_123")
        assert len(events) == 1

    def test_get_events_with_time_filter(self, event_log: SQLiteEventLog):
        """Test filtering events by time."""
        now = utc_now()

        event_log.append(Event(
            event_type=EventType.TOOL_CALLED,
            source="test",
        ))

        # Get events since now (should include the one we just added)
        events = event_log.get_events(since=now - timedelta(seconds=1))
        assert len(events) >= 1

        # Get events from the future (should be empty)
        events = event_log.get_events(since=now + timedelta(hours=1))
        assert len(events) == 0
