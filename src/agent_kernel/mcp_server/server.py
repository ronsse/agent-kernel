"""Agent Kernel MCP Server core.

Creates a FastMCP server with tools for memory, knowledge,
experience, skill, and context operations. Stores are initialized
lazily via StoreFactory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from mcp.server import FastMCP

from agent_kernel.core.config import Settings, get_settings
from agent_kernel.memory.factory import StoreFactory

logger = structlog.get_logger(__name__)


class StoreBundle:
    """Lazy-initializing bundle of all kernel stores."""

    def __init__(self, factory: StoreFactory, settings: Settings) -> None:
        self._factory = factory
        self._settings = settings
        self._document_store: Any = None
        self._vector_store: Any = None
        self._graph_store: Any = None
        self._experience_store: Any = None
        self._event_log: Any = None
        self._skill_store: Any = None
        self._context_assembler: Any = None
        self._trace_store: Any = None

    @property
    def document_store(self) -> Any:
        if self._document_store is None:
            self._document_store = self._factory.create_document_store()
        return self._document_store

    @property
    def vector_store(self) -> Any:
        if self._vector_store is None:
            self._vector_store = self._factory.create_vector_store()
        return self._vector_store

    @property
    def graph_store(self) -> Any:
        if self._graph_store is None:
            self._graph_store = self._factory.create_graph_store()
        return self._graph_store

    @property
    def experience_store(self) -> Any:
        if self._experience_store is None:
            self._experience_store = self._factory.create_experience_store()
        return self._experience_store

    @property
    def event_log(self) -> Any:
        if self._event_log is None:
            self._event_log = self._factory.create_event_log()
        return self._event_log

    @property
    def skill_store(self) -> Any:
        if self._skill_store is None:
            skills_dir = self._settings.skills_dir
            if skills_dir:
                from agent_kernel.skills.store import SkillStoreLocalFS

                skills_path = Path(skills_dir).expanduser()
                if skills_path.exists():
                    self._skill_store = SkillStoreLocalFS(skills_path)
        return self._skill_store

    @property
    def context_assembler(self) -> Any:
        if self._context_assembler is None:
            from agent_kernel.context.assembler import ContextAssembler

            self._context_assembler = ContextAssembler(
                document_store=self.document_store,
                vector_store=self.vector_store,
                graph_store=self.graph_store,
                skill_store=self.skill_store,
                experience_store=self.experience_store,
            )
        return self._context_assembler

    @property
    def trace_store(self) -> Any:
        if self._trace_store is None:
            self._trace_store = self._factory.create_trace_store()
        return self._trace_store


def create_server(settings: Settings | None = None) -> FastMCP:
    """Create and configure the agent-kernel MCP server."""
    if settings is None:
        settings = get_settings()

    factory = StoreFactory(settings)
    stores = StoreBundle(factory, settings)

    mcp = FastMCP(
        name="agent-kernel",
        instructions=(
            "Agent Kernel MCP server. Provides tools for searching memory, "
            "querying the knowledge graph, browsing experience cases/lessons, "
            "managing skills, and assembling context packets."
        ),
    )

    # Register all tool groups
    from agent_kernel.mcp_server.tools.context import register_context_tools
    from agent_kernel.mcp_server.tools.experience import register_experience_tools
    from agent_kernel.mcp_server.tools.knowledge import register_knowledge_tools
    from agent_kernel.mcp_server.tools.memory import register_memory_tools
    from agent_kernel.mcp_server.tools.skill import register_skill_tools

    register_memory_tools(mcp, stores)
    register_knowledge_tools(mcp, stores)
    register_experience_tools(mcp, stores)
    register_skill_tools(mcp, stores)
    from agent_kernel.mcp_server.tools.tracing import register_tracing_tools
    register_tracing_tools(mcp, stores)
    register_context_tools(mcp, stores)

    logger.info("mcp_server_created", tool_count=5, backend=settings.store_backend)

    return mcp
