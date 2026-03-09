# Tool Broker & Capability Registry

**Version:** 1.0.1  
**Status:** Design Phase

The Tool Broker is the **single point of tool execution**. No agent or engine may call tools directly.

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        TOOL BROKER                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────┐                                             │
│  │   Capability    │ ◀── JSON Schema definitions                │
│  │    Registry     │     from configs/capabilities/             │
│  └────────┬────────┘                                             │
│           │                                                      │
│           ▼                                                      │
│  ┌─────────────────┐                                             │
│  │   Tool Broker   │                                             │
│  │                 │                                             │
│  │  1. Validate    │ ◀── Input against schema                   │
│  │  2. Authorize   │ ◀── AgentProfile allowlist                 │
│  │  3. Gate        │ ◀── Approval policy                        │
│  │  4. Execute     │ ◀── Via adapter                            │
│  │  5. Log         │ ◀── ToolCallRecord                         │
│  │                 │                                             │
│  └────────┬────────┘                                             │
│           │                                                      │
│           ▼                                                      │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                     TOOL ADAPTERS                        │    │
│  ├─────────────┬─────────────┬─────────────┬───────────────┤    │
│  │   Local     │  Subprocess │    HTTP     │     MCP       │    │
│  │  Function   │     CLI     │   Adapter   │   (future)    │    │
│  └─────────────┴─────────────┴─────────────┴───────────────┘    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Capability Registry

### Purpose

The registry loads capability definitions from `configs/capabilities/` and provides:
- Input/output schema validation
- Policy information (side effects, approval defaults)
- Timeout and rate limit configuration

### CapabilityDef Model

```python
class CapabilityDef(BaseModel):
    """Definition of a tool capability."""
    
    capability_name: str  # e.g., "tasks.create@v1"
    description: str
    input_schema: dict[str, Any]  # JSON Schema
    output_schema: dict[str, Any]  # JSON Schema
    side_effect_level: SideEffect
    requires_approval_default: bool
    timeout_ms: int = 30000
    rate_limit: RateLimit | None = None
    redaction_policy: RedactionPolicy | None = None
    adapter_type: str = "local"  # "local", "http", "subprocess", "mcp"
    adapter_config: dict[str, Any] = {}

class RateLimit(BaseModel):
    max_calls: int
    window_seconds: int

class RedactionPolicy(BaseModel):
    redact_input_fields: list[str] = []
    redact_output_fields: list[str] = []
```

### Registry Interface

```python
class CapabilityRegistry:
    """Registry of available tool capabilities."""
    
    def __init__(self, config_dir: Path):
        self._capabilities: dict[str, CapabilityDef] = {}
        self._load_capabilities(config_dir)
    
    def get(self, capability_name: str) -> CapabilityDef | None:
        """Get capability definition by name."""
        return self._capabilities.get(capability_name)
    
    def list_capabilities(self) -> list[CapabilityDef]:
        """List all registered capabilities."""
        return list(self._capabilities.values())
    
    def validate_input(
        self,
        capability_name: str,
        args: dict[str, Any],
    ) -> ValidationResult:
        """Validate input against capability schema."""
        ...
    
    def validate_output(
        self,
        capability_name: str,
        result: dict[str, Any],
    ) -> ValidationResult:
        """Validate output against capability schema."""
        ...
```

### Capability Naming Convention

```
{domain}.{action}@v{version}

Examples:
- tasks.create@v1
- tasks.update@v1
- tasks.list@v1
- notes.search@v1
- notes.create@v1
- calendar.create@v1
- graph.record@v1
- graph.query@v1
```

---

## Tool Broker

### Purpose

The broker is the **only component that executes tools**. It:
1. Validates inputs against capability schema
2. Enforces AgentProfile allowlists
3. Gates approvals per policy
4. Executes via appropriate adapter
5. Logs every call as ToolCallRecord
6. Handles retries and timeouts

### Broker Interface

