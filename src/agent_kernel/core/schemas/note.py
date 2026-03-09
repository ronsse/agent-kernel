"""Note schemas for Obsidian vault alignment (v1.0.8).

This module defines the Note Schema v2 contract for Obsidian notes,
coordinating between human-authored content and kernel-managed metadata.

Key concepts:
- `state` = lifecycle (inbox/active/evergreen/archived) for automation
- `status` = semantic status of the object (task/project state)
- `auto.*` = kernel-managed enrichment namespace
- Reserved blocks = safe deterministic writeback zones

References:
- Vault Patch v1.0.8: Obsidian Alignment & Coordination Contract
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import Field

from agent_kernel.core.schemas.base import KernelModel, VersionedModel, utc_now


class NoteLifecycleState(str, Enum):
    """Lifecycle state of a note in the vault.
    
    Used for automation and retrieval filtering.
    Maps to folder structure:
    - 00-Inbox -> inbox
    - 06-Archive -> archived
    - Most active work -> active
    - Long-lived reference -> evergreen
    """

    INBOX = "inbox"         # Not processed yet (00-Inbox/)
    ACTIVE = "active"       # Current work (most notes)
    EVERGREEN = "evergreen" # Long-lived reference
    ARCHIVED = "archived"   # Completed/old (06-Archive/)


class NoteType(str, Enum):
    """Standard note types in the vault."""

    DAILY = "daily"
    TASK = "task"
    PROJECT = "project"
    RESOURCE = "resource"
    MEETING = "meeting"
    AREA = "area"
    PERSON = "person"
    SPEC = "spec"
    OTHER = "other"


class AutoMetadata(KernelModel):
    """Kernel-managed enrichment metadata.
    
    This namespace is machine-owned and may be regenerated at any time.
    Human tags should NOT be copied here automatically.
    """

    tags: list[str] = Field(
        default_factory=list,
        description="LLM-generated topic tags",
    )
    classification: str | None = Field(
        default=None,
        alias="class",
        description="LLM-generated classification",
    )
    summary: str | None = Field(
        default=None,
        description="LLM-generated summary for semantic search",
    )
    entities: list[str] = Field(
        default_factory=list,
        description="Extracted named entities (people, projects, etc.)",
    )
    confidence: float | None = Field(
        default=None,
        description="Confidence score for enrichment (0-1)",
    )
    extracted_on: date | None = Field(
        default=None,
        description="Date when enrichment was performed",
    )


class NoteMetadata(VersionedModel):
    """Complete frontmatter contract for Obsidian notes (Schema v2).
    
    Ownership rules:
    - Human-owned: tags, type, status, main markdown body
    - Kernel-owned: id, state, auto.*
    
    Example:
        ---
        id: note_01J...
        created: 2026-01-21
        type: project
        state: active
        tags: [work, q1-goals]
        status: active
        auto:
          tags: [planning, strategy]
          class: project
          summary: Q1 goals planning document...
        ---
    """

    # Kernel-managed (required)
    id: str = Field(
        ...,
        description="Stable note ID (kernel-managed, never change)",
    )
    created: date = Field(
        ...,
        description="Creation date",
    )

    # Classification
    type: NoteType = Field(
        default=NoteType.OTHER,
        description="Note type classification",
    )
    state: NoteLifecycleState = Field(
        default=NoteLifecycleState.ACTIVE,
        description="Lifecycle state (kernel uses this for automation)",
    )

    # Human-owned
    tags: list[str] = Field(
        default_factory=list,
        description="Human tags (never auto-modified)",
    )
    status: str | None = Field(
        default=None,
        description="Semantic status (varies by type: active/completed/etc.)",
    )

    # Optional fields
    priority: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="Priority (1-5, for tasks)",
    )
    due: date | None = Field(
        default=None,
        description="Due date (for tasks)",
    )
    project: str | None = Field(
        default=None,
        description="Project slug for retrieval filtering",
    )

    # Kernel-managed enrichment
    auto: AutoMetadata = Field(
        default_factory=AutoMetadata,
        description="Kernel-managed enrichment metadata",
    )

    # Extensions
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional frontmatter fields",
    )

    @classmethod
    def from_frontmatter(cls, frontmatter: dict[str, Any], note_id: str | None = None) -> NoteMetadata:
        """Parse NoteMetadata from raw frontmatter dict.
        
        Args:
            frontmatter: Raw YAML frontmatter dict
            note_id: Optional note ID to use if not in frontmatter
        """
        # Extract known fields
        data = dict(frontmatter)
        
        # Handle id
        if "id" not in data and note_id:
            data["id"] = note_id
        
        # Handle created
        if "created" not in data:
            data["created"] = date.today()
        elif isinstance(data["created"], str):
            data["created"] = date.fromisoformat(data["created"])
        elif isinstance(data["created"], datetime):
            data["created"] = data["created"].date()
        
        # Handle type
        if "type" in data:
            try:
                data["type"] = NoteType(data["type"])
            except ValueError:
                data["type"] = NoteType.OTHER
        
        # Handle state
        if "state" in data:
            try:
                data["state"] = NoteLifecycleState(data["state"])
            except ValueError:
                data["state"] = NoteLifecycleState.ACTIVE
        
        # Handle auto
        if "auto" in data and isinstance(data["auto"], dict):
            data["auto"] = AutoMetadata(**data["auto"])
        
        # Extract known fields, put rest in extra
        known_fields = {
            "id", "created", "type", "state", "tags", "status",
            "priority", "due", "project", "auto"
        }
        extra = {k: v for k, v in data.items() if k not in known_fields}
        data["extra"] = extra
        
        # Remove unknown fields from data
        data = {k: v for k, v in data.items() if k in known_fields or k == "extra"}
        
        return cls(**data)

    def to_frontmatter(self) -> dict[str, Any]:
        """Convert to frontmatter dict for YAML serialization."""
        result: dict[str, Any] = {
            "id": self.id,
            "created": self.created.isoformat(),
            "type": self.type.value,
            "state": self.state.value,
        }
        
        if self.tags:
            result["tags"] = self.tags
        if self.status:
            result["status"] = self.status
        if self.priority is not None:
            result["priority"] = self.priority
        if self.due:
            result["due"] = self.due.isoformat()
        if self.project:
            result["project"] = self.project
        
        # Auto metadata (only include if populated)
        auto_dict = {}
        if self.auto.tags:
            auto_dict["tags"] = self.auto.tags
        if self.auto.classification:
            auto_dict["class"] = self.auto.classification
        if self.auto.summary:
            auto_dict["summary"] = self.auto.summary
        if self.auto.entities:
            auto_dict["entities"] = self.auto.entities
        if self.auto.confidence is not None:
            auto_dict["confidence"] = self.auto.confidence
        if self.auto.extracted_on:
            auto_dict["extracted_on"] = self.auto.extracted_on.isoformat()
        
        if auto_dict:
            result["auto"] = auto_dict
        
        # Extra fields
        result.update(self.extra)
        
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Reserved Block Support
# ─────────────────────────────────────────────────────────────────────────────

class ReservedBlockType(str, Enum):
    """Types of kernel-managed reserved blocks in markdown."""

    TASKS_TODAY = "tasks_today"
    CALENDAR_TODAY = "calendar_today"
    MEETING_TODAY = "meeting_today"
    DAILY_SUMMARY = "daily_summary"
    PROJECT_DASHBOARD = "project_dashboard"
    PROJECT_TASKS = "project_tasks"
    WEEKLY_SUMMARY = "weekly_summary"
    MEETING_AUTO = "meeting_auto"


class ReservedBlock(KernelModel):
    """A kernel-managed reserved block in markdown.
    
    Format in markdown:
        <!-- kernel:block:{block_type} begin -->
        ... kernel-managed content only ...
        <!-- kernel:block:{block_type} end -->
    
    Rules:
    - Kernel may ONLY edit inside these blocks
    - Humans can delete a block to opt out
    - Kernel recreates only if workflow requests it
    """

    block_type: ReservedBlockType = Field(
        ...,
        description="Type of reserved block",
    )
    content: str = Field(
        default="",
        description="Content inside the block",
    )
    start_line: int | None = Field(
        default=None,
        description="Line number where block starts (if parsed from content)",
    )
    end_line: int | None = Field(
        default=None,
        description="Line number where block ends (if parsed from content)",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Folder → State Mapping
# ─────────────────────────────────────────────────────────────────────────────

# Default folder to state mappings
FOLDER_STATE_MAP: dict[str, NoteLifecycleState] = {
    "00-Inbox": NoteLifecycleState.INBOX,
    "00-inbox": NoteLifecycleState.INBOX,
    "Inbox": NoteLifecycleState.INBOX,
    "inbox": NoteLifecycleState.INBOX,
    "06-Archive": NoteLifecycleState.ARCHIVED,
    "06-archive": NoteLifecycleState.ARCHIVED,
    "Archive": NoteLifecycleState.ARCHIVED,
    "archive": NoteLifecycleState.ARCHIVED,
}


def infer_state_from_path(path: str) -> NoteLifecycleState:
    """Infer lifecycle state from note path based on folder structure.
    
    Args:
        path: Relative path to the note (e.g., "00-Inbox/quick-note.md")
    
    Returns:
        Inferred NoteLifecycleState
    """
    # Check each folder mapping
    for folder, state in FOLDER_STATE_MAP.items():
        if path.startswith(folder + "/") or path.startswith(folder + "\\"):
            return state
    
    # Default to active
    return NoteLifecycleState.ACTIVE


def infer_type_from_path(path: str) -> NoteType:
    """Infer note type from path based on folder structure.
    
    Args:
        path: Relative path to the note
    
    Returns:
        Inferred NoteType
    """
    path_lower = path.lower()
    
    if "01-daily" in path_lower or "/daily/" in path_lower:
        return NoteType.DAILY
    if "02-tasks" in path_lower or "/tasks/" in path_lower:
        return NoteType.TASK
    if "03-projects" in path_lower or "/projects/" in path_lower:
        return NoteType.PROJECT
    if "04-areas" in path_lower or "/areas/" in path_lower:
        return NoteType.AREA
    if "05-resources" in path_lower or "/resources/" in path_lower:
        return NoteType.RESOURCE
    if "agent-rules" in path_lower:
        return NoteType.SPEC
    
    return NoteType.OTHER
