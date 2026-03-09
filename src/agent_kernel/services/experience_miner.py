"""Experience Miner - extracts learning cases from decision traces.

Deterministically converts DecisionTrace -> ExperienceCase for the
experience memory system. Optionally generates lesson candidates
using LLM analysis of case patterns.
"""

from __future__ import annotations

import json

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.experience import (
    ExperienceCase,
    LessonLearned,
    LessonScope,
    OutcomeLabel,
)
from agent_kernel.core.schemas.trace import DecisionTrace, OutcomeStatus
from agent_kernel.memory.event_log import EventLog
from agent_kernel.memory.experience_store import ExperienceStore
from agent_kernel.services.llm import LLMService

logger = structlog.get_logger(__name__)


def outcome_to_label(status: OutcomeStatus) -> OutcomeLabel:
    """Map trace outcome status to experience label."""
    mapping = {
        OutcomeStatus.COMPLETED: OutcomeLabel.SUCCESS,
        OutcomeStatus.PARTIAL: OutcomeLabel.PARTIAL,
        OutcomeStatus.FAILED: OutcomeLabel.FAILURE,
        OutcomeStatus.NEEDS_APPROVAL: OutcomeLabel.UNKNOWN,
        OutcomeStatus.CANCELLED: OutcomeLabel.UNKNOWN,
    }
    return mapping.get(status, OutcomeLabel.UNKNOWN)


