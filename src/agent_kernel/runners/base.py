"""Base classes for external runner adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agent_kernel.runners.types import RunnerRequest, RunnerResponse


class RunnerAdapter(ABC):
    """Thin wrapper around an external agent runner (Cursor CLI, Claude Code, etc).

    Design goals:
    - No dependency on any particular orchestration framework.
    - Stable interface for the Tool Broker to call.
    - Runner-specific behavior lives behind the adapter.
    """

    runner_id: str

    @abstractmethod
    def run(self, request: RunnerRequest) -> RunnerResponse:
        """Execute the runner with the given request."""
        raise NotImplementedError

    # Optional extension points (implement as needed per runner)
    def list_threads(self, workspace_path: str, limit: int = 20) -> list[dict[str, Any]]:
        """List recent thread/session IDs (if supported by the runner)."""
        return []

    def list_models(self) -> list[str]:
        """List supported model IDs (if supported by the runner)."""
        return []
