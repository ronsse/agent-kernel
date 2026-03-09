"""Capability Registry - load and manage tool capability definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

from agent_kernel.core.errors import CapabilityNotFoundError
from agent_kernel.core.schemas import (
    CapabilityDef,
    RateLimit,
    RedactionPolicy,
    SideEffect,
)

logger = structlog.get_logger(__name__)


class CapabilityRegistry:
    """Registry for tool capability definitions.

    Loads capability definitions from YAML files and provides
    lookup, validation, and listing functionality.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._capabilities: dict[str, CapabilityDef] = {}
        logger.debug("capability_registry_initialized")

    def register(self, capability: CapabilityDef) -> None:
        """Register a capability definition.

        Args:
            capability: The capability definition to register.
        """
        self._capabilities[capability.capability_name] = capability
        logger.info(
            "capability_registered",
            capability_name=capability.capability_name,
            adapter_type=capability.adapter_type,
        )

    def get(self, capability_name: str) -> CapabilityDef | None:
        """Get a capability by name.

        Args:
            capability_name: The capability name (e.g., "tasks.create@v1").

        Returns:
            The capability definition or None if not found.
        """
        return self._capabilities.get(capability_name)

    def get_capability(self, capability_name: str) -> CapabilityDef | None:
        """Legacy alias for get()."""
        return self.get(capability_name)

    def get_or_raise(self, capability_name: str) -> CapabilityDef:
        """Get a capability by name or raise if not found.

        Args:
            capability_name: The capability name.

        Returns:
            The capability definition.

        Raises:
            CapabilityNotFoundError: If capability is not registered.
        """
        capability = self.get(capability_name)
        if capability is None:
            raise CapabilityNotFoundError(capability_name)
        return capability

    def list_capabilities(self) -> list[CapabilityDef]:
        """List all registered capabilities.

        Returns:
            List of all capability definitions.
        """
        return list(self._capabilities.values())

    def list_names(self) -> list[str]:
        """List all capability names.

        Returns:
            List of capability names.
        """
        return list(self._capabilities.keys())

    def has(self, capability_name: str) -> bool:
        """Check if a capability is registered.

        Args:
            capability_name: The capability name.

        Returns:
            True if registered, False otherwise.
        """
        return capability_name in self._capabilities

    def load_from_yaml(self, yaml_path: str | Path) -> CapabilityDef:
        """Load a capability definition from a YAML file.

        Args:
            yaml_path: Path to the YAML file.

        Returns:
            The loaded capability definition.
        """
        yaml_path = Path(yaml_path)
        logger.debug("loading_capability_from_yaml", path=str(yaml_path))

        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        capability = self._parse_capability(data)
        self.register(capability)
        return capability

    def load_from_directory(self, directory: str | Path) -> list[CapabilityDef]:
        """Load all capability definitions from a directory.

        Args:
            directory: Path to directory containing YAML files.

        Returns:
            List of loaded capability definitions.
        """
        directory = Path(directory)
        if not directory.exists():
            logger.warning("capability_directory_not_found", path=str(directory))
            return []

        loaded = []
        for yaml_file in sorted(directory.glob("*.yaml")):
            try:
                capability = self.load_from_yaml(yaml_file)
                loaded.append(capability)
            except Exception as e:
                logger.error(
                    "capability_load_failed",
                    path=str(yaml_file),
                    error=str(e),
                )
                raise

        logger.info(
            "capabilities_loaded_from_directory",
            path=str(directory),
            count=len(loaded),
        )
        return loaded

    def _parse_capability(self, data: dict[str, Any]) -> CapabilityDef:
        """Parse capability data from YAML into CapabilityDef.

        Supports legacy aliases with deprecation warnings:
        - 'name' -> 'capability_name'
        - 'side_effect' -> 'side_effect_level'
        - 'requires_approval' -> 'requires_approval_default'
        - 'adapter.type' (nested) -> 'adapter_type'

        Args:
            data: Parsed YAML data.

        Returns:
            CapabilityDef instance.
        """
        # Apply legacy aliases with warnings
        data = self._apply_legacy_aliases(data)

        # Parse rate limit if present
        rate_limit = None
        if "rate_limit" in data:
            rl_data = data["rate_limit"]
            rate_limit = RateLimit(
                max_calls_per_minute=rl_data.get("max_calls", 60),
                max_calls_per_hour=rl_data.get("max_calls", 60) * 60 // rl_data.get("window_seconds", 60),
            )

        # Parse redaction policy if present
        redaction_policy = None
        if "redaction_policy" in data:
            rp_data = data["redaction_policy"]
            redaction_policy = RedactionPolicy(
                redact_fields=rp_data.get("redact_fields", []),
                redact_patterns=rp_data.get("redact_patterns", []),
            )

        # Parse side effect level
        side_effect_str = data.get("side_effect_level", "none")
        if side_effect_str in {"read", "write", "execute"}:
            side_effect = side_effect_str
        else:
            side_effect_map = {
                "none": SideEffect.NONE,
                "read": SideEffect.READ,
                "write": SideEffect.WRITE,
                "execute": SideEffect.EXECUTE,
                "local": SideEffect.LOCAL_WRITE,
                "external": SideEffect.EXTERNAL_WRITE,
            }
            side_effect = side_effect_map.get(side_effect_str, SideEffect.NONE)

        return CapabilityDef(
            capability_name=data["capability_name"],
            description=data.get("description", ""),
            input_schema=data.get("input_schema", {}),
            output_schema=data.get("output_schema", {}),
            side_effect_level=side_effect,
            requires_approval_default=data.get("requires_approval_default", False),
            timeout_ms=data.get("timeout_ms", 30000),
            rate_limit=rate_limit,
            redaction_policy=redaction_policy,
            adapter_type=data.get("adapter_type", "local"),
        )

    def _apply_legacy_aliases(self, data: dict[str, Any]) -> dict[str, Any]:
        """Apply legacy config aliases with deprecation warnings.

        This supports backwards compatibility with v1.0.0 configs.

        Args:
            data: Raw YAML data.

        Returns:
            Data with aliases resolved.
        """
        result = dict(data)
        aliases_used = []

        # 'capability_id' -> 'capability_name'
        if "capability_id" in result and "capability_name" not in result:
            result["capability_name"] = result.pop("capability_id")
            aliases_used.append("capability_id -> capability_name")

        # 'name' -> 'capability_name' (legacy v1.0.0)
        if "name" in result and "capability_name" not in result:
            result["capability_name"] = result.pop("name")
            aliases_used.append("name -> capability_name")

        # 'side_effect' -> 'side_effect_level'
        if "side_effect" in result and "side_effect_level" not in result:
            result["side_effect_level"] = result.pop("side_effect")
            aliases_used.append("side_effect -> side_effect_level")

        # 'requires_approval' -> 'requires_approval_default'
        if "requires_approval" in result and "requires_approval_default" not in result:
            result["requires_approval_default"] = result.pop("requires_approval")
            aliases_used.append("requires_approval -> requires_approval_default")

        # 'adapter.type' (nested) -> 'adapter_type'
        if "adapter" in result and isinstance(result["adapter"], dict):
            adapter = result.pop("adapter")
            if "type" in adapter and "adapter_type" not in result:
                result["adapter_type"] = adapter["type"]
                aliases_used.append("adapter.type -> adapter_type")
            # Also check for adapter.target for local functions
            if "target" in adapter:
                result.setdefault("adapter_config", {})["target"] = adapter["target"]

        # Log deprecation warnings
        if aliases_used:
            capability_name = result.get("capability_name", "unknown")
            logger.warning(
                "legacy_config_aliases_used",
                capability_name=capability_name,
                aliases=aliases_used,
                message="Please update your config to use canonical field names",
            )

        return result

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """Export registry as a dictionary.

        Returns:
            Dictionary mapping capability names to their definitions.
        """
        return {
            name: cap.model_dump()
            for name, cap in self._capabilities.items()
        }

    def clear(self) -> None:
        """Clear all registered capabilities."""
        self._capabilities.clear()
        logger.debug("capability_registry_cleared")
