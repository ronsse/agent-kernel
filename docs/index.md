# Agent Kernel

**Framework-agnostic foundation for building AI agent systems.**

Agent Kernel implements a strict separation between reasoning (LLM-driven planning) and execution (deterministic tool running). It provides typed schema contracts, pluggable components, and comprehensive tracing -- so you can build reliable, auditable AI agents without framework lock-in.

## Key Features

- **Schema Contracts** -- All data flows through typed Pydantic models (`ContextPacket`, `Plan`, `DecisionTrace`). No unstructured data between components.
- **Pluggable Engines** -- Swap agent frameworks (custom, LangGraph, Semantic Kernel) without rewriting business logic. Engines produce Plans; the kernel handles everything else.
- **Tool Governance** -- The Tool Broker is the single execution gateway. Capability allowlists, approval gates, rate limits, retry with circuit breaker, and full audit logging.
- **Comprehensive Tracing** -- Every decision produces an immutable `DecisionTrace` with context used, plan generated, tools called, and outcomes recorded.
- **Framework-Agnostic** -- The kernel owns memory, tools, and traces. Orchestration frameworks are optional adapters for control flow only.

## Quick Install

```bash
pip install agentkernel
```

Or with optional extras:

```bash
pip install agentkernel[vectors]   # LanceDB vector store
pip install agentkernel[api]       # FastAPI dashboard + REST API
```

## Architecture at a Glance

```
Intent --> Context Assembler --> ContextPacket
                                     |
                                Agent Engine --> Plan
                                     |
                                Executor (validates plan)
                                     |
                                Tool Broker (executes actions)
                                     |
                                DecisionTrace (logged)
```

## Next Steps

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Getting Started**

    ---

    Install Agent Kernel, configure your first agent, and run a workflow.

    [:octicons-arrow-right-24: Getting Started](getting-started/index.md)

-   :material-book-open-variant:{ .lg .middle } **Concepts**

    ---

    Understand schemas, engines, the executor, and the trust boundary model.

    [:octicons-arrow-right-24: Concepts](concepts/index.md)

-   :material-tools:{ .lg .middle } **Guides**

    ---

    Step-by-step guides for common tasks and integrations.

    [:octicons-arrow-right-24: Guides](guides/index.md)

-   :material-api:{ .lg .middle } **API Reference**

    ---

    Auto-generated reference for all public modules.

    [:octicons-arrow-right-24: API Reference](reference/)

</div>
