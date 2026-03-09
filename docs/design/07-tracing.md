# Tracing & Observability

**Version:** 1.0.1  
**Status:** Implementation Phase

Comprehensive trace logging is **non-negotiable**. Every run must be fully auditable.

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    TRACING SUBSYSTEM                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                     TRACE STORE                             ││
│  │                                                             ││
│  │  • Store DecisionTrace                                      ││
│  │  • Query by time, agent, status                             ││
│  │  • Full-text search on intent/summary                       ││
│  │                                                             ││
│  └─────────────────────────────────────────────────────────────┘│
│                          │                                       │
│            ┌─────────────┼─────────────┐                        │
│            ▼             ▼             ▼                        │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────┐         │
│  │  SQLite Sink  │ │  JSONL Sink   │ │  HTTP Sink    │         │
│  │  (primary)    │ │  (backup)     │ │  (future)     │         │
│  └───────────────┘ └───────────────┘ └───────────────┘         │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## What to Log (Non-Negotiable)

Every `DecisionTrace` must include:

| Field | Description | Required |
|-------|-------------|----------|
| `trace_id` | Unique identifier | ✅ |
| `run_id` | Workflow run ID | ✅ |
| `agent_profile_id` | Which agent profile | ✅ |
| `engine_id` + version | Which engine produced plan | ✅ |
| `intent` | Original user/system intent | ✅ |
| `timestamp` | When trace was created | ✅ |
| `context_packet_id` | Link to context used | ✅ |
| `context_refs_used` | What sources were cited | ✅ |
| `plan.summary` | Plan summary | ✅ |
| `plan.actions` | Actions requested | ✅ |
| `tool_calls[]` | Each tool call with I/O | ✅ |
| `tool_calls[].duration_ms` | Timing | ✅ |
| `tool_calls[].status` | Success/error/denied | ✅ |
| `approvals[]` | Approval records | ✅ |
| `outcome.status` | Final status | ✅ |
| `outcome.artifacts` | Created items | ✅ |
| `provenance.config_hash` | Config snapshot | ✅ |
| `provenance.prompt_bundle_hash` | System prompt bundle hash | ✅ |
| `provenance.prompt_parts[]` | Prompt part hashes + layers | ✅ |
| `provenance.engine_version` | Version info | ✅ |
| `workflow_id` | Explicit workflow ID (v1.0.1) | ✅ |
| `llm_calls[]` | LLM interaction details (v1.0.1) | ✅ |
| `schema_version` | Schema version for migration (v1.0.1) | ✅ |
| `kernel_version` | Kernel version for debugging (v1.0.1) | ✅ |

---

## LLM Call Records (v1.0.1)

Every LLM interaction is captured as an `LLMCallRecord`:

```python
class LLMCallRecord(VersionedModel):
    llm_call_id: str
    trace_id: str
    stage: Literal["routing", "propose_plan", "critic", "revise", "other"]
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    request: LLMRequest
    response: LLMResponse
    request_hash: str | None  # For reproducibility
    response_hash: str | None  # For dedup
    escalated_from_id: str | None  # If this was an escalation
```

### Why This Matters

- **Debugging:** "Why did it plan that?" becomes answerable
- **Cost Tracking:** Token usage and estimated cost per call
- **Escalation Analysis:** Trace when and why escalations occur
- **Evals:** Measure plan quality, gate failure causes, escalation effectiveness

### SQLite Storage

```sql
CREATE TABLE IF NOT EXISTS llm_calls (
    llm_call_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    request_json TEXT NOT NULL,  -- Redacted
    response_json TEXT NOT NULL,
    request_hash TEXT,
    response_hash TEXT,
    escalated_from_id TEXT,
    schema_version TEXT NOT NULL,
    kernel_version TEXT NOT NULL
);

CREATE INDEX idx_llm_calls_trace ON llm_calls(trace_id);
CREATE INDEX idx_llm_calls_stage ON llm_calls(stage);
```

---

## Trace Store Interface

