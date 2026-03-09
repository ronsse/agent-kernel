# Multi-Agent Debate Example

Demonstrates multiple engines proposing competing plans, with a judge
selecting the best one based on risk and action count.

## What it demonstrates

- Implementing two `AgentEngine` instances with different strategies
- Registering engines in the `EngineRegistry`
- Comparing plans by risk level and action count
- A simple "critic/judge" pattern for plan selection
- Executing only the selected plan

## Run

```bash
python main.py
```

## Expected output

```
=== Multi-Agent Debate Example ===

--- Optimistic Engine ---
Plan: Move fast: batch process 3 data sources and deploy immediately
Risk: medium | Actions: 3

--- Conservative Engine ---
Plan: Process data sources one at a time with validation between each step
Risk: low | Actions: 4

--- Judge Decision ---
Selected: conservative
Reason: Lower risk (low vs medium)

--- Executing selected plan ---
Trace ID: <ulid>
Outcome:  completed
Tool calls: 4 total, 4 succeeded
```
