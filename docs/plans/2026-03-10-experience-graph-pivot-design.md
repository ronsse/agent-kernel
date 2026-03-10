# Experience Graph — Pivot Design

## Overview

Pivot from "Agent Kernel" (agent runtime with deterministic executor) to "Experience Graph" (org-scale context graph + experience store). The system captures decision traces and curated knowledge into a shared graph, serves task-scoped retrieval packs back to agents, and learns from outcomes over time.

**One-liner**: A shared experience store for AI agents and teams: traces + provenance + curated precedent + retrieval packs, with policy-gated mutations and immutable audit trails.

**Scope statement**: This is not an agent orchestration framework. It is the org's context and precedent system of record that agents can read from and write to under governance.

## Identity

- **Repo**: `experience-graph`
- **PyPI packages**: `xpgraph` (core), `xpgraph-cli`, `xpgraph-workers`, `xpgraph-obsidian` (etc.)
- **CLI binary**: `xpg`
- **Current repo**: `agent-kernel` archived with README pointing to `experience-graph`

## Adoption Model

Works at multiple scales:
- Single developer capturing traces locally
- BU-level shared system with domain scoping
- Org-wide managed infrastructure

Each BU curates their own portion of the graph. The governed write pipeline starts user-facing and can be pushed behind a service layer over time.

## Relationship to Agents

Agents are external — they live in LangGraph, CrewAI, custom code, whatever. They push traces in and pull context packs out via CLI/SDK. The system has zero opinion about how agents run.

However, the system ships lightweight internal "workers" for curation workflows (enrichment, learning, maintenance). These use an adapted engine/thinking-policy system internally but are not the headline feature.

## Core Entity Model

Six first-class nouns:

### Trace

A record of something that happened (agent run, human action, workflow execution).

```
Trace
  trace_id: ULID
  source: TraceSource           # agent, human, workflow, system
  intent: str
  steps: list[TraceStep]        # ordered sequence of what happened
  evidence_used: list[EvidenceRef]
  artifacts_produced: list[ArtifactRef]
  outcome: Outcome              # success/failure/partial + metrics
  feedback: list[Feedback]      # human or automated ratings
  context: TraceContext          # agent_id, team, domain, timestamps
  metadata: dict
```

### Entity

A node in the graph.

```
Entity
  entity_id: ULID
  entity_type: EntityType       # person, system, concept, service, team, document, ...
  name: str
  properties: dict
  source: EntitySource          # how it entered the graph
  created_at, updated_at
```

### Evidence

Provenance-tracked artifacts attached to traces and/or entities.

```
Evidence
  evidence_id: ULID
  evidence_type: EvidenceType   # document, snippet, link, config, image, file_pointer
  content: str | None           # inline for small items
  uri: str | None               # pointer for large items
  content_hash: str
  source: EvidenceSource        # trace, manual, ingestion
  attached_to: list[AttachmentRef]
  metadata: dict
```

### Precedent

Promoted/curated institutional knowledge derived from traces.

```
Precedent
  precedent_id: ULID
  source_trace_ids: list[str]   # traces it was derived from
  title: str
  description: str
  pattern: str | None           # generalized pattern extracted
  applicability: list[str]      # domains/contexts where relevant
  confidence: float
  promoted_by: str              # who/what promoted it
  evidence_refs: list[str]
  feedback: list[Feedback]
```

### Policy

Governance rules for the write pipeline.

```
Policy
  policy_id: ULID
  policy_type: PolicyType       # mutation, access, retention, redaction
  scope: PolicyScope            # global, domain, team, entity_type
  rules: list[PolicyRule]
  enforcement: Enforcement      # enforce, warn, audit_only
```

### Pack

Retrieval bundles assembled for a specific task/agent/domain.

```
Pack
  pack_id: ULID
  intent: str
  items: list[PackItem]         # traces, evidence, precedents, entities
  retrieval_report: RetrievalReport
  policies_applied: list[str]
  budget: PackBudget            # token/item limits
  assembled_at: datetime
```

## Package Structure

```
experience-graph/
├── src/
│   ├── xpgraph/               # core library (PyPI: xpgraph)
│   │   ├── schemas/           # 6 core entities + supporting types
│   │   ├── stores/            # doc, vector, graph, event log, trace
│   │   ├── ingest/            # trace + evidence ingestion pipeline
│   │   ├── retrieve/          # pack assembly + ranking + hybrid search
│   │   ├── mutate/            # governed write pipeline
│   │   ├── curate/            # atomic primitives: promote, link, label, attach
│   │   ├── config.py
│   │   └── errors.py
│   │
│   ├── xpgraph_cli/           # CLI (PyPI: xpgraph-cli)
│   │   ├── commands/          # ingest, curate, retrieve, analyze, admin, worker
│   │   └── main.py
│   │
│   └── xpgraph_workers/       # curation workflows (PyPI: xpgraph-workers)
│       ├── enrichment/        # LLM enrichment pipelines
│       ├── learning/          # outcome analysis, precedent extraction
│       ├── maintenance/       # retention, reconciliation, staleness
│       └── engine/            # adapted thinking policy + workflow runner
│
├── integrations/              # optional extras (each its own PyPI package)
│   ├── obsidian/
│   ├── jira/
│   ├── slack/
│   └── ...
│
├── configs/
├── tests/
└── docs/
```

## Governed Write Pipeline (mutate/)

Every mutation to the experience graph goes through this pipeline.

### Command Model

```
Command
  command_id: ULID
  operation: Operation          # enum of all mutation types
  target: TargetRef             # what entity/trace/evidence is being mutated
  args: dict
  requested_by: str             # agent, user, workflow
  idempotency_key: str
  metadata: dict
```

### Operations

