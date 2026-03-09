# Minimal Agent Example

Demonstrates the core Agent Kernel data flow:

**Context -> Plan -> Execute -> Trace**

This is the simplest possible agent setup. A stub engine returns a hardcoded
plan, the executor validates and runs it, and a complete `DecisionTrace` is
produced.

## What it demonstrates

- Creating a `ContextPacket` with a simple intent
- Implementing a stub `AgentEngine` that returns a `Plan`
- Executing the plan via `DeterministicExecutor`
- Inspecting the resulting `DecisionTrace`

## Run

```bash
python main.py
```

## Expected output

```
=== Minimal Agent Example ===
Stub engine proposed plan: Minimal stub plan for: What should I work on today?
Trace ID: <ulid>
Outcome:  completed
Summary:  No actions to execute
Plan:     Minimal stub plan for: What should I work on today?
```
