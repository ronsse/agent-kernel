"""Tests for task sync markers."""

from __future__ import annotations

from agent_kernel.services.task_parser import ObsidianTaskParser


def test_sync_markers_detected_and_cleaned() -> None:
    content = "- [ ] Follow up #todo\n- [ ] Review @task"
    parser = ObsidianTaskParser()

    result = parser.parse_note(content, note_id="note_1", note_path="Meetings.md")

    assert len(result.tasks) == 2
    assert result.tasks[0].should_sync is True
    assert result.tasks[0].sync_marker == "todo"
    assert result.tasks[0].content == "Follow up"
    assert result.tasks[1].should_sync is True
    assert result.tasks[1].sync_marker == "task"
    assert result.tasks[1].content == "Review"


def test_no_sync_marker_defaults_to_local_only() -> None:
    content = "- [ ] Capture note"
    parser = ObsidianTaskParser()

    result = parser.parse_note(content, note_id="note_2", note_path="Notes.md")

    assert len(result.tasks) == 1
    assert result.tasks[0].should_sync is False
    assert result.tasks[0].sync_marker is None
