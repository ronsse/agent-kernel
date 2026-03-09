"""CLI subsystem - command-line interface for agent kernel.

Provides commands for:
- Initialization
- Running workflows
- Viewing traces
- Listing capabilities
"""

from agent_kernel.cli.main import app, main

__all__ = [
    "app",
    "main",
]
