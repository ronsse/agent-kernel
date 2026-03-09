# Quickstart: Your First Agent in 5 Minutes

This guide takes you from zero to a running `DecisionTrace` with no API keys, no external services, and no configuration files.

## Step 1: Install

```bash
pip install agentkernel
```

That's it. The core package has no heavy dependencies.

## Step 2: Create a Stub Engine

An **Agent Engine** is anything that turns a `ContextPacket` into a `Plan`. For this quickstart, we'll create a stub that returns a fixed plan -- no LLM or API keys needed.

```python
from agent_kernel.core.schemas import (
    Plan,
    PlanValidation,
    RiskAssessment,
)


class StubEngine:
    """A mock engine that returns a fixed plan. No API keys needed."""

    engine_id = "stub"
    version = "1.0.0"

    async def propose(self, context_packet, agent_profile):
        return Plan(
            intent=context_packet.intent,
            summary="Stub plan for demonstration.",
            context_refs_used=[],
            actions=[],
            risk=RiskAssessment(level="low", reasons=["Demo plan"]),
            validation=PlanValidation(
                missing_info=[],
                assumptions=["Using stub engine"],
            ),
        )
```

In a real system, you would swap this for a `CustomEngine` backed by an LLM provider.

## Step 3: Set Up In-Memory Stores

Agent Kernel uses SQLite for all storage. For this quickstart, we use `:memory:` databases so nothing touches disk.

```python
import asyncio

from agent_kernel import (
    CapabilityRegistry,
    DeterministicExecutor,
    SQLiteDocumentStore,
    SQLiteEventLog,
    SQLiteGraphStore,
    ToolBroker,
)
from agent_kernel.core.schemas import (
    AgentProfile,
    ApprovalPolicy,
    ContextBudget,
    ContextPacket,
    ContextPolicy,
    ModelConfig,
    RetrievalLimits,
    RetrievalReport,
)
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink
```

## Step 4: Build a Context Packet and Run the Engine

A `ContextPacket` is the bounded input an agent receives. We'll create one manually:

```python
async def main():
    # Create in-memory stores
    doc_store = SQLiteDocumentStore(":memory:")
    graph_store = SQLiteGraphStore(":memory:")
    event_log = SQLiteEventLog(":memory:")
    trace_sink = SQLiteTraceSink(":memory:")

    # Initialize stores
    await doc_store.initialize()
    await graph_store.initialize()
    await event_log.initialize()
    trace_sink.initialize()

    # Create tool broker (no capabilities registered for this demo)
    registry = CapabilityRegistry()
    broker = ToolBroker(registry=registry)

    # Create executor
    executor = DeterministicExecutor(
        tool_broker=broker,
        trace_store=trace_sink,
        event_log=event_log,
    )

    # Build a context packet
    context_packet = ContextPacket(
        intent="What should I work on today?",
        budget=ContextBudget(
            max_tokens=4000,
            max_items=10,
            retrieval_limits=RetrievalLimits(),
        ),
        items=[],
        retrieval_report=RetrievalReport(
            queries_run=[],
            filters_applied=[],
            items_considered=0,
            items_selected=0,
            selection_strategy="manual",
        ),
    )

    # Create an agent profile
    profile = AgentProfile(
        agent_profile_id="quickstart_agent",
        name="Quickstart Agent",
        engine="stub",
        llm_config=ModelConfig(provider="stub", model="stub"),
        allowed_capabilities=[],
        context_policy=ContextPolicy(must_cite=False),
        approval_policy=ApprovalPolicy(),
        output_schema_version="1.0.0",
    )

    # Generate a plan using the stub engine
    engine = StubEngine()
    plan = await engine.propose(context_packet, profile)
    print(f"Plan summary: {plan.summary}")
```

## Step 5: Execute the Plan and Inspect the Trace

The `DeterministicExecutor` validates the plan, runs any actions through the Tool Broker, and produces an immutable `DecisionTrace`:

```python
    # Execute the plan
    trace = await executor.execute(
        plan=plan,
        context_packet=context_packet,
        agent_profile=profile,
    )

    # Inspect the trace
    print(f"Trace ID:  {trace.trace_id}")
    print(f"Outcome:   {trace.outcome.status.value}")
    print(f"Actions:   {len(trace.tool_calls)}")
    print(f"Timestamp: {trace.timestamp.isoformat()}")
```

## Complete Runnable Script

