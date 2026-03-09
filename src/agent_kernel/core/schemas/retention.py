"""Retention Policy schemas (v1.0.4, v1.0.6).

Defines policy-driven retention tiers for growth management:
- Traces: hot → warm → cold compaction
- Vectors: summary vs chunk retention
- Graph: human vs auto edge pruning
- Documents: cache TTL and size limits
- Knowledge: HOT → WARM → COLD tiering + compaction (v1.0.6)
- Trajectories: full → summary compaction (v1.0.6)

References:
- Design Patch v1.0.4: Universal Context System
- Design Patch v1.0.6: Context Graph
"""

from __future__ import annotations

from pydantic import Field

from agent_kernel.core.schemas.base import KernelModel


class TraceRetentionPolicy(KernelModel):
    """Retention policy for decision traces."""

    hot_days: int = Field(
        default=14,
        description="Days to keep full traces including tool I/O",
    )
    warm_days: int = Field(
        default=90,
        description="Days to keep compacted trace summaries + key metadata",
    )
    cold_days: int = Field(
        default=365,
        description="Days to keep case + evaluation + lessons only",
    )
    keep_failed_longer: bool = Field(
        default=True,
        description="Keep failed traces longer for debugging",
    )
    failed_multiplier: float = Field(
        default=2.0,
        description="Multiplier for retention of failed traces",
    )


class LLMCallRetentionPolicy(KernelModel):
    """Retention policy for LLM call logs."""

    keep_raw_messages_days: int = Field(
        default=7,
        description="Days to keep raw request/response messages",
    )
    keep_hashes_days: int = Field(
        default=365,
        description="Days to keep content hashes for reproducibility",
    )


class ToolCallRetentionPolicy(KernelModel):
    """Retention policy for tool call records."""

    keep_full_output_days: int = Field(
        default=7,
        description="Days to keep full tool outputs",
    )
    keep_redacted_preview_days: int = Field(
        default=90,
        description="Days to keep redacted output previews",
    )
    compress_large_outputs: bool = Field(
        default=True,
        description="Compress outputs larger than threshold",
    )
    large_output_threshold_kb: int = Field(
        default=256,
        description="Threshold for large output compression (KB)",
    )


class VectorRetentionPolicy(KernelModel):
    """Retention policy for vector embeddings."""

    keep_summary_embeddings_days: int = Field(
        default=3650,
        description="Days to keep summary embeddings (~10 years)",
    )
    keep_chunk_embeddings_days: int = Field(
        default=180,
        description="Days to keep chunk embeddings",
    )
    drop_chunks_when_entity_state: list[str] = Field(
        default_factory=lambda: ["archived"],
        description="Drop chunks when entity reaches these states",
    )
    max_total_vectors: int = Field(
        default=500000,
        description="Maximum total vectors before pruning",
    )


class GraphRetentionPolicy(KernelModel):
    """Retention policy for graph edges."""

    keep_human_edges_days: int = Field(
        default=3650,
        description="Days to keep human-created edges (~10 years)",
    )
    prune_auto_edges_below_confidence: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        description="Prune auto edges below this confidence",
    )
    prune_auto_edges_older_than_days: int = Field(
        default=365,
        description="Prune auto edges older than this",
    )


class DocumentCachePolicy(KernelModel):
    """Retention policy for document cache."""

    requires_live_fetch_ttl_minutes: int = Field(
        default=60,
        description="TTL for sources requiring live fetch",
    )
    max_cache_size_mb: int = Field(
        default=2048,
        description="Maximum document cache size (MB)",
    )


class EventLogRetentionPolicy(KernelModel):
    """Retention policy for event log."""

    keep_event_log_days: int = Field(
        default=3650,
        description="Days to keep event log entries (~10 years)",
    )


class KnowledgeRetentionPolicy(KernelModel):
    """Retention policy for context graph knowledge nodes (v1.0.6)."""

    hot_days: int = Field(
        default=90,
        description="Days a node stays HOT after last access",
    )
    warm_days: int = Field(
        default=365,
        description="Days WARM before transition to COLD",
    )
    cold_days: int = Field(
        default=1825,
        description="Days COLD before eligible for pruning (~5 years)",
    )
    compact_cold_after_days: int = Field(
        default=180,
        description="Compact COLD cluster nodes after this many days",
    )
    prune_low_confidence_below: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Prune COLD nodes with confidence below this threshold",
    )
    prune_low_relevance_below: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Prune COLD nodes with effective relevance below this",
    )
    pinned_exempt: bool = Field(
        default=True,
        description="Pinned nodes exempt from decay and pruning",
    )
    max_knowledge_nodes: int = Field(
        default=100_000,
        description="Maximum total knowledge nodes before forced pruning",
    )


class TrajectoryRetentionPolicy(KernelModel):
    """Retention policy for context graph trajectory nodes (v1.0.6)."""

    keep_full_trajectory_days: int = Field(
        default=90,
        description="Days to keep full trajectory with all decision events",
    )
    keep_summary_trajectory_days: int = Field(
        default=730,
        description="Days to keep compacted trajectory summary (~2 years)",
    )
    compact_after_days: int = Field(
        default=90,
        description="Compact trajectory (remove events, keep summary) after this",
    )


class RetentionPolicy(KernelModel):
    """Complete retention policy configuration.

    Defines how long different types of data are retained
    and when compaction/pruning occurs.
    """

    traces: TraceRetentionPolicy = Field(
        default_factory=TraceRetentionPolicy,
        description="Trace retention settings",
    )
    llm_calls: LLMCallRetentionPolicy = Field(
        default_factory=LLMCallRetentionPolicy,
        description="LLM call retention settings",
    )
    tool_calls: ToolCallRetentionPolicy = Field(
        default_factory=ToolCallRetentionPolicy,
        description="Tool call retention settings",
    )
    vectors: VectorRetentionPolicy = Field(
        default_factory=VectorRetentionPolicy,
        description="Vector retention settings",
    )
    graph: GraphRetentionPolicy = Field(
        default_factory=GraphRetentionPolicy,
        description="Graph retention settings",
    )
    document_cache: DocumentCachePolicy = Field(
        default_factory=DocumentCachePolicy,
        description="Document cache settings",
    )
    events: EventLogRetentionPolicy = Field(
        default_factory=EventLogRetentionPolicy,
        description="Event log retention settings",
    )
    knowledge: KnowledgeRetentionPolicy = Field(
        default_factory=KnowledgeRetentionPolicy,
        description="Knowledge node retention settings (v1.0.6)",
    )
    trajectories: TrajectoryRetentionPolicy = Field(
        default_factory=TrajectoryRetentionPolicy,
        description="Trajectory retention settings (v1.0.6)",
    )


# Default retention policy
DEFAULT_RETENTION_POLICY = RetentionPolicy()
