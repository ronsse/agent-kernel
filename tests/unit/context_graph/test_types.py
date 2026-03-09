"""Tests for TypeRegistry - type discovery and frequency tracking."""

from __future__ import annotations

import pytest

from agent_kernel.context_graph.types import TypeRegistry
from agent_kernel.core.schemas.graph import NodeType


@pytest.fixture
def registry():
    return TypeRegistry()


@pytest.mark.asyncio
async def test_record_new_type(registry):
    """Recording a new type should create a DiscoveredType entry."""
    await registry.record_type_usage("custom_entity", "node", {"field": "value"})

    types = await registry.get_discovered_types(min_frequency=1)
    assert len(types) == 1
    assert types[0].type_name == "custom_entity"
    assert types[0].category == "node"
    assert types[0].frequency == 1
    assert types[0].example_properties == {"field": "value"}


@pytest.mark.asyncio
async def test_frequency_increments(registry):
    """Recording the same type multiple times should increment frequency."""
    await registry.record_type_usage("widget", "node")
    await registry.record_type_usage("widget", "node")
    await registry.record_type_usage("widget", "node")

    types = await registry.get_discovered_types(min_frequency=1)
    assert len(types) == 1
    assert types[0].frequency == 3


@pytest.mark.asyncio
async def test_min_frequency_filter(registry):
    """get_discovered_types should filter by min_frequency."""
    await registry.record_type_usage("rare", "node")
    await registry.record_type_usage("common", "node")
    await registry.record_type_usage("common", "node")
    await registry.record_type_usage("common", "node")

    # Only common should pass min_frequency=3
    types = await registry.get_discovered_types(min_frequency=3)
    assert len(types) == 1
    assert types[0].type_name == "common"

    # Both should pass min_frequency=1
    types = await registry.get_discovered_types(min_frequency=1)
    assert len(types) == 2


@pytest.mark.asyncio
async def test_separate_node_and_edge_tracking(registry):
    """Node and edge types with the same name should be tracked separately."""
    await registry.record_type_usage("relates_to", "node")
    await registry.record_type_usage("relates_to", "edge")

    types = await registry.get_discovered_types(min_frequency=1)
    assert len(types) == 2

    categories = {t.category for t in types}
    assert categories == {"node", "edge"}


@pytest.mark.asyncio
async def test_type_stats(registry):
    """get_type_stats should return stats for all known types."""
    await registry.record_type_usage("my_type", "node")
    await registry.record_type_usage("my_type", "node")

    stats = await registry.get_type_stats()
    assert "node:my_type" in stats
    stat = stats["node:my_type"]
    assert stat.frequency == 2
    assert stat.is_core is False


@pytest.mark.asyncio
async def test_core_type_detected(registry):
    """Core types (from NodeType enum) should be flagged as is_core."""
    await registry.record_type_usage(NodeType.DOMAIN.value, "node")

    stats = await registry.get_type_stats()
    key = f"node:{NodeType.DOMAIN.value}"
    assert key in stats
    assert stats[key].is_core is True


@pytest.mark.asyncio
async def test_is_core_type(registry):
    """is_core_type should correctly identify core vs custom types."""
    assert registry.is_core_type(NodeType.TRAJECTORY.value, "node") is True
    assert registry.is_core_type("custom_widget", "node") is False
    assert registry.is_core_type("custom_edge", "edge") is False


@pytest.mark.asyncio
async def test_example_properties_updated(registry):
    """Recording with new example_properties should update the stored examples."""
    await registry.record_type_usage("evolving", "node", {"v1": True})
    await registry.record_type_usage("evolving", "node", {"v2": True})

    types = await registry.get_discovered_types(min_frequency=1)
    assert len(types) == 1
    # Should have the latest example properties
    assert types[0].example_properties == {"v2": True}


@pytest.mark.asyncio
async def test_timestamps_preserved(registry):
    """first_seen should stay constant; last_seen should update."""
    await registry.record_type_usage("timed", "node")
    types = await registry.get_discovered_types(min_frequency=1)
    first_seen = types[0].first_seen

    await registry.record_type_usage("timed", "node")
    types = await registry.get_discovered_types(min_frequency=1)
    assert types[0].first_seen == first_seen
    assert types[0].last_seen >= first_seen


@pytest.mark.asyncio
async def test_empty_registry(registry):
    """An empty registry should return empty results."""
    types = await registry.get_discovered_types(min_frequency=1)
    assert types == []

    stats = await registry.get_type_stats()
    assert stats == {}
