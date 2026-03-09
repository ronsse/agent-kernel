"""Task Entity schemas (v1.0.5).

Platform-neutral task representation that can sync with multiple backends
(external system, Obsidian, Google Tasks, etc.) while keeping the kernel as
the system of record.

Design principles:
- Single canonical TaskEntity schema with backend-specific extensions in `ext.*`
- TaskLink for explicit kernel ↔ external ID mapping (no fuzzy matching)
- Kernel can reconstruct full task set without any external backend

References:
- Design Patch v1.0.5: external system Integration
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import VersionedModel, utc_now


class TaskStatus(str, Enum):
    """Canonical task status (platform-neutral)."""

    OPEN = "open"
    COMPLETED = "completed"
    CANCELED = "canceled"
    ARCHIVED = "archived"
    # Legacy aliases
    INCOMPLETE = "open"
    COMPLETE = "completed"


class TaskPriority(str, Enum):
    """Canonical task priority (maps to P1-P4 across systems)."""

    P1 = "p1"  # Urgent/highest
    P2 = "p2"  # High
    P3 = "p3"  # Medium
    P4 = "p4"  # Low/default
    # Legacy aliases
    HIGH = "p1"
    MEDIUM = "p3"
    LOW = "p4"
    NONE = "p4"

    @classmethod
    def from_numeric(cls, priority: int) -> "TaskPriority":
        """Convert numeric priority (1-4, where 4 is highest) to kernel priority."""
        mapping = {4: cls.P1, 3: cls.P2, 2: cls.P3, 1: cls.P4}
        return mapping.get(priority, cls.P4)

    def to_numeric(self) -> int:
        """Convert to numeric priority (1-4, where 4 is highest)."""
        mapping = {
            TaskPriority.P1: 4,
            TaskPriority.P2: 3,
            TaskPriority.P3: 2,
            TaskPriority.P4: 1,
        }
        return mapping.get(self, 1)


class TaskScope(str, Enum):
    """Task scope/namespace (for routing to projects)."""

    WORK = "work"
    PERSONAL = "personal"
    MIXED = "mixed"


class RecurrenceRule(VersionedModel):
    """Canonical recurrence rule (vendor-agnostic).

    Stores recurrence in a portable format that can be translated
    to/from external system due_string, iCal RRULE, etc.
    """

    # Human-readable pattern (primary representation)
    pattern: str = Field(
        ...,
        description="Human-readable pattern (e.g., 'every monday', 'every 2 weeks')",
    )

    # Optional structured representation
    frequency: str | None = Field(
        default=None,
        description="RRULE frequency: DAILY, WEEKLY, MONTHLY, YEARLY",
    )
    interval: int = Field(
        default=1,
        description="Interval between occurrences",
    )
    by_weekday: list[str] | None = Field(
        default=None,
        description="Days of week (MO, TU, WE, TH, FR, SA, SU)",
    )
    by_monthday: list[int] | None = Field(
        default=None,
        description="Days of month (1-31)",
    )
    count: int | None = Field(
        default=None,
        description="Number of occurrences (None = infinite)",
    )
    until: date | None = Field(
        default=None,
        description="End date for recurrence",
    )

    def to_due_string(self) -> str:
        """Convert to natural language due string format."""
        return self.pattern

    @classmethod
    def from_due_string(cls, due_string: str) -> "RecurrenceRule":
        """Create from a natural language due string."""
        return cls(pattern=due_string)


class TaskEntity(VersionedModel):
    """Canonical task entity (platform-neutral).

    This is the kernel's system-of-record representation of a task.
    Backend-specific fields live in the `ext` dictionary.
    """

    # Identity
    id: str = Field(
        default_factory=lambda: f"task_{generate_ulid()}",
        description="Kernel task ID (task_{ulid})",
    )

    # Core fields
    title: str = Field(
        ...,
        description="Task title/content",
    )
    description: str = Field(
        default="",
        description="Extended description/notes",
    )
    status: TaskStatus = Field(
        default=TaskStatus.OPEN,
        description="Task status",
    )
    priority: TaskPriority = Field(
        default=TaskPriority.P4,
        description="Task priority (P1=highest, P4=lowest)",
    )

    # Scope and organization
    scope: TaskScope = Field(
        default=TaskScope.PERSONAL,
        description="Work/personal namespace",
    )
    project_ref: str | None = Field(
        default=None,
        description="Kernel project ID reference",
    )
    labels: list[str] = Field(
        default_factory=list,
        description="Semantic labels (maps to external system label names)",
    )
    section_ref: str | None = Field(
        default=None,
        description="Section within project",
    )

    # Temporal
    due: datetime | date | None = Field(
        default=None,
        description="Due date/datetime (date for all-day, datetime for specific time)",
    )
    due_timezone: str | None = Field(
        default=None,
        description="Timezone for due datetime (None = floating/local)",
    )
    deadline: date | None = Field(
        default=None,
        description="Hard deadline (date-only, non-recurring)",
    )
    duration_minutes: int | None = Field(
        default=None,
        ge=1,
        description="Estimated duration in minutes",
    )
    recurrence: RecurrenceRule | None = Field(
        default=None,
        description="Recurrence rule if this is a recurring task",
    )

    # Hierarchy
    parent_task_id: str | None = Field(
        default=None,
        description="Parent task ID for subtasks",
    )

    # Source/provenance
    source_system: str | None = Field(
        default=None,
        description="Where this task was originally created (external, obsidian, etc.)",
    )
    source_entity_ref: str | None = Field(
        default=None,
        description="Reference to source entity (e.g., note_id, email_id)",
    )
    captured_at: datetime | None = Field(
        default=None,
        description="When the task was first captured",
    )

    # Completion tracking
    completed_at: datetime | None = Field(
        default=None,
        description="When the task was completed",
    )
    completed_by: str | None = Field(
        default=None,
        description="Who completed the task (user_id)",
    )

    # Backend-specific extensions
    ext: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Backend-specific extensions (e.g., ext.external.*, ext.obsidian.*)",
    )

    # Timestamps
    created_at: datetime = Field(
        default_factory=utc_now,
        description="When this entity was created in the kernel",
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        description="When this entity was last updated",
    )

    @model_validator(mode="after")
    def set_completed_at_on_complete(self) -> TaskEntity:
        """Set completed_at when status changes to completed."""
        if self.status == TaskStatus.COMPLETED and self.completed_at is None:
            self.completed_at = utc_now()
        return self

    @property
    def is_overdue(self) -> bool:
        """Check if task is overdue."""
        if self.status != TaskStatus.OPEN:
            return False
        if self.due is None:
            return False
        if isinstance(self.due, date) and not isinstance(self.due, datetime):
            return date.today() > self.due
        # Handle timezone comparison
        now = utc_now()
        due = self.due
        if due.tzinfo is None:
            # Make naive datetime UTC for comparison
            from datetime import timezone
            due = due.replace(tzinfo=timezone.utc)
        return now > due

    @property
    def is_due_today(self) -> bool:
        """Check if task is due today."""
        if self.due is None:
            return False
        if isinstance(self.due, date) and not isinstance(self.due, datetime):
            return self.due == date.today()
        return self.due.date() == date.today()

    @property
    def is_recurring(self) -> bool:
        """Check if this is a recurring task."""
        return self.recurrence is not None

    def to_obsidian_task(self) -> str:
        """Render as Obsidian markdown task.

        Format: - [x] Task title @due(YYYY-MM-DD) #label
        """
        checkbox = "[x]" if self.status == TaskStatus.COMPLETED else "[ ]"
        parts = [f"- {checkbox} {self.title}"]

        if self.due:
            due_str = self.due.strftime("%Y-%m-%d") if isinstance(self.due, datetime) else str(self.due)
            parts.append(f"📅 {due_str}")

        if self.priority != TaskPriority.P4:
            priority_icons = {
                TaskPriority.P1: "🔺",
                TaskPriority.P2: "🔼",
                TaskPriority.P3: "🔽",
            }
            parts.append(priority_icons.get(self.priority, ""))

        for label in self.labels:
            parts.append(f"#{label}")

        return " ".join(parts)


class ProjectEntity(VersionedModel):
    """Canonical project entity (task grouping).

    Projects are containers for tasks, mapped to external system projects,
    Obsidian folders/tags, etc.
    """

    id: str = Field(
        default_factory=lambda: f"proj_{generate_ulid()}",
        description="Kernel project ID (proj_{ulid})",
    )
    name: str = Field(
        ...,
        description="Project name",
    )
    description: str = Field(
        default="",
        description="Project description",
    )
    scope: TaskScope = Field(
        default=TaskScope.PERSONAL,
        description="Work/personal namespace",
    )
    color: str | None = Field(
        default=None,
        description="Display color (hex or named)",
    )
    parent_id: str | None = Field(
        default=None,
        description="Parent project ID for nested projects",
    )
    is_inbox: bool = Field(
        default=False,
        description="Whether this is the default inbox project",
    )
    is_archived: bool = Field(
        default=False,
        description="Whether this project is archived",
    )

    # Backend-specific extensions
    ext: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Backend-specific extensions",
    )

    created_at: datetime = Field(
        default_factory=utc_now,
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
    )


class LabelEntity(VersionedModel):
    """Canonical label entity (semantic tag for tasks).

    Labels are normalized across systems (case-folded, trimmed).
    """

    id: str = Field(
        default_factory=lambda: f"label_{generate_ulid()}",
        description="Kernel label ID",
    )
    name: str = Field(
        ...,
        description="Normalized label name (lowercase, no whitespace)",
    )
    display_name: str | None = Field(
        default=None,
        description="Human-readable display name",
    )
    color: str | None = Field(
        default=None,
        description="Display color",
    )
    scope: TaskScope | None = Field(
        default=None,
        description="Optional scope restriction",
    )

    # Backend-specific extensions
    ext: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
    )

    created_at: datetime = Field(
        default_factory=utc_now,
    )

    @classmethod
    def normalize_name(cls, name: str) -> str:
        """Normalize a label name for consistent matching."""
        return name.lower().strip().replace(" ", "-")


class ReminderPolicy(VersionedModel):
    """Reminder policy for tasks.

    Defines when/how reminders should be set. Stored in kernel,
    materialized to external system reminders when possible.
    """

    # Default offsets by priority
    default_offsets: dict[TaskPriority, timedelta] = Field(
        default_factory=lambda: {
            TaskPriority.P1: timedelta(hours=1),
            TaskPriority.P2: timedelta(hours=3),
            TaskPriority.P3: timedelta(days=1),
            TaskPriority.P4: timedelta(days=1),
        },
        description="Default reminder offset before due date, by priority",
    )

    # Per-scope overrides
    scope_offsets: dict[TaskScope, timedelta] | None = Field(
        default=None,
        description="Override offsets by scope",
    )

    # Fallback behavior
    fallback_to_calendar: bool = Field(
        default=True,
        description="Create calendar events if backend doesn't support reminders",
    )


class TaskLink(VersionedModel):
    """Mapping between kernel task and external system task.

    Provides explicit, stable linking without fuzzy matching.
    Stores per-field hashes and timestamps for conflict detection.
    """

    id: str = Field(
        default_factory=lambda: f"tlink_{generate_ulid()}",
        description="Link record ID",
    )

    # Kernel side
    kernel_task_id: str = Field(
        ...,
        description="Kernel TaskEntity.id",
    )

    # External side
    external_system: str = Field(
        ...,
        description="External system identifier (external, google_tasks, etc.)",
    )
    external_id: str = Field(
        ...,
        description="ID in the external system",
    )
    external_project_id: str | None = Field(
        default=None,
        description="Project ID in the external system",
    )
    workspace_id: str | None = Field(
        default=None,
        description="Workspace/team ID if applicable",
    )

    # Sync state
    last_sync_at: datetime = Field(
        default_factory=utc_now,
        description="When this link was last synced",
    )
    kernel_hash: str | None = Field(
        default=None,
        description="Hash of kernel task at last sync",
    )
    external_hash: str | None = Field(
        default=None,
        description="Hash of external task at last sync",
    )
    sync_version: int = Field(
        default=1,
        description="Sync version for conflict detection",
    )

    # Per-field sync state (for field-level conflict resolution)
    field_hashes: dict[str, str] = Field(
        default_factory=dict,
        description="Per-field content hashes for granular sync",
    )

    created_at: datetime = Field(
        default_factory=utc_now,
    )

    def needs_sync(self, current_kernel_hash: str, current_external_hash: str) -> bool:
        """Check if this link needs synchronization."""
        return (
            self.kernel_hash != current_kernel_hash
            or self.external_hash != current_external_hash
        )


class ContextLink(VersionedModel):
    """Link between a task and context entities (notes, emails, events, etc.).

    Provides bidirectional navigation from tasks to their source context.
    """

    id: str = Field(
        default_factory=lambda: f"clink_{generate_ulid()}",
    )
    task_id: str = Field(
        ...,
        description="Kernel task ID",
    )

    # Context entity reference
    context_source: str = Field(
        ...,
        description="Source system of context (obsidian, outlook, slack, etc.)",
    )
    context_type: str = Field(
        ...,
        description="Type of context (note, email, thread, event, etc.)",
    )
    context_id: str = Field(
        ...,
        description="ID of context entity",
    )
    context_uri: str | None = Field(
        default=None,
        description="URI/permalink to context (for embedding in external system description)",
    )

    # Relationship type
    relationship: str = Field(
        default="related_to",
        description="Type of relationship (created_from, related_to, blocks, etc.)",
    )

    # Metadata
    created_at: datetime = Field(
        default_factory=utc_now,
    )
    created_by: str | None = Field(
        default=None,
        description="Who/what created this link (user, agent, sync)",
    )


class TaskQuery(VersionedModel):
    """Query parameters for listing tasks from any backend."""

    # Filters
    status: list[TaskStatus] | None = Field(
        default=None,
        description="Filter by status (None = all)",
    )
    priority: list[TaskPriority] | None = Field(
        default=None,
        description="Filter by priority",
    )
    project_ref: str | None = Field(
        default=None,
        description="Filter by project",
    )
    labels: list[str] | None = Field(
        default=None,
        description="Filter by labels (AND)",
    )
    scope: TaskScope | None = Field(
        default=None,
        description="Filter by scope",
    )

    # Temporal filters
    due_before: datetime | date | None = Field(
        default=None,
        description="Due before this date/time",
    )
    due_after: datetime | date | None = Field(
        default=None,
        description="Due after this date/time",
    )
    include_overdue: bool = Field(
        default=True,
        description="Include overdue tasks",
    )

    # Hierarchy
    parent_task_id: str | None = Field(
        default=None,
        description="Filter to subtasks of a parent",
    )
    include_subtasks: bool = Field(
        default=True,
        description="Include subtasks in results",
    )

    # Pagination
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
    )
    offset: int = Field(
        default=0,
        ge=0,
    )


class TaskPatch(VersionedModel):
    """Partial update for a task.

    Only fields that are set will be updated.
    """

    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    scope: TaskScope | None = None
    project_ref: str | None = None
    labels: list[str] | None = None
    due: datetime | date | None = None
    due_timezone: str | None = None
    deadline: date | None = None
    duration_minutes: int | None = None
    recurrence: RecurrenceRule | None = None
    parent_task_id: str | None = None

    def to_update_dict(self) -> dict[str, Any]:
        """Get dictionary of non-None fields for update."""
        return {k: v for k, v in self.model_dump().items() if v is not None}