```python
class TraceStore(ABC):
    """Interface for trace persistence."""
    
    @abstractmethod
    async def write(self, trace: DecisionTrace) -> None:
        """Write a trace (append-only)."""
        ...
    
    @abstractmethod
    async def get(self, trace_id: str) -> DecisionTrace | None:
        """Retrieve a trace by ID."""
        ...
    
    @abstractmethod
    async def query(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        agent_profile_id: str | None = None,
        engine_id: str | None = None,
        outcome_status: OutcomeStatus | None = None,
        intent_search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DecisionTrace]:
        """Query traces with filters."""
        ...
    
    @abstractmethod
    async def list_recent(
        self,
        limit: int = 20,
    ) -> list[TraceSummary]:
        """List recent traces (lightweight summaries)."""
        ...
    
    @abstractmethod
    async def search(
        self,
        query: str,
        limit: int = 50,
    ) -> list[TraceSummary]:
        """Full-text search across traces."""
        ...
    
    @abstractmethod
    async def get_statistics(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> TraceStatistics:
        """Get aggregate statistics."""
        ...


class TraceSummary(BaseModel):
    """Lightweight trace summary for listings."""
    
    trace_id: str
    timestamp: datetime
    intent: str
    agent_profile_id: str
    outcome_status: str
    actions_count: int
    duration_ms: int


class TraceStatistics(BaseModel):
    """Aggregate trace statistics."""
    
    total_traces: int
    by_status: dict[str, int]
    by_agent: dict[str, int]
    avg_duration_ms: float
    success_rate: float
    total_tool_calls: int
    tool_calls_by_capability: dict[str, int]
```

---

## Trace Sinks

### Multi-Sink Architecture

The kernel can write to **multiple sinks** for redundancy:

```python
class MultiSinkTraceStore(TraceStore):
    """Writes to multiple sinks."""
    
    def __init__(self, sinks: list[TraceSink]):
        self.sinks = sinks
        self._primary = sinks[0]  # For queries
    
    async def write(self, trace: DecisionTrace) -> None:
        """Write to all sinks."""
        await asyncio.gather(
            *(sink.write(trace) for sink in self.sinks)
        )
    
    async def get(self, trace_id: str) -> DecisionTrace | None:
        """Read from primary sink."""
        return await self._primary.get(trace_id)


class TraceSink(ABC):
    """Interface for trace sinks."""
    
    @abstractmethod
    async def write(self, trace: DecisionTrace) -> None:
        """Write a trace."""
        ...
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if sink is healthy."""
        ...
```

### SQLite Sink (Primary)

