# Storage & Workflow Optimization Analysis

**Date:** 2026-01-25
**Version:** 1.0.0
**Scope:** Memory subsystem, workflows, LLM selection, trace-based optimization

---

## Executive Summary

After comprehensive analysis of the agent-kernel codebase, I've identified **significant optimization opportunities** across four areas:

1. **Memory Subsystem**: Critical scalability bottleneck in vector store (O(N) search)
2. **Workflows**: No persistent checkpointing; resume re-executes entire workflow
3. **LLM Selection**: Static model assignment; no dynamic routing or cost optimization
4. **Trace Utilization**: Rich audit data exists but isn't fed back into system improvement

The system has **excellent architectural foundations** (clean abstractions, immutable traces, schema contracts), but lacks production-grade optimizations for scale and cost efficiency.

---

## Part 1: Memory Subsystem Analysis

### Current Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Memory Subsystem                             │
├─────────────────┬─────────────────┬─────────────────┬───────────────┤
│ Document Store  │  Vector Store   │   Graph Store   │  Entity Store │
│ (SQLite FTS5)   │  (SQLite BLOB)  │   (SQLite)      │  (SQLite)     │
├─────────────────┼─────────────────┼─────────────────┼───────────────┤
│ Full-text       │ Semantic        │ Relationships   │ Multi-source  │
│ keyword search  │ similarity      │ & traversal     │ registry      │
└─────────────────┴─────────────────┴─────────────────┴───────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │         Event Log             │
              │   (Immutable audit trail)     │
              └───────────────────────────────┘
```

### Critical Issues

#### Issue 1: Vector Store Scalability (CRITICAL)

**Location:** `src/agent_kernel/memory/vector_store.py:142-176`

```python
# CURRENT: O(N) linear scan
async def query(self, vector, top_k, filters):
    rows = cursor.fetchall()  # Fetches ALL vectors
    for row in rows:
        similarity = cosine_similarity(query_vec, row_vec)  # N comparisons
    # Filter in Python AFTER full scan
```

**Impact:**
| Vector Count | Query Time (est.) | Acceptable? |
|-------------|-------------------|-------------|
| 1,000       | ~50ms             | ✓ Yes       |
| 10,000      | ~500ms            | ⚠ Borderline |
| 100,000     | ~5s               | ✗ No        |
| 1,000,000   | ~50s              | ✗ Unusable  |

**Recommendation:** Replace with dedicated vector DB

```python
# RECOMMENDED: LanceDB with HNSW index → O(log N)
from lancedb import connect

class LanceVectorStore(VectorStore):
    def __init__(self, db_path: str):
        self._db = connect(db_path)

    async def query(self, vector, top_k, filters):
        # Sub-linear search via HNSW index
        return self._table.search(vector).limit(top_k).to_list()
```

**Alternatives:**
- **LanceDB** - Embedded, Rust-based, HNSW index (recommended for local)
- **Qdrant** - Dedicated service, excellent filtering
- **Chroma** - Python-native, simpler API
- **pgvector** - If consolidating on PostgreSQL

#### Issue 2: N+1 Queries in Graph Traversal

**Location:** `src/agent_kernel/memory/graph_store.py:333-381`

```python
# CURRENT: N+1 queries for subgraph
async def get_subgraph(seed_ids, depth, edge_types):
    for node_id in frontier:
        node = await self.get_node(node_id)      # 1 query per node
        edges = await self.get_edges(node_id)    # 1 query per node
```

**Recommendation:** Batch fetch with single query

```python
# RECOMMENDED: Single query with JOIN
async def get_subgraph_batch(seed_ids, depth):
    query = """
    WITH RECURSIVE traversal AS (
        SELECT node_id, 0 as depth FROM nodes WHERE node_id IN (?)
        UNION
        SELECT e.target_id, t.depth + 1
        FROM traversal t
        JOIN edges e ON e.source_id = t.node_id
        WHERE t.depth < ?
    )
    SELECT n.*, e.* FROM traversal t
    JOIN nodes n ON n.node_id = t.node_id
    LEFT JOIN edges e ON e.source_id = t.node_id
    """
```

#### Issue 3: Post-Query Filtering Pattern

Across all stores, metadata filtering happens in Python after database fetch:

```python
# CURRENT (Document, Vector, Graph, Entity, Experience stores)
rows = cursor.fetchall()
for row in rows:
    if filter_matches(row):  # Python filtering
        results.append(row)
```

**Recommendation:** Push filtering to SQL layer

```sql
-- For JSON metadata filtering
SELECT * FROM entities
WHERE json_extract(metadata, '$.source_id') = ?
  AND json_extract(metadata, '$.entity_type') = ?