```
# Ingest
trace.ingest, trace.append_step, trace.record_outcome
evidence.ingest, evidence.attach

# Curate
precedent.promote, precedent.update
entity.create, entity.update, entity.merge
link.create, link.remove
label.add, label.remove
feedback.record

# Maintain
redaction.apply
retention.prune
pack.publish, pack.invalidate
```

### Pipeline Stages

```
Command → Validate → Policy Check → Execute → Trace → Emit Event
```

1. **Validate** — schema validation, referential integrity, args match operation
2. **Policy Check** — match applicable policies by scope, enforce/warn/audit. Approval gates live here.
3. **Execute** — deterministic write to stores. Idempotent.
4. **Trace** — every mutation produces an immutable audit record (system eats its own dog food)
5. **Emit Event** — append to event log for downstream consumers

**Key rule**: Policy is authoritative, never the caller. A command can request `skip_approval: true` but the pipeline ignores that if policy says otherwise.

**Batch support**: `CommandBatch` with sequential/parallel/stop_on_error strategies. Each command goes through full pipeline.

### Mapping from Current Architecture

| Current | Becomes |
|---------|---------|
| `DeterministicExecutor` | `MutationExecutor` |
| `ApprovalGate` | `PolicyGate` (approval is one policy type) |
| `QualityGateRunner` | Folded into Validate stage |
| `ToolBroker` + adapters | Dropped. Operations write to stores directly |
| `CapabilityRegistry` | `OperationRegistry` |
| Idempotency/retry | Preserved, applied at Execute stage |

## CLI Command Tree

Binary: `xpg`. Thin client over core SDK.

```
xpg ingest
  xpg ingest trace <file|->
  xpg ingest traces <dir|glob>
  xpg ingest evidence <path>
  xpg ingest evidence-dir <dir>

xpg curate
  xpg curate promote <trace-id>
  xpg curate link <source> <target>
  xpg curate label <id> <label>
  xpg curate attach <evidence-id> <target-id>
  xpg curate merge <entity-id> <entity-id>
  xpg curate feedback <trace-id> <rating>

xpg retrieve
  xpg retrieve pack --intent "..." [--domain X] [--agent Y]
  xpg retrieve search <query>
  xpg retrieve entity <id>
  xpg retrieve trace <id>
  xpg retrieve precedents [--domain X]

xpg analyze
  xpg analyze paths [--domain X]
  xpg analyze outcomes --by agent|tool|domain
  xpg analyze drift [--baseline <pack-id>]
  xpg analyze gaps --domain X

xpg admin
  xpg admin init
  xpg admin migrate
  xpg admin policy list|add|remove
  xpg admin retention run
  xpg admin redact scan|apply
  xpg admin health
  xpg admin export <format>

xpg worker
  xpg worker run <worker-name>
  xpg worker list
  xpg worker schedule
```

### CLI Design Principles

- `--domain` and `--agent` are pervasive BU-scoping flags
- `--dry-run` on all mutations
- `--format json` for machine output (default: human-readable)
- Stdin/stdout composable (`xpg ingest trace -` reads from stdin)
- No hidden state — config in `~/.xpg/config.yaml` or env vars
- Every CLI command has a Python SDK equivalent

## SDK

```python
from xpgraph import Client

client = Client()
pack = client.retrieve.pack(intent="...", domain="platform")
client.curate.promote(trace_id="...", title="...")
client.ingest.trace(trace_data)
```

## Migration Path

Fresh repo, not a rename. 7 phases:

### Phase 1: New repo + core schemas
Start `experience-graph` repo. Define the 6 core entity schemas as Pydantic models. Set up project structure, pyproject.toml, CI.

### Phase 2: Port stores
Port SQLite document store, graph store, vector store, event log from agent-kernel. Adapt interfaces to match new schemas. These are the most reusable pieces.

### Phase 3: Port retrieval
Current `context/assembler.py` becomes the pack builder. Importance weighting, hybrid search, budget management carry over. Rename context → pack throughout.

### Phase 4: Build mutation pipeline
New code modeled on `DeterministicExecutor` + `ApprovalGate`. Narrower scope: graph mutations only. Command → Validate → Policy Check → Execute → Trace → Emit Event.

### Phase 5: CLI
New `xpg` CLI using Typer. Commands map to core APIs. Structured output support.

### Phase 6: Port workers
Engine, workflows, thinking policy, enrichment service move into `xpgraph_workers/`. Adapted to new schemas.

### Phase 7: Integrations
Obsidian indexer, task sync, calendar sync become optional `xpgraph-obsidian` etc. packages.

### What Gets Preserved

| Current | New Location |
|---------|-------------|
| Store backends (SQLite doc/graph/vector/event) | `xpgraph/stores/` |
| ULID generation, content hashing | `xpgraph/core/` |
| Pydantic base models + versioning | `xpgraph/schemas/base.py` |
| Importance scoring + hybrid search | `xpgraph/retrieve/` |
| Enrichment service + LLM service | `xpgraph_workers/enrichment/` |
| Thinking policy + tiers | `xpgraph_workers/engine/` |
| File indexer | `xpgraph/ingest/` |
| Error hierarchy pattern | `xpgraph/errors.py` |

### What Gets Dropped

| Current | Reason |
|---------|--------|
| `ToolBroker` + capability YAML + adapters | Replaced by mutation pipeline |
| `CapabilityRegistry` | Replaced by `OperationRegistry` |
| `AgentProfile` (as core concept) | Workers have config, but agent profiles aren't core |
| `PromptRegistry` + serializers (in core) | Moves to workers |
| MCP server | Deferred — could return as integration |
| SDK (`agent_kernel_sdk/`) | Replaced by new `xpgraph` SDK |
| Notification system | Event log + webhooks cover this |

### Disposition of agent-kernel

Archive with README pointing to `experience-graph`.
