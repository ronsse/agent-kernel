# Agent Integration Contract

**Version:** 1.0.0
**Status:** Draft

The Agent Kernel is a **platform** that external agents consume. This document defines the contract between the kernel and any agent runtime — external agent runtime, LangChain, Claude Code, custom Python scripts, or future frameworks.

---

## Design Principles

### 1. The Kernel is Not an Agent

The kernel provides memory, tool governance, context assembly, tracing, and experience learning. It does not make decisions, generate plans, or take autonomous action. Agents do that. The kernel is the substrate they operate on.

### 2. Memory Ownership is Split

Agent runtimes own **session memory** — conversation history, personality, workspace files, compaction. The kernel owns **institutional memory** — structured knowledge, audit trails, lessons learned, cross-agent patterns.

Neither system should duplicate the other's job.

### 3. Integration is Graduated

Not every agent needs every kernel capability. The contract defines four levels, each building on the last. An agent can start at Level 0 and graduate as the integration matures.

### 4. Framework-Agnostic Surface

The kernel exposes three equivalent API surfaces: REST (HTTP), MCP (stdio), and Python import. The contract is the same regardless of transport. Framework-specific adapters translate between the runtime's conventions and the kernel's API.

---

## Memory Boundary: Who Owns What

This is the most important architectural decision. Getting this wrong creates duplication, staleness, and confusion about which system is authoritative.

### Agent Runtime Owns (Session Layer)

| Concern | Examples | Why Runtime |
|---------|----------|-------------|
| **Conversation history** | Message log, context window | Runtime manages compaction, token budgets, threading |
| **Personality & identity** | SOUL.md, IDENTITY.md, tone, name | Per-agent, subjective, no schema needed |
| **Workspace state** | Current files, environment, paths | Ephemeral, machine-specific |
| **Session memory** | Daily logs, scratch notes, temp state | High-churn, agent-private, discarded after compaction |
| **User preferences** | USER.md, communication style | Per-agent view of the user |
| **Semantic search over own history** | "What did I do yesterday?" | Runtime's search index covers its own files |

**Rule:** If it's about *this agent's current session or personality*, the runtime owns it.

### Kernel Owns (Institutional Layer)

| Concern | Examples | Why Kernel |
|---------|----------|------------|
| **Decision audit trail** | DecisionTrace, ToolCallRecord, Plans | Immutable, cross-agent, needs provenance |
| **Knowledge graph** | Concepts, patterns, insights, entity relationships | Shared across agents, typed, versioned |
| **Experience memory** | Cases, lessons, playbooks | Derived from traces, shared learning |
| **Tool governance** | Capability registry, approvals, rate limits, side effects | Centralized policy, not per-agent |
| **Context assembly** | Retrieval from all memory layers, ranked, budget-enforced | Deterministic, observable, reusable |
| **Entity registry** | Canonical IDs for notes, tasks, people, systems | Cross-system deduplication |
| **Cost & performance tracking** | LLM call costs, tool latency, anomaly detection | Aggregate view across all agents |

**Rule:** If it's about *what happened, what we learned, and how tools are governed*, the kernel owns it.

### The Overlap Zone

Some data exists in both systems. The contract defines who is authoritative:

