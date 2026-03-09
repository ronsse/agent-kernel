"""Tests for ExperienceMiner service."""

from __future__ import annotations

import pytest

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.context import ContextRef, RefType
from agent_kernel.core.schemas.experience import OutcomeLabel
from agent_kernel.core.schemas.plan import Plan
from agent_kernel.core.schemas.trace import (
    CallStatus,
    DecisionTrace,
    Outcome,
    OutcomeStatus,
    ToolCallRecord,
)
from agent_kernel.memory.event_log import SQLiteEventLog
from agent_kernel.memory.experience_store import SQLiteExperienceStore
from agent_kernel.services.experience_miner import ExperienceMiner, outcome_to_label


@pytest.fixture
def experience_store(tmp_path):
    return SQLiteExperienceStore(tmp_path / "experience.db")


@pytest.fixture
def event_log(tmp_path):
    return SQLiteEventLog(tmp_path / "events.db")


def _make_plan(
    intent: str = "test intent",
    summary: str = "test summary",
    context_refs: list[ContextRef] | None = None,
) -> Plan:
    return Plan(
        intent=intent,
        summary=summary,
        context_refs_used=context_refs or [],
    )


def _make_trace(
    intent: str = "test intent",
    outcome_status: OutcomeStatus = OutcomeStatus.COMPLETED,
    outcome_summary: str | None = None,
    plan: Plan | None = None,
    tool_calls: list[ToolCallRecord] | None = None,
    workflow_id: str = "test_workflow",
) -> DecisionTrace:
    if plan is None:
        plan = _make_plan(intent=intent)
    return DecisionTrace(
        agent_profile_id="test_agent",
        intent=intent,
        context_packet_id="packet_001",
        plan=plan,
        tool_calls=tool_calls or [],
        outcome=Outcome(
            status=outcome_status,
            summary=outcome_summary,
        ),
        workflow_id=workflow_id,
    )


class TestOutcomeMapping:
    def test_completed_maps_to_success(self):
        assert outcome_to_label(OutcomeStatus.COMPLETED) == OutcomeLabel.SUCCESS

    def test_partial_maps_to_partial(self):
        assert outcome_to_label(OutcomeStatus.PARTIAL) == OutcomeLabel.PARTIAL

    def test_failed_maps_to_failure(self):
        assert outcome_to_label(OutcomeStatus.FAILED) == OutcomeLabel.FAILURE

    def test_needs_approval_maps_to_unknown(self):
        assert outcome_to_label(OutcomeStatus.NEEDS_APPROVAL) == OutcomeLabel.UNKNOWN

    def test_cancelled_maps_to_unknown(self):
        assert outcome_to_label(OutcomeStatus.CANCELLED) == OutcomeLabel.UNKNOWN


class TestExtractCase:
    def test_extract_case_basic(self, experience_store):
        miner = ExperienceMiner(experience_store)
        trace = _make_trace(
            intent="daily review",
            outcome_status=OutcomeStatus.COMPLETED,
            outcome_summary="All tasks reviewed",
        )

        case = miner.extract_case(trace)

        assert case.trace_id == trace.trace_id
        assert case.intent == "daily review"
        assert case.label == OutcomeLabel.SUCCESS
        assert case.outcome_summary == "All tasks reviewed"
        assert case.workflow_id == "test_workflow"
        assert case.agent_profile_id == "test_agent"
        assert case.plan_summary == "test summary"

    def test_extract_case_idempotent(self, experience_store):
        miner = ExperienceMiner(experience_store)
        trace = _make_trace()

        case1 = miner.extract_case(trace)
        case2 = miner.extract_case(trace)

        assert case1.case_id == case2.case_id
        assert case1.trace_id == case2.trace_id

    def test_extracts_capability_names(self, experience_store):
        miner = ExperienceMiner(experience_store)
        tool_calls = [
            ToolCallRecord(
                capability_name="tasks.create@v1",
                status=CallStatus.SUCCESS,
            ),
            ToolCallRecord(
                capability_name="notes.update@v1",
                status=CallStatus.SUCCESS,
            ),
            ToolCallRecord(
                capability_name="tasks.create@v1",
                status=CallStatus.SUCCESS,
            ),
        ]
        trace = _make_trace(tool_calls=tool_calls)

        case = miner.extract_case(trace)

        assert sorted(case.capability_names) == [
            "notes.update@v1",
            "tasks.create@v1",
        ]

    def test_extracts_entity_types(self, experience_store):
        miner = ExperienceMiner(experience_store)
        refs = [
            ContextRef(ref_type=RefType.NOTE, ref_id="note_001"),
            ContextRef(ref_type=RefType.TASK, ref_id="task_001"),
            ContextRef(ref_type=RefType.NOTE, ref_id="note_002"),
        ]
        plan = _make_plan(context_refs=refs)
        trace = _make_trace(plan=plan)

        case = miner.extract_case(trace)

        assert sorted(case.entity_types_used) == ["note", "task"]

    def test_handles_no_tool_calls(self, experience_store):
        miner = ExperienceMiner(experience_store)
        trace = _make_trace(tool_calls=[])

        case = miner.extract_case(trace)

        assert case.capability_names == []

    def test_handles_no_context_refs(self, experience_store):
        miner = ExperienceMiner(experience_store)
        plan = _make_plan(context_refs=[])
        trace = _make_trace(plan=plan)

        case = miner.extract_case(trace)

        assert case.entity_types_used == []

    def test_outcome_summary_fallback(self, experience_store):
        """When outcome has no summary, falls back to status value."""
        miner = ExperienceMiner(experience_store)
        trace = _make_trace(
            outcome_status=OutcomeStatus.FAILED,
            outcome_summary=None,
        )

        case = miner.extract_case(trace)

        assert case.outcome_summary == "failed"
        assert case.label == OutcomeLabel.FAILURE

    def test_event_emission(self, experience_store, event_log):
        miner = ExperienceMiner(experience_store, event_log=event_log)
        trace = _make_trace()

        case = miner.extract_case(trace)

        events = event_log.get_events(limit=10)
        assert len(events) >= 1
        last_event = events[-1]
        assert last_event.entity_id == case.case_id
        assert last_event.payload["trace_id"] == trace.trace_id
        assert last_event.payload["action"] == "experience_case_created"

    def test_no_event_when_no_event_log(self, experience_store):
        """No crash when event_log is None."""
        miner = ExperienceMiner(experience_store, event_log=None)
        trace = _make_trace()

        case = miner.extract_case(trace)
        assert case is not None

    def test_empty_workflow_id(self, experience_store):
        """Empty string workflow_id is treated as None."""
        miner = ExperienceMiner(experience_store)
        trace = _make_trace(workflow_id="")

        case = miner.extract_case(trace)

        # Empty string becomes None due to our "or None" logic
        assert case.workflow_id is None
