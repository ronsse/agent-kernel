# Examples

Agent Kernel ships with four standalone example applications. Each demonstrates a
different use case at increasing complexity. **All examples run without API keys**
--- they use stub engines and in-memory SQLite stores.

## Quick Start

```bash
# Install the package
pip install agentkernel

# Run any example
python examples/minimal-agent/main.py
```

## Example Overview

| Example | Complexity | What It Demonstrates |
|---------|-----------|---------------------|
| [Minimal Agent](minimal-agent.md) | Beginner | Core data flow: context, plan, execute, trace |
| [Personal Assistant](personal-assistant.md) | Intermediate | Workflow with memory stores, multi-step execution, approval demo |
| [Multi-Agent Debate](multi-agent-debate.md) | Intermediate | Two engines propose competing plans, judge selects best |
| [Tool Workflow](tool-workflow.md) | Advanced | Approval gates, retry config, circuit breaker |

## Key Patterns

Every example follows the same architectural pattern:

1. **Stub Engine** returns a hardcoded `Plan` (no LLM calls)
2. **CapabilityRegistry** + **ToolBroker** manage tool execution
3. **DeterministicExecutor** validates the plan and runs tools
4. **DecisionTrace** captures the full audit trail

This mirrors how a production system works --- just swap the stub engine for a
real `CustomEngine` backed by an LLM provider.

## Running the Examples

Each example is a standalone directory with its own `pyproject.toml`:

```
examples/
  minimal-agent/
    main.py
    pyproject.toml
    README.md
  personal-assistant/
    main.py
    pyproject.toml
    README.md
    configs/
      workflow.yaml
      agent_profile.yaml
  multi-agent-debate/
    main.py
    pyproject.toml
    README.md
  tool-workflow/
    main.py
    pyproject.toml
    README.md
```

All examples use `:memory:` for SQLite stores and require no network access.
