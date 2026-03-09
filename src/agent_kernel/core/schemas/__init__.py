"""Core Pydantic schemas - the contracts for all data flows.

This module exports all schema classes used throughout the Agent Kernel.
These schemas define the strict contracts between components.

v1.0.1 additions:
- VersionedModel, SCHEMA_VERSION for schema evolution
- LLMRequest, LLMResponse, LLMCallRecord for LLM call tracing
- ApprovalRequest, WorkflowRun, WorkflowRunStatus for approval persistence
- GraphNode, GraphEdge, NodeType, EdgeType for graph ontology
- TypedGraphSlice for strongly-typed graph context

v1.0.2 additions:
- ContextPack, ContextPackSelector, ContextPackScope for context packs
- SourceDescriptor, FieldDescriptor, SourceConstraint for source schemas
- RetrievalPlan, RetrievalDirective, RetrievalFilter for retrieval planning
- CoverageGateResult, RetrievalQualityReport, RetrievalScope for gates
- RULE and SPEC RefTypes for context pack references

v1.0.3 additions:
- ThinkingConfig, ThinkingTierConfig for thinking policy
- RetrievalConfig, VerificationConfig, EscalationConfig for composable features
- QualityGatesConfig for gate configuration
- Predefined configs: STANDARD_THINKING, DEEP_THINKING, ADAPTIVE_THINKING

v1.0.4 additions:
- EntityRef, EntityView, EntityViewType for universal entity model
- KnownSources, KnownEntityTypes for well-known identifiers
- Entity fields in ContextRef for backwards-compatible entity support
- Experience memory schemas (OutcomeEvaluation, ExperienceCase, LessonLearned)
- Playbook schema for behavioral patterns
- RetentionPolicy for growth management

v1.0.5 additions:
- TaskEntity, ProjectEntity, LabelEntity for canonical task model
- TaskLink, ContextLink for cross-system mapping
- TaskStatus, TaskPriority, TaskScope for task enums
- RecurrenceRule for vendor-agnostic recurrence
- TaskQuery, TaskPatch for task operations
- ReminderPolicy for reminder configuration

v1.1.2 additions:
- SkillOrigin, SkillManifest, SkillResourceRef, SkillLoadResult
- RefType.SKILL for skill references
"""

# Base utilities and versioning
# Agent schemas
from agent_kernel.core.schemas.agent import (
    AgentProfile,
    ApprovalPolicy,
    ModelConfig,
    PromptConfig,
)
from agent_kernel.core.schemas.base import (
    SCHEMA_VERSION,
    IdentifiedModel,
    KernelModel,
    TimestampedModel,
    VersionedModel,
    get_kernel_version,
    to_json_dict,
    utc_now,
)

# Capability schemas
from agent_kernel.core.schemas.capability import (
    CapabilityDef,
    CapabilitySpec,
    normalize_side_effect_level,
    RateLimit,
    RedactionPolicy,
)

# Context schemas
from agent_kernel.core.schemas.context import (
    ContextBudget,
    ContextItem,
    ContextPacket,
    ContextPolicy,
    ContextRef,
    GraphSlice,
    QueryRecord,
    RefType,
    RetrievalLimits,
    RetrievalReport,
)

# Context Pack schemas (v1.0.2)
from agent_kernel.core.schemas.context_pack import (
    ContextPack,
    ContextPackScope,
    ContextPackSelector,
)

# Entity schemas (v1.0.4)
from agent_kernel.core.schemas.entity import (
    EntityRef,
    EntityView,
    EntityViewType,
    KnownEntityTypes,
    KnownSources,
)

# Experience Memory schemas (v1.0.4)
from agent_kernel.core.schemas.experience import (
    ExperienceCase,
    FailureCategory,
    LessonLearned,
    LessonScope,
    OutcomeEvaluation,
    OutcomeLabel,
    Playbook,
    PlaybookSelector,
)

# Skill schemas (v1.1.x)
from agent_kernel.core.schemas.skill import (
    SkillLoadResult,
    SkillManifest,
    SkillOrigin,
    SkillResourceRef,
)

# Knowledge schemas (v1.0.6)
from agent_kernel.core.schemas.knowledge import (
    ConceptProperties,
    DataObjectProperties,
    DecisionEventProperties,
    DecompositionResult,
    DomainProperties,
    FreshnessScore,
    InsightProperties,
    KnowledgeNodeProperties,
    KnowledgeSource,
    KnowledgeTier,
    PatternProperties,
    SummaryProperties,
    SystemProperties,
    TrajectoryProperties,
)

