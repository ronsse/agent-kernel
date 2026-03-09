"""Task Adapter - Tool implementations for task management (v1.0.5).

Provides the actual implementations for task management capabilities
that agents can invoke through the Tool Broker.

These functions work with the TaskManager service to:
- List/filter/search tasks
- Create, update, complete tasks
- Move tasks between projects
- Manage labels and organization

All write operations return TaskAction objects that require approval.

References:
- Design Patch v1.0.5: Task Integration
"""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog

from agent_kernel.core.schemas.task import TaskPriority, TaskScope, TaskStatus
from agent_kernel.services.task_manager import (
    SortOrder,
    TaskFilter,
    TaskManager,
    get_task_manager,
)

logger = structlog.get_logger(__name__)

# Module-level singleton for the task manager
_task_manager: TaskManager | None = None


def _get_manager() -> TaskManager:
    """Get or create the task manager singleton."""
    global _task_manager
    if _task_manager is None:
        _task_manager = get_task_manager()
        if not _task_manager.is_synced():
            _task_manager.sync()
    return _task_manager


# ─────────────────────────────────────────────────────────────────
# Capability: tasks.list@v1
# ─────────────────────────────────────────────────────────────────


def list_tasks(
    filter: str | None = None,
    project: str | None = None,
    labels: list[str] | None = None,
    scope: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    search: str | None = None,
    sort: str = "due_date",
    limit: int = 50,
) -> dict[str, Any]:
    """List tasks with optional filtering.

    Args:
        filter: Predefined filter (all, today, tomorrow, etc.)
        project: Filter by project name
        labels: Filter by labels (AND logic)
        scope: Filter by scope (work/personal)
        priority: Filter by priority (p1-p4)
        status: Filter by status (open/completed)
        search: Text search
        sort: Sort order
        limit: Max results

    Returns:
        Dict with tasks list and metadata.
    """
    manager = _get_manager()

    # Parse filter enum
    task_filter = None
    if filter:
        try:
            task_filter = TaskFilter(filter)
        except ValueError:
            pass

    # Parse sort order
    try:
        sort_order = SortOrder(sort)
    except ValueError:
        sort_order = SortOrder.DUE_DATE

    # Parse other enums
    task_scope = TaskScope(scope) if scope else None
    task_priority = TaskPriority(priority) if priority else None
    task_status = TaskStatus(status) if status else None

    view = manager.get_tasks(
        filter=task_filter,
        project=project,
        labels=labels,
        scope=task_scope,
        priority=task_priority,
        status=task_status,
        search=search,
        sort=sort_order,
        limit=limit,
    )

    return {
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status.value,
                "priority": t.priority.value,
                "due": str(t.due) if t.due else None,
                "project": t.project_ref,
                "labels": t.labels,
                "scope": t.scope.value,
                "is_overdue": t.is_overdue,
                "is_recurring": t.is_recurring,
            }
            for t in view.tasks
        ],
        "count": view.count,
        "open_count": view.open_count,
        "completed_count": view.completed_count,
        "filter_applied": view.filter_applied,
        "sort_order": view.sort_order,
    }


# ─────────────────────────────────────────────────────────────────
# Capability: tasks.create@v1
# ─────────────────────────────────────────────────────────────────


def create_task(
    title: str,
    description: str = "",
    project: str | None = None,
    priority: str = "p4",
    scope: str = "personal",
    labels: list[str] | None = None,
    due_date: str | None = None,
    due_string: str | None = None,
) -> dict[str, Any]:
    """Create a new task.

    Args:
        title: Task title
        description: Extended description
        project: Project name
        priority: Priority (p1-p4)
        scope: Scope (work/personal)
        labels: Labels to apply
        due_date: Due date (YYYY-MM-DD)
        due_string: Natural language due

    Returns:
        Dict with action details for approval.
    """
    manager = _get_manager()

    # Parse enums
    task_priority = TaskPriority(priority)
    task_scope = TaskScope(scope)

    # Parse due date
    due = None
    if due_date:
        try:
            due = date.fromisoformat(due_date)
        except ValueError:
            pass

    action = manager.create_task(
        title=title,
        description=description,
        project=project,
        priority=task_priority,
        scope=task_scope,
        labels=labels,
        due=due,
        due_string=due_string,
    )

    return {
        "action_id": action.id,
        "action_type": action.action_type,
        "task_title": action.task_title,
        "requires_approval": action.requires_approval,
        "parameters": action.parameters,
        "message": f"Task '{title}' will be created. Approval required."
        if action.requires_approval
        else f"Task '{title}' created.",
    }


