# Public/Private Separation

This guide explains what ships in the public `agentkernel` package versus what stays
private, and how to layer personal configurations, integrations, and workflows on top
of the public package using the **overlay pattern**.

---

## What Ships Publicly

The `agentkernel` package on PyPI contains the framework -- all the reusable
infrastructure for building AI agent systems. Your private overlay provides the
configuration and integrations that make it *your* system.

| Public (`agentkernel` package) | Private (your overlay) |
|---|---|
| Core schemas (ContextPacket, Plan, DecisionTrace, etc.) | Personal agent profiles (identities, system prompts) |
| Memory subsystem (document, vector, graph, event stores) | Personal workflow configs (schedules, integrations) |
| Tool Broker + capability registry | Personal capability definitions |
| Context Assembler | Personal integration code (Todoist, Slack, etc.) |
| Agent engines (CustomEngine, LangGraph adapter) | Personal secrets (`.env`, API keys) |
| Executor + approval gates | Personal data (traces, graph, vectors) |
| Workflow Runner + scheduler | Personal scripts (cron jobs, ops scripts) |
| CLI (`agent-kernel` command) | Personal prompts and templates |
| REST API server (optional `[api]` extra) | Dashboard customizations |
| Tracing subsystem | -- |
| Generic example configs | -- |
| Documentation + examples | -- |

**Rule of thumb:** If it is reusable infrastructure that any developer could benefit
from, it belongs in the public package. If it contains your personal preferences,
secrets, schedules, or third-party account integrations, it belongs in your private
overlay.

---

## The Overlay Pattern

An **overlay** is a separate project that depends on `agentkernel` and adds your
personal configuration on top. Think of it like a Linux overlay filesystem: the base
layer (the package) provides the tools, and your overlay provides the customization.

### Step 1: Create a New Project

```bash
mkdir my-agent-system
cd my-agent-system
```

### Step 2: Create `pyproject.toml`

```toml
[project]
name = "my-agent-system"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "agentkernel[vectors,api]>=0.1.0,<0.2.0",
]

[project.optional-dependencies]
dev = ["pytest", "ruff"]
```

!!! tip "Pin to a compatible range"
    Use `>=0.1.0,<0.2.0` to get bug fixes but avoid breaking changes.
    The `agentkernel` project follows semantic versioning -- minor versions
    add features, major versions may break the API.

### Step 3: Create the Directory Layout

```
my-agent-system/
  pyproject.toml              # Depends on agentkernel
  .env                        # API keys and secrets (gitignored)
  .env.example                # Template showing required variables
  configs/
    agents/                   # Agent profiles (YAML)
      daily_review.yaml
      project_manager.yaml
    workflows/                # Workflow specs (YAML)
      daily_checkin.yaml
      weekly_review.yaml
    capabilities/             # Capability definitions (YAML)
      tasks.create@v1.yaml
    thinking_tiers.yaml       # Thinking tier configuration
  integrations/               # Custom adapters and integrations
    __init__.py
    my_task_adapter.py        # TaskSyncAdapter implementation
    my_calendar_adapter.py    # CalendarAdapter implementation
  scripts/                    # Operational scripts
    start.sh
    run-workflow.sh
  data/                       # Local data (gitignored)
    traces/
    graph/
    vectors/
```

### Step 4: Wire It Together

Create a `main.py` (or package entry point) that loads the public framework with
your private configuration:

