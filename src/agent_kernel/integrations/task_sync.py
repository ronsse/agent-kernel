"""Task Sync Adapter - Abstract interface for external task systems.

Provides a pluggable interface for syncing tasks between the kernel's
graph store and external task management systems (Linear, Jira, etc.).

All external writes are approval-gated per the integration patterns:
- Sync operations that CREATE/UPDATE external tasks require approval
- Sync operations that only READ external tasks do not require approval

Example implementations:
- LinearTaskAdapter: Sync with Linear issues
- HTTPTaskAdapter: Generic HTTP webhook-based sync
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

import structlog

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.task import TaskPriority, TaskStatus

logger = structlog.get_logger(__name__)


class SyncDirection(str, Enum):
    """Direction of task sync."""

    PUSH = "push"  # Kernel -> External
    PULL = "pull"  # External -> Kernel
    BIDIRECTIONAL = "bidirectional"


class SyncOperation(str, Enum):
    """Type of sync operation."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    COMPLETE = "complete"
    SKIP = "skip"


@dataclass
class ExternalTask:
    """Representation of a task in an external system.

    Maps between kernel task format and external system format.
    """

    # Identity
    external_id: str  # ID in external system
    kernel_task_id: str | None = None  # Corresponding kernel task ID

    # Content
    text: str = ""
    description: str | None = None

    # Status
    status: TaskStatus = TaskStatus.OPEN
    is_complete: bool = False

    # Scheduling
    due_date: date | None = None
    priority: TaskPriority = TaskPriority.P4

    # Organization
    project_id: str | None = None
    project_name: str | None = None
    tags: list[str] = field(default_factory=list)
    contexts: list[str] = field(default_factory=list)

    # Metadata
    source_note_id: str | None = None
    source_path: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Raw data from external system
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        status_value = self.status.value
        if self.status == TaskStatus.OPEN:
            status_value = "incomplete"
        elif self.status == TaskStatus.COMPLETED:
            status_value = "complete"
        return {
            "external_id": self.external_id,
            "kernel_task_id": self.kernel_task_id,
            "text": self.text,
            "description": self.description,
            "status": status_value,
            "is_complete": self.is_complete,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "priority": self.priority.value,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "tags": self.tags,
            "contexts": self.contexts,
            "source_note_id": self.source_note_id,
            "source_path": self.source_path,
        }


@dataclass
class SyncResult:
    """Result of a sync operation."""

    success: bool
    operation: SyncOperation
    external_task: ExternalTask | None = None
    kernel_task_id: str | None = None
    external_id: str | None = None
    error: str | None = None
    requires_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "operation": self.operation.value,
            "kernel_task_id": self.kernel_task_id,
            "external_id": self.external_id,
            "error": self.error,
            "requires_approval": self.requires_approval,
        }


@dataclass
class SyncSummary:
    """Summary of a batch sync operation."""

    started_at: datetime
    completed_at: datetime | None = None
    direction: SyncDirection = SyncDirection.PUSH
    total_tasks: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    pending_approval: int = 0
    results: list[SyncResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "direction": self.direction.value,
            "total_tasks": self.total_tasks,
            "created": self.created,
            "updated": self.updated,
            "deleted": self.deleted,
            "completed": self.completed,
            "skipped": self.skipped,
            "failed": self.failed,
            "pending_approval": self.pending_approval,
        }


