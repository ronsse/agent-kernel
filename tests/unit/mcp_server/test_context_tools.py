"""Tests for context MCP tools."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_kernel.core.schemas.context import (
    ContextItem,
    ContextPacket,
    ContextRef,
    RefType,
    RetrievalReport,
)
from agent_kernel.mcp_server.server import StoreBundle
from agent_kernel.mcp_server.tools.context import register_context_tools


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


def _make_stores(graph_store=None, assembler=None):
    stores = MagicMock(spec=StoreBundle)
    stores.graph_store = graph_store
    stores.context_assembler = assembler
    return stores


def _make_packet():
    return ContextPacket(
        packet_id="pkt_001",
        intent="test intent",
        items=[
            ContextItem(
                ref=ContextRef(ref_type=RefType.DOCUMENT, ref_id="doc_1"),
                excerpt="Test document content",
                summary="Test doc",
                relevance_score=0.9,
                included_reason="keyword_search",
            ),
        ],
        retrieval_report=RetrievalReport(
            items_considered=10,
            items_selected=1,
            selection_strategy="relevance_ranked",
        ),
    )


class TestContextAssemble:
    def test_assemble_returns_packet(self):
        assembler = MagicMock()
        assembler.assemble.return_value = _make_packet()
        stores = _make_stores(assembler=assembler)

        mcp = FakeMCP()
        register_context_tools(mcp, stores)

        result = mcp.get_tool("context_assemble")(intent="test intent")

        assert result["packet_id"] == "pkt_001"
        assert len(result["items"]) == 1
        assert result["items"][0]["ref_type"] == "doc"
        assert result["items"][0]["ref_id"] == "doc_1"
        assert result["retrieval_report"]["items_selected"] == 1

    def test_assemble_passes_project_id(self):
        assembler = MagicMock()
        assembler.assemble.return_value = _make_packet()
        stores = _make_stores(assembler=assembler)

        mcp = FakeMCP()
        register_context_tools(mcp, stores)

        mcp.get_tool("context_assemble")(intent="test", project_id="proj_1")

        call_kwargs = assembler.assemble.call_args[1]
        assert call_kwargs["project_id"] == "proj_1"

    def test_assemble_no_assembler(self):
        stores = _make_stores(assembler=None)

        mcp = FakeMCP()
        register_context_tools(mcp, stores)

        result = mcp.get_tool("context_assemble")(intent="test")

        assert "error" in result


class TestContextGraph:
    def test_graph_query_returns_nodes(self):
        graph = MagicMock()
        test_node = {
            "node_id": "node_1",
            "node_type": "concept",
            "label": "Agent Design",
            "properties": {
                "title": "Agent Design",
                "description": "How to design agents",
            },
        }

        def _query(node_type, **_kw):
            return [test_node] if node_type == "concept" else []
        graph.query.side_effect = _query
        stores = _make_stores(graph_store=graph)

        mcp = FakeMCP()
        register_context_tools(mcp, stores)

        result = mcp.get_tool("context_graph")(query="agent")

        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["node_id"] == "node_1"

    def test_graph_query_no_store(self):
        stores = _make_stores(graph_store=None)

        mcp = FakeMCP()
        register_context_tools(mcp, stores)

        result = mcp.get_tool("context_graph")(query="test")

        assert result["nodes"] == []
        assert "error" in result

    def test_graph_query_filters_by_keyword(self):
        graph = MagicMock()
        test_nodes = [
            {
                "node_id": "n1",
                "node_type": "concept",
                "label": "Memory",
                "properties": {"title": "Memory"},
            },
            {
                "node_id": "n2",
                "node_type": "concept",
                "label": "Unrelated",
                "properties": {"title": "Unrelated"},
            },
        ]

        def _query(node_type, **_kw):
            if node_type == "concept":
                return test_nodes
            return []
        graph.query.side_effect = _query
        stores = _make_stores(graph_store=graph)

        mcp = FakeMCP()
        register_context_tools(mcp, stores)

        result = mcp.get_tool("context_graph")(query="memory")

        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["node_id"] == "n1"