```

### Memory Subsystem Recommendations Summary

| Priority | Issue | Current | Recommended | Effort |
|----------|-------|---------|-------------|--------|
| P0 | Vector search | O(N) SQLite | LanceDB HNSW | Medium |
| P1 | Graph traversal | N+1 queries | Recursive CTE | Low |
| P1 | Metadata filtering | Python | SQL JSON functions | Low |
| P2 | Connection pooling | None | aiosqlite pool | Low |
| P2 | Batch operations | Single inserts | Bulk upserts | Medium |

---

## Part 2: Workflow System Analysis

### Current Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                          WorkflowRunner                                │
├────────────────────────────────────────────────────────────────────────┤
│ Steps: assemble_context → propose_plan → validate → execute → trace   │
├────────────────────────────────────────────────────────────────────────┤
│ State: IN-MEMORY ONLY (lost on restart)                               │
├────────────────────────────────────────────────────────────────────────┤
│ Resume: Re-executes ENTIRE workflow with approval tokens              │
└────────────────────────────────────────────────────────────────────────┘
```

### Critical Issues

#### Issue 1: No Persistent Checkpointing (HIGH IMPACT)

**Location:** `src/agent_kernel/workflows/runner.py:248-249`

```python
# CURRENT: Comment on line 248
# "can be replaced with persistent store"
self._workflow_runs: dict[str, WorkflowRun] = {}  # In-memory only!
```

**Impact:**
- Workflow runs lost on process restart
- Cannot resume across sessions
- Approval-blocked workflows require re-run from start
- No durability guarantees for long-running workflows

**Recommendation:** Add WorkflowRunStore

```python
class SQLiteWorkflowRunStore:
    """Persistent workflow state with checkpoint support."""

    async def save_checkpoint(
        self,
        run_id: str,
        step_index: int,
        step_outputs: dict[str, Any],
        state: CalendarDerivationState | None,
    ) -> None:
        """Save state after each step for resumption."""

    async def load_checkpoint(self, run_id: str) -> WorkflowCheckpoint:
        """Load checkpoint for resumption."""
```

#### Issue 2: Resume Re-Executes Entire Workflow

**Location:** `src/agent_kernel/workflows/runner.py:2247-2290`

```python
# CURRENT
async def resume(run_id, approval_tokens):
    run = self._workflow_runs.get(run_id)  # Get in-memory state
    # Simply calls run() again with approval tokens
    return await self.run(
        workflow_id=run.workflow_id,
        approval_tokens=approval_tokens,  # Only difference
    )
    # ^^^ This re-runs ALL steps, not just from checkpoint
```

**Impact:**
- Expensive re-computation (context assembly, LLM calls)
- Calendar derivation state lost
- Task project cache invalidated
- Repeated API calls to external services

**Recommendation:** True checkpoint resumption

```python
async def resume(run_id, approval_tokens):
    checkpoint = await self._store.load_checkpoint(run_id)

    # Resume from last successful step
    for step in spec.steps[checkpoint.step_index:]:
        result = await self._execute_step(
            step=step,
            context=checkpoint.step_outputs,  # Reuse prior outputs
            state=checkpoint.state,
        )
```

#### Issue 3: Configuration Loaded Every Time

**Location:** `src/agent_kernel/workflows/runner.py:917-1015`

```python
# CURRENT: No caching
def _load_calendar_sources(self):
    config_path = Path(self._configs_dir) / "integrations" / "calendar_sources.yaml"
    return yaml.safe_load(config_path.read_text())  # Parsed every call
```

**Recommendation:** Cache with invalidation

```python
@functools.lru_cache(maxsize=1)
def _load_calendar_sources(self, config_mtime: float):
    """Cache until config file changes."""
    ...

def get_calendar_sources(self):
    mtime = self._config_path.stat().st_mtime
    return self._load_calendar_sources(mtime)
```

### Workflow Recommendations Summary

| Priority | Issue | Impact | Recommendation | Effort |
|----------|-------|--------|----------------|--------|
| P0 | No persistence | Data loss | SQLiteWorkflowRunStore | Medium |
| P0 | Resume re-executes | Wasted compute | Checkpoint resumption | Medium |
| P1 | Config reloading | I/O overhead | LRU cache with mtime | Low |
| P1 | Store reconnections | DB overhead | Instance caching | Low |
| P2 | 2600-line runner | Maintainability | Extract step handlers | Medium |

---

## Part 3: LLM & Agent Selection Analysis

### Current Model Assignments

