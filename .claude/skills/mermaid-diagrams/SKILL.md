---
name: mermaid-diagrams
description: >-
  Expert guidance for creating high-quality Mermaid diagrams. Use when creating,
  improving, or reviewing any Mermaid diagram including flowcharts, sequence,
  C4 architecture, ERD, class, or state diagrams. Applies consistent color
  palettes and readability best practices.
version: "1.0.0"
allowed_tools:
  - mcp__mermaid__mermaid_preview
  - mcp__mermaid__mermaid_save
  - Read
  - Write
  - Edit
  - Glob
---

# Mermaid Diagrams — Expert Skill

You are an expert at creating clear, well-structured Mermaid diagrams. Follow
these guidelines for every diagram you create or modify.

## Tool Integration

- **Preview:** Use `mermaid_preview` with format `svg` during iteration. Use a
  descriptive `preview_id` (e.g., `"architecture"`, `"data-flow"`).
- **Save:** Use `mermaid_save` to export the final diagram after the user
  approves the preview.
- **Source files:** Always save the `.mmd` source alongside the rendered output.
  Include YAML front matter with at least a `title` field:
  ```
  ---
  title: System Architecture
  ---
  ```
- **Iteration:** Preview first, refine based on rendering, then save. Mermaid
  rendering has quirks — always verify visually before declaring done.

## Diagram Type Selection

| What you want to show | Diagram type | Reference |
|------------------------|-------------|-----------|
| System boundaries, external actors | C4 Context | `references/c4.md` |
| Internal containers/services | C4 Container | `references/c4.md` |
| Process flow, pipelines, architecture | Flowchart | `references/flowchart.md` |
| Data models, table schemas | ERD | `references/erd.md` |
| Temporal interactions, API calls | Sequence | `references/sequence.md` |
| Type hierarchies, interfaces | Class | `references/class.md` |
| Lifecycle, FSM, state transitions | State | `references/state.md` |

When unsure, default to **flowchart** — it's the most flexible.

## Universal Best Practices

### Direction

- **TB (top-to-bottom):** Architecture diagrams, hierarchies, layer diagrams
- **LR (left-to-right):** Pipelines, timelines, sequential processes
- **RL:** Rare — use for "pull" semantics or right-to-left reading

### Node Naming

- Use short camelCase IDs: `DocStore`, `VaultWatcher`, `ContextPacket`
- Use display labels with `<br/>` for multi-line and `<i>` for secondary info:
  ```
  DocStore["<b>DocumentStore</b><br/><i>memory/document_store.py</i><br/>FTS5 full-text search"]
  ```
- Every node must have a human-readable label — never show raw IDs

### Edge Labels

- Keep edge labels short (1-4 words): `"file change"`, `"semantic search"`
- Use `-->` for primary flow, `-.->` for secondary/optional, `===` for emphasis
- Label edges that aren't self-evident; skip labels on obvious connections

### Subgraphs

- Group related nodes into subgraphs with clear titles
- Use `direction` inside subgraphs when flow differs from parent
- Limit nesting to 2-3 levels — deeper nesting breaks readability

### Source Organization

- Add section comments with `%%` separator lines between logical groups
- Order: subgraph definitions first, then inter-group connections, then styles
- Keep one blank line between sections

```mermaid
%% ═══════════════════════════════════════════════
%% SECTION NAME
%% ═══════════════════════════════════════════════
```

## Color Palette

Use semantic color classes consistently across all diagrams. These classes
encode the **role** of a node, not its visual appearance.

### Class Definitions

Paste this block into any flowchart that needs colors:

