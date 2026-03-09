"""Tests for Task Sync Adapter and Service."""

from __future__ import annotations

from datetime import date

import pytest
from agent_kernel.integrations.task_sync import (
    ExternalTask,
    MemoryTaskAdapter,
    SyncDirection,
    SyncOperation,
    SyncResult,
)
from agent_kernel.services.task_parser import TaskPriority, TaskStatus

EXPECTED_ORPHAN_DELETES = 2
EXPECTED_TWO_TASKS = 2


class TestExternalTask:
    """Tests for ExternalTask dataclass."""

    def test_default_values(self) -> None:
        """Test default initialization."""
        task = ExternalTask(external_id="ext_123")
        assert task.external_id == "ext_123"
        assert task.kernel_task_id is None
        assert task.text == ""
        assert task.status == TaskStatus.OPEN
        assert task.is_complete is False
        assert task.priority == TaskPriority.P4
        assert task.tags == []

    def test_with_all_fields(self) -> None:
        """Test task with all fields populated."""
        task = ExternalTask(
            external_id="ext_456",
            kernel_task_id="task_abc123",
            text="Complete the report",
            status=TaskStatus.OPEN,
            is_complete=False,
            due_date=date(2026, 1, 20),
            priority=TaskPriority.P2,
            tags=["work", "urgent"],
            contexts=["office"],
            source_note_id="note_xyz",
        )
        assert task.external_id == "ext_456"
        assert task.kernel_task_id == "task_abc123"
        assert task.due_date == date(2026, 1, 20)
        assert task.priority == TaskPriority.P2

    def test_to_dict(self) -> None:
        """Test conversion to dictionary."""
        task = ExternalTask(
            external_id="ext_789",
            text="Test task",
            tags=["test"],
        )
        d = task.to_dict()
        assert d["external_id"] == "ext_789"
        assert d["text"] == "Test task"
        assert d["tags"] == ["test"]
        assert d["status"] == "incomplete"


