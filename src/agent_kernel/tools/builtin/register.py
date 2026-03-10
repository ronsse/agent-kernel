"""Register built-in tools with the tool broker."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from agent_kernel.capabilities.dev_agent_runner import (
    agent_runner_apply_v1,
    agent_runner_run_v1,
)
from agent_kernel.tools.broker import ToolBroker
from agent_kernel.tools.adapters.graph_adapter import (
    graph_get_node,
    graph_neighbors,
    graph_query,
    set_graph_store,
)
from agent_kernel.tools.builtin.knowledge import (
    init_knowledge_tools,
    knowledge_add,
    knowledge_entity_history,
    knowledge_search,
)
from agent_kernel.tools.builtin.notes import (
    create_note,
    delete_note,
    get_note,
    list_notes,
    search_notes,
    update_note,
)
from agent_kernel.tools.builtin.obsidian import (
    obsidian_create,
    obsidian_daily,
    obsidian_list,
    obsidian_read,
    obsidian_search,
    obsidian_update,
)
from agent_kernel.tools.builtin.tasks import (
    complete_task,
    create_task,
    delete_task,
    get_task,
    list_tasks,
    search_tasks,
    sync_tasks,
    update_task,
)

if TYPE_CHECKING:
    from agent_kernel.memory.event_log import EventLog
    from agent_kernel.memory.graph_store import GraphStore

logger = structlog.get_logger(__name__)


def register_builtin_tools(
    broker: ToolBroker,
    graph_store: GraphStore | None = None,
    event_log: EventLog | None = None,
) -> None:
    """Register all built-in tools with the broker.

    Args:
        broker: The tool broker to register with.
        graph_store: Optional graph store for graph query tools.
        event_log: Optional event log for knowledge tools.
    """
    adapter = broker.local_adapter

    # Task tools
    adapter.register("tasks.list@v1", list_tasks)
    adapter.register("tasks.create@v1", create_task)
    adapter.register("tasks.get@v1", get_task)
    adapter.register("tasks.update@v1", update_task)
    adapter.register("tasks.complete@v1", complete_task)
    adapter.register("tasks.delete@v1", delete_task)
    adapter.register("tasks.search@v1", search_tasks)
    adapter.register("tasks.sync@v1", sync_tasks)

    # Note tools
    adapter.register("notes.search@v1", search_notes)
    adapter.register("notes.create@v1", create_note)
    adapter.register("notes.get@v1", get_note)
    adapter.register("notes.update@v1", update_note)
    adapter.register("notes.delete@v1", delete_note)
    adapter.register("notes.list@v1", list_notes)

    # Obsidian vault tools
    adapter.register("obsidian.read@v1", obsidian_read)
    adapter.register("obsidian.create@v1", obsidian_create)
    adapter.register("obsidian.update@v1", obsidian_update)
    adapter.register("obsidian.search@v1", obsidian_search)
    adapter.register("obsidian.list@v1", obsidian_list)
    adapter.register("obsidian.daily@v1", obsidian_daily)

    # External agent runner tools (v1.0.7)
    adapter.register("dev.agent_runner.run@v1", agent_runner_run_v1)
    adapter.register("dev.agent_runner.apply@v1", agent_runner_apply_v1)

    # Context graph knowledge tools
    adapter.register("knowledge.search@v1", knowledge_search)
    adapter.register("knowledge.add@v1", knowledge_add)
    adapter.register("knowledge.history@v1", knowledge_entity_history)

    # Graph query tools
    adapter.register("graph.query@v1", graph_query)
    adapter.register("graph.neighbors@v1", graph_neighbors)
    adapter.register("graph.get_node@v1", graph_get_node)

    # Initialize graph store for graph adapters
    if graph_store is not None:
        set_graph_store(graph_store)
        init_knowledge_tools(graph_store, event_log=event_log)
        logger.info("graph_tools_initialized", graph_store=type(graph_store).__name__)

    registered_tools = [
        "tasks.list@v1",
        "tasks.create@v1",
        "tasks.get@v1",
        "tasks.update@v1",
        "tasks.complete@v1",
        "tasks.delete@v1",
        "tasks.search@v1",
        "tasks.sync@v1",
        "notes.search@v1",
        "notes.create@v1",
        "notes.get@v1",
        "notes.update@v1",
        "notes.delete@v1",
        "notes.list@v1",
        "obsidian.read@v1",
        "obsidian.create@v1",
        "obsidian.update@v1",
        "obsidian.search@v1",
        "obsidian.list@v1",
        "obsidian.daily@v1",
        "dev.agent_runner.run@v1",
        "dev.agent_runner.apply@v1",
        "knowledge.search@v1",
        "knowledge.add@v1",
        "knowledge.history@v1",
        "graph.query@v1",
        "graph.neighbors@v1",
        "graph.get_node@v1",
    ]

    logger.info(
        "builtin_tools_registered",
        tools=registered_tools,
        count=len(registered_tools),
    )
