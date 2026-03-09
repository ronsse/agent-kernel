# Skills System

Skills are portable units of procedural guidance that agents can load and follow. A skill packages instructions, reference materials, and metadata into a self-contained bundle that enhances agent behavior for specific tasks.

## What Is a Skill?

A skill consists of:

- **SKILL.md** -- the main instruction file (~130 lines) with rules, patterns, and decision trees
- **References** -- supporting files (e.g., `references/flowchart.md`, `references/best-practices.md`)
- **Manifest** -- metadata about the skill (ID, name, compatibility, allowed tools)

Skills are loaded as optional context when an agent needs specific expertise.

## SkillManifest

Every skill has a manifest describing its contents:

```python
from agent_kernel.core.schemas import SkillManifest, SkillOrigin

manifest = SkillManifest(
    skill_id="mermaid-diagrams",
    name="Mermaid Diagrams",
    description="Expert guidance for creating Mermaid diagrams",
    license="MIT",
    compatibility=">=0.1.0",
    allowed_tools=["mermaid_preview", "mermaid_save"],
    metadata={"category": "diagramming"},
    origin=SkillOrigin(
        kind="local",
        path=".claude/skills/mermaid-diagrams",
        installed_at=datetime.now(),
        content_hash="sha256:abc123...",
    ),
)
```

### Manifest Fields

| Field | Description |
|-------|-------------|
| `skill_id` | Unique identifier |
| `name` | Display name |
| `description` | What the skill provides |
| `license` | License for the skill content |
| `compatibility` | Kernel version compatibility range |
| `allowed_tools` | Tools the skill may reference |
| `origin` | Where the skill came from (local, git, registry) |

## Skill Origins

Skills can come from multiple sources:

| Origin | Description |
|--------|-------------|
| `local` | A directory on disk |
| `git` | A Git repository (with ref/branch) |
| `registry` | A future skill registry |

```python
from agent_kernel.core.schemas import SkillOrigin

# Local skill
local = SkillOrigin(kind="local", path=".claude/skills/my-skill")

# Git skill
git = SkillOrigin(kind="git", repo="https://github.com/org/skills", ref="main")
```

## Loading a Skill

When a skill is loaded, the system returns a `SkillLoadResult` with the manifest, resource inventory, and file contents:

```python
from agent_kernel.core.schemas import SkillLoadResult, SkillResourceRef

result = SkillLoadResult(
    manifest=manifest,
    resources=[
        SkillResourceRef(path="SKILL.md", kind="skill_md", hash="..."),
        SkillResourceRef(path="references/flowchart.md", kind="reference", hash="..."),
    ],
    files={
        "SKILL.md": "# Mermaid Diagrams Skill\n...",
        "references/flowchart.md": "# Flowchart Reference\n...",
    },
)
```

### SkillResourceRef

Each resource in a skill is tracked:

| Field | Description |
|-------|-------------|
| `path` | Relative path within the skill |
| `kind` | `skill_md`, `reference`, `asset`, or `script` |
| `hash` | Content hash for change detection |
| `bytes` | File size (optional) |

## Creating a Custom Skill

To create a skill:

1. **Create a directory** with a `SKILL.md` file
2. **Add reference files** in a `references/` subdirectory
3. **Define the manifest** in the `SKILL.md` frontmatter or a separate manifest file

### Skill Directory Structure

```
my-skill/
  SKILL.md              # Main instruction file
  references/
    patterns.md         # Reference material
    examples.md         # Code examples
```

### SKILL.md Format

```markdown
---
skill_id: my-custom-skill
name: My Custom Skill
description: Guidance for a specific task domain
---

# My Custom Skill

## When to Use

[Describe when this skill applies]

## Rules

1. [Rule 1]
2. [Rule 2]

## Decision Tree

[Provide structured decision guidance]
```

## Context Integration

Skills integrate with the context system through `RefType.SKILL`:

```python
from agent_kernel.core.schemas import ContextRef, RefType

skill_ref = ContextRef(
    ref_type=RefType.SKILL,
    ref_id="mermaid-diagrams",
    metadata={"name": "Mermaid Diagrams", "version": "1.0.0"},
)
```

This allows skills to appear in `ContextPacket` items and be cited in Plans like any other context source.

## Next Steps

- [Architecture Guide](architecture.md) -- how skills fit into the overall system
- [Schema Contracts](../concepts/schemas.md) -- the `SkillManifest` and related schemas
- [Thinking Escalation](thinking-escalation.md) -- how skills interact with reasoning tiers