# ─────────────────────────────────────────────────────────────────
# Capability: tasks.update@v1
# ─────────────────────────────────────────────────────────────────


def update_task(
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    labels: list[str] | None = None,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
    due_date: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Update an existing task.

    Args:
        task_id: Task ID to update
        title: New title
        description: New description
        priority: New priority
        labels: Replace all labels
        add_labels: Labels to add
        remove_labels: Labels to remove
        due_date: New due date
        project: Move to project

    Returns:
        Dict with action details.
    """
    manager = _get_manager()

    # Get current task
    task = manager.get_task(task_id)
    if not task:
        return {
            "error": f"Task not found: {task_id}",
            "success": False,
        }

    # Handle label modifications
    final_labels = labels
    if add_labels and not labels:
        final_labels = list(set(task.labels + add_labels))
    if remove_labels and final_labels:
        final_labels = [l for l in final_labels if l not in remove_labels]
    elif remove_labels and not labels:
        final_labels = [l for l in task.labels if l not in remove_labels]

    # Parse priority
    task_priority = TaskPriority(priority) if priority else None

    # Parse due date
    due = None
    if due_date:
        try:
            due = date.fromisoformat(due_date)
        except ValueError:
            pass

    action = manager.update_task(
        task_id=task_id,
        title=title,
        description=description,
        priority=task_priority,
        labels=final_labels,
        due=due,
        project=project,
    )

    if not action:
        return {
            "error": "Failed to create update action",
            "success": False,
        }

    return {
        "action_id": action.id,
        "task_id": task_id,
        "task_title": task.title,
        "requires_approval": action.requires_approval,
        "changes": {
            k: v for k, v in action.parameters.items() if v is not None
        },
    }


# ─────────────────────────────────────────────────────────────────
# Capability: tasks.complete@v1
# ─────────────────────────────────────────────────────────────────


def complete_task(task_id: str) -> dict[str, Any]:
    """Mark a task as complete.

    Args:
        task_id: Task ID to complete

    Returns:
        Dict with action details.
    """
    manager = _get_manager()

    task = manager.get_task(task_id)
    if not task:
        return {
            "error": f"Task not found: {task_id}",
            "success": False,
        }

    action = manager.complete_task(task_id)
    if not action:
        return {
            "error": "Failed to create complete action",
            "success": False,
        }

    return {
        "action_id": action.id,
        "task_id": task_id,
        "task_title": task.title,
        "requires_approval": action.requires_approval,
        "message": f"Task '{task.title}' will be marked complete.",
    }


# ─────────────────────────────────────────────────────────────────
# Capability: tasks.move@v1
# ─────────────────────────────────────────────────────────────────


def move_task(
    task_id: str,
    to_project: str,
    to_section: str | None = None,
) -> dict[str, Any]:
    """Move a task to a different project.

    Args:
        task_id: Task ID to move
        to_project: Target project name
        to_section: Target section (optional)

    Returns:
        Dict with action details.
    """
    manager = _get_manager()

    task = manager.get_task(task_id)
    if not task:
        return {
            "error": f"Task not found: {task_id}",
            "success": False,
        }

    action = manager.move_task(
        task_id=task_id,
        to_project=to_project,
        to_section=to_section,
    )

    if not action:
        return {
            "error": "Failed to create move action",
            "success": False,
        }

    return {
        "action_id": action.id,
        "task_id": task_id,
        "task_title": task.title,
        "from_project": task.project_ref,
        "to_project": to_project,
        "to_section": to_section,
        "requires_approval": action.requires_approval,
    }


# ─────────────────────────────────────────────────────────────────
# Capability: tasks.projects@v1
# ─────────────────────────────────────────────────────────────────


def list_projects(
    include_archived: bool = False,
    include_task_counts: bool = True,
) -> dict[str, Any]:
    """List all projects.

    Args:
        include_archived: Include archived projects
        include_task_counts: Include task counts

    Returns:
        Dict with projects list.
    """
    manager = _get_manager()
    projects = manager.get_projects()

    # Filter archived
    if not include_archived:
        projects = [p for p in projects if not p.is_archived]

    result_projects = []
    for p in projects:
        proj_data = {
            "id": p.id,
            "name": p.name,
            "scope": p.scope.value,
            "is_inbox": p.is_inbox,
            "is_archived": p.is_archived,
        }

        if include_task_counts:
            tasks = manager.get_tasks_by_project(p.name)
            proj_data["task_count"] = len(tasks)
            proj_data["open_count"] = sum(
                1 for t in tasks if t.status == TaskStatus.OPEN
            )

        result_projects.append(proj_data)

    return {
        "projects": result_projects,
        "count": len(result_projects),
    }


# ─────────────────────────────────────────────────────────────────
# Capability: tasks.search@v1
# ─────────────────────────────────────────────────────────────────


def search_tasks(
    query: str,
    limit: int = 20,
    include_completed: bool = False,
) -> dict[str, Any]:
    """Search tasks by text.

    Args:
        query: Search query
        limit: Max results
        include_completed: Include completed tasks

    Returns:
        Dict with matching tasks.
    """
    manager = _get_manager()

    tasks = manager.search_tasks(query, limit=limit * 2)  # Get extra for filtering

    # Filter by status if needed
    if not include_completed:
        tasks = [t for t in tasks if t.status == TaskStatus.OPEN]

    tasks = tasks[:limit]

    return {
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status.value,
                "priority": t.priority.value,
                "project": t.project_ref,
                "labels": t.labels,
            }
            for t in tasks
        ],
        "count": len(tasks),
        "query": query,
    }


# ─────────────────────────────────────────────────────────────────
# Capability: tasks.stats@v1
# ─────────────────────────────────────────────────────────────────


def get_task_stats() -> dict[str, Any]:
    """Get task statistics.

    Returns:
        Dict with task statistics.
    """
    manager = _get_manager()
    return manager.get_stats()


# ─────────────────────────────────────────────────────────────────
# Action Execution (for Tool Broker)
# ─────────────────────────────────────────────────────────────────


def approve_and_execute_action(action_id: str) -> dict[str, Any]:
    """Approve and execute a pending action.

    Args:
        action_id: Action ID to approve and execute

    Returns:
        Dict with execution result.
    """
    manager = _get_manager()

    if manager.approve_action(action_id):
        for action in manager.get_pending_actions():
            if action.id == action_id:
                success = manager.execute_action(action)
                return {
                    "success": success,
                    "action_id": action_id,
                    "result": action.result,
                    "task_id": action.task_id,
                }

    return {
        "success": False,
        "error": f"Action not found: {action_id}",
    }


def get_pending_actions() -> dict[str, Any]:
    """Get all pending actions waiting for approval.

    Returns:
        Dict with pending actions.
    """
    manager = _get_manager()
    pending = manager.get_pending_actions()

    return {
        "actions": [
            {
                "id": a.id,
                "type": a.action_type,
                "task_title": a.task_title,
                "task_id": a.task_id,
                "parameters": a.parameters,
                "requires_approval": a.requires_approval,
                "created_at": a.created_at.isoformat(),
            }
            for a in pending
        ],
        "count": len(pending),
    }
