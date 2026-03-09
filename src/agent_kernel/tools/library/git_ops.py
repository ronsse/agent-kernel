"""Git operations for Tool Broker usage.

Provides read-only and write capabilities for git repositories.
All operations validate repo paths against CODE_TOOLS_ALLOWED_REPO_ROOTS.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_MAX_DIFF_CHARS = 10_000
_MAX_LOG_ENTRIES = 100
_MAX_OUTPUT_CHARS = 50_000
_SUBPROCESS_TIMEOUT_S = 30
_REF_PATTERN = re.compile(r"^[a-zA-Z0-9._/~^@{}\-]+$")


def _get_allowed_roots() -> list[Path]:
    """Return the list of allowed repository root directories."""
    raw = os.environ.get("CODE_TOOLS_ALLOWED_REPO_ROOTS", "")
    if not raw.strip():
        return []
    return [Path(p.strip()).resolve() for p in raw.split(",") if p.strip()]



def _get_denied_substrings() -> list[str]:
    raw = os.environ.get("CODE_TOOLS_DENY_REPO_SUBSTRINGS", "")
    if not raw.strip():
        return []
    return [s.strip().lower() for s in raw.split(",") if s.strip()]

def _validate_repo_path(repo_path: str) -> Path:
    """Validate that *repo_path* is an existing git repo within allowed roots.

    Raises ``ValueError`` on any problem so callers can let it propagate as a
    structured error through the broker.
    """
    path = Path(repo_path).resolve()
    if not path.is_dir():
        msg = f"Repository path does not exist: {repo_path}"
        raise ValueError(msg)
    if not (path / ".git").exists():
        msg = f"Not a git repository (no .git): {repo_path}"
        raise ValueError(msg)

    allowed = _get_allowed_roots()
    if allowed and not any(
        path == root or root in path.parents for root in allowed
    ):
        msg = (
            f"Repository {repo_path} is not within allowed roots. "
            f"Set CODE_TOOLS_ALLOWED_REPO_ROOTS to allow it."
        )
        raise ValueError(msg)

    denied = _get_denied_substrings()
    if denied:
        path_lower = str(path).lower()
        for substring in denied:
            if substring in path_lower:
                msg = f"Repository path contains denied substring: {substring}"
                raise ValueError(msg)

    return path


def _validate_ref(ref: str) -> str:
    """Validate a git ref (branch name, tag, commit) against a safe pattern."""
    if ".." in ref:
        msg = f"Invalid git ref (contains '..'): {ref!r}"
        raise ValueError(msg)
    if not _REF_PATTERN.match(ref):
        msg = f"Invalid git ref: {ref!r}"
        raise ValueError(msg)
    return ref


def _run_git(
    repo_path: Path,
    args: list[str],
    *,
    timeout: int = _SUBPROCESS_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Run a git command safely (no shell=True)."""
    cmd = ["git", "-C", str(repo_path), *args]
    try:
        return subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        msg = f"Git command timed out after {timeout}s: git {' '.join(args)}"
        raise TimeoutError(msg) from None


def _truncate(text: str, max_chars: int) -> dict[str, Any]:
    """Return text with truncation metadata."""
    if len(text) <= max_chars:
        return {"text": text, "truncated": False}
    return {
        "text": text[:max_chars],
        "truncated": True,
        "original_length": len(text),
    }


# ---------------------------------------------------------------------------
# Read-only capabilities
# ---------------------------------------------------------------------------


def git_status(*, repo_path: str) -> dict[str, Any]:
    """Get working tree status (``git.status@v1``)."""
    path = _validate_repo_path(repo_path)
    result = _run_git(path, ["status", "--porcelain=v1"])
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "returncode": result.returncode}

    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    files: list[dict[str, str]] = []
    for line in lines:
        status_code = line[:2]
        filepath = line[3:]
        files.append({"status": status_code.strip(), "path": filepath})

    branch_result = _run_git(path, ["branch", "--show-current"])
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None

    return {
        "branch": branch,
        "files": files,
        "file_count": len(files),
        "clean": len(files) == 0,
    }


def git_diff(
    *,
    repo_path: str,
    ref: str | None = None,
    staged: bool = False,
    path_filter: str | None = None,
) -> dict[str, Any]:
    """Show diff output (``git.diff@v1``)."""
    repo = _validate_repo_path(repo_path)
    args = ["diff"]
    if staged:
        args.append("--cached")
    if ref:
        args.append(_validate_ref(ref))
    if path_filter:
        args.extend(["--", path_filter])

    result = _run_git(repo, args)
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "returncode": result.returncode}

    output = _truncate(result.stdout, _MAX_DIFF_CHARS)
    stat_result = _run_git(repo, [*args[:], "--stat"])
    stat_lines = stat_result.stdout.strip().splitlines()
    stat_summary = stat_lines[-1] if stat_lines else ""

    return {
        "diff": output["text"],
        "truncated": output.get("truncated", False),
        "stat_summary": stat_summary,
    }


