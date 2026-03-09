"""Source Registry for v1.0.2 flexible context retrieval.

The SourceRegistry loads source descriptors from configuration and
provides schema-aware filter validation for retrieval planning.
"""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml

from agent_kernel.core.schemas.source_descriptor import (
    FieldDescriptor,
    SourceConstraint,
    SourceDescriptor,
)

logger = structlog.get_logger(__name__)


class SourceRegistry:
    """Registry of source descriptors for schema-aware retrieval.

    The registry:
    1. Loads descriptors from configs/sources/
    2. Provides field/operator validation for retrieval filters
    3. Exposes source constraints for retrieval planning
    """

    def __init__(
        self,
        config_dir: str | Path | None = None,
        sources: list[SourceDescriptor] | None = None,
    ) -> None:
        """Initialize the registry.

        Args:
            config_dir: Directory containing source YAML files.
            sources: Pre-loaded sources (for testing or programmatic use).
        """
        self._sources: dict[str, SourceDescriptor] = {}

        if sources:
            for source in sources:
                self._sources[source.source_id] = source

        if config_dir:
            self._load_from_directory(Path(config_dir))

        logger.info(
            "source_registry_initialized",
            source_count=len(self._sources),
        )

    def _load_from_directory(self, config_dir: Path) -> None:
        """Load sources from YAML files in directory."""
        if not config_dir.exists():
            logger.warning("source_config_dir_not_found", path=str(config_dir))
            return

        for yaml_file in config_dir.glob("*.yaml"):
            try:
                self._load_source_file(yaml_file)
            except Exception as e:
                logger.warning(
                    "source_load_failed",
                    file=str(yaml_file),
                    error=str(e),
                )

    def _load_source_file(self, path: Path) -> None:
        """Load a single source descriptor from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        if not data:
            return

        # Parse fields
        fields = []
        for field_data in data.get("fields", []):
            field = FieldDescriptor(
                name=field_data.get("name", ""),
                type=field_data.get("type", "string"),
                allowed_ops=field_data.get("allowed_ops", []),
                description=field_data.get("description"),
                examples=field_data.get("examples", []),
            )
            fields.append(field)

        # Parse constraints
        constraints_data = data.get("constraints", {})
        constraints = SourceConstraint(
            can_store_text=constraints_data.get("can_store_text", True),
            max_retention_days=constraints_data.get("max_retention_days"),
            allowed_entity_types=constraints_data.get("allowed_entity_types", []),
            requires_live_fetch=constraints_data.get("requires_live_fetch", False),
            notes=constraints_data.get("notes"),
        )

        source = SourceDescriptor(
            source_id=data.get("source_id", path.stem),
            description=data.get("description", ""),
            fields=fields,
            constraints=constraints,
        )

        self._sources[source.source_id] = source
        logger.debug(
            "source_loaded",
            source_id=source.source_id,
            field_count=len(fields),
        )

    def get(self, source_id: str) -> SourceDescriptor | None:
        """Get a source descriptor by ID."""
        return self._sources.get(source_id)

    def list_sources(self) -> list[SourceDescriptor]:
        """List all loaded source descriptors."""
        return list(self._sources.values())

    def list_source_ids(self) -> list[str]:
        """List all source IDs."""
        return list(self._sources.keys())

    def has_source(self, source_id: str) -> bool:
        """Check if a source exists."""
        return source_id in self._sources

    def validate_filter(
        self,
        source_id: str,
        field_name: str,
        op: str,
    ) -> tuple[bool, str | None]:
        """Validate a filter against a source's schema.

        Args:
            source_id: ID of the source to validate against.
            field_name: Name of the field to filter on.
            op: Filter operator.

        Returns:
            Tuple of (is_valid, error_message).
        """
        source = self.get(source_id)
        if source is None:
            return False, f"Source '{source_id}' not found in registry"

        return source.validate_filter(field_name, op)

    def validate_entity_type(
        self,
        source_id: str,
        entity_type: str,
    ) -> tuple[bool, str | None]:
        """Validate that an entity type is allowed for a source.

        Args:
            source_id: ID of the source.
            entity_type: Entity type to validate.

        Returns:
            Tuple of (is_valid, error_message).
        """
        source = self.get(source_id)
        if source is None:
            return False, f"Source '{source_id}' not found in registry"

        allowed = source.list_entity_types()
        if allowed and entity_type not in allowed:
            return False, (
                f"Entity type '{entity_type}' not allowed for source '{source_id}' "
                f"(allowed: {allowed})"
            )

        return True, None

    def get_field(
        self,
        source_id: str,
        field_name: str,
    ) -> FieldDescriptor | None:
        """Get a field descriptor from a source.

        Args:
            source_id: ID of the source.
            field_name: Name of the field.

        Returns:
            FieldDescriptor or None if not found.
        """
        source = self.get(source_id)
        if source is None:
            return None
        return source.get_field(field_name)

    def requires_live_fetch(self, source_id: str) -> bool:
        """Check if a source requires live fetching (not indexed)."""
        source = self.get(source_id)
        if source is None:
            return False
        return source.constraints.requires_live_fetch

    def can_store_text(self, source_id: str) -> bool:
        """Check if a source allows text storage in derived indexes."""
        source = self.get(source_id)
        if source is None:
            return True  # Default to allowing
        return source.can_store()

    def add_source(self, source: SourceDescriptor) -> None:
        """Add a source descriptor programmatically."""
        self._sources[source.source_id] = source

    def remove_source(self, source_id: str) -> bool:
        """Remove a source by ID."""
        if source_id in self._sources:
            del self._sources[source_id]
            return True
        return False
