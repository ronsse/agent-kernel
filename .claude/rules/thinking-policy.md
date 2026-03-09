---
paths:
  - "src/agent_kernel/engine/**"
---

# Thinking Policy Rules

## Core Principle

**Don't pre-classify complexity. Try fast, validate, escalate on evidence.**

## Reasoning Depth Control

### Primary Lever: `reasoning.effort`

| Effort | Use Case |
|--------|----------|
| `none`/`low` | Routing, classification, simple extraction |
| `medium` | Normal planning, standard workflows |
| `high` | Deep analysis, ambiguity, complex reasoning |

## Recommended Pattern: Attempt -> Gate -> Escalate

```
Try Cheap/Fast -> Quality Gates -> Pass? Execute : Escalate
```

**Why this beats pre-classification:**
- Evidence-driven (escalate on actual failure)
- No wasted "deep thinking" on easy tasks
- Quality gates catch real problems

## Key Components to Add

| Component | Purpose |
|-----------|---------|
| **ThinkingPolicyController** | Decides reasoning budget per task |
| **QualityGateRunner** | Deterministic validators (schema, citations, constraints) |
| **EscalationManager** | Manages attempt -> gate -> escalate flow |
| **CriticEngine** (optional) | Challenges plans for high-reliability tasks |

## Escalation Triggers

- Schema validation failed
- Quality gates failed
- Confidence below threshold (0.7)
- Risk level is high
- Explicit "deep analysis" request

## Tier System

```yaml
Tier 0: routing      (cheap model, low effort)
Tier 1: standard     (normal model, medium effort)
Tier 2: deep         (best model, high effort)
Tier 3: deep+critic  (best model + verification pass)
```

## Rules

1. **Default to standard tier** - Most tasks don't need deep thinking
2. **Escalate on evidence** - Not predictions
3. **Use Structured Outputs** - Makes orchestration reliable
4. **Add confidence to Plan** - Enables automatic escalation
5. **Critic is optional** - Only for high-stakes/high-reliability
6. **Log reasoning metadata** - Tier, escalations, gate failures in traces

## When to Just Use Best Model

For genuinely deep analysis, use the best model. Architecture helps you:
- Use it efficiently (not for easy tasks)
- Validate outputs before execution
- Keep costs predictable

But the ceiling is still the model's capability.

**Reference:** See `docs/design/11-thinking-policy.md` for full implementation spec.
