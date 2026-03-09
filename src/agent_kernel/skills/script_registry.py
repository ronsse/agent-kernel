"""Register skill script capabilities with the tool broker."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

import structlog

from agent_kernel.core.schemas import CapabilityDef, SideEffect
from agent_kernel.skills.policy import SkillPolicy
from agent_kernel.skills.store import SkillStoreLocalFS
from agent_kernel.tools.adapters.skill_script import (
    SkillScriptAdapter,
    SkillScriptCommand,
)
from agent_kernel.tools.broker import ToolBroker
from agent_kernel.tools.registry import CapabilityRegistry

logger = structlog.get_logger(__name__)


def register_skill_scripts(
    registry: CapabilityRegistry,
    broker: ToolBroker,
    skills_dir: Path | str,
    policy: SkillPolicy,
    extensions: Iterable[str],
    timeout_ms: int = 30000,
) -> int:
    skills_path = Path(skills_dir).expanduser()
    if not skills_path.exists():
        logger.info("skills_dir_missing", path=str(skills_path))
        return 0

    store = SkillStoreLocalFS(skills_path)
    manifests = store.list_manifests_sync()
    if not manifests:
        return 0

    ext_set = {ext.lower() for ext in extensions if ext}
    adapter = SkillScriptAdapter(default_timeout_ms=timeout_ms)
    broker.add_adapter(adapter)

    registered = 0
    for manifest in manifests:
        if not policy.allows_script(manifest):
            continue

        skill_dir = Path(manifest.origin.path) if manifest.origin.path else skills_path / manifest.name
        scripts_dir = skill_dir / "scripts"
        if not scripts_dir.exists():
            continue

        for script in sorted(scripts_dir.iterdir()):
            if not script.is_file():
                continue
            if ext_set and script.suffix.lower() not in ext_set:
                continue

            script_name = _sanitize_script_name(script.stem)
            capability_name = f"skill.{manifest.skill_id}.{script_name}@v1"
            command = _build_command(script)
            if not command:
                logger.warning(
                    "skill_script_skipped",
                    script=str(script),
                    reason="unsupported_script_type",
                )
                continue

            adapter.register(
                capability_name,
                SkillScriptCommand(
                    command=command,
                    working_dir=str(skill_dir),
                ),
            )

            registry.register(
                CapabilityDef(
                    capability_name=capability_name,
                    description=f"Run skill script {manifest.skill_id}/{script_name}",
                    input_schema={"type": "object", "additionalProperties": True},
                    output_schema={"type": "object", "additionalProperties": True},
                    side_effect_level=SideEffect.LOCAL_WRITE,
                    requires_approval_default=True,
                    timeout_ms=timeout_ms,
                    adapter_type="skill_script",
                )
            )
            registered += 1

    if registered:
        logger.info("skill_scripts_registered", count=registered)
    return registered


def _sanitize_script_name(name: str) -> str:
    name = name.strip()
    if not name:
        return "script"
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", name)


def _build_command(script: Path) -> list[str] | None:
    if script.suffix.lower() == ".py":
        return [sys.executable, str(script)]
    if script.suffix.lower() == ".sh":
        return ["/bin/bash", str(script)]
    if script.is_file() and script.stat().st_mode & 0o111:
        return [str(script)]
    return None