| Agent | Model | Purpose | Assessment |
|-------|-------|---------|------------|
| `personal_ops_agent` | gpt-5 | Daily planning | ✓ Appropriate |
| `daily_planner_agent` | gpt-5 | Workflow planning | ⚠ Expensive for simple tasks |
| `trace_analyst` | gpt-4o | Diagnostics | ✓ Appropriate |
| `vault_indexer_agent` | gpt-4o-mini | Classification | ✓ Cost-optimized |
| `meeting_note_agent` | gpt-4o-mini | Note generation | ⚠ May need upgrade for quality |

### Issues Identified

#### Issue 1: Static Model Assignment

All agents have fixed models in YAML configs. No dynamic routing based on:
- Task complexity
- Token budget constraints
- Latency requirements
- Historical success rates

**Recommendation:** Implement model router

```python
class DynamicModelRouter:
    """Route tasks to appropriate models based on complexity."""

    def select_model(
        self,
        intent: str,
        context_size: int,
        task_type: str,
        budget_remaining: float,
    ) -> ModelConfig:
        # Check historical success rates from traces
        success_rates = self._trace_store.get_model_success_rates(task_type)

        # Route based on complexity indicators
        if self._is_complex(intent, context_size):
            return ModelConfig(model="gpt-5", reasoning_effort="high")
        elif budget_remaining < 0.10:
            return ModelConfig(model="gpt-4o-mini")  # Cost-conscious
        else:
            return ModelConfig(model="gpt-4o")  # Default
```

#### Issue 2: Anthropic Implementation Unused

**Location:** `src/agent_kernel/services/llm.py`

`AnthropicLLMService` is fully implemented but no agent configs use it:

```yaml
# All configs use:
llm_config:
  provider: openai
```

**Recommendation:** Add Anthropic routing for specific use cases

| Use Case | Recommended Model | Rationale |
|----------|-------------------|-----------|
| Long context (100K+) | claude-3.5-sonnet | Better at long context |
| Code generation | claude-3.5-sonnet | Strong code capabilities |
| Structured output | gpt-4o | More reliable JSON |
| Cost-sensitive | gpt-4o-mini | Lowest cost |

#### Issue 3: No Caching for Identical Requests

Same intent + context → same LLM call → same cost.

**Recommendation:** Semantic caching

```python
class SemanticCache:
    """Cache LLM responses based on semantic similarity."""

    async def get_or_generate(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> str:
        # Hash the prompts
        cache_key = self._hash(system_prompt, user_prompt, model)

        # Check exact match
        if cached := await self._cache.get(cache_key):
            return cached

        # Check semantic similarity (for near-duplicates)
        embedding = await self._embed(user_prompt)
        if similar := await self._find_similar(embedding, threshold=0.95):
            return similar.response

        # Generate and cache
        response = await self._llm.generate(...)
        await self._cache.set(cache_key, response)
        return response
```

### Thinking Policy Optimization

The system has a sophisticated ThinkingPolicyController but it's underutilized:

```python
# CURRENT: Thinking policy passed but often defaults
thinking_policy = thinking_policy or self._default_policy()

# OPPORTUNITY: Use trace data to tune
historical_escalations = trace_store.get_escalation_rates(workflow_id)
if historical_escalations > 0.3:
    # This workflow often needs deep thinking
    return ThinkingPolicy(start_tier=2)
```

### LLM Recommendations Summary

| Priority | Issue | Current | Recommended | Effort |
|----------|-------|---------|-------------|--------|
| P1 | Static models | Fixed in YAML | Dynamic router | Medium |
| P1 | No Anthropic usage | Unused | Route specific tasks | Low |
| P1 | No caching | Every call hits API | Semantic cache | Medium |
| P2 | Thinking policy | Underutilized | Trace-based tuning | Medium |
| P2 | Token budgeting | None | Daily/workflow limits | Low |

---

## Part 4: Trace-Based Feedback Loop

### Current Tracing Capabilities

