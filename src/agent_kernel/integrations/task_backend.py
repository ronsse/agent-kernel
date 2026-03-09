"""ITaskBackend interface (v1.0.5).

Framework-agnostic interface that any task backend must implement.
This enables swapping between external, Google Tasks, local storage, etc.
while keeping the kernel as the system of record.

Design principles:
- All methods return/accept kernel schema objects (TaskEntity, ProjectEntity, etc.)
- Backend-specific details are hidden behind the interface
- Kernel can work without any backend (using local storage)

References:
- Design Patch v1.0.5: external Integration
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_kernel.core.schemas.task import (
        LabelEntity,
        ProjectEntity,
        TaskEntity,
        TaskPatch,
        TaskQuery,
    )


class BackendTaskRef:
    """Reference to a task in the external backend.

    Used as the return value from create_task to provide
    the external ID without fetching the full entity.
    """

    def __init__(
        self,
        external_id: str,
        external_project_id: str | None = None,
        external_url: str | None = None,
    ) -> None:
        self.external_id = external_id
        self.external_project_id = external_project_id
        self.external_url = external_url


class ITaskBackend(ABC):
    """Abstract interface for task backends.

    Any system that can store/manage tasks (external, Google Tasks,
    local SQLite, etc.) should implement this interface.

    All methods accept/return kernel schema objects. The implementation
    handles translation to/from backend-specific formats.
    """

    @property
    @abstractmethod
    def backend_id(self) -> str:
        """Unique identifier for this backend (e.g., 'linear', 'google_tasks')."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for this backend."""
        ...

    @property
    def requires_approval_for_writes(self) -> bool:
        """Whether writes to this backend require user approval.

        Defaults to True per kernel design principles (all external writes
        must be approval-gated).
        """
        return True

    # ─────────────────────────────────────────────────────────────────
    # Connection & Status
    # ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if the backend is properly configured (credentials, etc.)."""
        ...

    @abstractmethod
    def test_connection(self) -> bool:
        """Test the connection to the backend.

        Returns:
            True if connection is working.
        """
        ...

    # ─────────────────────────────────────────────────────────────────
    # Task Operations
    # ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def list_tasks(self, query: TaskQuery | None = None) -> list[TaskEntity]:
        """List tasks from the backend.

        Args:
            query: Optional query parameters for filtering.

        Returns:
            List of TaskEntity objects (kernel schema).
        """
        ...

    @abstractmethod
    def get_task(self, external_id: str) -> TaskEntity | None:
        """Get a single task by its external ID.

        Args:
            external_id: ID of the task in the external system.

        Returns:
            TaskEntity if found, None otherwise.
        """
        ...

    @abstractmethod
    def create_task(self, task: TaskEntity) -> BackendTaskRef:
        """Create a task in the backend.

        Args:
            task: TaskEntity to create.

        Returns:
            BackendTaskRef with the external ID.

        Raises:
            BackendError: If creation fails.
        """
        ...

    @abstractmethod
    def update_task(self, external_id: str, patch: TaskPatch) -> TaskEntity:
        """Update a task in the backend.

        Args:
            external_id: ID of the task to update.
            patch: TaskPatch with fields to update.

        Returns:
            Updated TaskEntity.

        Raises:
            BackendError: If update fails.
            TaskNotFoundError: If task doesn't exist.
        """
        ...

    @abstractmethod
    def complete_task(self, external_id: str) -> TaskEntity:
        """Mark a task as complete.

        Args:
            external_id: ID of the task to complete.

        Returns:
            Updated TaskEntity with completed status.

        Raises:
            BackendError: If completion fails.
        """
        ...

    @abstractmethod
    def reopen_task(self, external_id: str) -> TaskEntity:
        """Reopen a completed task.

        Args:
            external_id: ID of the task to reopen.

        Returns:
            Updated TaskEntity with open status.

        Raises:
            BackendError: If reopening fails.
        """
        ...

    @abstractmethod
    def delete_task(self, external_id: str) -> bool:
        """Delete a task from the backend.

        Args:
            external_id: ID of the task to delete.

        Returns:
            True if deleted successfully.

        Raises:
            BackendError: If deletion fails.
        """
        ...

    # ─────────────────────────────────────────────────────────────────
    # Project Operations
    # ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def list_projects(self) -> list[ProjectEntity]:
        """List all projects from the backend.

        Returns:
            List of ProjectEntity objects.
        """
        ...

    def get_project(self, external_id: str) -> ProjectEntity | None:
        """Get a project by external ID.

        Default implementation searches list_projects().
        Override for more efficient lookup.
        """
        for project in self.list_projects():
            if project.ext.get(self.backend_id, {}).get("id") == external_id:
                return project
        return None

    # ─────────────────────────────────────────────────────────────────
    # Label Operations
    # ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def list_labels(self) -> list[LabelEntity]:
        """List all labels from the backend.

        Returns:
            List of LabelEntity objects.
        """
        ...

    # ─────────────────────────────────────────────────────────────────
    # Optional: Sync Support
    # ─────────────────────────────────────────────────────────────────

    def supports_incremental_sync(self) -> bool:
        """Whether this backend supports incremental sync tokens."""
        return False

    def get_sync_token(self) -> str | None:
        """Get the current sync token for incremental sync."""
        return None

    def sync_changes(self, sync_token: str | None = None) -> tuple[list[TaskEntity], str]:
        """Get changes since the last sync.

        Args:
            sync_token: Token from previous sync (None for full sync).

        Returns:
            Tuple of (changed_tasks, new_sync_token).

        Raises:
            NotImplementedError: If incremental sync not supported.
        """
        raise NotImplementedError(
            f"{self.backend_id} does not support incremental sync"
        )

    # ─────────────────────────────────────────────────────────────────
    # Optional: Reminders
    # ─────────────────────────────────────────────────────────────────

    def supports_reminders(self) -> bool:
        """Whether this backend supports reminders."""
        return False

    def set_reminder(
        self,
        task_id: str,
        reminder_at: str,  # ISO datetime or relative string
    ) -> bool:
        """Set a reminder for a task.

        Raises:
            NotImplementedError: If reminders not supported.
        """
        raise NotImplementedError(
            f"{self.backend_id} does not support reminders"
        )


class BackendError(Exception):
    """Base exception for backend errors."""

    def __init__(self, message: str, backend_id: str, original: Exception | None = None):
        super().__init__(message)
        self.backend_id = backend_id
        self.original = original


class TaskNotFoundError(BackendError):
    """Task not found in the backend."""

    def __init__(self, external_id: str, backend_id: str):
        super().__init__(
            f"Task {external_id} not found in {backend_id}",
            backend_id,
        )
        self.external_id = external_id


class BackendConnectionError(BackendError):
    """Connection to backend failed."""

    pass


class BackendAuthError(BackendError):
    """Authentication with backend failed."""

    pass
