"""Enrichment Configuration Registry (v1.0.5).

Loads and manages source-specific enrichment configurations from YAML files.
Each source (Obsidian, Slack, Outlook, etc.) can have its own config file
in configs/enrichment/ with customized prompts and thresholds.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import yaml

from agent_kernel.core.schemas.enrichment_config import (
    DEFAULT_OBSIDIAN_CONFIG,
    EnrichmentThresholds,
    SourceEnrichmentConfig,
)

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


class EnrichmentConfigRegistry:
    """Registry for source-specific enrichment configurations.

    Loads YAML config files from a directory and provides lookup by source_id.
    Falls back to built-in defaults if no config file exists.
    """

    def __init__(
        self,
        config_dir: str | Path = "configs/enrichment",
        load_defaults: bool = True,
    ) -> None:
        """Initialize the registry.

        Args:
            config_dir: Directory containing enrichment config YAML files.
            load_defaults: Whether to load built-in default configs.
        """
        self.config_dir = Path(config_dir)
        self._configs: dict[str, SourceEnrichmentConfig] = {}

        # Load built-in defaults
        if load_defaults:
            self._register_defaults()

        # Load configs from directory
        if self.config_dir.exists():
            self._load_from_directory()

    def _register_defaults(self) -> None:
        """Register built-in default configurations."""
        self._configs["obsidian"] = DEFAULT_OBSIDIAN_CONFIG
        logger.debug("default_configs_registered", sources=list(self._configs.keys()))

    def _load_from_directory(self) -> None:
        """Load all YAML configs from the config directory."""
        if not self.config_dir.exists():
            logger.debug("enrichment_config_dir_not_found", path=str(self.config_dir))
            return

        yaml_files = list(self.config_dir.glob("*.yaml")) + list(
            self.config_dir.glob("*.yml")
        )

        for yaml_file in yaml_files:
            try:
                config = self._load_yaml_config(yaml_file)
                if config:
                    self._configs[config.source_id] = config
                    logger.info(
                        "enrichment_config_loaded",
                        source_id=config.source_id,
                        file=yaml_file.name,
                    )
            except Exception:
                logger.exception("enrichment_config_load_failed", file=yaml_file.name)

    def _load_yaml_config(self, yaml_file: Path) -> SourceEnrichmentConfig | None:
        """Load a single YAML config file.

        Args:
            yaml_file: Path to the YAML file.

        Returns:
            Parsed SourceEnrichmentConfig or None if invalid.
        """
        with open(yaml_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or not isinstance(data, dict):
            logger.warning("enrichment_config_empty", file=yaml_file.name)
            return None

        if "source_id" not in data or "system_prompt" not in data:
            logger.warning(
                "enrichment_config_skipped",
                file=yaml_file.name,
                reason="missing_source_id_or_system_prompt",
            )
            return None

        # Handle nested thresholds
        if "thresholds" in data and isinstance(data["thresholds"], dict):
            data["thresholds"] = EnrichmentThresholds(**data["thresholds"])

        return SourceEnrichmentConfig(**data)

    def get(self, source_id: str) -> SourceEnrichmentConfig | None:
        """Get configuration for a source.

        Args:
            source_id: Source identifier (obsidian, slack, etc.).

        Returns:
            SourceEnrichmentConfig or None if not found.
        """
        return self._configs.get(source_id)

    def get_or_default(
        self, source_id: str, default: SourceEnrichmentConfig | None = None
    ) -> SourceEnrichmentConfig:
        """Get configuration for a source, with fallback.

        Args:
            source_id: Source identifier.
            default: Default config to use if not found.

        Returns:
            SourceEnrichmentConfig (found, default, or Obsidian default).
        """
        config = self._configs.get(source_id)
        if config:
            return config

        if default:
            return default

        # Fall back to Obsidian config with modified source_id
        fallback = DEFAULT_OBSIDIAN_CONFIG.model_copy(update={"source_id": source_id})
        logger.debug(
            "enrichment_config_fallback",
            source_id=source_id,
            fallback="obsidian_default",
        )
        return fallback

    def register(self, config: SourceEnrichmentConfig) -> None:
        """Register a configuration programmatically.

        Args:
            config: SourceEnrichmentConfig to register.
        """
        self._configs[config.source_id] = config
        logger.debug("enrichment_config_registered", source_id=config.source_id)

    def list_sources(self) -> list[str]:
        """List all registered source IDs.

        Returns:
            List of source identifiers.
        """
        return list(self._configs.keys())

    def reload(self) -> None:
        """Reload all configs from the directory."""
        self._configs.clear()
        self._register_defaults()
        if self.config_dir.exists():
            self._load_from_directory()
        logger.info("enrichment_configs_reloaded", count=len(self._configs))


# Global registry instance (lazy-loaded)
_registry: EnrichmentConfigRegistry | None = None


def get_enrichment_registry(
    config_dir: str | Path = "configs/enrichment",
) -> EnrichmentConfigRegistry:
    """Get or create the global enrichment config registry.

    Args:
        config_dir: Directory containing config files.

    Returns:
        EnrichmentConfigRegistry instance.
    """
    global _registry
    if _registry is None:
        _registry = EnrichmentConfigRegistry(config_dir=config_dir)
    return _registry