The system captures rich audit data but doesn't use it for self-improvement:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        What's Captured                                 │
├────────────────────────────────────────────────────────────────────────┤
│ ✓ Tool execution latency (duration_ms per call)                       │
│ ✓ Tool success/failure rates (CallStatus)                             │
│ ✓ Error categorization (ErrorRecord)                                  │
│ ✓ LLM invocations + tokens + cost (LLMCallRecord)                     │
│ ✓ Reasoning tier + escalation decisions (ReasoningMetadata)           │
│ ✓ Approval flow and policy enforcement                                │
│ ✓ Time-series aggregation (hourly/daily)                              │
├────────────────────────────────────────────────────────────────────────┤
│                        What's NOT Used                                 │
├────────────────────────────────────────────────────────────────────────┤
│ ✗ Feedback loop to tune model selection                               │
│ ✗ Automatic timeout adjustment based on historical latency            │
│ ✗ Approval policy expansion based on denial patterns                  │
│ ✗ Cost optimization based on usage patterns                           │
│ ✗ Anomaly detection for degraded performance                          │
└────────────────────────────────────────────────────────────────────────┘
```

### Recommended Feedback Loops

#### Loop 1: Performance-Based Timeout Tuning

```python
class AdaptiveTimeoutManager:
    """Tune timeouts based on historical latency."""

    async def get_timeout(self, capability_name: str) -> int:
        stats = await self._trace_store.get_tool_call_stats(
            capability_name=capability_name,
            since=datetime.utcnow() - timedelta(days=7),
        )

        # P99 + 20% buffer
        return int(stats["p99_duration_ms"] * 1.2)
```

#### Loop 2: Model Success Rate Routing

```python
class SuccessRateRouter:
    """Route to models with best success rates per task type."""

    async def select_model(self, workflow_id: str, task_type: str) -> str:
        # Query traces for this workflow
        summary = await self._trace_adapter.summarize_traces(
            workflow_id=workflow_id,
            since_hours=168,  # 7 days
            focus="performance",
        )

        model_rates = summary["model_success_rates"]
        # {"gpt-4o": 0.92, "gpt-4o-mini": 0.78, "gpt-5": 0.98}

        # Return best model within budget
        for model, rate in sorted(model_rates.items(), key=lambda x: -x[1]):
            if self._within_budget(model):
                return model
```

#### Loop 3: Automatic Approval Policy Expansion

```python
class ApprovalPolicyOptimizer:
    """Expand auto-approve based on historical safety."""

    async def suggest_expansions(self, agent_profile_id: str) -> list[str]:
        # Get denied approvals that were manually approved
        traces = await self._trace_store.list_traces(
            agent_profile_id=agent_profile_id,
        )

        safe_capabilities = []
        for trace in traces:
            for approval in trace.approvals:
                if (
                    approval.initially_denied and
                    approval.manually_approved and
                    approval.outcome_was_safe
                ):
                    safe_capabilities.append(approval.capability_name)

        # Recommend expanding if consistently safe
        return [cap for cap, count in Counter(safe_capabilities).items()
                if count > 10]
```

#### Loop 4: Cost Anomaly Detection

```python
class CostAnomalyDetector:
    """Detect unusual cost spikes."""

    async def check_anomalies(self) -> list[Anomaly]:
        # Get time series of costs
        series = await self._trace_adapter.summarize_traces(
            since_hours=168,
            focus="performance",
        )["time_series"]

        # Detect spikes (> 2 std dev from rolling mean)
        anomalies = []
        for point in series:
            if point["cost_usd"] > self._rolling_mean * 2:
                anomalies.append(Anomaly(
                    timestamp=point["time"],
                    metric="cost_usd",
                    value=point["cost_usd"],
                    expected=self._rolling_mean,
                ))

        return anomalies
```

### Experience Store Integration

The system has an Experience Store for learning but it's not wired up:

```python
# EXISTING but underutilized
experience_store.add_outcome_evaluation(trace_id, rating, feedback)
experience_store.add_case(case_summary, trace_ids)
experience_store.add_lesson(scope, guidance)
experience_store.add_playbook(workflow_id, pattern)
```

**Recommendation:** Wire up automated case mining

```python
class ExperienceMiner:
    """Mine traces for reusable patterns."""

    async def mine_cases(self, since_hours: int = 168):
        # Find successful traces with similar intents
        traces = await self._trace_store.list_traces(
            since=datetime.utcnow() - timedelta(hours=since_hours),
        )

        # Cluster by intent similarity
        clusters = self._cluster_by_intent(traces)

        # Extract patterns from successful clusters
        for cluster in clusters:
            if cluster.success_rate > 0.9:
                case = self._extract_case(cluster)
                await self._experience_store.add_case(case)
