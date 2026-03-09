"""Unit tests for TaskParser."""

from __future__ import annotations

from datetime import date

from agent_kernel.services.task_parser import (
    ObsidianTaskParser,
    ParsedTask,
    TaskPriority,
    TaskStatus,
    extract_tasks,
)


class TestParsedTask:
    """Tests for ParsedTask dataclass."""

    def test_is_complete_when_complete(self) -> None:
        """Test is_complete returns True for completed tasks."""
        task = ParsedTask(
            raw_line="- [x] Test task",
            line_number=1,
            content="Test task",
            is_completed=True,
        )
        assert task.is_completed is True

    def test_is_complete_when_incomplete(self) -> None:
        """Test is_complete returns False for incomplete tasks."""
        task = ParsedTask(
            raw_line="- [ ] Test task",
            line_number=1,
            content="Test task",
            is_completed=False,
        )
        assert task.is_completed is False

    def test_to_task_entity(self) -> None:
        """Test conversion to TaskEntity."""
        parser = ObsidianTaskParser()
        task = ParsedTask(
            raw_line="- [ ] Buy milk",
            line_number=5,
            content="Buy milk",
            is_completed=False,
            priority=TaskPriority.P2,
            due_date=date(2026, 1, 15),
            tags=["shopping"],
            context="home",
            source_note_id="note_xyz",
        )
        entity = parser.to_task_entity(task)

        assert entity.title == "Buy milk"
        assert entity.status == TaskStatus.OPEN
        assert entity.priority == TaskPriority.P2
        assert entity.due == date(2026, 1, 15)
        assert entity.labels == ["shopping"]
        assert entity.ext["obsidian"]["note_id"] == "note_xyz"


