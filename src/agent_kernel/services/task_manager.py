"""Task Manager Service (v1.0.5).

Unified service for managing tasks across Obsidian, Kernel, and external backend.
This is the primary interface for agents to use when working with tasks.

Capabilities provided:
- List/filter/search tasks
- Create tasks (in external backend, Obsidian, or both)
- Move tasks between projects
- Update task metadata (priority, labels, due dates)
- Complete/reopen tasks
- Organize tasks (assign to projects, sections)
- Generate task views for Obsidian

Design principles:
- Kernel is system of record
- All writes go through approval gates
- Bidirectional sync with explicit mappings
- Agents use this service, not raw adapters

References:
- Design Patch v1.0.5: external backend Integration
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.task import (
    ContextLink,
    LabelEntity,
    ProjectEntity,
    TaskEntity,
    TaskLink,
    TaskPatch,
    TaskPriority,
    TaskQuery,
    TaskScope,
    TaskStatus,
)
from agent_kernel.integrations.task_backend import ITaskBackend
from agent_kernel.integrations.task_sync_orchestrator import (
    SyncResult,
    TaskSyncOrchestrator,
)

if TYPE_CHECKING:
    from agent_kernel.memory.graph_store import GraphStore

logger = structlog.get_logger(__name__)


class TaskFilter(str, Enum):
    """Predefined task filters."""

    ALL = "all"
    TODAY = "today"
    TOMORROW = "tomorrow"
    THIS_WEEK = "this_week"
    OVERDUE = "overdue"
    NO_DUE_DATE = "no_due_date"
    HIGH_PRIORITY = "high_priority"
    COMPLETED_TODAY = "completed_today"


class SortOrder(str, Enum):
    """Task sort orders."""

    DUE_DATE = "due_date"
    PRIORITY = "priority"
    CREATED = "created"
    PROJECT = "project"
    ALPHABETICAL = "alphabetical"


@dataclass
class TaskView:
    """A view/query of tasks for display or export."""

    name: str
    tasks: list[TaskEntity] = field(default_factory=list)
    filter_applied: str = ""
    sort_order: str = ""
    generated_at: datetime = field(default_factory=utc_now)

    @property
    def count(self) -> int:
        return len(self.tasks)

    @property
    def open_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == TaskStatus.OPEN)

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == TaskStatus.COMPLETED)


@dataclass
class TaskAction:
    """Represents a task action to be executed (with approval)."""

    id: str = field(default_factory=lambda: f"action_{generate_ulid()}")
    action_type: str = ""  # create, update, complete, move, delete
    task_id: str | None = None
    task_title: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = True
    approved: bool = False
    executed: bool = False
    result: str = ""
    created_at: datetime = field(default_factory=utc_now)


class TaskManager:
    """Unified task management service for agents.

    Provides a high-level API for task operations that:
    - Works with TaskEntity (kernel schema)
    - Syncs with external backends (external backend)
    - Maintains TaskLinks for cross-system references
    - Respects approval gates for writes

    Usage:
        manager = TaskManager(backend=MyBackend())  # Your ITaskBackend implementation
        await manager.sync()

        # List tasks
        today_tasks = manager.get_tasks(filter=TaskFilter.TODAY)

        # Create task (returns action for approval)
        action = manager.create_task(
            title="Review PR",
            project="Work",
            priority=TaskPriority.P2,
        )

        # Execute after approval
        if action.approved:
            result = await manager.execute_action(action)
    """

    def __init__(
        self,
        backend: ITaskBackend | None = None,
        graph_store: GraphStore | None = None,
        auto_sync: bool = True,
    ) -> None:
        """Initialize the task manager.

        Args:
            backend: External task backend (external backend, etc.)
            graph_store: Graph store for persistence
            auto_sync: Whether to sync on initialization
        """
        self.backend = backend
        self.graph_store = graph_store
        self._orchestrator: TaskSyncOrchestrator | None = None
        self._synced = False

        # In-memory task store
        self._tasks: dict[str, TaskEntity] = {}
        self._projects: dict[str, ProjectEntity] = {}
        self._labels: dict[str, LabelEntity] = {}
        self._task_links: dict[str, TaskLink] = {}  # kernel_id -> TaskLink
        self._context_links: list[ContextLink] = []

        # Pending actions queue
        self._pending_actions: list[TaskAction] = []

        if backend:
            self._orchestrator = TaskSyncOrchestrator(
                backend=backend,
                graph_store=graph_store,
            )

    # ─────────────────────────────────────────────────────────────────
    # Sync Operations
    # ─────────────────────────────────────────────────────────────────

    def sync(self) -> SyncResult:
        """Sync tasks from backend to kernel.

        Returns:
            SyncResult with counts and any errors.
        """
        if not self._orchestrator:
            return SyncResult(errors=["No backend configured"])

        result = self._orchestrator.sync_from_backend()

        # Update local caches
        for task in self._orchestrator.get_all_tasks():
            self._tasks[task.id] = task
            # Track task links
            ext_id = task.ext.get(self.backend.backend_id, {}).get("id") if self.backend else None
            if ext_id:
                self._task_links[task.id] = TaskLink(
                    kernel_task_id=task.id,
                    external_system=self.backend.backend_id if self.backend else "",
                    external_id=ext_id,
                )

        # Sync projects
        if self.backend:
            for project in self.backend.list_projects():
                self._projects[project.id] = project

            for label in self.backend.list_labels():
                self._labels[label.id] = label

        self._synced = True
        logger.info(
            "task_manager_synced",
            task_count=len(self._tasks),
            project_count=len(self._projects),
            label_count=len(self._labels),
        )
        return result

    def is_synced(self) -> bool:
        """Check if manager has synced with backend."""
        return self._synced

    # ─────────────────────────────────────────────────────────────────
    # Task Queries
    # ─────────────────────────────────────────────────────────────────

    def get_tasks(
        self,
        filter: TaskFilter | None = None,
        project: str | None = None,
        labels: list[str] | None = None,
        scope: TaskScope | None = None,
        priority: TaskPriority | None = None,
        status: TaskStatus | None = None,
        search: str | None = None,
        sort: SortOrder = SortOrder.DUE_DATE,
        limit: int | None = None,
    ) -> TaskView:
        """Get tasks with optional filtering.

        Args:
            filter: Predefined filter (TODAY, OVERDUE, etc.)
            project: Filter by project name or ID
            labels: Filter by labels (AND)
            scope: Filter by scope (work/personal)
            priority: Filter by priority
            status: Filter by status
            search: Text search in title/description
            sort: Sort order
            limit: Max results

        Returns:
            TaskView with filtered tasks.
        """
        tasks = list(self._tasks.values())

        # Apply predefined filter
        if filter:
            tasks = self._apply_filter(tasks, filter)

        # Apply custom filters
        if project:
            tasks = [t for t in tasks if self._matches_project(t, project)]
        if labels:
            tasks = [t for t in tasks if all(l in t.labels for l in labels)]
        if scope:
            tasks = [t for t in tasks if t.scope == scope]
        if priority:
            tasks = [t for t in tasks if t.priority == priority]
        if status:
            tasks = [t for t in tasks if t.status == status]
        if search:
            search_lower = search.lower()
            tasks = [
                t for t in tasks
                if search_lower in t.title.lower()
                or search_lower in t.description.lower()
            ]

        # Sort
        tasks = self._sort_tasks(tasks, sort)

        # Limit
        if limit:
            tasks = tasks[:limit]

        return TaskView(
            name=filter.value if filter else "custom",
            tasks=tasks,
            filter_applied=str(filter) if filter else "",
            sort_order=sort.value,
        )

    def get_task(self, task_id: str) -> TaskEntity | None:
        """Get a specific task by kernel ID."""
        return self._tasks.get(task_id)

    def get_task_by_external_id(self, external_id: str) -> TaskEntity | None:
        """Get a task by its external (external backend) ID."""
        for task in self._tasks.values():
            ext = task.ext.get(self.backend.backend_id if self.backend else "", {})
            if ext.get("id") == external_id:
                return task
        return None

    def search_tasks(self, query: str, limit: int = 20) -> list[TaskEntity]:
        """Search tasks by text in title and description."""
        query_lower = query.lower()
        results = []
        for task in self._tasks.values():
            if query_lower in task.title.lower() or query_lower in task.description.lower():
                results.append(task)
            if len(results) >= limit:
                break
        return results

    # ─────────────────────────────────────────────────────────────────
    # Task Actions (require approval)
    # ─────────────────────────────────────────────────────────────────

    def create_task(
        self,
        title: str,
        description: str = "",
        project: str | None = None,
        priority: TaskPriority = TaskPriority.P4,
        scope: TaskScope = TaskScope.PERSONAL,
        labels: list[str] | None = None,
        due: date | datetime | None = None,
        due_string: str | None = None,
    ) -> TaskAction:
        """Create a new task (returns action for approval).

        Args:
            title: Task title/content
            description: Extended description
            project: Project name or ID
            priority: Priority level
            scope: Work/personal scope
            labels: List of labels
            due: Due date/datetime
            due_string: Natural language due (e.g., "tomorrow", "next Monday")

        Returns:
            TaskAction to be approved and executed.
        """
        action = TaskAction(
            action_type="create",
            task_title=title,
            parameters={
                "title": title,
                "description": description,
                "project": project,
                "priority": priority.value,
                "scope": scope.value,
                "labels": labels or [],
                "due": str(due) if due else None,
                "due_string": due_string,
            },
            requires_approval=self.backend.requires_approval_for_writes if self.backend else True,
        )
        self._pending_actions.append(action)
        return action

    def update_task(
        self,
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        priority: TaskPriority | None = None,
        labels: list[str] | None = None,
        due: date | datetime | None = None,
        project: str | None = None,
    ) -> TaskAction | None:
        """Update a task (returns action for approval)."""
        task = self.get_task(task_id)
        if not task:
            logger.warning("update_task_not_found", task_id=task_id)
            return None

        action = TaskAction(
            action_type="update",
            task_id=task_id,
            task_title=task.title,
            parameters={
                "title": title,
                "description": description,
                "priority": priority.value if priority else None,
                "labels": labels,
                "due": str(due) if due else None,
                "project": project,
            },
            requires_approval=self.backend.requires_approval_for_writes if self.backend else True,
        )
        self._pending_actions.append(action)
        return action

    def complete_task(self, task_id: str) -> TaskAction | None:
        """Mark a task as complete (returns action for approval)."""
        task = self.get_task(task_id)
        if not task:
            return None

        action = TaskAction(
            action_type="complete",
            task_id=task_id,
            task_title=task.title,
            requires_approval=self.backend.requires_approval_for_writes if self.backend else True,
        )
        self._pending_actions.append(action)
        return action

    def move_task(
        self,
        task_id: str,
        to_project: str,
        to_section: str | None = None,
    ) -> TaskAction | None:
        """Move a task to a different project (returns action for approval)."""
        task = self.get_task(task_id)
        if not task:
            return None

        action = TaskAction(
            action_type="move",
            task_id=task_id,
            task_title=task.title,
            parameters={
                "to_project": to_project,
                "to_section": to_section,
            },
            requires_approval=True,
        )
        self._pending_actions.append(action)
        return action

    def add_labels(self, task_id: str, labels: list[str]) -> TaskAction | None:
        """Add labels to a task."""
        task = self.get_task(task_id)
        if not task:
            return None

        new_labels = list(set(task.labels + labels))
        return self.update_task(task_id, labels=new_labels)

    def remove_labels(self, task_id: str, labels: list[str]) -> TaskAction | None:
        """Remove labels from a task."""
        task = self.get_task(task_id)
        if not task:
            return None

        new_labels = [l for l in task.labels if l not in labels]
        return self.update_task(task_id, labels=new_labels)

    def set_priority(self, task_id: str, priority: TaskPriority) -> TaskAction | None:
        """Set task priority."""
        return self.update_task(task_id, priority=priority)

    def set_due_date(self, task_id: str, due: date | datetime) -> TaskAction | None:
        """Set task due date."""
        return self.update_task(task_id, due=due)

    # ─────────────────────────────────────────────────────────────────
    # Action Execution
    # ─────────────────────────────────────────────────────────────────

    def execute_action(self, action: TaskAction) -> bool:
        """Execute an approved action.

        Args:
            action: TaskAction to execute.

        Returns:
            True if successful.
        """
        if not action.approved and action.requires_approval:
            logger.warning("action_not_approved", action_id=action.id)
            return False

        if action.executed:
            logger.warning("action_already_executed", action_id=action.id)
            return False

        try:
            if action.action_type == "create":
                return self._execute_create(action)
            elif action.action_type == "update":
                return self._execute_update(action)
            elif action.action_type == "complete":
                return self._execute_complete(action)
            elif action.action_type == "move":
                return self._execute_move(action)
            else:
                logger.warning("unknown_action_type", action_type=action.action_type)
                return False
        except Exception as e:
            logger.exception("action_execution_failed", action_id=action.id)
            action.result = str(e)
            return False

    def _execute_create(self, action: TaskAction) -> bool:
        """Execute a create action."""
        if not self.backend:
            action.result = "No backend configured"
            return False

        params = action.parameters
        task = TaskEntity(
            title=params["title"],
            description=params.get("description", ""),
            priority=TaskPriority(params["priority"]),
            scope=TaskScope(params["scope"]),
            labels=params.get("labels", []),
            due=date.fromisoformat(params["due"]) if params.get("due") else None,
            project_ref=self._resolve_project_id(params.get("project")),
        )

        ref = self.backend.create_task(task)
        task.ext[self.backend.backend_id] = {"id": ref.external_id}
        self._tasks[task.id] = task

        action.task_id = task.id
        action.executed = True
        action.result = f"Created task: {ref.external_id}"
        logger.info("task_created", kernel_id=task.id, external_id=ref.external_id)
        return True

    def _execute_update(self, action: TaskAction) -> bool:
        """Execute an update action."""
        if not self.backend or not action.task_id:
            return False

        task = self.get_task(action.task_id)
        if not task:
            return False

        ext_id = task.ext.get(self.backend.backend_id, {}).get("id")
        if not ext_id:
            action.result = "Task not synced to backend"
            return False

        params = action.parameters
        patch = TaskPatch(
            title=params.get("title"),
            description=params.get("description"),
            priority=TaskPriority(params["priority"]) if params.get("priority") else None,
            labels=params.get("labels"),
            due=date.fromisoformat(params["due"]) if params.get("due") else None,
        )

        self.backend.update_task(ext_id, patch)

        # Update local cache
        for key, value in patch.to_update_dict().items():
            setattr(task, key, value)

        action.executed = True
        action.result = f"Updated task: {ext_id}"
        return True

    def _execute_complete(self, action: TaskAction) -> bool:
        """Execute a complete action."""
        if not self.backend or not action.task_id:
            return False

        task = self.get_task(action.task_id)
        if not task:
            return False

        ext_id = task.ext.get(self.backend.backend_id, {}).get("id")
        if not ext_id:
            action.result = "Task not synced to backend"
            return False

        self.backend.complete_task(ext_id)
        task.status = TaskStatus.COMPLETED
        task.completed_at = utc_now()

        action.executed = True
        action.result = f"Completed task: {task.title}"
        return True

    def _execute_move(self, action: TaskAction) -> bool:
        """Execute a move action."""
        if not self.backend or not action.task_id:
            return False

        task = self.get_task(action.task_id)
        if not task:
            return False

        ext_id = task.ext.get(self.backend.backend_id, {}).get("id")
        if not ext_id:
            return False

        params = action.parameters
        project_id = self._resolve_project_id(params.get("to_project"))

        patch = TaskPatch()
        # Note: Moving requires updating project_id via the backend
        # This is external backend-specific and would need the backend to support it
        self.backend.update_task(ext_id, patch)

        action.executed = True
        action.result = f"Moved task to {params.get('to_project')}"
        return True

    # ─────────────────────────────────────────────────────────────────
    # Project & Label Operations
    # ─────────────────────────────────────────────────────────────────

    def get_projects(self) -> list[ProjectEntity]:
        """Get all projects."""
        return list(self._projects.values())

    def get_project(self, name_or_id: str) -> ProjectEntity | None:
        """Get project by name or ID."""
        # Try by ID first
        if name_or_id in self._projects:
            return self._projects[name_or_id]

        # Try by name
        for project in self._projects.values():
            if project.name.lower() == name_or_id.lower():
                return project

        return None

    def get_labels(self) -> list[LabelEntity]:
        """Get all labels."""
        return list(self._labels.values())

    def get_tasks_by_project(self, project_name: str) -> list[TaskEntity]:
        """Get all tasks in a project."""
        project = self.get_project(project_name)
        if not project:
            return []

        return [
            t for t in self._tasks.values()
            if self._matches_project(t, project_name)
        ]

    def get_tasks_by_label(self, label: str) -> list[TaskEntity]:
        """Get all tasks with a specific label."""
        label_lower = label.lower()
        return [
            t for t in self._tasks.values()
            if label_lower in [l.lower() for l in t.labels]
        ]

    # ─────────────────────────────────────────────────────────────────
    # Context Links (Task ↔ Note/Email/etc)
    # ─────────────────────────────────────────────────────────────────

    def link_task_to_context(
        self,
        task_id: str,
        context_source: str,
        context_type: str,
        context_id: str,
        context_uri: str | None = None,
        relationship: str = "related_to",
    ) -> ContextLink:
        """Link a task to a context entity (note, email, etc.)."""
        link = ContextLink(
            task_id=task_id,
            context_source=context_source,
            context_type=context_type,
            context_id=context_id,
            context_uri=context_uri,
            relationship=relationship,
        )
        self._context_links.append(link)
        logger.info(
            "context_link_created",
            task_id=task_id,
            context_source=context_source,
            context_id=context_id,
        )
        return link

    def get_context_links(self, task_id: str) -> list[ContextLink]:
        """Get all context links for a task."""
        return [l for l in self._context_links if l.task_id == task_id]

    def get_tasks_linked_to(
        self,
        context_source: str,
        context_id: str,
    ) -> list[TaskEntity]:
        """Get all tasks linked to a specific context."""
        task_ids = [
            l.task_id for l in self._context_links
            if l.context_source == context_source and l.context_id == context_id
        ]
        return [self._tasks[tid] for tid in task_ids if tid in self._tasks]

    # ─────────────────────────────────────────────────────────────────
    # Pending Actions Queue
    # ─────────────────────────────────────────────────────────────────

    def get_pending_actions(self) -> list[TaskAction]:
        """Get all pending (unapproved) actions."""
        return [a for a in self._pending_actions if not a.executed]

    def approve_action(self, action_id: str) -> bool:
        """Approve an action for execution."""
        for action in self._pending_actions:
            if action.id == action_id:
                action.approved = True
                return True
        return False

    def approve_all_pending(self) -> int:
        """Approve all pending actions."""
        count = 0
        for action in self._pending_actions:
            if not action.executed and not action.approved:
                action.approved = True
                count += 1
        return count

    def execute_all_approved(self) -> tuple[int, int]:
        """Execute all approved actions.

        Returns:
            Tuple of (success_count, failure_count).
        """
        success = 0
        failure = 0
        for action in self._pending_actions:
            if action.approved and not action.executed:
                if self.execute_action(action):
                    success += 1
                else:
                    failure += 1
        return success, failure

    # ─────────────────────────────────────────────────────────────────
    # Statistics
    # ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Get task statistics."""
        tasks = list(self._tasks.values())
        today = date.today()

        return {
            "total": len(tasks),
            "open": sum(1 for t in tasks if t.status == TaskStatus.OPEN),
            "completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
            "overdue": sum(1 for t in tasks if t.is_overdue),
            "due_today": sum(1 for t in tasks if t.is_due_today),
            "high_priority": sum(
                1 for t in tasks
                if t.priority in (TaskPriority.P1, TaskPriority.P2)
                and t.status == TaskStatus.OPEN
            ),
            "projects": len(self._projects),
            "labels": len(self._labels),
            "pending_actions": len(self.get_pending_actions()),
        }

    # ─────────────────────────────────────────────────────────────────
    # Private Helpers
    # ─────────────────────────────────────────────────────────────────

    def _apply_filter(
        self, tasks: list[TaskEntity], filter: TaskFilter
    ) -> list[TaskEntity]:
        """Apply a predefined filter."""
        today = date.today()

        if filter == TaskFilter.ALL:
            return [t for t in tasks if t.status == TaskStatus.OPEN]
        elif filter == TaskFilter.TODAY:
            return [t for t in tasks if t.is_due_today and t.status == TaskStatus.OPEN]
        elif filter == TaskFilter.TOMORROW:
            tomorrow = today + timedelta(days=1)
            return [
                t for t in tasks
                if t.due and self._date_equals(t.due, tomorrow)
                and t.status == TaskStatus.OPEN
            ]
        elif filter == TaskFilter.THIS_WEEK:
            week_end = today + timedelta(days=7)
            return [
                t for t in tasks
                if t.due and today <= self._to_date(t.due) <= week_end
                and t.status == TaskStatus.OPEN
            ]
        elif filter == TaskFilter.OVERDUE:
            return [t for t in tasks if t.is_overdue]
        elif filter == TaskFilter.NO_DUE_DATE:
            return [t for t in tasks if t.due is None and t.status == TaskStatus.OPEN]
        elif filter == TaskFilter.HIGH_PRIORITY:
            return [
                t for t in tasks
                if t.priority in (TaskPriority.P1, TaskPriority.P2)
                and t.status == TaskStatus.OPEN
            ]
        elif filter == TaskFilter.COMPLETED_TODAY:
            return [
                t for t in tasks
                if t.status == TaskStatus.COMPLETED
                and t.completed_at
                and t.completed_at.date() == today
            ]

        return tasks

    def _sort_tasks(
        self, tasks: list[TaskEntity], order: SortOrder
    ) -> list[TaskEntity]:
        """Sort tasks by specified order."""
        if order == SortOrder.DUE_DATE:
            # None due dates at the end
            return sorted(
                tasks,
                key=lambda t: (t.due is None, self._to_date(t.due) if t.due else date.max),
            )
        elif order == SortOrder.PRIORITY:
            return sorted(tasks, key=lambda t: t.priority.value)
        elif order == SortOrder.CREATED:
            return sorted(tasks, key=lambda t: t.created_at, reverse=True)
        elif order == SortOrder.PROJECT:
            return sorted(tasks, key=lambda t: t.project_ref or "")
        elif order == SortOrder.ALPHABETICAL:
            return sorted(tasks, key=lambda t: t.title.lower())

        return tasks

    def _matches_project(self, task: TaskEntity, project_name: str) -> bool:
        """Check if task belongs to project."""
        if not task.project_ref:
            return False

        # Check by project ref
        if task.project_ref.lower() == project_name.lower():
            return True

        # Check by project name
        for project in self._projects.values():
            if project.name.lower() == project_name.lower():
                ext_id = project.ext.get(self.backend.backend_id if self.backend else "", {}).get("id")
                if ext_id == task.project_ref:
                    return True

        return False

    def _resolve_project_id(self, name_or_id: str | None) -> str | None:
        """Resolve project name to external ID."""
        if not name_or_id:
            return None

        project = self.get_project(name_or_id)
        if project and self.backend:
            return project.ext.get(self.backend.backend_id, {}).get("id")
        return name_or_id

    def _to_date(self, d: date | datetime) -> date:
        """Convert datetime to date."""
        if isinstance(d, datetime):
            return d.date()
        return d

    def _date_equals(self, d: date | datetime, target: date) -> bool:
        """Check if date equals target."""
        return self._to_date(d) == target


def get_task_manager(backend: ITaskBackend | None = None) -> TaskManager:
    """Get a configured TaskManager instance.

    Args:
        backend: Task backend implementation. Required -- no default backend
                 is configured. Implement ITaskBackend for your task system.

    Raises:
        ValueError: If no backend is provided.
    """
    if backend is None:
        raise ValueError(
            "No task backend provided. Pass an ITaskBackend implementation "
            "to get_task_manager()."
        )
    return TaskManager(backend=backend)
