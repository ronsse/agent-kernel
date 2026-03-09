"""Tests for MCP Tool Adapter."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_kernel.tools.adapters.mcp import (
    MCPConnection,
    MCPServerConfig,
    MCPTool,
    MCPToolAdapter,
)


class TestMCPServerConfig:
    """Tests for MCPServerConfig dataclass."""

    def test_default_values(self):
        """Test default config values."""
        config = MCPServerConfig(
            name="test-server",
            command="node",
            args=["server.js"],
        )

        assert config.name == "test-server"
        assert config.command == "node"
        assert config.args == ["server.js"]
        assert config.transport == "stdio"
        assert config.is_stdio is True
        assert config.timeout_ms == 30000

    def test_custom_values(self):
        """Test custom config values."""
        config = MCPServerConfig(
            name="custom",
            command="python",
            args=["-m", "mcp_server"],
            env={"DEBUG": "true"},
            transport="sse",
            url="http://localhost:3000",
            timeout_ms=60000,
        )

        assert config.transport == "sse"
        assert config.is_stdio is False
        assert config.url == "http://localhost:3000"


class TestMCPTool:
    """Tests for MCPTool dataclass."""

    def test_create_tool(self):
        """Test creating a tool definition."""
        tool = MCPTool(
            name="read_file",
            description="Read a file from disk",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
            server_name="filesystem",
        )

        assert tool.name == "read_file"
        assert tool.server_name == "filesystem"
        assert "path" in tool.input_schema["properties"]


class TestMCPConnection:
    """Tests for MCPConnection."""

    def test_init(self):
        """Test connection initialization."""
        config = MCPServerConfig(name="test", command="node")
        connection = MCPConnection(config)

        assert connection.config == config
        assert connection.is_connected is False

    def test_parse_message_complete(self):
        """Test parsing a complete message."""
        config = MCPServerConfig(name="test", command="node")
        connection = MCPConnection(config)

        content = '{"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}}'
        buffer = f"Content-Length: {len(content)}\r\n\r\n{content}".encode()

        message, remaining = connection._parse_message(buffer)

        assert message is not None
        assert message["id"] == 1
        assert message["result"]["status"] == "ok"
        assert remaining == b""

    def test_parse_message_incomplete(self):
        """Test parsing incomplete message."""
        config = MCPServerConfig(name="test", command="node")
        connection = MCPConnection(config)

        # Incomplete - no content yet
        buffer = b"Content-Length: 50\r\n\r\n{partial"

        message, remaining = connection._parse_message(buffer)

        assert message is None
        assert remaining == buffer

    def test_parse_message_no_header(self):
        """Test parsing without header."""
        config = MCPServerConfig(name="test", command="node")
        connection = MCPConnection(config)

        buffer = b"some random data"

        message, remaining = connection._parse_message(buffer)

        assert message is None
        assert remaining == buffer

    def test_handle_message_response(self):
        """Test handling a response message."""
        import asyncio

        config = MCPServerConfig(name="test", command="node")
        connection = MCPConnection(config)

        # Set up pending request
        loop = asyncio.new_event_loop()
        try:
            future = loop.create_future()
            connection._pending[1] = future

            message = {"jsonrpc": "2.0", "id": 1, "result": {"data": "test"}}
            connection._handle_message(message)

            assert future.done()
            assert future.result() == {"data": "test"}
        finally:
            loop.close()

    def test_handle_message_error(self):
        """Test handling an error response."""
        import asyncio

        config = MCPServerConfig(name="test", command="node")
        connection = MCPConnection(config)

        loop = asyncio.new_event_loop()
        try:
            future = loop.create_future()
            connection._pending[1] = future

            message = {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32600, "message": "Invalid request"},
            }
            connection._handle_message(message)

            assert future.done()
            with pytest.raises(RuntimeError, match="MCP error"):
                future.result()
        finally:
            loop.close()


class TestMCPToolAdapter:
    """Tests for MCPToolAdapter."""

    def test_init(self):
        """Test adapter initialization."""
        adapter = MCPToolAdapter()

        assert adapter._connections == {}
        assert adapter._tool_to_server == {}

    def test_supports(self):
        """Test supports method."""
        adapter = MCPToolAdapter()

        assert adapter.supports("mcp") is True
        assert adapter.supports("http") is False
        assert adapter.supports("local") is False

    def test_has_tool(self):
        """Test has_tool method."""
        adapter = MCPToolAdapter()
        adapter._tool_to_server["read_file"] = "filesystem"

        assert adapter.has_tool("read_file") is True
        assert adapter.has_tool("unknown") is False

    def test_list_tools(self):
        """Test listing tools."""
        adapter = MCPToolAdapter()

        # Mock connection with tools
        mock_connection = MagicMock()
        mock_connection.get_tools.return_value = [
            MCPTool("tool1", "Desc 1", {}, "server1"),
            MCPTool("tool2", "Desc 2", {}, "server1"),
        ]
        adapter._connections["server1"] = mock_connection

        tools = adapter.list_tools()

        assert len(tools) == 2
        assert tools[0].name == "tool1"

    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self):
        """Test executing unknown tool."""
        adapter = MCPToolAdapter()

        result = await adapter.execute("unknown_tool", {}, 5000)

        assert result.success is False
        assert result.error_code == "TOOL_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_execute_server_disconnected(self):
        """Test executing when server disconnected."""
        adapter = MCPToolAdapter()
        adapter._tool_to_server["test_tool"] = "server1"

        # No connection for server1
        result = await adapter.execute("test_tool", {}, 5000)

        assert result.success is False
        assert result.error_code == "SERVER_DISCONNECTED"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_execute_success(self):
        """Test successful tool execution."""
        adapter = MCPToolAdapter()
        adapter._tool_to_server["read_file"] = "filesystem"

        # Mock connection
        mock_connection = MagicMock()
        mock_connection.is_connected = True
        mock_connection.call_tool = AsyncMock(return_value={
            "content": [
                {"type": "text", "text": "File contents here"},
            ],
        })
        adapter._connections["filesystem"] = mock_connection

        result = await adapter.execute(
            "read_file",
            {"path": "/tmp/test.txt"},
            5000,
        )

        assert result.success is True
        assert "File contents" in result.output["result"]

    @pytest.mark.asyncio
    async def test_execute_tool_error(self):
        """Test tool returning error."""
        adapter = MCPToolAdapter()
        adapter._tool_to_server["failing_tool"] = "server1"

        mock_connection = MagicMock()
        mock_connection.is_connected = True
        mock_connection.call_tool = AsyncMock(return_value={
            "isError": True,
            "content": [
                {"type": "text", "text": "Something went wrong"},
            ],
        })
        adapter._connections["server1"] = mock_connection

        result = await adapter.execute("failing_tool", {}, 5000)

        assert result.success is False
        assert result.error_code == "MCP_TOOL_ERROR"

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        """Test tool timeout."""

        adapter = MCPToolAdapter()
        adapter._tool_to_server["slow_tool"] = "server1"

        # Use AsyncMock that raises TimeoutError
        mock_connection = MagicMock()
        mock_connection.is_connected = True
        mock_connection.call_tool = AsyncMock(
            side_effect=TimeoutError("Timed out")
        )
        adapter._connections["server1"] = mock_connection

        result = await adapter.execute("slow_tool", {}, 100)  # 100ms timeout

        assert result.success is False
        assert result.error_code == "TIMEOUT"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_remove_server(self):
        """Test removing a server."""
        adapter = MCPToolAdapter()

        # Add mock connection
        mock_connection = MagicMock()
        mock_connection.disconnect = AsyncMock()
        mock_connection.get_tools.return_value = [
            MCPTool("tool1", "", {}, "server1"),
        ]
        adapter._connections["server1"] = mock_connection
        adapter._tool_to_server["tool1"] = "server1"

        await adapter.remove_server("server1")

        assert "server1" not in adapter._connections
        assert "tool1" not in adapter._tool_to_server
        mock_connection.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_close(self):
        """Test closing all connections."""
        adapter = MCPToolAdapter()

        mock1 = MagicMock()
        mock1.disconnect = AsyncMock()
        mock2 = MagicMock()
        mock2.disconnect = AsyncMock()

        adapter._connections["s1"] = mock1
        adapter._connections["s2"] = mock2

        await adapter.close()

        assert adapter._connections == {}
        mock1.disconnect.assert_called_once()
        mock2.disconnect.assert_called_once()
