"""Tool adapter implementations.

Adapters provide the actual execution of tool capabilities.
"""

from agent_kernel.tools.adapters.base import ToolAdapter, ToolResult
from agent_kernel.tools.adapters.graph_adapter import (
    graph_get_node,
    graph_neighbors,
    graph_query,
    set_graph_store,
)
from agent_kernel.tools.adapters.http import HTTPEndpoint, HTTPMethod, HTTPToolAdapter
from agent_kernel.tools.adapters.local_function import LocalFunctionAdapter
from agent_kernel.tools.adapters.mcp import (
    MCPConnection,
    MCPServerConfig,
    MCPTool,
    MCPToolAdapter,
)
from agent_kernel.tools.adapters.subprocess import (
    SubprocessCommand,
    SubprocessToolAdapter,
)
from agent_kernel.tools.adapters.skill_script import SkillScriptAdapter, SkillScriptCommand
from agent_kernel.tools.adapters.vector_adapter import (
    set_embedding_service,
    set_vector_store,
    vector_search,
    vector_search_async,
)

__all__ = [
    "ToolAdapter",
    "ToolResult",
    "LocalFunctionAdapter",
    "HTTPToolAdapter",
    "HTTPEndpoint",
    "HTTPMethod",
    "MCPToolAdapter",
    "MCPServerConfig",
    "MCPConnection",
    "MCPTool",
    "SubprocessToolAdapter",
    "SubprocessCommand",
    "SkillScriptAdapter",
    "SkillScriptCommand",
    # Graph adapter
    "set_graph_store",
    "graph_query",
    "graph_neighbors",
    "graph_get_node",
    # Vector adapter
    "set_vector_store",
    "set_embedding_service",
    "vector_search",
    "vector_search_async",
]
