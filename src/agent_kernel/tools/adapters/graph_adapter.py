"""Graph adapter - provides graph query and traversal tools.

These functions are registered as capabilities for agents to call
for ad-hoc graph queries beyond automatic context assembly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from agent_kernel.memory.graph_store import GraphStore

logger = structlog.get_logger(__name__)

# Global graph store reference (set during initialization)
_graph_store: GraphStore | None = None


def set_graph_store(store: GraphStore) -> None:
    """Set the global graph store for adapter functions.

    Args:
        store: The graph store instance to use.
    """
    global _graph_store
    _graph_store = store
    logger.info("graph_adapter_store_set")


def graph_query(
    node_type: str | None = None,
    properties: dict[str, Any] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Query graph nodes by type and/or properties.

    Args:
        node_type: Filter by node type (e.g., "person", "project").
        properties: Filter by property values.
        limit: Maximum results to return.

    Returns:
        Dict with nodes list.
    """
    if _graph_store is None:
        return {"error": "Graph store not initialized", "nodes": []}

    try:
        nodes = _graph_store.query(
            node_type=node_type,
            properties=properties,
            limit=limit,
        )
        logger.debug(
            "graph_query_executed",
            node_type=node_type,
            results=len(nodes),
        )
        return {"nodes": nodes, "count": len(nodes)}
    except Exception as e:
        logger.error("graph_query_failed", error=str(e))
        return {"error": str(e), "nodes": []}


def graph_neighbors(
    seed_ids: list[str],
    depth: int = 2,
    edge_types: list[str] | None = None,
) -> dict[str, Any]:
    """Get subgraph around seed nodes.

    Args:
        seed_ids: Starting node IDs for traversal.
        depth: Number of hops to traverse (1-4).
        edge_types: Optional filter for edge types.

    Returns:
        Dict with nodes and edges lists.
    """
    if _graph_store is None:
        return {"error": "Graph store not initialized", "nodes": [], "edges": []}

    # Clamp depth to safe range
    depth = max(1, min(4, depth))

    try:
        subgraph = _graph_store.get_subgraph(
            seed_ids=seed_ids,
            depth=depth,
            edge_types=edge_types,
        )
        logger.debug(
            "graph_neighbors_executed",
            seed_count=len(seed_ids),
            depth=depth,
            nodes_found=len(subgraph.get("nodes", [])),
            edges_found=len(subgraph.get("edges", [])),
        )
        return subgraph
    except Exception as e:
        logger.error("graph_neighbors_failed", error=str(e))
        return {"error": str(e), "nodes": [], "edges": []}


def graph_get_node(node_id: str) -> dict[str, Any]:
    """Get a single node by ID.

    Args:
        node_id: The node ID to retrieve.

    Returns:
        The node data or error.
    """
    if _graph_store is None:
        return {"error": "Graph store not initialized"}

    try:
        node = _graph_store.get_node(node_id)
        if node is None:
            return {"error": f"Node not found: {node_id}"}
        return {"node": node}
    except Exception as e:
        logger.error("graph_get_node_failed", error=str(e))
        return {"error": str(e)}
