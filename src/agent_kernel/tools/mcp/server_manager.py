"""MCP server configuration loading and adapter wiring."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import yaml

from agent_kernel.tools.adapters.mcp import MCPServerConfig, MCPToolAdapter, MCPToolMapping

logger = structlog.get_logger(__name__)

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_env(value: str) -> str:
    """Expand ${VAR} placeholders using environment variables."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return os.environ.get(name, "")

    return _ENV_PATTERN.sub(replace, value)


def _expand_any(value: Any) -> Any:
    if isinstance(value, str):
        return _expand_env(value)
    if isinstance(value, list):
        return [_expand_any(item) for item in value]
    if isinstance(value, dict):
        return {k: _expand_any(v) for k, v in value.items()}
    return value


@dataclass(frozen=True)
class MCPServerSpec:
    server_id: str
    display_name: str | None = None
    enabled: bool = True
    mode: str = "managed_by_kernel"  # managed_by_kernel | external
    transport: str = "stdio"  # stdio | http | sse | websocket
    cwd: str | None = None
    start_command: list[str] | None = None
    endpoint: str | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPToolMappingSpec:
    capability_name: str
    server_id: str
    tool_name: str


def load_mcp_server_specs(config_dir: str | Path) -> dict[str, MCPServerSpec]:
    """Load MCP server specs from YAML files."""
    config_dir = Path(config_dir)
    if not config_dir.exists():
        return {}

    specs: dict[str, MCPServerSpec] = {}
    for yaml_file in sorted(config_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        data = _expand_any(data)

        server_id = data.get("server_id", yaml_file.stem)
        spec = MCPServerSpec(
            server_id=server_id,
            display_name=data.get("display_name"),
            enabled=bool(data.get("enabled", True)),
            mode=data.get("mode", "managed_by_kernel"),
            transport=data.get("transport", "stdio"),
            cwd=data.get("cwd"),
            start_command=data.get("start_command"),
            endpoint=data.get("endpoint"),
            env=data.get("env", {}) or {},
        )
        specs[server_id] = spec

    return specs


def load_mcp_mappings(config_dir: str | Path) -> dict[str, MCPToolMapping]:
    """Load MCP capability-to-tool mappings from YAML files."""
    config_dir = Path(config_dir)
    if not config_dir.exists():
        return {}

    mappings: dict[str, MCPToolMapping] = {}
    for yaml_file in sorted(config_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        data = _expand_any(data)
        server_id = data.get("server_id")
        for capability_name, mapping in (data.get("mappings") or {}).items():
            tool_name = mapping.get("tool_name")
            if not tool_name:
                continue
            mappings[capability_name] = MCPToolMapping(
                capability_name=capability_name,
                tool_name=tool_name,
                server_name=server_id,
            )

    return mappings


class MCPServerManager:
    """Loads MCP configs and wires MCPToolAdapter."""

    def __init__(self, configs_dir: str | Path) -> None:
        self._configs_dir = Path(configs_dir)

    async def configure_adapter(self, adapter: MCPToolAdapter) -> bool:
        """Configure adapter with servers and mappings."""
        servers_dir = self._configs_dir / "mcp_servers"
        mappings_dir = self._configs_dir / "mcp_mappings"

        server_specs = load_mcp_server_specs(servers_dir)
        mappings = load_mcp_mappings(mappings_dir)

        if mappings:
            adapter.register_mappings(mappings)

        configured = False
        for spec in server_specs.values():
            if not spec.enabled:
                continue

            if spec.mode != "managed_by_kernel":
                logger.info(
                    "mcp_server_external_skipped",
                    server_id=spec.server_id,
                    transport=spec.transport,
                )
                continue

            if spec.transport != "stdio":
                logger.warning(
                    "mcp_server_transport_unsupported",
                    server_id=spec.server_id,
                    transport=spec.transport,
                )
                continue

            if not spec.start_command:
                logger.warning(
                    "mcp_server_missing_start_command",
                    server_id=spec.server_id,
                )
                continue

            command = spec.start_command[0]
            args = spec.start_command[1:]

            try:
                await adapter.add_server(
                    MCPServerConfig(
                        name=spec.server_id,
                        command=command,
                        args=args,
                        env=spec.env,
                        transport="stdio",
                        cwd=spec.cwd,
                    )
                )
                configured = True
            except Exception as exc:
                logger.warning(
                    "mcp_server_add_failed",
                    server_id=spec.server_id,
                    error=str(exc),
                )

        return configured or bool(mappings)
