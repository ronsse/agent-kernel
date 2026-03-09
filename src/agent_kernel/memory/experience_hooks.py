"""Experience Memory Hooks - record and retrieve triage outcomes.

Provides hooks for:
- Recording experience cases after workflow completion
- Retrieving similar past experiences for context enrichment
- Supporting the tiered integration architecture (Tier 2: KERNEL_LITE)

This enables learning from past triage decisions:
- What worked? What failed?
- Similar patterns for future predictions
- Gradual improvement of deterministic rules

References:
- Design: Tiered Integration Architecture
- Schema: experience.py (ExperienceCase, OutcomeEvaluation)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.experience import (
    ExperienceCase,
    FailureCategory,
    OutcomeLabel,
)
from agent_kernel.core.schemas.trace import DecisionTrace

if TYPE_CHECKING:
    from agent_kernel.memory.experience_store import ExperienceStore
    from agent_kernel.memory.vector_store import VectorStore
    from agent_kernel.workflows.runner import WorkflowResult

logger = structlog.get_logger(__name__)


@dataclass
class ExperienceMatch:
    """A matched experience case with relevance score."""

    case: ExperienceCase
    score: float  # 0.0 to 1.0, higher is more relevant
    match_reason: str  # Why this case matched (e.g., "same_workflow", "similar_intent")


@dataclass
class ExperienceContext:
    """Experience context for enriching workflow decisions."""

    similar_cases: list[ExperienceMatch]
    success_rate: float  # 0.0 to 1.0
    common_failure_categories: list[FailureCategory]
    relevant_lessons: list[str]  # Lesson IDs


class ExperienceMemoryHooks:
    """Hooks for recording and retrieving experience memory.

    Integration with workflow runner:
    1. After workflow completes, call `record_outcome`
    2. During context assembly for Tier 2 workflows, call `get_similar_experiences`
    """

    def __init__(
        self,
        experience_store: ExperienceStore,
        vector_store: VectorStore | None = None,
    ) -> None:
        """Initialize experience memory hooks.

        Args:
            experience_store: Store for experience cases and evaluations
            vector_store: Optional vector store for semantic similarity search
        """
        self._experience_store = experience_store
        self._vector_store = vector_store

    def record_outcome(
        self,
        workflow_id: str,
        trace: DecisionTrace | None,
        success: bool,
        error: str | None = None,
        agent_profile_id: str | None = None,
    ) -> ExperienceCase | None:
        """Record the outcome of a workflow as an experience case.

        Creates an ExperienceCase that can be retrieved later for:
        - Finding similar past situations
        - Learning success/failure patterns
        - Improving triage decisions

        Args:
            workflow_id: The workflow that was executed
            trace: The decision trace (if available)
            success: Whether the workflow succeeded
            error: Error message if failed
            agent_profile_id: Agent profile used

        Returns:
            The created ExperienceCase, or None if no trace
        """
        if trace is None:
            logger.debug(
                "experience_record_skipped",
                reason="no_trace",
                workflow_id=workflow_id,
            )
            return None

        now = utc_now()

        # Determine outcome label
        if success:
            label = OutcomeLabel.SUCCESS
        elif error and "validation" in error.lower():
            label = OutcomeLabel.FAILURE
        elif error and "timeout" in error.lower():
            label = OutcomeLabel.FAILURE
        else:
            label = OutcomeLabel.FAILURE if error else OutcomeLabel.PARTIAL

        # Determine failure category
        failure_category = None
        if not success and error:
            failure_category = self._categorize_failure(error)

        # Extract capability names from plan
        capability_names = []
        if trace.plan and trace.plan.actions:
            capability_names = list({
                action.capability_name
                for action in trace.plan.actions
                if action.capability_name
            })

        # Extract sources from context
        sources_used = []
        entity_types_used = []
        if trace.context_packet:
            for ref in trace.context_packet.refs:
                if ref.source and ref.source not in sources_used:
                    sources_used.append(ref.source)
                if ref.ref_type and ref.ref_type.value not in entity_types_used:
                    entity_types_used.append(ref.ref_type.value)

        # Create summaries
        intent = trace.context_packet.intent if trace.context_packet else "unknown"
        context_summary = self._summarize_context(trace)
        plan_summary = self._summarize_plan(trace)
        outcome_summary = self._summarize_outcome(trace, success, error)

        case = ExperienceCase(
            case_id=generate_ulid(),
            trace_id=trace.trace_id,
            intent=intent,
            intent_embedding_id=None,  # Could add vector embedding later
            context_summary=context_summary,
            plan_summary=plan_summary,
            outcome_summary=outcome_summary,
            workflow_id=workflow_id,
            agent_profile_id=agent_profile_id,
            capability_names=capability_names,
            sources_used=sources_used,
            entity_types_used=entity_types_used,
            label=label,
            rating=None,  # User can rate later
            failure_category=failure_category,
            created_at=now,
            updated_at=now,
        )

        try:
            self._experience_store.put_case(case)
            logger.info(
                "experience_case_recorded",
                case_id=case.case_id,
                workflow_id=workflow_id,
                label=label.value,
                capability_count=len(capability_names),
            )
        except Exception as e:
            logger.warning(
                "experience_case_record_failed",
                error=str(e),
                workflow_id=workflow_id,
            )
            return None

        return case

    def get_similar_experiences(
        self,
        workflow_id: str,
        intent: str | None = None,
        capability_names: list[str] | None = None,
        limit: int = 5,
    ) -> ExperienceContext:
        """Get similar past experiences for context enrichment.

        Used during context assembly for Tier 2 (KERNEL_LITE) workflows
        to inform decisions based on past outcomes.

        Args:
            workflow_id: Current workflow being executed
            intent: Current intent/goal (for semantic matching)
            capability_names: Capabilities that will be used
            limit: Maximum number of cases to return

        Returns:
            ExperienceContext with similar cases and aggregate stats
        """
        similar_cases: list[ExperienceMatch] = []

        # Find cases with same workflow
        workflow_cases = self._experience_store.find_similar_cases(
            workflow_id=workflow_id,
            capability_names=capability_names,
            limit=limit,
        )

        for case in workflow_cases:
            score = self._compute_similarity_score(case, workflow_id, capability_names)
            similar_cases.append(ExperienceMatch(
                case=case,
                score=score,
                match_reason="same_workflow",
            ))

        # Sort by score descending
        similar_cases.sort(key=lambda m: m.score, reverse=True)
        similar_cases = similar_cases[:limit]

        # Compute success rate
        success_count = sum(1 for m in similar_cases if m.case.label == OutcomeLabel.SUCCESS)
        total_count = len(similar_cases)
        success_rate = success_count / total_count if total_count > 0 else 0.5

        # Collect common failure categories
        failure_categories = [
            m.case.failure_category
            for m in similar_cases
            if m.case.failure_category is not None
        ]
        unique_categories = list(set(failure_categories))

        # Get relevant lessons (would query lessons store in full impl)
        relevant_lessons: list[str] = []

        logger.debug(
            "experience_context_retrieved",
            workflow_id=workflow_id,
            cases_found=len(similar_cases),
            success_rate=success_rate,
        )

        return ExperienceContext(
            similar_cases=similar_cases,
            success_rate=success_rate,
            common_failure_categories=unique_categories,
            relevant_lessons=relevant_lessons,
        )

    def should_escalate_tier(
        self,
        workflow_id: str,
        current_tier: int,
    ) -> tuple[bool, str]:
        """Check if past experience suggests escalating to a higher tier.

        Based on failure patterns, recommends whether to use a higher
        integration tier than the default for this workflow.

        Args:
            workflow_id: The workflow being considered
            current_tier: The tier that would normally be used

        Returns:
            Tuple of (should_escalate, reason)
        """
        # Get recent cases for this workflow
        recent_cases = self._experience_store.list_cases(
            workflow_id=workflow_id,
            limit=10,
        )

        if len(recent_cases) < 3:
            # Not enough history to make a recommendation
            return False, "insufficient_history"

        # Check failure rate
        failure_count = sum(
            1 for c in recent_cases
            if c.label in (OutcomeLabel.FAILURE, OutcomeLabel.REGRESSION)
        )
        failure_rate = failure_count / len(recent_cases)

        # If failure rate is high, recommend escalation
        if failure_rate >= 0.5 and current_tier < 3:
            return True, f"high_failure_rate:{failure_rate:.0%}"

        # Check for specific failure patterns
        misretrieval_count = sum(
            1 for c in recent_cases
            if c.failure_category == FailureCategory.MISRETRIEVAL
        )
        if misretrieval_count >= 2 and current_tier < 2:
            return True, "repeated_misretrieval"

        misplanning_count = sum(
            1 for c in recent_cases
            if c.failure_category == FailureCategory.MISPLANNING
        )
        if misplanning_count >= 2 and current_tier < 3:
            return True, "repeated_misplanning"

        return False, "no_escalation_needed"

    def _categorize_failure(self, error: str) -> FailureCategory:
        """Categorize a failure based on the error message."""
        error_lower = error.lower()

        if any(kw in error_lower for kw in ["timeout", "timed out", "deadline"]):
            return FailureCategory.TIMEOUT

        if any(kw in error_lower for kw in ["rate limit", "quota", "too many"]):
            return FailureCategory.RESOURCE

        if any(kw in error_lower for kw in ["validation", "schema", "invalid"]):
            return FailureCategory.MISPLANNING

        if any(kw in error_lower for kw in ["not found", "missing", "404"]):
            return FailureCategory.MISRETRIEVAL

        if any(kw in error_lower for kw in ["api", "connection", "network"]):
            return FailureCategory.TOOL_ERROR

        if any(kw in error_lower for kw in ["approval", "blocked", "denied"]):
            return FailureCategory.POLICY_BLOCK

        return FailureCategory.OTHER

    def _summarize_context(self, trace: DecisionTrace) -> str:
        """Generate a short summary of the context."""
        if not trace.context_packet:
            return "No context"

        ref_count = len(trace.context_packet.refs)
        sources = list({
            ref.source for ref in trace.context_packet.refs if ref.source
        })

        return f"Context with {ref_count} refs from {', '.join(sources) or 'unknown'}"

    def _summarize_plan(self, trace: DecisionTrace) -> str:
        """Generate a short summary of the plan."""
        if not trace.plan or not trace.plan.actions:
            return "No plan"

        action_count = len(trace.plan.actions)
        capabilities = list({
            a.capability_name for a in trace.plan.actions if a.capability_name
        })[:3]  # Top 3

        return f"Plan with {action_count} actions: {', '.join(capabilities)}"

    def _summarize_outcome(
        self,
        trace: DecisionTrace,
        success: bool,
        error: str | None,
    ) -> str:
        """Generate a short summary of the outcome."""
        if success:
            # Count tool calls that succeeded
            if trace.tool_calls:
                completed = sum(1 for t in trace.tool_calls if t.status.value == "success")
                return f"Success: {completed}/{len(trace.tool_calls)} tools completed"
            return "Success: workflow completed"

        if error:
            # Truncate error to first 100 chars
            short_error = error[:100] + "..." if len(error) > 100 else error
            return f"Failed: {short_error}"

        return "Unknown outcome"

    def _compute_similarity_score(
        self,
        case: ExperienceCase,
        workflow_id: str,
        capability_names: list[str] | None,
    ) -> float:
        """Compute a similarity score between a case and current context."""
        score = 0.0

        # Workflow match is worth 0.4
        if case.workflow_id == workflow_id:
            score += 0.4

        # Capability overlap is worth 0.3
        if capability_names and case.capability_names:
            overlap = len(set(capability_names) & set(case.capability_names))
            total = len(set(capability_names) | set(case.capability_names))
            if total > 0:
                score += 0.3 * (overlap / total)

        # Recency bonus (recent cases worth up to 0.2 more)
        # This is a placeholder - would use actual timestamps
        score += 0.1

        # Success cases are slightly more valuable (0.1)
        if case.label == OutcomeLabel.SUCCESS:
            score += 0.1

        return min(score, 1.0)


def create_experience_hooks(
    experience_store: ExperienceStore,
    vector_store: VectorStore | None = None,
) -> ExperienceMemoryHooks:
    """Factory function to create experience memory hooks.

    Args:
        experience_store: Store for experience cases
        vector_store: Optional vector store for semantic search

    Returns:
        Configured ExperienceMemoryHooks instance
    """
    return ExperienceMemoryHooks(
        experience_store=experience_store,
        vector_store=vector_store,
    )


def record_workflow_outcome(
    experience_hooks: ExperienceMemoryHooks,
    workflow_result: WorkflowResult,
    agent_profile_id: str | None = None,
) -> ExperienceCase | None:
    """Convenience function to record a workflow result as experience.

    Args:
        experience_hooks: The experience hooks instance
        workflow_result: Result from workflow runner
        agent_profile_id: Agent profile used

    Returns:
        Created ExperienceCase if recorded
    """
    return experience_hooks.record_outcome(
        workflow_id=workflow_result.workflow_id,
        trace=workflow_result.trace,
        success=workflow_result.success,
        error=workflow_result.error,
        agent_profile_id=agent_profile_id,
    )
