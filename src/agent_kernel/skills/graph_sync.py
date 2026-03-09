"""Skill → Knowledge Graph synchronization.

Syncs skill manifests from SkillStore into the knowledge graph as
NodeType.SKILL nodes. Skill files remain the source of truth — graph
nodes are derived indexes for cross-layer discovery.

Idempotent: checks content_hash before updating.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.graph import GraphNode, NodeType

if TYPE_CHECKING:
    from agent_kernel.core.schemas.skill import SkillManifest
    from agent_kernel.memory.graph_store import GraphStore
    from agent_kernel.skills.store import SkillStore

logger = structlog.get_logger(__name__)

_EXTRACTED_BY = "skill_graph_sync"


class SkillGraphSync:
    """Syncs skill manifests to knowledge graph as NodeType.SKILL nodes."""

    def __init__(
        self,
        skill_store: SkillStore,
        graph_store: GraphStore,
    ) -> None:
        self._skill_store = skill_store
        self._graph_store = graph_store

    def sync_all(self) -> int:
        """Sync all skills to the graph. Returns count of nodes created/updated."""
        manifests = self._skill_store.list_manifests_sync()
        count = 0
        for manifest in manifests:
            node_id = self.sync_skill(manifest)
            if node_id:
                count += 1
        stale = self.remove_stale()
        logger.info(
            "skill_graph_sync_complete",
            synced=count,
            stale_removed=stale,
            total_manifests=len(manifests),
        )
        return count

    def sync_skill(self, manifest: SkillManifest) -> str | None:
        """Sync a single skill manifest to the graph.

        Returns the node_id if created/updated, None if unchanged.
        """
        from agent_kernel.core.schemas.skill import (  # noqa: PLC0415
            SkillManifest as _SkillManifest,
        )

        if not isinstance(manifest, _SkillManifest):
            return None

        node_id = f"skill_{manifest.skill_id}"
        existing = self._graph_store.get_node(node_id)

        # Check content_hash for idempotency
        if existing:
            existing_hash = existing.get("properties", {}).get("content_hash", "")
            if existing_hash == manifest.origin.content_hash:
                return None  # No change

        now = utc_now()
        properties = {
            "title": manifest.name,
            "description": manifest.description,
            "skill_id": manifest.skill_id,
            "content_hash": manifest.origin.content_hash,
            "allowed_tools": manifest.allowed_tools or [],
            "origin_kind": manifest.origin.kind,
            "origin_path": manifest.origin.path or "",
            "license": manifest.license or "",
        }
        if manifest.metadata:
            properties["metadata"] = manifest.metadata

        node = GraphNode(
            node_id=node_id,
            node_type=NodeType.SKILL,
            properties=properties,
            label=manifest.name,
            uri=manifest.origin.path,
            created_at=now if not existing else existing.get("created_at", now),
            updated_at=now,
        )

        self._graph_store.upsert_node(node.model_dump())

        logger.debug(
            "skill_node_synced",
            node_id=node_id,
            skill_id=manifest.skill_id,
            action="updated" if existing else "created",
        )

        return node_id

    def remove_stale(self) -> int:
        """Remove graph nodes for skills no longer in the store.

        Returns count of removed nodes.
        """
        # Get current skill IDs
        manifests = self._skill_store.list_manifests_sync()
        current_ids = {f"skill_{m.skill_id}" for m in manifests}

        # Query all SKILL nodes in graph
        results = self._graph_store.query(
            node_type=NodeType.SKILL.value,
            limit=1000,
        )
        nodes = results if isinstance(results, list) else results.get("nodes", [])

        removed = 0
        for node in nodes:
            nid = node.get("node_id") or node.get("id", "")
            if nid and nid not in current_ids:
                self._graph_store.delete_node(nid)
                logger.info("skill_node_removed_stale", node_id=nid)
                removed += 1

        return removed
