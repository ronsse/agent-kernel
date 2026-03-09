"""Engine Registry - manage available agent engines."""

from __future__ import annotations

import structlog

from agent_kernel.core.errors import EngineNotFoundError
from agent_kernel.engine.agent_engine import AgentEngine

logger = structlog.get_logger(__name__)


class EngineRegistry:
    """Registry for agent engines.

    Manages available engines and provides lookup by ID.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._engines: dict[str, AgentEngine] = {}
        logger.debug("engine_registry_initialized")

    def register(self, engine: AgentEngine) -> None:
        """Register an engine.

        Args:
            engine: The engine to register.
        """
        self._engines[engine.engine_id] = engine
        logger.info(
            "engine_registered",
            engine_id=engine.engine_id,
            version=engine.version,
        )

    def get(self, engine_id: str) -> AgentEngine | None:
        """Get an engine by ID.

        Args:
            engine_id: The engine identifier.

        Returns:
            The engine or None if not found.
        """
        return self._engines.get(engine_id)

    def get_or_raise(self, engine_id: str) -> AgentEngine:
        """Get an engine by ID or raise if not found.

        Args:
            engine_id: The engine identifier.

        Returns:
            The engine.

        Raises:
            EngineNotFoundError: If engine not registered.
        """
        engine = self.get(engine_id)
        if engine is None:
            raise EngineNotFoundError(engine_id)
        return engine

    def list_engines(self) -> list[AgentEngine]:
        """List all registered engines."""
        return list(self._engines.values())

    def list_ids(self) -> list[str]:
        """List all engine IDs."""
        return list(self._engines.keys())

    def has(self, engine_id: str) -> bool:
        """Check if an engine is registered."""
        return engine_id in self._engines

    def unregister(self, engine_id: str) -> bool:
        """Unregister an engine.

        Args:
            engine_id: The engine to unregister.

        Returns:
            True if unregistered, False if not found.
        """
        if engine_id in self._engines:
            del self._engines[engine_id]
            logger.debug("engine_unregistered", engine_id=engine_id)
            return True
        return False

    def clear(self) -> None:
        """Clear all registered engines."""
        self._engines.clear()
        logger.debug("engine_registry_cleared")
