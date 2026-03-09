"""Tests for experience MCP tools."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.experience import (
    ExperienceCase,
    LessonLearned,
    LessonScope,
    OutcomeLabel,
    Playbook,
    PlaybookSelector,
)
from agent_kernel.mcp_server.server import StoreBundle
from agent_kernel.mcp_server.tools.experience import register_experience_tools


class FakeMCP:
    def __init__(self):
        self._tools = {}

    def tool(self, name=None, description=None, **kwargs):
        def decorator(fn):
            self._tools[name] = fn
            return fn
        return decorator

    def get_tool(self, name):
        return self._tools[name]


def _make_case(case_id="case_001"):
    now = utc_now()
    return ExperienceCase(
        case_id=case_id,
        trace_id="trace_001",
        intent="Process daily tasks",
        workflow_id="daily_checkin",
        capability_names=["tasks.list@v1"],
        sources_used=["obsidian"],
        entity_types_used=["note"],
        label=OutcomeLabel.SUCCESS,
        context_summary="Reviewed tasks",
        plan_summary="Listed tasks",
        outcome_summary="3 tasks prioritized",
        created_at=now,
        updated_at=now,
    )


def _make_lesson(lesson_id="lesson_001"):
    now = utc_now()
    return LessonLearned(
        lesson_id=lesson_id,
        title="Check due dates first",
        lesson_text="Always prioritize tasks with upcoming due dates.",
        scope=LessonScope(workflow_id="daily_checkin"),
        source_case_ids=["case_001"],
        confidence=0.9,
        status="active",
        created_at=now,
        updated_at=now,
    )


def _make_playbook(playbook_id="playbook_001"):
    now = utc_now()
    return Playbook(
        playbook_id=playbook_id,
        name="Daily Review Playbook",
        selectors=[PlaybookSelector(workflow_id="daily_checkin")],
        required_entity_types=["note"],
        required_sources=["obsidian"],
        checklist=["Check open tasks", "Review calendar"],
        pitfalls=["Don't skip overdue tasks"],
        recommended_thinking_tier=1,
        status="active",
        created_at=now,
        updated_at=now,
    )


def _make_stores(experience_store=None):
    stores = MagicMock(spec=StoreBundle)
    stores.experience_store = experience_store
    return stores


class TestExperienceCases:
    def test_returns_cases(self):
        exp = MagicMock()
        exp.find_similar_cases.return_value = [_make_case()]
        stores = _make_stores(experience_store=exp)

        mcp = FakeMCP()
        register_experience_tools(mcp, stores)

        result = mcp.get_tool("experience_cases")()

        assert len(result["cases"]) == 1
        assert result["cases"][0]["case_id"] == "case_001"
        assert result["cases"][0]["label"] == "success"

    def test_filters_by_label(self):
        exp = MagicMock()
        exp.find_similar_cases.return_value = []
        stores = _make_stores(experience_store=exp)

        mcp = FakeMCP()
        register_experience_tools(mcp, stores)

        mcp.get_tool("experience_cases")(label="failure")

        call_kwargs = exp.find_similar_cases.call_args[1]
        assert call_kwargs["label"] == OutcomeLabel.FAILURE

    def test_invalid_label_returns_error(self):
        exp = MagicMock()
        stores = _make_stores(experience_store=exp)

        mcp = FakeMCP()
        register_experience_tools(mcp, stores)

        result = mcp.get_tool("experience_cases")(label="invalid")

        assert "error" in result

    def test_no_experience_store(self):
        stores = _make_stores(experience_store=None)

        mcp = FakeMCP()
        register_experience_tools(mcp, stores)

        result = mcp.get_tool("experience_cases")()

        assert result["cases"] == []


class TestExperienceLessons:
    def test_returns_lessons(self):
        exp = MagicMock()
        exp.list_lessons.return_value = [_make_lesson()]
        stores = _make_stores(experience_store=exp)

        mcp = FakeMCP()
        register_experience_tools(mcp, stores)

        result = mcp.get_tool("experience_lessons")()

        assert len(result["lessons"]) == 1
        assert result["lessons"][0]["lesson_id"] == "lesson_001"
        assert result["lessons"][0]["confidence"] == 0.9

    def test_filters_by_workflow(self):
        exp = MagicMock()
        exp.list_lessons.return_value = []
        stores = _make_stores(experience_store=exp)

        mcp = FakeMCP()
        register_experience_tools(mcp, stores)

        mcp.get_tool("experience_lessons")(workflow_id="daily_checkin")

        call_kwargs = exp.list_lessons.call_args[1]
        assert call_kwargs["scope"].workflow_id == "daily_checkin"


class TestExperiencePlaybooks:
    def test_returns_playbooks(self):
        exp = MagicMock()
        exp.list_playbooks.return_value = [_make_playbook()]
        stores = _make_stores(experience_store=exp)

        mcp = FakeMCP()
        register_experience_tools(mcp, stores)

        result = mcp.get_tool("experience_playbooks")()

        assert len(result["playbooks"]) == 1
        assert result["playbooks"][0]["name"] == "Daily Review Playbook"
        assert result["playbooks"][0]["recommended_thinking_tier"] == 1

    def test_filters_by_workflow(self):
        exp = MagicMock()
        exp.find_playbooks.return_value = [_make_playbook()]
        stores = _make_stores(experience_store=exp)

        mcp = FakeMCP()
        register_experience_tools(mcp, stores)

        result = mcp.get_tool("experience_playbooks")(workflow_id="daily_checkin")

        exp.find_playbooks.assert_called_once_with(workflow_id="daily_checkin")
        assert len(result["playbooks"]) == 1
