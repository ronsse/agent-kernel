# Minimal Agent

The simplest possible Agent Kernel setup. Demonstrates the core data flow:

**Context -> Plan -> Execute -> Trace**

## What It Demonstrates

- Creating a `ContextPacket` with a simple intent
- Implementing a stub `AgentEngine` that returns a `Plan`
- Executing the plan via `DeterministicExecutor`
- Inspecting the resulting `DecisionTrace`

## Key Concepts

| Concept | Description |
|---------|-------------|
| `ContextPacket` | Bounded input to the agent containing intent and context items |
| `Plan` | Structured output from an engine with actions, risk assessment, and citations |
| `DeterministicExecutor` | Validates plans and executes actions via the Tool Broker |
| `DecisionTrace` | Complete audit record of the execution |

## How It Works

### 1. Create a Stub Engine

The engine implements the `AgentEngine` protocol. In production you would use
`CustomEngine` backed by an LLM, but for this example a stub returns a hardcoded
plan:

```python
class StubEngine:
    @property
    def engine_id(self) -> str:
        return "stub"

    @property
    def version(self) -> str:
        return "1.0.0"

    async def propose(self, context_packet, agent_profile, thinking_policy=None):
        return Plan(
            intent=context_packet.intent,
            summary=f"Minimal stub plan for: {context_packet.intent}",
            risk=RiskAssessment(level="low", reasons=["No actions"]),
        )
```

### 2. Set Up Infrastructure

```python
trace_store = SQLiteTraceSink(":memory:")
registry = CapabilityRegistry()
broker = ToolBroker(registry=registry, enable_circuit_breaker=False)
executor = DeterministicExecutor(tool_broker=broker, trace_store=trace_store)
```

### 3. Build Context and Execute

```python
context = ContextPacket(intent="What should I work on today?")
plan = await engine.propose(context, profile)
trace = await executor.execute(
    plan=plan,
    context_packet=context,
    agent_profile=profile,
    engine_id=engine.engine_id,
)
```

### 4. Inspect the Trace

```python
print(f"Trace ID: {trace.trace_id}")
print(f"Outcome:  {trace.outcome.status.value}")
print(f"Summary:  {trace.outcome.summary}")
```

## Expected Output

```
=== Minimal Agent Example ===
Stub engine proposed plan: Minimal stub plan for: What should I work on today?
Trace ID: 01KK...
Outcome:  completed
Summary:  No actions to execute
Plan:     Minimal stub plan for: What should I work on today?
```

## What to Explore Next

- [Personal Assistant](personal-assistant.md) --- add tool capabilities and multi-step execution
- [Multi-Agent Debate](multi-agent-debate.md) --- compare plans from multiple engines
- [Tool Workflow](tool-workflow.md) --- approval gates and retry/circuit breaker patterns
