# Development Planning

## Comparative Analysis: Martek vs Agent Kernel

### Component Comparison

| Component | Martek | Agent Kernel | Decision |
|-----------|--------|--------------|----------|
| **Package Manager** | Poetry | uv + hatchling | ✅ Keep uv (faster, modern) |
| **Config** | Pydantic BaseSettings | Pydantic BaseSettings | ✅ Adopt pattern |
| **Tracing** | Braintrust (external) | Own TraceStore | ✅ Build our own |
| **Prompts** | Braintrust-managed | Local + configurable | ✅ Keep local |
| **Agent Interface** | AgentBase with `run()` | AgentEngine with `propose()` | ✅ Adopt thin interface concept |
| **Tool Calling** | Mixed (some direct) | Tool Broker ONLY | ✅ Enforce broker pattern |
| **Error Handling** | `@dbx_handled` decorator | Custom exceptions | ✅ Adopt decorator pattern |
| **Testing** | Tiered (unit/integration) | Tiered (unit/integration) | ✅ Adopt pattern |
| **Observability** | Braintrust tracing | Own trace + analysis agent | ✅ Build own + analyzer |
| **Memory** | Not centralized | Kernel-owned subsystem | ✅ New architecture |
| **Workflows** | Hardcoded strategies | Declarative YAML | ✅ New architecture |

---

## What to ADOPT from Martek

### 1. ✅ Thin Agent Interface Pattern
Martek's `AgentBase` has the right idea - a minimal interface:

```python
# Martek pattern (good)
class AgentBase:
    def run(self, input: AgentInput) -> AgentOutput

# Our adaptation
class AgentEngine(Protocol):
    def propose(self, context: ContextPacket, profile: AgentProfile) -> Plan
```

**Why adopt:** Clean separation, easy to test, framework-agnostic.

### 2. ✅ Pydantic Settings Pattern
Martek's `settings.py` using Pydantic BaseSettings is excellent:

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    external_api_host: str
    openai_api_key: str
    # Resolution: env vars > .env > defaults
```

**Why adopt:** Type-safe config, automatic env loading, validation.

### 3. ✅ Error Handling Decorator Pattern
Martek's `@dbx_handled` decorator for external calls:

```python
@dbx_handled  # Retry, backoff, structured errors
async def call_external_api():
    ...
```

**What we'll build:**

```python
@tool_handled(retries=3, backoff=True)
async def execute_tool():
    ...
```

### 4. ✅ Tiered Testing Strategy
Martek's approach:
- `make test-unit` - Fast feedback (11s)
- `make test` - Comprehensive (2-3 min)
- `make test-all` - Full including integration

**Why adopt:** Memory-safe, good developer experience.

### 5. ✅ AgentInput/AgentOutput Types (Text-First)
Martek's types prioritize text with optional structured data:

```python
class AgentInput(BaseModel):
    text: str
    payload: dict | None = None

class AgentOutput(BaseModel):
    text: str
    success: bool
    metadata: dict
```

**Our adaptation:** `ContextPacket` (input) and `Plan` (output) are more structured but follow same philosophy.

---

## What to AVOID from Martek

### 1. ❌ Braintrust Dependency
Martek uses Braintrust for:
- Prompt management (loading prompts at runtime)
- Tracing and observability
- Evaluations

**Problems:**
- External service dependency
- Can't run fully offline
- Limited control over trace format
- Vendor lock-in

**Our approach:** Build own TraceStore + trace analysis agent.

### 2. ❌ Hardcoded Execution Strategies
Martek has fixed execution strategies defined as Python enums.

**Problems:**
- Not flexible for new use cases
- Tight coupling to specific tools and services
- Requires code changes to add new strategies

**Our approach:** Declarative workflows in YAML that can be modified without code changes.

### 3. ❌ Domain-Specific Built-in Agents
Martek has:
- SportsAgent
- VisualizationAgent
- QuestionRewriterAgent
- FollowUpQuestionsAgent

**Problems:**
- These are use-case specific, not framework code
- Bloats the core library

**Our approach:** Core kernel is domain-agnostic. Specialized agents are user-implemented.

### 4. ❌ Mixed Tool Calling
Martek sometimes calls tools directly from agents:
```python
# Found in some agents - bypasses tracing
result = await some_tool.execute(args)
```

**Problems:**
- Inconsistent tracing
- No allowlist enforcement
- No approval gating

**Our approach:** ALL tool calls go through ToolBroker. No exceptions.

### 5. ❌ Braintrust Prompt Loading
Martek loads prompts from Braintrust at runtime:
```python
prompt = await load_agent_prompt(slug="daily_review")
```

**Problems:**
- Network dependency
- Can't version prompts with code
- Harder to test

**Our approach:** Prompts in config files or agent profile YAML.

---

## New Components to Build

### 1. 🆕 Trace Analysis Agent + MCP Tool

Build an agent that can analyze trace logs to understand system behavior.

**MCP Tools:**
```yaml
# trace_analysis.query@v1
capability_name: trace_analysis.query@v1
description: Query and analyze decision traces
input_schema:
  properties:
    query: { type: string }
    time_range: { type: object }
    filters: { type: object }

