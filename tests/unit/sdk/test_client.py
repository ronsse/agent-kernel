"""Tests for KernelClient — async client with mocked transport."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_kernel_sdk.client import KernelClient
from agent_kernel_sdk.models import (
    ActionRecord,
    TraceIngestRequest,
    TraceOutcome,
)


@pytest.fixture
def client():
    return KernelClient("http://localhost:8787", timeout_s=1.0)


class TestHealth:
    async def test_healthy(self, client):
        with patch.object(
            client._transport, "get", return_value={"status": "healthy"}
        ):
            assert await client.is_healthy() is True
        await client.close()

    async def test_unhealthy(self, client):
        with patch.object(client._transport, "get", return_value=None):
            assert await client.is_healthy() is False
        await client.close()

    async def test_unhealthy_wrong_status(self, client):
        with patch.object(
            client._transport, "get", return_value={"status": "degraded"}
        ):
            assert await client.is_healthy() is False
        await client.close()


class TestIngestTrace:
    async def test_success(self, client):
        mock_response = {
            "trace_id": "t1",
            "trajectory_node_id": "traj_1",
            "success": True,
        }
        with patch.object(
            client._transport, "post", return_value=mock_response
        ):
            req = TraceIngestRequest(
                agent_id="test",
                intent="do stuff",
                actions=[ActionRecord(capability="cap@v1")],
                outcome=TraceOutcome(status="completed"),
            )
            result = await client.ingest_trace(req)
            assert result is not None
            assert result.trace_id == "t1"
            assert result.success is True
        await client.close()

    async def test_returns_none_on_failure(self, client):
        with patch.object(client._transport, "post", return_value=None):
            req = TraceIngestRequest(
                agent_id="test",
                intent="do stuff",
                outcome=TraceOutcome(status="completed"),
            )
            result = await client.ingest_trace(req)
            assert result is None
        await client.close()


class TestIngestTraceSimple:
    async def test_convenience_method(self, client):
        mock_response = {"trace_id": "t2", "success": True}
        with patch.object(
            client._transport, "post", return_value=mock_response
        ) as mock_post:
            result = await client.ingest_trace_simple(
                agent_id="test",
                intent="sync",
                actions=[{"capability": "tasks.list@v1"}],
                outcome_status="completed",
                summary="All done",
            )
            assert result is not None
            assert result.trace_id == "t2"
            # Verify the payload structure
            call_args = mock_post.call_args
            payload = call_args[0][1]
            assert payload["agent_id"] == "test"
            assert payload["outcome"]["status"] == "completed"
        await client.close()


class TestGetContext:
    async def test_success(self, client):
        mock_response = {
            "packet_id": "pkt_01",
            "items": [
                {
                    "type": "knowledge",
                    "title": "Test",
                    "excerpt": "Some text",
                    "relevance_score": 0.9,
                    "source": "graph",
                }
            ],
            "enrichment_text": "## Relevant Knowledge\n- **Test**: Some text",
            "token_estimate": 15,
        }
        with patch.object(
            client._transport, "post", return_value=mock_response
        ):
            result = await client.get_context("plan", "agent", max_tokens=1000)
            assert result is not None
            assert result.packet_id == "pkt_01"
            assert len(result.items) == 1
            assert result.items[0].title == "Test"
        await client.close()

    async def test_returns_none_on_failure(self, client):
        with patch.object(client._transport, "post", return_value=None):
            result = await client.get_context("plan", "agent")
            assert result is None
        await client.close()


class TestGetContextText:
    async def test_returns_text(self, client):
        mock_response = {
            "packet_id": "pkt_01",
            "items": [],
            "enrichment_text": "## Knowledge\n- item",
            "token_estimate": 5,
        }
        with patch.object(
            client._transport, "post", return_value=mock_response
        ):
            text = await client.get_context_text("plan", "agent")
            assert "## Knowledge" in text
        await client.close()

    async def test_returns_empty_on_failure(self, client):
        with patch.object(client._transport, "post", return_value=None):
            text = await client.get_context_text("plan", "agent")
            assert text == ""
        await client.close()


class TestKnowledgeSearch:
    async def test_success(self, client):
        mock_response = {
            "results": [
                {
                    "node_id": "n1",
                    "node_type": "concept",
                    "title": "Test",
                    "description": "A test node",
                    "relevance_score": 0.95,
                    "freshness_score": 0.8,
                    "confidence": 1.0,
                }
            ],
            "total_candidates": 10,
            "query_time_ms": 25,
        }
        with patch.object(
            client._transport, "post", return_value=mock_response
        ):
            result = await client.knowledge_search("test query")
            assert result is not None
            assert len(result.results) == 1
            assert result.results[0].node_id == "n1"
        await client.close()

    async def test_with_filters(self, client):
        with patch.object(
            client._transport, "post", return_value=None
        ) as mock_post:
            await client.knowledge_search(
                "query",
                node_types=["concept"],
                tags=["test"],
                limit=5,
            )
            call_args = mock_post.call_args
            payload = call_args[0][1]
            assert payload["node_types"] == ["concept"]
            assert payload["tags"] == ["test"]
            assert payload["limit"] == 5
        await client.close()


class TestKnowledgeAdd:
    async def test_success(self, client):
        mock_response = {"node_id": "n_new", "success": True}
        with patch.object(
            client._transport, "post", return_value=mock_response
        ):
            result = await client.knowledge_add(
                "Test Node", "A description", tags=["test"]
            )
            assert result is not None
            assert result.node_id == "n_new"
        await client.close()

    async def test_returns_none_on_failure(self, client):
        with patch.object(client._transport, "post", return_value=None):
            result = await client.knowledge_add("Test", "Desc")
            assert result is None
        await client.close()


class TestKnowledgeHistory:
    async def test_success(self, client):
        mock_response = {
            "entity_node_id": "n1",
            "trajectories": [
                {
                    "node_id": "traj_1",
                    "intent": "sync tasks",
                    "outcome_status": "completed",
                    "relevance_score": 0.9,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
        }
        with patch.object(
            client._transport, "get", return_value=mock_response
        ):
            result = await client.knowledge_history("n1")
            assert result is not None
            assert result.entity_node_id == "n1"
            assert len(result.trajectories) == 1
        await client.close()


class TestExperience:
    async def test_get_lessons(self, client):
        mock_response = {"lessons": []}
        with patch.object(
            client._transport, "get", return_value=mock_response
        ):
            result = await client.get_lessons(workflow_id="daily")
            assert result == {"lessons": []}
        await client.close()

    async def test_get_playbooks(self, client):
        mock_response = {"playbooks": []}
        with patch.object(
            client._transport, "get", return_value=mock_response
        ):
            result = await client.get_playbooks(limit=5)
            assert result == {"playbooks": []}
        await client.close()


class TestContextManager:
    async def test_async_context_manager(self):
        async with KernelClient("http://localhost:8787") as client:
            with patch.object(
                client._transport, "get", return_value={"status": "healthy"}
            ):
                assert await client.is_healthy() is True
