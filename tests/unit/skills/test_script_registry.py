"""Tests for skill script registry."""

from __future__ import annotations

from pathlib import Path

from agent_kernel.skills.policy import SkillPolicy
from agent_kernel.skills.script_registry import register_skill_scripts
from agent_kernel.tools.broker import ToolBroker
from agent_kernel.tools.registry import CapabilityRegistry


def _write_skill(skill_root: Path, name: str) -> None:
    skill_dir = skill_root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Demo Skill\nskill_id: demo-skill\n---\n",
        encoding="utf-8",
    )
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "run.py").write_text("print('ok')\n", encoding="utf-8")


def test_register_skill_scripts(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "demo-skill")

    registry = CapabilityRegistry()
    broker = ToolBroker(registry)
    policy = SkillPolicy(
        allow_script_execution=True,
        allowed_skill_ids={"demo-skill"},
        allowed_origins={"local"},
    )

    registered = register_skill_scripts(
        registry=registry,
        broker=broker,
        skills_dir=skills_dir,
        policy=policy,
        extensions=[".py"],
        timeout_ms=1000,
    )

    assert registered == 1
    assert registry.get("skill.demo-skill.run@v1") is not None