# trace_analysis.summarize@v1
capability_name: trace_analysis.summarize@v1
description: Summarize patterns in traces
input_schema:
  properties:
    trace_ids: { type: array }
    focus: { type: string }  # "errors", "performance", "decisions"

# trace_analysis.diagnose@v1
capability_name: trace_analysis.diagnose@v1
description: Diagnose issues from trace patterns
input_schema:
  properties:
    symptom: { type: string }
    context: { type: object }
```

**Agent Profile:**
```yaml
agent_profile_id: trace_analyst
name: Trace Analysis Agent
engine: custom
allowed_capabilities:
  - trace_analysis.query@v1
  - trace_analysis.summarize@v1
  - trace_analysis.diagnose@v1
  - notes.create@v1
```

**Use Cases:**
- "Why did yesterday's daily check-in fail?"
- "What's the average tool execution time this week?"
- "Show me all denied approvals and their reasons"
- "Summarize error patterns in the last 24 hours"

### 2. 🆕 Self-Hosted Tracing Dashboard (Future)

A local web UI for:
- Browsing traces
- Filtering by agent/status/time
- Viewing tool call details
- Performance metrics
- Error analysis

### 3. 🆕 Prompt Management System

Instead of Braintrust:
- Prompts stored in `configs/prompts/` as YAML/Jinja2
- Versioned with code
- Can be overridden per agent profile
- Template variables for dynamic content

---

## Development Priorities

### Priority Matrix

| Priority | Component | Effort | Value | Dependencies |
|----------|-----------|--------|-------|--------------|
| **P0** | Core Schemas | Low | Critical | None |
| **P0** | TraceStore (SQLite) | Medium | Critical | Schemas |
| **P0** | Event Log | Low | Critical | Schemas |
| **P1** | Capability Registry | Medium | High | Schemas |
| **P1** | Tool Broker | Medium | High | Registry, TraceStore |
| **P1** | Local Function Adapter | Low | High | Broker |
| **P2** | Document Store | Medium | High | Schemas |
| **P2** | Vector Store (LanceDB) | Medium | High | Schemas |
| **P2** | Graph Store | Medium | High | Schemas |
| **P2** | Context Assembler | Medium | High | All stores |
| **P3** | AgentEngine Interface | Low | Critical | Schemas |
| **P3** | CustomEngine (LLM) | Medium | Critical | Engine interface |
| **P3** | LLM Service (OpenAI) | Medium | Critical | None |
| **P4** | Deterministic Executor | High | Critical | Broker, Engine |
| **P4** | Workflow Runner | High | High | Executor |
| **P5** | CLI | Medium | High | All above |
| **P5** | First Workflow (daily_checkin) | Low | High | Runner |
| **P6** | Trace Analysis Capabilities | Medium | Medium | TraceStore |
| **P6** | Trace Analyst Agent | Medium | Medium | Analysis caps |
| **P7** | Additional Adapters | Medium | Medium | Broker |
| **P8** | Web Dashboard | High | Low | TraceStore |
| **P8** | LangGraph Adapter | Medium | Low | Engine interface |

---

## Implementation Sprints

### Sprint 1: Foundation (Week 1)
**Goal:** Core schemas and storage working

- [ ] Core schemas (ContextRef, ContextPacket, Plan, etc.)
- [ ] TraceStore with SQLite sink
- [ ] Event log (append-only)
- [ ] Config system (Pydantic Settings)
- [ ] Custom exceptions
- [ ] ULID generation

**Deliverable:** `list-traces` CLI command works with test data

### Sprint 2: Tool Execution (Week 2)
**Goal:** Tool Broker executing tools with full tracing

- [ ] CapabilityDef schema and loader
- [ ] Capability Registry
- [ ] Tool Broker
- [ ] LocalFunctionAdapter
- [ ] `@tool_handled` decorator
- [ ] 3 initial tools (tasks.list, tasks.create, notes.search)

**Deliverable:** Tools execute and log ToolCallRecords

### Sprint 3: Memory & Context (Week 2-3)
**Goal:** Context assembly from multiple sources

- [ ] Document Store (SQLite + FTS)
- [ ] Vector Store (LanceDB)
- [ ] Graph Store (SQLite)
- [ ] EmbeddingService (OpenAI)
- [ ] ContextAssembler
- [ ] Retrieval report generation

**Deliverable:** ContextPacket assembles from stubbed data

### Sprint 4: Agent Engine (Week 3)
**Goal:** LLM produces valid Plans

- [ ] AgentEngine protocol
- [ ] LLMService (OpenAI)
- [ ] CustomEngine
- [ ] Prompt templates in config
- [ ] Citation validation

**Deliverable:** Engine produces Plan from ContextPacket

### Sprint 5: Execution & Workflows (Week 4)
**Goal:** End-to-end workflow execution

- [ ] DeterministicExecutor
- [ ] Approval gate logic
- [ ] WorkflowSpec loader
- [ ] WorkflowRunner
- [ ] Write-back logic

**Deliverable:** `run-workflow daily_checkin` works

### Sprint 6: CLI & Polish (Week 4-5)
**Goal:** Usable CLI with trace analysis

- [ ] Full CLI commands
- [ ] Trace analysis capabilities
- [ ] Trace analyst agent profile
- [ ] Error handling polish
- [ ] Documentation

**Deliverable:** Complete MVP

---

## Decision Log

| Decision | Chosen | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| Tracing | Own TraceStore | Braintrust, OpenTelemetry | Full control, offline, custom analyzer |
| Package Manager | uv + hatchling | Poetry, pip | Faster, modern Python standards |
| Vector Store | LanceDB | ChromaDB, pgvector, Qdrant | Lightweight, Rust-based, truly embedded |
| LLM Provider | OpenAI (default) | Anthropic, local | Best structured output support |
| Workflow Format | YAML | Python DSL, JSON | Readable, editable, versionable |
| Graph Store | SQLite tables | Neo4j | Start simple, migrate later |
| Prompt Storage | Local YAML | Braintrust, DB | Version with code, no external dep |

---

## Open Questions

1. **Approval UI**: CLI-only for now, or build simple web approval page?
   - *Recommendation:* CLI first, add web later

2. **Multi-user**: Support multiple users with separate contexts?
   - *Recommendation:* Single-user MVP, multi-user later

3. **Scheduling**: Use APScheduler or simple cron file watcher?
   - *Recommendation:* APScheduler for flexibility

4. **Prompt Templates**: Jinja2 or simple string formatting?
   - *Recommendation:* Jinja2 for conditionals/loops

5. **MCP Integration**: When to add MCP adapter?
   - *Recommendation:* After MVP, when policy allows

---

## Success Metrics

### Sprint 1 Complete When:
- [ ] All schemas validate with Pydantic
- [ ] Traces persist to SQLite
- [ ] `agent-kernel list-traces` works

### Sprint 2 Complete When:
- [ ] 3 capabilities registered
- [ ] Tool Broker validates inputs
- [ ] ToolCallRecords logged for every call

### MVP Complete When:
- [ ] `agent-kernel run-workflow daily_checkin` executes end-to-end
- [ ] Traces show full context + plan + execution
- [ ] Trace analyst can answer "why did X fail?"
- [ ] All tests pass
- [ ] Documentation complete
