"""Skill store implementations for local skills directory."""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
import yaml

from agent_kernel.core.schemas.skill import (
    SkillLoadResult,
    SkillManifest,
    SkillOrigin,
    SkillResourceRef,
)

logger = structlog.get_logger(__name__)

_FRONTMATTER_BOUNDARY = "---"
_ALLOWED_TOOLS_KEY = "allowed-tools"


class SkillStore(ABC):
    """Abstract interface for skill discovery and loading."""

    @abstractmethod
    def list_manifests_sync(self) -> list[SkillManifest]:
        """List available skill manifests."""

    @abstractmethod
    def get_manifest_sync(self, skill_id: str) -> SkillManifest | None:
        """Get a skill manifest by ID."""

    @abstractmethod
    def search_sync(self, query: str, top_k: int = 10) -> list[SkillManifest]:
        """Search manifests by query."""

    @abstractmethod
    def load_sync(
        self,
        skill_id: str,
        include: list[str] | None = None,
    ) -> SkillLoadResult | None:
        """Load skill content and referenced resources."""

    async def list_manifests(self) -> list[SkillManifest]:
        return self.list_manifests_sync()

    async def get_manifest(self, skill_id: str) -> SkillManifest | None:
        return self.get_manifest_sync(skill_id)

    async def search(self, query: str, top_k: int = 10) -> list[SkillManifest]:
        return self.search_sync(query, top_k=top_k)

    async def load(
        self,
        skill_id: str,
        include: list[str] | None = None,
    ) -> SkillLoadResult | None:
        return self.load_sync(skill_id, include=include)


