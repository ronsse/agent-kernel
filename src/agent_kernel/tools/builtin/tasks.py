"""Built-in task tool implementations.

Provides task management functionality backed by SQLite.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.config import get_settings
from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.integrations.task_sync_service import TaskSyncConfig, TaskSyncService
from agent_kernel.memory.graph_store import SQLiteGraphStore

logger = structlog.get_logger(__name__)


@dataclass
class TaskRecord:
    """A task record."""

    task_id: str
    title: str
    description: str
    status: str
    priority: str
    due_date: str | None
    project_id: str | None
    tags: list[str]
    parent_task_id: str | None
    created_at: str
    updated_at: str
    completed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "due_date": self.due_date,
            "project_id": self.project_id,
            "tags": self.tags,
            "parent_task_id": self.parent_task_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }


class TaskStore:
    """SQLite-backed task store."""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        """Initialize task store.

        Args:
            db_path: Path to SQLite database, or :memory: for in-memory.
        """
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_schema(self) -> None:
        """Initialize database schema."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                priority TEXT DEFAULT 'medium',
                due_date TEXT,
                project_id TEXT,
                tags_json TEXT DEFAULT '[]',
                parent_task_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date)
        """)
        conn.commit()

    def create(
        self,
        title: str,
        description: str = "",
        due_date: str | None = None,
        priority: str = "medium",
        project_id: str | None = None,
        tags: list[str] | None = None,
        parent_task_id: str | None = None,
    ) -> TaskRecord:
        """Create a new task."""
        task_id = generate_ulid()
        now = utc_now().isoformat()

        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO tasks
            (task_id, title, description, status, priority, due_date,
             project_id, tags_json, parent_task_id, created_at, updated_at)
            VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                title,
                description,
                priority,
                due_date,
                project_id,
                json.dumps(tags or []),
                parent_task_id,
                now,
                now,
            ),
        )
        conn.commit()

        logger.info("task_created", task_id=task_id, title=title)

        return TaskRecord(
            task_id=task_id,
            title=title,
            description=description,
            status="open",
            priority=priority,
            due_date=due_date,
            project_id=project_id,
            tags=tags or [],
            parent_task_id=parent_task_id,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )

    def get(self, task_id: str) -> TaskRecord | None:
        """Get a task by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()

        if row is None:
            return None

        return self._row_to_record(row)

    def update(
        self,
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        due_date: str | None = None,
        priority: str | None = None,
        project_id: str | None = None,
        tags: list[str] | None = None,
    ) -> TaskRecord | None:
        """Update a task."""
        task = self.get(task_id)
        if task is None:
            return None

        updates = []
        values = []

        if title is not None:
            updates.append("title = ?")
            values.append(title)
        if description is not None:
            updates.append("description = ?")
            values.append(description)
        if due_date is not None:
            updates.append("due_date = ?")
            values.append(due_date)
        if priority is not None:
            updates.append("priority = ?")
            values.append(priority)
        if project_id is not None:
            updates.append("project_id = ?")
            values.append(project_id)
        if tags is not None:
            updates.append("tags_json = ?")
            values.append(json.dumps(tags))

        if not updates:
            return task

        now = utc_now().isoformat()
        updates.append("updated_at = ?")
        values.append(now)
        values.append(task_id)

        conn = self._get_conn()
        conn.execute(
            f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?",
            values,
        )
        conn.commit()

        logger.info("task_updated", task_id=task_id)

        return self.get(task_id)

    def complete(self, task_id: str) -> TaskRecord | None:
        """Mark a task as completed."""
        task = self.get(task_id)
        if task is None:
            return None

        now = utc_now().isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE tasks
            SET status = 'completed', completed_at = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (now, now, task_id),
        )
        conn.commit()

        logger.info("task_completed", task_id=task_id)

        return self.get(task_id)

    def reopen(self, task_id: str) -> TaskRecord | None:
        """Reopen a completed task."""
        task = self.get(task_id)
        if task is None:
            return None

        now = utc_now().isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE tasks
            SET status = 'open', completed_at = NULL, updated_at = ?
            WHERE task_id = ?
            """,
            (now, task_id),
        )
        conn.commit()

        logger.info("task_reopened", task_id=task_id)

        return self.get(task_id)

    def delete(self, task_id: str) -> bool:
        """Delete a task."""
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM tasks WHERE task_id = ?",
            (task_id,),
        )
        conn.commit()

        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("task_deleted", task_id=task_id)

        return deleted

    def list(
        self,
        status: str = "open",
        project_id: str | None = None,
        due_before: str | None = None,
        due_after: str | None = None,
        limit: int = 50,
        include_completed: bool = False,
    ) -> tuple[list[TaskRecord], int]:
        """List tasks with filters.

        Returns:
            Tuple of (tasks, total_count).
        """
        conn = self._get_conn()

        # Build query
        where_clauses = []
        params: list[Any] = []

        if status == "open":
            where_clauses.append("status != 'completed'")
        elif status == "completed":
            where_clauses.append("status = 'completed'")
        # status == "all" has no filter

        if project_id:
            where_clauses.append("project_id = ?")
            params.append(project_id)

        if due_before:
            where_clauses.append("due_date < ?")
            params.append(due_before)

        if due_after:
            where_clauses.append("due_date > ?")
            params.append(due_after)

        if not include_completed and status != "completed":
            where_clauses.append("status != 'completed'")

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Get total count
        count_row = conn.execute(
            f"SELECT COUNT(*) as count FROM tasks WHERE {where_sql}",
            params,
        ).fetchone()
        total_count = count_row["count"] if count_row else 0

        # Get tasks
        rows = conn.execute(
            f"""
            SELECT * FROM tasks
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()

        tasks = [self._row_to_record(row) for row in rows]

        return tasks, total_count

    def search(
        self, query: str, limit: int = 20, include_completed: bool = False
    ) -> list[TaskRecord]:
        """Search tasks by title/description."""
        conn = self._get_conn()
        pattern = f"%{query}%"

        status_filter = ""
        params: list[Any] = [pattern, pattern]
        if not include_completed:
            status_filter = "AND status != 'completed'"

        rows = conn.execute(
            f"""
            SELECT * FROM tasks
            WHERE (title LIKE ? OR description LIKE ?)
            {status_filter}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()

        return [self._row_to_record(row) for row in rows]

    def clear(self) -> None:
        """Clear all tasks (for testing)."""
        conn = self._get_conn()
        conn.execute("DELETE FROM tasks")
        conn.commit()

    def _row_to_record(self, row: sqlite3.Row) -> TaskRecord:
        """Convert a database row to TaskRecord."""
        return TaskRecord(
            task_id=row["task_id"],
            title=row["title"],
            description=row["description"] or "",
            status=row["status"],
            priority=row["priority"],
            due_date=row["due_date"],
            project_id=row["project_id"],
            tags=json.loads(row["tags_json"]) if row["tags_json"] else [],
            parent_task_id=row["parent_task_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )


# Global task store instance (can be replaced for testing)
_task_store: TaskStore | None = None


def get_task_store(db_path: Path | str | None = None) -> TaskStore:
    """Get the task store instance."""
    global _task_store
    if _task_store is None:
        _task_store = TaskStore(db_path or ":memory:")
    return _task_store


def set_task_store(store: TaskStore) -> None:
    """Set the task store instance (for testing)."""
    global _task_store
    _task_store = store


# =============================================================================
# Capability Functions (exposed via Tool Broker)
# =============================================================================


def list_tasks(
    status: str = "open",
    project_id: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    limit: int = 50,
    include_completed: bool = False,
) -> dict[str, Any]:
    """List tasks with optional filters.

    Args:
        status: Filter by status (open, completed, all).
        project_id: Filter by project ID.
        due_before: Filter tasks due before this date.
        due_after: Filter tasks due after this date.
        limit: Maximum tasks to return.
        include_completed: Include completed tasks.

    Returns:
        Dict with tasks list and total count.
    """
    store = get_task_store()
    tasks, total_count = store.list(
        status=status,
        project_id=project_id,
        due_before=due_before,
        due_after=due_after,
        limit=limit,
        include_completed=include_completed,
    )

    return {
        "tasks": [t.to_dict() for t in tasks],
        "total_count": total_count,
    }


def create_task(
    title: str,
    description: str | None = None,
    due_date: str | None = None,
    due_string: str | None = None,
    priority: str = "medium",
    project: str | None = None,
    project_id: str | None = None,
    tags: list[str] | None = None,
    labels: list[str] | None = None,
    scope: str | None = None,
    parent_task_id: str | None = None,
) -> dict[str, Any]:
    """Create a new task.

    Args:
        title: Task title (required).
        description: Detailed description.
        due_date: Due date in YYYY-MM-DD format.
        priority: Priority level (low, medium, high).
        project_id: Associated project ID.
        tags: Tags for categorization.
        parent_task_id: Parent task ID for subtasks.

    Returns:
        Dict with created task details.
    """
    store = get_task_store()
    resolved_project_id = project_id or project
    resolved_tags = list(tags or [])
    if labels:
        resolved_tags.extend(labels)
    if scope:
        resolved_tags.append(f"scope:{scope}")
    resolved_due = due_date or due_string
    task = store.create(
        title=title,
        description=description or "",
        due_date=resolved_due,
        priority=priority,
        project_id=resolved_project_id,
        tags=resolved_tags,
        parent_task_id=parent_task_id,
    )

    return {
        "task_id": task.task_id,
        "title": task.title,
        "created_at": task.created_at,
    }


def get_task(task_id: str) -> dict[str, Any]:
    """Get a task by ID.

    Args:
        task_id: The task ID.

    Returns:
        Dict with task details or error.
    """
    store = get_task_store()
    task = store.get(task_id)

    if task is None:
        return {"error": "Task not found", "task_id": task_id}

    return {"task": task.to_dict()}


def update_task(
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    due_date: str | None = None,
    due_string: str | None = None,
    priority: str | None = None,
    project: str | None = None,
    project_id: str | None = None,
    tags: list[str] | None = None,
    labels: list[str] | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """Update a task.

    Args:
        task_id: The task ID.
        title: New title.
        description: New description.
        due_date: New due date.
        priority: New priority.
        project_id: New project ID.
        tags: New tags.

    Returns:
        Dict with updated task details or error.
    """
    store = get_task_store()
    resolved_project_id = project_id or project
    resolved_tags = list(tags or [])
    if labels:
        resolved_tags.extend(labels)
    if scope:
        resolved_tags.append(f"scope:{scope}")
    resolved_due = due_date or due_string
    task = store.update(
        task_id=task_id,
        title=title,
        description=description,
        due_date=resolved_due,
        priority=priority,
        project_id=resolved_project_id,
        tags=resolved_tags,
    )

    if task is None:
        return {"error": "Task not found", "task_id": task_id}

    return {"task": task.to_dict()}


def complete_task(task_id: str) -> dict[str, Any]:
    """Mark a task as completed.

    Args:
        task_id: The task ID.

    Returns:
        Dict with completed task details or error.
    """
    store = get_task_store()
    task = store.complete(task_id)

    if task is None:
        return {"error": "Task not found", "task_id": task_id}

    return {"task": task.to_dict()}


def delete_task(task_id: str) -> dict[str, Any]:
    """Delete a task.

    Args:
        task_id: The task ID.

    Returns:
        Dict with deletion status.
    """
    store = get_task_store()
    deleted = store.delete(task_id)

    return {"deleted": deleted, "task_id": task_id}


def search_tasks(
    query: str, limit: int = 20, include_completed: bool = False
) -> dict[str, Any]:
    """Search tasks by title or description.

    Args:
        query: Search query.
        limit: Maximum results.

    Returns:
        Dict with matching tasks.
    """
    store = get_task_store()
    tasks = store.search(query, limit=limit, include_completed=include_completed)

    return {
        "tasks": [t.to_dict() for t in tasks],
        "total_count": len(tasks),
    }


def clear_tasks() -> None:
    """Clear all tasks (for testing)."""
    store = get_task_store()
    store.clear()


def sync_tasks(
    adapter_id: str,
    operation: str = "sync_all",
    kernel_task_id: str | None = None,
    external_id: str | None = None,
    task: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sync kernel tasks to an external system.

    Args:
        adapter_id: Target adapter ID (e.g., "linear").
        operation: Operation type (default: sync_all).
        kernel_task_id: Optional kernel task ID for single-task ops.
        external_id: Optional external task ID for single-task ops.
        task: Optional task payload for single-task ops.
        config: Optional sync config (projects, tags, include_completed, dry_run).
    """
    if operation != "sync_all":
        return {
            "success": False,
            "operation": operation,
            "error": "Only sync_all is supported by tasks.sync@v1",
        }

    # No task sync adapters are currently registered.
    # To add one, implement TaskSyncAdapter and register it here.
    return {
        "success": False,
        "operation": operation,
        "error": f"No task sync adapter registered for: {adapter_id}. "
        "Implement TaskSyncAdapter and register it to enable sync.",
    }
