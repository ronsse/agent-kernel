"""Experience Memory schemas (v1.0.4).

Schemas for tracking decisions, evaluations, lessons, and behavioral patterns.

The experience memory system enables:
- Tracking decision outcomes (success/failure)
- Mining lessons from traces + evaluations
- Retrieving similar cases during future runs
- Building playbooks from accumulated experience

References:
- Design Patch v1.0.4: Universal Context System
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import Field

from agent_kernel.core.schemas.base import VersionedModel
from agent_kernel.core.schemas.context import ContextRef


class OutcomeLabel(str, Enum):
    """Label for decision outcome quality."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    REGRESSION = "regression"  # Was working, now broken
    UNKNOWN = "unknown"


class FailureCategory(str, Enum):
    """Categories of failure for diagnosis and learning."""

    MISRETRIEVAL = "misretrieval"      # Wrong/missing context retrieved
    MISPLANNING = "misplanning"        # Plan logic was incorrect
    TOOL_ERROR = "tool_error"          # Integration/tool failure
    POLICY_BLOCK = "policy_block"      # Blocked by approvals/constraints
    HALLUCINATION = "hallucination"    # Unsupported claims/facts
    UX = "ux"                          # Formatting/usability issues
    TIMEOUT = "timeout"                # Operation timed out
    RESOURCE = "resource"              # Resource limits exceeded
    OTHER = "other"


class OutcomeEvaluation(VersionedModel):
    """User or automated evaluation of a decision trace outcome.
    
    Captures feedback about whether a trace's outcome was good or bad,
    enabling the system to learn from both successes and failures.
    """

    evaluation_id: str = Field(
        ...,
        description="Unique identifier for this evaluation",
    )
    trace_id: str = Field(
        ...,
        description="The DecisionTrace being evaluated",
    )
    run_id: str | None = Field(
        default=None,
        description="Optional workflow run ID if part of a workflow",
    )

    # Evaluation data
    label: OutcomeLabel = Field(
        default=OutcomeLabel.UNKNOWN,
        description="High-level outcome label",
    )
    rating: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="Optional 1-5 rating for quality",
    )
    failure_category: FailureCategory | None = Field(
        default=None,
        description="Category of failure if label is FAILURE",
    )
    feedback: str | None = Field(
        default=None,
        description="Short user feedback text",
    )

    # Metadata
    created_at: datetime = Field(
        ...,
        description="When the evaluation was recorded",
    )
    evaluator: str | None = Field(
        default=None,
        description="Who/what provided the evaluation (user, auto, review_agent)",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional evaluation metadata",
    )


class ExperienceCase(VersionedModel):
    """Compacted, retrievable case memory derived from a trace.
    
    Cases are the durable learning substrate - they survive trace compaction
    and become the primary objects for experience retrieval.
    """

    case_id: str = Field(
        ...,
        description="Unique identifier for this case",
    )
    trace_id: str = Field(
        ...,
        description="Source DecisionTrace ID",
    )
    intent: str = Field(
        ...,
        description="The original intent/goal",
    )
    intent_embedding_id: str | None = Field(
        default=None,
        description="Vector embedding ID for similarity search",
    )

    # Summaries for retrieval (generated from trace)
    context_summary: str | None = Field(
        default=None,
        description="Summary of context that was retrieved",
    )
    plan_summary: str | None = Field(
        default=None,
        description="Summary of the plan that was created",
    )
    outcome_summary: str | None = Field(
        default=None,
        description="Summary of what happened",
    )

    # Structured features for filtering
    workflow_id: str | None = Field(
        default=None,
        description="Workflow this case was part of",
    )
    agent_profile_id: str | None = Field(
        default=None,
        description="Agent profile that was used",
    )
    capability_names: list[str] = Field(
        default_factory=list,
        description="Capabilities/tools that were used",
    )
    sources_used: list[str] = Field(
        default_factory=list,
        description="Data sources that were queried (obsidian, slack, etc.)",
    )
    entity_types_used: list[str] = Field(
        default_factory=list,
        description="Entity types that were retrieved (note, message, etc.)",
    )

    # Evaluation rollup (from OutcomeEvaluation if any)
    label: OutcomeLabel = Field(
        default=OutcomeLabel.UNKNOWN,
        description="Outcome label from evaluation",
    )
    rating: int | None = Field(
        default=None,
        description="Rating from evaluation",
    )
    failure_category: FailureCategory | None = Field(
        default=None,
        description="Failure category if applicable",
    )

    # Timestamps
    created_at: datetime = Field(
        ...,
        description="When the case was created",
    )
    updated_at: datetime = Field(
        ...,
        description="When the case was last updated",
    )


