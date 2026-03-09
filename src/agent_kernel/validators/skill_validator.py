"""Skill integrity validator."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import structlog

from agent_kernel.validators.results import (
    CheckStatus,
    ValidationCheck,
    ValidationResult,
)

logger = structlog.get_logger(__name__)

_FRONTMATTER_BOUNDARY = "---"
_SCRIPT_EXTENSIONS = {".sh", ".py", ".bash"}


class SkillValidator:
    """Validates skill directory integrity."""

    def __init__(self, skills_dir: Path | str) -> None:
        self._skills_dir = Path(skills_dir).expanduser()

    def validate(self, skill_id: str) -> ValidationResult:
        """Validate a single skill by ID."""
        result = ValidationResult(target=f"skill:{skill_id}")

        # Check existence
        skill_dir = self._find_skill_dir(skill_id)
        if skill_dir is None:
            result.checks.append(ValidationCheck(
                name="skill_exists",
                status=CheckStatus.ERROR,
                message=f"Skill '{skill_id}' not found in {self._skills_dir}",
            ))
            return result

        result.checks.append(ValidationCheck(
            name="skill_exists",
            status=CheckStatus.PASS,
            message=f"Found at {skill_dir}",
        ))

        # Check SKILL.md
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            result.checks.append(ValidationCheck(
                name="manifest_exists",
                status=CheckStatus.ERROR,
                message="SKILL.md not found",
            ))
            return result

        result.checks.append(ValidationCheck(
            name="manifest_exists",
            status=CheckStatus.PASS,
            message="SKILL.md present",
        ))

        # Check frontmatter fields
        result.checks.append(self._check_manifest_fields(skill_md))

        # Check references
        result.checks.append(self._check_references(skill_dir, skill_md))

        # Check scripts
        scripts = self._find_scripts(skill_dir)
        result.checks.append(self._check_script_shebangs(scripts))
        result.checks.append(self._check_script_permissions(scripts))

        return result

    def validate_all(self) -> list[ValidationResult]:
        """Validate all skills in the store."""
        if not self._skills_dir.exists():
            return []

        return [
            self.validate(entry.name)
            for entry in sorted(self._skills_dir.iterdir())
            if entry.is_dir()
        ]

    def _find_skill_dir(self, skill_id: str) -> Path | None:
        """Locate a skill directory by ID."""
        if not self._skills_dir.exists():
            return None

        for entry in sorted(self._skills_dir.iterdir()):
            if entry.is_dir() and entry.name == skill_id:
                return entry

        return None

    def _parse_frontmatter(self, path: Path) -> dict:
        """Parse YAML frontmatter from a file."""
        try:
            import yaml  # noqa: PLC0415
        except ImportError:
            return {}

        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines or lines[0].strip() != _FRONTMATTER_BOUNDARY:
            return {}

        end_idx = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == _FRONTMATTER_BOUNDARY:
                end_idx = idx
                break

        if end_idx is None:
            return {}

        raw = "\n".join(lines[1:end_idx])
        try:
            data = yaml.safe_load(raw) or {}
        except Exception:
            data = {}
        return data

    def _check_manifest_fields(self, skill_md: Path) -> ValidationCheck:
        """Check that required frontmatter fields are present."""
        data = self._parse_frontmatter(skill_md)

        missing: list[str] = []
        if not data.get("name"):
            missing.append("name")
        if not data.get("description"):
            missing.append("description")

        if missing:
            return ValidationCheck(
                name="manifest_fields",
                status=CheckStatus.ERROR,
                message=f"Missing required frontmatter fields: {', '.join(missing)}",
                detail="Add these fields to the YAML frontmatter in SKILL.md",
            )

        return ValidationCheck(
            name="manifest_fields",
            status=CheckStatus.PASS,
            message=f"Frontmatter OK (name='{data['name']}')",
        )

    def _check_references(self, skill_dir: Path, skill_md: Path) -> ValidationCheck:
        """Check that referenced directories exist."""
        refs_dir = skill_dir / "references"
        text = skill_md.read_text(encoding="utf-8")

        # Check if SKILL.md mentions references/
        if "references/" in text and not refs_dir.exists():
            return ValidationCheck(
                name="references_exist",
                status=CheckStatus.ERROR,
                message="SKILL.md references 'references/' but directory is missing",
            )

        if refs_dir.exists():
            return ValidationCheck(
                name="references_exist",
                status=CheckStatus.PASS,
                message=f"References directory present "
                f"({len(list(refs_dir.iterdir()))} files)",
            )

        return ValidationCheck(
            name="references_exist",
            status=CheckStatus.PASS,
            message="No references directory (not required)",
        )

    def _find_scripts(self, skill_dir: Path) -> list[Path]:
        """Find script files in a skill directory."""
        scripts: list[Path] = []
        for root_dir in [skill_dir, skill_dir / "scripts"]:
            if not root_dir.exists():
                continue
            scripts.extend(
                f for f in root_dir.iterdir()
                if f.is_file() and f.suffix in _SCRIPT_EXTENSIONS
            )
        return scripts

    def _check_script_shebangs(self, scripts: list[Path]) -> ValidationCheck:
        """Check that scripts have shebang lines."""
        if not scripts:
            return ValidationCheck(
                name="script_shebangs",
                status=CheckStatus.PASS,
                message="No scripts to check",
            )

        missing: list[str] = []
        for script in scripts:
            try:
                first_line = script.read_text(encoding="utf-8").split("\n", 1)[0]
                if not first_line.startswith("#!"):
                    missing.append(script.name)
            except Exception:
                missing.append(script.name)

        if missing:
            return ValidationCheck(
                name="script_shebangs",
                status=CheckStatus.WARN,
                message=f"Scripts missing shebang: {', '.join(missing)}",
                detail="Add #!/usr/bin/env python3 or #!/usr/bin/env bash",
            )

        return ValidationCheck(
            name="script_shebangs",
            status=CheckStatus.PASS,
            message=f"All {len(scripts)} script(s) have shebangs",
        )

    def _check_script_permissions(self, scripts: list[Path]) -> ValidationCheck:
        """Check that scripts have executable permissions (POSIX only)."""
        if os.name != "posix":
            return ValidationCheck(
                name="script_permissions",
                status=CheckStatus.SKIP,
                message="Permission check skipped (non-POSIX)",
            )

        if not scripts:
            return ValidationCheck(
                name="script_permissions",
                status=CheckStatus.PASS,
                message="No scripts to check",
            )

        non_exec: list[str] = []
        for script in scripts:
            mode = script.stat().st_mode
            if not (mode & stat.S_IXUSR):
                non_exec.append(script.name)

        if non_exec:
            return ValidationCheck(
                name="script_permissions",
                status=CheckStatus.WARN,
                message=f"Scripts not executable: {', '.join(non_exec)}",
                detail="Run: chmod +x <script>",
            )

        return ValidationCheck(
            name="script_permissions",
            status=CheckStatus.PASS,
            message=f"All {len(scripts)} script(s) are executable",
        )
