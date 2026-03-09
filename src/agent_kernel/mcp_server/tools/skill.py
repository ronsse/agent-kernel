"""Skill tools (Layer 2) - SkillStore operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from mcp.server import FastMCP

    from agent_kernel.mcp_server.server import StoreBundle

logger = structlog.get_logger(__name__)


def register_skill_tools(mcp: FastMCP, stores: StoreBundle) -> None:
    """Register skill tools with the MCP server."""

    @mcp.tool(
        name="skill_search",
        description=(
            "Search for skills by intent or keywords. Skills are portable "
            "procedural guidance (how-to documents) that help agents complete "
            "specific tasks."
        ),
    )
    def skill_search(
        query: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Search skills by intent.

        Args:
            query: Search query (intent or keywords).
            top_k: Maximum results.
        """
        skill = stores.skill_store
        if not skill:
            return {"skills": [], "error": "Skill store not available"}

        manifests = skill.search_sync(query, top_k=top_k)
        return {
            "skills": [
                {
                    "skill_id": m.skill_id,
                    "name": m.name,
                    "description": m.description,
                    "allowed_tools": m.allowed_tools,
                    "origin": m.origin.kind,
                    "content_hash": m.origin.content_hash,
                    "metadata": m.metadata,
                }
                for m in manifests
            ],
        }

    @mcp.tool(
        name="skill_load",
        description=(
            "Load the full content of a skill by ID. Returns the SKILL.md "
            "content and any referenced files (references, assets, scripts)."
        ),
    )
    def skill_load(
        skill_id: str,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        """Load a skill's content.

        Args:
            skill_id: The skill identifier.
            include: Optional list of files to include (default: SKILL.md).
        """
        skill = stores.skill_store
        if not skill:
            return {"error": "Skill store not available"}

        result = skill.load_sync(skill_id, include=include)
        if not result:
            return {"error": f"Skill '{skill_id}' not found"}

        return {
            "manifest": {
                "skill_id": result.manifest.skill_id,
                "name": result.manifest.name,
                "description": result.manifest.description,
                "allowed_tools": result.manifest.allowed_tools,
            },
            "files": result.files,
            "resources": [
                {
                    "path": r.path,
                    "kind": r.kind,
                    "hash": r.hash,
                    "bytes": r.bytes,
                }
                for r in result.resources
            ],
        }

    @mcp.tool(
        name="skill_list",
        description=(
            "List all available skill manifests. Returns name, description, "
            "and metadata for each installed skill."
        ),
    )
    def skill_list() -> dict[str, Any]:
        """List all available skills."""
        skill = stores.skill_store
        if not skill:
            return {"skills": [], "error": "Skill store not available"}

        manifests = skill.list_manifests_sync()
        return {
            "skills": [
                {
                    "skill_id": m.skill_id,
                    "name": m.name,
                    "description": m.description,
                    "allowed_tools": m.allowed_tools,
                    "origin": m.origin.kind,
                    "metadata": m.metadata,
                }
                for m in manifests
            ],
        }
