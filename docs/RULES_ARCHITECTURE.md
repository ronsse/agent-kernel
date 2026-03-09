# Rules Architecture

This document explains the shared rule system used by both Claude Code and Cursor.

## Overview

The project uses a **multi-layer rule system** to ensure AI coding assistants (Claude Code, Cursor, and others) use the same canonical rules:

```
┌─────────────────────────────────────────────────────────────────┐
│                     RULES ARCHITECTURE                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  LAYER 1: Canonical Sources (hand-edited)                        │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  CLAUDE.md               Core rules (~190 lines)            ││
│  │  .claude/rules/*.md      Specialist rules, path-scoped      ││
│  │  .shared-rules/*.md      Coding standards (cross-tool)      ││
│  └─────────────────────────────────────────────────────────────┘│
│                          │                                       │
│                          ▼                                       │
│  LAYER 2: Generated Files                                        │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  AGENTS.md  ◀── Concatenated from CLAUDE.md + .claude/rules/││
│  │  .cursor/rules/*.mdc  ◀── Wrappers for .shared-rules/      ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## File Locations

| File/Directory | Purpose | Committed? |
|----------------|---------|------------|
| `CLAUDE.md` | Canonical core rules (loaded every Claude Code session) | Yes |
| `.claude/rules/*.md` | Specialist rules with optional path-scoping | Yes |
| `.shared-rules/*.md` | Canonical rule definitions (shared across tools) | Yes |
| `AGENTS.md` | Generated flat concatenation for non-Claude tools | Yes |
| `.cursor/rules/*.mdc` | Cursor wrappers that include .shared-rules/ | No (gitignored) |
| `docs/design/*.md` | Deep architecture specs (referenced via @import) | Yes |

## How It Works

### For Claude Code

Claude Code reads `CLAUDE.md` at the repository root every session. It also auto-loads `.claude/rules/*.md` files, with optional path-scoping via YAML frontmatter:

```
CLAUDE.md                    (always loaded, ~190 lines)
.claude/rules/
├── workflow-checklists.md   (always loaded)
├── pr-checklist.md          (always loaded)
├── framework-agnosticism.md (loads for engine/**, workflows/**)
├── thinking-policy.md       (loads for engine/**)
└── integration-patterns.md  (loads for integrations/**, configs/**)
```

### For Cursor

Cursor uses `.cursor/rules/*.mdc` files that reference the canonical content in `.shared-rules/`:

```yaml
# .cursor/rules/coding-standards.mdc
---
description: Coding Standards
globs:
alwaysApply: false
---

@include ../../.shared-rules/coding-standards.md
```

The `.mdc` wrappers are auto-generated and gitignored. Run `scripts/sync_rules.py` after cloning.

### For Other AI Tools

Other tools read `AGENTS.md` at the repository root. This is a flat concatenation of `CLAUDE.md` + all `.claude/rules/*.md` files (with path-scoping frontmatter stripped).

## Sync Script

The `scripts/sync_rules.py` script:

1. Reads `CLAUDE.md` (canonical source)
2. Reads `.claude/rules/*.md` (strips `paths:` frontmatter)
3. Concatenates into `AGENTS.md`
4. Generates `.cursor/rules/*.mdc` wrappers

### Running the Sync

```bash
python scripts/sync_rules.py
```

Run this after:
- Editing `CLAUDE.md` or any file in `.claude/rules/`
- Editing any file in `.shared-rules/`
- Cloning the repository (to regenerate Cursor wrappers)

## Editing Rules

### To update core rules (Claude Code)

Edit `CLAUDE.md` directly:

```bash
# Edit the source
vim CLAUDE.md

# Regenerate AGENTS.md
python scripts/sync_rules.py

# Commit
git add CLAUDE.md AGENTS.md
git commit -m "Update core agent rules"
```

### To add or update specialist rules

Edit files in `.claude/rules/`:

```bash
# Edit or create a rule
vim .claude/rules/new-rule.md

# Regenerate AGENTS.md
python scripts/sync_rules.py

# Commit
git add .claude/rules/ AGENTS.md
git commit -m "Update specialist rules"
```

### To update shared rules (Cursor + reference)

Edit files in `.shared-rules/`:

```bash
# Edit the source
vim .shared-rules/coding-standards.md

# Regenerate Cursor wrappers
python scripts/sync_rules.py

# Commit (wrappers are gitignored)
git add .shared-rules/
git commit -m "Update coding standards"
```

## Path-Scoping (Claude Code Only)

`.claude/rules/` files can include YAML frontmatter to restrict when they load:

```markdown
---
paths:
  - "src/agent_kernel/engine/**"
  - "src/agent_kernel/workflows/**"
---
# Framework Agnosticism Rules
...
```

This rule only loads when Claude is working on files matching the glob. Other tools reading `AGENTS.md` get all rules unconditionally.

## Naming Conventions

### Claude Rules (`.claude/rules/`)

Use descriptive kebab-case names:
- `workflow-checklists.md` - Task checklists
- `pr-checklist.md` - PR requirements
- `framework-agnosticism.md` - Framework integration rules
- `thinking-policy.md` - Reasoning tier system
- `integration-patterns.md` - External integration rules

### Shared Rules (`.shared-rules/`)

Use descriptive kebab-case names:
- `coding-standards.md`
- `agent-workflow.md`
- `project-organization.md`

## Best Practices

1. **Single source of truth**: Edit `CLAUDE.md` or `.claude/rules/`, never edit `AGENTS.md` directly

2. **Always sync after edits**: Run `sync_rules.py` after any rule changes

3. **Commit generated files**: `AGENTS.md` should be committed so it's available without running the script

4. **Cursor wrappers are ephemeral**: They're gitignored and regenerated on demand

5. **Keep CLAUDE.md concise**: Core rules only; specialist content goes in `.claude/rules/`

## Troubleshooting

### Cursor rules not loading

```bash
# Regenerate wrappers
python scripts/sync_rules.py

# Restart Cursor
```

### AGENTS.md out of sync

```bash
python scripts/sync_rules.py
git diff AGENTS.md  # See what changed
```

### Adding a new shared rule

1. Create `.shared-rules/new-rule.md`
2. Add wrapper config in `sync_rules.py` (in `wrapper_configs` list)
3. Run `python scripts/sync_rules.py`
