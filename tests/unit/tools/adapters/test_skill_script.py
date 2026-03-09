"""Tests for SkillScriptAdapter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_kernel.tools.adapters.skill_script import SkillScriptAdapter, SkillScriptCommand


@pytest.mark.asyncio
async def test_skill_script_adapter_executes(tmp_path: Path) -> None:
    script_path = tmp_path / "echo_skill.py"
    script_path.write_text(
        "import json, sys\n"
        "data = json.load(sys.stdin)\n"
        "print(json.dumps({'received': data.get('value')}))\n",
        encoding="utf-8",
    )

    adapter = SkillScriptAdapter(default_timeout_ms=1000)
    adapter.register(
        "skill.test.echo@v1",
        SkillScriptCommand(command=[sys.executable, str(script_path)], working_dir=str(tmp_path)),
    )

    result = await adapter.execute("skill.test.echo@v1", {"value": "ok"}, 1000)

    assert result.success is True
    assert result.output["received"] == "ok"
