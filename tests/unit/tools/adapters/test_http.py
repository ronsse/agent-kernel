"""Tests for HTTP Tool Adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_kernel.tools.adapters.http import (
    HTTPEndpoint,
    HTTPMethod,
    HTTPToolAdapter,
)


class TestHTTPEndpoint:
    """Tests for HTTPEndpoint dataclass."""

    def test_default_values(self):
        """Test default endpoint values."""
        endpoint = HTTPEndpoint(url="https://api.example.com/test")

        assert endpoint.url == "https://api.example.com/test"
        assert endpoint.method == HTTPMethod.POST
        assert endpoint.headers == {}
        assert endpoint.auth_header is None
        assert 429 in endpoint.retryable_status_codes

    def test_custom_values(self):
        """Test custom endpoint values."""
        endpoint = HTTPEndpoint(
            url="https://api.example.com/data",
            method=HTTPMethod.GET,
            headers={"X-Custom": "value"},
            auth_header="Bearer {token}",
            auth_env_var="API_TOKEN",
            response_path="data.results",
            timeout_override_ms=5000,
        )

        assert endpoint.method == HTTPMethod.GET
        assert endpoint.headers["X-Custom"] == "value"
        assert endpoint.response_path == "data.results"


class TestHTTPToolAdapter:
    """Tests for HTTPToolAdapter."""

    def test_init(self):
        """Test adapter initialization."""
        adapter = HTTPToolAdapter(
            default_headers={"User-Agent": "TestAgent"},
            default_timeout_ms=10000,
        )

        assert adapter._default_headers == {"User-Agent": "TestAgent"}
        assert adapter._default_timeout_ms == 10000

    def test_register_endpoint(self):
        """Test registering an endpoint."""
        adapter = HTTPToolAdapter()
        endpoint = HTTPEndpoint(url="https://api.example.com/test")

        adapter.register("test.action@v1", endpoint)

        assert adapter.has_endpoint("test.action@v1")
        assert not adapter.has_endpoint("other@v1")

    def test_register_simple(self):
        """Test simple endpoint registration."""
        adapter = HTTPToolAdapter()

        adapter.register_simple(
            "weather.get@v1",
            url="https://api.weather.com/current",
            method=HTTPMethod.GET,
        )

        assert adapter.has_endpoint("weather.get@v1")
        endpoint = adapter._endpoints["weather.get@v1"]
        assert endpoint.method == HTTPMethod.GET

    def test_unregister(self):
        """Test unregistering an endpoint."""
        adapter = HTTPToolAdapter()
        adapter.register_simple("test@v1", url="https://example.com")

        assert adapter.has_endpoint("test@v1")

        adapter.unregister("test@v1")

        assert not adapter.has_endpoint("test@v1")

    def test_supports(self):
        """Test supports method."""
        adapter = HTTPToolAdapter()

        assert adapter.supports("http") is True
        assert adapter.supports("local") is False
        assert adapter.supports("subprocess") is False

    def test_extract_response_dict(self):
        """Test response extraction with dict."""
        adapter = HTTPToolAdapter()

        data = {"name": "test", "value": 42}
        result = adapter._extract_response(data, None)

        assert result == {"name": "test", "value": 42}

    def test_extract_response_with_path(self):
        """Test response extraction with path."""
        adapter = HTTPToolAdapter()

        data = {
            "status": "ok",
            "data": {
                "results": [{"id": 1}, {"id": 2}],
            },
        }

        result = adapter._extract_response(data, "data.results")
        assert result == {"result": [{"id": 1}, {"id": 2}]}

        result = adapter._extract_response(data, "data")
        assert result == {"results": [{"id": 1}, {"id": 2}]}

    def test_extract_response_path_not_found(self):
        """Test response extraction with invalid path."""
        adapter = HTTPToolAdapter()

        data = {"foo": "bar"}
        result = adapter._extract_response(data, "missing.path")

        assert "path_not_found" in result

    def test_build_headers_with_auth(self):
        """Test header building with auth."""
        adapter = HTTPToolAdapter(default_headers={"Accept": "application/json"})

        endpoint = HTTPEndpoint(
            url="https://example.com",
            headers={"X-Custom": "value"},
            auth_header="Bearer {token}",
            auth_env_var="TEST_TOKEN",
        )

        with patch.dict("os.environ", {"TEST_TOKEN": "secret123"}):
            headers = adapter._build_headers(endpoint)

        assert headers["Accept"] == "application/json"
        assert headers["X-Custom"] == "value"
        assert headers["Authorization"] == "Bearer secret123"

    @pytest.mark.asyncio
    async def test_execute_not_registered(self):
        """Test executing unregistered capability."""
        adapter = HTTPToolAdapter()

        result = await adapter.execute("unknown@v1", {}, 5000)

        assert result.success is False
        assert result.error_code == "ENDPOINT_NOT_REGISTERED"

    @pytest.mark.asyncio
    async def test_execute_success_post(self):
        """Test successful POST request."""
        adapter = HTTPToolAdapter()
        adapter.register_simple("test@v1", url="https://api.example.com/data")

        # Mock the HTTP client
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "success", "id": 123}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        adapter._client = mock_client

        result = await adapter.execute(
            "test@v1",
            {"name": "test", "value": 42},
            5000,
        )

        assert result.success is True
        assert result.output["result"] == "success"
        assert result.output["id"] == 123

        # Verify request was made with JSON body
        mock_client.request.assert_called_once()
        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["method"] == "POST"
        assert call_kwargs["json"] == {"name": "test", "value": 42}

    @pytest.mark.asyncio
    async def test_execute_success_get(self):
        """Test successful GET request."""
        adapter = HTTPToolAdapter()
        adapter.register_simple(
            "search@v1",
            url="https://api.example.com/search",
            method=HTTPMethod.GET,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        adapter._client = mock_client

        result = await adapter.execute(
            "search@v1",
            {"query": "test", "limit": 10},
            5000,
        )

        assert result.success is True

        # Verify request was made with query params
        call_kwargs = mock_client.request.call_args.kwargs
        assert call_kwargs["method"] == "GET"
        assert call_kwargs["params"] == {"query": "test", "limit": 10}

    @pytest.mark.asyncio
    async def test_execute_http_error(self):
        """Test HTTP error response."""
        adapter = HTTPToolAdapter()
        adapter.register_simple("test@v1", url="https://api.example.com/data")

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "Bad request"}
        mock_response.text = "Bad request"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        adapter._client = mock_client

        result = await adapter.execute("test@v1", {}, 5000)

        assert result.success is False
        assert result.error_code == "HTTP_400"
        assert "Bad request" in result.error

    @pytest.mark.asyncio
    async def test_execute_retryable_error(self):
        """Test retryable HTTP error."""
        adapter = HTTPToolAdapter()
        adapter.register_simple("test@v1", url="https://api.example.com/data")

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.json.return_value = {"error": "Service unavailable"}
        mock_response.text = "Service unavailable"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        adapter._client = mock_client

        result = await adapter.execute("test@v1", {}, 5000)

        assert result.success is False
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        """Test request timeout."""
        import asyncio

        adapter = HTTPToolAdapter()
        adapter.register_simple("slow@v1", url="https://api.example.com/slow")

        async def slow_request(*args, **kwargs):
            await asyncio.sleep(10)
            return MagicMock()

        mock_client = AsyncMock()
        mock_client.request = slow_request
        adapter._client = mock_client

        result = await adapter.execute("slow@v1", {}, 100)  # 100ms timeout

        assert result.success is False
        assert result.error_code == "TIMEOUT"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_execute_with_response_path(self):
        """Test response extraction with path."""
        adapter = HTTPToolAdapter()
        endpoint = HTTPEndpoint(
            url="https://api.example.com/data",
            response_path="data.items",
        )
        adapter.register("test@v1", endpoint)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "data": {
                "items": [{"id": 1}, {"id": 2}],
            },
        }

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        adapter._client = mock_client

        result = await adapter.execute("test@v1", {}, 5000)

        assert result.success is True
        assert result.output["result"] == [{"id": 1}, {"id": 2}]

    @pytest.mark.asyncio
    async def test_close(self):
        """Test closing the adapter."""
        adapter = HTTPToolAdapter()

        # Create a client
        _ = adapter._get_client()
        assert adapter._client is not None

        await adapter.close()
        assert adapter._client is None
