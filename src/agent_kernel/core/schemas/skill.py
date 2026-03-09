"""Skill schemas for portable agent skills (v1.1.x).

Skills represent portable procedural guidance (SKILL.md + references).
They are referenced as first-class context items but are not tools.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from agent_kernel.core.schemas.base import VersionedModel


class SkillOrigin(VersionedModel):
    """Provenance for a skill installation."""

    kind: Literal["local", "git", "registry"]
    repo: str | None = None
    ref: str | None = None
    path: str | None = None
    installed_at: datetime
    content_hash: str


class SkillManifest(VersionedModel):
    """Manifest metadata parsed from SKILL.md frontmatter."""

    skill_id: str
    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: list[str] | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    origin: SkillOrigin


class SkillResourceRef(VersionedModel):
    """Reference to a resource within a skill directory."""

    path: str
    kind: Literal["skill_md", "reference", "asset", "script"]
    hash: str
    bytes: int | None = None


class SkillLoadResult(VersionedModel):
    """Loaded skill content and referenced resources."""

    manifest: SkillManifest
    resources: list[SkillResourceRef] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