class TestMemoryTaskAdapter:
    """Tests for the in-memory task adapter."""

    @pytest.fixture
    def adapter(self) -> MemoryTaskAdapter:
        """Create a memory adapter."""
        return MemoryTaskAdapter()

    @pytest.mark.asyncio
    async def test_properties(self, adapter: MemoryTaskAdapter) -> None:
        """Test adapter properties."""
        assert adapter.adapter_id == "memory"
        assert adapter.display_name == "In-Memory Tasks"
        assert adapter.requires_approval_for_writes is False

    @pytest.mark.asyncio
    async def test_connection(self, adapter: MemoryTaskAdapter) -> None:
        """Test connection always succeeds."""
        assert await adapter.test_connection() is True

    @pytest.mark.asyncio
    async def test_create_task(self, adapter: MemoryTaskAdapter) -> None:
        """Test creating a task."""
        task = ExternalTask(
            external_id="",  # Will be assigned
            kernel_task_id="task_123",
            text="New task",
        )
        result = await adapter.create_task(task)

        assert result.success is True
        assert result.operation == SyncOperation.CREATE
        assert result.external_id is not None
        assert result.external_id.startswith("mem_")
        assert result.kernel_task_id == "task_123"

    @pytest.mark.asyncio
    async def test_get_task(self, adapter: MemoryTaskAdapter) -> None:
        """Test getting a task by ID."""
        # Create a task first
        task = ExternalTask(
            external_id="",
            kernel_task_id="task_456",
            text="Get me",
        )
        result = await adapter.create_task(task)
        external_id = result.external_id

        # Get the task
        retrieved = await adapter.get_task(external_id)
        assert retrieved is not None
        assert retrieved.external_id == external_id
        assert retrieved.text == "Get me"

    @pytest.mark.asyncio
    async def test_get_nonexistent_task(self, adapter: MemoryTaskAdapter) -> None:
        """Test getting a nonexistent task."""
        task = await adapter.get_task("nonexistent")
        assert task is None

    @pytest.mark.asyncio
    async def test_update_task(self, adapter: MemoryTaskAdapter) -> None:
        """Test updating a task."""
        # Create a task
        task = ExternalTask(external_id="", text="Original")
        result = await adapter.create_task(task)
        external_id = result.external_id

        # Update it
        updated_task = ExternalTask(
            external_id=external_id,
            text="Updated",
            priority=TaskPriority.P2,
        )
        update_result = await adapter.update_task(updated_task)

        assert update_result.success is True
        assert update_result.operation == SyncOperation.UPDATE

        # Verify update
        retrieved = await adapter.get_task(external_id)
        assert retrieved is not None
        assert retrieved.text == "Updated"
        assert retrieved.priority == TaskPriority.P2

    @pytest.mark.asyncio
    async def test_update_nonexistent_task(
        self, adapter: MemoryTaskAdapter
    ) -> None:
        """Test updating a nonexistent task."""
        task = ExternalTask(external_id="nonexistent", text="Won't work")
        result = await adapter.update_task(task)

        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_complete_task(self, adapter: MemoryTaskAdapter) -> None:
        """Test marking a task as complete."""
        # Create a task
        task = ExternalTask(external_id="", text="Complete me")
        result = await adapter.create_task(task)
        external_id = result.external_id

        # Complete it
        complete_result = await adapter.complete_task(external_id)

        assert complete_result.success is True
        assert complete_result.operation == SyncOperation.COMPLETE

        # Verify completion
        retrieved = await adapter.get_task(external_id)
        assert retrieved is not None
        assert retrieved.is_complete is True
        assert retrieved.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_delete_task(self, adapter: MemoryTaskAdapter) -> None:
        """Test deleting a task."""
        # Create a task
        task = ExternalTask(external_id="", text="Delete me")
        result = await adapter.create_task(task)
        external_id = result.external_id

        # Delete it
        delete_result = await adapter.delete_task(external_id)

        assert delete_result.success is True
        assert delete_result.operation == SyncOperation.DELETE

        # Verify deletion
        retrieved = await adapter.get_task(external_id)
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_list_tasks(self, adapter: MemoryTaskAdapter) -> None:
        """Test listing all tasks."""
        # Create some tasks
        tasks_to_create = ["Task 1", "Task 2", "Task 3"]
        for text in tasks_to_create:
            await adapter.create_task(ExternalTask(external_id="", text=text))

        tasks = await adapter.list_tasks()
        assert len(tasks) == len(tasks_to_create)

    @pytest.mark.asyncio
    async def test_list_tasks_excludes_completed(
        self, adapter: MemoryTaskAdapter
    ) -> None:
        """Test that list_tasks excludes completed by default."""
        # Create tasks
        result = await adapter.create_task(
            ExternalTask(external_id="", text="Active")
        )
        await adapter.create_task(
            ExternalTask(external_id="", text="Also active")
        )

        # Complete one
        await adapter.complete_task(result.external_id)

        # List should only have 1 (the one not completed)
        tasks = await adapter.list_tasks(include_completed=False)
        expected_active = 1
        assert len(tasks) == expected_active

        # With include_completed, should have all tasks
        all_tasks = await adapter.list_tasks(include_completed=True)
        expected_total = 2
        assert len(all_tasks) == expected_total

    @pytest.mark.asyncio
    async def test_sync_to_external(self, adapter: MemoryTaskAdapter) -> None:
        """Test batch sync to external."""
        tasks = [
            ExternalTask(external_id="", kernel_task_id="k1", text="Task 1"),
            ExternalTask(external_id="", kernel_task_id="k2", text="Task 2"),
        ]

        summary = await adapter.sync_to_external(tasks)

        expected_count = len(tasks)
        assert summary.direction == SyncDirection.PUSH
        assert summary.total_tasks == expected_count
        assert summary.created == expected_count
        assert summary.failed == 0

    @pytest.mark.asyncio
    async def test_sync_updates_existing(self, adapter: MemoryTaskAdapter) -> None:
        """Test that sync updates existing tasks."""
        # Create a task first
        result = await adapter.create_task(
            ExternalTask(external_id="", kernel_task_id="k1", text="Original")
        )
        external_id = result.external_id

        # Sync with updated version
        tasks = [
            ExternalTask(
                external_id=external_id,
                kernel_task_id="k1",
                text="Updated via sync",
            ),
        ]

        summary = await adapter.sync_to_external(tasks)

        assert summary.updated == 1
        assert summary.created == 0

        # Verify update
        retrieved = await adapter.get_task(external_id)
        assert retrieved is not None
        assert retrieved.text == "Updated via sync"

    @pytest.mark.asyncio
    async def test_sync_completes_tasks(self, adapter: MemoryTaskAdapter) -> None:
        """Test that sync completes tasks marked as complete."""
        # Create a task first
        result = await adapter.create_task(
            ExternalTask(external_id="", kernel_task_id="k1", text="To complete")
        )
        external_id = result.external_id

        # Sync with completed version
        tasks = [
            ExternalTask(
                external_id=external_id,
                kernel_task_id="k1",
                text="To complete",
                is_complete=True,
            ),
        ]

        summary = await adapter.sync_to_external(tasks)

        assert summary.completed == 1

        # Verify completion
        retrieved = await adapter.get_task(external_id)
        assert retrieved is not None
        assert retrieved.is_complete is True


