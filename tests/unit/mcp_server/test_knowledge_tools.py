"""Tests for knowledge MCP tools."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_kernel.mcp_server.server import StoreBundle
from agent_kernel.mcp_server.tools.knowledge import register_knowledge_tools


class FakeMCP:
    def __init__(self):
        self._tools = {}

    def tool(self, name=None, description=None, **kwargs):
        def decorator(fn):
            self._tools[name] = fn
            return fn
        return decorator

    def get_tool(self, name):
        return self._tools[name]


def _make_stores(graph_store=None):
    stores = MagicMock(spec=StoreBundle)
    stores.graph_store = graph_store
    return stores


class TestKnowledgeQuery:
    def test_query_returns_scored_nodes(self):
        graph = MagicMock()
        test_node = {
            "node_id": "node_1",
            "node_type": "concept",
            "label": "Agent Architecture",
            "properties": {
                "title": "Agent Architecture",
                "description": "How agents work",
            },
        }
        # Return the node only for "concept" type, empty for others
        def _query(node_type, **_kw):
            return [test_node] if node_type == "concept" else []
        graph.query.side_effect = _query
        stores = _make_stores(graph_store=graph)

        mcp = FakeMCP()
        register_knowledge_tools(mcp, stores)

        result = mcp.get_tool("knowledge_query")(query="agent")

        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["node_id"] == "node_1"
        assert result["nodes"][0]["score"] > 0

    def test_query_filters_by_relevance(self):
        graph = MagicMock()
        test_node = {
            "node_id": "node_1",
            "node_type": "concept",
            "label": "Unrelated",
            "properties": {"title": "Unrelated"},
        }

        def _query(node_type, **_kw):
            return [test_node] if node_type == "concept" else []
        graph.query.side_effect = _query
        stores = _make_stores(graph_store=graph)

        mcp = FakeMCP()
        register_knowledge_tools(mcp, stores)

        result = mcp.get_tool("knowledge_query")(query="agent")

        assert len(result["nodes"]) == 0  # No match

    def test_query_no_graph_store(self):
        stores = _make_stores(graph_store=None)

        mcp = FakeMCP()
        register_knowledge_tools(mcp, stores)

        result = mcp.get_tool("knowledge_query")(query="test")

        assert result["nodes"] == []
        assert "error" in result


class TestKnowledgeAdd:
    def test_add_creates_node(self):
        graph = MagicMock()
        stores = _make_stores(graph_store=graph)

        mcp = FakeMCP()
        register_knowledge_tools(mcp, stores)

        result = mcp.get_tool("knowledge_add")(
            title="Test Concept",
            description="A test concept",
            node_type="concept",
        )

        assert "node_id" in result
        assert result["node_id"].startswith("knowledge_")
        graph.upsert_node.assert_called_once()

    def test_add_rejects_invalid_type(self):
        graph = MagicMock()
        stores = _make_stores(graph_store=graph)

        mcp = FakeMCP()
        register_knowledge_tools(mcp, stores)

        result = mcp.get_tool("knowledge_add")(
            title="Test",
            description="desc",
            node_type="invalid_type",
        )

        assert "error" in result
        graph.upsert_node.assert_not_called()


class TestKnowledgeRelate:
    def test_relate_creates_edge(self):
        graph = MagicMock()
        stores = _make_stores(graph_store=graph)

        mcp = FakeMCP()
        register_knowledge_tools(mcp, stores)

        result = mcp.get_tool("knowledge_relate")(
            source_id="node_1",
            target_id="node_2",
            edge_type="concept_related_to",
        )

        assert "edge_id" in result
        assert result["edge_id"].startswith("edge_")
        graph.upsert_edge.assert_called_once()
