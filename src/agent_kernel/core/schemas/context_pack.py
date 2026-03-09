"""Context Pack schemas for v1.0.2 flexible context retrieval.

Context Packs formalize "system specifications" - vault rules, project
conventions, workflow guidelines - that should be consistently included
in context without dumping entire directories.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agent_kernel.core.schemas.base import KernelModel, VersionedModel
from agent_kernel.core.schemas.context import ContextRef


class ContextPackSelector(KernelModel):
    """Selector that determines when a context pack should be included.

    A pack is included if ANY selector matches the current scope.
    All non-None fields in a selector must match for that selector to match.
    """

    vault_id: str | None = Field(
        default=None,
        description="Match when operating on this vault",
    )
    project_id: str | None = Field(
        default=None,
        description="Match when scope includes this project",
    )
    workflow_id: str | None = Field(
        default=None,
        description="Match when running this workflow",
    )
    agent_profile_id: str | None = Field(
        default=None,
        description="Match when this agent profile is active",
    )
    path_globs: list[str] = Field(
        default_factory=list,
        description="Match when scope path matches any of these globs",
    )

    def matches(
        self,
        vault_id: str | None = None,
        project_id: str | None = None,
        workflow_id: str | None = None,
        agent_profile_id: str | None = None,
        path: str | None = None,
    ) -> bool:
        """Check if this selector matches the given scope.

        Returns True if all non-None selector fields match the scope.
        """
        import fnmatch

        # Check vault_id
        if self.vault_id is not None and self.vault_id != vault_id:
            return False

        # Check project_id
        if self.project_id is not None and self.project_id != project_id:
            return False

        # Check workflow_id
        if self.workflow_id is not None and self.workflow_id != workflow_id:
            return False

        # Check agent_profile_id
        if self.agent_profile_id is not None and self.agent_profile_id != agent_profile_id:
            return False

        # Check path globs
        if self.path_globs and path is not None:
            if not any(fnmatch.fnmatch(path, glob) for glob in self.path_globs):
                return False

        return True


class ContextPack(VersionedModel):
    """A curated set of context refs that get included based on selectors.

    Context Packs solve the problem of "rules/specs get lost" by formalizing
    which vault conventions, formatting rules, and workflow guidelines
    should be included for different scopes.
    """

    pack_id: str = Field(
        description="Unique identifier for this pack",
    )
    name: str = Field(
        description="Human-readable name",
    )
    description: str | None = Field(
        default=None,
        description="Description of what this pack contains",
    )
    priority: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Lower number = higher priority (included first in context)",
    )
    selectors: list[ContextPackSelector] = Field(
        default_factory=list,
        description="Selectors that determine when this pack is included",
    )
    refs: list[ContextRef] = Field(
        default_factory=list,
        description="Context references to include when pack is selected",
    )
    include_policy: Literal["always", "relevance", "manual"] = Field(
        default="relevance",
        description=(
            "always: include regardless of selectors; "
            "relevance: include if any selector matches; "
            "manual: never auto-include"
        ),
    )
    max_tokens: int | None = Field(
        default=None,
        description="Optional token budget for this pack's content",
    )

    def matches_scope(
        self,
        vault_id: str | None = None,
        project_id: str | None = None,
        workflow_id: str | None = None,
        agent_profile_id: str | None = None,
        path: str | None = None,
    ) -> bool:
        """Check if this pack should be included for the given scope.

        Returns:
            True if the pack should be included based on policy and selectors.
        """
        if self.include_policy == "always":
            return True

        if self.include_policy == "manual":
            return False

        # include_policy == "relevance"
        if not self.selectors:
            # No selectors means always include for relevance policy
            return True

        return any(
            selector.matches(
                vault_id=vault_id,
                project_id=project_id,
                workflow_id=workflow_id,
                agent_profile_id=agent_profile_id,
                path=path,
            )
            for selector in self.selectors
        )


class ContextPackScope(KernelModel):
    """Scope parameters for resolving context packs.

    This is passed to ContextPackResolver to determine which packs to include.
    """

    vault_id: str | None = Field(
        default=None,
        description="ID of the vault being operated on",
    )
    project_id: str | None = Field(
        default=None,
        description="ID of the project in scope",
    )
    workflow_id: str | None = Field(
        default=None,
        description="ID of the workflow being executed",
    )
    agent_profile_id: str | None = Field(
        default=None,
        description="ID of the agent profile in use",
    )
    path: str | None = Field(
        default=None,
        description="File or directory path in scope",
    )
