"""Tests for plugin discovery via entry points.

Entry points are only discoverable when the package is installed
with ``pip install -e .``.  Tests gracefully handle the case where
the package is not installed with entry-point metadata.
"""

from __future__ import annotations

import importlib.metadata

import pytest

from agent_kernel.plugins import ENTRY_POINT_GROUPS, discover_plugins


def _has_entry_points(group: str) -> bool:
    """Check whether any entry points exist for *group*."""
    try:
        eps = importlib.metadata.entry_points(group=group)
        return len(list(eps)) > 0
    except TypeError:
        eps = importlib.metadata.entry_points().get(group, [])
        return len(eps) > 0


# ---- discover_plugins basic behaviour ----


def test_discover_plugins_returns_dict() -> None:
    """discover_plugins always returns a dict, even for unknown groups."""
    result = discover_plugins("nonexistent.group.that.does.not.exist")
    assert isinstance(result, dict)
    assert len(result) == 0


def test_discover_plugins_function_importable() -> None:
    """The discover_plugins function is importable from agent_kernel.plugins."""
    from agent_kernel.plugins import discover_plugins as dp

    assert callable(dp)


def test_entry_point_groups_defined() -> None:
    """ENTRY_POINT_GROUPS lists the canonical plugin groups."""
    assert "agentkernel.engines" in ENTRY_POINT_GROUPS
    assert "agentkernel.stores.vector" in ENTRY_POINT_GROUPS
    assert "agentkernel.stores.graph" in ENTRY_POINT_GROUPS
    assert "agentkernel.stores.document" in ENTRY_POINT_GROUPS
    assert "agentkernel.stores.trace" in ENTRY_POINT_GROUPS


# ---- Entry point discovery (skip if not installed with eps) ----


@pytest.mark.skipif(
    not _has_entry_points("agentkernel.engines"),
    reason="Entry points not registered (package not installed with -e)",
)
def test_engines_entry_point() -> None:
    """agentkernel.engines should include 'custom' engine."""
    result = discover_plugins("agentkernel.engines")
    assert "custom" in result


@pytest.mark.skipif(
    not _has_entry_points("agentkernel.stores.vector"),
    reason="Entry points not registered",
)
def test_vector_stores_entry_point() -> None:
    """agentkernel.stores.vector should include sqlite and/or lancedb."""
    result = discover_plugins("agentkernel.stores.vector")
    assert len(result) > 0


@pytest.mark.skipif(
    not _has_entry_points("agentkernel.stores.graph"),
    reason="Entry points not registered",
)
def test_graph_stores_entry_point() -> None:
    """agentkernel.stores.graph should include sqlite."""
    result = discover_plugins("agentkernel.stores.graph")
    assert "sqlite" in result


@pytest.mark.skipif(
    not _has_entry_points("agentkernel.stores.document"),
    reason="Entry points not registered",
)
def test_document_stores_entry_point() -> None:
    """agentkernel.stores.document should include sqlite."""
    result = discover_plugins("agentkernel.stores.document")
    assert "sqlite" in result


@pytest.mark.skipif(
    not _has_entry_points("agentkernel.stores.trace"),
    reason="Entry points not registered",
)
def test_trace_stores_entry_point() -> None:
    """agentkernel.stores.trace should include sqlite and/or jsonl."""
    result = discover_plugins("agentkernel.stores.trace")
    assert len(result) > 0
