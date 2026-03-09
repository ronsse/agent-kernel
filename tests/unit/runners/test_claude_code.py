"""Unit tests for ClaudeCodeRunner internals."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent_kernel.runners.claude_code import (
    ClaudeCodeConfig,
    ClaudeCodeRunner,
    _extract_wait_seconds,
    _is_rate_limited,
    _safe_json_loads,
    _truncate,
    DEFAULT_RATE_LIMIT_WAIT_SECONDS,
    MAX_RATE_LIMIT_WAIT_SECONDS,
)
from agent_kernel.runners.types import RunnerRequest


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_empty(self) -> None:
        text, truncated = _truncate("", 100)
        assert text == ""
        assert not truncated

    def test_short_string(self) -> None:
        text, truncated = _truncate("hello", 100)
        assert text == "hello"
        assert not truncated

    def test_exact_limit(self) -> None:
        text, truncated = _truncate("abcde", 5)
        assert text == "abcde"
        assert not truncated

    def test_over_limit_keeps_tail(self) -> None:
        text, truncated = _truncate("abcdefghij", 5)
        assert text == "fghij"
        assert truncated


# ---------------------------------------------------------------------------
# _safe_json_loads
# ---------------------------------------------------------------------------


class TestSafeJsonLoads:
    def test_valid_dict(self) -> None:
        result = _safe_json_loads('{"type": "system"}')
        assert result == {"type": "system"}

    def test_empty_string(self) -> None:
        assert _safe_json_loads("") is None

    def test_whitespace(self) -> None:
        assert _safe_json_loads("   ") is None

    def test_non_dict_json(self) -> None:
        result = _safe_json_loads("[1, 2, 3]")
        assert result == {"_non_dict_json": [1, 2, 3]}

    def test_invalid_json(self) -> None:
        assert _safe_json_loads("not json at all") is None


# ---------------------------------------------------------------------------
# _is_rate_limited
# ---------------------------------------------------------------------------


class TestIsRateLimited:
    def test_exit_zero_not_rate_limited(self) -> None:
        assert not _is_rate_limited("rate limit exceeded", "", 0)

    def test_rate_limit_in_stderr(self) -> None:
        assert _is_rate_limited("Error: rate limit exceeded", "", 1)

    def test_429_in_stderr(self) -> None:
        assert _is_rate_limited("HTTP 429 Too Many Requests", "", 1)

    def test_too_many_requests(self) -> None:
        assert _is_rate_limited("too many requests, please wait", "", 1)

    def test_quota_exceeded(self) -> None:
        assert _is_rate_limited("", "quota exceeded for this model", 1)

    def test_usage_limit(self) -> None:
        assert _is_rate_limited("usage limit reached", "", 2)

    def test_unrelated_error(self) -> None:
        assert not _is_rate_limited("file not found", "", 1)

    def test_empty_output(self) -> None:
        assert not _is_rate_limited("", "", 1)


# ---------------------------------------------------------------------------
# _extract_wait_seconds
# ---------------------------------------------------------------------------


class TestExtractWaitSeconds:
    def test_seconds_pattern(self) -> None:
        result = _extract_wait_seconds("try again in 30 seconds", "")
        assert result == 30

    def test_minutes_pattern(self) -> None:
        result = _extract_wait_seconds("retry after 2 minutes", "")
        assert result == 120

    def test_no_pattern_returns_default(self) -> None:
        result = _extract_wait_seconds("something went wrong", "")
        assert result == DEFAULT_RATE_LIMIT_WAIT_SECONDS

    def test_caps_at_max(self) -> None:
        result = _extract_wait_seconds("wait 9999 seconds", "")
        assert result == MAX_RATE_LIMIT_WAIT_SECONDS

    def test_pattern_in_stdout(self) -> None:
        result = _extract_wait_seconds("", "retry in 45 seconds please")
        assert result == 45


# ---------------------------------------------------------------------------
# ClaudeCodeRunner._build_cmd
# ---------------------------------------------------------------------------


class TestBuildCmd:
    def _make_runner(self, **kwargs: object) -> ClaudeCodeRunner:
        config = ClaudeCodeConfig(**kwargs)  # type: ignore[arg-type]
        runner = ClaudeCodeRunner(config)
        return runner

    def _make_request(self, **kwargs: object) -> RunnerRequest:
        defaults = {
            "runner_id": "claude",
            "prompt": "hello world",
            "workspace_path": "/tmp/test",
        }
        defaults.update(kwargs)
        return RunnerRequest(**defaults)  # type: ignore[arg-type]

    @patch("agent_kernel.runners.claude_code.shutil.which", return_value="/usr/bin/claude")
    def test_basic_cmd(self, _mock_which: MagicMock) -> None:
        runner = self._make_runner()
        req = self._make_request()
        cmd = runner._build_cmd(req)

        assert cmd[0] == "/usr/bin/claude"
        assert "--print" in cmd
        assert cmd[-1] == "hello world"

    @patch("agent_kernel.runners.claude_code.shutil.which", return_value="/usr/bin/claude")
    def test_output_format(self, _mock_which: MagicMock) -> None:
        runner = self._make_runner()
        req = self._make_request(output_format="stream-json")
        cmd = runner._build_cmd(req)
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "stream-json"

    @patch("agent_kernel.runners.claude_code.shutil.which", return_value="/usr/bin/claude")
    def test_read_only_restricts_tools(self, _mock_which: MagicMock) -> None:
        runner = self._make_runner()
        req = self._make_request(allow_write=False)
        cmd = runner._build_cmd(req)
        assert "--allowedTools" in cmd

    @patch("agent_kernel.runners.claude_code.shutil.which", return_value="/usr/bin/claude")
    def test_write_allowed_no_tool_restriction(self, _mock_which: MagicMock) -> None:
        runner = self._make_runner()
        req = self._make_request(allow_write=True)
        cmd = runner._build_cmd(req)
        assert "--allowedTools" not in cmd

    @patch("agent_kernel.runners.claude_code.shutil.which", return_value="/usr/bin/claude")
    def test_budget_from_request(self, _mock_which: MagicMock) -> None:
        runner = self._make_runner()
        req = self._make_request(max_budget_usd=2.50)
        cmd = runner._build_cmd(req)
        assert "--max-budget-usd" in cmd
        idx = cmd.index("--max-budget-usd")
        assert cmd[idx + 1] == "2.5"

    @patch("agent_kernel.runners.claude_code.shutil.which", return_value="/usr/bin/claude")
    def test_budget_from_config_default(self, _mock_which: MagicMock) -> None:
        runner = self._make_runner(default_max_budget_usd=5.0)
        req = self._make_request()
        cmd = runner._build_cmd(req)
        assert "--max-budget-usd" in cmd
        idx = cmd.index("--max-budget-usd")
        assert cmd[idx + 1] == "5.0"

    @patch("agent_kernel.runners.claude_code.shutil.which", return_value="/usr/bin/claude")
    def test_no_budget_when_unset(self, _mock_which: MagicMock) -> None:
        runner = self._make_runner()
        req = self._make_request()
        cmd = runner._build_cmd(req)
        assert "--max-budget-usd" not in cmd


# ---------------------------------------------------------------------------
# Rate limit retry loop
# ---------------------------------------------------------------------------


class TestRateLimitRetry:
    @patch("agent_kernel.runners.claude_code.time.sleep")
    def test_retries_on_rate_limit(self, mock_sleep: MagicMock) -> None:
        """Runner should retry when rate limited, then succeed."""
        runner = ClaudeCodeRunner(
            ClaudeCodeConfig(rate_limit_max_retries=2, rate_limit_default_wait_seconds=1)
        )

        rate_limited_response = MagicMock()
        rate_limited_response.logs = {"stderr_tail": "rate limit exceeded", "stdout_tail": ""}
        rate_limited_response.exit_code = 1

        success_response = MagicMock()
        success_response.logs = {"stderr_tail": "", "stdout_tail": ""}
        success_response.exit_code = 0

        with patch.object(runner, "_run_once", side_effect=[rate_limited_response, success_response]):
            req = RunnerRequest(runner_id="claude", prompt="test", workspace_path="/tmp")
            result = runner.run(req)

        assert result is success_response
        mock_sleep.assert_called_once()

    @patch("agent_kernel.runners.claude_code.time.sleep")
    def test_exhausted_retries(self, mock_sleep: MagicMock) -> None:
        """Runner should return last response after exhausting retries."""
        runner = ClaudeCodeRunner(
            ClaudeCodeConfig(rate_limit_max_retries=1, rate_limit_default_wait_seconds=1)
        )

        rate_limited_response = MagicMock()
        rate_limited_response.logs = {"stderr_tail": "rate limit exceeded", "stdout_tail": ""}
        rate_limited_response.exit_code = 1

        with patch.object(runner, "_run_once", return_value=rate_limited_response):
            req = RunnerRequest(runner_id="claude", prompt="test", workspace_path="/tmp")
            result = runner.run(req)

        assert result is rate_limited_response
        assert mock_sleep.call_count == 1  # Retried once, then gave up

    def test_no_retry_on_success(self) -> None:
        """Runner should not retry when command succeeds."""
        runner = ClaudeCodeRunner(ClaudeCodeConfig(rate_limit_max_retries=3))

        success_response = MagicMock()
        success_response.logs = {"stderr_tail": "", "stdout_tail": ""}
        success_response.exit_code = 0

        with patch.object(runner, "_run_once", return_value=success_response) as mock_run:
            req = RunnerRequest(runner_id="claude", prompt="test", workspace_path="/tmp")
            result = runner.run(req)

        assert result is success_response
        mock_run.assert_called_once()