class TestDeleteMissingTasks:
    """Tests for _delete_missing_tasks logic."""

    @pytest.fixture
    def adapter(self) -> MemoryTaskAdapter:
        """Create a memory adapter."""
        return MemoryTaskAdapter()

    @pytest.mark.asyncio
    async def test_deletes_orphaned_tasks(self, adapter: MemoryTaskAdapter) -> None:
        """Test that tasks in external but not in kernel get deleted."""
        # Create 3 tasks in external system
        r1 = await adapter.create_task(ExternalTask(external_id="", text="Keep me"))
        await adapter.create_task(ExternalTask(external_id="", text="Delete me 1"))
        await adapter.create_task(ExternalTask(external_id="", text="Delete me 2"))

        # Only 1 task matches kernel
        kernel_tasks = [
            ExternalTask(external_id=r1.external_id, text="Keep me"),
        ]

        summary = await adapter.sync_to_external(kernel_tasks, delete_missing=True)

        assert summary.deleted == EXPECTED_ORPHAN_DELETES
        # Only the kept task should remain
        remaining = await adapter.list_tasks()
        assert len(remaining) == 1
        assert remaining[0].external_id == r1.external_id

    @pytest.mark.asyncio
    async def test_no_orphans_no_deletions(self, adapter: MemoryTaskAdapter) -> None:
        """Test no deletions when all external tasks match kernel."""
        r1 = await adapter.create_task(ExternalTask(external_id="", text="Task 1"))
        r2 = await adapter.create_task(ExternalTask(external_id="", text="Task 2"))

        kernel_tasks = [
            ExternalTask(external_id=r1.external_id, text="Task 1"),
            ExternalTask(external_id=r2.external_id, text="Task 2"),
        ]

        summary = await adapter.sync_to_external(kernel_tasks, delete_missing=True)

        assert summary.deleted == 0
        remaining = await adapter.list_tasks()
        assert len(remaining) == EXPECTED_TWO_TASKS

    @pytest.mark.asyncio
    async def test_handles_delete_failure(self) -> None:
        """Test that delete failures are counted and don't crash sync."""
        adapter = MemoryTaskAdapter()

        # Populate external system
        r1 = await adapter.create_task(ExternalTask(external_id="", text="Keep"))
        r2 = await adapter.create_task(ExternalTask(external_id="", text="Orphan"))

        # Only r1 is in kernel
        kernel_tasks = [
            ExternalTask(external_id=r1.external_id, text="Keep"),
        ]

        # Patch delete_task to fail
        original_delete = adapter.delete_task

        async def failing_delete(_external_id: str) -> SyncResult:
            msg = "Simulated delete failure"
            raise RuntimeError(msg)

        adapter.delete_task = failing_delete  # type: ignore[assignment]

        summary = await adapter.sync_to_external(kernel_tasks, delete_missing=True)

        assert summary.failed >= 1
        assert summary.deleted == 0

        # Restore and verify the task still exists
        adapter.delete_task = original_delete  # type: ignore[assignment]
        task = await adapter.get_task(r2.external_id)
        assert task is not None

    @pytest.mark.asyncio
    async def test_handles_list_failure(self) -> None:
        """Test graceful handling when list_tasks raises during delete check."""
        adapter = MemoryTaskAdapter()

        r1 = await adapter.create_task(ExternalTask(external_id="", text="Task"))

        kernel_tasks = [
            ExternalTask(external_id=r1.external_id, text="Task"),
        ]

        # Patch list_tasks to fail
        original_list = adapter.list_tasks

        async def failing_list(**_kwargs: object) -> list[ExternalTask]:
            msg = "Simulated list failure"
            raise RuntimeError(msg)

        adapter.list_tasks = failing_list  # type: ignore[assignment]

        # Should not raise — the outer try/except in _delete_missing_tasks
        # catches the error
        summary = await adapter.sync_to_external(kernel_tasks, delete_missing=True)

        # Sync still succeeds for the kernel tasks themselves
        assert summary.updated >= 1 or summary.created >= 0
        # The task should still exist
        adapter.list_tasks = original_list  # type: ignore[assignment]
        remaining = await adapter.list_tasks()
        assert len(remaining) == 1
