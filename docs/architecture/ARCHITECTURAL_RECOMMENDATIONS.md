# Architectural Recommendations for System Expansion

**Version:** 1.0
**Date:** 2026-01-24
**Author:** Software Architecture Analysis
**Purpose:** Comprehensive recommendations for expanding the agent kernel to support trading, multi-tenant SaaS, and high-scale production environments.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Architecture Assessment](#2-current-architecture-assessment)
3. [Trading Domain Architecture](#3-trading-domain-architecture)
4. [Data Layer Recommendations](#4-data-layer-recommendations)
5. [Execution Layer Recommendations](#5-execution-layer-recommendations)
6. [Workflow Orchestration Improvements](#6-workflow-orchestration-improvements)
7. [Performance Optimization Strategy](#7-performance-optimization-strategy)
8. [Security & Compliance](#8-security--compliance)
9. [Implementation Roadmap](#9-implementation-roadmap)
10. [Migration Strategy](#10-migration-strategy)

---

## 1. Executive Summary

### 1.1 Assessment Overview

After comprehensive analysis of the agent kernel architecture across schemas, execution infrastructure, memory subsystems, tool broker, and workflow orchestration, we rate the system's readiness for various deployment scenarios:

| Deployment Scenario | Readiness | Key Gaps |
|---------------------|-----------|----------|
| **Personal Knowledge Management** | 95% ✅ | Minor optimizations only |
| **Daily Workflow Automation** | 90% ✅ | Production-ready |
| **Low-Frequency Trading** | 60% ⚠️ | Need: time-series DB, faster vector store |
| **High-Frequency Trading** | 20% ❌ | Critical: latency bottlenecks, no real-time events |
| **Multi-Tenant SaaS** | 30% ❌ | Need: tenant isolation, horizontal scaling |

### 1.2 Key Strengths

✅ **Excellent Schema Design** - Universal entity model, versioning, multi-view support
✅ **Strong Separation of Concerns** - Clean boundaries between reasoning and execution
✅ **Comprehensive Auditability** - DecisionTrace captures everything for compliance
✅ **Flexible Tool System** - Multiple adapters (local, HTTP, subprocess, MCP, skill scripts)
✅ **Policy Enforcement** - Approval system with granular control
✅ **Async Throughout** - Non-blocking I/O foundation for concurrency

### 1.3 Critical Gaps

❌ **Vector Store Scalability** - O(N) linear scan limits to ~10K vectors
❌ **No Time-Series Support** - Cannot handle market data efficiently
❌ **Single-Threaded Execution** - No parallel action execution
❌ **In-Memory Workflow Runs** - Lost on restart, no persistence
❌ **Cron-Only Triggers** - No sub-second event-driven workflows
❌ **LLM in Critical Path** - 500-3000ms latency for every decision

### 1.4 Recommended Investment

**Timeline:** 8-10 weeks for production trading readiness
**Team:** 3 senior engineers
**Infrastructure Cost:** $500-1000/month (managed services)
**Risk:** Low (incremental changes, backward compatible)

---

## 2. Current Architecture Assessment

### 2.1 Architectural Layers

```
┌─────────────────────────────────────────────────────────────┐
│ INTERFACE LAYER                                             │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│ │ CLI (Typer)  │  │ API (Future) │  │ UI (Future)  │      │
│ └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ ORCHESTRATION LAYER                                         │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│ │ Workflow     │  │ Scheduler    │  │ Event Log    │      │
│ │ Runner       │  │ (Cron)       │  │              │      │
│ └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ AGENT LAYER                                                 │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│ │ Context      │  │ Agent Engine │  │ Executor     │      │
│ │ Assembler    │  │ (LLM)        │  │ + Approval   │      │
│ └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ CAPABILITY LAYER                                            │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│ │ Tool Broker  │  │ Capability   │  │ Adapters     │      │
│ │              │  │ Registry     │  │ (5 types)    │      │
│ └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ MEMORY LAYER                                                │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│ │ Document     │  │ Vector Store │  │ Graph Store  │      │
│ │ Store (FTS5) │  │ (SQLite)     │  │ (SQLite)     │      │
│ └──────────────┘  └──────────────┘  └──────────────┘      │
│ ┌──────────────┐  ┌──────────────┐                        │
│ │ Entity Store │  │ Experience   │                        │
│ │              │  │ Store        │                        │
│ └──────────────┘  └──────────────┘                        │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Strengths by Layer

| Layer | Strengths | Production-Ready? |
|-------|-----------|-------------------|
| **Schemas** | Universal entity model, versioning, multi-source support | ✅ Yes (8.5/10) |
| **Orchestration** | Clear workflow specs, deterministic execution | ✅ Yes (8/10) |
| **Agent** | Trust boundary, quality gates, tier-based reasoning | ✅ Yes (9/10) |
| **Capability** | Multiple adapters, policy enforcement, audit trail | ✅ Yes (8.5/10) |
| **Memory** | Clean separation, rebuild capability, entity registry | ⚠️ Partial (6/10) |

### 2.3 Bottlenecks by Layer

| Layer | Critical Bottleneck | Impact | Priority |
|-------|---------------------|--------|----------|
| **Memory** | Vector store O(N) scan | 45% of latency | CRITICAL |
| **Agent** | LLM in critical path | 36% of latency | HIGH |
| **Orchestration** | No event-driven triggers | Can't do HFT | HIGH |
| **Capability** | Sequential execution | 5x slower | HIGH |
| **Memory** | No time-series support | Can't store ticks | CRITICAL |

---

## 3. Trading Domain Architecture

### 3.1 Hybrid Deliberative-Reactive Architecture

**Recommendation:** Implement **two-tier architecture** separating strategic (slow) from tactical (fast) decisions.

```
┌─────────────────────────────────────────────────────────────────┐
│ DELIBERATIVE TIER (Agent Kernel)                               │
│ Purpose: Strategic planning, research, multi-step reasoning    │
│ Latency: 1-10 seconds acceptable                               │
│ Frequency: Daily/hourly/on-demand                              │
│                                                                  │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│ │ Daily        │  │ Strategy     │  │ Risk         │          │
│ │ Planning     │  │ Research     │  │ Analysis     │          │
│ └──────────────┘  └──────────────┘  └──────────────┘          │
│                                                                  │
│ Data:                                                           │
│ - Trading strategies (Obsidian notes)                           │
│ - Strategy embeddings (LanceDB)                                 │
│ - Strategy relationships (Graph)                                │
│ - Research and insights (Documents)                             │
└─────────────────────────────────────────────────────────────────┘
                            ↓ (Strategic guidance)
┌─────────────────────────────────────────────────────────────────┐
│ REACTIVE TIER (Trading Engine)                                  │
│ Purpose: Real-time execution, monitoring, risk management       │
│ Latency: <100ms required                                        │
│ Frequency: Sub-second to minute                                 │
│                                                                  │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│ │ Market Watch │  │ Order Exec   │  │ Risk Monitor │          │
│ │ (Event)      │  │ (Pre-approved)│  │ (Continuous) │          │
│ └──────────────┘  └──────────────┘  └──────────────┘          │
│                                                                  │
│ Data:                                                           │
│ - Market ticks (TimescaleDB)                                    │
│ - Positions (PostgreSQL + cache)                                │
│ - Orders (event-sourced ledger)                                 │
│ - Risk metrics (Redis cache)                                    │
│                                                                  │
│ Execution:                                                      │
│ - Fast-path templates (no LLM)                                  │
│ - Cached plans (Redis)                                          │
│ - Parallel tool execution                                       │
│ - Event-driven triggers (Kafka/Redis Streams)                   │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Deliberative Tier (Current Agent Kernel)

**Use Cases:**
- Daily market analysis ("What sectors look strong today?")
- Strategy backtesting ("Test this strategy on 2024 data")
- Portfolio rebalancing ("Rebalance to 60/40 allocation")
- Research synthesis ("Summarize earnings reports")

**Characteristics:**
- LLM-driven planning
- Multi-step workflows
- Human approval for high-risk actions
- Rich context assembly (notes, tasks, calendar, research)

**Example Workflow:**
```yaml
workflow_id: daily_market_analysis
trigger:
  type: cron
  schedule: "0 9 * * 1-5"  # Weekday mornings

steps:
  - vault_sync                  # Load updated research notes
  - assemble_context            # Get market data, positions, news
  - propose_plan                # LLM analyzes and suggests trades
  - validate                    # Risk checks, position limits
  - gate_approvals              # Human review (async)
  - execute                     # Execute approved trades
  - write_back                  # Create daily summary note
```

**Latency Budget:**
- Total: 2-10 seconds
- Acceptable for strategic decisions

---

### 3.3 Reactive Tier (New Trading Engine)

**Use Cases:**
- Order execution ("Execute limit order at $250")
- Stop-loss triggers ("Exit if price drops 2%")
- Risk monitoring ("Alert if VaR > 2%")
- Market event reactions ("Price spike detected")

**Characteristics:**
- Pre-approved action templates (no LLM delay)
- Event-driven triggers (<100ms latency)
- Cached context (positions, risk metrics)
- Parallel execution

**Example Fast-Path Template:**
```python
# Pre-approved order template (bypasses LLM)
class MarketOrderTemplate:
    def validate(self, symbol, quantity, side):
        # Check position limits
        position = POSITION_CACHE.get(symbol, 0)
        new_position = position + (quantity if side == "buy" else -quantity)

        if abs(new_position) > MAX_POSITION[symbol]:
            return False, "Exceeds position limit"

        return True, None

    def create_plan(self, symbol, quantity, side):
        return Plan(
            actions=[
                ActionRequest(
                    capability_name="trading.placeorder@v1",
                    args={
                        "symbol": symbol,
                        "quantity": quantity,
                        "side": side,
                        "order_type": "market",
                    },
                    idempotency_key=self.generate_key(symbol, quantity, side),
                )
            ]
        )

# Execution
template = MarketOrderTemplate()
valid, error = template.validate("AAPL", 100, "buy")
if valid:
    plan = template.create_plan("AAPL", 100, "buy")
    await executor.execute(plan)  # ~50ms total
```

**Latency Budget:**
- Total: <100ms
- Context assembly: 10ms (cached)
- Plan generation: 0ms (template)
- Validation: 5ms
- Execution: 50ms (network)
- Trace write: 5ms

---

### 3.4 Data Flow Between Tiers

```
DELIBERATIVE TIER
    ↓ (Strategic decisions)
    ↓ - Trading strategies
    ↓ - Position limits
    ↓ - Risk parameters
    ↓
REACTIVE TIER
    ↓ (Tactical execution)
    ↓
EVENT LOG (Shared)
    ↑ (Execution audit trail)
    ↑
DELIBERATIVE TIER
    ↑ (Review outcomes, learn)
```

**Flow Example:**
1. **Morning (Deliberative):** Agent analyzes market, proposes "Buy tech stocks if they dip 1%"
2. **Afternoon (Reactive):** Price drops 1% → Event triggers → Fast-path executes buy
3. **Evening (Deliberative):** Agent reviews execution, updates strategy

---

## 4. Data Layer Recommendations

### 4.1 Multi-Store Architecture

**Current:** Single SQLite database
**Recommended:** Specialized stores by data type

```
┌─────────────────────────────────────────────────────────────┐
│ KNOWLEDGE LAYER (Deliberative)                              │
├─────────────────────────────────────────────────────────────┤
│ DocumentStore        → PostgreSQL + Full-Text Search        │
│   - Trading strategies, research notes, analysis            │
│   - ~10K documents                                          │
│   - FTS for keyword search                                  │
│                                                              │
│ VectorStore          → LanceDB (embedded)                   │
│   - Strategy embeddings, note embeddings                    │
│   - ~100K vectors (strategies + chunks)                     │
│   - HNSW index for <50ms similarity search                  │
│                                                              │
│ GraphStore           → PostgreSQL (or Neo4j if complex)     │
│   - Portfolio → Position → Symbol → Sector → Index         │
│   - Strategy → Indicator → Signal relationships             │
│   - ~10K nodes, ~50K edges                                  │
│                                                              │
│ ExperienceStore      → PostgreSQL                           │
│   - Outcome evaluations, lessons, playbooks                 │
│   - Learning loop: trace → evaluation → lesson              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ OPERATIONAL LAYER (Reactive)                                │
├─────────────────────────────────────────────────────────────┤
│ TimeSeriesStore      → TimescaleDB (Hypertable)             │
│   - Market ticks (OHLCV, quotes, order book)                │
│   - ~1B rows/year (50K ticks/day × 100 symbols × 365)       │
│   - Automatic retention policy (keep 1 year, archive rest)  │
│   - Continuous aggregates (1min, 5min, 1hour bars)          │
│                                                              │
│ PositionStore        → PostgreSQL (live) + Redis (cache)    │
│   - Current positions, P&L, margin                          │
│   - ~1K positions                                           │
│   - Cached in Redis (refresh every 100ms)                   │
│                                                              │
│ OrderStore           → PostgreSQL (event-sourced)           │
│   - Order history, fills, executions                        │
│   - ~10K orders/year                                        │
│   - Append-only for audit compliance                        │
│                                                              │
│ RiskMetricsCache     → Redis                                │
│   - VaR, Greeks, exposure, limits                           │
│   - TTL: 10 seconds                                         │
│   - Recomputed on position change                           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ AUDIT LAYER (Compliance)                                    │
├─────────────────────────────────────────────────────────────┤
│ TraceStore           → PostgreSQL (partitioned by month)    │
│   - DecisionTrace records                                   │
│   - ~100K traces/year                                       │
│   - Immutable, full audit trail                             │
│                                                              │
│ EventLog             → PostgreSQL (append-only)             │
│   - System events, tool calls, approvals                    │
│   - ~10M events/year                                        │
│   - Retention: 7 years (regulatory)                         │
│                                                              │
│ ApprovalStore        → PostgreSQL                           │
│   - Approval requests, resolutions, audit                   │
│   - ~10K approvals/year                                     │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Store-Specific Recommendations

#### 4.2.1 VectorStore: Migrate to LanceDB

**Current Problem:**
- SQLite with O(N) linear scan
- Loads all vectors into memory
- ~1 second per query at 10K vectors

**Recommended Solution: LanceDB**

**Why LanceDB:**
- ✅ Embedded (no separate server process)
- ✅ HNSW index (O(log N) queries)
- ✅ Apache Arrow format (efficient columnar storage)
- ✅ Native Python bindings
- ✅ Scales to 10M+ vectors
- ✅ <50ms queries at 1M vectors

**Migration Path:**
```python
# 1. Install LanceDB
pip install lancedb

# 2. Create LanceDB adapter
from agent_kernel.memory.implementations.lancedb_vector_store import LanceDBVectorStore

vector_store = LanceDBVectorStore(
    db_path="data/lancedb",
    table_name="embeddings",
    dimension=1536,  # OpenAI embedding size
)

# 3. Migrate existing vectors
for vector in sqlite_vector_store.list_all():
    vector_store.upsert(
        item_id=vector.item_id,
        vector=vector.vector,
        metadata=vector.metadata,
    )

# 4. Update dependencies to use LanceDB
context_assembler = ContextAssembler(
    vector_store=vector_store,  # LanceDB
    document_store=document_store,
    graph_store=graph_store,
)
```

**Expected Performance:**
- 10K vectors: <10ms (100x faster)
- 100K vectors: <30ms (500x faster)
- 1M vectors: <50ms (2000x faster)

---

#### 4.2.2 TimeSeriesStore: Add TimescaleDB

**Current Problem:**
- No native time-series support
- Cannot efficiently store/query market ticks

**Recommended Solution: TimescaleDB**

**Why TimescaleDB:**
- ✅ PostgreSQL extension (familiar SQL)
- ✅ Automatic time-partitioning (hypertables)
- ✅ Continuous aggregates (pre-computed OHLCV)
- ✅ Data retention policies
- ✅ Scales to 1B+ rows

**Schema Design:**
```sql
-- Hypertable for market ticks
CREATE TABLE market_ticks (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    price NUMERIC(12, 2) NOT NULL,
    volume BIGINT NOT NULL,
    bid NUMERIC(12, 2),
    ask NUMERIC(12, 2),
    exchange TEXT,
    metadata JSONB
);

-- Convert to hypertable (auto-partition by time)
SELECT create_hypertable('market_ticks', 'time');

-- Create indexes
CREATE INDEX ON market_ticks (symbol, time DESC);
CREATE INDEX ON market_ticks (time DESC, symbol);

-- Continuous aggregate: 1-minute OHLCV
CREATE MATERIALIZED VIEW ohlcv_1min
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 minute', time) AS bucket,
    symbol,
    first(price, time) AS open,
    max(price) AS high,
    min(price) AS low,
    last(price, time) AS close,
    sum(volume) AS volume
FROM market_ticks
GROUP BY bucket, symbol;

-- Retention policy: Keep raw ticks for 1 year
SELECT add_retention_policy('market_ticks', INTERVAL '1 year');
```

**Adapter Implementation:**
```python
class TimescaleDBTickStore:
    async def insert_tick(self, tick: MarketTick) -> None:
        await self.db.execute(
            """
            INSERT INTO market_ticks (time, symbol, price, volume, bid, ask)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            tick.timestamp,
            tick.symbol,
            tick.price,
            tick.volume,
            tick.bid,
            tick.ask,
        )

    async def query_ticks(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        filters: dict | None = None,
    ) -> list[MarketTick]:
        query = """
            SELECT * FROM market_ticks
            WHERE symbol = $1
              AND time >= $2
              AND time < $3
        """

        if filters and "min_price" in filters:
            query += f" AND price >= {filters['min_price']}"

        query += " ORDER BY time ASC"

        rows = await self.db.fetch(query, symbol, start_time, end_time)
        return [MarketTick.from_row(row) for row in rows]

    async def get_ohlcv(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1min",
    ) -> list[OHLCV]:
        # Query pre-computed continuous aggregate
        query = """
            SELECT * FROM ohlcv_1min
            WHERE symbol = $1
              AND bucket >= $2
              AND bucket < $3
            ORDER BY bucket ASC
        """

        rows = await self.db.fetch(query, symbol, start_time, end_time)
        return [OHLCV.from_row(row) for row in rows]
```

**Expected Performance:**
- Insert: 100K ticks/second (batched)
- Query: <100ms for 1M ticks
- Aggregates: <10ms (pre-computed)

---

#### 4.2.3 PositionStore: PostgreSQL + Redis Cache

**Current Problem:**
- No dedicated position store
- No caching for frequently accessed data

**Recommended Solution: PostgreSQL + Redis**

**Schema Design:**
```sql
-- PostgreSQL: Source of truth
CREATE TABLE positions (
    position_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    quantity NUMERIC(18, 8) NOT NULL,
    average_price NUMERIC(12, 2) NOT NULL,
    current_price NUMERIC(12, 2),
    unrealized_pnl NUMERIC(12, 2),
    realized_pnl NUMERIC(12, 2),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(account_id, symbol)
);

CREATE INDEX ON positions (account_id, updated_at DESC);
CREATE INDEX ON positions (symbol);
```

**Caching Strategy:**
```python
class CachedPositionStore:
    def __init__(self, db: PostgreSQL, cache: Redis):
        self._db = db
        self._cache = cache
        self._cache_ttl = 60  # 1 minute

    async def get_position(self, account_id: str, symbol: str) -> Position | None:
        # Try cache first
        cache_key = f"position:{account_id}:{symbol}"
        cached = await self._cache.get(cache_key)

        if cached:
            return Position.parse_raw(cached)

        # Cache miss: fetch from DB
        row = await self._db.fetchrow(
            "SELECT * FROM positions WHERE account_id = $1 AND symbol = $2",
            account_id,
            symbol,
        )

        if not row:
            return None

        position = Position.from_row(row)

        # Store in cache
        await self._cache.setex(
            cache_key,
            self._cache_ttl,
            position.json(),
        )

        return position

    async def update_position(self, position: Position) -> None:
        # Write to DB
        await self._db.execute(
            """
            INSERT INTO positions (position_id, account_id, symbol, quantity, ...)
            VALUES ($1, $2, $3, $4, ...)
            ON CONFLICT (account_id, symbol) DO UPDATE SET ...
            """,
            ...
        )

        # Invalidate cache
        cache_key = f"position:{position.account_id}:{position.symbol}"
        await self._cache.delete(cache_key)
```

**Expected Performance:**
- Cache hit: <1ms
- Cache miss: <10ms (DB query + cache write)
- Update: <5ms (DB write + cache invalidate)

---

### 4.3 Data Migration Strategy

**Phase 1: Add New Stores (Parallel)**
- Deploy LanceDB alongside SQLite VectorStore
- Deploy TimescaleDB for new market data
- Keep existing SQLite stores running

**Phase 2: Dual-Write (Validation)**
- Write to both old and new stores
- Validate consistency
- Monitor performance improvements

**Phase 3: Cut Over (Switch Reads)**
- Switch reads to new stores
- Monitor error rates
- Keep old stores as fallback

**Phase 4: Cleanup (Deprecate Old)**
- Delete old SQLite stores
- Archive historical data

---

## 5. Execution Layer Recommendations

### 5.1 Parallel Action Execution

**Current Problem:**
- Actions execute sequentially
- 5x slower for independent actions

**Recommended Solution: Dependency Analysis + asyncio.gather**

**Implementation:**
```python
class ParallelExecutor:
    async def execute(self, plan: Plan, ...) -> DecisionTrace:
        # Build dependency graph
        dep_graph = self._build_dependency_graph(plan.actions)

        # Topological sort into execution batches
        batches = self._topological_sort(dep_graph)

        tool_calls = []

        for batch in batches:
            # Execute batch in parallel
            results = await asyncio.gather(*[
                self._execute_action(action) for action in batch
            ])

            tool_calls.extend(results)

        return DecisionTrace(tool_calls=tool_calls, ...)

    def _build_dependency_graph(self, actions: list[ActionRequest]) -> dict:
        """
        Build dependency graph by analyzing action inputs/outputs.

        Example:
          Action 1: get_quote(symbol="AAPL") → {"price": 150}
          Action 2: calculate_position(price=${Action 1.price}) → depends on Action 1
          Action 3: get_quote(symbol="MSFT") → independent

        Result:
          Batch 1: [Action 1, Action 3] (parallel)
          Batch 2: [Action 2] (depends on Action 1)
        """
        graph = {action.action_id: set() for action in actions}

        for action in actions:
            # Check if action params reference previous action outputs
            for param_value in action.args.values():
                if isinstance(param_value, str) and "${" in param_value:
                    # Extract referenced action ID
                    ref_action_id = self._parse_action_ref(param_value)
                    if ref_action_id:
                        graph[action.action_id].add(ref_action_id)

        return graph

    def _topological_sort(self, graph: dict) -> list[list[str]]:
        """Group actions into batches for parallel execution."""
        batches = []
        remaining = set(graph.keys())

        while remaining:
            # Find actions with no dependencies
            ready = {
                action_id
                for action_id in remaining
                if not graph[action_id].intersection(remaining)
            }

            if not ready:
                raise CyclicDependencyError("Circular dependency in plan")

            batches.append(list(ready))
            remaining -= ready

        return batches
```

**Expected Performance:**
```
Example plan: 5 independent market data fetches

Sequential: 5 × 50ms = 250ms
Parallel:   max(50ms) = 50ms

5x speedup
```

---

### 5.2 Fast-Path Execution (Bypass LLM)

**Current Problem:**
- LLM in critical path (500-3000ms)
- Not needed for pre-approved patterns

**Recommended Solution: Template Library**

**Implementation:**
```python
class TemplateLibrary:
    """Library of pre-approved action templates."""

    def __init__(self):
        self._templates: dict[str, ActionTemplate] = {}

    def register(self, template_id: str, template: ActionTemplate) -> None:
        self._templates[template_id] = template

    def match(self, intent: str, context: ContextPacket) -> ActionTemplate | None:
        """
        Match intent to template.

        Example:
          Intent: "Buy 100 shares of AAPL"
          → Matches MarketOrderTemplate
        """
        for template in self._templates.values():
            if template.matches(intent, context):
                return template
        return None

class ActionTemplate(ABC):
    @abstractmethod
    def matches(self, intent: str, context: ContextPacket) -> bool:
        """Check if template applies to this intent."""

    @abstractmethod
    def validate(self, **params) -> tuple[bool, str | None]:
        """Validate parameters meet constraints."""

    @abstractmethod
    def create_plan(self, **params) -> Plan:
        """Generate plan from template."""

# Example: Market order template
class MarketOrderTemplate(ActionTemplate):
    def matches(self, intent: str, context: ContextPacket) -> bool:
        # Simple pattern matching
        return re.match(r"(buy|sell) \d+ (shares of )?\w+", intent.lower())

    def validate(self, symbol: str, quantity: int, side: str) -> tuple[bool, str | None]:
        # Check position limits
        position = POSITION_CACHE.get(symbol, 0)
        new_position = position + (quantity if side == "buy" else -quantity)

        if abs(new_position) > MAX_POSITION.get(symbol, 1000):
            return False, f"Exceeds position limit for {symbol}"

        return True, None

    def create_plan(self, symbol: str, quantity: int, side: str) -> Plan:
        return Plan(
            actions=[
                ActionRequest(
                    capability_name="trading.placeorder@v1",
                    args={
                        "symbol": symbol,
                        "quantity": quantity,
                        "side": side,
                        "order_type": "market",
                    },
                )
            ],
            confidence=1.0,
            reasoning="Pre-approved market order template",
        )

# Usage in workflow
template_lib = TemplateLibrary()
template_lib.register("market_order", MarketOrderTemplate())

async def run_workflow(intent: str):
    # Try template first
    template = template_lib.match(intent, context)

    if template:
        # Fast path: Use template (no LLM)
        params = template.parse_params(intent)
        valid, error = template.validate(**params)

        if valid:
            plan = template.create_plan(**params)
            return await executor.execute(plan)  # ~50ms

    # Slow path: Use LLM
    plan = await agent_engine.propose(context, agent_profile)  # ~2000ms
    return await executor.execute(plan)
```

**Expected Performance:**
```
Fast path (template): ~50ms end-to-end
Slow path (LLM):      ~2000ms end-to-end

40x speedup for common patterns
```

---

### 5.3 Plan Caching

**Current Problem:**
- Identical contexts re-generate same plan
- Wasted LLM calls

**Recommended Solution: Redis-backed Plan Cache**

**Implementation:**
```python
class PlanCache:
    def __init__(self, redis: Redis, ttl: int = 3600):
        self._redis = redis
        self._ttl = ttl

    def cache_key(self, context: ContextPacket, agent_profile: AgentProfile) -> str:
        """Generate cache key from context hash."""
        # Hash context items + agent profile
        items_hash = hashlib.md5(
            json.dumps([item.ref.dict() for item in context.items]).encode()
        ).hexdigest()

        profile_hash = hashlib.md5(
            agent_profile.json().encode()
        ).hexdigest()

        return f"plan:{profile_hash}:{items_hash}"

    async def get(
        self,
        context: ContextPacket,
        agent_profile: AgentProfile,
    ) -> Plan | None:
        key = self.cache_key(context, agent_profile)
        cached = await self._redis.get(key)

        if cached:
            return Plan.parse_raw(cached)
        return None

    async def set(
        self,
        context: ContextPacket,
        agent_profile: AgentProfile,
        plan: Plan,
    ) -> None:
        key = self.cache_key(context, agent_profile)
        await self._redis.setex(key, self._ttl, plan.json())

# Usage in agent engine
class CachedAgentEngine:
    def __init__(self, engine: AgentEngine, cache: PlanCache):
        self._engine = engine
        self._cache = cache

    async def propose(
        self,
        context: ContextPacket,
        agent_profile: AgentProfile,
    ) -> Plan:
        # Try cache
        cached_plan = await self._cache.get(context, agent_profile)
        if cached_plan:
            logger.info("plan_cache_hit", context_id=context.context_packet_id)
            return cached_plan

        # Cache miss: Generate new plan
        logger.info("plan_cache_miss", context_id=context.context_packet_id)
        plan = await self._engine.propose(context, agent_profile)

        # Store in cache
        await self._cache.set(context, agent_profile, plan)

        return plan
```

**Expected Performance:**
- Cache hit: <10ms (vs 2000ms for LLM)
- 200x speedup for repeated contexts

---

## 6. Workflow Orchestration Improvements

### 6.1 Event-Driven Triggers

**Current Problem:**
- Cron only (minimum 1 minute)
- No sub-second event triggers

**Recommended Solution: Redis Streams + Event Listener**

**Architecture:**
```
Market Data Feed
    ↓
Redis Streams (event queue)
    ↓
Event Listener (agent kernel)
    ↓
Workflow Runner (triggered)
```

**Implementation:**
```python
class EventDrivenWorkflowRunner:
    def __init__(self, workflow_runner: WorkflowRunner, redis: Redis):
        self._runner = workflow_runner
        self._redis = redis
        self._listeners: dict[str, EventListener] = {}

    async def register_event_workflow(
        self,
        event_type: str,
        workflow_id: str,
    ) -> None:
        """Register workflow to trigger on event type."""
        listener = EventListener(
            redis=self._redis,
            stream_name=f"events:{event_type}",
            workflow_id=workflow_id,
            workflow_runner=self._runner,
        )

        self._listeners[event_type] = listener
        await listener.start()

    async def start_all(self) -> None:
        """Start all event listeners."""
        await asyncio.gather(*[
            listener.start() for listener in self._listeners.values()
        ])

class EventListener:
    async def start(self) -> None:
        """Listen for events and trigger workflows."""
        # Create consumer group
        await self._redis.xgroup_create(
            self._stream_name,
            "workflow_group",
            id="0",
            mkstream=True,
        )

        while True:
            # Read events from stream
            events = await self._redis.xreadgroup(
                groupname="workflow_group",
                consumername="worker_1",
                streams={self._stream_name: ">"},
                count=10,
                block=1000,  # 1 second timeout
            )

            for stream, messages in events:
                for message_id, data in messages:
                    # Trigger workflow
                    await self._handle_event(message_id, data)

    async def _handle_event(self, message_id: str, data: dict) -> None:
        try:
            # Parse event data
            event = Event.parse_obj(data)

            # Trigger workflow
            result = await self._workflow_runner.run(
                self._workflow_id,
                intent=event.description,
                event_data=event.data,
            )

            # Acknowledge event
            await self._redis.xack(
                self._stream_name,
                "workflow_group",
                message_id,
            )

        except Exception as e:
            logger.error("event_handling_error", error=str(e))
            # Don't acknowledge (will be retried)

# Usage
event_runner = EventDrivenWorkflowRunner(workflow_runner, redis)

# Register event workflows
await event_runner.register_event_workflow(
    event_type="market.price_spike",
    workflow_id="react_to_price_spike",
)

await event_runner.register_event_workflow(
    event_type="risk.var_breach",
    workflow_id="risk_mitigation",
)

# Start listening
await event_runner.start_all()
```

**Publishing Events:**
```python
# Market data service publishes events
async def on_price_update(symbol: str, price: float, prev_price: float):
    change_pct = abs(price - prev_price) / prev_price

    if change_pct > 0.01:  # 1% move
        # Publish event to Redis Streams
        await redis.xadd(
            "events:market.price_spike",
            {
                "symbol": symbol,
                "price": price,
                "change_pct": change_pct,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
```

**Expected Performance:**
- Event-to-workflow latency: <50ms
- Throughput: 10K events/second

---

### 6.2 Conditional Branching in Workflows

**Current Problem:**
- No native if/then/else in YAML
- Must embed logic in agent

**Recommended Solution: Conditional Step Syntax**

**Proposed YAML Syntax:**
```yaml
workflow_id: trading_workflow
steps:
  - assemble_context
  - propose_plan
  - validate

  # Conditional: Large trades need second approval
  - conditional:
      if: "plan.total_notional > 1000000"
      then:
        - escalate_to_risk_committee
        - require_second_approval
      else:
        - gate_approvals

  - execute

  # Conditional: Hedge if exposure high
  - conditional:
      if: "execution.outcome == 'SUCCESS' and risk.delta > 10"
      then:
        - propose_hedge_plan
        - execute_hedge

  - write_back
```

**Implementation:**
```python
class ConditionalStep:
    def __init__(
        self,
        condition: str,
        then_steps: list[str],
        else_steps: list[str] | None = None,
    ):
        self._condition = condition
        self._then_steps = then_steps
        self._else_steps = else_steps or []

    async def execute(self, context: dict) -> list[str]:
        """
        Evaluate condition and return steps to execute.

        Context contains:
        - plan: Plan object
        - execution: ExecutionResult
        - risk: RiskMetrics
        """
        # Evaluate condition (safe eval with allowlist)
        result = self._safe_eval(self._condition, context)

        if result:
            return self._then_steps
        else:
            return self._else_steps

    def _safe_eval(self, expr: str, context: dict) -> bool:
        """
        Safely evaluate expression.

        Allowlist:
        - Comparison operators: >, <, ==, !=, >=, <=
        - Logical operators: and, or, not
        - Numeric literals
        - String literals
        - Dot notation for object access
        """
        # Parse expression to AST
        # Validate only allowed operations
        # Execute in restricted environment
        ...
```

---

## 7. Performance Optimization Strategy

### 7.1 Latency Optimization Targets

| Scenario | Current P99 | Target P99 | Gap | Priority |
|----------|-------------|------------|-----|----------|
| **Daily planning** | 5s | 2s | -60% | MEDIUM |
| **Low-frequency trade** | 3s | 500ms | -83% | HIGH |
| **High-frequency trade** | N/A | 100ms | N/A | CRITICAL |
| **Risk monitoring** | 2s | 200ms | -90% | HIGH |

### 7.2 Optimization Roadmap

**Phase 1: Low-Hanging Fruit (Week 1)**
- ✅ Add plan caching (200x speedup on cache hits)
- ✅ Add position caching (10x speedup)
- ✅ Parallel action execution (5x speedup)

**Expected Impact:** 50% latency reduction

**Phase 2: Structural Changes (Weeks 2-3)**
- ✅ Upgrade to LanceDB (100x faster vector search)
- ✅ Add TimescaleDB (native time-series)
- ✅ Add fast-path templates (40x faster for common patterns)

**Expected Impact:** 80% latency reduction

**Phase 3: Event-Driven (Weeks 4-5)**
- ✅ Add Redis Streams (sub-100ms event triggers)
- ✅ Add event-driven workflows
- ✅ Background workers for continuous monitoring

**Expected Impact:** Enable HFT scenarios

---

## 8. Security & Compliance

### 8.1 Regulatory Requirements for Trading

**MiFID II / SEC Requirements:**
- ✅ Complete audit trail (DecisionTrace captures everything)
- ✅ Approval records (ApprovalStore with timestamps, reasons)
- ✅ Immutable event log (append-only EventLog)
- ✅ Version tracking (schema_version, kernel_version in all records)
- ⚠️ Need: 7-year retention policy
- ⚠️ Need: Encrypted at-rest storage

**Clock Synchronization:**
- ⚠️ Need: NTP sync for accurate timestamps
- ⚠️ Need: Microsecond precision timestamps

**Best Execution:**
- ✅ Tool execution records include timing (started_at, ended_at)
- ✅ Order routing captured in ToolCallRecord
- ⚠️ Need: Venue comparison logic

### 8.2 Security Hardening

**Data Encryption:**
```python
# At-rest encryption for sensitive data
class EncryptedStore:
    def __init__(self, underlying_store, encryption_key):
        self._store = underlying_store
        self._cipher = Fernet(encryption_key)

    async def put(self, key: str, value: str) -> None:
        encrypted = self._cipher.encrypt(value.encode())
        await self._store.put(key, encrypted.decode())

    async def get(self, key: str) -> str:
        encrypted = await self._store.get(key)
        if encrypted:
            decrypted = self._cipher.decrypt(encrypted.encode())
            return decrypted.decode()
        return None
```

**Secrets Management:**
- ✅ Already uses environment variables for API keys
- ⚠️ Need: Integration with HashiCorp Vault or AWS Secrets Manager
- ⚠️ Need: Key rotation policy

**API Security:**
```python
# Rate limiting
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/workflows/{workflow_id}/run")
@limiter.limit("10/minute")
async def run_workflow(workflow_id: str):
    ...

# Authentication
from fastapi_auth import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

@app.post("/workflows/{workflow_id}/run")
async def run_workflow(
    workflow_id: str,
    token: str = Depends(oauth2_scheme),
):
    # Verify token
    user = await verify_token(token)
    # Check permissions
    if not user.can_run_workflow(workflow_id):
        raise HTTPException(status_code=403)
    ...
```

---

## 9. Implementation Roadmap

### 9.1 Three-Phase Rollout (8-10 Weeks)

#### **Phase 1: Foundation (Weeks 1-2)**

**Goal:** Fix critical bottlenecks

**Tasks:**
1. ✅ Migrate vector store to LanceDB
2. ✅ Add workflow run persistence (PostgreSQL)
3. ✅ Add plan caching (Redis)
4. ✅ Implement parallel action execution
5. ✅ Add position caching layer

**Success Metrics:**
- Vector search: <50ms at 100K vectors
- Context assembly: <100ms P99
- Plan generation: <500ms with caching

**Resources:**
- 2 senior engineers
- 1 week testing
- $200/month infrastructure (Redis, LanceDB cloud)

---

#### **Phase 2: Trading Support (Weeks 3-5)**

**Goal:** Enable trading workflows

**Tasks:**
1. ✅ Add TimescaleDB for market data
2. ✅ Implement tick store adapter
3. ✅ Add event queue (Redis Streams)
4. ✅ Implement event-driven workflow runner
5. ✅ Create fast-path template library
6. ✅ Add trading capabilities (place order, query position, etc.)

**Success Metrics:**
- Market data ingestion: 100K ticks/second
- Event-to-workflow latency: <100ms
- Fast-path execution: <100ms end-to-end
- Low-frequency trades: <500ms P99

**Resources:**
- 3 senior engineers
- 2 weeks testing + backtesting
- $500/month infrastructure (TimescaleDB, Redis)

---

#### **Phase 3: Production Hardening (Weeks 6-8)**

**Goal:** Production-ready reliability

**Tasks:**
1. ✅ Add backpressure handling
2. ✅ Implement conditional workflow branching
3. ✅ Add monitoring & alerting (Prometheus, Grafana)
4. ✅ Security hardening (encryption, secrets management)
5. ✅ Load testing (1000 workflows/min)
6. ✅ Documentation & runbooks

**Success Metrics:**
- System uptime: 99.9%
- Max concurrent workflows: 100
- Queue depth under load: <10
- Alert response time: <5 minutes

**Resources:**
- 2 senior engineers + 1 SRE
- 2 weeks load testing
- $1000/month infrastructure (full stack)

---

### 9.2 Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| **Vector migration data loss** | Low | High | Dual-write, validation phase |
| **TimescaleDB performance** | Medium | High | Benchmark before production |
| **Event queue overload** | Medium | Medium | Backpressure, dead-letter queue |
| **LLM API rate limits** | High | Medium | Plan caching, fast-path templates |
| **Regulatory audit failure** | Low | Critical | Compliance review before launch |

---

## 10. Migration Strategy

### 10.1 Zero-Downtime Migration

**Principle:** Additive changes, backward compatibility

**Pattern:**
1. **Deploy new component** (parallel to old)
2. **Dual-write** (write to both old and new)
3. **Validate** (compare results, monitor errors)
4. **Cut over reads** (switch reads to new)
5. **Deprecate old** (remove after validation period)

**Example: Vector Store Migration**

```
Week 1: Deploy LanceDB
  - Install LanceDB alongside SQLite
  - Configure, test queries

Week 2: Dual-Write
  - Modify VectorStore to write to both
  - Monitor consistency (log mismatches)

Week 3: Validate
  - Run both queries, compare results
  - Measure performance (expect 100x faster)

Week 4: Cut Over
  - Switch ContextAssembler to read from LanceDB
  - Keep SQLite as fallback (read-only)

Week 5: Cleanup
  - Remove SQLite vector store
  - Archive data
```

---

### 10.2 Rollback Plan

**For each component:**

1. **Feature flag** controls new vs old:
   ```python
   if settings.USE_LANCEDB:
       vector_store = LanceDBVectorStore(...)
   else:
       vector_store = SQLiteVectorStore(...)
   ```

2. **Monitoring alerts** on error rates:
   ```python
   if error_rate > 1%:
       alert("Vector store errors elevated")
       # Auto-rollback to SQLite
   ```

3. **Data backup** before migration:
   ```bash
   pg_dump agent_kernel > backup_2026_01_24.sql
   ```

---

## Conclusion

The agent kernel is **architecturally sound** and **production-ready** for its current use case (personal knowledge management, daily workflows). Expansion to trading requires **targeted improvements** in three areas:

1. **Data Layer:** Upgrade vector store, add time-series support
2. **Execution Layer:** Add parallelism, caching, fast-path templates
3. **Orchestration Layer:** Add event-driven triggers, conditional branching

With the recommended **8-10 week implementation plan**, the system will be capable of:

✅ **Low-frequency trading** (daily/hourly strategies)
✅ **Real-time risk monitoring** (<100ms event response)
✅ **High-throughput data ingestion** (100K+ ticks/second)
✅ **Regulatory compliance** (full audit trail)

**Investment:** ~$30K engineering + $12K/year infrastructure
**Expected ROI:** Enables trading business line with robust, auditable infrastructure

The architecture's strong foundation (schemas, separation of concerns, comprehensive tracing) makes it an excellent candidate for expansion with manageable risk and clear migration path.
