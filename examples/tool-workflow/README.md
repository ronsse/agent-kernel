# Tool-Heavy Workflow Example

Demonstrates multiple tools with approval gates, retry configuration,
and circuit breaker setup.

## What it demonstrates

- Registering 3+ tool capabilities with different side effect levels
- Approval policy enforcement (auto-approve reads/local, require approval for external)
- Retry configuration for transient failures
- Circuit breaker setup for failing tools
- The full approval flow: pending -> grant -> re-execute
- Inspecting `ToolCallRecord` details (timing, status, side effects)

## Run

```bash
python main.py
```

## Expected output

```
=== Tool-Heavy Workflow Example ===

Registered: data.fetch@v1, data.transform@v1, notification.send@v1
Retry config: max_retries=2, base_delay=100ms

--- First execution (notification requires approval) ---
Outcome: needs_approval
  data.fetch@v1 -> success
  data.transform@v1 -> success
  notification.send@v1 -> skipped (needs approval)

--- Granting approval ---
Approved action: <action_id>

--- Re-executing with approval ---
Outcome: completed
  data.fetch@v1 -> success
  data.transform@v1 -> success
  notification.send@v1 -> success

--- Circuit breaker status ---
All circuits: closed (healthy)
```