def git_log(
    *,
    repo_path: str,
    max_count: int = 20,
    ref: str | None = None,
    oneline: bool = True,
    since: str | None = None,
    author: str | None = None,
) -> dict[str, Any]:
    """Show commit log (``git.log@v1``)."""
    repo = _validate_repo_path(repo_path)
    count = min(max_count, _MAX_LOG_ENTRIES)

    args = ["log", f"--max-count={count}"]
    if oneline:
        args.append("--oneline")
    else:
        args.append("--format=%H|%an|%ae|%ai|%s")
    if since:
        args.append(f"--since={since}")
    if author:
        args.append(f"--author={author}")
    if ref:
        args.append(_validate_ref(ref))

    result = _run_git(repo, args)
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "returncode": result.returncode}

    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]

    commits: list[dict[str, Any]] = []
    if oneline:
        for line in lines:
            parts = line.split(" ", 1)
            commits.append({
                "hash": parts[0],
                "message": parts[1] if len(parts) > 1 else "",
            })
    else:
        for line in lines:
            parts = line.split("|", 4)
            if len(parts) >= 5:  # noqa: PLR2004
                commits.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "email": parts[2],
                    "date": parts[3],
                    "message": parts[4],
                })

    return {"commits": commits, "count": len(commits)}


def git_branches(
    *,
    repo_path: str,
    remote: bool = False,
    all_branches: bool = False,
) -> dict[str, Any]:
    """List branches (``git.branches@v1``)."""
    repo = _validate_repo_path(repo_path)
    args = [
        "branch",
        "--format=%(refname:short)|%(objectname:short)|%(upstream:short)",
    ]
    if all_branches:
        args.append("--all")
    elif remote:
        args.append("--remotes")

    result = _run_git(repo, args)
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "returncode": result.returncode}

    current_result = _run_git(repo, ["branch", "--show-current"])
    current = (
        current_result.stdout.strip() if current_result.returncode == 0 else None
    )

    branches = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 2)
        name = parts[0].strip()
        upstream = (
            parts[2].strip()
            if len(parts) > 2 and parts[2].strip()  # noqa: PLR2004
            else None
        )
        branches.append({
            "name": name,
            "hash": parts[1].strip() if len(parts) > 1 else "",
            "upstream": upstream,
            "current": name == current,
        })

    return {"branches": branches, "count": len(branches), "current": current}


# ---------------------------------------------------------------------------
# Write capabilities
# ---------------------------------------------------------------------------


def git_commit(
    *,
    repo_path: str,
    message: str,
    files: list[str] | None = None,
    all_tracked: bool = False,
) -> dict[str, Any]:
    """Stage and commit changes (``git.commit@v1``).

    Either *files* (specific paths) or *all_tracked* (``git add -u``) must
    be provided.  Never uses ``git add -A`` to avoid capturing unintended
    files.
    """
    repo = _validate_repo_path(repo_path)

    if not message.strip():
        msg = "Commit message must not be empty"
        raise ValueError(msg)

    if files:
        for f in files:
            add_result = _run_git(repo, ["add", "--", f])
            if add_result.returncode != 0:
                return {
                    "error": f"Failed to stage {f}: {add_result.stderr.strip()}",
                    "returncode": add_result.returncode,
                }
    elif all_tracked:
        add_result = _run_git(repo, ["add", "-u"])
        if add_result.returncode != 0:
            return {
                "error": f"Failed to stage tracked files: {add_result.stderr.strip()}",
                "returncode": add_result.returncode,
            }
    else:
        msg = "Either 'files' or 'all_tracked' must be provided"
        raise ValueError(msg)

    result = _run_git(repo, ["commit", "-m", message])
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "returncode": result.returncode}

    hash_result = _run_git(repo, ["rev-parse", "HEAD"])
    commit_hash = hash_result.stdout.strip() if hash_result.returncode == 0 else None

    return {
        "success": True,
        "commit_hash": commit_hash,
        "message": message,
        "output": result.stdout.strip(),
    }


def git_create_branch(
    *,
    repo_path: str,
    branch_name: str,
    start_point: str | None = None,
    checkout: bool = True,
) -> dict[str, Any]:
    """Create a new branch (``git.branch.create@v1``)."""
    repo = _validate_repo_path(repo_path)
    _validate_ref(branch_name)
    if start_point:
        _validate_ref(start_point)

    args = (
        ["checkout", "-b", branch_name] if checkout else ["branch", branch_name]
    )
    if start_point:
        args.append(start_point)

    result = _run_git(repo, args)
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "returncode": result.returncode}

    return {
        "success": True,
        "branch": branch_name,
        "checked_out": checkout,
        "start_point": start_point,
    }


def git_push(
    *,
    repo_path: str,
    remote: str = "origin",
    branch: str | None = None,
    set_upstream: bool = False,
) -> dict[str, Any]:
    """Push commits to remote (``git.push@v1``).

    This is an external write and should require approval.
    """
    repo = _validate_repo_path(repo_path)
    _validate_ref(remote)
    # Deny pushing to protected branches (keep merges to main manual).
    target_branch = branch
    if target_branch is None:
        br = _run_git(repo, ["branch", "--show-current"])
        target_branch = br.stdout.strip() if br.returncode == 0 else None
    if target_branch in {"main", "master"}:
        raise ValueError("Refusing to push protected branch (main/master). Create a feature branch and PR instead.")


    args = ["push"]
    if set_upstream:
        args.append("--set-upstream")
    args.append(remote)
    if branch:
        args.append(_validate_ref(branch))

    result = _run_git(repo, args, timeout=60)
    if result.returncode != 0:
        return {"error": result.stderr.strip(), "returncode": result.returncode}

    return {
        "success": True,
        "remote": remote,
        "branch": branch,
        "output": result.stderr.strip() or result.stdout.strip(),
    }
