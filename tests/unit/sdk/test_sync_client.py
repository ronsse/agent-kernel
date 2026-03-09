"""Tests for SyncKernelClient — blocking wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from agent_kernel_sdk.models import TraceIngestRequest, TraceOutcome
from agent_kernel_sdk.sync_client import SyncKernelClient


class TestSyncClient:
    def test_is_healthy(self):
        client = SyncKernelClient("http://localhost:8787", timeout_s=1.0)
        with patch.object(
            client._client._transport,
            "get",
            new_callable=AsyncMock,
            return_value={"status": "healthy"},
        ):
            assert client.is_healthy() is True
        client.close()

    def test_is_healthy_failure(self):
        client = SyncKernelClient("http://localhost:8787", timeout_s=1.0)
        with patch.object(
            client._client._transport,
            "get",
            new_callable=AsyncMock,
            return_value=None,
        ):
            assert client.is_healthy() is False
        client.close()

    def test_ingest_trace(self):
        client = SyncKernelClient("http://localhost:8787", timeout_s=1.0)
        mock_response = {"trace_id": "t1", "success": True}
        with patch.object(
            client._client._transport,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            req = TraceIngestRequest(
                agent_id="test",
                intent="test",
                outcome=TraceOutcome(status="completed"),
            )
            result = client.ingest_trace(req)
            assert result is not None
            assert result.trace_id == "t1"
        client.close()

    def test_get_context_text(self):
        client = SyncKernelClient("http://localhost:8787", timeout_s=1.0)
        mock_response = {
            "packet_id": "pkt_01",
            "items": [],
            "enrichment_text": "## Knowledge",
            "token_estimate": 5,
        }
        with patch.object(
            client._client._transport,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            text = client.get_context_text("plan", "agent")
            assert "## Knowledge" in text
        client.close()

    def test_get_context_text_failure(self):
        client = SyncKernelClient("http://localhost:8787", timeout_s=1.0)
        with patch.object(
            client._client._transport,
            "post",
            new_callable=AsyncMock,
            return_value=None,
        ):
            text = client.get_context_text("plan", "agent")
            assert text == ""
        client.close()

    def test_knowledge_search(self):
        client = SyncKernelClient("http://localhost:8787", timeout_s=1.0)
        mock_response = {
            "results": [
                {
                    "node_id": "n1",
                    "node_type": "concept",
                    "title": "Test",
                    "description": "desc",
                    "relevance_score": 0.9,
                    "freshness_score": 0.5,
                    "confidence": 1.0,
                }
            ],
            "total_candidates": 1,
            "query_time_ms": 10,
        }
        with patch.object(
            client._client._transport,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = client.knowledge_search("test")
            assert result is not None
            assert len(result.results) == 1
        client.close()

    def test_knowledge_add(self):
        client = SyncKernelClient("http://localhost:8787", timeout_s=1.0)
        mock_response = {"node_id": "n_new", "success": True}
        with patch.object(
            client._client._transport,
            "post",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = client.knowledge_add("Title", "Description")
            assert result is not None
            assert result.success
        client.close()

    def test_knowledge_history(self):
        client = SyncKernelClient("http://localhost:8787", timeout_s=1.0)
        mock_response = {"entity_node_id": "n1", "trajectories": []}
        with patch.object(
            client._client._transport,
            "get",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            result = client.knowledge_history("n1")
            assert result is not None
            assert result.entity_node_id == "n1"
        client.close()

    def test_context_manager(self):
        with (
            SyncKernelClient("http://localhost:8787", timeout_s=1.0) as client,
            patch.object(
                client._client._transport,
                "get",
                new_callable=AsyncMock,
                return_value={"status": "healthy"},
            ),
        ):
            assert client.is_healthy() is True
