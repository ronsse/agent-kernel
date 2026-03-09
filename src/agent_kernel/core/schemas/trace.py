"""Trace schemas - ToolCallRecord, DecisionTrace, and related types."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import AliasChoices, Field, field_validator

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import (
    KernelModel,
    VersionedModel,
    get_kernel_version,
    utc_now,
)
from agent_kernel.core.schemas.context import ContextRef
from agent_kernel.core.schemas.skill import SkillResourceRef
from agent_kernel.core.schemas.plan import Plan, SideEffect


class CallStatus(str, Enum):
    """Status of a tool call execution."""

    SUCCESS = "success"
    ERROR = "error"
    FAILED = "failed"
    DENIED = "denied"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


class ErrorRecord(KernelModel):
    """Structured error information."""

    code: str
    message: str
    details: dict[str, Any] | None = None
    retryable: bool = False


class CostRecord(KernelModel):
    """Cost tracking for a tool call."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None


class PromptPartRef(KernelModel):
    """Provenance reference for a prompt part."""

    prompt_id: str
    hash: str
    layer: str | None = None
    path: str | None = None


class ToolCallRecord(VersionedModel):
    """Record of what actually ran during execution.

    Immutable execution record for tracing and debugging.
    Includes trust boundary enforcement: requested values from agent
    vs effective values computed by executor.
    """

    tool_call_id: str = Field(
        default_factory=generate_ulid,
        validation_alias=AliasChoices("tool_call_id", "record_id"),
    )
    capability_name: str
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    duration_ms: int = 0
    input: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("input", "args"),
    )  # Redacted as needed
    output: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("output", "result"),
    )  # Redacted as needed
    status: CallStatus = CallStatus.SUCCESS
    error: ErrorRecord | None = None
    cost: CostRecord | None = None
    related_action_id: str | None = None  # Link to ActionRequest

    # Trust boundary: agent hints (non-authoritative)
    requested_side_effect: SideEffect | None = Field(
        default=None,
        description="Side effect level requested by agent (hint only)",
    )
    requested_requires_approval: bool | None = Field(
        default=None,
        description="Approval requirement requested by agent (hint only)",
    )

    # Trust boundary: executor-computed values (authoritative)
    effective_side_effect: SideEffect = Field(
        default=SideEffect.NONE,
        description="Actual side effect level computed from CapabilityDef + AgentProfile",
    )
    effective_requires_approval: bool = Field(
        default=False,
        description="Actual approval requirement computed from policy",
    )

    # Idempotency tracking
    idempotency_key: str | None = Field(
        default=None,
        description="Idempotency key for deduplication of writes",
    )

    @field_validator("error", mode="before")
    @classmethod
    def _coerce_error(cls, value: Any) -> Any:
        """Allow legacy string errors and convert to structured ErrorRecord."""
        if value is None or isinstance(value, ErrorRecord):
            return value
        if isinstance(value, str):
            return ErrorRecord(code="error", message=value)
        return value


class ApprovalRecord(KernelModel):
    """Record of an approval or denial decision."""

    action_id: str
    approved: bool
    approved_by: str | None = None
    approved_at: datetime | None = None
    reason: str | None = None


class OutcomeStatus(str, Enum):
    """Status of the overall execution outcome."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    NEEDS_APPROVAL = "needs_approval"
    CANCELLED = "cancelled"


class Outcome(KernelModel):
    """Final outcome of a decision trace."""

    status: OutcomeStatus = OutcomeStatus.COMPLETED
    artifacts: list[ContextRef] = Field(default_factory=list)  # Created items
    summary: str | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


class Provenance(KernelModel):
    """Version and configuration information for reproducibility."""

    prompt_hash: str | None = None
    prompt_bundle_hash: str | None = None
    prompt_parts: list[PromptPartRef] = Field(default_factory=list)
    config_hash: str
    git_commit: str | None = None
    engine_version: str
    kernel_version: str


def _default_provenance() -> Provenance:
    return Provenance(
        config_hash="legacy",
        engine_version="legacy",
        kernel_version=get_kernel_version(),
    )


class ReasoningMetadata(KernelModel):
    """Metadata about reasoning decisions for analysis.

    Captures the thinking policy decisions made during plan generation,
    including tier selection, escalations, and gate failures.
    """

    # Tier information
    initial_tier: int = 1
    final_tier: int = 1
    tier_name: str = "standard"

    # Model configuration used
    model_id: str = ""
    reasoning_effort: str = "medium"  # none/low/medium/high

    # Escalation tracking
    total_attempts: int = 1
    escalation_count: int = 0
    escalation_reasons: list[str] = Field(default_factory=list)

    # Quality gate results
    gate_failures: list[str] = Field(default_factory=list)
    gate_warnings: list[str] = Field(default_factory=list)

    # Critic results (if used)
    critic_used: bool = False
    critic_issues: list[str] = Field(default_factory=list)

    # Token usage for reasoning
    total_reasoning_tokens: int = 0


class DecisionTrace(VersionedModel):
    """The complete auditable unit of work.

    Contains everything needed to understand and replay a decision:
    - What context was provided
    - What plan was generated
    - What actions were executed
    - What the outcome was

    Inherits from VersionedModel for schema version tracking.
    """

    trace_id: str = Field(default_factory=generate_ulid)
    run_id: str = Field(default_factory=generate_ulid)  # Workflow run identifier
    workflow_id: str = Field(
        default="",
        description="Explicit workflow ID (not just run_id prefix)",
    )
    agent_profile_id: str
    engine_id: str = "legacy"  # Which engine produced the plan
    intent: str
    timestamp: datetime = Field(default_factory=utc_now)
    context_packet_id: str
    plan: Plan
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    llm_calls: list[Any] = Field(
        default_factory=list,
        description="LLM calls made during plan generation (typed as LLMCallRecord at runtime)",
    )
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    outcome: Outcome = Field(default_factory=Outcome)
    provenance: Provenance = Field(default_factory=_default_provenance)

    # Reasoning metadata for analysis (optional)
    reasoning: ReasoningMetadata | None = None

    # Skill usage signals (optional)
    skills_considered: list[str] = Field(default_factory=list)
    skills_invoked: list[str] = Field(default_factory=list)
    skills_loaded_files: list[SkillResourceRef] = Field(default_factory=list)

    def total_duration_ms(self) -> int:
        """Calculate total duration of all tool calls."""
        return sum(tc.duration_ms for tc in self.tool_calls)

    def has_errors(self) -> bool:
        """Check if any tool calls failed."""
        return any(
            tc.status in {CallStatus.ERROR, CallStatus.FAILED}
            for tc in self.tool_calls
        )

    def success_rate(self) -> float:
        """Calculate success rate of tool calls."""
        if not self.tool_calls:
            return 1.0
        successes = sum(1 for tc in self.tool_calls if tc.status == CallStatus.SUCCESS)
        return successes / len(self.tool_calls)
