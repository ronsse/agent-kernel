from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent_kernel.workflows.runner import (
    CalendarDerivationState,
    CalendarEventRecord,
    CalendarSourceConfig,
    CalendarSourceFilters,
    DerivationCaps,
    MeetingNoteDerivationConfig,
    WorkflowRunner,
)


def test_event_filter_requires_zoom_link() -> None:
    filters = CalendarSourceFilters(require_zoom_link=True)
    event = CalendarEventRecord(
        source_id="src",
        provider="google",
        calendar_id="cal",
        event_id="evt",
        title="Example",
        description=None,
        start=datetime.now(timezone.utc),
        end=None,
        all_day=False,
        status="confirmed",
        updated_at=None,
        etag=None,
        location=None,
        attendees=[],
        conference_link=None,
        raw={},
    )
    runner = WorkflowRunner.__new__(WorkflowRunner)
    assert runner._event_passes_filters(event, filters) is False

def test_extract_zoom_link_from_description() -> None:
    runner = WorkflowRunner.__new__(WorkflowRunner)
    text = "Join: https://fanduel.zoom.us/j/123456789"
    assert (
        runner._extract_zoom_link(text)
        == "https://fanduel.zoom.us/j/123456789"
    )


def test_extract_zoom_link_from_location() -> None:
    runner = WorkflowRunner.__new__(WorkflowRunner)
    text = "Zoom https://zoom.us/my/teamroom"
    assert runner._extract_zoom_link(text) == "https://zoom.us/my/teamroom"

def test_is_one_on_one_title_colon() -> None:
    runner = WorkflowRunner.__new__(WorkflowRunner)
    assert runner._is_one_on_one_title("Alex : Jordan") is True


def test_is_one_on_one_title_angle() -> None:
    runner = WorkflowRunner.__new__(WorkflowRunner)
    assert runner._is_one_on_one_title("Alex <> Jordan") is True


def test_is_one_on_one_title_1on1() -> None:
    runner = WorkflowRunner.__new__(WorkflowRunner)
    assert runner._is_one_on_one_title("Diwakar / Nate 1:1") is True

def test_build_daily_meetings_block() -> None:
    runner = WorkflowRunner.__new__(WorkflowRunner)
    event = CalendarEventRecord(
        source_id="src",
        provider="google",
        calendar_id="cal",
        event_id="evt",
        title="Alex : Jordan",
        description=None,
        start=datetime(2026, 2, 2, 18, 0, tzinfo=timezone.utc),
        end=None,
        all_day=False,
        status="confirmed",
        updated_at=None,
        etag=None,
        location=None,
        attendees=[],
        conference_link=None,
        raw={},
        zoom_link="https://zoom.us/j/1",
    )
    content = runner._build_daily_meetings_block(
        one_on_ones=[event],
        group_links=[],
    )
    assert "## Meetings" in content
    assert "Alex : Jordan" in content

@pytest.mark.asyncio
async def test_one_on_one_skips_note_creation() -> None:
    class DummySuppressionRegistry:
        def get_suppression(self, **_kwargs):
            return None

        def put_suppression(self, *_args, **_kwargs):
            return None

    class DummyMappingStore:
        def get_mapping(self, **_kwargs):
            return None

        def delete_mapping(self, *_args, **_kwargs):
            return None

        def put_mapping(self, *_args, **_kwargs):
            return None

    class DummyExecutor:
        def __init__(self, note):
            self._note = note

        async def execute_actions(self, actions, _agent_profile):
            outputs = []
            for action in actions:
                if action.capability_name == "obsidian.daily@v1":
                    outputs.append(SimpleNamespace(output={"note": self._note}))
                else:
                    outputs.append(SimpleNamespace(output={}))
            return outputs

    now = datetime.now(timezone.utc)
    event = CalendarEventRecord(
        source_id="src",
        provider="google",
        calendar_id="cal",
        event_id="evt",
        title="Alex : Jordan",
        description=None,
        start=now,
        end=None,
        all_day=False,
        status="confirmed",
        updated_at=None,
        etag=None,
        location=None,
        attendees=[],
        conference_link=None,
        raw={},
        zoom_link="https://zoom.us/j/1",
    )

    state = CalendarDerivationState()
    state.sources["src"] = CalendarSourceConfig(
        source_id="src",
        provider="google",
        calendar_id="cal",
        purpose="work_meetings",
        import_window_days=7,
        filters=CalendarSourceFilters(),
        meeting_note_derivations=[
            MeetingNoteDerivationConfig(
                vault_path="/vault",
                meeting_folder="Meetings",
                template="templates/obsidian/meeting.md",
                caps=DerivationCaps(),
                suppression_ttl_hours=24,
                project_folder_map={},
            )
        ],
    )
    state.events_by_source["src"] = [event]

    daily_note = {
        "path": "Daily/2026-02-02.md",
        "title": "2026-02-02",
        "content": "# Daily\n\n## Notes\n",
        "frontmatter": {},
    }

    runner = WorkflowRunner.__new__(WorkflowRunner)
    runner._executor = DummyExecutor(daily_note)
    runner._get_derivation_stores = (
        lambda: (DummyMappingStore(), DummySuppressionRegistry())
    )

    plan = await runner._derive_meeting_notes(
        state=state,
        agent_profile=SimpleNamespace(),
        refresh_only=False,
    )

    capability_names = [action.capability_name for action in plan.actions]
    assert "obsidian.update@v1" in capability_names
    assert "obsidian.create@v1" not in capability_names

    update_action = next(
        action
        for action in plan.actions
        if action.capability_name == "obsidian.update@v1"
    )
    assert update_action.args["path"] == "Daily/2026-02-02.md"
    assert "Alex : Jordan" in update_action.args["content"]

def test_tentative_event_not_accepted() -> None:
    runner = WorkflowRunner.__new__(WorkflowRunner)
    event = CalendarEventRecord(
        source_id="src",
        provider="google",
        calendar_id="cal",
        event_id="evt",
        title="Tentative Meeting",
        description=None,
        start=datetime.now(timezone.utc),
        end=None,
        all_day=False,
        status="tentative",
        updated_at=None,
        etag=None,
        location=None,
        attendees=[],
        conference_link=None,
        raw={"attendees": [{"self": True, "responseStatus": "accepted"}]},
        zoom_link=None,
    )
    assert runner._is_meeting_accepted(event) is False


def test_zoom_link_requires_description_or_location() -> None:
    runner = WorkflowRunner.__new__(WorkflowRunner)
    source = CalendarSourceConfig(
        source_id="src",
        provider="google",
        calendar_id="cal",
        purpose="work_meetings",
        import_window_days=7,
        filters=CalendarSourceFilters(),
    )
    raw = {
        "id": "evt",
        "summary": "Zoom Meeting",
        "start": {"dateTime": "2026-02-02T10:00:00Z"},
        "hangoutLink": "https://zoom.us/j/123456789",
    }
    record = runner._normalize_calendar_event(raw, source)
    assert record is not None
    assert record.zoom_link is None

