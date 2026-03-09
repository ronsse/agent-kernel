"""Agent Kernel MCP Server.

Exposes memory, knowledge, experience, skill, and context tools
to MCP clients (e.g., Claude Code).

Usage:
    python -m agent_kernel.mcp_server
"""

from agent_kernel.mcp_server.server import create_server

__all__ = ["create_server"]