```python
"""Bootstrap agentkernel with personal configuration."""

import asyncio
from pathlib import Path

from agent_kernel.core.config import Settings
from agent_kernel.memory.document_store import SQLiteDocumentStore
from agent_kernel.memory.graph_store import SQLiteGraphStore
from agent_kernel.memory.event_log import SQLiteEventLog
from agent_kernel.tools.broker import ToolBroker
from agent_kernel.tools.registry import CapabilityRegistry
from agent_kernel.context.assembler import ContextAssembler
from agent_kernel.executor.executor import DeterministicExecutor
from agent_kernel.workflows.runner import WorkflowRunner


async def main() -> None:
    # Load settings (reads from .env automatically)
    settings = Settings()

    # Initialize memory stores
    doc_store = SQLiteDocumentStore(settings.document_store_path)
    graph_store = SQLiteGraphStore(settings.graph_store_path)
    event_log = SQLiteEventLog(settings.event_log_path)

    # Load personal capabilities from configs/capabilities/
    registry = CapabilityRegistry()
    cap_dir = Path("configs/capabilities")
    if cap_dir.exists():
        registry.load_directory(str(cap_dir))

    # Create the tool broker
    broker = ToolBroker(registry=registry)

    # Create executor and workflow runner
    executor = DeterministicExecutor(
        tool_broker=broker,
        trace_store=...,   # Your trace store
        event_log=event_log,
    )

    runner = WorkflowRunner(
        context_assembler=ContextAssembler(
            document_store=doc_store,
            graph_store=graph_store,
        ),
        executor=executor,
    )

    # Run a personal workflow
    result = await runner.run("daily_checkin", intent="What should I focus on today?")
    print(f"Workflow result: {result.status}")


if __name__ == "__main__":
    asyncio.run(main())
```

### Key Principle

> **The public package provides the framework; your overlay provides the
> configuration, integrations, and operational scripts.**

Your overlay should **only** use the public API -- symbols exported in `__all__`
from each module. Never import internal or private modules (those prefixed with `_`).
This ensures your overlay survives package upgrades without breaking.

---

## Migration Checklist

If you have an existing monolithic repository that contains both the framework code
and your personal configuration, follow these steps to split it into the overlay
pattern.

### Pre-Migration

- [ ] Identify which files are framework (reusable) vs personal (configs, secrets, integrations)
- [ ] List all imports your personal code makes from the framework
- [ ] Verify those imports are available in the public `agentkernel` API

### Create Overlay

1. **Create a new project directory** for your overlay
2. **Create `pyproject.toml`** with `agentkernel` as a dependency:
   ```toml
   dependencies = ["agentkernel[vectors,api]>=0.1.0,<0.2.0"]
   ```
3. **Copy personal agent profiles** (`configs/agents/`) to your overlay
4. **Copy personal workflow configs** (`configs/workflows/`) to your overlay
5. **Copy personal capability definitions** (`configs/capabilities/`) to your overlay
6. **Copy personal integration code** to your overlay's `integrations/` directory
7. **Copy operational scripts** to your overlay's `scripts/` directory
8. **Copy `.env` and `.env.example`** to your overlay root

### Validate

9. **Update imports** -- ensure all imports use the public API only:
    ```python
    # Good: public API
    from agent_kernel.core.schemas import Plan, ContextPacket
    from agent_kernel.tools.broker import ToolBroker

    # Bad: internal module (may break on upgrade)
    from agent_kernel.tools._internal import _validate_args
    ```
10. **Create a smoke test** that imports every symbol your overlay depends on:
    ```python
    def test_public_api_imports():
        """Verify all symbols we use are in the public API."""
        from agent_kernel.core.schemas import (
            Plan,
            ContextPacket,
            DecisionTrace,
            ActionRequest,
            ToolCallRecord,
        )
        from agent_kernel.tools.broker import ToolBroker
        from agent_kernel.workflows.runner import WorkflowRunner
        from agent_kernel.executor.executor import DeterministicExecutor
        # All imports succeed -- public API is stable
    ```
11. **Install in a fresh virtualenv** and verify:
    ```bash
    python -m venv .venv
    source .venv/bin/activate
    pip install agentkernel[vectors,api]
    pip install -e .
    pytest tests/ -x
    ```
12. **Run your workflows** against the installed package and confirm they pass

### Post-Migration

- [ ] Add `data/` to `.gitignore` (traces, vectors, graph databases are local)
- [ ] Verify `.env` is in `.gitignore`
- [ ] Remove framework source code from your private repo (it now comes from PyPI)
- [ ] Set up a CI job in your private repo that runs the smoke test on new `agentkernel` releases

---

## Best Practices

