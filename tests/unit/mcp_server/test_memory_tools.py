"""Tests for memory MCP tools."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_kernel.mcp_server.server import StoreBundle
from agent_kernel.mcp_server.tools.memory import register_memory_tools


class FakeMCP:
    """Fake MCP server for testing tool registration."""

    def __init__(self):
        self._tools = {}

    def tool(self, name=None, description=None, **kwargs):
        def decorator(fn):
            self._tools[name] = fn
            return fn
        return decorator

    def get_tool(self, name):
        return self._tools[name]


def _make_stores(doc_store=None):
    stores = MagicMock(spec=StoreBundle)
    stores.document_store = doc_store
    return stores


class TestMemorySearch:
    def test_keyword_search_returns_results(self):
        doc_store = MagicMock()
        doc_store.search.return_value = [
            {
                "doc_id": "doc_1",
                "content": "Hello world",
                "rank": 1.5,
                "metadata": {"title": "test"},
            },
        ]
        stores = _make_stores(doc_store=doc_store)

        mcp = FakeMCP()
        register_memory_tools(mcp, stores)

        result = mcp.get_tool("memory_search")(query="hello", mode="keyword")

        assert result["mode"] == "keyword"
        assert len(result["results"]) == 1
        assert result["results"][0]["doc_id"] == "doc_1"
        assert result["results"][0]["source"] == "keyword"

    def test_keyword_search_with_project_filter(self):
        doc_store = MagicMock()
        doc_store.search.return_value = []
        stores = _make_stores(doc_store=doc_store)

        mcp = FakeMCP()
        register_memory_tools(mcp, stores)

        mcp.get_tool("memory_search")(
            query="test", mode="keyword", project_id="proj_1"
        )

        doc_store.search.assert_called_once_with(
            "test", limit=10, filters={"project_id": "proj_1"}
        )

    def test_semantic_search_returns_note(self):
        stores = _make_stores(doc_store=MagicMock())
        stores.vector_store = MagicMock()

        mcp = FakeMCP()
        register_memory_tools(mcp, stores)

        result = mcp.get_tool("memory_search")(query="test", mode="semantic")

        assert len(result["results"]) == 1
        assert result["results"][0]["source"] == "semantic_note"


class TestMemoryStore:
    def test_store_document(self):
        doc_store = MagicMock()
        stores = _make_stores(doc_store=doc_store)

        mcp = FakeMCP()
        register_memory_tools(mcp, stores)

        result = mcp.get_tool("memory_store")(
            doc_id="doc_1", content="Hello", metadata={"tag": "test"}
        )

        assert result["stored"] is True
        doc_store.put.assert_called_once_with(
            "doc_1", "Hello", metadata={"tag": "test"}
        )

    def test_store_without_doc_store(self):
        stores = _make_stores(doc_store=None)

        mcp = FakeMCP()
        register_memory_tools(mcp, stores)

        result = mcp.get_tool("memory_store")(doc_id="doc_1", content="Hello")

        assert result["stored"] is False
        assert "error" in result


class TestMemoryDelete:
    def test_delete_document(self):
        doc_store = MagicMock()
        doc_store.delete.return_value = True
        stores = _make_stores(doc_store=doc_store)

        mcp = FakeMCP()
        register_memory_tools(mcp, stores)

        result = mcp.get_tool("memory_delete")(doc_id="doc_1")

        assert result["deleted"] is True
        doc_store.delete.assert_called_once_with("doc_1")