# Enrichment Config schemas (v1.0.5)
from agent_kernel.core.schemas.enrichment_config import (
    DEFAULT_ENRICHMENT_THRESHOLDS,
    DEFAULT_OBSIDIAN_CONFIG,
    DEFAULT_SUMMARIZATION_CONFIG,
    EnrichmentThresholds,
    SourceEnrichmentConfig,
    SummarizationConfig,
)

# Retention Policy schemas (v1.0.4)
from agent_kernel.core.schemas.retention import (
    DEFAULT_RETENTION_POLICY,
    DocumentCachePolicy,
    EventLogRetentionPolicy,
    GraphRetentionPolicy,
    KnowledgeRetentionPolicy,
    LLMCallRetentionPolicy,
    RetentionPolicy,
    ToolCallRetentionPolicy,
    TraceRetentionPolicy,
    TrajectoryRetentionPolicy,
    VectorRetentionPolicy,
)

# Task schemas (v1.0.5)
from agent_kernel.core.schemas.task import (
    ContextLink,
    LabelEntity,
    ProjectEntity,
    RecurrenceRule,
    ReminderPolicy,
    TaskEntity,
    TaskLink,
    TaskPatch,
    TaskPriority,
    TaskQuery,
    TaskScope,
    TaskStatus,
)

# Note schemas (v1.0.8)
from agent_kernel.core.schemas.note import (
    AutoMetadata,
    FOLDER_STATE_MAP,
    NoteLifecycleState,
    NoteMetadata,
    NoteType,
    ReservedBlock,
    ReservedBlockType,
    infer_state_from_path,
    infer_type_from_path,
)

# Graph ontology schemas (v1.0.1)
from agent_kernel.core.schemas.graph import (
    EdgeType,
    GraphEdge,
    GraphNode,
    NodeType,
    TypedGraphSlice,
)

# LLM call schemas (v1.0.1)
from agent_kernel.core.schemas.llm import (
    LLMCallRecord,
    LLMRequest,
    LLMResponse,
    ReasoningEffort,
)

# Plan schemas
from agent_kernel.core.schemas.plan import (
    ActionRequest,
    Plan,
    PlanValidation,
    RiskAssessment,
    RiskLevel,
    SideEffect,
)

# Retrieval Plan schemas (v1.0.2)
from agent_kernel.core.schemas.retrieval import (
    CoverageGateResult,
    RetrievalDirective,
    RetrievalFilter,
    RetrievalPlan,
    RetrievalQualityReport,
    RetrievalScope,
)

# Source Descriptor schemas (v1.0.2)
from agent_kernel.core.schemas.source_descriptor import (
    FieldDescriptor,
    SourceConstraint,
    SourceDescriptor,
)

# Thinking Policy schemas (v1.0.3)
from agent_kernel.core.schemas.thinking import (
    ADAPTIVE_THINKING,
    DEEP_THINKING,
    STANDARD_THINKING,
    EscalationConfig,
    QualityGatesConfig,
    RetrievalConfig,
    ThinkingConfig,
    ThinkingTierConfig,
    VerificationConfig,
)

# Trace schemas
from agent_kernel.core.schemas.trace import (
    ApprovalRecord,
    CallStatus,
    CostRecord,
    DecisionTrace,
    ErrorRecord,
    Outcome,
    OutcomeStatus,
    PromptPartRef,
    Provenance,
    ReasoningMetadata,
    ToolCallRecord,
)

# Workflow schemas (v1.0.1)
from agent_kernel.core.schemas.workflow import (
    ApprovalRequest,
    ApprovalRequestStatus,
    WorkflowRun,
    WorkflowRunStatus,
)

