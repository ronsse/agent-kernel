"""Task Sync Service - Orchestrates task synchronization.

Provides high-level task sync functionality:
1. Reads tasks from kernel graph store
2. Maps to ExternalTask format
3. Syncs to configured external adapters
4. Tracks sync state for idempotency

Integrates with the approval workflow for external writes.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.graph import NodeType
from agent_kernel.integrations.task_sync import (
    ExternalTask,
    SyncDirection,
    SyncSummary,
    TaskSyncAdapter,
)
from agent_kernel.core.schemas.task import TaskPriority, TaskStatus

if TYPE_CHECKING:
    from agent_kernel.memory.graph_store import GraphStore

logger = structlog.get_logger(__name__)


def _parse_due_date(value: Any) -> date | None:
    """Parse a due date from various input formats.

    Handles: date objects, datetime objects, ISO strings, YYYY-MM-DD strings.
    Returns None and logs a warning for unparseable values.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        # Try ISO format first (YYYY-MM-DD or full ISO datetime)
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
        # Try datetime ISO format (e.g., "2026-01-20T10:00:00")
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            pass
        logger.warning(
            "unparseable_due_date",
            value=str(value)[:50],
            msg="Could not parse due_date string",
        )
        return None
    if isinstance(value, (int, float)):
        # Unix timestamp
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).date()
        except (ValueError, OSError):
            logger.warning("unparseable_due_date_timestamp", value=value)
            return None
    logger.warning(
        "unexpected_due_date_type",
        type=type(value).__name__,
        msg="due_date is not a recognized type",
    )
    return None


@dataclass
class TaskSyncState:
    """Tracks sync state for a task."""

    kernel_task_id: str
    adapter_id: str
    external_id: str | None = None
    last_synced_at: datetime | None = None
    sync_hash: str | None = None  # Hash of synced content for change detection


@dataclass
class TaskSyncConfig:
    """Configuration for task sync."""

    # Which adapters to sync to
    adapters: list[str] = field(default_factory=list)

    # Filters
    projects: list[str] | None = None  # Sync only these projects
    tags: list[str] | None = None  # Sync only tasks with these tags
    include_completed: bool = False
    include_linked_only: bool = False  # Include tasks already linked to adapter

    # Behavior
    sync_direction: SyncDirection = SyncDirection.PUSH
    dry_run: bool = False  # Log what would be synced without syncing
    update_only: bool = False  # Only update linked tasks; never create
    enrichment_mode: bool = False  # Only update high-confidence fields
    max_creates: int = 10
    max_updates: int = 25


