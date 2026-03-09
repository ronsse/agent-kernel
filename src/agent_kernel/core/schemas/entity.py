"""Universal Entity Model schemas (v1.0.4).

Everything becomes an Entity with {source_id, entity_type, entity_id}.
This generalizes indexing, retrieval, embeddings, and graph relationships
beyond "notes" to any source (Obsidian, Slack, Outlook, GitHub, etc.).

References:
- Design Patch v1.0.4: Universal Context System
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field

from agent_kernel.core.schemas.base import VersionedModel


class EntityViewType(str, Enum):
    """Types of views (representations) an entity can have.
    
    Each entity can have multiple views used for different retrieval purposes.
    """

    # Core views
    SUMMARY = "summary"              # Entity-level summary for ranking
    CHUNK = "chunk"                  # Chunk/passage for detailed retrieval
    TITLE = "title"                  # Title-only view
    METADATA = "metadata"            # Structured metadata view

    # Communication views
    THREAD_SUMMARY = "thread_summary"  # Summary of a conversation thread
    TRANSCRIPT = "transcript"          # Full transcript/content

    # Experience views
    DECISION_SUMMARY = "decision_summary"  # Summary of a decision trace
    LESSON = "lesson"                      # Actionable lesson learned
    PLAYBOOK = "playbook"                  # Behavioral pattern spec


class EntityRef(VersionedModel):
    """Universal reference to any entity across all sources.
    
    Entity identity is three-part:
    - source_id: where it originates (obsidian, slack, outlook, github, ...)
    - entity_type: what it is (note, message, thread, email, event, ticket, ...)
    - entity_id: stable identifier within that source
    
    Canonical rule: each source is canonical for its own objects.
    """

    source_id: str = Field(
        ...,
        description="Source system identifier (obsidian, slack, outlook, github, jira, etc.)",
    )
    entity_type: str = Field(
        ...,
        description="Entity type within the source (note, message, thread, email, event, etc.)",
    )
    entity_id: str = Field(
        ...,
        description="Stable identifier within the source",
    )

    # Optional fields for richer context
    uri: str | None = Field(
        default=None,
        description="Canonical URI/permalink/path if available",
    )
    canonical_id: str | None = Field(
        default=None,
        description="Kernel-owned global ID for cross-source resolution (ent_{ulid})",
    )
    canonical_hash: str | None = Field(
        default=None,
        description="Content hash for sources where we can hash content",
    )

    # Temporal context
    occurred_at: datetime | None = Field(
        default=None,
        description="When the entity occurred/was created in the source",
    )
    recorded_at: datetime | None = Field(
        default=None,
        description="When the entity was ingested into the kernel (UTC)",
    )

    # Extensible metadata
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific metadata",
    )

    def to_vector_id(self, view_type: EntityViewType, segment_id: str | None = None) -> str:
        """Generate vector ID for this entity's view.
        
        Format: {canonical_id or entity_id}:{view_type}:{segment_id?}
        """
        base_id = self.canonical_id or self.entity_id
        if segment_id:
            return f"{base_id}:{view_type.value}:{segment_id}"
        return f"{base_id}:{view_type.value}"

    def to_node_id(self) -> str:
        """Generate graph node ID for this entity.
        
        Format: {entity_type}:{entity_id}
        """
        return f"{self.entity_type}:{self.entity_id}"

    @classmethod
    def from_note(cls, note_id: str, path: str | None = None) -> EntityRef:
        """Create EntityRef from a note ID (backwards compatibility)."""
        return cls(
            source_id="obsidian",
            entity_type="note",
            entity_id=note_id,
            uri=path,
        )

    @classmethod
    def from_task(cls, task_id: str, source_note_id: str | None = None) -> EntityRef:
        """Create EntityRef from a task ID."""
        return cls(
            source_id="tasks",
            entity_type="task",
            entity_id=task_id,
            metadata={"source_note_id": source_note_id} if source_note_id else {},
        )

    @classmethod
    def from_calendar_event(cls, event_id: str, calendar_source: str = "outlook") -> EntityRef:
        """Create EntityRef from a calendar event."""
        return cls(
            source_id=calendar_source,
            entity_type="calendar_event",
            entity_id=event_id,
        )


class EntityView(VersionedModel):
    """A specific view/representation of an entity.
    
    Entities can have multiple views used for different purposes:
    - Summary view for note-level relevance ranking
    - Chunk views for passage retrieval
    - Thread summary for conversation context
    - Lesson view for actionable guidance
    
    This replaces the note-centric "summary embedding" vs "chunk embeddings"
    with a general mechanism that works for any entity type.
    """

    view_id: str = Field(
        ...,
        description="Stable, unique identifier for this view",
    )
    entity: EntityRef = Field(
        ...,
        description="Reference to the parent entity",
    )
    view_type: EntityViewType = Field(
        ...,
        description="Type of view (summary, chunk, thread_summary, etc.)",
    )

    # Content
    segment_id: str | None = Field(
        default=None,
        description="Segment identifier (chunk index, message index, etc.)",
    )
    content: str | None = Field(
        default=None,
        description="View content (may be omitted per source constraints)",
    )
    content_hash: str | None = Field(
        default=None,
        description="Hash of content for change detection",
    )

    # Timestamps
    created_at: datetime = Field(
        ...,
        description="When this view was created",
    )
    updated_at: datetime = Field(
        ...,
        description="When this view was last updated",
    )

    # Extensible metadata
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="View-specific metadata",
    )

    def to_vector_id(self) -> str:
        """Generate vector ID for this view."""
        return self.entity.to_vector_id(self.view_type, self.segment_id)

    def to_embedding_metadata(self) -> dict[str, Any]:
        """Generate metadata for vector embedding.
        
        Includes both entity-level and view-level fields.
        """
        return {
            # Entity fields
            "source_id": self.entity.source_id,
            "entity_type": self.entity.entity_type,
            "entity_id": self.entity.entity_id,
            "canonical_id": self.entity.canonical_id,
            "uri": self.entity.uri,
            # View fields
            "view_type": self.view_type.value,
            "segment_id": self.segment_id,
            "content_hash": self.content_hash,
            # Legacy compatibility for notes
            "note_id": self.entity.entity_id if self.entity.entity_type == "note" else None,
            "embedding_type": self.view_type.value,  # Legacy field
            # Additional metadata
            **self.metadata,
        }


# Source constants for common sources
class KnownSources:
    """Well-known source identifiers."""

    # Notes/Documents
    OBSIDIAN = "obsidian"
    NOTION = "notion"

    # Communication
    SLACK = "slack"
    OUTLOOK = "outlook"

    # Task Management
    LINEAR = "linear"

    # Google
    GOOGLE_CALENDAR = "google_calendar"
    GOOGLE_TASKS = "google_tasks"

    # Dev Tools
    GITHUB = "github"
    JIRA = "jira"
    LINEAR = "linear"

    # Internal
    EXPERIENCE = "experience"  # Experience memory
    KERNEL = "kernel"  # Kernel-generated
    SKILLS = "skills"  # Skill library (local or synced)


# Entity type constants for common types
class KnownEntityTypes:
    """Well-known entity types."""

    # Notes/Documents
    NOTE = "note"
    DOCUMENT = "document"
    
    # Tasks
    TASK = "task"
    ISSUE = "issue"
    TICKET = "ticket"

    # Communication
    MESSAGE = "message"
    THREAD = "thread"
    EMAIL = "email"
    CHANNEL = "channel"

    # Calendar
    CALENDAR_EVENT = "calendar_event"
    MEETING = "meeting"

    # Code
    FILE = "file"
    COMMIT = "commit"
    PULL_REQUEST = "pull_request"

    # Experience (internal)
    TRACE = "trace"
    CASE = "case"
    EVALUATION = "evaluation"
    LESSON = "lesson"
    PLAYBOOK = "playbook"
    SKILL = "skill"

    # Entities
    PERSON = "person"
    PROJECT = "project"
    TAG = "tag"

    # v1.0.6: Knowledge (semantic memory)
    DOMAIN = "domain"
    SYSTEM = "system"
    CONCEPT = "concept"
    PRACTICE = "practice"
    INSIGHT = "insight"
    PATTERN = "pattern"
    DATA_OBJECT = "data_object"
    RULE = "rule"

    # v1.0.6: Event clock (episodic memory)
    TRAJECTORY = "trajectory"
    DECISION_EVENT = "decision_event"
    OBSERVATION = "observation"
    SUMMARY = "summary"
