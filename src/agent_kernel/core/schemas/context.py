"""Context schemas - ContextRef, ContextPacket, and related types.

v1.0.2 additions:
- RULE and SPEC RefTypes for context packs
- Extended RetrievalReport with retrieval_plan_id, quality
- Extended ContextPacket with retrieval_mode, context_packs

v1.0.4 additions:
- EntityRef embedding in ContextRef for universal entity support
- source_id, entity_type, entity_id fields on ContextRef
- Upcast rules from RefType to entity model
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, model_validator

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import KernelModel, VersionedModel, utc_now

if TYPE_CHECKING:
    from agent_kernel.core.schemas.entity import EntityRef


class RefType(str, Enum):
    """Type of reference to a source item."""

    NOTE = "note"
    TASK = "task"
    EVENT = "event"
    GRAPH_NODE = "graph_node"
    GRAPH_EDGE = "graph_edge"
    DOCUMENT = "doc"
    EMAIL = "email"
    SKILL = "skill"
    MEMORY = "memory"
    EXTERNAL = "external"
    # v1.0.2: Context pack reference types
    RULE = "rule"  # Vault/project rules and conventions
    SPEC = "spec"  # System specifications
    # v1.0.4: Experience memory types
    CASE = "case"  # Experience case
    LESSON = "lesson"  # Lesson learned
    PLAYBOOK = "playbook"  # Behavioral pattern
    # v1.0.6: Context graph types
    KNOWLEDGE = "knowledge"  # Knowledge graph node (concept, insight, etc.)
    TRAJECTORY = "trajectory"  # Past decision trajectory (episodic memory)


# Mapping from RefType to (source_id, entity_type) for upcasting
_REFTYPE_TO_ENTITY: dict[RefType, tuple[str, str]] = {
    RefType.NOTE: ("obsidian", "note"),
    RefType.TASK: ("tasks", "task"),
    RefType.EVENT: ("calendar", "calendar_event"),
    RefType.DOCUMENT: ("obsidian", "document"),
    RefType.EMAIL: ("outlook", "email"),
    RefType.SKILL: ("skills", "skill"),
    RefType.CASE: ("experience", "case"),
    RefType.LESSON: ("experience", "lesson"),
    RefType.PLAYBOOK: ("experience", "playbook"),
    RefType.KNOWLEDGE: ("kernel", "knowledge"),
    RefType.TRAJECTORY: ("kernel", "trajectory"),
}


class ContextRef(KernelModel):
    """Reference to any source item the agent used.

    Used for citations and provenance tracking.

    v1.0.4: Extended with entity model fields for universal entity support.
    When `entity` is present, it is the preferred reference.
    Legacy fields (ref_type, ref_id) are kept for backwards compatibility.
    """

    ref_type: RefType
    ref_id: str
    uri: str | None = None
    hash: str | None = None  # Content hash for reproducibility
    metadata: dict[str, Any] = Field(default_factory=dict)

    # v1.0.4: Entity model fields (preferred when present)
    entity: Any | None = Field(
        default=None,
        description="EntityRef for universal entity support (v1.0.4)",
    )
    source_id: str | None = Field(
        default=None,
        description="Source system identifier (v1.0.4)",
    )
    entity_type: str | None = Field(
        default=None,
        description="Entity type within source (v1.0.4)",
    )
    entity_id: str | None = Field(
        default=None,
        description="Stable entity ID within source (v1.0.4)",
    )

    @model_validator(mode="after")
    def upcast_to_entity_fields(self) -> "ContextRef":
        """Upcast legacy RefType to entity model fields if not set."""
        if self.source_id is None and self.ref_type in _REFTYPE_TO_ENTITY:
            source_id, entity_type = _REFTYPE_TO_ENTITY[self.ref_type]
            object.__setattr__(self, "source_id", source_id)
            object.__setattr__(self, "entity_type", entity_type)
            object.__setattr__(self, "entity_id", self.ref_id)
        return self

    def to_entity_ref(self) -> "EntityRef":
        """Convert to EntityRef for entity-based operations.

        Returns the embedded entity if present, otherwise creates one
        from legacy fields.
        """
        if self.entity is not None:
            return self.entity

        # Import here to avoid circular import
        from agent_kernel.core.schemas.entity import EntityRef

        return EntityRef(
            source_id=self.source_id or "unknown",
            entity_type=self.entity_type or self.ref_type.value,
            entity_id=self.entity_id or self.ref_id,
            uri=self.uri,
        )

    @classmethod
    def from_entity_ref(cls, entity: "EntityRef", ref_type: RefType | None = None) -> "ContextRef":
        """Create ContextRef from EntityRef.

        Args:
            entity: The EntityRef to wrap
            ref_type: Optional RefType override. If not provided, infers from entity_type.
        """
        # Infer ref_type from entity_type
        if ref_type is None:
            type_map = {
                "note": RefType.NOTE,
                "task": RefType.TASK,
                "calendar_event": RefType.EVENT,
                "email": RefType.EMAIL,
                "skill": RefType.SKILL,
                "case": RefType.CASE,
                "lesson": RefType.LESSON,
                "playbook": RefType.PLAYBOOK,
                "knowledge": RefType.KNOWLEDGE,
                "trajectory": RefType.TRAJECTORY,
            }
            ref_type = type_map.get(entity.entity_type, RefType.EXTERNAL)

        return cls(
            ref_type=ref_type,
            ref_id=entity.entity_id,
            uri=entity.uri,
            hash=entity.canonical_hash,
            entity=entity,
            source_id=entity.source_id,
            entity_type=entity.entity_type,
            entity_id=entity.entity_id,
            metadata=entity.metadata,
        )


class RetrievalLimits(KernelModel):
    """Limits for context retrieval by type."""

    max_notes: int = Field(default=20, ge=0)
    max_tasks: int = Field(default=30, ge=0)
    max_events: int = Field(default=10, ge=0)
    max_graph_nodes: int = Field(default=50, ge=0)


class ContextBudget(KernelModel):
    """Budget constraints for context assembly."""

    max_tokens: int = Field(default=8000, ge=0)
    max_items: int = Field(default=50, ge=0)
    retrieval_limits: RetrievalLimits = Field(default_factory=RetrievalLimits)
    # Legacy flat limits (v1.0.1 compatibility)
    max_notes: int | None = Field(default=None, exclude=True)
    max_tasks: int | None = Field(default=None, exclude=True)
    max_events: int | None = Field(default=None, exclude=True)
    max_graph_nodes: int | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _apply_legacy_limits(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        legacy = {}
        for key in ("max_notes", "max_tasks", "max_events", "max_graph_nodes"):
            if key in values and values[key] is not None:
                legacy[key] = values[key]
        if legacy:
            retrieval = values.get("retrieval_limits", {})
            if isinstance(retrieval, RetrievalLimits):
                retrieval = retrieval.model_dump()
            retrieval.update(legacy)
            values["retrieval_limits"] = retrieval
        return values

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_limits(cls, values: Any) -> Any:
        """Support legacy max_* fields by mapping into retrieval_limits."""
        if not isinstance(values, dict):
            return values

        legacy_keys = ("max_notes", "max_tasks", "max_events", "max_graph_nodes")
        if not any(key in values for key in legacy_keys):
            return values

        data = dict(values)
        retrieval_limits = dict(data.get("retrieval_limits") or {})

        for key in legacy_keys:
            if key in data and key not in retrieval_limits:
                retrieval_limits[key] = data[key]
            data.pop(key, None)

        data["retrieval_limits"] = retrieval_limits
        return data


class ContextItem(KernelModel):
    """A single item of context with its reference and content."""

    ref: ContextRef
    excerpt: str  # Extracted text snippet
    summary: str | None = None  # Optional AI summary
    relevance_score: float = 0.0
    included_reason: str = ""  # Why this was included


class QueryRecord(KernelModel):
    """Record of a query run during context retrieval."""

    source: str  # "vector", "graph", "document", "keyword"
    query: str
    results_count: int
    duration_ms: int


class RetrievalReport(KernelModel):
    """Report on context retrieval for debugging and tracing.

    v1.0.2: Extended with retrieval plan reference and quality report.
    """

    queries_run: list[QueryRecord] = Field(default_factory=list)
    filters_applied: list[str] = Field(default_factory=list)
    items_considered: int = 0
    items_selected: int = 0
    selection_strategy: str = "relevance_ranked"
    # v1.0.2: Retrieval plan tracking
    retrieval_plan_id: str | None = Field(
        default=None,
        description="ID of the RetrievalPlan used (v1.0.2)",
    )
    # Note: Using Any to avoid circular import; actual type is RetrievalPlan
    retrieval_plan: Any | None = Field(
        default=None,
        description="Full RetrievalPlan if included (v1.0.2)",
    )
    # Note: Using Any to avoid circular import; actual type is RetrievalQualityReport
    quality: Any | None = Field(
        default=None,
        description="Quality report from coverage gates (v1.0.2)",
    )


class GraphSlice(KernelModel):
    """A slice of the context graph included in the packet."""

    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class ContextPacket(VersionedModel):
    """The bounded input an agent receives.

    Deterministically assembled by the ContextAssembler.
    Contains all relevant context, budget info, and retrieval report.

    Inherits from VersionedModel for schema version tracking.

    v1.0.2: Added retrieval_mode and context_packs fields.
    """

    packet_id: str = Field(default_factory=generate_ulid)
    intent: str
    project_id: str | None = None
    generated_at: datetime = Field(default_factory=utc_now)
    budget: ContextBudget = Field(default_factory=ContextBudget)
    items: list[ContextItem] = Field(default_factory=list)
    graph_slice: GraphSlice | None = None
    retrieval_report: RetrievalReport = Field(default_factory=RetrievalReport)
    # v1.0.2: Retrieval mode and context packs
    retrieval_mode: Literal["baseline", "instructed", "iterative"] = Field(
        default="baseline",
        description="How context was retrieved (v1.0.2)",
    )
    context_packs: list[str] = Field(
        default_factory=list,
        description="IDs of context packs included (v1.0.2)",
    )


class ContextPolicy(KernelModel):
    """Policy for context retrieval and handling.

    Part of AgentProfile configuration.
    """

    max_tokens: int = 4000
    max_notes: int = 10
    max_tasks: int = 20
    max_events: int = 5
    must_cite: bool = True
    allowed_scopes: list[str] = Field(default_factory=list)  # Project IDs, empty = all
    redaction_rules: list[str] = Field(default_factory=list)