```
classDef store fill:#4a90d9,stroke:#2c5f8a,color:#fff
classDef schema fill:#e8a838,stroke:#b8832c,color:#fff
classDef engine fill:#7b68ee,stroke:#5a4cba,color:#fff
classDef exec fill:#dc5c5c,stroke:#a84444,color:#fff
classDef ingest fill:#5cb85c,stroke:#3d7e3d,color:#fff
classDef runner fill:#9b59b6,stroke:#7d3f98,color:#fff
classDef external fill:#95a5a6,stroke:#7f8c8d,color:#fff
classDef user fill:#1abc9c,stroke:#16a085,color:#fff
classDef highlight fill:#e74c3c,stroke:#c0392b,color:#fff
classDef muted fill:#bdc3c7,stroke:#95a5a6,color:#333
```

### Role Reference

| Class | Fill | Use for |
|-------|------|---------|
| `store` | Blue `#4a90d9` | Data stores, databases, persistence |
| `schema` | Amber `#e8a838` | Schemas, contracts, data models |
| `engine` | Purple `#7b68ee` | Processing engines, LLM, compute |
| `exec` | Red `#dc5c5c` | Execution, validation, enforcement |
| `ingest` | Green `#5cb85c` | Ingestion, input, import pipelines |
| `runner` | Violet `#9b59b6` | Orchestration, workflow runners |
| `external` | Gray `#95a5a6` | External/third-party systems |
| `user` | Teal `#1abc9c` | Users, personas, actors |
| `highlight` | Bright red `#e74c3c` | Critical paths, warnings |
| `muted` | Light gray `#bdc3c7` | Disabled, deprecated, background |

### Application

Apply classes with `class` statements at the end of the diagram:

```
class DocStore,VecStore,GraphStore store
class ContextPacket,Plan schema
class Executor,Broker exec
```

### Accessibility

- All colors have sufficient contrast with white text (except `muted` which uses dark text)
- Fill/stroke pairs are distinguishable in grayscale
- Use shape variations (rectangles, rounded, diamonds) alongside color for redundancy

## Sizing Guide

| Diagram type | Default width | Default height | Notes |
|-------------|--------------|---------------|-------|
| Flowchart (small) | 800 | 600 | 5-15 nodes |
| Flowchart (large) | 1200 | 900 | 15+ nodes, use `scale: 1.5` |
| Sequence | 800 | 600 | Grows vertically with messages |
| C4 Context | 1000 | 700 | Few elements, needs spacing |
| C4 Container | 1200 | 800 | More elements than Context |
| ERD | 800 | 600 | Grows with entity count |
| Class | 800 | 600 | Grows with class count |
| State | 800 | 600 | Usually compact |

- Use `theme: "default"` for most diagrams. Use `"neutral"` for print/docs.
- Set `scale: 2` for high-DPI output (default).

## Quality Checklist

Before saving any diagram, verify:

1. Every node has a readable label (not just an ID)
2. Semantic color classes are applied consistently
3. Edge labels are present where flow isn't obvious
4. No orphan nodes (every node has at least one connection)
5. Source has section comments separating logical groups
6. Subgraph nesting doesn't exceed 3 levels
7. Direction is appropriate (TB for architecture, LR for processes)
8. `.mmd` source file has YAML front matter with `title`
9. Diagram renders correctly in `mermaid_preview` (no syntax errors)
10. Text is legible at the chosen dimensions (increase size if crowded)

## Reference Files

Load the relevant reference when creating a specific diagram type:

- **`references/flowchart.md`** — Flowcharts: node shapes, subgraphs, classDef,
  HTML labels, pipeline/hub-spoke/layered patterns
- **`references/sequence.md`** — Sequence diagrams: participants, activations,
  loops, alt/opt, notes. No `style` directives allowed.
- **`references/c4.md`** — C4 architecture: Context, Container, Component,
  Deployment. System boundaries and relationships.
- **`references/erd.md`** — Entity-Relationship: entities, attributes, crow's-foot
  cardinality notation, normalized schemas
- **`references/class.md`** — UML class diagrams: visibility, stereotypes,
  relationships, generics with `~T~`
- **`references/state.md`** — State diagrams: transitions, fork/join, choice,
  composite states, lifecycle patterns
