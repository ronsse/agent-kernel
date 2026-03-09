# Agent Kernel

**A framework-agnostic foundation for building reliable, auditable AI agent systems.**

[![CI](https://github.com/ORG/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/ORG/REPO/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/agentkernel)](https://pypi.org/project/agentkernel/)
[![Python versions](https://img.shields.io/pypi/pyversions/agentkernel)](https://pypi.org/project/agentkernel/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Overview

Agent Kernel separates **reasoning** (LLM-driven planning) from **execution** (deterministic tool running), giving you strict schema contracts, comprehensive trace logging, and pluggable components at every layer.

### Core Principles

1. **Schemas are the contract.** Everything meaningful is a typed envelope.
2. **Separation of reasoning vs. execution.** LLM produces structured plan; deterministic executor runs tools.
3. **Kernel owns memory + traces.** Agent frameworks must *not* be the source of truth.
4. **Adapters everywhere.** Swap agent framework, vector store, graph store, LLM provider, or tool transport.
5. **Try fast, escalate on evidence.** Don't pre-classify complexity; use quality gates.

---

## Why Agent Kernel?

| | Agent Kernel | LangChain/LangGraph | CrewAI | Pydantic AI |
|---|---|---|---|---|
| **Framework lock-in** | Zero -- swap engines freely | Moderate -- LangChain ecosystem | High -- CrewAI patterns | Low -- but limited scope |
| **Trace/audit** | Built-in immutable traces | Via LangSmith (external) | Limited | None built-in |
| **Tool governance** | Approval gates, rate limits, circuit breakers | Basic | Basic | None |
| **Reasoning control** | 4-tier adaptive escalation | Manual | Manual | None |
| **Memory ownership** | You own all data (local SQLite) | Framework-managed | Framework-managed | None |
| **Schema contracts** | Strict Pydantic throughout | Loose | Loose | Strict |

**Key differentiators:**

- **Framework-agnostic** -- use LangGraph, custom engines, or any LLM provider as a pluggable adapter
- **Immutable audit trails** -- every decision produces a `DecisionTrace` with full provenance
- **Tool governance** -- approval gates, rate limits, and circuit breakers (not just tool calling)
- **Adaptive reasoning** -- try fast, validate with quality gates, escalate on evidence
- **Local-first** -- own your data in local SQLite stores, no external services required

---

## Features

### Core

- **Schema contracts** -- strict Pydantic models for all data flows (`ContextPacket`, `Plan`, `DecisionTrace`)
- **Separation of reasoning and execution** -- LLM proposes plans; deterministic executor validates and runs them
- **Immutable traces** -- complete audit trail of every decision, tool call, and outcome

### Engines

- **Pluggable agent engines** -- swap LangGraph, Semantic Kernel, or custom implementations
- **Adaptive reasoning** -- 4-tier thinking policy with automatic escalation (routing -> standard -> deep -> deep+critic)
- **Quality gates** -- deterministic plan validation before execution
- **Critic engine** -- optional second-opinion pass for high-reliability tasks

### Tools

- **Tool Broker** -- single point of tool execution with input validation and logging
- **Approval gates** -- human-in-the-loop for sensitive operations
- **Retry + circuit breaker** -- exponential backoff and cascading failure prevention
- **Rate limiting** -- per-capability rate control

### Memory

- **Document store** -- full-text storage with content hashing
- **Vector index** -- semantic search via LanceDB
- **Knowledge graph** -- typed nodes and edges with 30+ node types
- **Event log** -- append-only system event stream

### Operations

- **Workflow runner** -- state machine for multi-step agent workflows
- **Scheduler** -- cron, file watch, event, and workflow triggers
- **CLI** -- comprehensive Typer-based command-line interface
- **REST API** -- FastAPI server with full kernel access

---

## Quick Start

### Installation

```bash
pip install agentkernel

# Or with optional extras
pip install agentkernel[vectors,api]
```

### Minimal Example

```python
import asyncio
from agent_kernel import (
    AgentProfile, CapabilityRegistry, ContextPacket,
    DeterministicExecutor, Plan, ToolBroker,
)
from agent_kernel.core.schemas import (
    ApprovalPolicy, ContextPolicy, ModelConfig, RiskAssessment,
)
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink

# In-memory stores (no API keys needed)
trace_store = SQLiteTraceSink(":memory:")
broker = ToolBroker(registry=CapabilityRegistry(), enable_circuit_breaker=False)
executor = DeterministicExecutor(tool_broker=broker, trace_store=trace_store)

profile = AgentProfile(
    agent_profile_id="demo", name="Demo Agent",
    llm_config=ModelConfig(provider="stub", model="stub"),
    context_policy=ContextPolicy(must_cite=False),
    approval_policy=ApprovalPolicy(),
)

async def main():
    context = ContextPacket(intent="What should I work on today?")
    plan = Plan(
        intent=context.intent,
        summary="Focus on the top 3 priority tasks",
        risk=RiskAssessment(level="low", reasons=["No actions"]),
    )
    trace = await executor.execute(
        plan=plan, context_packet=context,
        agent_profile=profile, engine_id="stub",
    )
    print(f"Trace: {trace.trace_id} | Outcome: {trace.outcome.status.value}")

asyncio.run(main())
```

See the [examples directory](examples/) and [documentation](https://ORG.github.io/REPO/) for more.

---

## Architecture

```
                           AGENT KERNEL

  +--------------+    +--------------+    +----------------+
  |   Context    |--->|   Agent      |--->| Deterministic  |
  |  Assembler   |    |   Engine     |    |   Executor     |
  +--------------+    +--------------+    +----------------+
         |                  |                   |
         |           +------+------+            |
         |           v             v            |
         |    +----------+ +-----------+        |
         |    | Thinking | |  Quality  |        |
         |    | Policy   | |   Gates   |        |
         |    +----------+ +-----------+        |
         |           |             |            |
         |           +------+------+            |
         |                  v                   |
         |         +--------------+             |
         |         | Escalation   |             |
         |         |  Manager     |             |
         |         +--------------+             |
         v                                      v
  +--------------+    +--------------+    +--------------+
  |   Memory     |    |    Plan      |    |    Tool      |
  |  Subsystem   |    |   Schema     |    |   Broker     |
  +--------------+    +--------------+    +--------------+
         |                                      |
         +------------------+-------------------+
                            v
                    +--------------+
                    |   Trace      |
                    |   Store      |
                    +--------------+
```

### Key Components

| Component | Description |
|-----------|-------------|
| **Context Assembler** | Deterministic retrieval from memory stores into `ContextPacket` |
| **Agent Engine** | Pluggable: LLM produces `Plan` from `ContextPacket` |
| **Thinking Policy** | Controls reasoning budget per task (tiers 0-3) |
| **Quality Gates** | Deterministic plan validation |
| **Escalation Manager** | Attempt, gate, escalate flow |
| **Deterministic Executor** | Validates plan, gates approvals, executes via Tool Broker |
| **Tool Broker** | Single point of tool execution with validation + logging |
| **Memory Subsystem** | Document store, vector index (LanceDB), graph store, event log |
| **Trace Store** | Immutable audit trail of all decisions and executions |
| **Workflow Runner** | State machine for multi-step agent workflows |
| **Context Graph** | Trace decomposition, knowledge graph, event clock |
| **LLM Cache** | Tier-aware semantic response caching |
| **Feedback Loops** | Success rate routing, cost anomaly detection, adaptive timeouts |

---

## Project Structure

```
agent-kernel/
+-- src/agent_kernel/             # Main kernel source code
|   +-- core/
|   |   +-- schemas/              # Pydantic models (THE contract)
|   |   +-- config.py             # Pydantic Settings
|   |   +-- ids.py                # ULID generation
|   |   +-- errors.py             # Custom exceptions
|   +-- memory/                   # Document, vector, graph, event stores
|   +-- tools/                    # Tool broker, registry, adapters
|   +-- context/                  # Context assembler
|   +-- context_graph/            # Trace decomposition, knowledge graph
|   +-- engine/                   # Agent engines, thinking policy, escalation
|   +-- executor/                 # Deterministic executor, approval gates
|   +-- workflows/                # Workflow runner, specs
|   +-- scheduler/                # Cron scheduling, file watchers
|   +-- services/                 # LLM, embedding, vault indexer, enrichment
|   +-- integrations/             # Calendar, task sync adapters
|   +-- tracing/                  # Trace store and sinks
|   +-- api/                      # FastAPI REST server
|   +-- cli/                      # Typer CLI
+-- configs/
|   +-- capabilities/             # Capability definition YAMLs
+-- tests/                        # Unit and integration tests
+-- examples/                     # Example applications
+-- docs/                         # Documentation source
+-- pyproject.toml                # Project config
```

---

## Testing

```bash
# Run all tests
make test

# Run unit tests only
make test-unit

# Run with coverage
make test-cov

# Run specific test file
pytest tests/unit/engine/test_escalation.py -v
```

---

## Development

```bash
# Setup
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env

# Quality checks
make lint          # Lint with ruff
make format        # Format code
make typecheck     # Type checking with mypy
make test          # Run all tests

# Pre-commit hooks run automatically on commit
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for full development guidelines.

---

## Documentation

Full documentation is available at [https://ORG.github.io/REPO/](https://ORG.github.io/REPO/), including:

- Architecture and design guides
- API reference
- Example walkthroughs
- Integration patterns

---

## License

MIT License - see [LICENSE](LICENSE) for details.
