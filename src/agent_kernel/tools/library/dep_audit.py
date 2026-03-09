"""Dependency auditing tools for Tool Broker usage.

Provides security and freshness checks for Python and Node.js dependencies.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.tools.library.git_ops import _validate_repo_path

logger = structlog.get_logger(__name__)

_SUBPROCESS_TIMEOUT_S = 60
_MAX_OUTPUT_CHARS = 5_000
_MAX_ITEMS = 50


def _run_tool(
    args: list[str],
    *,
    cwd: Path,
    timeout: int = _SUBPROCESS_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Run a dependency tool safely (no shell=True)."""
    try:
        return subprocess.run(  # noqa: S603
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            check=False,
        )
    except FileNotFoundError:
        msg = f"Tool not found: {args[0]}"
        raise ValueError(msg) from None
    except subprocess.TimeoutExpired:
        msg = f"Command timed out after {timeout}s: {' '.join(args)}"
        raise TimeoutError(msg) from None


def audit_python_deps(
    *,
    repo_path: str,
    check_outdated: bool = True,
    check_vulnerabilities: bool = True,
) -> dict[str, Any]:
    """Audit Python dependencies (``deps.audit.python@v1``).

    Uses ``pip list --outdated`` for freshness and ``pip-audit`` for
    known vulnerabilities.
    """
    repo = _validate_repo_path(repo_path)
    results: dict[str, Any] = {"language": "python"}

    if check_outdated:
        outdated_result = _run_tool(
            ["pip", "list", "--outdated", "--format=json"],
            cwd=repo,
        )
        if outdated_result.returncode == 0 and outdated_result.stdout.strip():
            try:
                outdated = json.loads(outdated_result.stdout)
                results["outdated"] = outdated[:_MAX_ITEMS]
                results["outdated_count"] = len(outdated)
                results["outdated_truncated"] = len(outdated) > _MAX_ITEMS
            except json.JSONDecodeError:
                results["outdated_error"] = "Failed to parse pip output"
        else:
            results["outdated"] = []
            results["outdated_count"] = 0

    if check_vulnerabilities:
        vuln_result = _run_tool(
            ["pip-audit", "--format=json"],
            cwd=repo,
        )
        if vuln_result.stdout.strip():
            try:
                vuln_data = json.loads(vuln_result.stdout)
                vulnerabilities = (
                    vuln_data if isinstance(vuln_data, list) else []
                )
                results["vulnerabilities"] = vulnerabilities[:_MAX_ITEMS]
                results["vulnerability_count"] = len(vulnerabilities)
            except json.JSONDecodeError:
                results["vulnerability_error"] = (
                    "Failed to parse pip-audit output"
                )
                results["vulnerability_count"] = 0
        elif vuln_result.returncode != 0:
            results["vulnerability_error"] = (
                vuln_result.stderr.strip()[:500] or "pip-audit failed"
            )
            results["vulnerability_count"] = 0
        else:
            results["vulnerabilities"] = []
            results["vulnerability_count"] = 0

    return results


def audit_node_deps(
    *,
    repo_path: str,
) -> dict[str, Any]:
    """Audit Node.js dependencies (``deps.audit.node@v1``).

    Uses ``npm audit --json`` for known vulnerabilities.
    Requires a ``package.json`` in the repo root.
    """
    repo = _validate_repo_path(repo_path)

    if not (repo / "package.json").exists():
        return {
            "language": "node",
            "error": "No package.json found in repository",
        }

    result = _run_tool(["npm", "audit", "--json"], cwd=repo)

    # npm audit returns non-zero when vulnerabilities exist
    audit_data: dict[str, Any] = {}
    if result.stdout.strip():
        try:
            audit_data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {
                "language": "node",
                "error": "Failed to parse npm audit output",
                "raw_output": result.stdout[:_MAX_OUTPUT_CHARS],
            }

    metadata = audit_data.get("metadata", {})
    vulnerabilities_summary = metadata.get("vulnerabilities", {})

    return {
        "language": "node",
        "total_dependencies": metadata.get("totalDependencies", 0),
        "vulnerabilities": vulnerabilities_summary,
        "advisories_count": len(audit_data.get("advisories", {})),
        "audit_level": metadata.get("auditReportVersion", "unknown"),
    }