class LessonScope(VersionedModel):
    """Scope defining when a lesson applies."""

    workflow_id: str | None = Field(
        default=None,
        description="Specific workflow this lesson applies to",
    )
    capability_name: str | None = Field(
        default=None,
        description="Specific capability this lesson applies to",
    )
    entity_type: str | None = Field(
        default=None,
        description="Entity type this lesson applies to",
    )
    project_id: str | None = Field(
        default=None,
        description="Project this lesson applies to",
    )
    source_id: str | None = Field(
        default=None,
        description="Data source this lesson applies to",
    )


class LessonLearned(VersionedModel):
    """An actionable lesson mined from experience cases.
    
    Lessons are short, actionable guidance that can be retrieved
    during future runs to warn or guide agents.
    
    All auto-generated lessons start as candidates and require
    human approval to become active.
    """

    lesson_id: str = Field(
        ...,
        description="Unique identifier for this lesson",
    )
    title: str = Field(
        ...,
        description="Short title summarizing the lesson",
    )
    lesson_text: str = Field(
        ...,
        description="The actionable lesson text",
    )
    scope: LessonScope = Field(
        default_factory=LessonScope,
        description="When this lesson applies",
    )

    # Evidence trail
    source_trace_ids: list[str] = Field(
        default_factory=list,
        description="Traces that contributed to this lesson",
    )
    source_case_ids: list[str] = Field(
        default_factory=list,
        description="Cases that contributed to this lesson",
    )

    # Confidence and lifecycle
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Confidence in the lesson (0-1)",
    )
    status: Literal["active", "deprecated", "candidate"] = Field(
        default="candidate",
        description="Lifecycle status (all new lessons start as candidates)",
    )

    # Timestamps
    created_at: datetime = Field(
        ...,
        description="When the lesson was created",
    )
    updated_at: datetime = Field(
        ...,
        description="When the lesson was last updated",
    )


class PlaybookSelector(VersionedModel):
    """Selector for when a playbook applies."""

    workflow_id: str | None = Field(
        default=None,
        description="Workflow ID this playbook applies to",
    )
    project_id: str | None = Field(
        default=None,
        description="Project ID this playbook applies to",
    )
    intent_contains: list[str] = Field(
        default_factory=list,
        description="Keywords that trigger this playbook",
    )
    capability_names: list[str] = Field(
        default_factory=list,
        description="Capabilities that trigger this playbook",
    )


class Playbook(VersionedModel):
    """A versioned behavioral pattern for a specific workflow type.
    
    Playbooks define:
    - What context must be present
    - Expected output formats
    - Known pitfalls and verification steps
    - Suggested reasoning tier
    
    Playbooks are the mechanism for encoding learned behavior patterns
    in a human-editable, auditable form.
    """

    playbook_id: str = Field(
        ...,
        description="Unique identifier for this playbook",
    )
    name: str = Field(
        ...,
        description="Human-readable playbook name",
    )
    description: str | None = Field(
        default=None,
        description="Description of what this playbook is for",
    )
    version: str = Field(
        default="v1",
        description="Playbook version for evolution tracking",
    )

    # Matching rules
    selectors: list[PlaybookSelector] = Field(
        default_factory=list,
        description="Rules for when this playbook applies",
    )

    # Behavioral guidance
    required_entity_types: list[str] = Field(
        default_factory=list,
        description="Entity types that must be in context",
    )
    required_sources: list[str] = Field(
        default_factory=list,
        description="Data sources that must be queried",
    )
    output_format_refs: list[ContextRef] = Field(
        default_factory=list,
        description="References to output templates/specs",
    )
    checklist: list[str] = Field(
        default_factory=list,
        description="Steps to verify before completion",
    )
    pitfalls: list[str] = Field(
        default_factory=list,
        description="Known issues to avoid",
    )

    # Suggested reasoning
    recommended_thinking_tier: int | None = Field(
        default=None,
        description="Suggested thinking tier (0-3)",
    )

    # Evidence and evolution
    derived_from_lessons: list[str] = Field(
        default_factory=list,
        description="Lesson IDs that contributed to this playbook",
    )
    status: Literal["active", "deprecated", "candidate"] = Field(
        default="candidate",
        description="Lifecycle status",
    )

    # Timestamps
    created_at: datetime = Field(
        ...,
        description="When the playbook was created",
    )
    updated_at: datetime = Field(
        ...,
        description="When the playbook was last updated",
    )
