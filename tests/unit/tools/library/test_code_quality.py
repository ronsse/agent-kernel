"""Tests for code quality tool library."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_kernel.tools.library.code_quality import (
    run_formatter,
    run_linter,
    run_tests,
    run_typecheck,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """Create a minimal fake git repo directory."""
    (tmp_path / ".git").mkdir()
    return tmp_path


def _make_completed_process(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["tool"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ---------------------------------------------------------------------------
# run_linter
# ---------------------------------------------------------------------------


class TestRunLinter:
    """Tests for run_linter."""

    @patch("agent_kernel.tools.library.code_quality._run_tool")
    def test_no_issues(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(stdout="[]")
        result = run_linter(repo_path=str(fake_repo))
        assert result["tool"] == "ruff"
        assert result["issue_count"] == 0
        assert result["issues"] == []

    @patch("agent_kernel.tools.library.code_quality._run_tool")
    def test_with_issues(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        issues = [
            {
                "code": "F401",
                "message": "unused import",
                "filename": "foo.py",
                "location": {"row": 1, "column": 1},
            },
            {
                "code": "E501",
                "message": "line too long",
                "filename": "bar.py",
                "location": {"row": 10, "column": 89},
            },
        ]
        mock_run.return_value = _make_completed_process(
            stdout=json.dumps(issues), returncode=1
        )
        result = run_linter(repo_path=str(fake_repo))
        assert result["issue_count"] == 2
        assert result["issues"][0]["code"] == "F401"

    @patch("agent_kernel.tools.library.code_quality._run_tool")
    def test_truncates_many_issues(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        issues = [{"code": f"E{i}", "message": f"issue {i}"} for i in range(150)]
        mock_run.return_value = _make_completed_process(
            stdout=json.dumps(issues), returncode=1
        )
        result = run_linter(repo_path=str(fake_repo))
        assert result["issue_count"] == 150
        assert len(result["issues"]) == 100
        assert result["truncated_issues"] is True

    def test_unsupported_linter(self, fake_repo: Path) -> None:
        with pytest.raises(ValueError, match="Unsupported linter"):
            run_linter(repo_path=str(fake_repo), tool="eslint")

    @patch("agent_kernel.tools.library.code_quality._run_tool")
    def test_json_parse_error(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(stdout="not json")
        result = run_linter(repo_path=str(fake_repo))
        assert result["parse_error"] is True


# ---------------------------------------------------------------------------
# run_typecheck
# ---------------------------------------------------------------------------


class TestRunTypecheck:
    """Tests for run_typecheck."""

    @patch("agent_kernel.tools.library.code_quality._run_tool")
    def test_clean_check(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(
            stdout="Success: no issues found in 5 source files\n",
            returncode=0,
        )
        result = run_typecheck(repo_path=str(fake_repo))
        assert result["success"] is True
        assert result["issue_count"] == 0
        assert result["error_count"] == 0

    @patch("agent_kernel.tools.library.code_quality._run_tool")
    def test_with_errors(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mypy_output = (
            "src/foo.py:10: error: Incompatible types [assignment]\n"
            "src/bar.py:5: warning: Unused variable [misc]\n"
            "Found 2 errors in 2 files\n"
        )
        mock_run.return_value = _make_completed_process(
            stdout=mypy_output, returncode=1
        )
        result = run_typecheck(repo_path=str(fake_repo))
        assert result["success"] is False
        assert result["issue_count"] == 2
        assert result["error_count"] == 1
        assert result["issues"][0]["file"] == "src/foo.py"
        assert result["issues"][0]["line"] == 10
        assert result["issues"][0]["severity"] == "error"

    def test_unsupported_typechecker(self, fake_repo: Path) -> None:
        with pytest.raises(ValueError, match="Unsupported type checker"):
            run_typecheck(repo_path=str(fake_repo), tool="pyright")


# ---------------------------------------------------------------------------
# run_tests
# ---------------------------------------------------------------------------


class TestRunTests:
    """Tests for run_tests."""

    @patch("agent_kernel.tools.library.code_quality._run_tool")
    def test_all_pass(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(
            stdout="10 passed in 1.5s\n", returncode=0
        )
        result = run_tests(repo_path=str(fake_repo))
        assert result["success"] is True
        assert result["passed"] == 10
        assert result["failed"] == 0

    @patch("agent_kernel.tools.library.code_quality._run_tool")
    def test_with_failures(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(
            stdout="3 passed, 2 failed in 3.0s\n", returncode=1
        )
        result = run_tests(repo_path=str(fake_repo))
        assert result["success"] is False
        assert result["passed"] == 3
        assert result["failed"] == 2

    @patch("agent_kernel.tools.library.code_quality._run_tool")
    def test_output_truncation(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        big_output = "x" * 10_000
        mock_run.return_value = _make_completed_process(
            stdout=big_output, returncode=0
        )
        result = run_tests(repo_path=str(fake_repo))
        assert result["truncated"] is True
        assert len(result["output"]) == 5_000

    def test_unsupported_runner(self, fake_repo: Path) -> None:
        with pytest.raises(ValueError, match="Unsupported test runner"):
            run_tests(repo_path=str(fake_repo), tool="unittest")


# ---------------------------------------------------------------------------
# run_formatter
# ---------------------------------------------------------------------------


class TestRunFormatter:
    """Tests for run_formatter."""

    @patch("agent_kernel.tools.library.code_quality._run_tool")
    def test_already_formatted(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(
            stdout="All checks passed!\n", returncode=0
        )
        result = run_formatter(repo_path=str(fake_repo), check_only=True)
        assert result["check_only"] is True
        assert result["files_changed"] == 0
        assert result["already_formatted"] is True

    @patch("agent_kernel.tools.library.code_quality._run_tool")
    def test_files_changed(
        self, mock_run: MagicMock, fake_repo: Path
    ) -> None:
        mock_run.return_value = _make_completed_process(
            stdout="src/foo.py\nsrc/bar.py\n", returncode=0
        )
        result = run_formatter(repo_path=str(fake_repo))
        assert result["files_changed"] == 2
        assert "src/foo.py" in result["changed_files"]

    def test_unsupported_formatter(self, fake_repo: Path) -> None:
        with pytest.raises(ValueError, match="Unsupported formatter"):
            run_formatter(repo_path=str(fake_repo), tool="black")
