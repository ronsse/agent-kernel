# Trust Boundaries

Agent Kernel enforces a strict trust boundary between what agents *request* and what the system *allows*. This guide covers how the trust model works, how approval policies are computed, and how the approval flow operates.

## The Trust Boundary

Agents produce Plans with `ActionRequest` entries. Each action includes `side_effect` and `requires_approval` fields -- but these are **non-authoritative hints**. The executor computes the real values from system policy.

```
Agent-Provided (Hints Only)          System-Computed (Authoritative)
----------------------------          ------------------------------
ActionRequest.side_effect       -->   CapabilityDef.side_effect_level
ActionRequest.requires_approval -->   CapabilityDef.requires_approval_default
                                      + AgentProfile.approval_policy
```

Both the agent-requested and system-computed values are recorded in the `ToolCallRecord` for debugging:

| Field | Source | Authority |
|-------|--------|-----------|
| `requested_side_effect` | Agent | Hint only |
| `requested_requires_approval` | Agent | Hint only |
| `effective_side_effect` | CapabilityDef | Authoritative |
| `effective_requires_approval` | Policy computation | Authoritative |

## Effective Policy Algorithm

The executor computes the effective policy for each action:

```python
def compute_effective_policy(action, capability, agent_profile):
    # Side effect from capability definition (authoritative)
    effective_side_effect = capability.side_effect_level

    # Approval from capability default + agent profile policy
    effective_requires_approval = capability.requires_approval_default

    # If the side effect is not in the auto-approve list, require approval
    if effective_side_effect not in agent_profile.approval_policy.auto_approve_side_effects:
        effective_requires_approval = True

    return effective_side_effect, effective_requires_approval
```

### Example Configurations

**Permissive agent** -- auto-approves local writes:

```yaml
approval_policy:
  auto_approve_side_effects: [none, local]
  max_auto_approve_risk: low
```

**Restricted agent** -- requires approval for everything except reads:

```yaml
approval_policy:
  auto_approve_side_effects: [none]
  require_approval_for:
    - calendar.create@v1
    - tasks.delete@v1
```

## Approval Flow

When an action requires approval, the system creates a durable `ApprovalRequest`:

```
Action needs approval
       |
       v
  Create ApprovalRequest (persisted to SQLite)
       |
       v
  Workflow pauses (status: WAITING_APPROVAL)
       |
       v
  Human reviews via CLI or API
       |
  +----+----+
  |         |
Approve   Deny
  |         |
  v         v
Resume    Cancel
workflow  workflow
```

### ApprovalRequest Record

Each pending approval is a durable record:

```python
class ApprovalRequest(VersionedModel):
    approval_id: str
    trace_id: str
    run_id: str
    workflow_id: str
    action_id: str
    capability_name: str
    effective_side_effect: SideEffect
    status: ApprovalRequestStatus  # pending, approved, denied, expired
    requested_at: datetime
    resolved_at: datetime | None
    resolver: str | None
    reason: str | None
    action_preview: dict  # Redacted args for review
```

### CLI Approval

```bash
# List pending approvals
agent-kernel list-approvals

# Approve an action
agent-kernel approve <approval_id>

# Approve without auto-resuming the workflow
agent-kernel approve <approval_id> --no-resume
```

### Workflow Resume

After approval, the workflow resumes from its checkpoint:

1. The `ApprovalRequest.status` is updated to `approved`
2. `WorkflowRunner.resume(run_id, approval_tokens)` is called
3. The checkpoint is loaded (skipping already-completed steps)
4. Remaining actions execute with the approval token

## Side Effect Classification

Every capability declares its side effect level:

| Level | Description | Examples |
|-------|-------------|---------|
| `none` | Read-only, no state changes | Search, list, query |
| `local` | Local file or database writes | Create document, update graph |
| `external` | External API calls with side effects | Send email, create calendar event |

The executor uses this classification to determine approval requirements, not the agent's self-reported classification.

## Security Properties

The trust boundary provides these guarantees:

1. **No tool call without validation** -- inputs are checked against JSON Schema
2. **No unauthorized capability** -- actions must be in the agent's allowlist
3. **No untracked side effect** -- every execution produces a `ToolCallRecord`
4. **No approval bypass** -- the executor, not the agent, computes approval requirements
5. **Durable approvals** -- pending approvals survive restarts (SQLite persistence)

## Next Steps

- [Executor](../concepts/executor.md) -- how the executor validates and runs plans
- [Tool Broker](../concepts/tool-broker.md) -- how tools are registered and executed
- [Architecture Guide](architecture.md) -- how trust boundaries fit into the overall system
