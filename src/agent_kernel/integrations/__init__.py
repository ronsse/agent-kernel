"""Integrations module - External system adapters (v1.0.5).

This module provides adapters for integrating with external systems:
- Task backends - ITaskBackend interface
- Task sync adapters
- Calendar adapters
- Note import adapters

All external writes are approval-gated per integration patterns.

v1.0.5 additions:
- ITaskBackend interface for pluggable task backends
- Task schemas (TaskEntity, ProjectEntity, etc.)
"""

from agent_kernel.integrations.calendar_sync import (
    CalendarAdapter,
    CalendarEvent,
    CalendarSyncSummary,
    EventStatus,
    EventVisibility,
    MemoryCalendarAdapter,
)
from agent_kernel.integrations.calendar_sync import (
    SyncOperation as CalendarSyncOperation,
)
from agent_kernel.integrations.calendar_sync import (
    SyncResult as CalendarSyncResult,
)
from agent_kernel.integrations.calendar_sync_service import (
    CalendarSyncConfig,
    CalendarSyncService,
    CalendarSyncState,
    create_calendar_event_action,
)

# v1.0.5: Task Backend Interface
from agent_kernel.integrations.task_backend import (
    BackendError,
    BackendTaskRef,
    ITaskBackend,
    TaskNotFoundError,
)

# v1.0.5: Task Sync Orchestrator
from agent_kernel.integrations.task_sync_orchestrator import (
    ConflictResolution,
    SyncConflict,
    SyncFieldPolicy,
    SyncState,
    TaskSyncOrchestrator,
)
from agent_kernel.integrations.task_sync_orchestrator import (
    SyncResult as OrchestratorSyncResult,
)
from agent_kernel.integrations.task_sync import (
    ExternalTask,
    MemoryTaskAdapter,
    SyncDirection,
    SyncResult,
    SyncSummary,
    TaskSyncAdapter,
)
from agent_kernel.integrations.task_sync_service import (
    TaskSyncConfig,
    TaskSyncService,
    TaskSyncState,
    create_task_sync_action,
)
__all__ = [
    # Task Backend Interface (v1.0.5)
    "ITaskBackend",
    "BackendTaskRef",
    "BackendError",
    "TaskNotFoundError",
    # Task Sync Adapter (legacy)
    "TaskSyncAdapter",
    "MemoryTaskAdapter",
    "ExternalTask",
    "SyncResult",
    "SyncSummary",
    "SyncDirection",
    # Task Sync Service
    "TaskSyncService",
    "TaskSyncConfig",
    "TaskSyncState",
    "create_task_sync_action",
    # Calendar Sync Adapter
    "CalendarAdapter",
    "MemoryCalendarAdapter",
    "CalendarEvent",
    "CalendarSyncResult",
    "CalendarSyncSummary",
    "CalendarSyncOperation",
    "EventStatus",
    "EventVisibility",
    # Calendar Sync Service
    "CalendarSyncService",
    "CalendarSyncConfig",
    "CalendarSyncState",
    "create_calendar_event_action",
    # Task Sync Orchestrator (v1.0.5)
    "TaskSyncOrchestrator",
    "OrchestratorSyncResult",
    "SyncConflict",
    "SyncState",
    "ConflictResolution",
    "SyncFieldPolicy",
]
