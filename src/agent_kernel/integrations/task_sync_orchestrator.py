"""TaskSyncOrchestrator (v1.0.5).

Coordinates bidirectional task sync between:
- Obsidian (Markdown tasks in notes)
- Kernel (TaskEntity in graph store)
- External backends (external, Google Tasks, etc.)

Responsibilities:
- Obsidian → Kernel: Parse markdown tasks, create TaskEntities
- Backend → Kernel: Ingest tasks, maintain TaskLinks
- Kernel → Backend: Push approved writes
- Kernel → Obsidian: Materialize task views (optional)

Design principles:
- Kernel is the system of record
- Explicit TaskLink mapping (no fuzzy matching)
- Field-level conflict resolution
- Idempotent sync operations

References:
- Design Patch v1.0.5: external Integration
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import to_json_dict, utc_now
from agent_kernel.core.schemas.task import (
    TaskEntity,
    TaskLink,
    TaskPatch,
    TaskStatus,
)

if TYPE_CHECKING:
    from agent_kernel.integrations.task_backend import ITaskBackend
    from agent_kernel.memory.graph_store import GraphStore

logger = structlog.get_logger(__name__)


class ConflictResolution(str, Enum):
    """Conflict resolution strategy."""

    BACKEND_WINS = "backend_wins"  # External backend wins
    KERNEL_WINS = "kernel_wins"  # Kernel/Obsidian wins
    NEWEST_WINS = "newest_wins"  # Most recently updated wins
    MANUAL = "manual"  # Require user approval


class SyncFieldPolicy(str, Enum):
    """Per-field sync policy."""

    BACKEND_WINS = "backend_wins"  # external wins for this field
    KERNEL_WINS = "kernel_wins"  # Kernel wins for this field
    NEWEST_WINS = "newest_wins"  # Most recent wins


@dataclass
class SyncConflict:
    """Represents a sync conflict between kernel and backend."""

    id: str = field(default_factory=lambda: f"conflict_{generate_ulid()}")
    task_link_id: str = ""
    kernel_task_id: str = ""
    external_id: str = ""
    field_name: str = ""
    kernel_value: Any = None
    backend_value: Any = None
    resolution: ConflictResolution | None = None
    resolved_value: Any = None
    resolved_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class SyncResult:
    """Result of a sync operation."""

    created: int = 0
    updated: int = 0
    deleted: int = 0
    conflicts: list[SyncConflict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


@dataclass
class SyncState:
    """Persistent sync state for a backend."""

    backend_id: str
    last_sync_at: datetime | None = None
    sync_token: str | None = None
    full_sync_needed: bool = True
    last_error: str | None = None


class TaskSyncOrchestrator:
    """Orchestrates task sync between kernel and external backends.

    Usage:
        orchestrator = TaskSyncOrchestrator(
            backend=MyBackend(),  # Your ITaskBackend implementation
            graph_store=graph_store,
        )

        # Full sync from backend to kernel
        result = await orchestrator.sync_from_backend()

        # Push a task to backend
        result = await orchestrator.push_to_backend(task_entity)
    """

    def __init__(
        self,
        backend: ITaskBackend,
        graph_store: GraphStore | None = None,
        conflict_policy: ConflictResolution = ConflictResolution.BACKEND_WINS,
        field_policies: dict[str, SyncFieldPolicy] | None = None,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            backend: ITaskBackend implementation (external, etc.)
            graph_store: GraphStore for persisting TaskLinks
            conflict_policy: Default conflict resolution strategy
            field_policies: Per-field override policies
        """
        self.backend = backend
        self.graph_store = graph_store
        self.conflict_policy = conflict_policy
        self.field_policies = field_policies or self._default_field_policies()

        # In-memory caches
        self._task_links: dict[str, TaskLink] = {}  # external_id -> TaskLink
        self._kernel_tasks: dict[str, TaskEntity] = {}  # kernel_id -> TaskEntity
        self._sync_state: SyncState = SyncState(backend_id=backend.backend_id)

        if self.graph_store:
            self._load_task_links()

    def _default_field_policies(self) -> dict[str, SyncFieldPolicy]:
        """Default per-field policies.

        Per v1.0.5 design:
        - backend_wins for completion status & reminders
        - kernel_wins for rich descriptions/context links
        - newest_wins for most fields
        """
        return {
            "status": SyncFieldPolicy.BACKEND_WINS,
            "completed_at": SyncFieldPolicy.BACKEND_WINS,
            "description": SyncFieldPolicy.KERNEL_WINS,
            "title": SyncFieldPolicy.NEWEST_WINS,
            "priority": SyncFieldPolicy.NEWEST_WINS,
            "due": SyncFieldPolicy.NEWEST_WINS,
            "labels": SyncFieldPolicy.NEWEST_WINS,
        }

    # ─────────────────────────────────────────────────────────────────
    # Sync Operations
    # ─────────────────────────────────────────────────────────────────

    def sync_from_backend(self) -> SyncResult:
        """Sync tasks from backend to kernel.

        Fetches all tasks from backend, creates/updates TaskEntities
        in kernel, maintains TaskLinks for mapping.

        Returns:
            SyncResult with counts and any conflicts.
        """
        start_time = utc_now()
        result = SyncResult()

        try:
            # Fetch tasks from backend
            backend_tasks = self.backend.list_tasks()
            logger.info(
                "sync_from_backend_started",
                backend=self.backend.backend_id,
                task_count=len(backend_tasks),
            )

            for backend_task in backend_tasks:
                external_id = backend_task.ext.get(self.backend.backend_id, {}).get("id")
                if not external_id:
                    result.errors.append(f"Task missing external ID: {backend_task.id}")
                    continue

                # Check if we have an existing link
                task_link = self._get_task_link(external_id)

                if task_link:
                    # Update existing
                    conflicts = self._merge_task(task_link, backend_task)
                    result.conflicts.extend(conflicts)
                    result.updated += 1
                else:
                    # Create new
                    self._create_task_link(backend_task, external_id)
                    result.created += 1

            # Update sync state
            self._sync_state.last_sync_at = utc_now()
            self._sync_state.full_sync_needed = False
            self._sync_state.last_error = None

        except Exception as e:
            logger.exception("sync_from_backend_failed")
            result.errors.append(str(e))
            self._sync_state.last_error = str(e)

        result.duration_ms = (utc_now() - start_time).total_seconds() * 1000
        logger.info(
            "sync_from_backend_completed",
            created=result.created,
            updated=result.updated,
            conflicts=len(result.conflicts),
            errors=len(result.errors),
            duration_ms=result.duration_ms,
        )
        return result

    def push_to_backend(self, task: TaskEntity) -> TaskLink | None:
        """Push a kernel task to the backend.

        Creates or updates the task in the external backend.

        Args:
            task: TaskEntity to push.

        Returns:
            TaskLink for the synced task, or None on failure.
        """
        try:
            # Check if task already has a link
            existing_link = self._get_task_link_by_kernel_id(task.id)

            if existing_link:
                # Update existing task
                external_id = existing_link.external_id
                current_hash = self._hash_task(task)
                if existing_link.kernel_hash == current_hash:
                    existing_link.last_sync_at = utc_now()
                    self._persist_task_link(existing_link)
                    logger.info(
                        "task_push_skipped_no_change",
                        kernel_id=task.id,
                        external_id=external_id,
                    )
                    return existing_link

                patch = self._task_to_patch(task)
                self.backend.update_task(external_id, patch)

                # Update link
                existing_link.last_sync_at = utc_now()
                existing_link.kernel_hash = current_hash
                existing_link.sync_version += 1
                self._persist_task_link(existing_link)

                logger.info(
                    "task_pushed_update",
                    kernel_id=task.id,
                    external_id=external_id,
                )
                return existing_link
            else:
                # Create new task in backend
                ref = self.backend.create_task(task)

                # Create link
                task_link = TaskLink(
                    kernel_task_id=task.id,
                    external_system=self.backend.backend_id,
                    external_id=ref.external_id,
                    external_project_id=ref.external_project_id,
                    kernel_hash=self._hash_task(task),
                )
                self._task_links[ref.external_id] = task_link
                self._kernel_tasks[task.id] = task
                self._persist_task_link(task_link)

                logger.info(
                    "task_pushed_create",
                    kernel_id=task.id,
                    external_id=ref.external_id,
                )
                return task_link

        except Exception as e:
            logger.exception("push_to_backend_failed", kernel_id=task.id)
            return None

    def complete_task(self, kernel_task_id: str) -> bool:
        """Mark a task as complete in both kernel and backend.

        Args:
            kernel_task_id: Kernel task ID to complete.

        Returns:
            True if successful.
        """
        task_link = self._get_task_link_by_kernel_id(kernel_task_id)
        if not task_link:
            logger.warning("complete_task_no_link", kernel_id=kernel_task_id)
            return False

        try:
            self.backend.complete_task(task_link.external_id)

            # Update kernel task
            if kernel_task_id in self._kernel_tasks:
                self._kernel_tasks[kernel_task_id].status = TaskStatus.COMPLETED
                self._kernel_tasks[kernel_task_id].completed_at = utc_now()

            task_link.last_sync_at = utc_now()
            logger.info("task_completed", kernel_id=kernel_task_id)
            return True

        except Exception:
            logger.exception("complete_task_failed", kernel_id=kernel_task_id)
            return False

    # ─────────────────────────────────────────────────────────────────
    # Task Link Management
    # ─────────────────────────────────────────────────────────────────

    def _get_task_link(self, external_id: str) -> TaskLink | None:
        """Get TaskLink by external ID."""
        return self._task_links.get(external_id)

    def _get_task_link_by_kernel_id(self, kernel_id: str) -> TaskLink | None:
        """Get TaskLink by kernel task ID."""
        for link in self._task_links.values():
            if link.kernel_task_id == kernel_id:
                return link
        return None

    def _create_task_link(
        self,
        backend_task: TaskEntity,
        external_id: str,
    ) -> TaskLink:
        """Create a new TaskLink for a backend task."""
        task_link = TaskLink(
            kernel_task_id=backend_task.id,
            external_system=self.backend.backend_id,
            external_id=external_id,
            external_project_id=backend_task.project_ref,
            kernel_hash=self._hash_task(backend_task),
            external_hash=self._hash_task(backend_task),
        )
        self._task_links[external_id] = task_link
        self._kernel_tasks[backend_task.id] = backend_task
        self._persist_task_link(task_link)
        return task_link

    # ─────────────────────────────────────────────────────────────────
    # Conflict Resolution
    # ─────────────────────────────────────────────────────────────────

    def _merge_task(
        self,
        task_link: TaskLink,
        backend_task: TaskEntity,
    ) -> list[SyncConflict]:
        """Merge backend task into kernel task.

        Uses field-level policies to resolve conflicts.

        Returns:
            List of conflicts that occurred.
        """
        conflicts: list[SyncConflict] = []
        kernel_task = self._kernel_tasks.get(task_link.kernel_task_id)

        if not kernel_task:
            # No existing kernel task, just accept backend version
            self._kernel_tasks[task_link.kernel_task_id] = backend_task
            task_link.last_sync_at = utc_now()
            task_link.external_hash = self._hash_task(backend_task)
            return conflicts

        # Check each field for conflicts
        merge_fields = ["title", "description", "status", "priority", "due", "labels"]

        for field_name in merge_fields:
            kernel_value = getattr(kernel_task, field_name, None)
            backend_value = getattr(backend_task, field_name, None)

            if kernel_value != backend_value:
                # Conflict detected
                policy = self.field_policies.get(field_name, SyncFieldPolicy.BACKEND_WINS)
                resolved_value = self._resolve_field_conflict(
                    field_name, kernel_value, backend_value, policy, kernel_task, backend_task
                )

                if resolved_value != kernel_value:
                    # Record the conflict
                    conflict = SyncConflict(
                        task_link_id=task_link.id,
                        kernel_task_id=kernel_task.id,
                        external_id=task_link.external_id,
                        field_name=field_name,
                        kernel_value=kernel_value,
                        backend_value=backend_value,
                        resolution=ConflictResolution.BACKEND_WINS,
                        resolved_value=resolved_value,
                        resolved_at=utc_now(),
                    )
                    conflicts.append(conflict)

                    # Apply resolution
                    setattr(kernel_task, field_name, resolved_value)

        # Update link
        task_link.last_sync_at = utc_now()
        task_link.external_hash = self._hash_task(backend_task)
        task_link.kernel_hash = self._hash_task(kernel_task)
        task_link.sync_version += 1
        self._persist_task_link(task_link)

        return conflicts

    def _resolve_field_conflict(
        self,
        field_name: str,
        kernel_value: Any,
        backend_value: Any,
        policy: SyncFieldPolicy,
        kernel_task: TaskEntity,
        backend_task: TaskEntity,
    ) -> Any:
        """Resolve a field-level conflict.

        Args:
            field_name: Name of the conflicting field.
            kernel_value: Current kernel value.
            backend_value: Value from backend.
            policy: Resolution policy.
            kernel_task: Full kernel task.
            backend_task: Full backend task.

        Returns:
            Resolved value to use.
        """
        if policy == SyncFieldPolicy.BACKEND_WINS:
            return backend_value
        elif policy == SyncFieldPolicy.KERNEL_WINS:
            return kernel_value
        elif policy == SyncFieldPolicy.NEWEST_WINS:
            # Compare updated_at timestamps
            if kernel_task.updated_at >= backend_task.updated_at:
                return kernel_value
            return backend_value

        return backend_value  # Default to backend

    # ─────────────────────────────────────────────────────────────────
    # Utilities
    # ─────────────────────────────────────────────────────────────────

    def _hash_task(self, task: TaskEntity) -> str:
        """Generate a content hash for a task.

        Used for detecting changes between syncs.
        """
        content = f"{task.title}|{task.description}|{task.status}|{task.priority}|{task.due}|{task.labels}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _task_to_patch(self, task: TaskEntity) -> TaskPatch:
        """Convert TaskEntity to TaskPatch for updates."""
        return TaskPatch(
            title=task.title,
            description=task.description,
            status=task.status,
            priority=task.priority,
            due=task.due,
            labels=task.labels,
            duration_minutes=task.duration_minutes,
        )

    def get_sync_state(self) -> SyncState:
        """Get current sync state."""
        return self._sync_state

    def get_all_tasks(self) -> list[TaskEntity]:
        """Get all kernel tasks from cache."""
        return list(self._kernel_tasks.values())

    def get_task_count(self) -> int:
        """Get count of synced tasks."""
        return len(self._kernel_tasks)

    def _load_task_links(self) -> None:
        """Load persisted TaskLinks from graph store."""
        if not self.graph_store:
            return

        try:
            nodes = self.graph_store.query(node_type="task_link", limit=10000)
        except Exception:
            logger.exception("task_link_load_failed")
            return

        loaded = 0
        for node in nodes:
            props = node.get("properties", {})
            try:
                link = TaskLink(**props)
            except Exception:
                logger.warning(
                    "task_link_load_invalid",
                    node_id=node.get("node_id"),
                )
                continue
            self._task_links[link.external_id] = link
            loaded += 1

        logger.info("task_links_loaded", count=loaded)

    def _persist_task_link(self, link: TaskLink) -> None:
        """Persist a TaskLink to graph store."""
        if not self.graph_store:
            return
        self.graph_store.upsert_node(
            node_id=link.id,
            node_type="task_link",
            properties=to_json_dict(link),
        )

    def get_conflict_log(self) -> list[SyncConflict]:
        """Get log of all conflicts (for debugging)."""
        # In a full implementation, this would query from persistent storage
        return []
