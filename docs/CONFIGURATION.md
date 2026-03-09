# Configuration Guide

This guide covers all configuration options for the Agent Kernel.

---

## Table of Contents

1. [Environment Variables](#environment-variables)
2. [Moltbot Sandbox (Rancher Desktop)](#moltbot-sandbox-rancher-desktop)
3. [Agent Profiles](#agent-profiles)
4. [Workflows](#workflows)
5. [Capabilities](#capabilities)
6. [Sources](#sources)
7. [Context Packs](#context-packs)
8. [Prompts](#prompts)
9. [Thinking Tiers](#thinking-tiers)
10. [Policies](#policies)
11. [Memory Stores](#memory-stores)
12. [Tracing](#tracing)

---

## Environment Variables

Create a `.env` file in the project root:

```bash
# =============================================================================
# LLM Configuration
# =============================================================================

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# Anthropic (optional)
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-3-opus-20240229

# Default provider
DEFAULT_LLM_PROVIDER=openai  # openai, anthropic, local

# =============================================================================
# Database & Storage Paths
# =============================================================================

# Main database
DATABASE_PATH=data/agent_kernel.db

# Traces
TRACE_STORE_PATH=data/traces
TRACE_JSONL_ENABLED=true

# Events
EVENT_LOG_PATH=data/events

# Documents
DOCUMENT_STORE_PATH=data/documents

# Vectors
VECTOR_STORE_PATH=data/vectors

# Graph
GRAPH_STORE_PATH=data/graph

# =============================================================================
# Context Assembly
# =============================================================================

CONTEXT_MAX_TOKENS=8000
CONTEXT_MAX_ITEMS=50
CONTEXT_MAX_NOTES=20
CONTEXT_MAX_TASKS=30
CONTEXT_MAX_EVENTS=10

# =============================================================================
# Workflow Defaults
# =============================================================================

WORKFLOW_DEFAULT_TIMEOUT_SECONDS=300
WORKFLOW_MAX_RETRIES=3

# =============================================================================
# API Server (optional)
# =============================================================================

API_HOST=0.0.0.0
API_PORT=8000
API_DEBUG=false

# =============================================================================
# Logging
# =============================================================================

LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR
LOG_FORMAT=json  # json, console
```

---

## Moltbot Sandbox (Rancher Desktop)

This project expects Moltbot to run inside a sandboxed container/VM. On this machine, the sandbox runtime is **Rancher Desktop (Docker engine)**.

### Prerequisites

- Rancher Desktop installed and running (Docker engine enabled).
- A valid OpenAI key available in `.env` (Anthropic is blocked from CLI here).
- Default model should be OpenAI **gpt5.2 thinking** (as configured in `.env`).

### Start Rancher Desktop (Docker)

1. Launch Rancher Desktop.
2. Confirm Docker is active:

```bash
docker context show
docker info
docker ps
```

### Get Moltbot Source

Clone the Moltbot repo (or update your existing clone). Use your preferred path:

```bash
git clone https://github.com/moltbot/moltbot.git ~/github/moltbot
cd ~/github/moltbot
git pull --ff-only
```

### Run Moltbot in Docker (follow upstream README)

Use the repo's official Docker instructions. Typical patterns:

```bash
# If docker compose is provided:
docker compose up -d

# Or if a startup script is provided:
./scripts/dev.sh
```

### Provide OpenAI Credentials to the Container

Ensure the container can read your `.env` or pass env vars explicitly:

```bash
OPENAI_API_KEY=sk-... OPENAI_MODEL=<gpt5.2-thinking> docker compose up -d
```

### Verify

```bash
docker ps
docker logs <moltbot_container_name>
```

---

## Agent Profiles

Agent profiles define agent behavior and constraints.

**Location:** `configs/agents/<name>.yaml`

### Full Schema

```yaml
# Required
agent_profile_id: string       # Unique identifier
name: string                   # Human-readable name
engine: string                 # Engine type: custom, langgraph, etc.

# Optional
description: string            # Agent description

# LLM Configuration
llm_config:
  provider: string             # openai, anthropic, local
  model: string                # Model name (e.g., gpt-4o)
  temperature: float           # 0.0-2.0, default 0.3
  max_tokens: int              # Max output tokens
  top_p: float                 # Nucleus sampling
  frequency_penalty: float     # -2.0 to 2.0
  presence_penalty: float      # -2.0 to 2.0

# Allowed Capabilities
allowed_capabilities:
  - capability.name@version    # List of allowed tools

# Context Retrieval Policy
context_policy:
  max_tokens: int              # Max context tokens (default: 4000)
  max_items: int               # Max context items (default: 50)
  max_notes: int               # Max notes to include
  max_tasks: int               # Max tasks to include
  max_events: int              # Max calendar events
  must_cite: bool              # Require citations in plan
  allowed_scopes: list         # Project IDs, empty = all
  redaction_rules: list        # Fields to redact

# Approval Policy
approval_policy:
  require_approval_for:
    - capability.name@version  # Always require approval
  auto_approve_side_effects:
    - none                     # Auto-approve side-effect levels
    - local
  max_auto_approve_risk: string  # low, medium, high

# Output validation
output_schema_version: string  # Plan schema version to validate
```

### Example

```yaml
agent_profile_id: research_agent
name: Research Agent
description: Searches notes and synthesizes information
engine: custom

llm_config:
  provider: openai
  model: gpt-4o
  temperature: 0.2
  max_tokens: 8000

allowed_capabilities:
  - notes.search@v1
  - notes.get@v1
  - notes.list@v1

context_policy:
  max_tokens: 6000
  max_notes: 30
  must_cite: true

approval_policy:
  auto_approve_side_effects:
    - none
  max_auto_approve_risk: low
```

---

## Workflows

Workflows define multi-step agent processes.

**Location:** `configs/workflows/<name>.yaml`

### Full Schema

```yaml
# Required
workflow_id: string            # Unique identifier
name: string                   # Human-readable name
agent_profile_id: string       # Which agent runs this

# Optional
description: string            # Workflow description

# Trigger Configuration
trigger:
  type: string                 # manual, cron, event, file_watch
  schedule: string             # Cron expression (if type=cron)
  event_type: string           # Event type (if type=event)
  path: string                 # Watch path (if type=file_watch)

# Workflow Steps (in order)
steps:
  - assemble_context           # Build ContextPacket
  - propose_plan               # Generate Plan via engine
  - validate                   # Run quality gates
  - gate_approvals             # Check approval requirements
  - execute                    # Execute plan via broker
  - write_back                 # Update memory stores
  - emit_trace                 # Write DecisionTrace

# Error Handling
on_error: string               # halt, continue, retry

# Retry Configuration (if on_error=retry)
retry:
  max_attempts: int            # Max retry attempts (default: 3)
  base_delay_ms: int           # Initial delay (default: 1000)
  max_delay_ms: int            # Max delay (default: 30000)
  backoff_multiplier: float    # Exponential backoff (default: 2.0)
  retryable_errors:            # Error patterns to retry
    - "timeout"
    - "rate_limit"

# Write-back Configuration
write_back:
  create_summary_note: bool    # Create summary note
  update_graph: bool           # Update graph store
  update_vectors: bool         # Update vector embeddings
  notify: list                 # Notification channels
```

### Example

```yaml
workflow_id: weekly_review
name: Weekly Review
description: Synthesizes weekly progress and plans next week

trigger:
  type: cron
  schedule: "0 9 * * 1"  # 9 AM every Monday

agent_profile_id: weekly_review_agent

steps:
  - assemble_context
  - propose_plan
  - validate
  - gate_approvals
  - execute
  - write_back
  - emit_trace

on_error: retry

retry:
  max_attempts: 2
  base_delay_ms: 5000
  retryable_errors:
    - "timeout"
    - "api_error"

write_back:
  create_summary_note: true
  update_graph: true
```

---

## Calendar Sources

Calendar sources define which calendars to import and how to filter events
before deriving tasks or meeting notes.

**Location:** `configs/integrations/calendar_sources.yaml`

### Example (work meetings)

```yaml
sources:
  - source_id: fanduel_work_google
    provider: google
    calendar_id: "your_calendar_id@import.calendar.google.com"
    purpose: "work_meetings"
    import_window_days: 7

    filters:
      exclude_all_day: true
      exclude_title_prefixes: ["OOO", "Lunch", "Focus"]
      exclude_title_keywords:
        - "standup"
        - "weekly"
        - "all hands"
        - "mle office hours"
      require_attendees_or_conference: false
      require_zoom_link: true

    derivations:
      - type: obsidian_meeting_notes
        vault_path: "${OBSIDIAN_VAULT_PATH}"
        meeting_folder: "Meetings/Work"
        template: "templates/obsidian/meeting_note_template.md"
        caps:
          max_create_per_run: 15
          max_update_per_run: 30
        suppression_ttl_hours: 24
```

### Filters

- `exclude_all_day`: Ignore all-day events.
- `exclude_title_prefixes`: Ignore titles starting with these prefixes.
- `exclude_title_keywords`: Ignore titles containing any of these keywords
  (case-insensitive substring).
- `require_attendees_or_conference`: Require attendees or a conference link.
- `require_zoom_link`: Require a Zoom link in the event description or
  location (recommended for work meetings where attendees are often missing).

### Meeting note behavior (obsidian_meeting_notes)

- 1:1s are listed in the daily note Meetings section.
- Group meetings (non-1:1 or 3+ attendees) get their own meeting note file.
- The daily note includes links to created group meeting notes.
- The Meetings block is inserted as a reserved block after the "Notes" heading.

---

## Capabilities

Capabilities define tool schemas and execution.

**Location:** `configs/capabilities/<name>@<version>.yaml`

### Full Schema

```yaml
# Required
name: string                   # capability.name@version
description: string            # What the tool does
adapter_type: string           # local, http, subprocess, mcp

# Side Effects
side_effect: string            # none, local, external
requires_approval: bool        # Always require approval

# Input/Output Schemas (JSON Schema)
input_schema:
  type: object
  properties:
    param_name:
      type: string
      description: string
      enum: list               # Optional: allowed values
      default: any             # Optional: default value
  required: list               # Required parameters

output_schema:
  type: object
  properties:
    field_name:
      type: string

# Rate Limiting (optional)
rate_limit:
  requests_per_minute: int
  requests_per_hour: int

# Adapter-specific Configuration
config:
  # For http adapter
  base_url: string
  method: string               # GET, POST, PUT, DELETE
  headers: object
  auth_type: string            # bearer, basic, api_key
  
  # For subprocess adapter
  command: string
  args: list
  timeout_seconds: int
  allowed_commands: list       # Security allowlist
  
  # For mcp adapter
  server_host: string
  server_port: int
```

### Examples

#### Local Function

```yaml
name: tasks.create@v1
description: Create a new task
adapter_type: local
side_effect: local
requires_approval: false

input_schema:
  type: object
  properties:
    title:
      type: string
      description: Task title
    description:
      type: string
    due_date:
      type: string
      format: date
    priority:
      type: string
      enum: [low, medium, high]
      default: medium
    project_id:
      type: string
  required: [title]

output_schema:
  type: object
  properties:
    task_id:
      type: string
    created_at:
      type: string
      format: date-time
```

#### HTTP Adapter

```yaml
name: weather.get@v1
description: Get current weather for a location
adapter_type: http
side_effect: none
requires_approval: false

config:
  base_url: https://api.weather.example.com
  method: GET
  auth_type: api_key
  auth_header: X-API-Key

input_schema:
  type: object
  properties:
    location:
      type: string
    units:
      type: string
      enum: [metric, imperial]
      default: metric
  required: [location]

output_schema:
  type: object
  properties:
    temperature:
      type: number
    conditions:
      type: string
```

#### Subprocess Adapter

```yaml
name: git.status@v1
description: Get git repository status
adapter_type: subprocess
side_effect: none
requires_approval: false

config:
  command: git
  args: [status, --porcelain]
  timeout_seconds: 30
  allowed_commands:
    - git status
    - git log

input_schema:
  type: object
  properties:
    repo_path:
      type: string
```

---

## Sources

Sources describe schema for retrieval queries.

**Location:** `configs/sources/<name>.yaml`

### Example (skills)

```yaml
source_id: skills
description: "Agent Skills repository (SKILL.md documents)"

fields:
  - name: skill_id
    type: string
    allowed_ops: [eq, in, contains, prefix]
    examples: ["daily-planner-synthesis"]
  - name: tags
    type: list_string
    allowed_ops: [contains, any_in, all_in]
    examples: ["planning", "calendar", "tasks"]
  - name: updated_at
    type: datetime
    allowed_ops: [gt, lt, gte, lte]

constraints:
  can_store_text: true
  allowed_entity_types: [skill]
  requires_live_fetch: false
```

---

## Context Packs

Context packs define reusable specs or skills that should be included in context.

**Location:** `configs/context_packs/<name>.yaml`

### Example (skills binding)

```yaml
pack_id: skills_daily_checkin
name: Skills: Daily Check-in
priority: 20
include_policy: always

selectors:
  - workflow_id: daily_checkin

refs:
  - ref_type: spec
    ref_id: skill_daily_planner_synthesis
    uri: "skills:///daily-planner-synthesis/SKILL.md"
    metadata:
      title: "daily-planner-synthesis"
      kind: "skill"
```

---

## Prompts

System prompts live in version-controlled files and are attached via context packs.

**Prompt files:** `prompts/system/`, `prompts/vaults/`, `prompts/workflows/`, `prompts/agents/`

**Prompt packs:** `configs/context_packs/prompt_*.yaml` with `metadata.kind: system_prompt`

### Example (kernel base prompts)

```yaml
pack_id: prompt_kernel_base
name: "Prompt: Kernel Base"
priority: 1
include_policy: always

refs:
  - ref_type: spec
    ref_id: prompt_base_system
    uri: "prompts:///system/base_system.md"
    metadata:
      kind: system_prompt
      layer: base
      title: "Kernel Base System Prompt"
```

---

## Thinking Tiers

Configure reasoning budget tiers.

**Location:** `configs/thinking_tiers.yaml`

### Full Schema

```yaml
tiers:
  0:  # Tier number
    name: string               # Tier name
    description: string        # When to use
    model: string              # LLM model
    reasoning_effort: string   # none, low, medium, high
    max_tokens: int            # Max output tokens
    run_critic: bool           # Run CriticEngine
    generate_candidates: int   # Multi-candidate count (1=single)

escalation_triggers:
  schema_validation_failed: bool
  quality_gates_failed: bool
  confidence_below_threshold: float
  risk_level_high: bool
  explicit_deep_analysis: bool
```

### Default Configuration

```yaml
tiers:
  0:
    name: "routing"
    description: "Classification, routing, simple extraction"
    model: "gpt-4o-mini"
    reasoning_effort: "low"
    max_tokens: 500
    run_critic: false
    generate_candidates: 1

  1:
    name: "standard"
    description: "Normal planning, most tasks"
    model: "gpt-4o"
    reasoning_effort: "medium"
    max_tokens: 2000
    run_critic: false
    generate_candidates: 1

  2:
    name: "deep"
    description: "Complex analysis, ambiguous tasks"
    model: "gpt-4o"
    reasoning_effort: "high"
    max_tokens: 4000
    run_critic: false
    generate_candidates: 1

  3:
    name: "deep_with_critic"
    description: "High stakes, requires verification"
    model: "gpt-4o"
    reasoning_effort: "high"
    max_tokens: 4000
    run_critic: true
    generate_candidates: 1

  4:
    name: "multi_candidate"
    description: "Highest stakes, multiple candidates with judge"
    model: "gpt-4o"
    reasoning_effort: "high"
    max_tokens: 4000
    run_critic: true
    generate_candidates: 3

escalation_triggers:
  schema_validation_failed: true
  quality_gates_failed: true
  confidence_below_threshold: 0.7
  risk_level_high: true
  explicit_deep_analysis: true
```

---

## Policies

Configure approval, rate limiting, and redaction policies.

**Location:** `configs/policies/default.yaml`

### Full Schema

```yaml
# Approval Rules
approval:
  mode: string                 # always, never, conditional
  require_for_capabilities:
    - capability.name@version
  require_for_side_effects:
    - external
  auto_approve_side_effects:
    - none
    - local

# Rate Limiting
rate_limit:
  enabled: bool
  requests_per_minute: int
  requests_per_hour: int
  per_capability: bool         # Separate limits per capability

# Data Redaction
redaction:
  enabled: bool
  fields:                      # Fields to always redact
    - password
    - api_key
    - token
  patterns:                    # Regex patterns to redact
    - "sk-[a-zA-Z0-9]+"
    - "\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b"
  mode: string                 # mask, hash, remove, truncate

# Scope Restrictions
scope:
  allowed_projects: list       # Project IDs, empty = all
  allowed_folders: list        # Folder paths
  denied_capabilities: list    # Blocked capabilities
```

### Example

```yaml
approval:
  mode: conditional
  require_for_capabilities:
    - calendar.create@v1
    - email.send@v1
    - tasks.delete@v1
  require_for_side_effects:
    - external
  auto_approve_side_effects:
    - none
    - local

rate_limit:
  enabled: true
  requests_per_minute: 30
  requests_per_hour: 500
  per_capability: true

redaction:
  enabled: true
  fields:
    - password
    - api_key
    - secret
    - token
    - credential
  patterns:
    - "sk-[a-zA-Z0-9]{48}"
    - "Bearer\\s+[a-zA-Z0-9._-]+"
  mode: mask

scope:
  allowed_projects: []  # All projects
  denied_capabilities:
    - system.exec@v1
```

---

## Memory Stores

Configure the three-store memory architecture.

### Document Store

SQLite-based with FTS5 for full-text search.

```python
from agent_kernel.memory.document_store import SQLiteDocumentStore

store = SQLiteDocumentStore("data/documents/documents.db")

# Store a document
doc_id = await store.store(
    content="Document content...",
    metadata={"title": "My Document", "project": "my-project"}
)

# Search documents
results = await store.search("search query", limit=10)
```

### Vector Store

SQLite-based with cosine similarity search.

```python
from agent_kernel.memory.vector_store import SQLiteVectorStore
from agent_kernel.services.embedding import create_embedding_service

embedding_service = create_embedding_service("openai")
store = SQLiteVectorStore("data/vectors/vectors.db")

# Store vector
embedding = await embedding_service.embed("Text to embed")
await store.store(
    doc_id="doc_123",
    vector=embedding.embedding,
    metadata={"title": "Document"}
)

# Semantic search
query_embedding = await embedding_service.embed("search query")
results = await store.search(query_embedding.embedding, limit=10)
```

### Graph Store

SQLite-based node and edge storage.

```python
from agent_kernel.memory.graph_store import SQLiteGraphStore

store = SQLiteGraphStore("data/graph/graph.db")

# Add nodes
await store.add_node("note_123", "Note", {"title": "My Note"})
await store.add_node("project_456", "Project", {"name": "My Project"})

# Add edge
await store.add_edge("note_123", "project_456", "BELONGS_TO", {})

# Query neighbors
neighbors = await store.get_neighbors("note_123", edge_type="BELONGS_TO")
```

---

## Tracing

Configure trace storage for audit and debugging.

### SQLite Sink

```python
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink

sink = SQLiteTraceSink("data/traces/traces.db")
```

### JSONL Sink

```python
from agent_kernel.tracing.sinks.jsonl_sink import JSONLTraceSink

sink = JSONLTraceSink("data/traces/traces.jsonl")
```

### Multi-Sink Store

```python
from agent_kernel.tracing import MultiSinkTraceStore
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink
from agent_kernel.tracing.sinks.jsonl_sink import JSONLTraceSink

store = MultiSinkTraceStore()
store.add_sink(SQLiteTraceSink("data/traces/traces.db"))
store.add_sink(JSONLTraceSink("data/traces/traces.jsonl"))

# All writes go to both sinks
store.write(trace)
```

---

## Quality Gates

Configure the 7 deterministic quality gates.

```python
from agent_kernel.executor import QualityGateRunner

runner = QualityGateRunner(
    confidence_threshold=0.7,      # Minimum confidence
    require_idempotency_keys=True, # Require keys for writes
    max_actions=10,                # Max actions per plan
)

result = runner.validate(plan, context_packet, agent_profile)

if not result.passed:
    print(f"Gate failures: {[f.message for f in result.failures]}")
    
if result.should_escalate:
    print(f"Should escalate: {result.escalation_reason}")
```

### Available Gates

1. **Schema Validity** - Plan structure
2. **Capability Allowlist** - All capabilities allowed
3. **Citations** - Required citations present
4. **Idempotency Keys** - Write actions have keys
5. **Action Count** - Reasonable action count
6. **Context References** - Cited refs exist in context
7. **Confidence** - Plan confidence above threshold

---

## CLI Configuration

The CLI reads configuration from:

1. Environment variables (`.env`)
2. `configs/` directory
3. Command-line arguments (override)

```bash
# Override database path
agent-kernel init --db-path /custom/path/db.sqlite

# Override workflow config
agent-kernel run-workflow daily_checkin \
    --intent "Custom intent" \
    --project "my-project"
```

---

## CLI Commands

Command names are source-first so it is clear what system they operate on.
Preferred naming uses `obsidian-*` for vault operations.

### Obsidian (Vault)

- `agent-kernel obsidian-sync` - Index vault notes into kernel stores
- `agent-kernel obsidian-watch` - Watch vault for changes and sync
- `agent-kernel obsidian-status` - Show vault index status
- `agent-kernel obsidian-search` - Search notes via hybrid retrieval

Aliases retained for compatibility:
`vault-sync`, `vault-watch`, `vault-status`, `search`.

### Task Management

Task sync with external backends is available via pluggable adapters.
See the task integration documentation for details on implementing
custom adapters.

### Obsidian Enrichment

Use `agent-kernel obsidian-sync --with-enrichment` to enrich notes
and keep indexes consistent in a single run.

For a full enrichment pass that also proposes link updates, run the
workflow:

`agent-kernel run-workflow obsidian_enrichment`

This workflow runs a vault sync with enrichment enabled and then applies
high-confidence link updates using `obsidian_link_enrichment` logic.

---

## Best Practices

### 1. Start Simple

Begin with minimal configuration:
- One agent profile
- Manual workflow trigger
- Local function adapters only

### 2. Use Environment Variables for Secrets

Never put API keys in YAML files. Use `.env`:

```yaml
# BAD
llm_config:
  api_key: sk-1234...

# GOOD (uses environment variable)
llm_config:
  provider: openai  # Reads OPENAI_API_KEY from env
```

### 3. Version Your Capabilities

Always include version in capability names:

```yaml
name: tasks.create@v1  # ✓ Good
name: tasks.create     # ✗ Bad
```

### 4. Use Approval Gates

Gate external writes:

```yaml
approval_policy:
  require_approval_for:
    - calendar.create@v1
    - email.send@v1
```

### 5. Enable Tracing

Always enable both sinks for debugging:

```bash
TRACE_JSONL_ENABLED=true  # Human-readable
# Plus SQLite for queries
```