class ExperienceMiner:
    """Extracts ExperienceCase records from DecisionTrace objects.

    Provides:
    - Deterministic trace -> case extraction with idempotency
    - Optional LLM-powered lesson candidate generation from failure patterns
    """

    def __init__(
        self,
        experience_store: ExperienceStore,
        event_log: EventLog | None = None,
        llm_service: LLMService | None = None,
    ) -> None:
        self._store = experience_store
        self._event_log = event_log
        self._llm_service = llm_service

    def extract_case(self, trace: DecisionTrace) -> ExperienceCase:
        """Extract an ExperienceCase from a DecisionTrace.

        Idempotent: if a case already exists for this trace, returns it.

        Args:
            trace: The decision trace to extract from.

        Returns:
            The extracted (or existing) ExperienceCase.
        """
        existing = self._store.get_case_for_trace(trace.trace_id)
        if existing is not None:
            logger.debug(
                "experience_case_already_exists",
                trace_id=trace.trace_id,
                case_id=existing.case_id,
            )
            return existing

        # Extract capability names from tool calls
        capability_names: list[str] = []
        if trace.tool_calls:
            capability_names = list(
                {tc.capability_name for tc in trace.tool_calls}
            )

        # Extract entity types from context refs
        entity_types_used: list[str] = []
        if trace.plan and trace.plan.context_refs_used:
            entity_types_used = list(
                {
                    (
                        ref.ref_type.value
                        if hasattr(ref.ref_type, "value")
                        else str(ref.ref_type)
                    )
                    for ref in trace.plan.context_refs_used
                }
            )

        # Build plan and outcome summaries
        plan_summary: str | None = None
        if trace.plan:
            plan_summary = trace.plan.summary

        outcome_summary: str | None = None
        if trace.outcome:
            outcome_summary = (
                trace.outcome.summary or trace.outcome.status.value
            )

        # Map outcome status to label
        label = OutcomeLabel.UNKNOWN
        if trace.outcome:
            label = outcome_to_label(trace.outcome.status)

        now = utc_now()
        case = ExperienceCase(
            case_id=generate_ulid(),
            trace_id=trace.trace_id,
            intent=trace.intent,
            workflow_id=trace.workflow_id or None,
            agent_profile_id=trace.agent_profile_id,
            capability_names=capability_names,
            entity_types_used=entity_types_used,
            sources_used=[],
            plan_summary=plan_summary,
            outcome_summary=outcome_summary,
            label=label,
            created_at=now,
            updated_at=now,
        )

        self._store.put_case(case)

        if self._event_log is not None:
            self._event_log.emit(
                "trace.completed",
                source="experience_miner",
                entity_id=case.case_id,
                entity_type="experience_case",
                payload={
                    "trace_id": trace.trace_id,
                    "label": case.label.value,
                    "action": "experience_case_created",
                },
            )

        logger.info(
            "experience_case_extracted",
            case_id=case.case_id,
            trace_id=trace.trace_id,
            label=case.label.value,
        )

        return case

    async def generate_lesson_candidates(
        self,
        workflow_id: str | None = None,
        min_cases: int = 3,
    ) -> list[LessonLearned]:
        """Generate lesson candidates from failure cases using LLM analysis.

        Args:
            workflow_id: Optional filter to a specific workflow.
            min_cases: Minimum failure cases required before analysis.

        Returns:
            List of candidate LessonLearned objects (stored in experience store).
        """
        if self._llm_service is None:
            return []

        cases = self._store.list_cases(workflow_id=workflow_id, limit=100)

        # Focus on failure cases for lesson extraction
        failure_cases = [
            c for c in cases
            if c.label in (OutcomeLabel.FAILURE, OutcomeLabel.PARTIAL)
        ]
        if len(cases) < min_cases or len(failure_cases) < min_cases:
            return []

        # Build case summaries for the LLM
        case_summaries = "\n".join(
            f"- Intent: {c.intent}, "
            f"Outcome: {c.label.value}, "
            f"Capabilities: {', '.join(c.capability_names)}, "
            f"Summary: {c.outcome_summary or 'N/A'}"
            for c in failure_cases[:20]  # Cap to avoid token overflow
        )

        prompt = (
            f"Analyze these {len(failure_cases)} failed/partial cases "
            f"and identify common patterns.\n"
            f"For each pattern, provide:\n"
            f"- title: Short descriptive title\n"
            f"- lesson_text: What should be done differently\n"
            f"- confidence: 0.0-1.0 how confident you are\n\n"
            f"Cases:\n{case_summaries}\n\n"
            f'Respond in JSON format:\n'
            f'[{{"title": "...", "lesson_text": "...", "confidence": 0.8}}]'
        )

        try:
            response = await self._llm_service.generate(
                system_prompt=(
                    "You are an experience analyst. "
                    "Identify patterns in failed cases."
                ),
                user_prompt=prompt,
            )
        except Exception:
            logger.exception("lesson_generation_llm_error")
            return []

        # Parse JSON from response
        lessons: list[LessonLearned] = []
        try:
            # Strip markdown code fences if present
            text = response.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("lesson_generation_parse_error", response=response[:200])
            return []

        if not isinstance(parsed, list):
            return []

        now = utc_now()
        source_case_ids = [c.case_id for c in failure_cases[:20]]
        for item in parsed:
            if not isinstance(item, dict):
                continue
            title = item.get("title", "")
            lesson_text = item.get("lesson_text", "")
            confidence = float(item.get("confidence", 0.5))
            if not title or not lesson_text:
                continue

            lesson = LessonLearned(
                lesson_id=generate_ulid(),
                title=title,
                lesson_text=lesson_text,
                scope=LessonScope(workflow_id=workflow_id),
                source_case_ids=source_case_ids,
                confidence=min(max(confidence, 0.0), 1.0),
                status="candidate",
                created_at=now,
                updated_at=now,
            )
            self._store.put_lesson(lesson)
            lessons.append(lesson)

            if self._event_log is not None:
                self._event_log.emit(
                    "trace.completed",
                    source="experience_miner",
                    entity_id=lesson.lesson_id,
                    entity_type="lesson_learned",
                    payload={
                        "action": "lesson_candidate_created",
                        "title": lesson.title,
                        "confidence": lesson.confidence,
                    },
                )

        logger.info(
            "lesson_candidates_generated",
            count=len(lessons),
            workflow_id=workflow_id,
        )

        return lessons
