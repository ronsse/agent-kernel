"""Tests for Subprocess Tool Adapter."""

import pytest

from agent_kernel.tools.adapters.subprocess import (
    SubprocessCommand,
    SubprocessToolAdapter,
)


class TestSubprocessCommand:
    """Tests for SubprocessCommand dataclass."""

    def test_default_values(self):
        """Test default command values."""
        cmd = SubprocessCommand(command="ls -la")

        assert cmd.command == "ls -la"
        assert cmd.shell is False
        assert cmd.working_dir is None
        assert cmd.env == {}
        assert cmd.capture_stderr is True
        assert cmd.allowed_exit_codes == [0]

    def test_custom_values(self):
        """Test custom command values."""
        cmd = SubprocessCommand(
            command="python {script}",
            shell=True,
            working_dir="/tmp",
            env={"PYTHONPATH": "/usr/lib"},
            timeout_override_ms=60000,
            allowed_exit_codes=[0, 1],
        )

        assert cmd.shell is True
        assert cmd.working_dir == "/tmp"
        assert 1 in cmd.allowed_exit_codes


class TestSubprocessToolAdapter:
    """Tests for SubprocessToolAdapter."""

    def test_init(self):
        """Test adapter initialization."""
        adapter = SubprocessToolAdapter(
            allowed_commands=["ls", "cat"],
            default_timeout_ms=10000,
        )

        assert "ls" in adapter._allowed_commands
        assert "cat" in adapter._allowed_commands
        assert adapter._default_timeout_ms == 10000

    def test_register_command(self):
        """Test registering a command."""
        adapter = SubprocessToolAdapter()
        cmd = SubprocessCommand(command="ls -la {path}")

        adapter.register("files.list@v1", cmd)

        assert adapter.has_command("files.list@v1")
        assert "ls" in adapter._allowed_commands

    def test_register_simple(self):
        """Test simple command registration."""
        adapter = SubprocessToolAdapter()

        adapter.register_simple("shell.echo@v1", "echo {message}")

        assert adapter.has_command("shell.echo@v1")

    def test_unregister(self):
        """Test unregistering a command."""
        adapter = SubprocessToolAdapter()
        adapter.register_simple("test@v1", "echo test")

        assert adapter.has_command("test@v1")

        adapter.unregister("test@v1")

        assert not adapter.has_command("test@v1")

    def test_supports(self):
        """Test supports method."""
        adapter = SubprocessToolAdapter()

        assert adapter.supports("subprocess") is True
        assert adapter.supports("http") is False
        assert adapter.supports("local") is False

    def test_format_command_simple(self):
        """Test command formatting."""
        adapter = SubprocessToolAdapter()

        result = adapter._format_command(
            "echo {message}",
            {"message": "hello world"},
        )

        assert result == "echo 'hello world'"

    def test_format_command_numeric(self):
        """Test command formatting with numbers."""
        adapter = SubprocessToolAdapter()

        result = adapter._format_command(
            "sleep {seconds}",
            {"seconds": 5},
        )

        assert result == "sleep 5"

    def test_format_command_list(self):
        """Test command formatting with list."""
        adapter = SubprocessToolAdapter()

        result = adapter._format_command(
            "rm {files}",
            {"files": ["a.txt", "b.txt"]},
        )

        # shlex.quote only adds quotes when needed for shell safety
        assert "a.txt" in result
        assert "b.txt" in result

    def test_format_command_injection_prevention(self):
        """Test that command injection is prevented."""
        adapter = SubprocessToolAdapter()

        result = adapter._format_command(
            "cat {file}",
            {"file": "; rm -rf /"},
        )

        # The malicious input should be quoted
        assert "rm -rf" in result
        assert result.count("'") >= 2  # Should be quoted

    def test_is_command_allowed_with_allowlist(self):
        """Test command allowlist."""
        adapter = SubprocessToolAdapter(allowed_commands=["ls", "cat"])

        assert adapter._is_command_allowed("ls -la") is True
        assert adapter._is_command_allowed("cat file.txt") is True
        assert adapter._is_command_allowed("rm -rf /") is False

    def test_is_command_allowed_no_restrictions(self):
        """Test with no allowlist."""
        adapter = SubprocessToolAdapter(allowed_commands=None)
        adapter._allowed_commands = set()  # Clear default

        # All commands allowed when no restrictions
        assert adapter._is_command_allowed("any command") is True

    @pytest.mark.asyncio
    async def test_execute_not_registered(self):
        """Test executing unregistered command."""
        adapter = SubprocessToolAdapter()

        result = await adapter.execute("unknown@v1", {}, 5000)

        assert result.success is False
        assert result.error_code == "COMMAND_NOT_REGISTERED"

    @pytest.mark.asyncio
    async def test_execute_missing_argument(self):
        """Test executing with missing argument."""
        adapter = SubprocessToolAdapter()
        adapter.register_simple("test@v1", "echo {message}")

        result = await adapter.execute("test@v1", {}, 5000)

        assert result.success is False
        assert result.error_code == "MISSING_ARGUMENT"

    @pytest.mark.asyncio
    async def test_execute_success_echo(self):
        """Test successful echo command."""
        adapter = SubprocessToolAdapter()
        adapter.register_simple("echo@v1", "echo {message}")

        result = await adapter.execute(
            "echo@v1",
            {"message": "hello"},
            5000,
        )

        assert result.success is True
        assert "hello" in result.output["stdout"]
        assert result.output["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_execute_success_with_shell(self):
        """Test command with shell execution."""
        adapter = SubprocessToolAdapter()
        cmd = SubprocessCommand(
            command="echo 'test' | cat",
            shell=True,
        )
        adapter.register("pipe@v1", cmd)

        result = await adapter.execute("pipe@v1", {}, 5000)

        assert result.success is True
        assert "test" in result.output["stdout"]

    @pytest.mark.asyncio
    async def test_execute_nonzero_exit(self):
        """Test command with non-zero exit code."""
        adapter = SubprocessToolAdapter()
        adapter.register_simple("fail@v1", "false")

        result = await adapter.execute("fail@v1", {}, 5000)

        assert result.success is False
        assert "exit" in result.error_code.lower()

    @pytest.mark.asyncio
    async def test_execute_allowed_nonzero_exit(self):
        """Test command where non-zero exit is allowed."""
        adapter = SubprocessToolAdapter()
        cmd = SubprocessCommand(
            command="false",
            allowed_exit_codes=[0, 1],
        )
        adapter.register("maybe_fail@v1", cmd)

        result = await adapter.execute("maybe_fail@v1", {}, 5000)

        assert result.success is True
        assert result.output["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_execute_stderr_capture(self):
        """Test stderr capture."""
        adapter = SubprocessToolAdapter()
        cmd = SubprocessCommand(
            command="echo 'error' >&2 && false",
            shell=True,
            capture_stderr=True,
            allowed_exit_codes=[0, 1],
        )
        adapter.register("stderr_test@v1", cmd)

        result = await adapter.execute("stderr_test@v1", {}, 5000)

        assert "stderr" in result.output
        assert "error" in result.output["stderr"]

    @pytest.mark.asyncio
    async def test_execute_command_not_found(self):
        """Test executing non-existent command."""
        adapter = SubprocessToolAdapter()
        adapter.register_simple(
            "noexist@v1",
            "definitely_not_a_real_command_12345",
        )

        result = await adapter.execute("noexist@v1", {}, 5000)

        assert result.success is False
        assert result.error_code == "COMMAND_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        """Test command timeout."""
        adapter = SubprocessToolAdapter()
        adapter.register_simple("slow@v1", "sleep 10")

        result = await adapter.execute("slow@v1", {}, 100)  # 100ms timeout

        assert result.success is False
        assert result.error_code == "TIMEOUT"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_execute_with_env(self):
        """Test command with custom environment."""
        adapter = SubprocessToolAdapter()
        cmd = SubprocessCommand(
            command="echo $TEST_VAR",
            shell=True,
            env={"TEST_VAR": "custom_value"},
        )
        adapter.register("env_test@v1", cmd)

        result = await adapter.execute("env_test@v1", {}, 5000)

        assert result.success is True
        assert "custom_value" in result.output["stdout"]

    @pytest.mark.asyncio
    async def test_execute_blocked_command(self):
        """Test blocked command by allowlist."""
        adapter = SubprocessToolAdapter(allowed_commands=["echo"])

        # Register a dangerous command
        adapter.register_simple("danger@v1", "rm {file}")
        # Manually add to commands but not allowlist
        adapter._allowed_commands.discard("rm")

        result = await adapter.execute(
            "danger@v1",
            {"file": "/important"},
            5000,
        )

        assert result.success is False
        assert result.error_code == "COMMAND_NOT_ALLOWED"