| Data | Runtime Has | Kernel Has | Authoritative |
|------|-------------|------------|---------------|
| **Task list** | Agent's working copy | Graph nodes + external task backend sync | **Kernel** (syncs to/from external task backend) |
| **Notes/documents** | Agent may read vault files | Document store + embeddings | **Source system** (Obsidian vault) — kernel indexes it |
| **Calendar events** | Agent may query calendar | Graph nodes | **Source system** (Google Calendar) — kernel indexes it |
| **"What I learned"** | MEMORY.md (agent's words) | Knowledge graph (structured) | **Both** — different representations of the same insight |
| **Past actions** | Session logs (ephemeral) | Traces (permanent) | **Kernel** for audit; runtime for recent recall |

### Handling "What I Learned"

This is the most nuanced overlap. When an agent discovers something useful:

1. **Agent writes to its own MEMORY.md** — natural language, for its own future sessions
2. **Agent calls `knowledge_add`** — structured node in the kernel graph, shared with all agents

These are not duplicates. MEMORY.md is "what I personally remember." The knowledge graph is "what the organization knows." An agent might write `"The external task backend API rate limits at 450 req/min"` to MEMORY.md for quick recall, AND add a knowledge node with `node_type: "rule"` so other agents learn it too.

**The contract does NOT require agents to use `knowledge_add`.** It's Level 2 behavior (see below). But the kernel should make it effortless.

---

## Integration Levels

### Level 0 — Traced (Minimum Viable)

**What the agent does:** Reports what it did after each run.

**Why this matters:** Without traces, the kernel cannot mine experience, detect cost anomalies, track tool usage patterns, or build the knowledge graph. Tracing is the foundation of all kernel value.

**Contract:**

```
MUST: POST /traces/ingest after each agent run
      {
        agent_id: string        # Maps to kernel AgentProfile
        intent: string          # What was the agent trying to do
        actions: [              # Tool calls that happened
          {
            capability: string  # Tool/function name
            input: object       # Arguments (redact secrets)
            output: object      # Result (truncate large payloads)
            status: "success" | "error" | "skipped"
            duration_ms: number
          }
        ]
        outcome: {
          status: "completed" | "partial" | "failed"
          summary: string       # 1-2 sentence result description
        }
      }

MUST: Map agent_id to a kernel AgentProfile (via adapter config)
MUST: Degrade gracefully if kernel is unreachable (log locally, retry later)
MUST NOT: Block agent execution waiting for trace ingestion
```

**What the kernel does with Level 0 data:**
- Stores as lightweight `DecisionTrace`
- Decomposes into graph nodes (TRAJECTORY, DECISION_EVENT)
- Feeds experience mining (nightly job extracts lessons)
- Tracks tool usage patterns and cost trends
- Enables `kernel_traces_recent` queries by any agent

**Adapter implementation pattern:**
```
# external agent runtime: after_tool_call hook buffers, agent_end hook flushes
# LangChain: CallbackHandler.on_chain_end posts trace
# Claude Code: MCP tool call at session end
# Custom: context manager (with kernel.trace() as t: ...)
```

### Level 1 — Context-Aware

**What the agent does:** Requests kernel context before planning.

**Why this matters:** The kernel assembles context from six memory layers (documents, vectors, graph, experience, skills, context packs), ranks items by relevance, enforces token budgets, and runs quality gates. An agent doing its own retrieval misses cross-agent knowledge, experience warnings, and playbook guidance.

**Contract:**

```
MUST: Level 0 (traced)
MUST: Request context before generating a plan
      POST /context/assemble
      {
        intent: string          # What the agent is about to do
        agent_id: string        # For profile-based context policy
        max_tokens: number      # How much context the agent can consume
        project_id?: string     # Optional scope filter
      }

SHOULD: Use returned context items in plan generation (include in prompt)
SHOULD: Cite context items when making decisions (enables trace→context linking)
MUST NOT: Ignore quality gate warnings (log them, surface to user if critical)
```

**What the kernel returns:**

```json
{
  "packet_id": "ctx_01ABC...",
  "items": [
    {
      "ref_type": "note",
      "ref_id": "note_01XYZ",
      "excerpt": "The external task backend API has a 450 req/min rate limit...",
      "relevance_score": 0.92,
      "included_reason": "Semantic match + recent experience warning"
    }
  ],
  "warnings": [
    "Similar intent failed 2 days ago (case_01DEF) — check lesson_01GHI"
  ],
  "playbook": {
    "name": "API Sync Pattern",
    "checklist": ["Verify auth token", "Check rate limits", "Use idempotency keys"],
    "pitfalls": ["Don't retry 4xx errors", "Batch requests when possible"]
  },
  "retrieval_report": {
    "queries_run": 4,
    "items_considered": 47,
    "items_selected": 12,
    "duration_ms": 280
  }
}
```

**Key design choice:** The kernel returns **text excerpts and metadata**, not raw embeddings or graph traversals. The agent doesn't need to understand the kernel's internals — it receives pre-ranked, budget-enforced, human-readable context.

**How this interacts with runtime memory:**

The agent's runtime (e.g., external agent runtime) already has its own context — conversation history, workspace files, personality. Kernel context is **additive**:

```
Agent's prompt = [System prompt]
              + [Personality/identity files]        ← Runtime owns
              + [Conversation history]              ← Runtime owns
              + [KERNEL CONTEXT]                    ← Kernel provides
              + [Current user message]              ← Runtime owns
```

The kernel context section is injected by the adapter (e.g., `before_agent_start` hook in external agent runtime). It does NOT replace the runtime's own context management.

### Level 2 — Learning

**What the agent does:** Writes knowledge back and consults experience before acting.

**Why this matters:** The kernel's value compounds over time. Agents that write knowledge create a shared institutional memory. Agents that read experience avoid repeating mistakes. This is the difference between agents that reset every session and agents that genuinely learn.

**Contract:**

```
MUST: Level 1 (context-aware)
SHOULD: Write discovered knowledge back to the kernel
        POST /knowledge/add
        {
          title: string         # Concise name
          description: string   # What was learned
          node_type: string     # concept | insight | pattern | rule | system
          tags?: string[]       # For retrieval
          confidence?: number   # 0.0-1.0 (how sure)
        }

SHOULD: Check experience before high-risk actions
        GET /experience/lessons?workflow_id=<current_workflow>
        GET /experience/playbooks?workflow_id=<current_workflow>

SHOULD: Report outcome quality when known
        POST /experience/evaluate
        {
          trace_id: string
          label: "success" | "partial" | "failure"
          feedback?: string
        }

MAY: Query knowledge graph for relationships
     POST /knowledge/query
     {
       query: string
       node_types?: string[]    # Filter to specific types
     }

MAY: Create knowledge relationships
     POST /knowledge/relate
     {
       source_id: string
       target_id: string
       edge_type: string        # e.g., "depends_on", "contradicts"
     }
```

**When to write knowledge:**

| Agent discovers... | Action |
|---------------------|--------|
| A reusable pattern | `knowledge_add(node_type="pattern", ...)` |
| A system behavior/constraint | `knowledge_add(node_type="rule", ...)` |
| A non-obvious insight | `knowledge_add(node_type="insight", ...)` |
| A connection between things | `knowledge_relate(source, target, edge_type)` |
| Nothing new | Don't write. Don't pollute the graph with noise. |

**Confidence guidelines:**

| Confidence | When |
|-----------|------|
| 0.9-1.0 | Verified through execution (tool succeeded, output confirmed) |
| 0.7-0.8 | Inferred from context (multiple sources agree) |
| 0.5-0.6 | Observed once, not yet verified |
| < 0.5 | Don't write. Wait for more evidence. |

### Level 3 — Governed (Full Citizen)

**What the agent does:** Uses the kernel's capability registry and respects approval gates.

**Why this matters:** At this level, the kernel provides centralized tool governance — capability schemas, side-effect classifications, approval policies, rate limits, idempotency enforcement. The agent doesn't need its own tool governance layer.

**Contract:**

```
MUST: Level 2 (learning)
MUST: Query capability registry before executing tools
      GET /capabilities/<capability_name>
      Returns: input_schema, output_schema, side_effect_level,
               requires_approval_default, rate_limit, timeout_ms

MUST: Submit plans for validation before execution
      POST /plans/validate
      {
        plan: Plan              # Full kernel Plan schema
        agent_profile_id: string
      }

MUST: Request approval for gated actions
      POST /approvals/request
      {
        action_id: string
        capability_name: string
        args_preview: object    # Redacted for review
      }

MUST: Wait for approval before executing gated actions
MUST: Report tool call records with kernel schema fields
      (effective_side_effect, idempotency_key, cost records)

SHOULD: Use kernel's adaptive timeouts for tool calls
SHOULD: Report LLM call costs for anomaly detection
```

**This level is optional for most integrations.** It's designed for agents that execute high-impact actions (external API writes, calendar changes, financial operations) where centralized governance adds real safety value.

**The tradeoff:** Level 3 agents are more tightly coupled to the kernel. They depend on the kernel being available for tool execution, not just for memory. This is appropriate for production agents in controlled environments, not for casual integrations.

---

## Adapter Architecture

Each agent runtime needs an adapter that translates between the runtime's conventions and the kernel's API. The adapter is a **separate package** — not part of the kernel, not part of the runtime.

```
┌─────────────────────────────────────────────────────────┐
│  AGENT RUNTIME                                           │
│  (external agent runtime, LangChain, Claude Code, custom)              │
│                                                          │
│  Runtime memory: conversation, personality, workspace    │
├──────────────────────────────────────────────────────────┤
│  ADAPTER (per-framework package)                         │
│                                                          │
│  Responsibilities:                                       │
│  1. Hook into runtime lifecycle (start, tool_call, end)  │
│  2. Translate runtime events → kernel API calls          │
│  3. Inject kernel context into runtime's prompt format   │
│  4. Buffer actions during session, flush as trace        │
│  5. Degrade gracefully when kernel unreachable           │
│                                                          │
│  Transport: HTTP client to kernel REST API               │
├──────────────────────────────────────────────────────────┤
│  KERNEL API (:8787 REST / MCP stdio / Python import)     │
│                                                          │
│  Institutional memory: knowledge, traces, experience     │
│  Tool governance: capabilities, approvals, rate limits   │
│  Context assembly: retrieval, ranking, quality gates     │
└──────────────────────────────────────────────────────────┘
```

### Adapter Contract

Every adapter MUST:

1. **Be non-blocking** — never delay agent execution waiting for kernel responses
2. **Be fault-tolerant** — agent works normally if kernel is down
3. **Buffer and batch** — don't fire API calls on every token; batch tool calls and flush on session end
4. **Respect token budgets** — pass `max_tokens` to context assembly; don't inject unbounded context
5. **Map identities** — translate runtime agent IDs to kernel AgentProfile IDs via config
6. **Redact secrets** — strip API keys, tokens, passwords from trace payloads before sending

Every adapter SHOULD:

7. **Inject context as a labeled section** — e.g., `[KERNEL CONTEXT]` block, clearly separated from runtime context
8. **Surface quality warnings** — if context assembly returns warnings, make them visible to the agent
9. **Support configurable integration level** — let users choose Level 0-3 per agent

### Existing Adapters

| Adapter | Runtime | Level | Package |
|---------|---------|-------|---------|
| `kernel-bridge` | external agent runtime | 1-2 | `src/external-agent-kernel-bridge/` (Node.js plugin) |
| MCP server | Claude Code | 1-2 | `src/agent_kernel/mcp_server/` (built into kernel) |

### Planned Adapters

| Adapter | Runtime | Transport | Notes |
|---------|---------|-----------|-------|
| Python SDK | Any Python agent | HTTP client | `agent-kernel-client` package |
| LangChain | LangChain/LangGraph | CallbackHandler | Hooks into chain lifecycle |
| Generic webhook | Any HTTP-capable agent | Webhook receiver | Kernel receives push events |

---

## Context Injection Protocol

When an adapter injects kernel context into an agent's prompt, it MUST follow this format:

```markdown
[KERNEL CONTEXT — {packet_id}]

## Relevant Knowledge
- {item.excerpt} (source: {item.ref_type}/{item.ref_id}, relevance: {score})
- ...

## Experience Warnings
- {warning text}

## Playbook: {playbook.name}
Checklist:
- {checklist items}
Pitfalls:
- {pitfall items}

[END KERNEL CONTEXT]
```

**Rules:**
- Context section MUST be clearly delimited (agents should know what came from the kernel)
- Items MUST include source references (enables citation tracking in traces)
- Section MUST respect the `max_tokens` budget requested
- If kernel is unreachable, section is omitted entirely (not replaced with an error message)

---

## Trace Ingestion Protocol

### Lightweight Trace (Level 0-2)

External agents send minimal trace data. The kernel expands it into a full `DecisionTrace`:

```
External Agent Sends              Kernel Creates
─────────────────────             ────────────────
agent_id: "example"           →    agent_profile_id: "example_agent"
intent: "sync tasks"         →    intent (preserved)
actions: [                   →    tool_calls: [ToolCallRecord(...)]
  {capability, input,              + effective_side_effect (from CapabilityDef)
   output, status,                 + effective_requires_approval
   duration_ms}                    + idempotency tracking
]
outcome: {status, summary}   →    outcome: Outcome(...)
                                  + plan: Plan (synthetic, single-action)
                                  + provenance: {engine: "external/<runtime>"}
                                  + graph decomposition (auto)
```

### Full Trace (Level 3)

Agents at Level 3 submit kernel-schema traces directly:

```
External Agent Sends              Kernel Creates
─────────────────────             ────────────────
Full Plan object             →    Stored as-is
Full ToolCallRecord[]        →    Stored as-is
LLMCallRecord[]              →    Cost tracking, cache integration
Provenance                   →    Stored as-is
```

### Trace Quality Expectations

| Field | Level 0 | Level 1 | Level 2 | Level 3 |
|-------|---------|---------|---------|---------|
| `agent_id` | Required | Required | Required | Required |
| `intent` | Required | Required | Required | Required |
| `actions[]` | Required | Required | Required | Full ToolCallRecord |
| `outcome` | Required | Required | Required | Required |
| `context_refs_used` | — | Recommended | Required | Required |
| `knowledge_written` | — | — | Recommended | Required |
| `llm_calls` | — | — | — | Required |
| `cost_records` | — | — | — | Required |

---

## Experience Feedback Loop

The kernel's value increases with every trace. Here's how the feedback loop works across integration levels:

```
Level 0: Agent runs → Trace ingested → Experience mining (nightly)
           ↓
Level 1: Experience → Context assembly → Agent's next run
           ↓
Level 2: Agent writes knowledge → Graph grows → Better context
           ↓
Level 3: Full audit trail → Cost optimization → Adaptive policies
```

### What Each Level Unlocks

| Kernel Capability | Level 0 | Level 1 | Level 2 | Level 3 |
|-------------------|---------|---------|---------|---------|
| Trace storage & audit | Yes | Yes | Yes | Yes |
| Experience mining (cases, lessons) | Yes | Yes | Yes | Yes |
| Context assembly with experience | — | Yes | Yes | Yes |
| Playbook matching | — | Yes | Yes | Yes |
| Quality gate warnings | — | Yes | Yes | Yes |
| Cross-agent knowledge sharing | — | — | Yes | Yes |
| Knowledge graph growth | — | — | Yes | Yes |
| Tool governance & approvals | — | — | — | Yes |
| Cost anomaly detection | — | — | — | Yes |
| Adaptive timeouts | — | — | — | Yes |

---

## Release Packaging

When the kernel is released as a standalone tool:

### Ships as `agent-kernel` (core package)

```
agent-kernel/
├── src/agent_kernel/           # Core kernel (memory, tools, context, tracing)
├── configs/
│   ├── capabilities/           # Example capability definitions
│   ├── agents/                 # Example agent profiles
│   ├── workflows/              # Example workflow specs
│   └── context_packs/          # Example context packs
├── docs/design/                # Architecture docs (including this contract)
├── pyproject.toml              # Python package
└── README.md
```

**Includes:** Memory stores, tool broker, context assembler, executor, tracing, CLI, MCP server, REST API.

**Does NOT include:** Adapter packages, personal agent profiles, personal workflows, vault-specific configs.

### Ships as separate packages (adapters)

```
agent-kernel-client/            # Python SDK for any Python agent
agent-kernel-external-agent/          # external agent runtime plugin (Node.js, current kernel-bridge)
agent-kernel-langchain/         # LangChain callback handler
```

Each adapter depends on the core kernel API (REST or MCP), not on Python imports. This means adapters can be in any language.

### User creates (their own)

```
my-agent-system/
├── configs/
│   ├── agents/*.yaml           # Their agent profiles
│   ├── workflows/*.yaml        # Their workflow specs
│   └── context_packs/*.yaml    # Their context packs
├── .env                        # Their API keys
└── data/                       # Their kernel data (traces, graph, etc.)
```

---

## Migration Path

### For Existing external agent runtime Users

1. **Already at Level 1-2** via kernel-bridge plugin
2. No changes needed — existing plugin becomes the `agent-kernel-external-agent` adapter
3. Optionally upgrade to Level 3 by submitting full Plan objects

### For New Integrations

1. **Start at Level 0** — just report traces. Takes 10 minutes.
2. **Move to Level 1** — add context assembly. Takes 30 minutes.
3. **Move to Level 2** — add knowledge writes. Gradual, per-agent.
4. **Level 3 only if needed** — for production agents with high-impact actions.

---

## Anti-Patterns

### Don't: Duplicate memory between runtime and kernel

If the agent stores insights in both MEMORY.md AND knowledge_add with identical content, one will go stale. Instead:
- **MEMORY.md** = personal shorthand, session recall, personality notes
- **knowledge_add** = structured facts, patterns, rules that other agents should know

### Don't: Use the kernel as a conversation store

The kernel stores *decisions and knowledge*, not *chat history*. Don't pipe every user message through trace ingestion. Trace the **outcome** of a conversation, not the conversation itself.

### Don't: Block on kernel availability

The kernel is an enhancement, not a dependency (except Level 3 governance). If the kernel is down, agents should work normally with degraded context and deferred trace reporting.

### Don't: Write low-confidence knowledge

The knowledge graph is shared across all agents. Writing speculative or unverified nodes pollutes everyone's context. Use confidence thresholds (>= 0.7) and verify through execution before writing.

### Don't: Bypass the adapter

Don't have agents make raw HTTP calls to the kernel. Use the adapter — it handles buffering, batching, error handling, secret redaction, and identity mapping. Raw calls skip all of these safety measures.

---

## Open Questions

1. **Should the kernel push to agents?** Currently all integration is pull-based (agent asks kernel). Should the kernel be able to push urgent lessons or warnings to running agents? This would require a pub/sub mechanism.

2. **Cross-agent messaging via kernel?** Currently agents coordinate through shared state (external task backend, GitHub, graph). Should the kernel provide an agent-to-agent message bus? Or is shared state sufficient?

3. **Trace retention for external agents?** External traces are lighter than kernel-native traces. Should they have different retention policies? Or should the kernel expand them to full fidelity and treat them identically?

4. **Knowledge conflict resolution?** If two agents write contradictory knowledge nodes, who wins? Currently: both exist with separate confidence scores. Should the kernel detect and flag conflicts?

---

## Related Documents

- [00-overview.md](00-overview.md) — Core design principles
- [01-schemas.md](01-schemas.md) — Data contracts
- [10-framework-agnosticism.md](10-framework-agnosticism.md) — Framework swappability
- [17-universal-context-system.md](17-universal-context-system.md) — Entity model
- [20-external-agent-kernel-bridge.md](20-external-agent-kernel-bridge.md) — external agent runtime adapter
