---
paths:
  - "src/agent_kernel/integrations/**"
  - "configs/**"
---

# Integration Patterns Rules

## Core Principle

**Obsidian vault stays source-of-truth. Agent system maintains derived indexes that can always be rebuilt.**

## Hard Rules

### Source-of-Truth Pattern

| Data | Source | Derived Indexes |
|------|--------|-----------------|
| **Notes/Documents** | Obsidian vault (filesystem) | Document Store, Graph Store, Vector Store |
| **Tasks** | Obsidian checkboxes (`- [ ]`) | Task index in Graph |
| **Calendar** | Outlook/Google Calendar | Cached in event log for context |
| **Captures** | Google Keep -> Import to Obsidian | Not stored separately |

### Integrations Are NOT Tied to Kernel

Build external integrations as:
- Extensible agents
- Pluggable workflows
- Capability adapters

**NOT** as core kernel components.

### Three-Store Separation

| Store | Contains | NOT Contains |
|-------|----------|--------------|
| **Document Store** | Full text, content hash, metadata | -- |
| **Graph Store** | Nodes, edges, relationships | Full note text |
| **Vector Store** | Chunks, embeddings | Full documents |

## Tag Separation Rule

**ALWAYS separate human tags from machine tags.**

```yaml
# Human tags (user sets)
tags: [project/agent-system, meeting]

# Machine tags (LLM sets)
auto:
  tags: [workflow, memory]
  class: "architecture"
```

**NEVER pollute human tags with auto-generated content.**

## Stable ID Rule

- Paths change. Titles change. **IDs should not.**
- Generate `id` once, write to frontmatter, never regenerate
- Use `id` as primary key in all indexes

## External Write Rule

**ALL external writes must be approval-gated:**
- Calendar event creation
- Task system writes
- Email sends
- Any non-local side effect

Pattern:
```
Agent proposes -> You approve -> Executor writes -> Trace logs
```

## Trigger Patterns

| Trigger | Use For |
|---------|---------|
| **File Watcher** | Real-time note changes (debounce 10-30s) |
| **Reconciliation Job** | Nightly safety net, catch missed events |
| **Schedule** | Daily/weekly synthesis workflows |
| **Manual Command** | User-initiated enrichment |

## Enrichment Mode Rule

| Mode | Auto-Apply | Require Approval |
|------|------------|------------------|
| **Suggest** (default) | `auto.*` fields only | Human tags, moves, renames |
| **Auto-apply** | Stable ID, `auto.*` fields | Everything else |

## Summary

1. **Obsidian = Source of Truth**
2. **Integrations = Pluggable Workflows** (not kernel)
3. **Human != Machine Tags**
4. **Stable IDs** for all entities
5. **Approval Gates** for external writes
6. **Debounce + Idempotency** for processing
7. **Trace Everything**

**Reference:** See `docs/design/12-integration-patterns.md` for full patterns.
