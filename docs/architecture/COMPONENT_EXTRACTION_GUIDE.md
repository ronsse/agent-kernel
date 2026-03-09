# Component Extraction Guide: Building Modular Systems

**Version:** 1.0
**Date:** 2026-01-24
**Purpose:** Guide for extracting and reusing individual components from the agent kernel to build specialized systems like trading platforms.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Dependency Architecture](#2-dependency-architecture)
3. [Extraction Strategy](#3-extraction-strategy)
4. [Package Structure](#4-package-structure)
5. [Trading System Example](#5-trading-system-example)
6. [Migration Guide](#6-migration-guide)
7. [Best Practices](#7-best-practices)

---

## 1. Overview

### 1.1 Current Architecture

The agent kernel is currently a **monolithic package** with clean internal boundaries but no package-level separation. All components live in `src/agent_kernel/`.

**Good news:** The architecture is **highly modular** with:
- ✅ No circular dependencies
- ✅ Protocol-based abstractions
- ✅ Schema-driven contracts
- ✅ Clean dependency hierarchy

**This makes extraction straightforward.**

### 1.2 Extraction Benefits

**Why extract components?**

| Benefit | Description |
|---------|-------------|
| **Selective Reuse** | Use only what you need (e.g., vector store without agents) |
| **Independent Versioning** | Update memory layer without touching executor |
| **Smaller Dependencies** | Trading system doesn't need workflow orchestration |
| **Faster Builds** | Compile only changed packages |
| **Team Ownership** | Different teams own different packages |
| **Multi-Project Sharing** | Use same vector store in trading + analytics |

### 1.3 Use Cases

**Scenario 1: Trading System**
- Needs: Memory (time-series, positions), Tools (order execution)
- Doesn't need: Workflows, Context assembly, Agent engines

**Scenario 2: Analytics Platform**
- Needs: Memory (vector search, graph), Tools (data fetching)
- Doesn't need: Executor, Approval system

**Scenario 3: Standalone Knowledge Base**
- Needs: Memory (document store, vector store, graph)
- Doesn't need: Everything else

---

## 2. Dependency Architecture

### 2.1 Dependency Pyramid

```
┌─────────────────────────────────────────────────────────┐
│ Layer 5: Orchestration                                  │
│ ┌─────────────┐                                         │
│ │ workflows/  │  Depends on: everything below           │
│ └─────────────┘                                         │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ Layer 4: Integration                                    │
│ ┌─────────────┐  ┌─────────────┐                       │
│ │ executor/   │  │ context/    │  Depends on: 1-3      │
│ └─────────────┘  └─────────────┘                       │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ Layer 3: Business Logic                                 │
│ ┌─────────────┐  ┌─────────────┐                       │
│ │ tools/      │  │ engine/     │  Depends on: 1-2      │
│ └─────────────┘  └─────────────┘                       │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ Layer 2: Persistence                                    │
│ ┌─────────────┐  ┌─────────────┐                       │
│ │ memory/     │  │ tracing/    │  Depends on: 1 only   │
│ └─────────────┘  └─────────────┘                       │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ Layer 1: Foundation                                     │
│ ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│ │ core/       │  │ prompting/  │  │ services/   │     │
│ │ (schemas)   │  │             │  │             │     │
│ └─────────────┘  └─────────────┘  └─────────────┘     │
│ No internal dependencies                               │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Component Dependencies

| Component | Direct Dependencies | Notes |
|-----------|---------------------|-------|
| **core/** | `pydantic`, `ulid` | Foundation, no internal deps |
| **memory/** | `core` | Isolated storage layer |
| **tools/** | `core`, `memory.event_log` | Tool execution gateway |
| **engine/** | `core`, `prompting` | Plan generation |
| **executor/** | `core`, `memory`, `tools`, `tracing` | Integration hub |
| **context/** | `core`, `memory` | Read-only assembly |
| **workflows/** | ALL | Orchestration hub |

### 2.3 Key Insight

**The architecture is a perfect DAG** (Directed Acyclic Graph):
- Information flows **one direction** (bottom-up)
- No circular dependencies
- Clean extraction boundaries

---

## 3. Extraction Strategy

### 3.1 Recommended Package Split

```
agent-kernel-monorepo/
├── packages/
│   ├── core/                    # Foundation schemas
│   ├── memory/                  # Storage layer
│   ├── tools/                   # Tool execution
│   ├── engine/                  # Plan generation
│   ├── executor/                # Execution layer
│   ├── context/                 # Context assembly
│   ├── workflows/               # Orchestration
│   └── integrations/            # Optional add-ons
│       ├── lancedb/
│       ├── timescaledb/
│       ├── openai/
│       └── anthropic/
└── pyproject.toml               # Workspace config
```

### 3.2 Package Definitions

#### **Package 1: `agent-kernel-core`**

**Purpose:** Foundation schemas and utilities

**Contains:**
```
agent_kernel_core/
├── __init__.py
├── schemas/
│   ├── __init__.py
│   ├── base.py              # KernelModel, VersionedModel
│   ├── context_packet.py
│   ├── plan.py
│   ├── trace.py
│   ├── entity.py
│   └── ...
├── ids.py                   # generate_ulid()
├── errors.py                # Exception hierarchy
└── config.py                # Settings
```

**Dependencies:**
```toml
[project]
name = "agent-kernel-core"
dependencies = [
    "pydantic>=2.0",
    "ulid-py>=1.1",
    "structlog>=23.0",
]
```

**Usage:**
```python
from agent_kernel_core.schemas import Plan, ContextPacket, DecisionTrace
from agent_kernel_core.ids import generate_ulid
from agent_kernel_core.errors import ValidationError
```

---

#### **Package 2: `agent-kernel-memory`**

**Purpose:** Storage layer (stores, indexes)

**Contains:**
```
agent_kernel_memory/
├── __init__.py
├── document_store.py        # ABC + SQLite impl
├── vector_store.py          # ABC + SQLite impl
├── graph_store.py           # ABC + SQLite impl
├── entity_store.py
├── event_log.py
├── experience_store.py
├── derivation_store.py
└── implementations/
    ├── sqlite/
    │   ├── document_store.py
    │   ├── vector_store.py
    │   └── graph_store.py
    └── lancedb/             # Optional
        └── vector_store.py
```

**Dependencies:**
```toml
[project]
name = "agent-kernel-memory"
dependencies = [
    "agent-kernel-core>=1.0",
    "numpy>=1.24",
    "sqlite3",              # stdlib but explicit
]

[project.optional-dependencies]
lancedb = ["lancedb>=0.3"]
postgres = ["asyncpg>=0.29"]
timescale = ["asyncpg>=0.29", "psycopg2>=2.9"]
```

**Usage:**
```python
from agent_kernel_memory import VectorStore, DocumentStore, GraphStore
from agent_kernel_memory.implementations.sqlite import SQLiteVectorStore

# Or with LanceDB
from agent_kernel_memory.implementations.lancedb import LanceDBVectorStore
```

---

#### **Package 3: `agent-kernel-tools`**

**Purpose:** Tool execution framework

**Contains:**
```
agent_kernel_tools/
├── __init__.py
├── registry.py              # CapabilityRegistry
├── broker.py                # ToolBroker
└── adapters/
    ├── base.py              # ToolAdapter ABC
    ├── local_function.py
    ├── http.py
    ├── subprocess.py
    ├── mcp.py
    └── skill_script.py
```

**Dependencies:**
```toml
[project]
name = "agent-kernel-tools"
dependencies = [
    "agent-kernel-core>=1.0",
    "agent-kernel-memory>=1.0",  # For event_log only
    "httpx>=0.25",
    "jsonschema>=4.0",
]

[project.optional-dependencies]
mcp = ["mcp-client>=0.1"]
```

**Usage:**
```python
from agent_kernel_tools import ToolBroker, CapabilityRegistry
from agent_kernel_tools.adapters import LocalFunctionAdapter, HTTPToolAdapter
```

---

#### **Package 4: `agent-kernel-engine`**

**Purpose:** Plan generation (LLM integration)

**Contains:**
```
agent_kernel_engine/
├── __init__.py
├── agent_engine.py          # Protocol
├── custom_engine.py         # Implementation
├── critic.py
├── thinking_policy.py
└── registry.py
```

**Dependencies:**
```toml
[project]
name = "agent-kernel-engine"
dependencies = [
    "agent-kernel-core>=1.0",
    "openai>=1.0",           # Optional
]

[project.optional-dependencies]
openai = ["openai>=1.0"]
anthropic = ["anthropic>=0.8"]
```

---

#### **Package 5: `agent-kernel-executor`**

**Purpose:** Plan validation and execution

**Contains:**
```
agent_kernel_executor/
├── __init__.py
├── executor.py              # DeterministicExecutor
├── approval.py              # ApprovalGate
├── quality_gates.py
└── policies.py
```

**Dependencies:**
```toml
[project]
name = "agent-kernel-executor"
dependencies = [
    "agent-kernel-core>=1.0",
    "agent-kernel-memory>=1.0",
    "agent-kernel-tools>=1.0",
]
```

---

#### **Package 6: `agent-kernel-workflows`**

**Purpose:** Orchestration layer

**Contains:**
```
agent_kernel_workflows/
├── __init__.py
├── runner.py                # WorkflowRunner
└── spec.py                  # WorkflowSpec
```

**Dependencies:**
```toml
[project]
name = "agent-kernel-workflows"
dependencies = [
    "agent-kernel-core>=1.0",
    "agent-kernel-memory>=1.0",
    "agent-kernel-tools>=1.0",
    "agent-kernel-engine>=1.0",
    "agent-kernel-executor>=1.0",
]
```

---

### 3.3 Workspace Configuration (Monorepo)

**Use `uv` workspace or Poetry:**

```toml
# pyproject.toml (root)
[tool.uv.workspace]
members = [
    "packages/core",
    "packages/memory",
    "packages/tools",
    "packages/engine",
    "packages/executor",
    "packages/workflows",
]

[tool.uv]
dev-dependencies = [
    "pytest>=7.0",
    "ruff>=0.1",
    "mypy>=1.0",
]
```

**Benefits:**
- Single `uv sync` installs all packages
- Local development uses editable installs
- Can publish packages independently
- Shared tooling (tests, lints)

---

## 4. Package Structure

### 4.1 Example: Standalone `agent-kernel-memory`

**Directory Structure:**
```
agent-kernel-memory/
├── pyproject.toml
├── README.md
├── src/
│   └── agent_kernel_memory/
│       ├── __init__.py
│       ├── document_store.py
│       ├── vector_store.py
│       ├── graph_store.py
│       └── implementations/
│           ├── __init__.py
│           └── sqlite/
│               ├── __init__.py
│               ├── document_store.py
│               └── vector_store.py
├── tests/
│   ├── test_document_store.py
│   └── test_vector_store.py
└── examples/
    └── quickstart.py
```

**pyproject.toml:**
```toml
[project]
name = "agent-kernel-memory"
version = "1.0.0"
description = "Storage layer for agent kernel: document, vector, and graph stores"
requires-python = ">=3.11"
dependencies = [
    "agent-kernel-core>=1.0",
    "numpy>=1.24",
]

[project.optional-dependencies]
lancedb = ["lancedb>=0.3"]
postgres = ["asyncpg>=0.29"]
dev = ["pytest>=7.0", "ruff>=0.1"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

**README.md:**
```markdown
# Agent Kernel Memory

Storage layer for agent systems: document store, vector store, and graph store.

## Installation

```bash
pip install agent-kernel-memory

# With LanceDB support
pip install agent-kernel-memory[lancedb]
```

## Quick Start

```python
from agent_kernel_memory import SQLiteVectorStore

store = SQLiteVectorStore("data/vectors.db")
await store.upsert("item_1", vector=[0.1, 0.2, ...], metadata={})
results = await store.query(query_vector=[0.1, 0.2, ...], top_k=10)
```

## Features

- **Document Store**: Full-text search with FTS5
- **Vector Store**: Semantic similarity search
- **Graph Store**: Relationship traversal
- **Pluggable**: Swap SQLite for PostgreSQL, LanceDB, etc.
```

---

## 5. Trading System Example

### 5.1 Architecture: Trading System Using Components

**Goal:** Build a trading system that uses:
- ✅ Memory layer (positions, market data)
- ✅ Tool broker (order execution)
- ❌ NOT using: Workflows, Context assembly, Agent engines

**Architecture:**

```
Trading System
├── Market Data Ingestor
│   └── Uses: TimescaleDBTickStore (from agent-kernel-memory)
├── Position Manager
│   └── Uses: PostgreSQL + Redis (custom, not from agent-kernel)
├── Strategy Engine (Custom)
│   ├── Reads: Position data, market data
│   └── Generates: Orders (not Plans)
├── Order Executor
│   └── Uses: ToolBroker (from agent-kernel-tools)
└── Risk Monitor
    └── Uses: VectorStore (from agent-kernel-memory) for pattern matching
```

### 5.2 Installation

**Install only what you need:**

```bash
# Core schemas (always needed)
pip install agent-kernel-core

# Memory layer with TimescaleDB support
pip install agent-kernel-memory[timescale,lancedb]

# Tool execution
pip install agent-kernel-tools
```

**You do NOT install:**
- `agent-kernel-workflows` (not needed)
- `agent-kernel-engine` (custom strategy logic)
- `agent-kernel-executor` (using ToolBroker directly)

### 5.3 Implementation

#### **Step 1: Setup Memory Layer**

```python
# trading_system/storage.py
from agent_kernel_memory.implementations.timescale import TimescaleDBTickStore
from agent_kernel_memory.implementations.lancedb import LanceDBVectorStore
from agent_kernel_memory.implementations.sqlite import SQLiteEventLog

class TradingStorage:
    def __init__(self):
        # Time-series for market data
        self.tick_store = TimescaleDBTickStore(
            connection_string="postgresql://localhost/trading",
        )

        # Vector store for strategy pattern matching
        self.strategy_store = LanceDBVectorStore(
            db_path="data/strategies",
            table_name="strategy_embeddings",
            dimension=1536,
        )

        # Event log for audit trail
        self.event_log = SQLiteEventLog(
            db_path="data/audit.db",
        )

    async def initialize(self):
        await self.tick_store.create_tables()
        await self.strategy_store.create_index()
        await self.event_log.init_schema()
```

#### **Step 2: Setup Tool Broker**

```python
# trading_system/execution.py
from agent_kernel_tools import ToolBroker, CapabilityRegistry
from agent_kernel_tools.adapters import HTTPToolAdapter
from agent_kernel_core.schemas import AgentProfile, ActionRequest, Plan

class OrderExecutor:
    def __init__(self, storage: TradingStorage):
        # Load capability definitions
        self.registry = CapabilityRegistry()
        self.registry.load_from_directory("configs/capabilities/trading/")

        # Setup HTTP adapter for broker API
        http_adapter = HTTPToolAdapter(
            default_headers={"Authorization": f"Bearer {BROKER_API_KEY}"},
        )

        # Register trading endpoints
        http_adapter.register(
            "trading.placeorder@v1",
            HTTPEndpoint(
                url="https://api.broker.com/orders",
                method=HTTPMethod.POST,
            ),
        )

        # Create tool broker
        self.broker = ToolBroker(
            registry=self.registry,
            event_log=storage.event_log,
        )
        self.broker.add_adapter(http_adapter)

    async def execute_order(
        self,
        symbol: str,
        quantity: int,
        side: str,
        order_type: str = "market",
    ) -> dict:
        """
        Execute order via ToolBroker (no Plan, no LLM).
        """
        # Create minimal agent profile (for allowlist)
        agent_profile = AgentProfile(
            agent_profile_id="trading_executor",
            allowed_capabilities=["trading.placeorder@v1"],
        )

        # Execute directly via broker
        result = await self.broker.execute(
            capability_name="trading.placeorder@v1",
            args={
                "symbol": symbol,
                "quantity": quantity,
                "side": side,
                "order_type": order_type,
            },
            agent_profile=agent_profile,
        )

        return result.output if result.success else {"error": result.error}
```

#### **Step 3: Custom Strategy Engine (No Agent)**

```python
# trading_system/strategy.py
from agent_kernel_memory import VectorStore
from agent_kernel_core.schemas import ContextItem, ContextRef

class TradingStrategy:
    def __init__(self, storage: TradingStorage):
        self.storage = storage

    async def check_signal(self, symbol: str) -> dict | None:
        """
        Custom logic (not LLM-driven).
        Uses vector store for pattern matching.
        """
        # Get recent price data
        ticks = await self.storage.tick_store.query_ticks(
            symbol=symbol,
            start_time=datetime.now() - timedelta(hours=1),
            end_time=datetime.now(),
        )

        # Compute features (moving averages, etc.)
        features = self._compute_features(ticks)

        # Embed features
        embedding = await self._embed(features)

        # Find similar patterns in history
        similar_patterns = await self.storage.strategy_store.query(
            query_vector=embedding,
            top_k=5,
            filters={"symbol": symbol},
        )

        # Check if similar patterns preceded profitable trades
        for pattern in similar_patterns:
            if pattern.metadata.get("outcome") == "profitable":
                # Signal detected
                return {
                    "action": "buy",
                    "symbol": symbol,
                    "quantity": 100,
                    "confidence": pattern.score,
                    "reason": f"Pattern match: {pattern.item_id}",
                }

        return None  # No signal

    def _compute_features(self, ticks) -> dict:
        # Custom feature engineering
        prices = [t.price for t in ticks]
        return {
            "ma_5": np.mean(prices[-5:]),
            "ma_20": np.mean(prices[-20:]),
            "volume": sum(t.volume for t in ticks),
        }

    async def _embed(self, features: dict) -> list[float]:
        # Convert features to embedding (custom or via API)
        # For example, use a simple encoding or call OpenAI
        return [features["ma_5"], features["ma_20"], features["volume"], ...]
```

#### **Step 4: Main Trading Loop**

```python
# trading_system/main.py
import asyncio
from trading_system.storage import TradingStorage
from trading_system.execution import OrderExecutor
from trading_system.strategy import TradingStrategy

async def main():
    # Initialize storage
    storage = TradingStorage()
    await storage.initialize()

    # Initialize executor (uses ToolBroker)
    executor = OrderExecutor(storage)

    # Initialize strategy (custom logic, no LLM)
    strategy = TradingStrategy(storage)

    # Trading loop
    symbols = ["AAPL", "MSFT", "TSLA"]

    while True:
        for symbol in symbols:
            # Check for trading signal
            signal = await strategy.check_signal(symbol)

            if signal:
                # Execute order via ToolBroker
                result = await executor.execute_order(
                    symbol=signal["symbol"],
                    quantity=signal["quantity"],
                    side=signal["action"],
                    order_type="market",
                )

                print(f"Order executed: {result}")

        # Wait before next check
        await asyncio.sleep(60)  # 1 minute

if __name__ == "__main__":
    asyncio.run(main())
```

### 5.4 What You're Using vs Not Using

| Component | Usage | Notes |
|-----------|-------|-------|
| **agent-kernel-core** | ✅ Yes | Schemas, IDs, errors |
| **agent-kernel-memory** | ✅ Yes | TimescaleDB, LanceDB, EventLog |
| **agent-kernel-tools** | ✅ Yes | ToolBroker for order execution |
| **agent-kernel-engine** | ❌ No | Custom strategy logic instead |
| **agent-kernel-executor** | ❌ No | Direct ToolBroker calls |
| **agent-kernel-workflows** | ❌ No | Custom loop instead |

**Result:**
- **50% smaller dependency footprint**
- **Faster builds** (only compile 3 packages)
- **Simpler mental model** (no workflows, no LLM planning)
- **Still get:** Storage, tool execution, audit trail

---

## 6. Migration Guide

### 6.1 Gradual Extraction (Recommended)

**Phase 1: Create Packages (No Code Changes)**

```bash
# Create package directories
mkdir -p packages/core packages/memory packages/tools

# Move code (keep imports identical)
cp -r src/agent_kernel/core packages/core/src/agent_kernel_core
cp -r src/agent_kernel/memory packages/memory/src/agent_kernel_memory
cp -r src/agent_kernel/tools packages/tools/src/agent_kernel_tools

# Add pyproject.toml to each
```

**Phase 2: Update Imports**

```python
# Old
from agent_kernel.core.schemas import Plan

# New
from agent_kernel_core.schemas import Plan
```

**Phase 3: Test Independently**

```bash
cd packages/core
pytest tests/

cd packages/memory
pytest tests/
```

**Phase 4: Publish**

```bash
cd packages/core
uv build
uv publish
```

### 6.2 Extract Single Component (Quick Start)

**Example: Extract just VectorStore**

```bash
# 1. Create new package
mkdir my-vector-store
cd my-vector-store

# 2. Copy files
cp -r /path/to/agent-kernel/src/agent_kernel/memory/vector_store.py src/
cp -r /path/to/agent-kernel/src/agent_kernel/memory/implementations/sqlite/vector_store.py src/

# 3. Copy minimal dependencies
cp /path/to/agent-kernel/src/agent_kernel/core/schemas/base.py src/schemas/
cp /path/to/agent-kernel/src/agent_kernel/core/ids.py src/

# 4. Create pyproject.toml
cat > pyproject.toml <<EOF
[project]
name = "my-vector-store"
version = "1.0.0"
dependencies = ["pydantic>=2.0", "numpy>=1.24"]
EOF

# 5. Install and test
uv pip install -e .
python -c "from vector_store import VectorStore; print('Success!')"
```

---

## 7. Best Practices

### 7.1 Package Design Principles

**1. Minimal Dependencies**
```python
# Good: Only import what you need
from agent_kernel_core.schemas import Plan

# Bad: Import entire package
import agent_kernel_core
```

**2. Stable Interfaces**
```python
# Use protocols for swappable implementations
from typing import Protocol

class VectorStore(Protocol):
    async def query(self, vector: list[float], top_k: int) -> list: ...
```

**3. Version Pinning**
```toml
# In pyproject.toml
dependencies = [
    "agent-kernel-core>=1.0,<2.0",  # Allow minor updates, not major
]
```

### 7.2 Testing Extracted Components

```python
# Test package in isolation
def test_vector_store_standalone():
    # Should work without any other agent-kernel packages
    from agent_kernel_memory import SQLiteVectorStore

    store = SQLiteVectorStore(":memory:")
    # ... test operations
```

### 7.3 Documentation

**Each package should have:**
- `README.md` - Quick start, installation, examples
- `CHANGELOG.md` - Version history
- `API.md` - Full API reference
- `examples/` - Working code samples

---

## Conclusion

The agent kernel's clean architecture makes component extraction **straightforward**:

✅ **No circular dependencies** - Clean extraction boundaries
✅ **Protocol-driven** - Easy to swap implementations
✅ **Schema contracts** - Stable interfaces across packages
✅ **Minimal coupling** - Most components only depend on `core`

**For trading systems specifically:**
- Use `agent-kernel-memory` for storage (TimescaleDB, LanceDB)
- Use `agent-kernel-tools` for order execution (ToolBroker)
- **Skip** workflows and LLM engines (use custom logic)
- Result: **50% smaller footprint**, faster builds, simpler mental model

**Recommended approach:**
1. Start with monorepo (all packages in workspace)
2. Extract components gradually (update imports)
3. Test each package independently
4. Publish stable packages to PyPI
5. Other projects install only what they need

This strategy balances **code reuse** with **independence**, allowing you to build specialized systems while maintaining a shared foundation.