### Version Pinning

Pin to a compatible version range to avoid breaking changes:

```toml
dependencies = ["agentkernel>=0.1.0,<0.2.0"]
```

When a new minor version is released (e.g., `0.1.1`), you get it automatically.
When a new major version is released (e.g., `0.2.0`), you explicitly opt in after
reviewing the changelog.

### Smoke Tests

Add a smoke test in your overlay that imports all symbols you depend on. Run this
test in CI to catch public API breakage early:

```python
# tests/test_api_surface.py

def test_core_schemas_importable():
    from agent_kernel.core.schemas import Plan, ContextPacket, DecisionTrace

def test_tools_importable():
    from agent_kernel.tools.broker import ToolBroker
    from agent_kernel.tools.registry import CapabilityRegistry

def test_workflows_importable():
    from agent_kernel.workflows.runner import WorkflowRunner
```

### Keep Your Overlay Focused

Your overlay should contain **only**:

- Configuration files (YAML agent profiles, workflow specs, capability definitions)
- Integration code (custom adapters for your specific services)
- Operational scripts (startup, cron jobs, deployment)
- Secrets and environment files

It should **not** contain:

- Copies of framework code
- Modified versions of framework modules
- Monkey-patches or internal imports

### Extend Through the Public API

The `agentkernel` package provides extension points for customization:

| Extension Point | How to Use |
|---|---|
| Custom agent engines | Implement the `AgentEngine` protocol |
| Custom tool adapters | Subclass `ToolAdapter` and register with the broker |
| Custom memory stores | Implement store interfaces (DocumentStore, VectorStore, etc.) |
| Custom task sync | Subclass `TaskSyncAdapter` |
| Custom calendar sync | Subclass `CalendarAdapter` |
| Plugin entry points | Register under the `agentkernel.*` namespace in your `pyproject.toml` |

Example plugin registration:

```toml
[project.entry-points."agentkernel.engines"]
my_engine = "my_agent_system.integrations:MyCustomEngine"

[project.entry-points."agentkernel.adapters"]
my_task_sync = "my_agent_system.integrations:MyTaskAdapter"
```

### Data Stays Local

Keep `data/` in your `.gitignore`. Traces, vectors, graph databases, and event logs
are local runtime data -- they should never be committed to version control:

```gitignore
# Runtime data
data/
*.db
*.db-journal
*.db-wal
```

---

## Example: Personal Assistant Overlay

Here is a complete example of a personal overlay that sets up a daily review workflow:

```
my-assistant/
  pyproject.toml
  .env
  configs/
    agents/
      daily_reviewer.yaml     # Agent profile with thinking config
    workflows/
      morning_checkin.yaml    # Runs at 9 AM, reviews tasks
  integrations/
    todoist_adapter.py        # Syncs tasks from Todoist
  scripts/
    start.sh                  # Starts the kernel API server
  tests/
    test_smoke.py             # Import smoke tests
```

**`configs/agents/daily_reviewer.yaml`:**

```yaml
agent_profile_id: daily_reviewer
name: Daily Reviewer
engine: custom
llm_config:
  provider: openai
  model: gpt-4o
  temperature: 0.3
  reasoning_effort: medium
allowed_capabilities:
  - tasks.list@v1
  - tasks.prioritize@v1
  - notes.search@v1
context_policy:
  max_tokens: 4000
  max_tasks: 20
  must_cite: true
approval_policy:
  auto_approve_side_effects: [none, local]
```

**`configs/workflows/morning_checkin.yaml`:**

```yaml
workflow_id: morning_checkin
name: Morning Check-in
trigger:
  type: cron
  schedule: "0 9 * * 1-5"
agent_profile_id: daily_reviewer
write_back:
  create_summary_note: true
  update_graph: true
```

This overlay contains zero framework code -- it relies entirely on the public
`agentkernel` package for execution. When a new version of `agentkernel` is released,
you update the version pin and run your smoke tests.

---

*See also: [Architecture Guide](architecture.md) | [Framework Agnosticism](framework-agnosticism.md)*