```python
class SQLiteTraceSink(TraceSink, TraceStore):
    """SQLite-backed trace storage."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._engine: AsyncEngine | None = None
    
    async def initialize(self) -> None:
        """Create tables if needed."""
        
        self._engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path}",
            echo=False,
        )
        
        async with self._engine.begin() as conn:
            await conn.run_sync(self._create_tables)
    
    def _create_tables(self, conn: Connection) -> None:
        """Create trace tables."""
        
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS traces (
                trace_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                agent_profile_id TEXT NOT NULL,
                engine_id TEXT NOT NULL,
                intent TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                context_packet_id TEXT NOT NULL,
                outcome_status TEXT NOT NULL,
                duration_ms INTEGER,
                data JSON NOT NULL,
                
                -- Indexes for common queries
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_traces_timestamp 
            ON traces(timestamp DESC)
        """))
        
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_traces_agent 
            ON traces(agent_profile_id)
        """))
        
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_traces_status 
            ON traces(outcome_status)
        """))
        
        # Full-text search
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS traces_fts 
            USING fts5(
                intent,
                summary,
                content='traces',
                content_rowid='rowid'
            )
        """))
        
        # Tool calls table for analytics
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tool_calls (
                tool_call_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL REFERENCES traces(trace_id),
                capability_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                data JSON NOT NULL
            )
        """))
        
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tool_calls_trace 
            ON tool_calls(trace_id)
        """))
        
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_tool_calls_capability 
            ON tool_calls(capability_name)
        """))
    
    async def write(self, trace: DecisionTrace) -> None:
        """Write trace to SQLite."""
        
        async with self._engine.begin() as conn:
            # Calculate duration
            duration_ms = 0
            if trace.tool_calls:
                start = min(tc.started_at for tc in trace.tool_calls)
                end = max(tc.ended_at for tc in trace.tool_calls)
                duration_ms = int((end - start).total_seconds() * 1000)
            
            # Insert trace
            await conn.execute(text("""
                INSERT INTO traces (
                    trace_id, run_id, agent_profile_id, engine_id,
                    intent, timestamp, context_packet_id, outcome_status,
                    duration_ms, data
                ) VALUES (
                    :trace_id, :run_id, :agent_profile_id, :engine_id,
                    :intent, :timestamp, :context_packet_id, :outcome_status,
                    :duration_ms, :data
                )
            """), {
                "trace_id": trace.trace_id,
                "run_id": trace.run_id,
                "agent_profile_id": trace.agent_profile_id,
                "engine_id": trace.engine_id,
                "intent": trace.intent,
                "timestamp": trace.timestamp.isoformat(),
                "context_packet_id": trace.context_packet_id,
                "outcome_status": trace.outcome.status.value,
                "duration_ms": duration_ms,
                "data": trace.model_dump_json(),
            })
            
            # Insert tool calls
            for tc in trace.tool_calls:
                await conn.execute(text("""
                    INSERT INTO tool_calls (
                        tool_call_id, trace_id, capability_name,
                        started_at, ended_at, duration_ms, status, data
                    ) VALUES (
                        :tool_call_id, :trace_id, :capability_name,
                        :started_at, :ended_at, :duration_ms, :status, :data
                    )
                """), {
                    "tool_call_id": tc.tool_call_id,
                    "trace_id": trace.trace_id,
                    "capability_name": tc.capability_name,
                    "started_at": tc.started_at.isoformat(),
                    "ended_at": tc.ended_at.isoformat(),
                    "duration_ms": tc.duration_ms,
                    "status": tc.status.value,
                    "data": tc.model_dump_json(),
                })
            
            # Update FTS index
            await conn.execute(text("""
                INSERT INTO traces_fts (rowid, intent, summary)
                SELECT rowid, intent, json_extract(data, '$.plan.summary')
                FROM traces WHERE trace_id = :trace_id
            """), {"trace_id": trace.trace_id})
    
    async def get(self, trace_id: str) -> DecisionTrace | None:
        """Retrieve trace by ID."""
        
        async with self._engine.connect() as conn:
            result = await conn.execute(text("""
                SELECT data FROM traces WHERE trace_id = :trace_id
            """), {"trace_id": trace_id})
            
            row = result.fetchone()
            if row:
                return DecisionTrace.model_validate_json(row[0])
            return None
    
    async def query(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        agent_profile_id: str | None = None,
        engine_id: str | None = None,
        outcome_status: OutcomeStatus | None = None,
        intent_search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DecisionTrace]:
        """Query traces with filters."""
        
        conditions = []
        params = {"limit": limit, "offset": offset}
        
        if since:
            conditions.append("timestamp >= :since")
            params["since"] = since.isoformat()
        
        if until:
            conditions.append("timestamp <= :until")
            params["until"] = until.isoformat()
        
        if agent_profile_id:
            conditions.append("agent_profile_id = :agent_profile_id")
            params["agent_profile_id"] = agent_profile_id
        
        if engine_id:
            conditions.append("engine_id = :engine_id")
            params["engine_id"] = engine_id
        
        if outcome_status:
            conditions.append("outcome_status = :outcome_status")
            params["outcome_status"] = outcome_status.value
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        # Use FTS for intent search
        if intent_search:
            query = f"""
                SELECT t.data FROM traces t
                JOIN traces_fts fts ON t.rowid = fts.rowid
                WHERE {where_clause} AND traces_fts MATCH :search
                ORDER BY t.timestamp DESC
                LIMIT :limit OFFSET :offset
            """
            params["search"] = intent_search
        else:
            query = f"""
                SELECT data FROM traces
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT :limit OFFSET :offset
            """
        
        async with self._engine.connect() as conn:
            result = await conn.execute(text(query), params)
            return [
                DecisionTrace.model_validate_json(row[0])
                for row in result.fetchall()
            ]
```

### JSONL Sink (Backup)

