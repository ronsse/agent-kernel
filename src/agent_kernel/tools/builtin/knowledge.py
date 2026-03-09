"""Context graph knowledge capability functions.

Provides Tool Broker capabilities for agents to intentionally
query and create knowledge in the context graph.

Three capabilities:
- knowledge.search@v1: Search knowledge graph
- knowledge.add@v1: Add knowledge nodes
- knowledge.history@v1: Get entity trajectory history

Follows the graph_adapter.py pattern with global service references.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from agent_kernel.context_graph.ingestion import ContextGraphIngestion
    from agent_kernel.context_graph.query import ContextGraphQueryService
    from agent_kernel.memory.event_log import EventLog
    from agent_kernel.memory.graph_store import GraphStore

logger = structlog.get_logger(__name__)

# Global references (set during application bootstrap)
_query_service: ContextGraphQueryService | None = None
_ingestion: ContextGraphIngestion | None = None


def set_context_graph_services(
    query: ContextGraphQueryService,
    ingestion: ContextGraphIngestion,
) -> None:
    """Set global context graph service references.

    Called during application bootstrap alongside set_graph_store().
    """
    global _query_service, _ingestion
    _query_service = query
    _ingestion = ingestion


def init_knowledge_tools(
    graph_store: GraphStore,
    event_log: EventLog | None = None,
) -> None:
    """Initialize context graph services for capability functions.

    Convenience function that creates services and sets globals.

    Args:
        graph_store: The graph store to use.
        event_log: Optional event log for auditing.
    """
    from agent_kernel.context_graph.ingestion import ContextGraphIngestion
    from agent_kernel.context_graph.query import ContextGraphQueryService

    query = ContextGraphQueryService(graph_store)
    ingestion = ContextGraphIngestion(graph_store, event_log=event_log)
    set_context_graph_services(query, ingestion)


async def knowledge_search(
    intent: str,
    node_types: list[str] | None = None,
    tags: list[str] | None = None,
    include_trajectories: bool = True,
    limit: int = 20,
) -> dict[str, Any]:
    """Search knowledge graph for relevant concepts, insights, and trajectories.

    Args:
        intent: Search query / intent to match against.
        node_types: Filter by node types (e.g., ["concept", "insight"]).
        tags: Filter by tags.
        include_trajectories: Include similar past trajectories.
        limit: Maximum results to return.

    Returns:
        Dict with 'knowledge' nodes and optional 'trajectories'.
    """
    if _query_service is None:
        return {"error": "Context graph query service not initialized"}

    from agent_kernel.context_graph.query import ContextGraphQuery

    q = ContextGraphQuery(
        node_types=node_types,
        keywords=intent.lower().split(),
        tags=tags,
        limit=limit,
    )
    result = await _query_service.query(q)

    knowledge = [
        {
            "node_id": n.node_id,
            "node_type": n.node_type,
            "title": n.properties.get("title", ""),
            "description": n.properties.get("description", ""),
            "relevance_score": n.relevance_score,
            "confidence": n.confidence,
            "tags": n.properties.get("tags", []),
        }
        for n in result.nodes
    ]

    response: dict[str, Any] = {
        "knowledge": knowledge,
        "total_candidates": result.total_candidates,
        "query_time_ms": result.query_time_ms,
    }

    if include_trajectories:
        trajectories = await _query_service.find_similar_trajectories(
            intent, limit=5,
        )
        response["trajectories"] = [
            {
                "node_id": t.node_id,
                "intent": t.properties.get("intent", ""),
                "outcome_summary": t.properties.get("outcome_summary", ""),
                "outcome_status": t.properties.get("outcome_status", ""),
                "relevance_score": t.relevance_score,
            }
            for t in trajectories
        ]

    return response


async def knowledge_add(
    node_type: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    confidence: float = 1.0,
    edges: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Add a knowledge node to the context graph.

    Args:
        node_type: Type of node (domain, system, concept, insight,
            pattern, data_object, rule).
        title: Title of the knowledge node.
        description: Description of the knowledge.
        tags: Optional tags.
        confidence: Confidence score (0-1).
        edges: Optional edges to create. Each dict: {target_id, edge_type}.

    Returns:
        Dict with created node_id.
    """
    if _ingestion is None:
        return {"error": "Context graph ingestion service not initialized"}

    properties: dict[str, Any] = {
        "title": title,
        "description": description,
        "tags": tags or [],
        "confidence": confidence,
    }

    edge_specs = None
    if edges:
        edge_specs = [
            {
                "target_id": e["target_id"],
                "edge_type": e["edge_type"],
                "properties": {},
            }
            for e in edges
        ]

    node_id = await _ingestion.ingest_manual(
        node_type=node_type,
        properties=properties,
        edges=edge_specs,
    )

    return {"node_id": node_id, "node_type": node_type, "title": title}


async def knowledge_entity_history(
    entity_node_id: str,
) -> dict[str, Any]:
    """Get the trajectory history for a specific entity.

    Args:
        entity_node_id: Node ID of the entity to get history for.

    Returns:
        Dict with trajectory history.
    """
    if _query_service is None:
        return {"error": "Context graph query service not initialized"}

    trajectories = await _query_service.get_entity_history(entity_node_id)

    return {
        "entity_node_id": entity_node_id,
        "trajectory_count": len(trajectories),
        "trajectories": [
            {
                "node_id": t.node_id,
                "intent": t.properties.get("intent", ""),
                "outcome_summary": t.properties.get("outcome_summary", ""),
                "outcome_status": t.properties.get("outcome_status", ""),
                "created_at": t.properties.get("created_at", ""),
                "relevance_score": t.relevance_score,
            }
            for t in trajectories
        ],
    }