```python
class ToolBroker:
    """Central tool execution gateway."""
    
    def __init__(
        self,
        registry: CapabilityRegistry,
        event_log: EventLog,
        adapters: dict[str, ToolAdapter],
    ):
        self.registry = registry
        self.event_log = event_log
        self.adapters = adapters
    
    async def execute(
        self,
        action: ActionRequest,
        agent_profile: AgentProfile,
        approval_token: str | None = None,
    ) -> ToolCallRecord:
        """Execute an action through the broker."""
        
        # 1. Get capability definition
        capability = self.registry.get(action.capability_name)
        if not capability:
            return self._error_record(action, "CAPABILITY_NOT_FOUND")
        
        # 2. Validate input
        validation = self.registry.validate_input(
            action.capability_name,
            action.args,
        )
        if not validation.valid:
            return self._error_record(action, "VALIDATION_FAILED", validation.errors)
        
        # 3. Check allowlist
        if action.capability_name not in agent_profile.allowed_capabilities:
            return self._error_record(action, "CAPABILITY_NOT_ALLOWED")
        
        # 4. Check approval
        if self._requires_approval(action, capability, agent_profile):
            if not approval_token:
                return self._pending_approval_record(action)
            if not self._validate_approval(approval_token, action):
                return self._error_record(action, "INVALID_APPROVAL")
        
        # 5. Execute via adapter
        adapter = self.adapters.get(capability.adapter_type)
        if not adapter:
            return self._error_record(action, "ADAPTER_NOT_FOUND")
        
        started_at = datetime.utcnow()
        try:
            result = await asyncio.wait_for(
                adapter.execute(capability, action.args),
                timeout=capability.timeout_ms / 1000,
            )
            status = CallStatus.SUCCESS
            error = None
        except asyncio.TimeoutError:
            result = {}
            status = CallStatus.TIMEOUT
            error = ErrorRecord(code="TIMEOUT", message="Execution timed out")
        except Exception as e:
            result = {}
            status = CallStatus.ERROR
            error = ErrorRecord(code="EXECUTION_ERROR", message=str(e))
        
        ended_at = datetime.utcnow()
        
        # 6. Create and log record
        record = ToolCallRecord(
            tool_call_id=generate_ulid(),
            capability_name=action.capability_name,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=int((ended_at - started_at).total_seconds() * 1000),
            input=self._redact_input(action.args, capability),
            output=self._redact_output(result, capability),
            status=status,
            error=error,
            related_action_id=action.action_id,
        )
        
        # 7. Emit event
        await self.event_log.append(Event(
            event_id=generate_ulid(),
            event_type=EventType.TOOL_CALLED,
            timestamp=ended_at,
            payload={"record_id": record.tool_call_id, "status": status.value},
        ))
        
        return record
    
    def _requires_approval(
        self,
        action: ActionRequest,
        capability: CapabilityDef,
        agent_profile: AgentProfile,
    ) -> bool:
        """Determine if action requires approval."""
        # Explicit in action
        if action.requires_approval:
            return True
        
        # Capability default
        if capability.requires_approval_default:
            return True
        
        # Profile policy
        if action.capability_name in agent_profile.approval_policy.require_approval_for:
            return True
        
        # Side effect level
        if action.side_effect == SideEffect.EXTERNAL_WRITE:
            if SideEffect.EXTERNAL_WRITE not in agent_profile.approval_policy.auto_approve_side_effects:
                return True
        
        return False
```

---

## Tool Adapters

### Adapter Interface

```python
class ToolAdapter(ABC):
    """Interface for tool execution adapters."""
    
    @abstractmethod
    async def execute(
        self,
        capability: CapabilityDef,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute the tool and return result."""
        ...
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if adapter is healthy."""
        ...
```

### 1. Local Function Adapter

For Python functions registered directly:

```python
class LocalFunctionAdapter(ToolAdapter):
    """Execute local Python functions."""
    
    def __init__(self):
        self._functions: dict[str, Callable] = {}
    
    def register(
        self,
        capability_name: str,
        func: Callable[..., Any],
    ) -> None:
        """Register a function for a capability."""
        self._functions[capability_name] = func
    
    async def execute(
        self,
        capability: CapabilityDef,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        func = self._functions.get(capability.capability_name)
        if not func:
            raise ValueError(f"No function registered for {capability.capability_name}")
        
        if asyncio.iscoroutinefunction(func):
            return await func(**args)
        else:
            return await asyncio.to_thread(func, **args)
```

### 2. HTTP Adapter

For REST API calls:

```python
class HTTPAdapter(ToolAdapter):
    """Execute HTTP API calls."""
    
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient()
    
    async def execute(
        self,
        capability: CapabilityDef,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        config = capability.adapter_config
        
        url = config["url"]
        method = config.get("method", "POST")
        headers = config.get("headers", {})
        
        response = await self.client.request(
            method=method,
            url=url,
            json=args,
            headers=headers,
            timeout=capability.timeout_ms / 1000,
        )
        response.raise_for_status()
        return response.json()
```

### 3. Subprocess Adapter

For CLI commands:

```python
class SubprocessAdapter(ToolAdapter):
    """Execute subprocess CLI commands."""
    
    async def execute(
        self,
        capability: CapabilityDef,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        config = capability.adapter_config
        
        command = config["command"]
        # Substitute args into command template
        formatted_command = command.format(**args)
        
        proc = await asyncio.create_subprocess_shell(
            formatted_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise RuntimeError(f"Command failed: {stderr.decode()}")
        
        return {"stdout": stdout.decode(), "returncode": proc.returncode}
```

### 4. MCP Adapter (Future)

For MCP protocol:

```python
class MCPAdapter(ToolAdapter):
    """Execute MCP server calls (future implementation)."""
    
    async def execute(
        self,
        capability: CapabilityDef,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        # TODO: Implement when MCP is allowed
        raise NotImplementedError("MCP adapter not yet implemented")
```

---

## Capability Definitions

### Example: tasks.create@v1

