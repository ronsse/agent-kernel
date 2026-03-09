# Multi-Agent Debate

Demonstrates two engines proposing competing plans, with a judge function
selecting the best one based on risk and action count.

## What It Demonstrates

- Implementing two `AgentEngine` instances with different strategies
- Registering engines in the `EngineRegistry`
- Comparing plans by risk level and action count
- A simple "critic/judge" pattern for plan selection
- Executing only the selected plan

## Key Concepts

| Concept | Description |
|---------|-------------|
| `AgentEngine` | Protocol for plan generation --- engines accept context and return plans |
| `EngineRegistry` | Registry for managing multiple engines |
| `RiskAssessment` | Risk evaluation attached to every plan |
| `RiskLevel` | Enum: low, medium, high, critical |

## How It Works

### 1. Two Competing Engines

**OptimisticEngine** proposes a fast, higher-risk approach:

```python
class OptimisticEngine:
    async def propose(self, context, profile, thinking_policy=None):
        return Plan(
            summary="Move fast: batch process and deploy immediately",
            risk=RiskAssessment(level=RiskLevel.MEDIUM,
                reasons=["Batch processing skips validation"]),
            actions=[...],  # 3 actions
        )
```

**ConservativeEngine** proposes a cautious, lower-risk approach:

```python
class ConservativeEngine:
    async def propose(self, context, profile, thinking_policy=None):
        return Plan(
            summary="Process one at a time with validation",
            risk=RiskAssessment(level=RiskLevel.LOW,
                reasons=["Step-by-step validation"]),
            actions=[...],  # 4 actions (includes validation step)
        )
```

### 2. Judge Function

A simple judge compares plans and selects the lower-risk option:

```python
RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}

def judge_plans(plan_a, plan_b):
    risk_a = RISK_ORDER[plan_a.risk.level]
    risk_b = RISK_ORDER[plan_b.risk.level]
    if risk_b < risk_a:
        return plan_b, "conservative", "Lower risk"
    return plan_a, "optimistic", "Lower or equal risk"
```

In a production system, this could be replaced with a `CriticEngine` that uses
an LLM to evaluate plans against quality criteria.

### 3. Execute the Winner

Only the selected plan is executed:

```python
trace = await executor.execute(
    plan=selected,
    context_packet=context,
    agent_profile=profile,
    engine_id=winner,
)
```

## The Critic Pattern

This example demonstrates the simplest form of the critic pattern. Agent Kernel
supports more sophisticated approaches:

| Pattern | Complexity | Description |
|---------|-----------|-------------|
| **Risk-based judge** (this example) | Simple | Compare risk levels and action counts |
| **Quality gates** | Medium | Deterministic validators (schema, citations, constraints) |
| **CriticEngine** | Advanced | LLM-based plan evaluation with structured critique |
| **Multi-candidate + judge** | Expert | Generate 3-5 plans, LLM selects/merges the best |

## Expected Output

```
=== Multi-Agent Debate Example ===

--- Optimistic Engine ---
Plan: Move fast: batch process 3 data sources and deploy immediately
Risk: medium | Actions: 3

--- Conservative Engine ---
Plan: Process data sources one at a time with validation between each step
Risk: low | Actions: 4

--- Judge Decision ---
Selected: conservative
Reason: Lower risk (low vs medium)

--- Executing selected plan ---
Trace ID: 01KK...
Outcome:  completed
Tool calls: 4 total, 4 succeeded
```

## What to Explore Next

- [Tool Workflow](tool-workflow.md) --- approval gates, retry, and circuit breaker
- [Minimal Agent](minimal-agent.md) --- understand the core data flow first