__all__ = [
    # Base and versioning (v1.0.1)
    "KernelModel",
    "VersionedModel",
    "TimestampedModel",
    "IdentifiedModel",
    "SCHEMA_VERSION",
    "get_kernel_version",
    "utc_now",
    "to_json_dict",
    # Context
    "RefType",
    "ContextRef",
    "RetrievalLimits",
    "ContextBudget",
    "ContextItem",
    "QueryRecord",
    "RetrievalReport",
    "GraphSlice",
    "ContextPacket",
    "ContextPolicy",
    # Graph ontology (v1.0.1)
    "NodeType",
    "EdgeType",
    "GraphNode",
    "GraphEdge",
    "TypedGraphSlice",
    # LLM call tracing (v1.0.1)
    "ReasoningEffort",
    "LLMRequest",
    "LLMResponse",
    "LLMCallRecord",
    # Plan
    "SideEffect",
    "RiskLevel",
    "ActionRequest",
    "RiskAssessment",
    "PlanValidation",
    "Plan",
    # Trace
    "CallStatus",
    "ErrorRecord",
    "CostRecord",
    "ToolCallRecord",
    "ApprovalRecord",
    "OutcomeStatus",
    "Outcome",
    "PromptPartRef",
    "Provenance",
    "ReasoningMetadata",
    "DecisionTrace",
    # Workflow (v1.0.1)
    "WorkflowRunStatus",
    "WorkflowRun",
    "ApprovalRequestStatus",
    "ApprovalRequest",
    # Agent
    "ModelConfig",
    "ApprovalPolicy",
    "AgentProfile",
    "PromptConfig",
    # Capability
    "RateLimit",
    "RedactionPolicy",
    "CapabilityDef",
    "CapabilitySpec",
    "normalize_side_effect_level",
    # Context Pack (v1.0.2)
    "ContextPackSelector",
    "ContextPack",
    "ContextPackScope",
    # Source Descriptor (v1.0.2)
    "FieldDescriptor",
    "SourceConstraint",
    "SourceDescriptor",
    # Retrieval Plan (v1.0.2)
    "RetrievalFilter",
    "RetrievalDirective",
    "RetrievalPlan",
    "CoverageGateResult",
    "RetrievalQualityReport",
    "RetrievalScope",
    # Thinking Policy (v1.0.3)
    "ThinkingConfig",
    "ThinkingTierConfig",
    "RetrievalConfig",
    "VerificationConfig",
    "EscalationConfig",
    "QualityGatesConfig",
    "STANDARD_THINKING",
    "DEEP_THINKING",
    "ADAPTIVE_THINKING",
    # Entity Model (v1.0.4)
    "EntityRef",
    "EntityView",
    "EntityViewType",
    "KnownSources",
    "KnownEntityTypes",
    # Experience Memory (v1.0.4)
    "OutcomeLabel",
    "FailureCategory",
    "OutcomeEvaluation",
    "ExperienceCase",
    "LessonScope",
    "LessonLearned",
    "PlaybookSelector",
    "Playbook",
    # Skills (v1.1.x)
    "SkillOrigin",
    "SkillManifest",
    "SkillResourceRef",
    "SkillLoadResult",
    # Retention Policy (v1.0.4)
    "RetentionPolicy",
    "TraceRetentionPolicy",
    "LLMCallRetentionPolicy",
    "ToolCallRetentionPolicy",
    "VectorRetentionPolicy",
    "GraphRetentionPolicy",
    "DocumentCachePolicy",
    "EventLogRetentionPolicy",
    "KnowledgeRetentionPolicy",
    "TrajectoryRetentionPolicy",
    "DEFAULT_RETENTION_POLICY",
    # Knowledge & Context Graph (v1.0.6)
    "KnowledgeSource",
    "KnowledgeTier",
    "FreshnessScore",
    "KnowledgeNodeProperties",
    "DomainProperties",
    "SystemProperties",
    "ConceptProperties",
    "InsightProperties",
    "PatternProperties",
    "DataObjectProperties",
    "SummaryProperties",
    "TrajectoryProperties",
    "DecisionEventProperties",
    "DecompositionResult",
    # Enrichment Config (v1.0.5)
    "EnrichmentThresholds",
    "SourceEnrichmentConfig",
    "DEFAULT_ENRICHMENT_THRESHOLDS",
    "DEFAULT_OBSIDIAN_CONFIG",
    # Task Model (v1.0.5)
    "TaskStatus",
    "TaskPriority",
    "TaskScope",
    "RecurrenceRule",
    "TaskEntity",
    "ProjectEntity",
    "LabelEntity",
    "ReminderPolicy",
    "TaskLink",
    "ContextLink",
    "TaskQuery",
    "TaskPatch",
    # Note Model (v1.0.8)
    "NoteLifecycleState",
    "NoteType",
    "NoteMetadata",
    "AutoMetadata",
    "ReservedBlock",
    "ReservedBlockType",
    "FOLDER_STATE_MAP",
    "infer_state_from_path",
    "infer_type_from_path",
    # Backwards compatibility aliases
    "SummarizationConfig",
    "DEFAULT_SUMMARIZATION_CONFIG",
]
