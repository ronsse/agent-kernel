# CLI-First Integration Evaluation

**Version:** 1.0.0
**Status:** Proposal
**Date:** 2026-03-06

---

## Context

The `gws` CLI (`@googleworkspace/cli`) demonstrates an optimal pattern for AI agent integration with external services:

- **One binary, all APIs** — reads Google's Discovery Service at runtime to build commands dynamically
- **Structured JSON output** — `--format json` by default, machine-parseable
- **Token-efficient agent skills** — 40+ SKILL.md files that give agents just enough context to use the CLI without embedding API docs
- **`--dry-run`** — validates locally before calling APIs
- **`--page-all`** — streams NDJSON for large result sets
- **Helper commands** (`+agenda`, `+triage`, `+insert`) — ergonomic shortcuts for common workflows

This document evaluates applying the `gws` pattern to the agent-kernel ecosystem and outlines what changes.

---

## The Token-Saving Pattern

The key insight from `gws` skills is a **three-layer architecture** that minimizes context window consumption:

### Layer 1: Shared Reference (loaded once)
```
gws-shared/SKILL.md  (~40 lines)
  - Auth setup
  - Global flags (--format, --dry-run, --params, --json)
  - CLI syntax pattern: gws <service> <resource> <method> [flags]
  - Security rules
```

### Layer 2: Service Overview (loaded per-service)
```
gws-calendar/SKILL.md  (~80 lines)
  - Lists ALL resources and methods (calendarList, events, freebusy, etc.)
  - Links to helper commands
  - Teaches: "use `gws schema calendar.<resource>.<method>` to discover params"
```

### Layer 3: Helper Commands (loaded on-demand)
```
gws-calendar-agenda/SKILL.md  (~30 lines)
  - Single-purpose: show upcoming events
  - Flags table, examples, tips
  - References back to shared + parent service
```

**Why this is token-efficient:**
- Agent loads ~40 lines of shared context, not thousands of API docs
- `gws schema <method>` is self-documenting — agent can introspect at runtime
- Helper commands (`+agenda`, `+insert`, `+triage`) cover 80% of use cases in ~30 lines each
- Full API surface available via raw `gws <service> <resource> <method>` for edge cases

**Contrast with current kernel approach:**
- Each Google adapter is 400-700 lines of Python wrapping API calls
- Capability YAML files define schemas statically (must be maintained)
- Auth is handled per-adapter with custom token management
- Agents can't introspect available operations at runtime

---

## Current State: Google Integration in Agent Kernel

### What Exists (4,086 lines of Python)

| File | Lines | What It Does |
|------|-------|-------------|
| `integrations/google/auth.py` | 655 | OAuth2 flow for Calendar, Tasks, Gmail, Drive, Keep |
| `integrations/google/calendar_adapter.py` | 467 | Google Calendar API wrapper |
| `integrations/google/gmail_adapter.py` | 720 | Gmail API wrapper |
| `integrations/google/keep_adapter.py` | 742 | Google Keep (unofficial gkeepapi) |
| `integrations/google/keep_obsidian_sync.py` | 638 | Keep-to-Obsidian sync pipeline |
| `integrations/google/tasks_adapter.py` | 410 | Google Tasks API wrapper |
| `tools/library/google_calendar.py` | 387 | Calendar tool broker integration |
| 6 capability YAMLs | ~120 | Static schema definitions |

### What's Actually Used

| Integration | Used By | Status |
|-------------|---------|--------|
| Calendar (read) | `calendar-import` CLI, meeting note derivation | Active |
| Calendar (write) | Never used (approval-gated, no consumer) | Dead code |
| Tasks | `task-sync` queries task lists | Lightly used |
| Gmail | Not wired to any workflow | Dead code |
| Keep | Deprecated in favor of task backend | Dead code |
| Keep-Obsidian sync | Deprecated | Dead code |

### Problems

1. **Maintenance burden** — 4,086 lines of API wrapper code for 2 active integrations
2. **Auth complexity** — Custom OAuth flow per service, token refresh logic, credential management
3. **Static schemas** — Capability YAMLs must be manually updated when API changes
4. **Wrong layer** — Google API access is an *agent tool*, not a *kernel concern*. The kernel should handle memory, traces, and context — not be a Google API client
5. **No runtime introspection** — Agents can't discover what operations are available

---

## Recommendation: Move Google to External Agent Runtime via `gws` CLI

### Architecture

```
BEFORE (current):
  External Agent → kernel API → kernel Google adapter → Google API
  (4000+ lines of Python, custom auth, static schemas)

AFTER (proposed):
  External Agent → gws CLI → Google API
  (0 lines of Python, gws handles auth, runtime discovery)
```

### What Moves to External Agent Runtime