```python
class JSONLTraceSink(TraceSink):
    """JSONL file-based trace sink for backup/export."""
    
    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    async def write(self, trace: DecisionTrace) -> None:
        """Append trace to daily JSONL file."""
        
        date_str = trace.timestamp.strftime("%Y-%m-%d")
        file_path = self.base_path / f"{date_str}.jsonl"
        
        async with aiofiles.open(file_path, "a") as f:
            await f.write(trace.model_dump_json() + "\n")
    
    async def health_check(self) -> bool:
        """Check if we can write to the directory."""
        return self.base_path.exists() and self.base_path.is_dir()
```

### HTTP Sink (Future)

```python
class HTTPTraceSink(TraceSink):
    """HTTP sink for remote trace collection."""
    
    def __init__(
        self,
        endpoint: str,
        api_key: str | None = None,
        batch_size: int = 10,
        flush_interval_seconds: float = 5.0,
    ):
        self.endpoint = endpoint
        self.api_key = api_key
        self.batch_size = batch_size
        self.flush_interval = flush_interval_seconds
        self._buffer: list[DecisionTrace] = []
        self._client = httpx.AsyncClient()
    
    async def write(self, trace: DecisionTrace) -> None:
        """Buffer trace and flush if needed."""
        
        self._buffer.append(trace)
        
        if len(self._buffer) >= self.batch_size:
            await self._flush()
    
    async def _flush(self) -> None:
        """Send buffered traces to endpoint."""
        
        if not self._buffer:
            return
        
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        payload = [t.model_dump() for t in self._buffer]
        
        try:
            response = await self._client.post(
                self.endpoint,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            self._buffer.clear()
        except Exception as e:
            # Log error but don't fail
            structlog.get_logger().error(
                "Failed to flush traces to HTTP sink",
                error=str(e),
                trace_count=len(self._buffer),
            )
```

---

## Redaction

Sensitive data must be redacted before logging:

```python
class TraceRedactor:
    """Redacts sensitive data from traces."""
    
    SENSITIVE_PATTERNS = [
        r"api[_-]?key",
        r"password",
        r"secret",
        r"token",
        r"credential",
        r"auth",
    ]
    
    def __init__(self, custom_patterns: list[str] | None = None):
        self.patterns = self.SENSITIVE_PATTERNS + (custom_patterns or [])
        self._compiled = [re.compile(p, re.IGNORECASE) for p in self.patterns]
    
    def redact_trace(self, trace: DecisionTrace) -> DecisionTrace:
        """Create a redacted copy of a trace."""
        
        # Deep copy and redact
        data = trace.model_dump()
        data = self._redact_dict(data)
        return DecisionTrace.model_validate(data)
    
    def _redact_dict(self, d: dict) -> dict:
        """Recursively redact dictionary."""
        
        result = {}
        for key, value in d.items():
            if self._is_sensitive_key(key):
                result[key] = "[REDACTED]"
            elif isinstance(value, dict):
                result[key] = self._redact_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    self._redact_dict(v) if isinstance(v, dict) else v
                    for v in value
                ]
            else:
                result[key] = value
        return result
    
    def _is_sensitive_key(self, key: str) -> bool:
        """Check if key matches sensitive patterns."""
        return any(p.search(key) for p in self._compiled)
```

---

## CLI Commands

