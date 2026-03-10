# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Agent Kernel is a framework-agnostic foundation for AI agent systems. It separates reasoning (LLM planning) from execution (deterministic tool running), with strict schema contracts, pluggable storage backends, and immutable audit trails. Python 3.11+, local-first (SQLite default, PostgreSQL optional).

## Commands

```bash
make install-dev      # Install dev dependencies (uses uv)
make lint             # Ruff linting
make format           # Ruff format + autofix
make typecheck        # mypy src/
make test             # All tests
make test-unit        # Unit tests only
make test-cov         # Tests with coverage report

# Single test file or specific test:
pytest tests/unit/engine/test_thinking_policy.py -v
pytest tests/unit/engine/test_thinking_policy.py::test_name -v
```

## Architecture

### Core Flow

```
ContextAssembler → AgentEngine.propose() → Plan → DeterministicExecutor → ToolBroker → TraceStore
```

1. **Context Assembler** builds a `ContextPacket` from memory stores
2. **AgentEngine** (LLM) produces a `Plan` from context + `AgentProfile`
3. **Executor** validates the plan, gates approvals, executes actions
4. **Tool Broker** is the ONLY component that executes tools (via capability adapters)
5. **Trace Store** writes immutable audit trails for every decision

### Key Subsystems (`src/agent_kernel/`)

- **`core/schemas/`** — Pydantic models that define ALL data contracts. Schema version 1.1.3 with migration support. Everything flows through these models.
- **`core/config.py`** — Pydantic Settings, environment-driven. Database, vector store, LLM provider, tool broker settings.
- **`core/errors.py`** — Exception hierarchy rooted at `AgentKernelError`. Use these, don't invent new base exceptions.
- **`memory/`** — Pluggable stores: document (full-text), graph (nodes/edges), vector (embeddings), event log (append-only), entity, experience. Each has SQLite default + optional PostgreSQL backend.
- **`tools/`** — `CapabilityRegistry` loads YAML from `configs/capabilities/`. `ToolBroker` validates, executes, logs. Includes circuit breaker, rate limiter, adaptive timeout, idempotency.
- **`engine/`** — `AgentEngine` protocol: `async def propose(context_packet, agent_profile) -> Plan`. Pluggable via entry points. Thinking policy controller with 4-tier escalation (routing → standard → deep → deep+critic).
- **`executor/`** — `DeterministicExecutor` + `ApprovalGate` + `QualityGateRunner`. Plans are validated before execution.
- **`workflows/`** — YAML-defined workflow specs compiled to runners. `WorkflowRunner`: `async def run(workflow_id, intent, **kwargs) -> WorkflowResult`.
- **`tracing/`** — Immutable trace sinks (SQLite, JSONL). Canonical audit trail.

### Non-Negotiable Rules

1. **Schema-first**: All data crosses boundaries as Pydantic models. New data = new schema first.
2. **Tool Broker is sacred**: No tool execution outside the broker. Ever.
3. **Engines don't call tools**: They propose plans. The executor calls tools via the broker.
4. **Traces are portable**: Never depend on framework-specific logging.
5. **Framework agnosticism**: Orchestration frameworks (LangGraph, etc.) are optional adapters. They consume kernel schemas, call the Tool Broker, and never bypass the executor.

### Thinking Policy (Adaptive Reasoning)

Default to standard tier. Escalate on evidence (quality gate failures, low confidence, high risk), not predictions. See `configs/thinking_tiers.yaml` and `docs/design/11-thinking-policy.md`.

## Conventions

- **Line length**: 88 chars (ruff)
- **Linting**: Strict ruff ruleset (E, F, B, W, I, N, UP, ANN, S, C4, DTZ, etc.). Tests exempt from ANN/S101/PLR2004.
- **Async**: Use `async/await` for I/O operations. `pytest-asyncio` with `asyncio_mode = "auto"`.
- **IDs**: ULID-based (`core/ids.py`). Generate once, never regenerate.
- **Config**: Environment variables via pydantic-settings, never hardcoded.
- **Capabilities**: Defined as YAML in `configs/capabilities/`, implemented as adapters in `tools/adapters/`.
- **Entry points**: Engines, stores are pluggable via `pyproject.toml` entry points.

## Adding New Components

- **New schema**: Define in `core/schemas/`, export in `__init__.py`, add tests in `tests/unit/core/schemas/`.
- **New capability**: YAML in `configs/capabilities/`, adapter in `tools/adapters/`, register in capability registry.
- **New engine**: Implement `AgentEngine` protocol (`propose(context_packet, agent_profile) -> Plan`). Must NOT call tools directly.
- **New store backend**: Implement the store protocol, register via entry point in `pyproject.toml`.

## Design Docs

Detailed specs live in `docs/design/` (00-overview through 25-cli-first-evaluation). Read the relevant doc before modifying a subsystem.