| Component | Action | Rationale |
|-----------|--------|-----------|
| Google Calendar read | Replace with `gws calendar +agenda` / `gws calendar events list` | `gws` handles auth, pagination, JSON output |
| Google Calendar write | Replace with `gws calendar +insert` | `gws` confirms writes, `--dry-run` support |
| Google Tasks | Replace with `gws tasks` | Runtime discovery via `gws schema tasks.*` |
| Gmail | Replace with `gws gmail +triage` / `gws gmail +send` | Never implemented in kernel anyway |
| Google Keep | Drop entirely | Deprecated, `gws keep` available if ever needed |
| Google Auth | Replace with `gws auth login` | One auth flow for all services |

### What Stays in Agent Kernel

| Component | Why It Stays |
|-----------|-------------|
| `calendar_sources.yaml` | Declarative config for *which* calendars to process and *what derivations* to create |
| Meeting note derivation logic | Kernel workflow that creates Obsidian notes from calendar events — this is memory/context, not API access |
| Calendar event → graph node mapping | Graph ontology is kernel's domain |

### How Meeting Notes Work After Migration

```
1. External Agent cron job runs: gws calendar events list --params '{"calendarId":"...", "timeMin":"...", "timeMax":"..."}' --format json
2. Output piped to kernel CLI: agent-kernel calendar-import --from-stdin
3. Kernel applies filters from calendar_sources.yaml
4. Kernel creates meeting notes in Obsidian vault
5. Kernel updates graph with calendar event nodes
```

