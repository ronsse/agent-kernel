# Tool-Heavy Workflow

Demonstrates multiple tools with approval gates, retry configuration, and
circuit breaker setup. This is the most feature-rich example.

## What It Demonstrates

- Registering 3 tool capabilities with different side effect levels
- Approval policy enforcement (auto-approve reads/local, require approval for external)
- Retry configuration for transient failures
- Circuit breaker setup for failing tools
- The full approval flow: pending, grant, re-execute
- Inspecting `ToolCallRecord` details (timing, status, side effects)

## Key Concepts

| Concept | Description |
|---------|-------------|
| `CapabilityDef` | Tool definition with side effect level and approval defaults |
| `SideEffect` | none, read, local (write), external (write) |
| `ApprovalPolicy` | Which side effects auto-approve, which require human approval |
| `RetryConfig` | Max retries, base delay, exponential backoff |
| `ToolBroker` | Executes tools with retry, circuit breaker, and rate limiting |

## How It Works

### 1. Three Capabilities with Different Side Effects

```python
# Read-only: auto-approved
CapabilityDef(
    capability_name="data.fetch@v1",
    side_effect_level=SideEffect.NONE,
)

# Local write: auto-approved
CapabilityDef(
    capability_name="data.transform@v1",
    side_effect_level=SideEffect.LOCAL_WRITE,
)

# External write: requires approval
CapabilityDef(
    capability_name="notification.send@v1",
    side_effect_level=SideEffect.EXTERNAL_WRITE,
    requires_approval_default=True,
)
```

### 2. Approval Policy

The agent profile controls which side effects are auto-approved:

```python
ApprovalPolicy(
    auto_approve_side_effects=[SideEffect.NONE, SideEffect.READ, SideEffect.LOCAL_WRITE],
    require_approval_for=["notification.send@v1"],
)
```

With this policy:

- `data.fetch@v1` (none) --- auto-approved
- `data.transform@v1` (local) --- auto-approved
- `notification.send@v1` (external) --- **requires approval**

### 3. Retry and Circuit Breaker

```python
retry_config = RetryConfig(max_retries=2, base_delay_ms=100, max_delay_ms=500)

broker = ToolBroker(
    registry=registry,
    retry_config=retry_config,        # Automatic retry with backoff
    enable_circuit_breaker=True,       # Prevent cascading failures
)
```

### 4. The Approval Flow

**First execution** --- notification is skipped (needs approval):

```python
trace = await executor.execute(plan=plan, ...)
# Outcome: needs_approval
# data.fetch@v1 -> success
# data.transform@v1 -> success
# notification.send@v1 -> skipped
```

**Grant approval:**

```python
pending_list = approval_gate.list_pending()
for pending in pending_list:
    approval_gate.approve(pending.approval_id, approved_by="admin")
    approval_tokens[pending.action_id] = pending.token
```

**Re-execute with tokens** --- all actions succeed:

```python
trace2 = await executor.execute(plan=plan, approval_tokens=approval_tokens, ...)
# Outcome: completed
# All 3 actions -> success
```

### 5. Circuit Breaker Status

After execution, you can inspect circuit breaker health:

```python
states = broker.get_circuit_breaker_states()
# {'data.fetch@v1': 'closed', 'data.transform@v1': 'closed', ...}
```

Circuit breaker states:

- **closed** --- healthy, requests flow normally
- **open** --- too many failures, requests are blocked
- **half_open** --- testing if the service has recovered

## Expected Output

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
Approved action: 01KK...

--- Re-executing with approval ---
Outcome: completed
  data.fetch@v1 -> success
  data.transform@v1 -> success
  notification.send@v1 -> success

--- Circuit breaker status ---
  data.fetch@v1: closed
  data.transform@v1: closed
  notification.send@v1: closed
```

## What to Explore Next

- [Minimal Agent](minimal-agent.md) --- start with the simplest example
- [Personal Assistant](personal-assistant.md) --- workflows with memory stores
- [Multi-Agent Debate](multi-agent-debate.md) --- competing engines and plan selection
