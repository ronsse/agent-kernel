"""Context tools (cross-layer) - ContextAssembler + ContextGraphQuery."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.core.schemas.context import ContextPolicy

if TYPE_CHECKING:
    from mcp.server import FastMCP

    from agent_kernel.mcp_server.server import StoreBundle

logger = structlog.get_logger(__name__)


def register_context_tools(mcp: FastMCP, stores: StoreBundle) -> None:
    """Register context assembly tools with the MCP server."""

    @mcp.tool(
        name="context_assemble",
        description=(
            "Assemble a full context packet for an intent. Searches across "
            "all memory layers (documents, skills, knowledge graph, experience) "
            "and returns a ranked, deduplicated set of context items with "
            "a retrieval report."
        ),
    )
    def context_assemble(
        intent: str,
        project_id: str | None = None,
        max_tokens: int = 4000,
        max_items: int = 30,
    ) -> dict[str, Any]:
        """Assemble context for an intent.

        Args:
            intent: The user's intent or query.
            project_id: Optional project scope.
            max_tokens: Maximum context tokens.
            max_items: Maximum context items.
        """
        assembler = stores.context_assembler
        if not assembler:
            return {"error": "Context assembler not available"}

        policy = ContextPolicy(
            max_tokens=max_tokens,
            max_notes=min(max_items, 20),
            max_tasks=min(max_items, 20),
            max_events=5,
        )

        packet = assembler.assemble(
            intent=intent,
            policy=policy,
            project_id=project_id,
        )

        return {
            "packet_id": packet.packet_id,
            "intent": packet.intent,
            "items": [
                {
                    "ref_type": item.ref.ref_type.value,
                    "ref_id": item.ref.ref_id,
                    "excerpt": item.excerpt[:300],
                    "summary": item.summary,
                    "score": item.relevance_score,
                    "reason": item.included_reason,
                }
                for item in packet.items
            ],
            "retrieval_report": {
                "queries_run": len(packet.retrieval_report.queries_run),
                "items_considered": packet.retrieval_report.items_considered,
                "items_selected": packet.retrieval_report.items_selected,
                "strategy": packet.retrieval_report.selection_strategy,
            },
        }

    @mcp.tool(
        name="context_graph",
        description=(
            "Query the context graph directly. Returns knowledge and episodic "
            "memory nodes with relevance scoring. More targeted than full "
            "context assembly."
        ),
    )
    def context_graph(
        query: str,
        node_types: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Query the context graph.

        Args:
            query: Search query.
            node_types: Optional filter for node types.
            limit: Maximum results.
        """
        graph = stores.graph_store
        if not graph:
            return {"nodes": [], "error": "Graph store not available"}

        # Search directly in graph store
        all_nodes: list[dict[str, Any]] = []
        search_types = node_types or [
            "domain", "system", "concept", "practice",
            "insight", "pattern", "rule", "trajectory",
        ]

        for ntype in search_types:
            results = graph.query(node_type=ntype, limit=limit)
            nodes = results if isinstance(results, list) else results.get("nodes", [])
            all_nodes.extend(nodes)

        # Score by keyword relevance
        query_lower = query.lower()
        scored: list[dict[str, Any]] = []
        for node in all_nodes:
            props = node.get("properties", {})
            text = " ".join(str(v) for v in props.values()).lower()
            label = (node.get("label") or "").lower()

            score = 0.0
            if query_lower in label:
                score += 2.0
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