```

### Feedback Loop Recommendations Summary

| Priority | Loop | Current State | Implementation | Effort |
|----------|------|---------------|----------------|--------|
| P1 | Timeout tuning | Static in config | AdaptiveTimeoutManager | Low |
| P1 | Model routing | Fixed assignment | SuccessRateRouter | Medium |
| P2 | Approval expansion | Manual only | ApprovalPolicyOptimizer | Medium |
| P2 | Cost anomalies | None | CostAnomalyDetector | Low |
| P2 | Experience mining | Manual | ExperienceMiner | High |

---

## Part 5: Reusability & Hardening

### Current Reusability Strengths

1. **Clean Protocol Abstractions**: `VectorStore`, `GraphStore`, `AgentEngine` are swappable
2. **Schema Contracts**: All data flows through Pydantic models
3. **Tool Broker Centralization**: Single execution gateway enables consistent logging
4. **Configuration-Driven**: YAML specs for workflows, agents, capabilities

### Hardening Recommendations

#### 1. Add Circuit Breaker Pattern

```python
class CircuitBreaker:
    """Prevent cascading failures from external services."""

    def __init__(self, failure_threshold: int = 5, reset_timeout: int = 60):
        self._failures = 0
        self._state = "closed"

    async def execute(self, func: Callable) -> Any:
        if self._state == "open":
            raise CircuitOpenError()

        try:
            result = await func()
            self._failures = 0
            return result
        except Exception:
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._state = "open"
                asyncio.create_task(self._schedule_reset())
            raise
```

#### 2. Add Idempotency at Workflow Level

```python
class IdempotentWorkflowRunner:
    """Prevent duplicate workflow executions."""

    async def run(self, workflow_id: str, idempotency_key: str, ...):
        # Check if already executed
        existing = await self._store.get_by_idempotency_key(idempotency_key)
        if existing:
            return existing.result

        # Execute with idempotency tracking
        result = await super().run(...)
        await self._store.save_idempotency(idempotency_key, result)
        return result
```

#### 3. Add Health Checks

```python
class HealthChecker:
    """Verify system components are operational."""

    async def check_all(self) -> HealthReport:
        return HealthReport(
            document_store=await self._check_document_store(),
            vector_store=await self._check_vector_store(),
            graph_store=await self._check_graph_store(),
            llm_service=await self._check_llm_service(),
            event_log=await self._check_event_log(),
        )

    async def _check_vector_store(self) -> ComponentHealth:
        try:
            count = await self._vector_store.count()
            return ComponentHealth(status="healthy", metadata={"count": count})
        except Exception as e:
            return ComponentHealth(status="unhealthy", error=str(e))
```

#### 4. Add Rate Limiting

```python
class RateLimiter:
    """Prevent API abuse and control costs."""

    def __init__(self, requests_per_minute: int = 60):
        self._requests = []
        self._limit = requests_per_minute

    async def acquire(self):
        now = time.time()
        self._requests = [t for t in self._requests if now - t < 60]

        if len(self._requests) >= self._limit:
            wait_time = 60 - (now - self._requests[0])
            await asyncio.sleep(wait_time)

        self._requests.append(now)
```

### Hardening Recommendations Summary

| Priority | Area | Current State | Recommendation | Effort |
|----------|------|---------------|----------------|--------|
| P1 | Circuit breaker | None | Add to external calls | Medium |
| P1 | Idempotency | Partial | Workflow-level dedup | Low |
| P2 | Health checks | None | HealthChecker class | Low |
| P2 | Rate limiting | None | RateLimiter class | Low |
| P2 | Graceful shutdown | Basic | Add drain period | Medium |

---

## Implementation Roadmap

### Phase 1: Critical Fixes (1-2 weeks effort)

1. **Replace Vector Store** → LanceDB with HNSW
2. **Add Workflow Persistence** → SQLiteWorkflowRunStore
3. **Cache Configuration** → LRU with mtime invalidation

### Phase 2: Optimization (2-4 weeks effort)

1. **Implement Checkpoint Resumption** → True step-by-step resume
2. **Add Semantic Caching** → Reduce duplicate LLM calls
3. **Batch Graph Queries** → Recursive CTE for subgraph

### Phase 3: Feedback Loops (4-6 weeks effort)

1. **Wire up Experience Store** → Automated case mining
2. **Add Model Router** → Trace-based model selection
3. **Implement Cost Tracking** → Per-workflow budgets

### Phase 4: Hardening (2-3 weeks effort)

1. **Circuit Breaker** → For external API calls
2. **Health Checks** → Component verification
3. **Rate Limiting** → API abuse prevention

---

## Conclusion

The agent-kernel has strong architectural foundations but needs production hardening:

| Category | Grade | Key Gap |
|----------|-------|---------|
| **Memory** | C+ | Vector search doesn't scale |
| **Workflows** | B- | No persistent checkpointing |
| **LLM Selection** | B | Static assignment, no caching |
| **Tracing** | A- | Rich data, underutilized |
| **Reusability** | A | Clean abstractions |

**Top 3 Actions:**
1. Replace SQLite vector store with LanceDB (P0)
2. Add workflow state persistence with checkpoint resumption (P0)
3. Implement trace-based feedback loops for cost/performance optimization (P1)

These changes will transform the system from a prototype to a production-ready personal agent platform.
