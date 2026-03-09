"""Skill policy for governing script execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from agent_kernel.core.schemas.skill import SkillManifest


@dataclass(frozen=True)
class SkillPolicy:
    """Policy for gating skill script execution."""

    allow_script_execution: bool = False
    allowed_skill_ids: set[str] | None = None
    allowed_origins: set[str] | None = None

    def allows_script(self, manifest: SkillManifest) -> bool:
        if not self.allow_script_execution:
            return False

        if self.allowed_skill_ids and manifest.skill_id not in self.allowed_skill_ids:
            return False

        if self.allowed_origins and manifest.origin.kind not in self.allowed_origins:
            return False

        return True

    @classmethod
    def from_settings(
        cls,
        allow_script_execution: bool,
        allowed_skill_ids: Iterable[str] | None = None,
        allowed_origins: Iterable[str] | None = None,
    ) -> "SkillPolicy":
        ids = {item.strip() for item in (allowed_skill_ids or []) if item.strip()}
        origins = {item.strip() for item in (allowed_origins or []) if item.strip()}
        return cls(
            allow_script_execution=allow_script_execution,
            allowed_skill_ids=ids or None,
            allowed_origins=origins or None,
        )
