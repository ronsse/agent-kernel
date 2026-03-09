"""Local library tools for skill discovery and loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.config import get_settings
from agent_kernel.skills.store import SkillStoreLocalFS

logger = structlog.get_logger(__name__)


def _get_store() -> SkillStoreLocalFS | None:
    settings = get_settings()
    skills_dir = Path(settings.skills_dir).expanduser()
    if not skills_dir.exists():
        logger.info("skills_dir_missing", path=str(skills_dir))
        return None
    return SkillStoreLocalFS(skills_dir)


def search_skills(query: str, top_k: int = 10) -> dict[str, Any]:
    """Search skill manifests by query."""
    store = _get_store()
    if not store:
        return {
            "skills": [],
            "available": False,
        }

    results = store.search_sync(query, top_k=top_k)
    return {
        "skills": [skill.model_dump(mode="json") for skill in results],
        "count": len(results),
        "available": True,
    }


def load_skill(skill_id: str, include: list[str] | None = None) -> dict[str, Any]:
    """Load a skill's content and requested files."""
    store = _get_store()
    if not store:
        return {
            "skill": None,
            "available": False,
        }

    result = store.load_sync(skill_id, include=include)
    if result is None:
        return {
            "skill": None,
            "available": True,
            "error": "skill_not_found",
        }

    return {
        "skill": result.model_dump(mode="json"),
        "available": True,
    }
