"""Graph ontology schemas - Node and Edge types for the knowledge graph.

This module defines the v1 graph ontology with:
- Node types: Note, Tag, Task, Project, Trace, CalendarEvent
- Edge types: Links between entities with confidence and validity tracking

Design decisions:
- Auto-generated edges should set confidence (human-authored may omit)
- Edges can have validity intervals for temporal relationships
- Provenance tracking for auto-extractions

v1.0.4 additions:
- Experience node types: Case, Evaluation, Lesson, Playbook
- Experience edge types: For linking traces to cases/evaluations/lessons
- Entity-based node types for universal entity model

v1.0.6 additions:
- Knowledge node types: Domain, System, Concept, Practice, Insight, Pattern,
  DataObject, Rule (semantic memory)
- Event clock node types: Trajectory, DecisionEvent, Observation, Summary
  (episodic memory)
- Trajectory edge types: touched, decided, observed, produced
- Knowledge edge types: domain_contains, system_integrates_with, etc.
- Growth management edge types: summary_of, supersedes
- Co-occurrence edge type for structural learning
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from agent_kernel.core.schemas.base import VersionedModel, utc_now


class NodeType(str, Enum):
    """Types of nodes in the knowledge graph."""

    # Core types
    NOTE = "note"
    TAG = "tag"
    TASK = "task"
    PROJECT = "project"
    TRACE = "trace"
    CALENDAR_EVENT = "calendar_event"
    PERSON = "person"

    # v1.0.4: Experience memory types
    CASE = "case"          # Experience case
    EVALUATION = "evaluation"  # Outcome evaluation
    LESSON = "lesson"      # Lesson learned
    PLAYBOOK = "playbook"  # Behavioral pattern
    SKILL = "skill"        # Portable procedural guidance

    # v1.0.4: Entity-based types (for external sources)
    MESSAGE = "message"    # Slack/chat message
    THREAD = "thread"      # Conversation thread
    EMAIL = "email"        # Email message
    TICKET = "ticket"      # Issue/ticket
    PULL_REQUEST = "pull_request"  # Code review
    CAPABILITY = "capability"  # Tool/capability

    # v1.0.5: Task management types
    LABEL = "label"        # Semantic label for tasks
    SECTION = "section"    # Section within a project

    # v1.0.7: File ingestion
    FILE = "file"              # Non-text file (pointer-only storage)

    # v1.0.6: Business knowledge (semantic memory)
    DOMAIN = "domain"              # Business domain
    SYSTEM = "system"              # Technical/business system
    CONCEPT = "concept"            # Abstract concept
    PRACTICE = "practice"          # Business practice/procedure
    INSIGHT = "insight"            # Learned heuristic
    PATTERN = "pattern"            # Recurring pattern
    DATA_OBJECT = "data_object"    # Table, endpoint, data entity
    RULE = "rule"                  # Business rule or constraint

    # v1.0.6: Event clock (episodic memory)
    TRAJECTORY = "trajectory"          # Agent walk through entity space
    DECISION_EVENT = "decision_event"  # A decision made during a trace
    OBSERVATION = "observation"        # Something observed/discovered
    SUMMARY = "summary"                # Compacted summary node


class EdgeType(str, Enum):
    """Types of edges in the knowledge graph."""

    # Note relationships
    NOTE_LINKS_TO_NOTE = "note_links_to_note"
    NOTE_TAGGED_WITH_TAG = "note_tagged_with_tag"
    NOTE_HAS_TASK = "note_has_task"
    NOTE_MENTIONS_PERSON = "note_mentions_person"

    # Task relationships
    TASK_BELONGS_TO_PROJECT = "task_belongs_to_project"
    TASK_BLOCKED_BY_TASK = "task_blocked_by_task"
    TASK_ASSIGNED_TO_PERSON = "task_assigned_to_person"

    # Trace relationships (for provenance)
    TRACE_USED_CONTEXT = "trace_used_context"
    TRACE_PRODUCED_ARTIFACT = "trace_produced_artifact"

    # Calendar relationships
    CALENDAR_EVENT_RELATED_TO_NOTE = "calendar_event_related_to_note"
    CALENDAR_EVENT_RELATED_TO_TASK = "calendar_event_related_to_task"

    # Project relationships
    PROJECT_CONTAINS_NOTE = "project_contains_note"

    # v1.0.4: Experience memory relationships
    TRACE_HAS_CASE = "trace_has_case"              # Trace → Case
    TRACE_HAS_EVALUATION = "trace_has_evaluation"  # Trace → Evaluation
    CASE_YIELDED_LESSON = "case_yielded_lesson"    # Case → Lesson
    LESSON_APPLIES_TO_CAPABILITY = "lesson_applies_to_capability"  # Lesson → Capability
    LESSON_APPLIES_TO_WORKFLOW = "lesson_applies_to_workflow"      # Lesson → Workflow
    LESSON_APPLIES_TO_ENTITY_TYPE = "lesson_applies_to_entity_type"  # Lesson → EntityType
    PLAYBOOK_DERIVED_FROM_LESSON = "playbook_derived_from_lesson"  # Playbook → Lesson
    PLAYBOOK_APPLIES_TO_WORKFLOW = "playbook_applies_to_workflow"  # Playbook → Workflow

    # v1.0.4: Cross-entity relationships
    ENTITY_RELATED_TO_ENTITY = "entity_related_to"  # Generic entity relationship
    ENTITY_MENTIONS_ENTITY = "entity_mentions"      # Entity mentions another entity

    # v1.0.5: Task management relationships
    TASK_HAS_LABEL = "task_has_label"              # Task → Label
    TASK_IN_SECTION = "task_in_section"            # Task → Section
    TASK_SUBTASK_OF = "task_subtask_of"            # Task → Parent Task
    TASK_CREATED_FROM = "task_created_from"        # Task → Source entity (note, email)
    PROJECT_HAS_SECTION = "project_has_section"    # Project → Section
    TASK_SYNCED_TO = "task_synced_to"              # Task -> External (linear, etc.)

    # v1.0.6: Trajectory edges (event clock)
    TRAJECTORY_TOUCHED = "trajectory_touched"            # Trajectory → entity (step_order)
    TRAJECTORY_DECIDED = "trajectory_decided"            # Trajectory → DecisionEvent
    TRAJECTORY_OBSERVED = "trajectory_observed"          # Trajectory → Observation
    TRAJECTORY_PRODUCED = "trajectory_produced"          # Trajectory → outcome artifact
    DECISION_ABOUT = "decision_about"                    # DecisionEvent → entity affected
    PRECEDED_BY = "preceded_by"                          # DecisionEvent → prior (causal)
    SIMILAR_TO = "similar_to"                            # Trajectory → Trajectory

    # v1.0.6: Knowledge edges (semantic memory)
    DOMAIN_CONTAINS = "domain_contains"                  # Domain → System/Concept
    SYSTEM_INTEGRATES_WITH = "system_integrates_with"    # System ↔ System
    SYSTEM_HAS_DATA_OBJECT = "system_has_data_object"    # System → DataObject
    CONCEPT_RELATED_TO = "concept_related_to"            # Concept ↔ Concept
    INSIGHT_ABOUT = "insight_about"                      # Insight → any entity
    INSIGHT_DERIVED_FROM = "insight_derived_from"         # Insight → Trajectory/Trace
    PATTERN_OBSERVED_IN = "pattern_observed_in"          # Pattern → Trajectory
    PRACTICE_USES = "practice_uses"                      # Practice → System/Tool
    RULE_CONSTRAINS = "rule_constrains"                  # Rule → entity

    # v1.0.6: Growth management edges
    SUMMARY_OF = "summary_of"                            # Summary → original nodes
    SUPERSEDES = "supersedes"                            # Newer → older version

    # v1.0.6: Co-occurrence (structural learning)
    CO_OCCURS_WITH = "co_occurs_with"                    # Entity ↔ Entity (weighted)

    # v1.1.4: Context curation (self-learning staircase)
    EFFECTIVE_FOR = "effective_for"                       # Knowledge → Agent (citation_rate, outcome_boost)


class GraphNode(VersionedModel):
    """A node in the knowledge graph.

    Nodes represent entities like notes, tasks, tags, projects, etc.
    Properties can store arbitrary metadata.
    """

    node_id: str = Field(description="Unique identifier for this node")
    node_type: NodeType = Field(description="Type of this node")
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary properties for this node",
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        description="When this node was created",
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        description="When this node was last updated",
    )

    # Optional display properties
    label: str | None = Field(
        default=None,
        description="Human-readable label for display",
    )
    uri: str | None = Field(
        default=None,
        description="External URI reference (e.g., file path, URL)",
    )


class GraphEdge(VersionedModel):
    """An edge (relationship) in the knowledge graph.

    Edges connect two nodes and can carry:
    - Properties for relationship metadata
    - Confidence scores for auto-extracted edges
    - Validity intervals for temporal relationships
    - Provenance for tracking how the edge was created
    """

    edge_id: str = Field(description="Unique identifier for this edge")
    edge_type: EdgeType = Field(description="Type of this relationship")
    source_id: str = Field(description="Source node ID")
    target_id: str = Field(description="Target node ID")
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary properties for this edge",
    )

    # Confidence for auto-extracted edges
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence score for auto-extracted edges (human edges may omit)",
    )

    # Temporal validity
    valid_from: datetime | None = Field(
        default=None,
        description="When this relationship became valid",
    )
    valid_to: datetime | None = Field(
        default=None,
        description="When this relationship stopped being valid",
    )

    # Provenance
    extracted_by: str | None = Field(
        default=None,
        description="What extracted this edge (e.g., 'vault_indexer')",
    )
    source_ref: str | None = Field(
        default=None,
        description="Reference to the source of this edge (e.g., trace_id)",
    )

    created_at: datetime = Field(
        default_factory=utc_now,
        description="When this edge was created",
    )

    @property
    def is_auto_extracted(self) -> bool:
        """Check if this edge was auto-extracted (has confidence score)."""
        return self.confidence is not None

    @property
    def is_valid(self) -> bool:
        """Check if this edge is currently valid."""
        if self.valid_to is None:
            return True
        return utc_now() <= self.valid_to


class TypedGraphSlice(VersionedModel):
    """A typed slice of the knowledge graph.

    Use this when including graph context in a ContextPacket.
    Provides strongly-typed nodes and edges.
    """

    nodes: list[GraphNode] = Field(
        default_factory=list,
        description="Nodes in this slice",
    )
    edges: list[GraphEdge] = Field(
        default_factory=list,
        description="Edges in this slice",
    )

    @property
    def node_count(self) -> int:
        """Get number of nodes."""
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        """Get number of edges."""
        return len(self.edges)

    def get_node(self, node_id: str) -> GraphNode | None:
        """Get a node by ID."""
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

    def get_edges_from(self, source_id: str) -> list[GraphEdge]:
        """Get all edges from a source node."""
        return [e for e in self.edges if e.source_id == source_id]

    def get_edges_to(self, target_id: str) -> list[GraphEdge]:
        """Get all edges to a target node."""
        return [e for e in self.edges if e.target_id == target_id]
