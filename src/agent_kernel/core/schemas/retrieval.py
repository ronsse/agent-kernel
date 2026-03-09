"""Retrieval Plan and Quality schemas for v1.0.2 flexible context retrieval.

These schemas define the structured output of retrieval planning, whether
done by the deterministic BaselineRetrievalPlanner or the LLM-powered
InstructedRetrievalPlanner. All retrieval directives are validated against
Source Descriptors before execution.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import KernelModel, VersionedModel


class RetrievalFilter(KernelModel):
    """A single filter condition for retrieval.

    Filters must reference fields that exist in the source's SourceDescriptor
    and use operators that are allowed for that field type.
    """

    field: str = Field(
        description="Field name to filter on (must exist in SourceDescriptor)",
    )
    op: str = Field(
        description="Filter operator (must be allowed for this field)",
    )
    value: Any = Field(
        description="Value to filter against",
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for query execution."""
        return {
            "field": self.field,
            "op": self.op,
            "value": self.value,
        }


class RetrievalDirective(KernelModel):
    """A single retrieval directive within a RetrievalPlan.

    Each directive specifies:
    - What source to query
    - What entity type to retrieve
    - Optional semantic query
    - Optional filters (validated against SourceDescriptor)
    - Ranking and limit parameters
    """

    directive_id: str = Field(
        default_factory=generate_ulid,
        description="Unique identifier for this directive",
    )
    source_id: str = Field(
        description="Source to query (e.g., 'obsidian', 'graph', 'tasks')",
    )
    entity_type: str = Field(
        description="Type of entity to retrieve (e.g., 'note', 'task', 'calendar_event')",
    )
    query: str | None = Field(
        default=None,
        description="Semantic/keyword query string",
    )
    filters: list[RetrievalFilter] = Field(
        default_factory=list,
        description="Structured filters to apply",
    )
    top_k: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of results to return",
    )
    min_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum relevance score for inclusion",
    )
    recency_boost: bool = Field(
        default=False,
        description="Whether to boost more recent items in ranking",
    )
    reason: str | None = Field(
        default=None,
        description="Explanation of why this directive is needed",
    )


class RetrievalPlan(VersionedModel):
    """A validated plan for retrieving context.

    RetrievalPlans are produced by either:
    - BaselineRetrievalPlanner: Deterministic, no LLM
    - InstructedRetrievalPlanner: LLM-powered, for complex constraints

    The plan is always validated against SourceDescriptors before execution.
    """

    retrieval_plan_id: str = Field(
        default_factory=generate_ulid,
        description="Unique identifier for this plan",
    )
    intent: str = Field(
        description="The original user intent/query",
    )
    mode: Literal["baseline", "instructed", "iterative"] = Field(
        default="baseline",
        description="How this plan was generated",
    )
    packs_used: list[str] = Field(
        default_factory=list,
        description="IDs of context packs included",
    )
    directives: list[RetrievalDirective] = Field(
        default_factory=list,
        description="Retrieval directives to execute",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description="Assumptions made during planning (for transparency)",
    )

    @property
    def directive_count(self) -> int:
        """Get number of directives in this plan."""
        return len(self.directives)

    def get_directive(self, directive_id: str) -> RetrievalDirective | None:
        """Get a directive by ID."""
        for directive in self.directives:
            if directive.directive_id == directive_id:
                return directive
        return None


class CoverageGateResult(KernelModel):
    """Result of a single coverage gate check.

    Gates verify retrieval quality before packing the ContextPacket.
    """

    gate: str = Field(
        description="Name of the gate (e.g., 'PackPresenceGate', 'ParityGate')",
    )
    passed: bool = Field(
        description="Whether the gate passed",
    )
    severity: Literal["info", "warning", "error"] = Field(
        default="error",
        description="Severity level if gate failed",
    )
    details: str | None = Field(
        default=None,
        description="Details about the gate result",
    )


class RetrievalQualityReport(KernelModel):
    """Quality report from retrieval gate checks.

    This is included in the RetrievalReport for observability and debugging.
    """

    mode: str = Field(
        description="Retrieval mode used (baseline/instructed/iterative)",
    )
    packs_included: list[str] = Field(
        default_factory=list,
        description="Context pack IDs that were included",
    )
    directives_executed: int = Field(
        default=0,
        description="Number of directives executed",
    )
    candidates_considered: int = Field(
        default=0,
        description="Total candidates before deduplication/ranking",
    )
    items_selected: int = Field(
        default=0,
        description="Final items selected for ContextPacket",
    )
    gate_results: list[CoverageGateResult] = Field(
        default_factory=list,
        description="Results of each coverage gate",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings generated during retrieval",
    )

    @property
    def all_gates_passed(self) -> bool:
        """Check if all gates passed."""
        return all(g.passed for g in self.gate_results)

    @property
    def has_errors(self) -> bool:
        """Check if any gate failed with error severity."""
        return any(
            not g.passed and g.severity == "error"
            for g in self.gate_results
        )

    @property
    def has_warnings(self) -> bool:
        """Check if any gate failed with warning severity or has warnings."""
        return bool(self.warnings) or any(
            not g.passed and g.severity == "warning"
            for g in self.gate_results
        )


class RetrievalScope(KernelModel):
    """Scope parameters for retrieval planning.

    This bundles all the contextual information needed to create a retrieval plan.
    """

    intent: str = Field(
        description="The user's query or intent",
    )
    vault_id: str | None = Field(
        default=None,
        description="ID of the vault being operated on",
    )
    project_id: str | None = Field(
        default=None,
        description="ID of the project in scope",
    )
    workflow_id: str | None = Field(
        default=None,
        description="ID of the workflow being executed",
    )
    agent_profile_id: str | None = Field(
        default=None,
        description="ID of the agent profile in use",
    )
    path: str | None = Field(
        default=None,
        description="File or directory path in scope",
    )
    time_range_days: int | None = Field(
        default=None,
        description="Optional time range constraint in days",
    )
