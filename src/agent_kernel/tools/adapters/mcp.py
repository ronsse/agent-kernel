"""MCP Tool Adapter - execute tools via Model Context Protocol.

The Model Context Protocol (MCP) is an open protocol for connecting
AI models to tools and data sources. This adapter enables the agent
kernel to use MCP servers as tool providers.

See: https://modelcontextprotocol.io/
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from agent_kernel.tools.adapters.base import ToolAdapter, ToolResult

logger = structlog.get_logger(__name__)


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server connection."""

    name: str
    command: str  # Command to start the server (for stdio transport)
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"  # stdio | sse | websocket
    url: str | None = None  # For sse/websocket transport
    cwd: str | None = None  # Working directory for stdio transport
    timeout_ms: int = 30000

    @property
    def is_stdio(self) -> bool:
        """Check if using stdio transport."""
        return self.transport == "stdio"


@dataclass
class MCPTool:
    """An MCP tool definition."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str


@dataclass
class MCPToolMapping:
    """Mapping from kernel capability name to MCP tool."""

    capability_name: str
    tool_name: str
    server_name: str | None = None


class MCPConnection:
    """Manages a connection to an MCP server via stdio."""

    def __init__(self, config: MCPServerConfig) -> None:
        """Initialize MCP connection.

        Args:
            config: Server configuration.
        """
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._tools: dict[str, MCPTool] = {}
        self._connected = False
        self._read_task: asyncio.Task | None = None

    @property
    def is_connected(self) -> bool:
        """Check if connected to server."""
        return self._connected and self._process is not None

    async def connect(self) -> None:
        """Start the MCP server process and initialize."""
        if self._connected:
            return

        import os

        # Merge environment
        env = {**os.environ, **self.config.env}

        logger.info(
            "mcp_server_starting",
            name=self.config.name,
            command=self.config.command,
        )

        self._process = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self.config.cwd,
        )

        # Start reading responses
        self._read_task = asyncio.create_task(self._read_responses())

        # Initialize connection
        await self._initialize()
        self._connected = True

        logger.info(
            "mcp_server_connected",
            name=self.config.name,
            tools_count=len(self._tools),
        )

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass

        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
            self._process = None

        self._connected = False
        self._tools.clear()

        logger.info("mcp_server_disconnected", name=self.config.name)

    async def _initialize(self) -> None:
        """Initialize the MCP connection (handshake)."""
        # Send initialize request
        result = await self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "agent-kernel",
                    "version": "0.1.0",
                },
            },
        )

        logger.debug("mcp_initialized", result=result)

        # Send initialized notification
        await self._send_notification("notifications/initialized", {})

        # Discover tools
        await self._discover_tools()

    async def _discover_tools(self) -> None:
        """Discover available tools from the server."""
        result = await self._send_request("tools/list", {})

        tools = result.get("tools", [])
        for tool_data in tools:
            tool = MCPTool(
                name=tool_data["name"],
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}),
                server_name=self.config.name,
            )
            self._tools[tool.name] = tool

        logger.debug(
            "mcp_tools_discovered",
            server=self.config.name,
            tools=[t.name for t in self._tools.values()],
        )

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Call a tool on the MCP server.

        Args:
            tool_name: Name of the tool to call.
            arguments: Tool arguments.
            timeout_ms: Optional timeout override.

        Returns:
            Tool result.
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to MCP server")

        timeout = (timeout_ms or self.config.timeout_ms) / 1000.0

        result = await asyncio.wait_for(
            self._send_request(
                "tools/call",
                {
                    "name": tool_name,
                    "arguments": arguments,
                },
            ),
            timeout=timeout,
        )

        return result

    def get_tools(self) -> list[MCPTool]:
        """Get available tools."""
        return list(self._tools.values())

    def has_tool(self, tool_name: str) -> bool:
        """Check if a tool is available."""
        return tool_name in self._tools

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for response."""
        self._request_id += 1
        request_id = self._request_id

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        await self._write_message(request)

        try:
            result = await future
            return result
        finally:
            self._pending.pop(request_id, None)

    async def _send_notification(
        self,
        method: str,
        params: dict[str, Any],
    ) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        await self._write_message(notification)

    async def _write_message(self, message: dict[str, Any]) -> None:
        """Write a message to the server."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Process not running")

        content = json.dumps(message)
        # MCP uses Content-Length header format
        header = f"Content-Length: {len(content)}\r\n\r\n"
        data = (header + content).encode()

        self._process.stdin.write(data)
        await self._process.stdin.drain()

    async def _read_responses(self) -> None:
        """Read responses from the server."""
        if not self._process or not self._process.stdout:
            return

        buffer = b""

        while True:
            try:
                chunk = await self._process.stdout.read(4096)
                if not chunk:
                    break

                buffer += chunk

                # Parse messages from buffer
                while True:
                    message, buffer = self._parse_message(buffer)
                    if message is None:
                        break

                    self._handle_message(message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("mcp_read_error", error=str(e))
                break

    def _parse_message(
        self,
        buffer: bytes,
    ) -> tuple[dict[str, Any] | None, bytes]:
        """Parse a message from the buffer."""
        # Look for Content-Length header
        header_end = buffer.find(b"\r\n\r\n")
        if header_end == -1:
            return None, buffer

        header = buffer[:header_end].decode()

        # Parse content length
        content_length = 0
        for line in header.split("\r\n"):
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":")[1].strip())
                break

        if content_length == 0:
            return None, buffer

        # Check if we have the full content
        content_start = header_end + 4
        content_end = content_start + content_length

        if len(buffer) < content_end:
            return None, buffer

        content = buffer[content_start:content_end].decode()
        remaining = buffer[content_end:]

        try:
            message = json.loads(content)
            return message, remaining
        except json.JSONDecodeError:
            logger.warning("mcp_invalid_json", content=content[:100])
            return None, remaining

    def _handle_message(self, message: dict[str, Any]) -> None:
        """Handle an incoming message."""
        # Check if it's a response
        if "id" in message and message["id"] in self._pending:
            request_id = message["id"]
            future = self._pending.get(request_id)

            if future and not future.done():
                if "error" in message:
                    error = message["error"]
                    future.set_exception(
                        RuntimeError(f"MCP error: {error.get('message', error)}")
                    )
                else:
                    future.set_result(message.get("result", {}))


class MCPToolAdapter(ToolAdapter):
    """Adapter that executes tools via MCP servers.

    Multiple MCP servers can be registered, each providing
    different sets of tools.
    """

    def __init__(self) -> None:
        """Initialize the MCP adapter."""
        self._connections: dict[str, MCPConnection] = {}
        self._tool_to_server: dict[str, str] = {}
        self._capability_mappings: dict[str, MCPToolMapping] = {}

    async def add_server(self, config: MCPServerConfig) -> list[MCPTool]:
        """Add and connect to an MCP server.

        Args:
            config: Server configuration.

        Returns:
            List of discovered tools.
        """
        connection = MCPConnection(config)
        await connection.connect()

        self._connections[config.name] = connection

        # Map tools to server
        tools = connection.get_tools()
        for tool in tools:
            self._tool_to_server[tool.name] = config.name

        logger.info(
            "mcp_server_added",
            name=config.name,
            tools_count=len(tools),
        )

        return tools

    def register_mappings(self, mappings: dict[str, MCPToolMapping]) -> None:
        """Register capability-to-tool mappings."""
        self._capability_mappings.update(mappings)

    async def remove_server(self, server_name: str) -> None:
        """Remove an MCP server.

        Args:
            server_name: Name of the server to remove.
        """
        connection = self._connections.pop(server_name, None)
        if connection:
            await connection.disconnect()

            # Remove tool mappings
            tools_to_remove = [
                tool for tool, server in self._tool_to_server.items()
                if server == server_name
            ]
            for tool in tools_to_remove:
                del self._tool_to_server[tool]

    async def close(self) -> None:
        """Close all MCP connections."""
        for connection in self._connections.values():
            await connection.disconnect()
        self._connections.clear()
        self._tool_to_server.clear()

    def list_tools(self) -> list[MCPTool]:
        """List all available tools from all servers."""
        tools = []
        for connection in self._connections.values():
            tools.extend(connection.get_tools())
        return tools

    def has_tool(self, tool_name: str) -> bool:
        """Check if a tool is available."""
        return tool_name in self._tool_to_server

    def supports(self, adapter_type: str) -> bool:
        """Check if this adapter supports the given type."""
        return adapter_type == "mcp"

    async def execute(
        self,
        capability_name: str,
        args: dict[str, Any],
        timeout_ms: int,
    ) -> ToolResult:
        """Execute an MCP tool.

        The capability_name should match the MCP tool name.

        Args:
            capability_name: The tool name (from MCP).
            args: The input arguments.
            timeout_ms: Maximum execution time.

        Returns:
            ToolResult with tool output or error.
        """
        mapping = self._capability_mappings.get(capability_name)
        tool_name = mapping.tool_name if mapping else capability_name
        server_name = mapping.server_name if mapping else None

        # Find the server for this tool
        if server_name is None:
            server_name = self._tool_to_server.get(tool_name)
        if not server_name:
            return ToolResult(
                success=False,
                output={},
                error=f"No MCP server has tool: {tool_name}",
                error_code="TOOL_NOT_FOUND",
            )

        connection = self._connections.get(server_name)
        if not connection or not connection.is_connected:
            return ToolResult(
                success=False,
                output={},
                error=f"MCP server not connected: {server_name}",
                error_code="SERVER_DISCONNECTED",
                retryable=True,
            )

        try:
            result = await connection.call_tool(
                tool_name,
                args,
                timeout_ms=timeout_ms,
            )

            # Extract content from MCP response
            content = result.get("content", [])
            if content and isinstance(content, list):
                # Combine text content
                text_parts = []
                for item in content:
                    if item.get("type") == "text":
                        text_parts.append(item.get("text", ""))

                output = {"result": "\n".join(text_parts)}
                if result.get("isError"):
                    return ToolResult(
                        success=False,
                        output=output,
                        error=output.get("result", "MCP tool error"),
                        error_code="MCP_TOOL_ERROR",
                    )
                return ToolResult(success=True, output=output)

            return ToolResult(success=True, output=result)

        except TimeoutError:
            logger.warning(
                "mcp_tool_timeout",
                tool=capability_name,
                server=server_name,
                timeout_ms=timeout_ms,
            )
            return ToolResult(
                success=False,
                output={},
                error=f"MCP tool timed out after {timeout_ms}ms",
                error_code="TIMEOUT",
                retryable=True,
            )

        except Exception as e:
            logger.error(
                "mcp_tool_error",
                tool=capability_name,
                server=server_name,
                error=str(e),
            )
            return ToolResult(
                success=False,
                output={},
                error=str(e),
                error_code="MCP_ERROR",
                retryable=False,
            )
