"""GitHub operations for Tool Broker usage.

Uses the ``gh`` CLI for PR management. Requires ``gh`` to be installed
and authenticated.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.tools.library.git_ops import _validate_repo_path

logger = structlog.get_logger(__name__)

_SUBPROCESS_TIMEOUT_S = 30


def _run_gh(
    repo_path: Path,
    args: list[str],
    *,
    timeout: int = _SUBPROCESS_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Run a gh CLI command safely (no shell=True)."""
    cmd = ["gh", *args]
    try:
        return subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo_path),
            check=False,
        )
    except FileNotFoundError:
        msg = (
            "GitHub CLI (gh) is not installed. "
            "Install from https://cli.github.com/"
        )
        raise ValueError(msg) from None
    except subprocess.TimeoutExpired:
        msg = f"gh command timed out after {timeout}s: gh {' '.join(args)}"
        raise TimeoutError(msg) from None


def gh_pr_list(
    *,
    repo_path: str,
    state: str = "open",
    limit: int = 30,
    base: str | None = None,
    author: str | None = None,
) -> dict[str, Any]:
    """List pull requests (``github.pr.list@v1``)."""
    repo = _validate_repo_path(repo_path)

    fields = (
        "number,title,state,author,baseRefName,"
        "headRefName,createdAt,updatedAt,url"
    )
    args = [
        "pr", "list",
        f"--state={state}",
        f"--limit={min(limit, _SUBPROCESS_TIMEOUT_S)}",
        f"--json={fields}",
    ]
    if base:
        args.append(f"--base={base}")
    if author:
        args.append(f"--author={author}")

    result = _run_gh(repo, args)
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "returncode": result.returncode}

    prs: list[dict[str, Any]] = []
    if result.stdout.strip():
        try:
            prs = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                "error": "Failed to parse gh output",
                "raw_output": result.stdout[:2000],
            }

    return {"pull_requests": prs, "count": len(prs)}


def gh_pr_create(
    *,
    repo_path: str,
    title: str,
    body: str,
    base: str = "main",
    head: str | None = None,
    draft: bool = False,
) -> dict[str, Any]:
    """Create a pull request (``github.pr.create@v1``).

    This is an external write and should require approval.
    """
    repo = _validate_repo_path(repo_path)

    if not title.strip():
        msg = "PR title must not be empty"
        raise ValueError(msg)

    args = [
        "pr", "create",
        "--title", title,
        "--body", body,
        "--base", base,
    ]
    if head:
        args.extend(["--head", head])
    if draft:
        args.append("--draft")

    result = _run_gh(repo, args, timeout=60)
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "returncode": result.returncode}

    # gh pr create outputs the PR URL on success
    pr_url = result.stdout.strip()

    return {
        "success": True,
        "url": pr_url,
        "title": title,
        "base": base,
        "draft": draft,
    }
