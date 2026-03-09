"""TypeRegistry - tracks discovered node/edge types through agent use.

Core types are predefined in NodeType/EdgeType enums for type safety.
Any string is valid as a type at the storage layer. This registry tracks
discovered types, their usage frequency, and example properties.

This is how schema becomes output, not (only) input — agents discover
and name entity types through use.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from agent_kernel.core.schemas.base import KernelModel, utc_now

logger = structlog.get_logger(__name__)


class DiscoveredType(KernelModel):
    """A type discovered through agent use."""

    type_name: str
    category: str  # "node" or "edge"
    first_seen: datetime
    last_seen: datetime
    frequency: int = 1
    example_properties: dict[str, Any] = {}
    promoted: bool = False  # Whether promoted to core enum


class TypeStats(KernelModel):
    """Statistics about a type."""

    type_name: str
    category: str
    frequency: int
    first_seen: datetime
    last_seen: datetime
    is_core: bool  # Whether it's a predefined enum member


class TypeRegistry:
    """Tracks node and edge types discovered through agent use.

    Core types are predefined in NodeType/EdgeType enums.
    Any string is valid as a type. This registry tracks:
    - First seen / last seen timestamps
    - Frequency (how often used)
    - Example properties (what fields these nodes typically have)
    - Whether the type has been "promoted" to core
    """

    def __init__(self) -> None:
        self._discovered: dict[str, DiscoveredType] = {}
        self._core_node_types: set[str] = set()
        self._core_edge_types: set[str] = set()
        self._load_core_types()

    def _load_core_types(self) -> None:
        """Load predefined core types from enums."""
        from agent_kernel.core.schemas.graph import EdgeType, NodeType

        self._core_node_types = {t.value for t in NodeType}
        self._core_edge_types = {t.value for t in EdgeType}

    async def record_type_usage(
        self,
        type_name: str,
        category: str,
        example_properties: dict[str, Any] | None = None,
    ) -> None:
        """Record usage of a type.

        Args:
            type_name: The type string (e.g., "domain", "custom_entity").
            category: "node" or "edge".
            example_properties: Sample properties to track schema patterns.
        """
        key = f"{category}:{type_name}"
        now = utc_now()

        if key in self._discovered:
            existing = self._discovered[key]
            self._discovered[key] = DiscoveredType(
                type_name=type_name,
                category=category,
                first_seen=existing.first_seen,
                last_seen=now,
                frequency=existing.frequency + 1,
                example_properties=example_properties or existing.example_properties,
                promoted=existing.promoted,
            )
        else:
            self._discovered[key] = DiscoveredType(
                type_name=type_name,
                category=category,
                first_seen=now,
                last_seen=now,
                frequency=1,
                example_properties=example_properties or {},
            )

            # Log discovery of non-core types
            is_core = (
                type_name in self._core_node_types
                if category == "node"
                else type_name in self._core_edge_types
            )
            if not is_core:
                logger.info(
                    "type_discovered",
                    type_name=type_name,
                    category=category,
                )

    async def get_discovered_types(
        self,
        min_frequency: int = 3,
    ) -> list[DiscoveredType]:
        """Get types discovered through use above a frequency threshold.

        Args:
            min_frequency: Minimum usage count to include.

        Returns:
            List of discovered types meeting the threshold.
        """
        return [
            dt
            for dt in self._discovered.values()
            if dt.frequency >= min_frequency
        ]

    async def get_type_stats(self) -> dict[str, TypeStats]:
        """Get statistics for all known types."""
        stats: dict[str, TypeStats] = {}

        for key, dt in self._discovered.items():
            is_core = (
                dt.type_name in self._core_node_types
                if dt.category == "node"
                else dt.type_name in self._core_edge_types
            )
            stats[key] = TypeStats(
                type_name=dt.type_name,
                category=dt.category,
                frequency=dt.frequency,
                first_seen=dt.first_seen,
                last_seen=dt.last_seen,
                is_core=is_core,
            )

        return stats

    def is_core_type(self, type_name: str, category: str) -> bool:
        """Check whether a type is a predefined core enum member."""
        if category == "node":
            return type_name in self._core_node_types
        return type_name in self._core_edge_types
