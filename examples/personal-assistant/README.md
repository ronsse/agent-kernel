# Personal Assistant Example

Demonstrates a multi-step workflow with memory stores and approval gates.

## What it demonstrates

- Setting up the full memory stack (document store, graph store, event log)
- Registering tool capabilities with stub handlers
- Running a workflow via `DeterministicExecutor`
- Multi-action plans with read-only and write side effects
- Approval flow: pending approval created, then resolved

## Run

```bash
python main.py
```

## Expected output

```
=== Personal Assistant Example ===

Registered capabilities: tasks.list@v1, summary.create@v1
Plan has 2 actions:
  1. tasks.list@v1 (side_effect=none)
  2. summary.create@v1 (side_effect=local, idempotency_key=summary_001)

--- Executing plan ---
Trace ID: <ulid>
Outcome:  completed
Tool calls:
  tasks.list@v1 -> success (Xms)
  summary.create@v1 -> success (Xms)

--- Approval demo ---
Created pending approval: <id>
Approved! Token: <token>
```