class SkillStoreLocalFS(SkillStore):
    """Local filesystem implementation for skills stored by directory."""

    def __init__(self, skills_dir: Path | str) -> None:
        self._skills_dir = Path(skills_dir).expanduser()

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    def list_manifests_sync(self) -> list[SkillManifest]:
        manifests: list[SkillManifest] = []
        for skill_dir in self._iter_skill_dirs():
            manifest = self._load_manifest(skill_dir)
            if manifest:
                manifests.append(manifest)
        return manifests

    def get_manifest_sync(self, skill_id: str) -> SkillManifest | None:
        skill_dir = self._find_skill_dir(skill_id)
        if not skill_dir:
            return None
        return self._load_manifest(skill_dir)

    def search_sync(self, query: str, top_k: int = 10) -> list[SkillManifest]:
        query = (query or "").strip()
        manifests = self.list_manifests_sync()
        if not query:
            return manifests[:top_k]

        tokens = [t for t in re.split(r"[\s,]+", query.lower()) if t]
        scored: list[tuple[float, SkillManifest]] = []
        for manifest in manifests:
            score = self._score_manifest(manifest, tokens)
            if score > 0:
                scored.append((score, manifest))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [m for _, m in scored[:top_k]]

    def load_sync(
        self,
        skill_id: str,
        include: list[str] | None = None,
    ) -> SkillLoadResult | None:
        skill_dir = self._find_skill_dir(skill_id)
        if not skill_dir:
            return None

        manifest = self._load_manifest(skill_dir)
        if not manifest:
            return None

        include = include or ["SKILL.md"]
        files: dict[str, str] = {}
        resources: list[SkillResourceRef] = []

        for rel_path in self._resolve_includes(skill_dir, include):
            content = self._read_text(rel_path)
            if content is None:
                continue
            relative = str(rel_path.relative_to(skill_dir))
            files[relative] = content
            resources.append(self._resource_ref(skill_dir, rel_path))

        return SkillLoadResult(
            manifest=manifest,
            resources=resources,
            files=files,
        )

    def _iter_skill_dirs(self) -> list[Path]:
        if not self._skills_dir.exists():
            return []
        dirs = []
        for entry in sorted(self._skills_dir.iterdir()):
            if entry.is_dir() and (entry / "SKILL.md").exists():
                dirs.append(entry)
        return dirs

    def _find_skill_dir(self, skill_id: str) -> Path | None:
        for entry in self._iter_skill_dirs():
            if entry.name == skill_id:
                return entry
            manifest = self._load_manifest(entry)
            if manifest and manifest.skill_id == skill_id:
                return entry
        return None

    def _load_manifest(self, skill_dir: Path) -> SkillManifest | None:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None

        frontmatter, body = self._parse_frontmatter(skill_md)
        name = str(frontmatter.get("name") or skill_dir.name)
        description = str(
            frontmatter.get("description") or self._infer_description(body)
        )
        skill_id = str(frontmatter.get("skill_id") or frontmatter.get("id") or skill_dir.name)

        allowed_tools = self._normalize_allowed_tools(
            frontmatter.get(_ALLOWED_TOOLS_KEY) or frontmatter.get("allowed_tools")
        )

        metadata = self._extract_metadata(frontmatter)
        content_hash = self._hash_skill_content(skill_dir, skill_md)

        origin = SkillOrigin(
            kind="local",
            path=str(skill_dir),
            installed_at=self._installed_at(skill_dir),
            content_hash=content_hash,
        )

        if frontmatter.get("name") and frontmatter.get("name") != skill_dir.name:
            logger.warning(
                "skill_name_mismatch",
                skill_id=skill_id,
                declared_name=frontmatter.get("name"),
                folder_name=skill_dir.name,
            )

        return SkillManifest(
            skill_id=skill_id,
            name=name,
            description=description,
            license=frontmatter.get("license"),
            compatibility=frontmatter.get("compatibility"),
            allowed_tools=allowed_tools,
            metadata=metadata,
            origin=origin,
        )

    def _parse_frontmatter(self, path: Path) -> tuple[dict[str, Any], str]:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines or lines[0].strip() != _FRONTMATTER_BOUNDARY:
            return {}, text

        end_idx = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == _FRONTMATTER_BOUNDARY:
                end_idx = idx
                break

        if end_idx is None:
            return {}, text

        raw_frontmatter = "\n".join(lines[1:end_idx])
        try:
            data = yaml.safe_load(raw_frontmatter) or {}
        except Exception as exc:
            logger.warning(
                "skill_frontmatter_parse_failed",
                path=str(path),
                error=str(exc),
            )
            data = {}
        body = "\n".join(lines[end_idx + 1 :])
        return data, body

    def _infer_description(self, body: str) -> str:
        for line in body.splitlines():
            candidate = line.strip()
            if not candidate:
                continue
            if candidate.startswith("#"):
                candidate = candidate.lstrip("#").strip()
            return candidate
        return ""

    def _normalize_allowed_tools(self, value: Any) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str):
            tokens = [t for t in re.split(r"[\s,]+", value) if t]
            return tokens or None
        return None

    def _extract_metadata(self, frontmatter: dict[str, Any]) -> dict[str, str]:
        reserved = {
            "id",
            "skill_id",
            "name",
            "description",
            "license",
            "compatibility",
            "allowed_tools",
            _ALLOWED_TOOLS_KEY,
        }
        metadata: dict[str, str] = {}
        for key, value in frontmatter.items():
            if key in reserved:
                continue
            metadata[key] = str(value)
        return metadata

    def _hash_skill_content(self, skill_dir: Path, skill_md: Path) -> str:
        hasher = hashlib.sha256()
        hasher.update(skill_md.read_bytes())
        for ref in self._iter_reference_files(skill_dir):
            hasher.update(ref.read_bytes())
        return hasher.hexdigest()[:32]

    def _iter_reference_files(self, skill_dir: Path) -> list[Path]:
        refs: list[Path] = []
        for folder in ("references", "assets", "scripts"):
            subdir = skill_dir / folder
            if not subdir.exists() or not subdir.is_dir():
                continue
            for entry in sorted(subdir.iterdir()):
                if entry.is_file():
                    refs.append(entry)
        return refs

    def _installed_at(self, skill_dir: Path) -> datetime:
        timestamp = skill_dir.stat().st_mtime
        return datetime.fromtimestamp(timestamp, tz=UTC)

    def _resolve_includes(self, skill_dir: Path, includes: list[str]) -> list[Path]:
        resolved: list[Path] = []
        for include in includes:
            include = include.strip()
            if not include:
                continue
            if include.endswith("/"):
                resolved.extend(self._list_dir(skill_dir, include))
                continue
            candidate = (skill_dir / include).resolve()
            if self._is_within(skill_dir, candidate) and candidate.exists():
                resolved.append(candidate)
        return resolved

    def _list_dir(self, skill_dir: Path, rel_dir: str) -> list[Path]:
        directory = (skill_dir / rel_dir).resolve()
        if not self._is_within(skill_dir, directory):
            return []
        if not directory.exists() or not directory.is_dir():
            return []
        return [entry for entry in sorted(directory.iterdir()) if entry.is_file()]

    def _is_within(self, root: Path, candidate: Path) -> bool:
        try:
            candidate.relative_to(root)
        except ValueError:
            return False
        return True

    def _resource_ref(self, skill_dir: Path, path: Path) -> SkillResourceRef:
        rel = str(path.relative_to(skill_dir))
        kind = self._resource_kind(rel)
        data = path.read_bytes()
        return SkillResourceRef(
            path=rel,
            kind=kind,
            hash=hashlib.sha256(data).hexdigest()[:32],
            bytes=len(data),
        )

    def _resource_kind(self, rel_path: str) -> str:
        if rel_path == "SKILL.md":
            return "skill_md"
        if rel_path.startswith("references/"):
            return "reference"
        if rel_path.startswith("scripts/"):
            return "script"
        return "asset"

    def _read_text(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("skill_file_not_utf8", path=str(path))
            return None

    def _score_manifest(self, manifest: SkillManifest, tokens: list[str]) -> float:
        haystack_meta = " ".join(manifest.metadata.values()).lower()
        name = manifest.name.lower()
        description = manifest.description.lower()
        score = 0.0
        for token in tokens:
            if token in name:
                score += 2.0
            if token in description:
                score += 1.0
            if token in haystack_meta:
                score += 0.5
        return score
