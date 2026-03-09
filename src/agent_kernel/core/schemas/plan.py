"""Plan schemas - ActionRequest, Plan, and related types."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, field_validator

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import KernelModel, VersionedModel
from agent_kernel.core.schemas.context import ContextRef


class SideEffect(str, Enum):
    """Classification of action side effects."""

    NONE = "none"  # No effect
    READ = "read"  # Read-only
    WRITE = "write"  # Local or reversible write
    EXECUTE = "execute"  # External or irreversible effect
    LOCAL_WRITE = "local"  # Legacy: Local file/DB changes
    EXTERNAL_WRITE = "external"  # Legacy: External API calls

    @property
    def is_read_only(self) -> bool:
        return self in {SideEffect.NONE, SideEffect.READ}

    @property
    def is_write(self) -> bool:
        return self in {
            SideEffect.WRITE,
            SideEffect.EXECUTE,
            SideEffect.LOCAL_WRITE,
            SideEffect.EXTERNAL_WRITE,
        }

    @property
    def is_external(self) -> bool:
        return self in {SideEffect.EXECUTE, SideEffect.EXTERNAL_WRITE}


class RiskLevel(str, Enum):
    """Risk level assessment."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionRequest(KernelModel):
    """A tool-like action that can be executed deterministically.

    Actions are validated against capability schemas before execution.

    Note: side_effect and requires_approval are agent hints (non-authoritative).
    The executor computes effective values from CapabilityDef and AgentProfile.
    """

    action_id: str = Field(default_factory=generate_ulid)
    capability_name: str  # e.g., "tasks.create@v1"
    args: dict = Field(default_factory=dict)  # Must validate against tool schema

    # Agent hints (non-authoritative - executor computes effective values)
    side_effect: SideEffect = SideEffect.NONE
    requires_approval: bool = False

    # Evidence linking action to context (for validation)
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="ContextRef.ref_id values that support this action",
    )

    rollback_hint: str | None = None
    idempotency_key: str | None = None  # Required for writes
    cap_group: str | None = Field(
        default=None,
        description="Optional cap group for deterministic action limits",
    )
    cap_limit: int | None = Field(
        default=None,
        description="Maximum actions allowed for cap_group in a plan",
    )

    @field_validator("capability_name")
    @classmethod
    def _validate_capability_name(cls, value: str) -> str:
        """Ensure capability names include a version suffix."""
        if "@" not in value:
            raise ValueError("capability_name must include @version suffix")
        name, version = value.split("@", 1)
        if not name or not version:
            raise ValueError("capability_name must include @version suffix")
        return value


class RiskAssessment(KernelModel):
    """Assessment of plan risk."""

    level: RiskLevel = RiskLevel.LOW
    reasons: list[str] = Field(default_factory=list)


class PlanValidation(KernelModel):
    """Self-check fields for plan validation."""

    missing_info: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class Plan(VersionedModel):
    """The structured agent output.

    Strictly validated. The agent must cite sources and
    propose specific actions from allowed capabilities.

    Inherits from VersionedModel for schema version tracking.
    """

    plan_id: str = Field(default_factory=generate_ulid)
    intent: str
    summary: str  # 1-5 sentences describing the plan
    context_refs_used: list[ContextRef] = Field(default_factory=list)  # Must cite
    actions: list[ActionRequest] = Field(default_factory=list)
    risk: RiskAssessment = Field(default_factory=RiskAssessment)
    questions: list[str] = Field(default_factory=list)  # Clarifying questions
    notes: str | None = None  # Short rationale (keep concise)
    validation: PlanValidation = Field(default_factory=PlanValidation)

    # Quality signals for escalation decisions (not chain-of-thought)
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Model's confidence in the plan"
    )
    uncertainties: list[str] = Field(
        default_factory=list, description="Key uncertainties in the plan"
    )
    assumptions: list[str] = Field(
        default_factory=list, description="Assumptions made by the model"
    )
    verification_steps: list[str] = Field(
        default_factory=list, description="Steps to verify the plan"
    )

    def has_external_writes(self) -> bool:
        """Check if plan has any external write actions."""
        return any(a.side_effect.is_external for a in self.actions)

    def requires_any_approval(self) -> bool:
        """Check if any action requires approval."""
        return any(a.requires_approval for a in self.actions)

    def get_capability_names(self) -> list[str]:
        """Get list of unique capability names used."""
        return list({a.capability_name for a in self.actions})