```python
# In cli/main.py

@app.command()
def list_traces(
    limit: int = 20,
    agent: str | None = None,
    status: str | None = None,
):
    """List recent traces."""
    
    traces = asyncio.run(trace_store.list_recent(limit=limit))
    
    table = Table(title="Recent Traces")
    table.add_column("ID", style="cyan")
    table.add_column("Time", style="green")
    table.add_column("Intent", style="white")
    table.add_column("Agent", style="blue")
    table.add_column("Status", style="yellow")
    table.add_column("Actions", style="magenta")
    
    for t in traces:
        table.add_row(
            t.trace_id[:8],
            t.timestamp.strftime("%Y-%m-%d %H:%M"),
            t.intent[:40] + "..." if len(t.intent) > 40 else t.intent,
            t.agent_profile_id,
            t.outcome_status,
            str(t.actions_count),
        )
    
    console.print(table)


@app.command()
def show_trace(trace_id: str):
    """Show detailed trace information."""
    
    trace = asyncio.run(trace_store.get(trace_id))
    
    if not trace:
        console.print(f"[red]Trace not found: {trace_id}[/red]")
        return
    
    console.print(Panel(
        f"[bold]Intent:[/bold] {trace.intent}\n"
        f"[bold]Agent:[/bold] {trace.agent_profile_id}\n"
        f"[bold]Engine:[/bold] {trace.engine_id}\n"
        f"[bold]Status:[/bold] {trace.outcome.status.value}\n"
        f"[bold]Time:[/bold] {trace.timestamp.isoformat()}",
        title=f"Trace: {trace.trace_id}",
    ))
    
    console.print("\n[bold]Plan Summary:[/bold]")
    console.print(trace.plan.summary)
    
    console.print("\n[bold]Tool Calls:[/bold]")
    for tc in trace.tool_calls:
        status_color = "green" if tc.status == CallStatus.SUCCESS else "red"
        console.print(
            f"  • {tc.capability_name}: "
            f"[{status_color}]{tc.status.value}[/{status_color}] "
            f"({tc.duration_ms}ms)"
        )
    
    console.print("\n[bold]Artifacts Created:[/bold]")
    for a in trace.outcome.artifacts:
        console.print(f"  • {a.ref_type}: {a.ref_id}")


@app.command()
def trace_stats(
    days: int = 7,
):
    """Show trace statistics."""
    
    since = datetime.utcnow() - timedelta(days=days)
    stats = asyncio.run(trace_store.get_statistics(since=since))
    
    console.print(Panel(
        f"[bold]Total Traces:[/bold] {stats.total_traces}\n"
        f"[bold]Success Rate:[/bold] {stats.success_rate:.1%}\n"
        f"[bold]Avg Duration:[/bold] {stats.avg_duration_ms:.0f}ms\n"
        f"[bold]Total Tool Calls:[/bold] {stats.total_tool_calls}",
        title=f"Statistics (last {days} days)",
    ))
```

---

## Trace-Derived Analytics (v1.2)

Traces are not just for auditing — they power runtime feedback loops.

### Event Types for Feedback Loops

The `EventLog` emits structured events that downstream components consume:

| Event Type | Emitter | Consumer | Purpose |
|------------|---------|----------|---------|
| `llm_cache.hit` | `CachedLLMService` | Metrics | Track cache effectiveness |
| `llm_cache.miss` | `CachedLLMService` | Metrics | Track cache miss rate |
| `cost.anomaly` | `CostAnomalyDetector` | Alerting | Flag unexpected cost spikes |

### Cost Anomaly Detection

The `CostAnomalyDetector` maintains a rolling window of per-trace costs (LLM + tool calls) and flags outliers:

- Computes rolling mean and standard deviation
- Triggers when `(current_cost - mean) / std > threshold` (default: 2.0 std devs)
- Emits `cost.anomaly` event with `AnomalyReport` payload
- Skips detection when insufficient data points (configurable `min_data_points`)

### Thinking Metrics

The `thinking-stats` CLI command aggregates `ReasoningMetadata` from traces:

| Metric | Description |
|--------|-------------|
| Tier distribution | Count of traces per thinking tier (0-3) |
| Escalation rate | Percentage of traces that escalated |
| Gate failure counts | Breakdown by failure type |
| Model success rates | Per-model success rate |
| Tokens per tier | Average token usage by tier |
| Cost per workflow | Total USD cost grouped by workflow |

```bash
agent-kernel thinking-stats --since-hours 168
agent-kernel thinking-stats --workflow-id daily_checkin
```

### Success Rate Routing

The `SuccessRateRouter` queries traces for per-model success rates and recommends models sorted by reliability:

```python
router = SuccessRateRouter(trace_store=store, min_success_rate=0.85)
best = router.best_model(fallback="gpt-4o")
recommendations = router.recommend(budget_usd=0.50)
```

---

## Related Documents

- [00-overview.md](00-overview.md) - Design principles
- [01-schemas.md](01-schemas.md) - DecisionTrace, ToolCallRecord
- [06-executor.md](06-executor.md) - Executor writes traces
- [11-thinking-policy.md](11-thinking-policy.md) - Trace-based feedback loops
