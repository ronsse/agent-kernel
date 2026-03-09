"""Tests for SkillStoreLocalFS."""

from __future__ import annotations

from pathlib import Path

import yaml

from agent_kernel.skills.store import SkillStoreLocalFS


def _write_skill(skill_root: Path, name: str, frontmatter: dict, body: str) -> None:
    skill_dir = skill_root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = "---\n"
    content += yaml.safe_dump(frontmatter, sort_keys=False)
    content += "---\n"
    content += body
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def test_list_manifests_parses_frontmatter(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    _write_skill(
        skills_dir,
        "daily-review",
        {
            "name": "Daily Review",
            "description": "Review tasks and notes.",
            "allowed-tools": "tasks.list notes.search",
            "license": "MIT",
        },
        "## Steps\n1. Do the thing\n",
    )

    store = SkillStoreLocalFS(skills_dir)
    manifests = store.list_manifests_sync()

    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.skill_id == "daily-review"
    assert manifest.name == "Daily Review"
    assert manifest.description == "Review tasks and notes."
    assert manifest.allowed_tools == ["tasks.list", "notes.search"]
    assert manifest.license == "MIT"


def test_search_skills_orders_by_relevance(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    _write_skill(
        skills_dir,
        "calendar-timeblocking",
        {"name": "Calendar Timeblocking", "description": "Block focus time."},
        "Focus blocks.\n",
    )
    _write_skill(
        skills_dir,
        "email-triage",
        {"name": "Email Triage", "description": "Process inbox efficiently."},
        "Inbox workflow.\n",
    )

    store = SkillStoreLocalFS(skills_dir)
    results = store.search_sync("email", top_k=2)

    assert results
    assert results[0].skill_id == "email-triage"


def test_load_skill_includes_references(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(
        skills_dir,
        "obsidian-rules",
        {"name": "Obsidian Rules", "description": "Vault conventions."},
        "Follow these rules.\n",
    )

    ref_dir = skills_dir / "obsidian-rules" / "references"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "style.md").write_text("Style guide", encoding="utf-8")

    store = SkillStoreLocalFS(skills_dir)
    result = store.load_sync("obsidian-rules", include=["SKILL.md", "references/"])

    assert result is not None
    assert "SKILL.md" in result.files
    assert "references/style.md" in result.files
    assert any(resource.kind == "reference" for resource in result.resources)
