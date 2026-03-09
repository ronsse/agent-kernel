"""Load local library tools and register with the broker.

This keeps library imports consolidated in one place.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import structlog
import yaml

from agent_kernel.tools.broker import ToolBroker

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class LibraryMapping:
    capability_name: str
    import_path: str


def _expand_env(value: str) -> str:
    for key, val in os.environ.items():
        value = value.replace(f"${{{key}}}", val)
    return value


def _expand_any(value: Any) -> Any:
    if isinstance(value, str):
        return _expand_env(value)
    if isinstance(value, list):
        return [_expand_any(item) for item in value]
    if isinstance(value, dict):
        return {k: _expand_any(v) for k, v in value.items()}
    return value


def _add_repo_path(repo_path: str | None) -> None:
    if not repo_path:
        return
    resolved = Path(repo_path).expanduser()
    if not resolved.exists():
        logger.warning("library_repo_path_missing", path=str(resolved))
        return
    if str(resolved) not in sys.path:
        sys.path.insert(0, str(resolved))
        logger.info("library_repo_path_added", path=str(resolved))


def _load_env_file(env_file: str | None) -> None:
    if not env_file:
        return
    path = Path(env_file).expanduser()
    if not path.exists():
        logger.warning("library_env_file_missing", path=str(path))
        return

    loaded = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1

    logger.info("library_env_loaded", path=str(path), keys_loaded=loaded)


def _import_callable(import_path: str) -> Any:
    if ":" in import_path:
        module_path, attr_path = import_path.split(":", 1)
    else:
        module_path, attr_path = import_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    target = module
    for attr in attr_path.split("."):
        target = getattr(target, attr)
    return target


def _normalize_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {"result": result}


def _wrap_tool(func: Callable[..., Any]) -> Callable[..., Any]:
    async def _wrapped(**kwargs: Any) -> dict[str, Any]:
        if asyncio.iscoroutinefunction(func):
            result = await func(**kwargs)
        else:
            result = func(**kwargs)
        return _normalize_result(result)

    return _wrapped


def configure_library_tools(broker: ToolBroker, configs_dir: Path) -> int:
    """Register local library tools from configs/library_mappings."""
    mappings_dir = configs_dir / "library_mappings"
    if not mappings_dir.exists():
        return 0

    registered = 0
    adapter = broker.local_adapter

    for yaml_file in sorted(mappings_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        data = _expand_any(data)
        repo_path = data.get("repo_path") or os.environ.get(data.get("repo_path_env", ""))
        _add_repo_path(repo_path)
        env_file = data.get("env_file") or os.environ.get(data.get("env_file_env", ""))
        _load_env_file(env_file)

        for capability_name, mapping in (data.get("mappings") or {}).items():
            import_path = mapping.get("import") or mapping.get("import_path")
            if not import_path:
                continue
            try:
                func = _import_callable(import_path)
                adapter.register(capability_name, _wrap_tool(func))
                registered += 1
            except Exception as exc:
                logger.warning(
                    "library_tool_register_failed",
                    capability_name=capability_name,
                    import_path=import_path,
                    error=str(exc),
                )

    if registered:
        logger.info("library_tools_registered", count=registered)
    return registered
