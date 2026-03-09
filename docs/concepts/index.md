# Core Concepts

Agent Kernel is built on a small set of design principles that govern how every component interacts:

1. **Schemas are the contract.** All data flows through typed Pydantic models. No unstructured data crosses component boundaries.
2. **Separation of reasoning vs. execution.** LLMs produce structured `Plan` objects; a deterministic executor validates and runs tools.
3. **Kernel owns memory and traces.** Agent frameworks are pluggable adapters for plan generation -- they never own data, call tools directly, or control the audit trail.

These principles ensure that you can swap agent frameworks, change LLM providers, or add new tool adapters without rewriting your system.

## Concept Pages

- [Schema Contracts](schemas.md) -- the typed data models that flow through every component
- [Tool Broker](tool-broker.md) -- the single gateway for all tool execution
- [Executor](executor.md) -- deterministic plan validation and execution
- [Context Assembler](context-assembler.md) -- how context is retrieved and assembled
- [Tracing](tracing.md) -- immutable audit trail for every decision
