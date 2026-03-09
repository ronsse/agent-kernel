"""Tests for HttpTransport — fire-and-forget semantics."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent_kernel_sdk.transport import HttpTransport


@pytest.fixture
def transport():
    return HttpTransport("http://localhost:8787", timeout_s=1.0)


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Create a mock httpx.Response with sync json() method."""
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


class TestPost:
    async def test_post_success(self, transport):
        resp = _mock_response({"ok": True})
        with patch.object(
            transport._client,
            "post",
            new_callable=AsyncMock,
            return_value=resp,
        ) as mock_post:
            result = await transport.post("/test", {"key": "value"})
            assert result == {"ok": True}
            mock_post.assert_called_once_with("/test", json={"key": "value"})
        await transport.close()

    async def test_post_returns_none_on_connection_error(self, transport):
        with patch.object(
            transport._client,
            "post",
            new_callable=AsyncMock,
            side_effect=ConnectionError("refused"),
        ):
            result = await transport.post("/test", {})
            assert result is None
        await transport.close()

    async def test_post_returns_none_on_timeout(self, transport):
        with patch.object(
            transport._client,
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.TimeoutException("timeout"),
        ):
            result = await transport.post("/test", {})
            assert result is None
        await transport.close()

    async def test_post_returns_none_on_http_error(self, transport):
        resp = MagicMock()
        resp.status_code = 500
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=httpx.Request("POST", "/test"), response=resp
        )
        with patch.object(
            transport._client,
            "post",
            new_callable=AsyncMock,
            return_value=resp,
        ):
            result = await transport.post("/test", {})
            assert result is None
        await transport.close()


class TestGet:
    async def test_get_success(self, transport):
        resp = _mock_response({"data": [1, 2, 3]})
        with patch.object(
            transport._client,
            "get",
            new_callable=AsyncMock,
            return_value=resp,
        ) as mock_get:
            result = await transport.get("/items", params={"limit": 10})
            assert result == {"data": [1, 2, 3]}
            mock_get.assert_called_once_with("/items", params={"limit": 10})
        await transport.close()

    async def test_get_returns_none_on_error(self, transport):
        with patch.object(
            transport._client,
            "get",
            new_callable=AsyncMock,
            side_effect=ConnectionError("refused"),
        ):
            result = await transport.get("/items")
            assert result is None
        await transport.close()

    async def test_get_no_params(self, transport):
        resp = _mock_response({})
        with patch.object(
            transport._client,
            "get",
            new_callable=AsyncMock,
            return_value=resp,
        ) as mock_get:
            result = await transport.get("/health")
            assert result == {}
            mock_get.assert_called_once_with("/health", params=None)
        await transport.close()


class TestClose:
    async def test_close(self, transport):
        with patch.object(
            transport._client, "aclose", new_callable=AsyncMock
        ) as mock_close:
            await transport.close()
            mock_close.assert_called_once()


class TestBaseUrlNormalization:
    async def test_strips_trailing_slash(self):
        t = HttpTransport("http://localhost:8787/")
        assert t._base_url == "http://localhost:8787"
        await t.close()

    async def test_no_trailing_slash(self):
        t = HttpTransport("http://localhost:8787")
        assert t._base_url == "http://localhost:8787"
        await t.close()
