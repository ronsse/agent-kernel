"""Entry point for running the agent-kernel MCP server.

Usage:
    python -m agent_kernel.mcp_server
"""

from agent_kernel.mcp_server.server import create_server


def main() -> None:
    """Run the MCP server over stdio."""
    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