class TaskSyncAdapter(ABC):
    """Abstract adapter for syncing tasks with external systems.

    Implementations should:
    1. Override connection/authentication in __init__
    2. Implement all abstract methods
    3. Map between ExternalTask and the external system's format

    All write operations (push) are approval-gated by default.
    Read operations (pull) do not require approval.

    Circuit breaker support:
        Pass a CircuitBreaker instance to protect against cascading failures
        from unreachable external systems. Use _check_circuit() before API
        calls and _record_circuit_result() after.
    """

    _circuit_breaker: Any | None = None

    def set_circuit_breaker(self, breaker: Any) -> None:
        """Attach a circuit breaker to this adapter.

        Args:
            breaker: A CircuitBreaker instance from tools.retry.
        """
        self._circuit_breaker = breaker

    def _check_circuit(self) -> bool:
        """Check if the circuit breaker allows a request.

        Returns:
            True if the request is allowed (or no breaker is set).
        """
        if self._circuit_breaker is None:
            return True
        return self._circuit_breaker.allow_request()

    def _record_circuit_success(self) -> None:
        """Record a successful external call."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_success()

    def _record_circuit_failure(self) -> None:
        """Record a failed external call."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_failure()

    @property
    @abstractmethod
    def adapter_id(self) -> str:
        """Unique identifier for this adapter (e.g., 'linear', 'jira')."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for this adapter."""

    @property
    def requires_approval_for_writes(self) -> bool:
        """Whether write operations require approval.

        Override to return False for trusted/local integrations.
        """
        return True

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test that the adapter can connect to the external system.

        Returns:
            True if connection is successful.
        """

    @abstractmethod
    async def list_tasks(
        self,
        project_id: str | None = None,
        include_completed: bool = False,
    ) -> list[ExternalTask]:
        """List tasks from the external system.

        Args:
            project_id: Optional project/list filter.
            include_completed: Whether to include completed tasks.

        Returns:
            List of tasks from external system.
        """

    @abstractmethod
    async def get_task(self, external_id: str) -> ExternalTask | None:
        """Get a single task by external ID.

        Args:
            external_id: ID in the external system.

        Returns:
            ExternalTask if found, None otherwise.
        """

    @abstractmethod
    async def create_task(self, task: ExternalTask) -> SyncResult:
        """Create a task in the external system.

        Args:
            task: Task to create.

        Returns:
            SyncResult with external_id if successful.
        """

    @abstractmethod
    async def update_task(self, task: ExternalTask) -> SyncResult:
        """Update a task in the external system.

        Args:
            task: Task with updated fields.

        Returns:
            SyncResult indicating success/failure.
        """

    @abstractmethod
    async def complete_task(self, external_id: str) -> SyncResult:
        """Mark a task as complete in the external system.

        Args:
            external_id: ID of task to complete.

        Returns:
            SyncResult indicating success/failure.
        """

    @abstractmethod
    async def delete_task(self, external_id: str) -> SyncResult:
        """Delete a task from the external system.

        Args:
            external_id: ID of task to delete.

        Returns:
            SyncResult indicating success/failure.
        """

    async def sync_to_external(
        self,
        tasks: list[ExternalTask],
        delete_missing: bool = False,
    ) -> SyncSummary:
        """Sync tasks from kernel to external system (PUSH).

        Args:
            tasks: Tasks to sync.
            delete_missing: Delete external tasks not in kernel.

        Returns:
            SyncSummary with operation counts.
        """
        summary = SyncSummary(started_at=utc_now(), direction=SyncDirection.PUSH)
        summary.total_tasks = len(tasks)

        for task in tasks:
            try:
                if task.external_id:
                    # Task exists in external, update it
                    if task.is_complete:
                        result = await self.complete_task(task.external_id)
                        if result.success:
                            summary.completed += 1
                    else:
                        result = await self.update_task(task)
                        if result.success:
                            summary.updated += 1
                else:
                    # Task doesn't exist in external, create it
                    result = await self.create_task(task)
                    if result.success:
                        summary.created += 1

                if result.requires_approval:
                    summary.pending_approval += 1

                if not result.success and not result.requires_approval:
                    summary.failed += 1

                summary.results.append(result)

            except Exception as e:
                logger.exception(
                    "task_sync_failed",
                    task_id=task.kernel_task_id,
                    external_id=task.external_id,
                )
                summary.failed += 1
                summary.results.append(
                    SyncResult(
                        success=False,
                        operation=SyncOperation.UPDATE,
                        kernel_task_id=task.kernel_task_id,
                        error=str(e),
                    )
                )

        # Handle deletion of missing tasks if requested
        if delete_missing:
            await self._delete_missing_tasks(tasks, summary)

        summary.completed_at = utc_now()

        logger.info(
            "task_sync_completed",
            adapter=self.adapter_id,
            direction=summary.direction.value,
            total=summary.total_tasks,
            created=summary.created,
            updated=summary.updated,
            completed=summary.completed,
            deleted=summary.deleted,
            failed=summary.failed,
            pending_approval=summary.pending_approval,
        )

        return summary

    async def _delete_missing_tasks(
        self,
        kernel_tasks: list[ExternalTask],
        summary: SyncSummary,
    ) -> None:
        """Delete tasks that exist in external but not in kernel.

        Args:
            kernel_tasks: Tasks from kernel that should exist in external.
            summary: Sync summary to update with deletion results.
        """
        try:
            # Get all tasks from external system
            external_tasks = await self.list_tasks(include_completed=False)

            # Build set of external IDs that should exist (from kernel tasks)
            kernel_external_ids = {
                task.external_id
                for task in kernel_tasks
                if task.external_id is not None
            }

            # Find tasks in external that aren't in kernel
            tasks_to_delete = [
                task for task in external_tasks if task.external_id not in kernel_external_ids
            ]

            if not tasks_to_delete:
                logger.debug(
                    "no_orphaned_tasks",
                    adapter=self.adapter_id,
                    external_count=len(external_tasks),
                    kernel_count=len(kernel_external_ids),
                )
                return

            logger.info(
                "deleting_orphaned_tasks",
                adapter=self.adapter_id,
                count=len(tasks_to_delete),
            )

            # Delete each orphaned task
            for task in tasks_to_delete:
                try:
                    result = await self.delete_task(task.external_id)

                    if result.success:
                        summary.deleted += 1
                        logger.debug(
                            "task_deleted",
                            external_id=task.external_id,
                            text=task.text,
                        )
                    elif result.requires_approval:
                        summary.pending_approval += 1
                        logger.debug(
                            "task_deletion_requires_approval",
                            external_id=task.external_id,
                            text=task.text,
                        )
                    else:
                        summary.failed += 1
                        logger.warning(
                            "task_deletion_failed",
                            external_id=task.external_id,
                            text=task.text,
                            error=result.error,
                        )

                    summary.results.append(result)

                except Exception as e:
                    logger.exception(
                        "task_deletion_exception",
                        external_id=task.external_id,
                    )
                    summary.failed += 1
                    summary.results.append(
                        SyncResult(
                            success=False,
                            operation=SyncOperation.DELETE,
                            external_id=task.external_id,
                            error=str(e),
                        )
                    )

        except Exception as e:
            logger.exception(
                "delete_missing_tasks_failed",
                adapter=self.adapter_id,
                error=str(e),
            )
            # Don't fail the entire sync if delete_missing fails
            # Just log the error and continue

    async def sync_from_external(
        self,
        project_id: str | None = None,
    ) -> list[ExternalTask]:
        """Sync tasks from external system to kernel (PULL).

        This is a read-only operation, no approval required.

        Args:
            project_id: Optional project filter.

        Returns:
            List of tasks from external system.
        """
        return await self.list_tasks(project_id=project_id, include_completed=False)


class MemoryTaskAdapter(TaskSyncAdapter):
    """In-memory task adapter for testing.

    Stores tasks in memory without any external system.
    """

    def __init__(self) -> None:
        """Initialize in-memory adapter."""
        self._tasks: dict[str, ExternalTask] = {}
        self._counter = 0

    @property
    def adapter_id(self) -> str:
        """Return adapter ID."""
        return "memory"

    @property
    def display_name(self) -> str:
        """Return display name."""
        return "In-Memory Tasks"

    @property
    def requires_approval_for_writes(self) -> bool:
        """Memory adapter doesn't require approval."""
        return False

    async def test_connection(self) -> bool:
        """Always returns True for memory adapter."""
        return True

    async def list_tasks(
        self,
        project_id: str | None = None,
        include_completed: bool = False,
    ) -> list[ExternalTask]:
        """List all tasks in memory."""
        tasks = list(self._tasks.values())

        if project_id:
            tasks = [t for t in tasks if t.project_id == project_id]

        if not include_completed:
            tasks = [t for t in tasks if not t.is_complete]

        return tasks

    async def get_task(self, external_id: str) -> ExternalTask | None:
        """Get a task by ID."""
        return self._tasks.get(external_id)

    async def create_task(self, task: ExternalTask) -> SyncResult:
        """Create a task in memory."""
        self._counter += 1
        external_id = f"mem_{self._counter}"

        new_task = ExternalTask(
            external_id=external_id,
            kernel_task_id=task.kernel_task_id,
            text=task.text,
            description=task.description,
            status=task.status,
            is_complete=task.is_complete,
            due_date=task.due_date,
            priority=task.priority,
            project_id=task.project_id,
            project_name=task.project_name,
            tags=task.tags,
            contexts=task.contexts,
            source_note_id=task.source_note_id,
            source_path=task.source_path,
            created_at=utc_now(),
        )

        self._tasks[external_id] = new_task

        return SyncResult(
            success=True,
            operation=SyncOperation.CREATE,
            external_task=new_task,
            kernel_task_id=task.kernel_task_id,
            external_id=external_id,
        )

    async def update_task(self, task: ExternalTask) -> SyncResult:
        """Update a task in memory."""
        if task.external_id not in self._tasks:
            return SyncResult(
                success=False,
                operation=SyncOperation.UPDATE,
                kernel_task_id=task.kernel_task_id,
                external_id=task.external_id,
                error="Task not found",
            )

        existing = self._tasks[task.external_id]
        existing.text = task.text
        existing.description = task.description
        existing.status = task.status
        existing.is_complete = task.is_complete
        existing.due_date = task.due_date
        existing.priority = task.priority
        existing.tags = task.tags
        existing.updated_at = utc_now()

        return SyncResult(
            success=True,
            operation=SyncOperation.UPDATE,
            external_task=existing,
            kernel_task_id=task.kernel_task_id,
            external_id=task.external_id,
        )

    async def complete_task(self, external_id: str) -> SyncResult:
        """Mark a task as complete."""
        if external_id not in self._tasks:
            return SyncResult(
                success=False,
                operation=SyncOperation.COMPLETE,
                external_id=external_id,
                error="Task not found",
            )

        task = self._tasks[external_id]
        task.is_complete = True
        task.status = TaskStatus.COMPLETED
        task.updated_at = utc_now()

        return SyncResult(
            success=True,
            operation=SyncOperation.COMPLETE,
            external_task=task,
            kernel_task_id=task.kernel_task_id,
            external_id=external_id,
        )

    async def delete_task(self, external_id: str) -> SyncResult:
        """Delete a task from memory."""
        if external_id not in self._tasks:
            return SyncResult(
                success=False,
                operation=SyncOperation.DELETE,
                external_id=external_id,
                error="Task not found",
            )

        del self._tasks[external_id]

        return SyncResult(
            success=True,
            operation=SyncOperation.DELETE,
            external_id=external_id,
        )
