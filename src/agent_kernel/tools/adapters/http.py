"""HTTP Tool Adapter - execute tools via REST API calls."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx
import structlog

from agent_kernel.tools.adapters.base import ToolAdapter, ToolResult

logger = structlog.get_logger(__name__)


class HTTPMethod(str, Enum):
    """HTTP methods."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


@dataclass
class HTTPEndpoint:
    """Configuration for an HTTP endpoint."""

    url: str
    method: HTTPMethod = HTTPMethod.POST
    headers: dict[str, str] = field(default_factory=dict)
    auth_header: str | None = None  # e.g., "Bearer {token}"
    auth_env_var: str | None = None  # Env var name for token
    response_path: str | None = None  # JSONPath to extract result
    timeout_override_ms: int | None = None
    retryable_status_codes: list[int] = field(
        default_factory=lambda: [429, 500, 502, 503, 504]
    )


class HTTPToolAdapter(ToolAdapter):
    """Adapter that executes tools via HTTP REST API calls.

    Endpoints are registered by capability name and called
    with the provided arguments as the request body (for POST/PUT/PATCH)
    or query parameters (for GET/DELETE).
    """

    def __init__(
        self,
        default_headers: dict[str, str] | None = None,
        default_timeout_ms: int = 30000,
    ) -> None:
        """Initialize the HTTP adapter.

        Args:
            default_headers: Headers to include in all requests.
            default_timeout_ms: Default timeout for requests.
        """
        self._endpoints: dict[str, HTTPEndpoint] = {}
        self._default_headers = default_headers or {}
        self._default_timeout_ms = default_timeout_ms
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self._default_headers,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def register(
        self,
        capability_name: str,
        endpoint: HTTPEndpoint,
    ) -> None:
        """Register an HTTP endpoint for a capability.

        Args:
            capability_name: The capability name (e.g., "weather.get@v1").
            endpoint: The endpoint configuration.
        """
        self._endpoints[capability_name] = endpoint
        logger.info(
            "http_endpoint_registered",
            capability_name=capability_name,
            url=endpoint.url,
            method=endpoint.method.value,
        )

    def register_simple(
        self,
        capability_name: str,
        url: str,
        method: HTTPMethod = HTTPMethod.POST,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Register a simple HTTP endpoint.

        Args:
            capability_name: The capability name.
            url: The endpoint URL.
            method: HTTP method (default POST).
            headers: Optional additional headers.
        """
        endpoint = HTTPEndpoint(
            url=url,
            method=method,
            headers=headers or {},
        )
        self.register(capability_name, endpoint)

    def unregister(self, capability_name: str) -> None:
        """Unregister an endpoint.

        Args:
            capability_name: The capability name to unregister.
        """
        if capability_name in self._endpoints:
            del self._endpoints[capability_name]
            logger.debug("http_endpoint_unregistered", capability_name=capability_name)

    def has_endpoint(self, capability_name: str) -> bool:
        """Check if an endpoint is registered for a capability.

        Args:
            capability_name: The capability name.

        Returns:
            True if an endpoint is registered.
        """
        return capability_name in self._endpoints

    def supports(self, adapter_type: str) -> bool:
        """Check if this adapter supports the given type."""
        return adapter_type == "http"

    def _build_headers(self, endpoint: HTTPEndpoint) -> dict[str, str]:
        """Build request headers including auth."""
        import os

        headers = {**self._default_headers, **endpoint.headers}

        # Add auth header if configured
        if endpoint.auth_header:
            token = ""
            if endpoint.auth_env_var:
                token = os.environ.get(endpoint.auth_env_var, "")
            headers["Authorization"] = endpoint.auth_header.format(token=token)

        return headers

    def _extract_response(
        self,
        data: Any,
        response_path: str | None,
    ) -> dict[str, Any]:
        """Extract result from response using optional path.

        Args:
            data: The full response data.
            response_path: Optional dot-notation path (e.g., "data.results").

        Returns:
            Extracted data as dict.
        """
        if response_path is None:
            if isinstance(data, dict):
                return data
            return {"result": data}

        # Navigate the path
        current = data
        for key in response_path.split("."):
            if isinstance(current, dict):
                current = current.get(key)
            elif isinstance(current, list) and key.isdigit():
                idx = int(key)
                current = current[idx] if idx < len(current) else None
            else:
                current = None

            if current is None:
                return {"result": None, "path_not_found": response_path}

        if isinstance(current, dict):
            return current
        return {"result": current}

    async def execute(
        self,
        capability_name: str,
        args: dict[str, Any],
        timeout_ms: int,
    ) -> ToolResult:
        """Execute an HTTP request.

        Args:
            capability_name: The capability to execute.
            args: The input arguments (body or query params).
            timeout_ms: Maximum execution time in milliseconds.

        Returns:
            ToolResult with response data or error.
        """
        endpoint = self._endpoints.get(capability_name)
        if endpoint is None:
            return ToolResult(
                success=False,
                output={},
                error=f"No endpoint registered for {capability_name}",
                error_code="ENDPOINT_NOT_REGISTERED",
            )

        # Use endpoint timeout override if specified
        effective_timeout = endpoint.timeout_override_ms or timeout_ms
        timeout_seconds = effective_timeout / 1000.0

        client = self._get_client()
        headers = self._build_headers(endpoint)

        try:
            # Build request based on method
            if endpoint.method in (HTTPMethod.GET, HTTPMethod.DELETE):
                # Use args as query parameters
                response = await asyncio.wait_for(
                    client.request(
                        method=endpoint.method.value,
                        url=endpoint.url,
                        params=args,
                        headers=headers,
                    ),
                    timeout=timeout_seconds,
                )
            else:
                # Use args as JSON body
                response = await asyncio.wait_for(
                    client.request(
                        method=endpoint.method.value,
                        url=endpoint.url,
                        json=args,
                        headers=headers,
                    ),
                    timeout=timeout_seconds,
                )

            logger.debug(
                "http_request_completed",
                capability_name=capability_name,
                status_code=response.status_code,
                url=endpoint.url,
            )

            # Check for HTTP errors
            if response.status_code >= 400:
                is_retryable = response.status_code in endpoint.retryable_status_codes

                # Try to get error message from response
                error_msg = f"HTTP {response.status_code}"
                try:
                    error_data = response.json()
                    if isinstance(error_data, dict):
                        error_msg = error_data.get(
                            "error",
                            error_data.get("message", error_msg),
                        )
                except Exception:
                    error_msg = response.text[:200] if response.text else error_msg

                return ToolResult(
                    success=False,
                    output={"status_code": response.status_code},
                    error=str(error_msg),
                    error_code=f"HTTP_{response.status_code}",
                    retryable=is_retryable,
                )

            # Parse response
            try:
                data = response.json()
            except Exception:
                # Non-JSON response
                data = {"text": response.text}

            output = self._extract_response(data, endpoint.response_path)

            return ToolResult(
                success=True,
                output=output,
            )

        except TimeoutError:
            logger.warning(
                "http_request_timeout",
                capability_name=capability_name,
                timeout_ms=effective_timeout,
                url=endpoint.url,
            )
            return ToolResult(
                success=False,
                output={},
                error=f"HTTP request timed out after {effective_timeout}ms",
                error_code="TIMEOUT",
                retryable=True,
            )

        except httpx.ConnectError as e:
            logger.warning(
                "http_connection_error",
                capability_name=capability_name,
                url=endpoint.url,
                error=str(e),
            )
            return ToolResult(
                success=False,
                output={},
                error=f"Connection error: {e}",
                error_code="CONNECTION_ERROR",
                retryable=True,
            )

        except httpx.RequestError as e:
            logger.error(
                "http_request_error",
                capability_name=capability_name,
                url=endpoint.url,
                error=str(e),
            )
            return ToolResult(
                success=False,
                output={},
                error=str(e),
                error_code="REQUEST_ERROR",
                retryable=False,
            )

        except Exception as e:
            logger.error(
                "http_unexpected_error",
                capability_name=capability_name,
                error=str(e),
                exc_info=True,
            )
            return ToolResult(
                success=False,
                output={},
                error=str(e),
                error_code="UNEXPECTED_ERROR",
                retryable=False,
            )
