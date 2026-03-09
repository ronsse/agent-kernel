# Tool Broker

The Tool Broker is the **single point of tool execution** in Agent Kernel. No agent, engine, or workflow may call tools directly. Every tool call goes through the broker, which validates inputs, enforces policies, gates approvals, logs the call, and handles retries.

## Why a Single Gateway?

Without centralized tool governance, agents can:

- Call tools they shouldn't have access to
- Skip input validation
- Bypass approval requirements
- Execute without audit logging
- Cause cascading failures without circuit breaking

The Tool Broker prevents all of these by design.

## Architecture

```
Agent Engine --> Plan (with ActionRequests)
                  |
                  v
            Deterministic Executor
                  |
                  v
            Tool Broker
              1. Validate input against schema
              2. Check capability allowlist
              3. Gate approval policy
              4. Execute via adapter
              5. Log ToolCallRecord
                  |
                  v
            Tool Adapters
            (Local | HTTP | Subprocess)
```

## Capability Registry

Every tool must be registered as a **capability** with a schema definition:

```python
from agent_kernel import CapabilityRegistry
from agent_kernel.core.schemas import CapabilityDef, SideEffect

registry = CapabilityRegistry()

# Register a capability
capability = CapabilityDef(
    capability_name="documents.create@v1",
    description="Create a new document",
    input_schema={
        "type": "object",
        "required": ["title", "content"],
        "properties": {
            "title": {"type": "string", "maxLength": 200},
            "content": {"type": "string"},
        },
    },
    output_schema={
        "type": "object",
        "properties": {
            "doc_id": {"type": "string"},
        },
    },
    side_effect_level=SideEffect.LOCAL_WRITE,
    requires_approval_default=False,
    timeout_ms=5000,
)

registry.register(capability)
```

### Capability Fields

| Field | Description |
|-------|-------------|
| `capability_name` | Versioned identifier (e.g., `tasks.create@v1`) |
| `input_schema` | JSON Schema for argument validation |
| `output_schema` | JSON Schema for return value validation |
| `side_effect_level` | `none` (read-only), `local` (local write), `external` (API call) |
| `requires_approval_default` | Whether approval is needed by default |
| `timeout_ms` | Execution timeout |
| `rate_limit` | Optional rate limiting configuration |
| `redaction_policy` | Fields to redact in trace logs |

## Side Effects and Approval

Actions are classified by their side effect level:

| Level | Description | Default Approval |
|-------|-------------|-----------------|
| `none` | Read-only operations | Auto-approved |
| `local` | Local file or database changes | Configurable |
| `external` | External API calls | Usually requires approval |

The broker computes the **effective** approval requirement from the capability definition and the agent profile's approval policy. Agent-provided hints are logged but never authoritative. See [Trust Boundaries](../guides/trust-boundaries.md) for details.

## Tool Adapters

The broker delegates execution to adapters:

| Adapter | Use Case |
|---------|----------|
| **Local Function** | Python functions called directly |
| **HTTP** | REST API calls to external services |
| **Subprocess** | Shell command execution |

Each adapter type handles its own error recovery, timeout enforcement, and output formatting.

## Retry and Circuit Breaker

The Tool Broker supports resilient execution:

- **Retry with exponential backoff** for transient failures (timeouts, rate limits, service unavailable)
- **Circuit breaker** to prevent cascading failures when a service is down

```python
from agent_kernel.tools.retry import RetryConfig

config = RetryConfig(
    max_retries=3,
    base_delay_ms=1000,
    max_delay_ms=30000,
    retryable_errors={"TIMEOUT", "RATE_LIMITED", "SERVICE_UNAVAILABLE"},
)
```

## Execution Flow

When the executor sends an action to the broker:

1. **Validate** -- input args checked against the capability's JSON Schema
2. **Authorize** -- capability must be in the agent profile's allowlist
3. **Gate** -- check approval policy; block if approval required but not provided
4. **Execute** -- call the appropriate adapter with timeout enforcement
5. **Log** -- create an immutable `ToolCallRecord` with timing, I/O, and status

Every step is recorded. Nothing executes without a trace.

## Next Steps

- [Executor](executor.md) -- how the executor orchestrates plan validation and tool execution
- [Trust Boundaries](../guides/trust-boundaries.md) -- the trust model between agents and the kernel
