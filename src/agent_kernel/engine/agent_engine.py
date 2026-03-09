"""AgentEngine protocol - the interface all engines must implement.

Engines are responsible for taking context and producing plans.
They must NOT call tools directly or own memory.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agent_kernel.core.schemas import AgentProfile, ContextPacket, Plan

if TYPE_CHECKING:
    from agent_kernel.engine.thinking_policy import ThinkingPolicy


@runtime_checkable
class AgentEngine(Protocol):
    """Protocol for agent engines that produce plans.

    All engines must implement this interface. Engines:
    - Accept ContextPacket and AgentProfile
    - Return structured Plan
    - Must NOT call tools directly
    - Must NOT own memory stores
    - Must NOT decide approval policies
    """

    @property
    def engine_id(self) -> str:
        """Unique identifier for this engine."""
        ...

    @property
    def version(self) -> str:
        """Engine version string."""
        ...

    async def propose(
        self,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
        thinking_policy: ThinkingPolicy | None = None,
    ) -> Plan:
        """Generate a plan from context.

        Args:
            context_packet: The assembled context.
            agent_profile: The agent's configuration.
            thinking_policy: Optional thinking policy with model/reasoning
                overrides. When provided, the engine should use the policy's
                model_id, reasoning_effort, max_tokens, and temperature
                instead of agent_profile.llm_config values.

        Returns:
            A structured Plan.
        """
        ...


class BaseAgentEngine(ABC):
    """Base class for agent engines with common functionality."""

    def __init__(self, engine_id: str, version: str = "1.0.0") -> None:
        """Initialize base engine.

        Args:
            engine_id: Unique identifier for this engine.
            version: Engine version string.
        """
        self._engine_id = engine_id
        self._version = version

    @property
    def engine_id(self) -> str:
        """Unique identifier for this engine."""
        return self._engine_id

    @property
    def version(self) -> str:
        """Engine version string."""
        return self._version

    @abstractmethod
    async def propose(
        self,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
        thinking_policy: ThinkingPolicy | None = None,
    ) -> Plan:
        """Generate a plan from context."""
