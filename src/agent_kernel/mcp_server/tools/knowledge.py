"""Knowledge tools (Layer 5) - GraphStore knowledge node operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.graph import GraphEdge, GraphNode, NodeType

if TYPE_CHECKING:
    from mcp.server import FastMCP

    from agent_kernel.mcp_server.server import StoreBundle

logger = structlog.get_logger(__name__)

# Valid knowledge node types for the knowledge.add tool
_KNOWLEDGE_TYPES = {
    "domain", "system", "concept", "practice", "insight",
    "pattern", "data_object", "rule",
}


def register_knowledge_tools(mcp: FastMCP, stores: StoreBundle) -> None:
    """Register knowledge graph tools with the MCP server."""

    @mcp.tool(
        name="knowledge_query",
        description=(
            "Query the knowledge graph for relevant nodes. Search by keywords, "
            "filter by node type, and get scored results. Useful for finding "
            "concepts, systems, insights, patterns, and rules."
        ),
    )
    def knowledge_query(
        query: str,
        node_types: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Query knowledge graph nodes.

        Args:
            query: Search query (keywords matched against node properties).
            node_types: Optional filter for node types (e.g., ["concept", "insight"]).
            limit: Maximum results.
        """
        graph = stores.graph_store
        if not graph:
            return {"nodes": [], "error": "Graph store not available"}

        # Build query - search across knowledge types
        search_types = node_types or list(_KNOWLEDGE_TYPES)

        all_nodes: list[dict[str, Any]] = []
        for ntype in search_types:
            results = graph.query(node_type=ntype, limit=limit)
            nodes = results if isinstance(results, list) else results.get("nodes", [])
            all_nodes.extend(nodes)

        # Filter by keyword match in properties
        query_lower = query.lower()
        scored: list[dict[str, Any]] = []
        for node in all_nodes:
            props = node.get("properties", {})
            text = " ".join(str(v) for v in props.values()).lower()
            label = (node.get("label") or "").lower()

            score = 0.0
            if query_lower in label:
                score += 2.0
            if query_lower in text:
                score += 1.0
            for token in query_lower.split():
                if token in text:
                    score += 0.5

            if score > 0:
                scored.append({
                    "node_id": node.get("node_id") or node.get("id", ""),
                    "type": node.get("node_type", ""),
                    "label": node.get("label", ""),
                    "properties": props,
                    "score": score,
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return {"nodes": scored[:limit]}

    @mcp.tool(
        name="knowledge_add",
        description=(
            "Add a knowledge node to the graph. Use for recording concepts, "
            "systems, insights, patterns, practices, rules, or data objects "
            "that the agent should remember."
        ),
    )
    def knowledge_add(
        title: str,
        description: str,
        node_type: str = "concept",
        tags: list[str] | None = None,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        """Add a knowledge node.

        Args:
            title: Node title/name.
            description: Node description.
            node_type: Type of knowledge node (concept, insight, pattern, etc).
            tags: Optional tags for categorization.
            confidence: Confidence score (0.0-1.0).
        """
        if node_type not in _KNOWLEDGE_TYPES:
            return {
                "error": (
                    f"Invalid node_type '{node_type}'. "
                    f"Must be one of: {sorted(_KNOWLEDGE_TYPES)}"
                ),
            }

        graph = stores.graph_store
        if not graph:
            return {"error": "Graph store not available"}

        node_id = f"knowledge_{generate_ulid()}"
        now = utc_now()

        node = GraphNode(
            node_id=node_id,
            node_type=NodeType(node_type),
            properties={
                "title": title,
                "description": description,
                "tags": tags or [],
                "confidence": confidence,
                "source": "mcp",
            },
            label=title,
            created_at=now,
            updated_at=now,
        )

        graph.upsert_node(node.model_dump())
        return {"node_id": node_id}

    @mcp.tool(
        name="knowledge_relate",
        description=(
            "Create a relationship (edge) between two knowledge graph nodes. "
            "Links concepts, systems, insights, and other entities."
        ),
    )
    def knowledge_relate(
        source_id: str,
        target_id: str,
        edge_type: str = "concept_related_to",
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a graph edge between two nodes.

        Args:
            source_id: Source node ID.
            target_id: Target node ID.
            edge_type: Edge type (e.g., "concept_related_to", "insight_about").
            properties: Optional edge properties.
        """
        graph = stores.graph_store
        if not graph:
            return {"error": "Graph store not available"}

        edge_id = f"edge_{generate_ulid()}"

        edge = GraphEdge(
            edge_id=edge_id,
            edge_type=edge_type,
            source_id=source_id,
            target_id=target_id,
            properties=properties or {},
            extracted_by="mcp",
            created_at=utc_now(),
        )

        graph.upsert_edge(edge.model_dump())
        return {"edge_id": edge_id}
