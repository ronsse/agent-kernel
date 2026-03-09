"""Code quality tools for Tool Broker usage.

Provides lint, typecheck, test, and format capabilities.
All operations validate repo paths via git_ops._validate_repo_path.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

from agent_kernel.tools.library.git_ops import _validate_repo_path

_MAX_OUTPUT_CHARS = 5_000
_MAX_ISSUES = 100
_SUBPROCESS_TIMEOUT_S = 120

_SUPPORTED_LINTERS = {"ruff"}
_SUPPORTED_TYPECHECKERS = {"mypy"}
_SUPPORTED_TEST_RUNNERS = {"pytest"}
_SUPPORTED_FORMATTERS = {"ruff"}


def _validate_paths(paths: list[str], repo: Path) -> list[str]:
    """Validate that paths don't escape the repository root."""
    for p in paths:
        resolved = (repo / p).resolve()
        if not str(resolved).startswith(str(repo)):
            msg = f"Path escapes repository root: {p}"
            raise ValueError(msg)
    return paths


def _run_tool(
    args: list[str],
    *,
    cwd: Path,
    timeout: int = _SUBPROCESS_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Run a code quality tool safely (no shell=True)."""
    try:
        return subprocess.run(  # noqa: S603
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            check=False,
        )
    except subprocess.TimeoutExpired:
        msg = f"Command timed out after {timeout}s: {' '.join(args)}"
        raise TimeoutError(msg) from None


def _truncate(text: str, max_chars: int = _MAX_OUTPUT_CHARS) -> dict[str, Any]:
    """Return text with truncation metadata."""
    if len(text) <= max_chars:
        return {"text": text, "truncated": False}
    return {
        "text": text[:max_chars],
        "truncated": True,
        "original_length": len(text),
    }


# ---------------------------------------------------------------------------
# Linting
# ---------------------------------------------------------------------------


def run_linter(
    *,
    repo_path: str,
    tool: str = "ruff",
    paths: list[str] | None = None,
    fix: bool = False,
) -> dict[str, Any]:
    """Run a linter on a repository (``code.lint@v1``).

    Returns structured issue data when possible (ruff --output-format=json).
    """
    if tool not in _SUPPORTED_LINTERS:
        msg = f"Unsupported linter: {tool!r}. Supported: {_SUPPORTED_LINTERS}"
        raise ValueError(msg)

    repo = _validate_repo_path(repo_path)
    args = [tool, "check", "--output-format=json"]
    if fix:
        args.append("--fix")
    if paths:
        args.extend(_validate_paths(paths, repo))
    else:
        args.append(".")

    result = _run_tool(args, cwd=repo)

    # ruff returns 1 when there are lint issues, which is not an error
    issues: list[dict[str, Any]] = []
    if result.stdout.strip():
        try:
            issues = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                "tool": tool,
                "raw_output": _truncate(result.stdout)["text"],
                "parse_error": True,
                "returncode": result.returncode,
            }

    return {
        "tool": tool,
        "issue_count": len(issues),
        "issues": issues[:_MAX_ISSUES],
        "truncated_issues": len(issues) > _MAX_ISSUES,
        "fixed": fix,
        "returncode": result.returncode,
    }


# ---------------------------------------------------------------------------
# Type checking
# ---------------------------------------------------------------------------

_MYPY_LINE_PATTERN = re.compile(
    r"^(.+?):(\d+):\s*(error|warning|note):\s*(.+?)(?:\s+\[(.+)\])?$"
)


def run_typecheck(
    *,
    repo_path: str,
    tool: str = "mypy",
    paths: list[str] | None = None,
) -> dict[str, Any]:
    """Run type checker on a repository (``code.typecheck@v1``)."""
    if tool not in _SUPPORTED_TYPECHECKERS:
        msg = (
            f"Unsupported type checker: {tool!r}. "
            f"Supported: {_SUPPORTED_TYPECHECKERS}"
        )
        raise ValueError(msg)

    repo = _validate_repo_path(repo_path)
    args = [tool]
    if paths:
        args.extend(_validate_paths(paths, repo))
    else:
        args.append(".")

    result = _run_tool(args, cwd=repo)

    # Parse mypy output
    issues: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        match = _MYPY_LINE_PATTERN.match(line)
        if match:
            issues.append({
                "file": match.group(1),
                "line": int(match.group(2)),
                "severity": match.group(3),
                "message": match.group(4),
                "code": match.group(5),
            })

    # Extract summary line (usually the last non-empty line)
    summary = ""
    for line in reversed(result.stdout.splitlines()):
        if line.strip() and not _MYPY_LINE_PATTERN.match(line):
            summary = line.strip()
            break

    error_count = sum(1 for i in issues if i["severity"] == "error")

    return {
        "tool": tool,
        "issue_count": len(issues),
        "error_count": error_count,
        "issues": issues[:_MAX_ISSUES],
        "truncated_issues": len(issues) > _MAX_ISSUES,
        "summary": summary,
        "success": result.returncode == 0,
        "returncode": result.returncode,
    }


# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

_PYTEST_SUMMARY_PATTERN = re.compile(
    r"(?:=+\s+)?(\d+)\s+passed"
    r"(?:.*?(\d+)\s+failed)?"
    r"(?:.*?(\d+)\s+error)?"
    r"(?:.*?(\d+)\s+skipped)?"
)


def run_tests(
    *,
    repo_path: str,
    tool: str = "pytest",
    paths: list[str] | None = None,
    marker: str | None = None,
    keyword: str | None = None,
) -> dict[str, Any]:
    """Run tests on a repository (``code.test@v1``)."""
    if tool not in _SUPPORTED_TEST_RUNNERS:
        msg = (
            f"Unsupported test runner: {tool!r}. "
            f"Supported: {_SUPPORTED_TEST_RUNNERS}"
        )
        raise ValueError(msg)

    repo = _validate_repo_path(repo_path)
    args = [tool, "--tb=short", "-q"]
    if marker:
        args.extend(["-m", marker])
    if keyword:
        args.extend(["-k", keyword])
    if paths:
        args.extend(_validate_paths(paths, repo))

    result = _run_tool(args, cwd=repo)
    output = _truncate(result.stdout + result.stderr)

    # Parse summary from pytest output
    passed = 0
    failed = 0
    errors = 0
    skipped = 0
    for line in result.stdout.splitlines():
        match = _PYTEST_SUMMARY_PATTERN.search(line)
        if match:
            passed = int(match.group(1)) if match.group(1) else 0
            failed = int(match.group(2)) if match.group(2) else 0
            errors = int(match.group(3)) if match.group(3) else 0
            skipped = int(match.group(4)) if match.group(4) else 0

    return {
        "tool": tool,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "success": result.returncode == 0,
        "output": output["text"],
        "truncated": output.get("truncated", False),
        "returncode": result.returncode,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def run_formatter(
    *,
    repo_path: str,
    tool: str = "ruff",
    paths: list[str] | None = None,
    check_only: bool = False,
) -> dict[str, Any]:
    """Run code formatter on a repository (``code.format@v1``).

    Use *check_only=True* to report without modifying files.
    """
    if tool not in _SUPPORTED_FORMATTERS:
        msg = (
            f"Unsupported formatter: {tool!r}. "
            f"Supported: {_SUPPORTED_FORMATTERS}"
        )
        raise ValueError(msg)

    repo = _validate_repo_path(repo_path)
    args = [tool, "format"]
    if check_only:
        args.append("--check")
    if paths:
        args.extend(_validate_paths(paths, repo))
    else:
        args.append(".")

    result = _run_tool(args, cwd=repo)

    # Count files from ruff format output
    changed_files: list[str] = []
    for raw_line in result.stdout.splitlines():
        stripped = raw_line.strip()
        if stripped and not stripped.startswith(("All", "Oh no")):
            changed_files.append(stripped)

    return {
        "tool": tool,
        "check_only": check_only,
        "files_changed": len(changed_files),
        "changed_files": changed_files[:50],
        "already_formatted": result.returncode == 0 and not changed_files,
        "returncode": result.returncode,
    }