Or simpler: the External Agent agent skill reads events via `gws`, applies the filter logic itself (it's just keyword matching), and calls `agent-kernel` CLI to create the meeting notes.

---

## CLI-First Pattern for Agent Kernel

Applying the same `gws` pattern to the kernel's own operations.

### Current Kernel CLI (55 commands)

The kernel already has a rich Typer CLI (`agent-kernel <command>`). The gap is:
1. **No structured JSON output** — commands print Rich tables, not parseable data
2. **No External Agent skills** — agents don't know these commands exist
3. **No `--format` flag** — can't switch between human and machine output

### Proposed Changes

#### 1. Add `--format json` to all read commands

Priority commands (used by External Agent agents):

| Command | What It Returns |
|---------|----------------|
| `agent-kernel list-traces --format json` | Recent traces as JSON array |
| `agent-kernel show-trace <id> --format json` | Single trace detail |
| `agent-kernel list-approvals --format json` | Pending approvals |
| `agent-kernel list-runs --format json` | Workflow run history |
| `agent-kernel list-capabilities --format json` | Available capabilities |
| `agent-kernel search --format json` | Obsidian semantic search results |
| `agent-kernel list-lessons --format json` | Experience lessons |
| `agent-kernel task-list --format json` | Task list |
| `agent-kernel health --format json` | Service health status |
| `agent-kernel thinking-stats --format json` | Reasoning metrics |

#### 2. Create External Agent Skills for Kernel CLI

Following the `gws` three-layer pattern:

```
skills/
  agent-kernel-shared/SKILL.md     # Auth (none needed), global flags, CLI syntax
  agent-kernel-traces/SKILL.md     # Trace commands: list, show, rate
  agent-kernel-memory/SKILL.md     # Knowledge search, context assembly
  agent-kernel-workflows/SKILL.md  # Run, list, resume, approve/deny
  agent-kernel-tasks/SKILL.md    # Task commands: list, add, complete, sync
  agent-kernel-vault/SKILL.md      # Vault sync, search, status
  agent-kernel-observe/SKILL.md    # Health, thinking-stats, retention-status
```

Each skill is ~30-50 lines: command reference, flags, examples, tips. Agents load only what they need.

#### 3. Deprecate MCP Server (Future)

The MCP server (`src/agent_kernel/mcp_server/`) provides 5 tool categories:
- memory, knowledge, experience, skill, context

With CLI-first + JSON output, External Agent agents can call `agent-kernel` directly via exec. MCP adds a transport layer that's unnecessary when the agent framework already has shell access.

**Timeline:** Keep MCP for now (other tools may use it), but don't invest in expanding it. New capabilities go CLI-first.

---

## What to Delete from Agent Kernel

### Immediate (this milestone)

| Path | Lines | Action |
|------|-------|--------|
| `integrations/google/keep_adapter.py` | 742 | Delete — deprecated, never reliable |
| `integrations/google/keep_obsidian_sync.py` | 638 | Delete — deprecated |
| `integrations/google/gmail_adapter.py` | 720 | Delete — never wired to any workflow |
| `configs/capabilities/google_keep.list@v1.yaml` | 27 | Delete |
| `configs/capabilities/google_docs.get_text@v1.yaml` | ~20 | Delete — no adapter exists |
| `configs/capabilities/google_drive.list@v1.yaml` | ~20 | Delete — no adapter exists |
| CLI commands: `keep-auth`, `keep-logout`, `keep-sync` | ~200 | Delete |

**Total: ~2,367 lines removed**

### After `gws` is installed and skills created

| Path | Lines | Action |
|------|-------|--------|
| `integrations/google/auth.py` | 655 | Delete — `gws auth login` replaces |
| `integrations/google/calendar_adapter.py` | 467 | Delete — `gws calendar` replaces |
| `integrations/google/tasks_adapter.py` | 410 | Delete — `gws tasks` replaces |
| `tools/library/google_calendar.py` | 387 | Delete — tool broker wrapper unnecessary |
| `configs/capabilities/google_tasks.*` | ~60 | Delete |
| CLI commands: `google-auth`, `google-status`, `calendar-sync` | ~300 | Delete or refactor to shell out to `gws` |

**Total: ~2,279 more lines removed**

### Keep (refactor)

| Path | Action |
|------|--------|
| `calendar_sources.yaml` | Keep — declarative config for derivation policies |
| `calendar-import` CLI command | Refactor to accept `--from-stdin` JSON (from `gws` output) |
| Meeting note derivation | Keep in kernel — this is context/memory work |

---

## Implementation Order

### Phase 1: Clean Dead Code (Quick, no dependencies)
1. Delete Keep adapter, Keep-Obsidian sync, Gmail adapter
2. Delete unused capability YAMLs
3. Delete `keep-*` CLI commands
4. ~2,400 lines removed, zero risk

### Phase 2: Install `gws` + Create Skills (External Agent side)
1. `npm install -g @googleworkspace/cli`
2. `gws auth login` (browser OAuth — replaces kernel's custom auth flow)
3. Create 2-3 External Agent skills: `gws-calendar-agenda`, `gws-calendar-insert`, `gws-gmail-triage`
4. Test from External Agent: "What's on my calendar today?" → agent uses `gws calendar +agenda`

### Phase 3: Add `--format json` to Kernel CLI
1. Add `OutputFormat` enum and `--format` option to Typer commands
2. Start with high-value commands: `list-traces`, `list-approvals`, `health`, `search`
3. JSON output returns same data as Rich tables, just structured

### Phase 4: Create Kernel CLI Skills for External Agent
1. Write `agent-kernel-shared/SKILL.md` (global reference)
2. Write per-domain skills (traces, memory, workflows, tasks, vault, observe)
3. Register as External Agent custom skills
4. Test: External Agent agent can run workflows, check traces, search memory via CLI

### Phase 5: Delete Remaining Google Code
1. Delete `auth.py`, `calendar_adapter.py`, `tasks_adapter.py`
2. Refactor `calendar-import` to accept stdin JSON
3. Delete `google-auth`, `google-status` CLI commands
4. Remove `google-auth-oauthlib`, `google-api-python-client` from dependencies

---

## Decision Summary

| Question | Answer |
|----------|--------|
| Where do Google integrations live? | **External Agent** — via `gws` CLI + skills |
| Where does meeting note derivation live? | **Kernel** — it's memory/context work |
| How do agents talk to kernel? | **CLI-first** — `agent-kernel <command> --format json` |
| What about MCP server? | **Keep but freeze** — don't expand, new capabilities go CLI |
| What about the Python SDK? | **Keep** — for programmatic integration (scripts, other Python tools) |
| How much code gets deleted? | **~4,000 lines** across two phases |

---

## Appendix: `gws` Skill Pattern Reference

### Shared Skill (loaded once per session)
```markdown
# gws -- Shared Reference
## Authentication
  gws auth login          # Browser OAuth
## Global Flags
  --format json|table|yaml|csv
  --dry-run               # Validate without calling API
## CLI Syntax
  gws <service> <resource> <method> [flags]
## Method Flags
  --params '{"key":"val"}'  # URL parameters
  --json '{"key":"val"}'    # Request body
  --page-all                # Auto-paginate (NDJSON)
```

### Service Skill (loaded per-domain)
```markdown
# calendar (v3)
## Helper Commands
  +agenda   — show upcoming events
  +insert   — create a new event
## API Resources
  events: list, get, insert, patch, delete, quickAdd
  calendarList: list, get
  freebusy: query
## Discovering Commands
  gws calendar --help
  gws schema calendar.events.list
```

### Helper Skill (loaded on-demand)
```markdown
# calendar +agenda
## Usage
  gws calendar +agenda --today
  gws calendar +agenda --week --calendar 'Work'
## Flags
  --today, --tomorrow, --week, --days N, --calendar NAME
## Tips
  Read-only. Queries all calendars by default.
```
