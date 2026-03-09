"""Context Pack Resolver for v1.0.2 flexible context retrieval.

The ContextPackResolver loads context packs from configuration and
determines which packs should be included based on the current scope.
"""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml

from agent_kernel.core.schemas import ContextRef, RefType
from agent_kernel.core.schemas.context_pack import (
    ContextPack,
    ContextPackScope,
    ContextPackSelector,
)

logger = structlog.get_logger(__name__)


class ContextPackResolver:
    """Resolves which context packs to include based on scope.

    The resolver:
    1. Loads packs from configs/context_packs/
    2. Matches selectors against the current scope
    3. Returns packs sorted by priority (lower = higher priority)
    4. Deduplicates refs across packs
    """

    def __init__(
        self,
        config_dir: str | Path | None = None,
        packs: list[ContextPack] | None = None,
    ) -> None:
        """Initialize the resolver.

        Args:
            config_dir: Directory containing pack YAML files.
            packs: Pre-loaded packs (for testing or programmatic use).
        """
        self._packs: dict[str, ContextPack] = {}

        if packs:
            for pack in packs:
                self._packs[pack.pack_id] = pack

        if config_dir:
            self._load_from_directory(Path(config_dir))

        logger.info(
            "context_pack_resolver_initialized",
            pack_count=len(self._packs),
        )

    def _load_from_directory(self, config_dir: Path) -> None:
        """Load packs from YAML files in directory."""
        if not config_dir.exists():
            logger.warning("pack_config_dir_not_found", path=str(config_dir))
            return

        for yaml_file in config_dir.glob("*.yaml"):
            try:
                self._load_pack_file(yaml_file)
            except Exception as e:
                logger.warning(
                    "pack_load_failed",
                    file=str(yaml_file),
                    error=str(e),
                )

    def _load_pack_file(self, path: Path) -> None:
        """Load a single pack from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        if not data:
            return

        # Parse refs from YAML format
        refs = []
        for ref_data in data.get("refs", []):
            ref = ContextRef(
                ref_type=RefType(ref_data.get("ref_type", "spec")),
                ref_id=ref_data.get("ref_id", ""),
                uri=ref_data.get("uri"),
                metadata=ref_data.get("metadata", {}),
            )
            refs.append(ref)

        # Parse selectors
        selectors = []
        for sel_data in data.get("selectors", []):
            selector = ContextPackSelector(
                vault_id=sel_data.get("vault_id"),
                project_id=sel_data.get("project_id"),
                workflow_id=sel_data.get("workflow_id"),
                agent_profile_id=sel_data.get("agent_profile_id"),
                path_globs=sel_data.get("path_globs", []),
            )
            selectors.append(selector)

        pack = ContextPack(
            pack_id=data.get("pack_id", path.stem),
            name=data.get("name", path.stem),
            description=data.get("description"),
            priority=data.get("priority", 50),
            selectors=selectors,
            refs=refs,
            include_policy=data.get("include_policy", "relevance"),
            max_tokens=data.get("max_tokens"),
        )

        self._packs[pack.pack_id] = pack
        logger.debug("pack_loaded", pack_id=pack.pack_id, refs=len(refs))

    def resolve(
        self,
        scope: ContextPackScope,
    ) -> list[ContextPack]:
        """Resolve which packs to include for the given scope.

        Args:
            scope: The context scope (vault, project, workflow, etc.)

        Returns:
            List of matching packs sorted by priority (ascending).
        """
        matching_packs: list[ContextPack] = []

        for pack in self._packs.values():
            if pack.matches_scope(
                vault_id=scope.vault_id,
                project_id=scope.project_id,
                workflow_id=scope.workflow_id,
                agent_profile_id=scope.agent_profile_id,
                path=scope.path,
            ):
                matching_packs.append(pack)

        # Sort by priority (lower number = higher priority)
        matching_packs.sort(key=lambda p: p.priority)

        logger.debug(
            "packs_resolved",
            scope_vault=scope.vault_id,
            scope_project=scope.project_id,
            scope_workflow=scope.workflow_id,
            matched_count=len(matching_packs),
            pack_ids=[p.pack_id for p in matching_packs],
        )

        return matching_packs

    def get_all_refs(
        self,
        packs: list[ContextPack],
    ) -> list[ContextRef]:
        """Get all refs from the given packs, deduplicated.

        Args:
            packs: List of context packs.

        Returns:
            List of unique ContextRefs.
        """
        seen_ref_ids: set[str] = set()
        refs: list[ContextRef] = []

        for pack in packs:
            for ref in pack.refs:
                if ref.ref_id not in seen_ref_ids:
                    seen_ref_ids.add(ref.ref_id)
                    refs.append(ref)

        return refs

    def get_pack(self, pack_id: str) -> ContextPack | None:
        """Get a pack by ID."""
        return self._packs.get(pack_id)

    def list_packs(self) -> list[ContextPack]:
        """List all loaded packs."""
        return list(self._packs.values())

    def add_pack(self, pack: ContextPack) -> None:
        """Add a pack programmatically."""
        self._packs[pack.pack_id] = pack

    def remove_pack(self, pack_id: str) -> bool:
        """Remove a pack by ID."""
        if pack_id in self._packs:
            del self._packs[pack_id]
            return True
        return False