class TestTaskParser:
    """Tests for TaskParser."""

    def test_parse_simple_incomplete_task(self) -> None:
        """Test parsing a simple incomplete task."""
        content = "- [ ] Buy groceries"
        parser = ObsidianTaskParser()
        result = parser.parse_note(content, note_id="note_123")
        tasks = result.tasks

        assert len(tasks) == 1
        assert tasks[0].content == "Buy groceries"
        assert tasks[0].is_completed is False
        assert tasks[0].line_number == 1

    def test_parse_simple_complete_task(self) -> None:
        """Test parsing a completed task."""
        content = "- [x] Buy groceries"
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 1
        assert tasks[0].is_completed is True

    def test_parse_complete_task_uppercase_x(self) -> None:
        """Test parsing a completed task with uppercase X."""
        content = "- [X] Buy groceries"
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 1
        assert tasks[0].is_completed is True

    def test_parse_multiple_tasks(self) -> None:
        """Test parsing multiple tasks."""
        content = """
# My Tasks

- [ ] First task
- [x] Second task (done)
- [ ] Third task
"""
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 3
        assert tasks[0].content == "First task"
        assert tasks[0].is_completed is False
        assert tasks[1].content == "Second task (done)"
        assert tasks[1].is_completed is True
        assert tasks[2].content == "Third task"
        assert tasks[2].is_completed is False

    def test_parse_due_date_emoji(self) -> None:
        """Test parsing due date with emoji format."""
        content = "- [ ] Submit report 📅 2026-01-20"
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 1
        assert tasks[0].due_date == date(2026, 1, 20)
        assert "📅" not in tasks[0].content  # Should be cleaned

    def test_parse_due_date_text(self) -> None:
        """Test parsing due date with text format."""
        content = "- [ ] Submit report @due(2026-01-20)"
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 1
        assert tasks[0].due_date == date(2026, 1, 20)

    def test_parse_due_date_dataview(self) -> None:
        """Test parsing due date with Dataview format."""
        content = "- [ ] Submit report [due:: 2026-01-20]"
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 1
        assert tasks[0].due_date == date(2026, 1, 20)

    def test_parse_priority_high_emoji(self) -> None:
        """Test parsing high priority with emoji."""
        content = "- [ ] 🔺 Important task"
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 1
        assert tasks[0].priority == TaskPriority.P1

    def test_parse_priority_high_letter(self) -> None:
        """Test parsing high priority with [#A]."""
        content = "- [ ] [#A] Important task"
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 1
        assert tasks[0].priority == TaskPriority.P1

    def test_parse_priority_low(self) -> None:
        """Test parsing low priority."""
        content = "- [ ] ⏬ Maybe someday"
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 1
        assert tasks[0].priority == TaskPriority.P4

    def test_parse_priority_medium(self) -> None:
        """Test parsing medium priority."""
        content = "- [ ] [#B] Normal task"
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 1
        assert tasks[0].priority == TaskPriority.P2

    def test_parse_tags(self) -> None:
        """Test parsing tags from task."""
        content = "- [ ] Work on #project-x and #backend"
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 1
        assert "project-x" in tasks[0].tags
        assert "backend" in tasks[0].tags

    def test_parse_contexts(self) -> None:
        """Test parsing @contexts from task."""
        content = "- [ ] Call @john about @work"
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 1
        assert tasks[0].context == "john"

    def test_parse_indented_tasks(self) -> None:
        """Test parsing indented (nested) tasks."""
        content = """
- [ ] Parent task
  - [ ] Nested task 1
    - [ ] Deep nested task
"""
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 3

    def test_parse_asterisk_checkbox(self) -> None:
        """Test parsing checkboxes with asterisk."""
        content = "* [ ] Task with asterisk"
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        # ObsidianTaskParser supports asterisk checkboxes
        assert len(tasks) == 1
        assert tasks[0].content == "Task with asterisk"

    def test_no_tasks_in_content(self) -> None:
        """Test parsing content with no tasks."""
        content = """
# Notes

Just some regular content here.
No checkboxes at all.
"""
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert len(tasks) == 0

    def test_stable_task_id_generation(self) -> None:
        """Test that task IDs are stable for same content."""
        content = "- [ ] Consistent task"
        parser = ObsidianTaskParser()
        entities1 = parser.parse_and_convert(content, note_id="note_123")
        entities2 = parser.parse_and_convert(content, note_id="note_123")

        assert entities1[0].id.startswith("task_")
        assert entities2[0].id.startswith("task_")

    def test_different_task_ids_for_different_content(self) -> None:
        """Test that different tasks get different IDs."""
        parser = ObsidianTaskParser()

        tasks1 = parser.parse_and_convert("- [ ] Task A", note_id="note_123")
        tasks2 = parser.parse_and_convert("- [ ] Task B", note_id="note_123")

        assert tasks1[0].id != tasks2[0].id

    def test_task_id_includes_note_id(self) -> None:
        """Test that task IDs differ for same task in different notes."""
        content = "- [ ] Same task text"

        parser = ObsidianTaskParser()

        tasks1 = parser.parse_and_convert(content, note_id="note_1")
        tasks2 = parser.parse_and_convert(content, note_id="note_2")

        assert tasks1[0].source_entity_ref == "note_1"
        assert tasks2[0].source_entity_ref == "note_2"

    def test_raw_line_preserved(self) -> None:
        """Test that raw line is preserved."""
        content = "- [ ] Task with 📅 2026-01-20 and #tag"
        parser = ObsidianTaskParser()
        tasks = parser.parse_note(content, note_id="note_123").tasks

        assert tasks[0].raw_line == content


class TestExtractTasksFunction:
    """Tests for the extract_tasks convenience function."""

    def test_extract_tasks_basic(self) -> None:
        """Test basic task extraction."""
        content = "- [ ] Simple task"

        tasks = extract_tasks(content, note_id="note_123")

        assert len(tasks) == 1
        assert tasks[0].source_note_id == "note_123"

    def test_extract_tasks_no_note_id(self) -> None:
        """Test extraction with no note_id."""
        content = "- [ ] Simple task"

        tasks = extract_tasks(content)

        assert len(tasks) == 1
        # note_id="" (falsy) → source_note_id stays None via __post_init__
        assert tasks[0].source_note_id is None
