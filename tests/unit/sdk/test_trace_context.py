"""Tests for TraceContext — auto-flush context manager."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent_kernel_sdk.client import KernelClient
from agent_kernel_sdk.trace_context import TraceContext


@pytest.fixture
def client():
    return KernelClient("http://localhost:8787", timeout_s=1.0)


async def _run_trace_with_error(client: KernelClient) -> None:
    """Helper to raise inside a trace context."""
    async with client.trace("my_agent", "do stuff") as t:
        t.record("cap@v1", status="success")
        msg = "something broke"
        raise ValueError(msg)


class TestTraceContext:
    async def test_normal_exit_flushes_completed(self, client):
        with patch.object(
            client, "ingest_trace", new_callable=AsyncMock
        ) as mock_ingest:
            mock_ingest.return_value = None

            async with client.trace("my_agent", "do stuff") as t:
                t.record("cap@v1", output={"ok": True}, status="success")
                t.record("cap2@v1", status="success", duration_ms=100)

            mock_ingest.assert_called_once()
            req = mock_ingest.call_args[0][0]
            assert req.agent_id == "my_agent"
            assert req.intent == "do stuff"
            assert len(req.actions) == 2
            assert req.outcome.status == "completed"

        await client.close()

    async def test_exception_flushes_failed(self, client):
        with patch.object(
            client, "ingest_trace", new_callable=AsyncMock
        ) as mock_ingest:
            mock_ingest.return_value = None

            with pytest.raises(ValueError, match="something broke"):
                await _run_trace_with_error(client)

            mock_ingest.assert_called_once()
            req = mock_ingest.call_args[0][0]
            assert req.outcome.status == "failed"
            assert "something broke" in (req.outcome.summary or "")

        await client.close()

    async def test_no_actions(self, client):
        with patch.object(
            client, "ingest_trace", new_callable=AsyncMock
        ) as mock_ingest:
            mock_ingest.return_value = None

            async with client.trace("agent", "empty run"):
                pass

            req = mock_ingest.call_args[0][0]
            assert req.actions == []
            assert req.outcome.status == "completed"

        await client.close()

    async def test_record_with_input(self, client):
        with patch.object(
            client, "ingest_trace", new_callable=AsyncMock
        ) as mock_ingest:
            mock_ingest.return_value = None

            async with client.trace("agent", "test") as t:
                t.record(
                    "tasks.create@v1",
                    input={"title": "new task"},
                    output={"task_id": "123"},
                    status="success",
                    duration_ms=50,
                )

            req = mock_ingest.call_args[0][0]
            action = req.actions[0]
            assert action.capability == "tasks.create@v1"
            assert action.input == {"title": "new task"}
            assert action.output == {"task_id": "123"}
            assert action.duration_ms == 50

        await client.close()

    async def test_standalone_trace_context(self, client):
        """TraceContext can be used directly without client.trace()."""
        with patch.object(
            client, "ingest_trace", new_callable=AsyncMock
        ) as mock_ingest:
            mock_ingest.return_value = None

            ctx = TraceContext(client, "agent", "direct")
            async with ctx as t:
                t.record("cap@v1")

            mock_ingest.assert_called_once()

        await client.close()
