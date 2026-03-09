"""Knowledge and Event Clock schemas for the Context Graph.

Typed property models for graph nodes in the context graph.
These are serialized via .model_dump() into GraphNode.properties — no new DB tables.

Memory hierarchy mapping:
- Working Memory: ContextPacket (ephemeral, per-run)
- Episodic Memory: TRAJECTORY + DECISION_EVENT nodes (traces decomposed into graph)
- Semantic Memory: Knowledge nodes (CONCEPT, SYSTEM, INSIGHT, etc.)
- Procedural Memory: Playbook + Skill (already exist)

v1.0.6 additions:
- FreshnessScore for time-decay relevance tracking
- KnowledgeNodeProperties base for all knowledge nodes
- TrajectoryProperties for event clock records
- DecisionEventProperties for individual decisions within trajectories
- DecompositionResult for trace decomposition output
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from agent_kernel.core.schemas.base import KernelModel, utc_now


class KnowledgeSource(str, Enum):
    """How a knowledge node was created."""

    MANUAL = "manual"          # User-created
    TRACE = "trace"            # Extracted from a DecisionTrace
    DOC = "doc"                # Extracted from a document
    IMPORT = "import"          # Imported from external source
    COMPACTION = "compaction"  # Created by compacting other nodes
    INFERENCE = "inference"    # Inferred from patterns


class KnowledgeTier(str, Enum):
    """Retention tier for knowledge nodes."""

    HOT = "hot"    # Recently accessed, high relevance
    WARM = "warm"  # Not recently accessed but still relevant
    COLD = "cold"  # Old, low access, candidate for compaction/pruning


class FreshnessScore(KernelModel):
    """Time-decay tracking for knowledge node relevance.

    The relevance signal that prevents every graph node from having
    equal weight regardless of age, access frequency, or reinforcement.

    score = base_relevance * (1 - decay_rate) ^ days_since_last_touch
    """

    base_relevance: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Initial relevance (1.0 = highly relevant)",
    )
    last_accessed_at: datetime = Field(
        default_factory=utc_now,
        description="Last time this node was read/used in context assembly",
    )
    last_reinforced_at: datetime = Field(
        default_factory=utc_now,
        description="Last time this node was validated/confirmed/updated",
    )
    access_count: int = Field(
        default=0,
        ge=0,
        description="Total times accessed for context",
    )
    decay_rate: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="Per-day decay rate (0=no decay, 1=instant decay)",
    )
    pinned: bool = Field(
        default=False,
        description="Exempt from decay — always fresh",
    )

    def effective_relevance(self, now: datetime | None = None) -> float:
        """Calculate current relevance with time decay.

        score = base * (1 - decay_rate) ^ days_since_last_touch

        Uses the most recent of last_accessed_at and last_reinforced_at
        as the "last touch" time.
        """
        if self.pinned:
            return self.base_relevance

        if now is None:
            now = utc_now()

        last_touch = max(self.last_accessed_at, self.last_reinforced_at)
        days_elapsed = (now - last_touch).total_seconds() / 86400.0

        if days_elapsed <= 0:
            return self.base_relevance

        return self.base_relevance * ((1.0 - self.decay_rate) ** days_elapsed)


class KnowledgeNodeProperties(KernelModel):
    """Base properties for all knowledge nodes in the context graph.

    Serialized into GraphNode.properties via .model_dump().
    """

    title: str = Field(description="Human-readable title")
    description: str | None = Field(
        default=None,
        description="Longer description of this knowledge",
    )
    knowledge_source: KnowledgeSource = Field(
        description="How this knowledge was created",
    )
    source_refs: list[str] = Field(
        default_factory=list,
        description="IDs of source entities (trace_ids, doc_ids, etc.)",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence (1.0 for manual, lower for extracted)",
    )
    freshness: FreshnessScore = Field(
        default_factory=FreshnessScore,
        description="Freshness/relevance tracking",
    )
    tier: KnowledgeTier = Field(
        default=KnowledgeTier.HOT,
        description="Current retention tier",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Machine tags (not human tags)",
    )
    created_by: str | None = Field(
        default=None,
        description="Agent profile ID or 'user' who created this",
    )
    superseded_by: str | None = Field(
        default=None,
        description="Node ID that supersedes this one (for compaction)",
    )


class DomainProperties(KnowledgeNodeProperties):
    """Properties for DOMAIN nodes — business/technical domains."""

    domain_scope: str | None = Field(
        default=None,
        description="Scope of this domain (e.g., 'engineering', 'sales')",
    )


class SystemProperties(KnowledgeNodeProperties):
    """Properties for SYSTEM nodes — technical or business systems."""

    system_type: str | None = Field(
        default=None,
        description="Type of system (e.g., 'database', 'api', 'service')",
    )
    url: str | None = Field(
        default=None,
        description="System URL or endpoint",
    )


class ConceptProperties(KnowledgeNodeProperties):
    """Properties for CONCEPT nodes — abstract concepts."""

    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative names for this concept",
    )


class InsightProperties(KnowledgeNodeProperties):
    """Properties for INSIGHT nodes — learned heuristics from traces."""

    insight_type: str | None = Field(
        default=None,
        description="Type of insight (e.g., 'optimization', 'warning', 'pattern')",
    )
    applicable_contexts: list[str] = Field(
        default_factory=list,
        description="Contexts where this insight applies (workflow IDs, etc.)",
    )


class PatternProperties(KnowledgeNodeProperties):
    """Properties for PATTERN nodes — recurring patterns across trajectories."""

    occurrence_count: int = Field(
        default=1,
        ge=1,
        description="How many times this pattern has been observed",
    )
    trajectory_ids: list[str] = Field(
        default_factory=list,
        description="Trajectory node IDs where this pattern was observed",
    )


class DataObjectProperties(KnowledgeNodeProperties):
    """Properties for DATA_OBJECT nodes — tables, endpoints, data entities."""

    object_type: str | None = Field(
        default=None,
        description="Type of data object (e.g., 'table', 'endpoint', 'schema')",
    )
    system_id: str | None = Field(
        default=None,
        description="Node ID of the parent system",
    )


class SummaryProperties(KnowledgeNodeProperties):
    """Properties for SUMMARY nodes — compacted summaries of other nodes."""

    summarized_node_ids: list[str] = Field(
        default_factory=list,
        description="Node IDs that were compacted into this summary",
    )
    original_count: int = Field(
        default=0,
        ge=0,
        description="Number of original nodes summarized",
    )


# --- Event Clock schemas (episodic memory) ---


class TrajectoryProperties(KernelModel):
    """Properties for TRAJECTORY nodes — an agent's walk through entity space.

    This is the event clock record. Each completed DecisionTrace produces
    one TRAJECTORY node that captures the walk through organizational state.
    """

    trace_id: str = Field(description="Link to full DecisionTrace in TraceStore")
    agent_profile_id: str = Field(description="Agent that performed this trajectory")
    intent: str = Field(description="What the agent was trying to do")
    workflow_id: str | None = Field(
        default=None,
        description="Workflow that triggered this trajectory",
    )
    outcome_status: str = Field(
        description="Outcome: completed|partial|failed",
    )
    outcome_summary: str | None = Field(
        default=None,
        description="Brief summary of what happened",
    )
    entities_touched: list[str] = Field(
        default_factory=list,
        description="Ordered list of entity node_ids touched during this trajectory",
    )
    capabilities_used: list[str] = Field(
        default_factory=list,
        description="Tool capabilities invoked during this trajectory",
    )
    step_count: int = Field(
        default=0,
        ge=0,
        description="Number of decision events in this trajectory",
    )
    duration_ms: int = Field(
        default=0,
        ge=0,
        description="Total duration of the trajectory in milliseconds",
    )
    reasoning_tier: int = Field(
        default=1,
        ge=0,
        description="Thinking tier used (0=routing, 1=standard, 2=deep, 3=deep+critic)",
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        description="When this trajectory was recorded",
    )


class DecisionEventProperties(KernelModel):
    """Properties for DECISION_EVENT nodes — individual decisions within a trajectory.

    Each tool call or key decision point in a trace becomes a DECISION_EVENT node,
    linked to its parent TRAJECTORY and to the entities it affected.
    """

    trace_id: str = Field(description="Link to parent DecisionTrace")
    step_order: int = Field(
        ge=0,
        description="Position in the trajectory sequence (0-indexed)",
    )
    action_type: str = Field(
        description="Type of action: tool_call|approval|plan_step",
    )
    capability_name: str | None = Field(
        default=None,
        description="Tool capability used (if action_type is tool_call)",
    )
    decision_rationale: str | None = Field(
        default=None,
        description="Why this action was taken (from plan reasoning)",
    )
    input_summary: str | None = Field(
        default=None,
        description="Summarized input to the action",
    )
    output_summary: str | None = Field(
        default=None,
        description="Summarized output from the action",
    )
    status: str = Field(
        description="Result: success|error|denied|skipped|timeout",
    )
    duration_ms: int = Field(
        default=0,
        ge=0,
        description="Duration of this individual action",
    )


# --- Decomposition Result ---


class DecompositionResult(KernelModel):
    """Result of decomposing a DecisionTrace into graph structure."""

    trajectory_node_id: str = Field(
        description="Node ID of the created TRAJECTORY node",
    )
    decision_event_ids: list[str] = Field(
        default_factory=list,
        description="Node IDs of created DECISION_EVENT nodes",
    )
    entities_linked: int = Field(
        default=0,
        ge=0,
        description="Number of entity edges created",
    )
    co_occurrence_edges_updated: int = Field(
        default=0,
        ge=0,
        description="Number of CO_OCCURS_WITH edges created or updated",
    )
    nodes_created: int = Field(
        default=0,
        ge=0,
        description="Total graph nodes created",
    )
    edges_created: int = Field(
        default=0,
        ge=0,
        description="Total graph edges created",
    )