class TaskSyncService:
    """Service for syncing tasks between kernel and external systems.

    Reads tasks from the kernel's graph store and syncs them to
    configured external task adapters (Linear, Jira, etc.).
    """

    def __init__(
        self,
        graph_store: GraphStore,
        adapters: dict[str, TaskSyncAdapter] | None = None,
    ) -> None:
        """Initialize task sync service.

        Args:
            graph_store: Graph store containing task nodes.
            adapters: Dict of adapter_id -> adapter instance.
        """
        self.graph_store = graph_store
        self._adapters: dict[str, TaskSyncAdapter] = adapters or {}

        # Track sync state per task/adapter pair
        self._sync_state: dict[str, TaskSyncState] = {}

        logger.info(
            "task_sync_service_initialized",
            adapter_count=len(self._adapters),
        )

    def register_adapter(self, adapter: TaskSyncAdapter) -> None:
        """Register a task sync adapter.

        Args:
            adapter: Adapter to register.
        """
        self._adapters[adapter.adapter_id] = adapter
        logger.info(
            "adapter_registered",
            adapter_id=adapter.adapter_id,
            display_name=adapter.display_name,
        )

    def get_adapter(self, adapter_id: str) -> TaskSyncAdapter | None:
        """Get an adapter by ID.

        Args:
            adapter_id: Adapter identifier.

        Returns:
            Adapter if found, None otherwise.
        """
        return self._adapters.get(adapter_id)

    def _get_sync_state(
        self, kernel_task_id: str, adapter_id: str
    ) -> TaskSyncState | None:
        state = self._sync_state.get(kernel_task_id)
        if not state or state.adapter_id != adapter_id:
            return None
        return state

    def _load_sync_state_from_props(
        self,
        kernel_task_id: str,
        props: dict[str, Any],
        adapter_id: str,
    ) -> TaskSyncState | None:
        external_sync = props.get("external_sync")
        if not isinstance(external_sync, dict):
            return None
        adapter_state = external_sync.get(adapter_id)
        if not isinstance(adapter_state, dict):
            return None
        sync_hash = adapter_state.get("sync_hash")
        if not sync_hash:
            return None
        last_synced_at = None
        last_synced_at_raw = adapter_state.get("last_synced_at")
        if isinstance(last_synced_at_raw, str):
            with contextlib.suppress(ValueError):
                last_synced_at = datetime.fromisoformat(last_synced_at_raw)
        external_ids = props.get("external_ids", {})
        external_id = ""
        if isinstance(external_ids, dict):
            external_id = str(external_ids.get(adapter_id, "") or "")
        return TaskSyncState(
            kernel_task_id=kernel_task_id,
            adapter_id=adapter_id,
            external_id=external_id or None,
            last_synced_at=last_synced_at,
            sync_hash=sync_hash,
        )

    def _compute_sync_hash(self, task: ExternalTask) -> str:
        raw_data = task.raw_data if isinstance(task.raw_data, dict) else {}
        normalized_raw: dict[str, Any] = {}
        for key, value in raw_data.items():
            if key in {"enrichment_mode", "update_only"}:
                continue
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                normalized_raw[key] = sorted(value)
            else:
                normalized_raw[key] = value
        payload = {
            "text": task.text,
            "description": task.description,
            "status": task.status.value,
            "is_complete": task.is_complete,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "priority": task.priority.value,
            "project_id": task.project_id,
            "project_name": task.project_name,
            "tags": sorted(task.tags),
            "contexts": sorted(task.contexts),
            "source_note_id": task.source_note_id,
            "source_path": task.source_path,
            "raw_data": normalized_raw,
        }
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    async def get_tasks_from_graph(
        self,
        projects: list[str] | None = None,  # noqa: ARG002 - Reserved for future use
        tags: list[str] | None = None,
        include_completed: bool = False,
        include_linked_only: bool = False,
        adapter_id: str | None = None,
    ) -> list[ExternalTask]:
        """Read tasks from kernel graph store.

        Args:
            projects: Filter by project names.
            tags: Filter by tag names.
            include_completed: Include completed tasks.
            include_linked_only: Only include tasks linked to adapter.

        Returns:
            List of ExternalTask objects.
        """
        # Query task nodes from graph
        nodes = self.graph_store.query(
            node_type=NodeType.TASK.value,
            limit=1000,
        )

        tasks: list[ExternalTask] = []

        for node in nodes:
            props = node.get("properties", {})

            # Filter by completion status
            is_complete = props.get("is_complete", False)
            if is_complete and not include_completed:
                continue

            # Get sync state to include external_id if known
            task_id = props.get("task_id", node.get("node_id", ""))
            sync_state = None
            external_id = ""
            external_ids = props.get("external_ids", {})
            if adapter_id and isinstance(external_ids, dict):
                external_id = str(external_ids.get(adapter_id, "") or "")
            if adapter_id:
                sync_state = self._get_sync_state(task_id, adapter_id)
                if not sync_state:
                    sync_state = self._load_sync_state_from_props(
                        task_id, props, adapter_id
                    )
                    if sync_state:
                        self._sync_state[task_id] = sync_state
                if not external_id and sync_state:
                    external_id = sync_state.external_id or ""
            else:
                sync_state = self._sync_state.get(task_id)
            if not external_id and sync_state:
                external_id = sync_state.external_id or ""
            is_linked = bool(external_id)

            # Filter by export markers or linked-only mode
            should_sync = bool(props.get("should_sync"))
            note_export_todo = bool(props.get("note_export_todo"))
            if include_linked_only and adapter_id:
                if not is_linked:
                    continue
            else:
                if not (should_sync or note_export_todo):
                    continue

            # Filter by tags
            task_tags = props.get("tags", [])
            if tags and not any(t in task_tags for t in tags):
                continue

            # Parse status
            status_str = props.get("status", "incomplete")
            try:
                status = TaskStatus(status_str)
            except ValueError:
                status = TaskStatus.OPEN

            # Parse priority
            priority_str = props.get("priority", "none")
            try:
                priority = TaskPriority(priority_str)
            except ValueError:
                priority = TaskPriority.P4

            # Parse due date
            due_date: date | None = None
            due_str = props.get("due_date")
            if due_str:
                with contextlib.suppress(ValueError):
                    due_date = datetime.fromisoformat(due_str).date()

            task = ExternalTask(
                external_id=external_id,
                kernel_task_id=task_id,
                text=props.get("text", ""),
                status=status,
                is_complete=is_complete,
                due_date=due_date,
                priority=priority,
                tags=task_tags,
                project_name=props.get("project"),
                contexts=props.get("contexts", []),
                source_note_id=props.get("source_note_id"),
                source_path=props.get("source_path"),
                raw_data={
                    "meeting_group_id": props.get("meeting_group_id"),
                    "meeting_title": props.get("meeting_title"),
                    "meeting_date": props.get("meeting_date"),
                    "note_tags": props.get("note_tags", []),
                    "note_summary": props.get("note_summary"),
                    "note_export_todo": props.get("note_export_todo"),
                },
            )
            if adapter_id and task.raw_data.get("meeting_group_id") and self.graph_store:
                note_node = self.graph_store.get_node(
                    f"note:{task.raw_data['meeting_group_id']}"
                )
                note_props = note_node.get("properties", {}) if note_node else {}
                meeting_parent_id = (
                    note_props.get(f"{adapter_id}_meeting_parent_id")
                    or note_props.get("meeting_parent_id")
                )
                if meeting_parent_id:
                    task.raw_data["meeting_parent_id"] = meeting_parent_id
            tasks.append(task)

        logger.debug(
            "tasks_read_from_graph",
            count=len(tasks),
            include_completed=include_completed,
        )

        return tasks

    async def sync_to_adapter(
        self,
        adapter_id: str,
        config: TaskSyncConfig | None = None,
    ) -> SyncSummary:
        """Sync tasks to a specific adapter.

        Args:
            adapter_id: ID of adapter to sync to.
            config: Optional sync configuration.

        Returns:
            SyncSummary with operation counts.
        """
        adapter = self._adapters.get(adapter_id)
        if not adapter:
            msg = f"Adapter not found: {adapter_id}"
            raise ValueError(msg)

        config = config or TaskSyncConfig()

        # Get tasks from graph
        tasks = await self.get_tasks_from_graph(
            projects=config.projects,
            tags=config.tags,
            include_completed=config.include_completed,
            include_linked_only=config.include_linked_only,
            adapter_id=adapter_id,
        )
        total_tasks = len(tasks)
        skipped_update_only = 0
        if config.update_only:
            update_candidates = [task for task in tasks if task.external_id]
            skipped_update_only = total_tasks - len(update_candidates)
            tasks = update_candidates

        sync_tasks: list[ExternalTask] = []
        skipped = skipped_update_only
        task_hashes: dict[str, str] = {}
        for task in tasks:
            kernel_task_id = task.kernel_task_id
            if not kernel_task_id:
                sync_tasks.append(task)
                continue
            current_hash = self._compute_sync_hash(task)
            task_hashes[kernel_task_id] = current_hash
            if task.external_id:
                sync_state = self._get_sync_state(kernel_task_id, adapter_id)
                if sync_state and sync_state.sync_hash == current_hash:
                    skipped += 1
                    continue
            sync_tasks.append(task)

        create_count = sum(1 for task in sync_tasks if not task.external_id)
        update_count = len(sync_tasks) - create_count
        if create_count > config.max_creates:
            raise ValueError(
                f"BulkWriteGate: create_count={create_count} exceeds "
                f"max_creates={config.max_creates}"
            )
        if update_count > config.max_updates:
            raise ValueError(
                f"BulkWriteGate: update_count={update_count} exceeds "
                f"max_updates={config.max_updates}"
            )

        if config.dry_run:
            logger.info(
                "dry_run_sync",
                adapter_id=adapter_id,
                task_count=len(sync_tasks),
            )
            return SyncSummary(
                started_at=utc_now(),
                completed_at=utc_now(),
                direction=SyncDirection.PUSH,
                total_tasks=total_tasks,
                skipped=total_tasks,
            )

        if not sync_tasks:
            return SyncSummary(
                started_at=utc_now(),
                completed_at=utc_now(),
                direction=SyncDirection.PUSH,
                total_tasks=total_tasks,
                skipped=skipped,
            )

        if config.enrichment_mode or config.update_only:
            for task in sync_tasks:
                raw = task.raw_data if isinstance(task.raw_data, dict) else {}
                raw["enrichment_mode"] = config.enrichment_mode
                raw["update_only"] = config.update_only
                task.raw_data = raw

        # Sync to adapter
        summary = await adapter.sync_to_external(sync_tasks)
        summary.total_tasks = total_tasks
        summary.skipped += skipped

        # Update sync state for successful syncs
        for result in summary.results:
            if result.success and result.kernel_task_id and result.external_id:
                sync_hash = task_hashes.get(result.kernel_task_id)
                self._sync_state[result.kernel_task_id] = TaskSyncState(
                    kernel_task_id=result.kernel_task_id,
                    adapter_id=adapter_id,
                    external_id=result.external_id,
                    last_synced_at=utc_now(),
                    sync_hash=sync_hash,
                )
                if self.graph_store:
                    node_id = f"task:{result.kernel_task_id}"
                    node = self.graph_store.get_node(node_id)
                    props = node.get("properties", {}) if node else {}
                    external_ids = props.get("external_ids", {})
                    if not isinstance(external_ids, dict):
                        external_ids = {}
                    external_ids[adapter_id] = result.external_id
                    props["external_ids"] = external_ids
                    external_sync = props.get("external_sync", {})
                    if not isinstance(external_sync, dict):
                        external_sync = {}
                    external_sync[adapter_id] = {
                        "sync_hash": sync_hash,
                        "last_synced_at": utc_now().isoformat(),
                    }
                    props["external_sync"] = external_sync
                    self.graph_store.upsert_node(
                        node_id=node_id,
                        node_type=NodeType.TASK.value,
                        properties=props,
                    )

        return summary

    async def sync_all(
        self,
        config: TaskSyncConfig | None = None,
    ) -> dict[str, SyncSummary]:
        """Sync tasks to all registered adapters.

        Args:
            config: Optional sync configuration.

        Returns:
            Dict of adapter_id -> SyncSummary.
        """
        results: dict[str, SyncSummary] = {}

        for adapter_id in self._adapters:
            try:
                summary = await self.sync_to_adapter(adapter_id, config)
                results[adapter_id] = summary
            except Exception:
                logger.exception("sync_to_adapter_failed", adapter_id=adapter_id)
                results[adapter_id] = SyncSummary(
                    started_at=utc_now(),
                    completed_at=utc_now(),
                    direction=SyncDirection.PUSH,
                    failed=1,
                )

        return results

    def get_sync_state(self, kernel_task_id: str) -> TaskSyncState | None:
        """Get sync state for a task.

        Args:
            kernel_task_id: Kernel task ID.

        Returns:
            TaskSyncState if found.
        """
        return self._sync_state.get(kernel_task_id)

    def get_all_sync_states(self) -> list[TaskSyncState]:
        """Get all sync states.

        Returns:
            List of all TaskSyncState objects.
        """
        return list(self._sync_state.values())


def create_task_sync_action(
    task: ExternalTask,
    adapter_id: str,
) -> dict[str, Any]:
    """Create an action request for task sync (for approval workflow).

    This creates a structured action that can be included in a Plan
    and executed via the Tool Broker with approval gates.

    Args:
        task: Task to sync.
        adapter_id: Target adapter.

    Returns:
        Dict suitable for ActionRequest.
    """
    return {
        "capability_name": "tasks.sync@v1",
        "args": {
            "adapter_id": adapter_id,
            "kernel_task_id": task.kernel_task_id,
            "external_id": task.external_id,
            "operation": "create" if not task.external_id else "update",
            "task": task.to_dict(),
        },
    }