```yaml
# configs/capabilities/tasks.create@v1.yaml
capability_name: tasks.create@v1
description: Create a new task in the local task store
input_schema:
  type: object
  required:
    - title
  properties:
    title:
      type: string
      maxLength: 200
      description: Task title
    description:
      type: string
      description: Detailed description
    due_date:
      type: string
      format: date
      description: Due date (YYYY-MM-DD)
    priority:
      type: string
      enum: [low, medium, high]
      default: medium
    project_id:
      type: string
      description: Associated project
    tags:
      type: array
      items:
        type: string
output_schema:
  type: object
  required:
    - task_id
    - created_at
  properties:
    task_id:
      type: string
    created_at:
      type: string
      format: date-time
side_effect_level: local
requires_approval_default: false
timeout_ms: 5000
adapter_type: local
```

### Example: notes.search@v1

```yaml
# configs/capabilities/notes.search@v1.yaml
capability_name: notes.search@v1
description: Search notes using semantic and keyword search
input_schema:
  type: object
  required:
    - query
  properties:
    query:
      type: string
      description: Search query
    limit:
      type: integer
      default: 10
      maximum: 50
    filters:
      type: object
      properties:
        project_id:
          type: string
        tags:
          type: array
          items:
            type: string
        date_range:
          type: object
          properties:
            start:
              type: string
              format: date
            end:
              type: string
              format: date
output_schema:
  type: object
  properties:
    results:
      type: array
      items:
        type: object
        properties:
          note_id:
            type: string
          title:
            type: string
          excerpt:
            type: string
          score:
            type: number
    total_count:
      type: integer
side_effect_level: none
requires_approval_default: false
timeout_ms: 10000
adapter_type: local
```

### Example: calendar.create@v1

```yaml
# configs/capabilities/calendar.create@v1.yaml
capability_name: calendar.create@v1
description: Create a calendar event (external API)
input_schema:
  type: object
  required:
    - title
    - start_time
    - end_time
  properties:
    title:
      type: string
    start_time:
      type: string
      format: date-time
    end_time:
      type: string
      format: date-time
    description:
      type: string
    attendees:
      type: array
      items:
        type: string
        format: email
output_schema:
  type: object
  properties:
    event_id:
      type: string
    html_link:
      type: string
      format: uri
side_effect_level: external
requires_approval_default: true
timeout_ms: 15000
adapter_type: http
adapter_config:
  url: "${CALENDAR_API_URL}/events"
  method: POST
  headers:
    Authorization: "Bearer ${CALENDAR_API_TOKEN}"
redaction_policy:
  redact_input_fields:
    - attendees
```

---

## Initial Capabilities

Implement these first:

| Capability | Side Effect | Priority |
|------------|-------------|----------|
| `tasks.list@v1` | none | High |
| `tasks.create@v1` | local | High |
| `tasks.update@v1` | local | High |
| `notes.search@v1` | none | High |
| `notes.create@v1` | local | High |
| `graph.query@v1` | none | Medium |
| `graph.record@v1` | local | Medium |
| `calendar.list@v1` | none | Medium |
| `calendar.create@v1` | external | Low |
| `reminders.schedule@v1` | local | Low |

---

## Adaptive Timeout Manager (v1.2)

**Location:** `agent_kernel.tools.adaptive_timeout`

Per-capability P99-based timeout tuning derived from historical trace data.

### Purpose

Static `timeout_ms` values in capability definitions are guesses. The `AdaptiveTimeoutManager` replaces them with data-driven timeouts computed from actual execution latency.

### How It Works

1. Queries `ToolCallRecord.duration_ms` from the trace store, grouped by `capability_name`
2. Computes P50, P99, and max latency per capability
3. Returns `P99 * buffer_factor` as the recommended timeout
4. Falls back to the capability's static `timeout_ms` when insufficient samples exist

### Integration with Tool Broker

The `ToolBroker` accepts an optional `AdaptiveTimeoutManager`. When present, adaptive timeouts override static values:

```python
# In ToolBroker.execute():
timeout_ms = capability.timeout_ms  # Static default
if self._timeout_manager:
    timeout_ms = self._timeout_manager.get_timeout(
        capability_name, capability.timeout_ms
    )
```

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `buffer_factor` | 1.2 | Multiplier on P99 (headroom for variance) |
| `min_samples` | 10 | Minimum calls before adaptive timeout activates |
| `cache_ttl_seconds` | 300 | How long computed stats are cached |
| `lookback_hours` | 168 | Time window for trace queries (1 week) |

### Key Data Structure

```python
@dataclass
class CapabilityLatencyStats:
    capability_name: str
    total_calls: int
    p50_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    recommended_timeout_ms: int
```

---

## Related Documents

- [00-overview.md](00-overview.md) - Design principles
- [01-schemas.md](01-schemas.md) - ActionRequest, ToolCallRecord
- [06-executor.md](06-executor.md) - Executor uses Tool Broker
- [11-thinking-policy.md](11-thinking-policy.md) - Trace-based feedback loops
