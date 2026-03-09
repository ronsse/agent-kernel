"""Tests for SkillValidator."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from agent_kernel.validators.results import CheckStatus
from agent_kernel.validators.skill_validator import SkillValidator


def _write_skill_md(
    skill_dir: Path,
    *,
    name: str = "Test Skill",
    description: str = "A test skill",
    body: str = "# Instructions\nDo the thing.",
    references_mention: bool = False,
) -> None:
    """Write a valid SKILL.md with frontmatter."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    refs = "\nSee references/ for details." if references_mention else ""
    content = f"---\nname: {name}\ndescription: {description}\n---\n{body}{refs}\n"
    (skill_dir / "SKILL.md").write_text(content)


class TestValidSkill:
    def test_valid_skill_passes(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "my-skill"
        _write_skill_md(skill_dir)

        validator = SkillValidator(skills_dir)
        result = validator.validate("my-skill")

        assert result.passed
        assert result.error_count == 0
        check_names = {c.name for c in result.checks}
        assert "skill_exists" in check_names
        assert "manifest_exists" in check_names
        assert "manifest_fields" in check_names


class TestSkillNotFound:
    def test_nonexistent_skill_errors(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        validator = SkillValidator(skills_dir)
        result = validator.validate("nonexistent")

        assert not result.passed
        assert result.error_count == 1
        exists_check = next(c for c in result.checks if c.name == "skill_exists")
        assert exists_check.status == CheckStatus.ERROR


class TestMissingSkillMd:
    def test_missing_skill_md_errors(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "empty-skill"
        skill_dir.mkdir(parents=True)

        validator = SkillValidator(skills_dir)
        result = validator.validate("empty-skill")

        assert not result.passed
        manifest_check = next(c for c in result.checks if c.name == "manifest_exists")
        assert manifest_check.status == CheckStatus.ERROR


class TestManifestFields:
    def test_missing_name_errors(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "no-name"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: Has desc but no name\n---\nBody."
        )

        validator = SkillValidator(skills_dir)
        result = validator.validate("no-name")

        fields_check = next(c for c in result.checks if c.name == "manifest_fields")
        assert fields_check.status == CheckStatus.ERROR
        assert "name" in fields_check.message

    def test_missing_description_errors(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "no-desc"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: Has Name\n---\nBody."
        )

        validator = SkillValidator(skills_dir)
        result = validator.validate("no-desc")

        fields_check = next(c for c in result.checks if c.name == "manifest_fields")
        assert fields_check.status == CheckStatus.ERROR
        assert "description" in fields_check.message

    def test_empty_frontmatter_errors(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "empty-fm"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\n---\nJust body.")

        validator = SkillValidator(skills_dir)
        result = validator.validate("empty-fm")

        fields_check = next(c for c in result.checks if c.name == "manifest_fields")
        assert fields_check.status == CheckStatus.ERROR
        assert "name" in fields_check.message
        assert "description" in fields_check.message


class TestScripts:
    def test_script_without_shebang_warns(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "has-script"
        _write_skill_md(skill_dir)

        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "run.py"
        script.write_text("print('hello')\n")

        validator = SkillValidator(skills_dir)
        result = validator.validate("has-script")

        shebang_check = next(c for c in result.checks if c.name == "script_shebangs")
        assert shebang_check.status == CheckStatus.WARN
        assert "run.py" in shebang_check.message

    @pytest.mark.skipif(os.name != "posix", reason="POSIX only")
    def test_non_executable_script_warns(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "non-exec"
        _write_skill_md(skill_dir)

        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "run.sh"
        script.write_text("#!/bin/bash\necho hi\n")
        # Ensure NOT executable
        script.chmod(stat.S_IRUSR | stat.S_IWUSR)

        validator = SkillValidator(skills_dir)
        result = validator.validate("non-exec")

        perm_check = next(c for c in result.checks if c.name == "script_permissions")
        assert perm_check.status == CheckStatus.WARN
        assert "run.sh" in perm_check.message

    def test_valid_script_passes(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "good-script"
        _write_skill_md(skill_dir)

        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "run.py"
        script.write_text("#!/usr/bin/env python3\nprint('hello')\n")
        if os.name == "posix":
            script.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

        validator = SkillValidator(skills_dir)
        result = validator.validate("good-script")

        shebang_check = next(c for c in result.checks if c.name == "script_shebangs")
        assert shebang_check.status == CheckStatus.PASS


class TestReferences:
    def test_missing_references_dir_errors(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "broken-refs"
        _write_skill_md(skill_dir, references_mention=True)

        validator = SkillValidator(skills_dir)
        result = validator.validate("broken-refs")

        refs_check = next(c for c in result.checks if c.name == "references_exist")
        assert refs_check.status == CheckStatus.ERROR

    def test_references_dir_present_passes(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "with-refs"
        _write_skill_md(skill_dir, references_mention=True)
        (skill_dir / "references").mkdir()

        validator = SkillValidator(skills_dir)
        result = validator.validate("with-refs")

        refs_check = next(c for c in result.checks if c.name == "references_exist")
        assert refs_check.status == CheckStatus.PASS


class TestValidateAll:
    def test_validate_all(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        _write_skill_md(skills_dir / "skill-a")
        _write_skill_md(skills_dir / "skill-b")

        validator = SkillValidator(skills_dir)
        results = validator.validate_all()

        assert len(results) == 2
        targets = {r.target for r in results}
        assert "skill:skill-a" in targets
        assert "skill:skill-b" in targets

    def test_empty_store(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        validator = SkillValidator(skills_dir)
        results = validator.validate_all()
        assert results == []

    def test_nonexistent_store(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "does-not-exist"

        validator = SkillValidator(skills_dir)
        results = validator.validate_all()
        assert results == []
