"""Unit tests for TaskSyncService."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_kernel.core.schemas.graph import NodeType
from agent_kernel.integrations.task_sync import MemoryTaskAdapter
from agent_kernel.integrations.task_sync_service import TaskSyncConfig, TaskSyncService
from agent_kernel.memory.graph_store import SQLiteGraphStore


@pytest.mark.asyncio
async def test_get_tasks_from_graph_filters_exported_tasks(tmp_path: Path) -> None:
    graph_store = SQLiteGraphStore(tmp_path / "graph.db")
    graph_store.upsert_node(
        node_id="task:task_1",
        node_type=NodeType.TASK.value,
        properties={
            "task_id": "task_1",
            "text": "Export me",
            "status": "open",
            "priority": "p4",
            "is_complete": False,
            "tags": [],
            "contexts": [],
            "should_sync": True,
            "note_export_todo": False,
        },
    )
    graph_store.upsert_node(
        node_id="task:task_2",
        node_type=NodeType.TASK.value,
        properties={
            "task_id": "task_2",
            "text": "Local only",
            "status": "open",
            "priority": "p4",
            "is_complete": False,
            "tags": [],
            "contexts": [],
            "should_sync": False,
            "note_export_todo": False,
        },
    )

    service = TaskSyncService(graph_store=graph_store)
    tasks = await service.get_tasks_from_graph(adapter_id="linear")

    assert len(tasks) == 1
    assert tasks[0].kernel_task_id == "task_1"


@pytest.mark.asyncio
async def test_meeting_metadata_included_in_raw_data(tmp_path: Path) -> None:
    graph_store = SQLiteGraphStore(tmp_path / "graph.db")
    graph_store.upsert_node(
        node_id="note:note_1",
        node_type=NodeType.NOTE.value,
        properties={
            "note_id": "note_1",
            "meeting_parent_id": "parent_123",
        },
    )
    graph_store.upsert_node(
        node_id="task:task_3",
        node_type=NodeType.TASK.value,
        properties={
            "task_id": "task_3",
            "text": "Action item",
            "status": "open",
            "priority": "p4",
            "is_complete": False,
            "tags": [],
            "contexts": [],
            "should_sync": True,
            "note_export_todo": False,
            "project": "Home",
            "meeting_group_id": "note_1",
            "meeting_title": "Weekly Sync",
            "meeting_date": "2026-01-22",
            "note_tags": ["work"],
        },
    )

    service = TaskSyncService(graph_store=graph_store)
    tasks = await service.get_tasks_from_graph(adapter_id="linear")

    assert len(tasks) == 1
    assert tasks[0].raw_data["meeting_group_id"] == "note_1"
    assert tasks[0].raw_data["meeting_parent_id"] == "parent_123"
    assert tasks[0].project_name == "Home"


@pytest.mark.asyncio
async def test_sync_skips_unchanged_tasks(tmp_path: Path) -> None:
    graph_store = SQLiteGraphStore(tmp_path / "graph.db")
    graph_store.upsert_node(
        node_id="task:task_1",
        node_type=NodeType.TASK.value,
        properties={
            "task_id": "task_1",
            "text": "Ship report",
            "status": "open",
            "priority": "p4",
            "is_complete": False,
            "tags": ["work"],
            "contexts": [],
            "should_sync": True,
            "note_export_todo": False,
        },
    )

    adapter = MemoryTaskAdapter()
    service = TaskSyncService(graph_store=graph_store)
    service.register_adapter(adapter)

    summary = await service.sync_to_adapter("memory", TaskSyncConfig())
    assert summary.created == 1
    assert summary.skipped == 0

    node = graph_store.get_node("task:task_1")
    props = node.get("properties", {}) if node else {}
    external_sync = props.get("external_sync", {})
    assert "memory" in external_sync
    assert external_sync["memory"]["sync_hash"]

    service_again = TaskSyncService(graph_store=graph_store)
    service_again.register_adapter(adapter)
    summary_again = await service_again.sync_to_adapter("memory", TaskSyncConfig())
    assert summary_again.created == 0
    assert summary_again.updated == 0
    assert summary_again.skipped == 1


@pytest.mark.asyncio
async def test_get_tasks_from_graph_include_linked_only(tmp_path: Path) -> None:
    graph_store = SQLiteGraphStore(tmp_path / "graph.db")
    graph_store.upsert_node(
        node_id="task:task_1",
        node_type=NodeType.TASK.value,
        properties={
            "task_id": "task_1",
            "text": "Linked task",
            "status": "open",
            "priority": "p4",
            "is_complete": False,
            "tags": [],
            "contexts": [],
            "should_sync": False,
            "note_export_todo": False,
            "external_ids": {"linear": "ext_1"},
        },
    )

    service = TaskSyncService(graph_store=graph_store)
    linked = await service.get_tasks_from_graph(
        adapter_id="linear", include_linked_only=True
    )
    assert len(linked) == 1
    assert linked[0].external_id == "ext_1"

    default = await service.get_tasks_from_graph(
        adapter_id="linear", include_linked_only=False
    )
    assert len(default) == 0


@pytest.mark.asyncio
async def test_sync_update_only_skips_creates(tmp_path: Path) -> None:
    graph_store = SQLiteGraphStore(tmp_path / "graph.db")
    graph_store.upsert_node(
        node_id="task:task_1",
        node_type=NodeType.TASK.value,
        properties={
            "task_id": "task_1",
            "text": "Export me",
            "status": "open",
            "priority": "p4",
            "is_complete": False,
            "tags": [],
            "contexts": [],
            "should_sync": True,
            "note_export_todo": False,
        },
    )

    adapter = MemoryTaskAdapter()
    service = TaskSyncService(graph_store=graph_store)
    service.register_adapter(adapter)

    summary = await service.sync_to_adapter(
        "memory", TaskSyncConfig(update_only=True)
    )
    assert summary.created == 0
    assert summary.updated == 0
    assert summary.skipped == 1
    assert summary.total_tasks == 1