Here is the entire quickstart as a single copy-pasteable script:

```python
"""Agent Kernel Quickstart -- run with: python quickstart.py"""
import asyncio

from agent_kernel import (
    CapabilityRegistry,
    DeterministicExecutor,
    SQLiteDocumentStore,
    SQLiteEventLog,
    SQLiteGraphStore,
    ToolBroker,
)
from agent_kernel.core.schemas import (
    AgentProfile,
    ApprovalPolicy,
    ContextBudget,
    ContextPacket,
    ContextPolicy,
    ModelConfig,
    Plan,
    PlanValidation,
    RetrievalLimits,
    RetrievalReport,
    RiskAssessment,
)
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink


class StubEngine:
    """A mock engine that returns a fixed plan. No API keys needed."""

    engine_id = "stub"
    version = "1.0.0"

    async def propose(self, context_packet, agent_profile):
        return Plan(
            intent=context_packet.intent,
            summary="Stub plan for demonstration.",
            context_refs_used=[],
            actions=[],
            risk=RiskAssessment(level="low", reasons=["Demo plan"]),
            validation=PlanValidation(
                missing_info=[],
                assumptions=["Using stub engine"],
            ),
        )


async def main():
    # Create in-memory stores
    doc_store = SQLiteDocumentStore(":memory:")
    graph_store = SQLiteGraphStore(":memory:")
    event_log = SQLiteEventLog(":memory:")
    trace_sink = SQLiteTraceSink(":memory:")

    await doc_store.initialize()
    await graph_store.initialize()
    await event_log.initialize()
    trace_sink.initialize()

    # Tool broker with no registered capabilities
    registry = CapabilityRegistry()
    broker = ToolBroker(registry=registry)

    # Deterministic executor
    executor = DeterministicExecutor(
        tool_broker=broker,
        trace_store=trace_sink,
        event_log=event_log,
    )

    # Context packet (the bounded input)
    context_packet = ContextPacket(
        intent="What should I work on today?",
        budget=ContextBudget(
            max_tokens=4000,
            max_items=10,
            retrieval_limits=RetrievalLimits(),
        ),
        items=[],
        retrieval_report=RetrievalReport(
            queries_run=[],
            filters_applied=[],
            items_considered=0,
            items_selected=0,
            selection_strategy="manual",
        ),
    )

    # Agent profile
    profile = AgentProfile(
        agent_profile_id="quickstart_agent",
        name="Quickstart Agent",
        engine="stub",
        llm_config=ModelConfig(provider="stub", model="stub"),
        allowed_capabilities=[],
        context_policy=ContextPolicy(must_cite=False),
        approval_policy=ApprovalPolicy(),
        output_schema_version="1.0.0",
    )

    # Generate and execute plan
    engine = StubEngine()
    plan = await engine.propose(context_packet, profile)
    trace = await executor.execute(
        plan=plan,
        context_packet=context_packet,
        agent_profile=profile,
    )

    # Print results
    print(f"Plan summary: {plan.summary}")
    print(f"Trace ID:     {trace.trace_id}")
    print(f"Outcome:      {trace.outcome.status.value}")
    print(f"Actions run:  {len(trace.tool_calls)}")
    print(f"Timestamp:    {trace.timestamp.isoformat()}")


if __name__ == "__main__":
    asyncio.run(main())
```

Run it:

```bash
python quickstart.py
```

Expected output:

```
Plan summary: Stub plan for demonstration.
Trace ID:     01JXYZ...
Outcome:      completed
Actions run:  0
Timestamp:    2026-01-15T10:30:00+00:00
```

## What Just Happened?

1. **StubEngine** produced a `Plan` from a `ContextPacket` -- this is the reasoning phase
2. **DeterministicExecutor** validated the plan schema and executed it -- this is the execution phase
3. An immutable **DecisionTrace** was created with the complete audit trail

In a real system, you would:

- Replace `StubEngine` with a `CustomEngine` backed by an LLM
- Register capabilities in the `CapabilityRegistry` for tool execution
- Use the `ContextAssembler` to build context packets from memory stores
- Wire up the `WorkflowRunner` for scheduled, multi-step workflows

## Next Steps

- [Schema Contracts](../concepts/schemas.md) -- understand the data models that flow through every component
- [Tool Broker](../concepts/tool-broker.md) -- learn how tool execution is governed
- [Architecture Guide](../guides/architecture.md) -- see how all the pieces fit together
