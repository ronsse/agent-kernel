"""Tests for experience retrieval in ContextAssembler.

Verifies that experience cases and lessons are included in context
assembly when an ExperienceStore is available.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_kernel.context.assembler import ContextAssembler
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.context import RefType
from agent_kernel.core.schemas.experience import (
    ExperienceCase,
    LessonLearned,
    LessonScope,
    OutcomeLabel,
)


def _make_case(
    case_id: str = "case_001",
    intent: str = "Process daily tasks",
    workflow_id: str | None = "daily_checkin",
    label: OutcomeLabel = OutcomeLabel.SUCCESS,
) -> ExperienceCase:
    now = utc_now()
    return ExperienceCase(
        case_id=case_id,
        trace_id="trace_001",
        intent=intent,
        workflow_id=workflow_id,
        capability_names=["tasks.list@v1"],
        sources_used=["obsidian"],
        entity_types_used=["note"],
        label=label,
        context_summary="Reviewed 5 tasks",
        plan_summary="Listed open tasks",
        outcome_summary="3 tasks prioritized",
        created_at=now,
        updated_at=now,
    )


def _make_lesson(
    lesson_id: str = "lesson_001",
    title: str = "Always check due dates",
    lesson_text: str = "Tasks with due dates should be prioritized first.",
    workflow_id: str | None = "daily_checkin",
) -> LessonLearned:
    now = utc_now()
    return LessonLearned(
        lesson_id=lesson_id,
        title=title,
        lesson_text=lesson_text,
        scope=LessonScope(workflow_id=workflow_id),
        source_case_ids=["case_001"],
        confidence=0.9,
        status="active",
        created_at=now,
        updated_at=now,
    )


class TestExperienceRetrieval:
    """Tests for experience retrieval in context assembly."""

    def test_search_experience_returns_cases(self):
        """Cases are returned as ContextItems with RefType.CASE."""
        experience_store = MagicMock()
        experience_store.find_similar_cases.return_value = [_make_case()]
        experience_store.list_lessons.return_value = []

        assembler = ContextAssembler(experience_store=experience_store)

        items, query = assembler._search_experience(
            intent="Process daily tasks",
            workflow_id="daily_checkin",
        )

        assert len(items) == 1
        assert items[0].ref.ref_type == RefType.CASE
        assert items[0].ref.ref_id == "case_001"
        assert items[0].included_reason == "experience_case"
        assert "Process daily tasks" in items[0].excerpt
        assert query.source == "experience"
        assert query.results_count == 1

    def test_search_experience_returns_lessons(self):
        """Lessons are returned as ContextItems with RefType.LESSON."""
        experience_store = MagicMock()
        experience_store.find_similar_cases.return_value = []
        experience_store.list_lessons.return_value = [_make_lesson()]

        assembler = ContextAssembler(experience_store=experience_store)

        items, _query = assembler._search_experience(
            intent="Process daily tasks",
            workflow_id="daily_checkin",
        )

        assert len(items) == 1
        assert items[0].ref.ref_type == RefType.LESSON
        assert items[0].ref.ref_id == "lesson_001"
        assert items[0].included_reason == "experience_lesson"
        assert "Always check due dates" in items[0].excerpt

    def test_search_experience_combined_budget(self):
        """Cases and lessons together respect the limit."""
        all_cases = [_make_case(case_id=f"case_{i}") for i in range(3)]
        all_lessons = [_make_lesson(lesson_id=f"lesson_{i}") for i in range(3)]

        experience_store = MagicMock()
        # Mock respects the limit kwarg (as a real store would)
        experience_store.find_similar_cases.side_effect = (
            lambda **kwargs: all_cases[: kwargs.get("limit", 3)]
        )
        experience_store.list_lessons.side_effect = (
            lambda **kwargs: all_lessons[: kwargs.get("limit", 3)]
        )

        assembler = ContextAssembler(experience_store=experience_store)

        items, _query = assembler._search_experience(
            intent="Process daily tasks",
            workflow_id="daily_checkin",
            limit=5,
        )

        # limit=5: 2 cases (limit//2) + 3 lessons (limit - 2)
        assert len(items) == 5
        case_items = [i for i in items if i.ref.ref_type == RefType.CASE]
        lesson_items = [i for i in items if i.ref.ref_type == RefType.LESSON]
        assert len(case_items) == 2
        assert len(lesson_items) == 3

    def test_search_experience_no_store(self):
        """Returns empty when no experience store is configured."""
        assembler = ContextAssembler()

        items, query = assembler._search_experience(
            intent="anything",
        )

        assert items == []
        assert query.results_count == 0

    def test_search_experience_handles_errors(self):
        """Gracefully handles errors from experience store."""
        experience_store = MagicMock()
        experience_store.find_similar_cases.side_effect = RuntimeError("DB error")
        experience_store.list_lessons.side_effect = RuntimeError("DB error")

        assembler = ContextAssembler(experience_store=experience_store)

        items, query = assembler._search_experience(
            intent="Process daily tasks",
        )

        assert items == []
        assert query.results_count == 0

    def test_case_excerpt_formatting(self):
        """Case excerpts include intent, outcome, and capabilities."""
        case = _make_case()
        excerpt = ContextAssembler._format_case_excerpt(case)

        assert "Process daily tasks" in excerpt
        assert "3 tasks prioritized" in excerpt
        assert "success" in excerpt
        assert "tasks.list@v1" in excerpt

    def test_experience_items_scored_below_knowledge(self):
        """Experience items have lower relevance scores than knowledge."""
        experience_store = MagicMock()
        experience_store.find_similar_cases.return_value = [_make_case()]
        experience_store.list_lessons.return_value = [_make_lesson()]

        assembler = ContextAssembler(experience_store=experience_store)

        items, _ = assembler._search_experience(
            intent="anything",
        )

        for item in items:
            assert item.relevance_score <= 0.5  # Below knowledge (0.8) and traj (0.7)

    def test_workflow_id_passed_to_cases(self):
        """workflow_id is forwarded to find_similar_cases."""
        experience_store = MagicMock()
        experience_store.find_similar_cases.return_value = []
        experience_store.list_lessons.return_value = []

        assembler = ContextAssembler(experience_store=experience_store)

        assembler._search_experience(
            intent="anything",
            workflow_id="daily_checkin",
        )

        experience_store.find_similar_cases.assert_called_once_with(
            workflow_id="daily_checkin",
            limit=2,
        )
