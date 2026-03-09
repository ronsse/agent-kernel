"""Plugin discovery via importlib.metadata entry points.

Provides a standard mechanism for discovering and loading plugins
(engines, stores, adapters) registered via ``pyproject.toml`` entry
point groups.

Available entry point groups
----------------------------
- ``agentkernel.engines``         -- Agent engine implementations
- ``agentkernel.stores.vector``   -- Vector store backends
- ``agentkernel.stores.graph``    -- Graph store backends
- ``agentkernel.stores.document`` -- Document store backends
- ``agentkernel.stores.trace``    -- Trace sink backends

Usage
-----
::

    from agent_kernel.plugins import discover_plugins

    engines = discover_plugins("agentkernel.engines")
    # {"custom": <class 'CustomEngine'>, ...}

Registering a plugin (in your package's ``pyproject.toml``)::

    [project.entry-points."agentkernel.engines"]
    my_engine = "my_package.engine:MyEngine"
"""

from __future__ import annotations

import importlib.metadata
import sys

import structlog

logger = structlog.get_logger(__name__)

#: All recognised entry-point groups for the kernel plugin system.
ENTRY_POINT_GROUPS: list[str] = [
    "agentkernel.engines",
    "agentkernel.stores.vector",
    "agentkernel.stores.graph",
    "agentkernel.stores.document",
    "agentkernel.stores.trace",
]


def discover_plugins(group: str) -> dict[str, type]:
    """Discover plugins registered under *group* via entry points.

    Parameters
    ----------
    group:
        The entry-point group name (e.g. ``"agentkernel.engines"``).

    Returns
    -------
    dict[str, type]
        Mapping of entry-point name to the loaded object (usually a
        class).  Entries that fail to load are logged and skipped.
    """
    plugins: dict[str, type] = {}

    if sys.version_info >= (3, 12):
        eps = importlib.metadata.entry_points(group=group)
    else:
        # Python 3.9-3.11 compatibility
        eps = importlib.metadata.entry_points().get(group, [])

    for ep in eps:
        try:
            obj = ep.load()
            plugins[ep.name] = obj
            logger.debug(
                "plugin_discovered",
                group=group,
                name=ep.name,
                module=ep.value,
            )
        except Exception:
            logger.warning(
                "plugin_load_failed",
                group=group,
                name=ep.name,
                exc_info=True,
            )

    return plugins


__all__ = ["discover_plugins", "ENTRY_POINT_GROUPS"]
