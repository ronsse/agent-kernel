"""Capability schemas - CapabilityDef for tool registration."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from agent_kernel.core.schemas.base import KernelModel
from agent_kernel.core.schemas.plan import SideEffect


def normalize_side_effect_level(level: SideEffect | str) -> SideEffect:
    """Normalize legacy side effect strings to canonical SideEffect."""
    if isinstance(level, SideEffect):
        return level
    value = str(level).lower()
    if value == "none":
        return SideEffect.NONE
    if value == "read":
        return SideEffect.READ
    if value in {"write", "local"}:
        return SideEffect.WRITE if value == "write" else SideEffect.LOCAL_WRITE
    if value in {"execute", "external"}:
        return SideEffect.EXECUTE if value == "execute" else SideEffect.EXTERNAL_WRITE
    return SideEffect.NONE


class RateLimit(KernelModel):
    """Rate limiting configuration for a capability."""

    max_calls_per_minute: int = 60
    max_calls_per_hour: int = 1000


class RedactionPolicy(KernelModel):
    """Policy for redacting sensitive data in logs."""

    redact_fields: list[str] = Field(default_factory=list)
    redact_patterns: list[str] = Field(default_factory=list)  # Regex patterns


class CapabilityDef(KernelModel):
    """Defines a tool capability's schema and policies.

    Loaded from YAML files in configs/capabilities/.
    """

    capability_name: str  # e.g., "tasks.create@v1"
    description: str
    input_schema: dict[str, Any]  # JSON Schema for args
    output_schema: dict[str, Any]  # JSON Schema for return
    side_effect_level: SideEffect | str = SideEffect.NONE
    requires_approval_default: bool = False
    timeout_ms: int = 30000
    rate_limit: RateLimit | None = None
    redaction_policy: RedactionPolicy | None = None
    adapter_type: str = "local"  # local | http | subprocess | mcp

    @property
    def base_name(self) -> str:
        """Get capability name without version suffix."""
        return self.capability_name.split("@")[0]

    @property
    def version(self) -> str | None:
        """Get version suffix if present."""
        if "@" in self.capability_name:
            return self.capability_name.split("@")[1]
        return None


class CapabilitySpec(KernelModel):
    """Legacy capability spec used by older tests and adapters."""

    capability_name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    side_effect_level: str = "none"
    adapter_type: str = "local_function"
    adapter_config: dict[str, Any] | None = None
    timeout_ms: int = 30000
    max_retries: int = 0
    tags: list[str] = Field(default_factory=list)
    category: str | None = None

    @property
    def base_name(self) -> str:
        """Get capability name without version suffix."""
        return self.capability_name.split("@")[0]

    @property
    def version(self) -> str | None:
        """Get version suffix if present."""
        if "@" in self.capability_name:
            return self.capability_name.split("@")[1]
        return None
