"""Schema migrations for version evolution.

This module provides upcasters to migrate older schema versions to newer ones.
Upcasters run on load, before Pydantic validation, ensuring backwards
compatibility with stored data.

Rules:
- Upcasters must be deterministic
- Upcasters should add missing fields with sensible defaults
- Breaking changes require new major version and explicit upcasters
- Additive changes only within a minor version
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from agent_kernel.core.schemas.base import SCHEMA_VERSION, get_kernel_version

logger = structlog.get_logger(__name__)

# Type alias for upcaster functions
Upcaster = Callable[[dict[str, Any]], dict[str, Any]]

# Registry of upcasters: (from_version, to_version) -> upcaster function
UPCASTERS: dict[tuple[str, str], Upcaster] = {}


def register(from_version: str, to_version: str) -> Callable[[Upcaster], Upcaster]:
    """Decorator to register an upcaster function.

    Args:
        from_version: Source schema version.
        to_version: Target schema version.

    Returns:
        Decorator that registers the upcaster.

    Example:
        @register("1.0.0", "1.0.1")
        def upcast_1_0_0_to_1_0_1(payload: dict) -> dict:
            payload.setdefault("new_field", "default_value")
            return payload
    """

    def decorator(fn: Upcaster) -> Upcaster:
        UPCASTERS[(from_version, to_version)] = fn
        logger.debug(
            "upcaster_registered",
            from_version=from_version,
            to_version=to_version,
        )
        return fn

    return decorator


def upcast(payload: dict[str, Any], to_version: str = SCHEMA_VERSION) -> dict[str, Any]:
    """Upcast a payload from its current version to the target version.

    Args:
        payload: The payload dict to upcast.
        to_version: Target schema version (defaults to current SCHEMA_VERSION).

    Returns:
        The upcasted payload with schema_version updated.

    Raises:
        ValueError: If no upcaster chain exists from current to target version.
    """
    current_version = payload.get("schema_version") or "1.0.0"

    if current_version == to_version:
        return payload

    # Find direct upcaster
    key = (current_version, to_version)
    if key in UPCASTERS:
        result = UPCASTERS[key](payload.copy())
        result["schema_version"] = to_version
        logger.debug(
            "payload_upcasted",
            from_version=current_version,
            to_version=to_version,
        )
        return result

    # Try to find a chain of upcasters
    # Build a path from current to target using available upcasters
    chain = _find_upcast_chain(current_version, to_version)
    if chain:
        result = payload.copy()
        for from_v, to_v in chain:
            result = UPCASTERS[(from_v, to_v)](result)
            result["schema_version"] = to_v
        logger.debug(
            "payload_upcasted_via_chain",
            from_version=current_version,
            to_version=to_version,
            chain_length=len(chain),
        )
        return result

    msg = f"No upcaster chain from version {current_version} to {to_version}"
    raise ValueError(msg)


def _find_upcast_chain(
    from_version: str, to_version: str
) -> list[tuple[str, str]] | None:
    """Find a chain of upcasters from one version to another.

    Uses BFS to find the shortest path.
    """
    if from_version == to_version:
        return []

    # Build adjacency list from upcasters
    adjacency: dict[str, list[str]] = {}
    for src, dst in UPCASTERS:
        if src not in adjacency:
            adjacency[src] = []
        adjacency[src].append(dst)

    # BFS to find path
    queue: list[tuple[str, list[tuple[str, str]]]] = [(from_version, [])]
    visited: set[str] = {from_version}

    while queue:
        current, path = queue.pop(0)
        for neighbor in adjacency.get(current, []):
            new_path = [*path, (current, neighbor)]
            if neighbor == to_version:
                return new_path
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, new_path))

    return None


def can_upcast(from_version: str, to_version: str = SCHEMA_VERSION) -> bool:
    """Check if an upcast path exists between versions.

    Args:
        from_version: Source schema version.
        to_version: Target schema version.

    Returns:
        True if upcast is possible, False otherwise.
    """
    if from_version == to_version:
        return True
    return _find_upcast_chain(from_version, to_version) is not None


# =============================================================================
# v1.0.0 -> v1.0.1 Unified Upcaster
# =============================================================================


@register("1.0.0", "1.0.1")
def upcast_v1_0_0_to_v1_0_1(payload: dict[str, Any]) -> dict[str, Any]:
    """Unified upcaster from v1.0.0 to v1.0.1.

    Handles all schema types:
    - DecisionTrace: workflow_id, llm_calls, tool_calls policy fields
    - ContextPacket: kernel_version
    - Plan: kernel_version, evidence_refs on actions
    - ToolCallRecord: policy fields, kernel_version
    - Event: occurred_at, recorded_at, payload rename
    """
    # Always add kernel_version if missing
    if "kernel_version" not in payload:
        payload["kernel_version"] = get_kernel_version()

    # DecisionTrace-specific: workflow_id and llm_calls
    if "run_id" in payload:
        if "workflow_id" not in payload:
            payload["workflow_id"] = payload.get("run_id", "unknown")
        if "llm_calls" not in payload:
            payload["llm_calls"] = []

    # Update tool_calls with new policy fields (for DecisionTrace)
    for tc in payload.get("tool_calls", []):
        if "effective_side_effect" not in tc:
            tc["effective_side_effect"] = tc.get("side_effect", "none")
        if "effective_requires_approval" not in tc:
            tc["effective_requires_approval"] = False
        if "requested_side_effect" not in tc:
            tc["requested_side_effect"] = None
        if "requested_requires_approval" not in tc:
            tc["requested_requires_approval"] = None

    # Plan-specific: evidence_refs on actions
    for action in payload.get("actions", []):
        if "evidence_refs" not in action:
            action["evidence_refs"] = []

    # ToolCallRecord-specific (standalone): policy fields
    if "capability_name" in payload and "tool_call_id" in payload:
        if "effective_side_effect" not in payload:
            payload["effective_side_effect"] = "none"
        if "effective_requires_approval" not in payload:
            payload["effective_requires_approval"] = False
        if "requested_side_effect" not in payload:
            payload["requested_side_effect"] = None
        if "requested_requires_approval" not in payload:
            payload["requested_requires_approval"] = None

    # Event-specific: occurred_at, recorded_at, payload
    if "event_type" in payload:
        timestamp = payload.get("timestamp")
        if "occurred_at" not in payload:
            payload["occurred_at"] = timestamp
        if "recorded_at" not in payload:
            payload["recorded_at"] = timestamp
        if "data" in payload and "payload" not in payload:
            payload["payload"] = payload.pop("data")

    return payload
