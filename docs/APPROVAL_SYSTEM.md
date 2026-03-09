# Approval System

The agent kernel includes a flexible approval system that allows you to control which actions agents can execute without manual review.

## Quick Start

### Interactive Approval (Default)

By default, workflows prompt you in real-time when approval is needed:

```bash
agent-kernel run-workflow daily_checkin --intent "Create a daily summary"

# When an action needs approval, you'll see:
⚠️  APPROVAL REQUIRED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Action ID    act_01HXYZ...
Capability   notes.create@v1
Side Effect  local
Description  Create note "Daily Summary - 2026-01-23"

Arguments:
  title     "Daily Summary - 2026-01-23"
  content   (250 lines - press 'd' to view)
  tags      ["daily", "summary"]

Approve? [y/n/d/q]: y
Reason (optional):

✓ Approved - continuing...
```

**Options:**
- `y` - Approve and continue
- `n` - Deny and skip action
- `d` - Show full details (then ask again)
- `q` - Quit workflow

### Auto-Approval by Capability

Skip prompts for specific capabilities you trust:

```bash
# Auto-approve note creation
agent-kernel run-workflow daily_checkin \
  --auto-approve notes.create@v1

# Auto-approve multiple capabilities
agent-kernel run-workflow daily_checkin \
  --auto-approve notes.create@v1 \
  --auto-approve tasks.create@v1
```

### Auto-Approval by Risk Level

Auto-approve all actions up to a risk threshold:

```bash
# Auto-approve only side-effect-free actions
agent-kernel run-workflow vault_sync --auto-approve-risk none

# Auto-approve local actions (reads + writes to vault)
agent-kernel run-workflow daily_checkin --auto-approve-risk low

# Auto-approve external API calls too
agent-kernel run-workflow calendar_sync --auto-approve-risk medium

# Auto-approve everything (dangerous!)
agent-kernel run-workflow daily_checkin --auto-approve-risk high
```

**Risk Levels:**
- `none` - No side effects (read-only operations)
- `low` - Local side effects (writes to vault, local DB)
- `medium` - External side effects (API calls, emails)
- `high` - Destructive operations (deletes, force-push)

### Dry-Run Mode

Preview what would need approval without executing:

```bash
agent-kernel run-workflow daily_checkin --dry-run

# Output:
Dry Run - Approval Summary
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total actions: 5
Would auto-approve: 3
Would need approval: 2

Auto-approved actions:
  ✓ tasks.list@v1
  ✓ notes.search@v1
  ✓ calendar.sync@v1

Actions requiring approval:
  ⚠️  notes.create@v1
  ⚠️  tasks.create@v1
```

## CLI Commands

### List Pending Approvals

```bash
# List all pending approvals
agent-kernel list-approvals

# Filter by workflow
agent-kernel list-approvals --workflow daily_checkin

# Filter by specific run
agent-kernel list-approvals --run run_01HXYZ...

# Limit results
agent-kernel list-approvals --limit 10
```

**Output:**
```
Pending Approval Requests
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ ID             ┃ Workflow     ┃ Action        ┃ Side Eff… ┃ Requested ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━┩
│ appr_01HXYZ... │ daily_check… │ notes.create… │ local     │ 5m ago    │
│ appr_01HABC... │ vault_sync   │ obsidian.wri… │ local     │ 2h ago    │
└────────────────┴──────────────┴───────────────┴───────────┴───────────┘
```

### Show Approval Details

```bash
agent-kernel show-approval appr_01HXYZ...
```

**Output:**
```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Approval Request                       ┃
┃ Approval appr_01HXYZ...                ┃
┃ Status: pending                        ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

Workflow      daily_checkin
Run ID        run_01HXYZ...
Action        notes.create@v1
Side Effect   local
Requested At  2026-01-23T10:15:30Z
Expires At    2026-01-24T10:15:30Z
Policy Basis  Agent profile: requires_approval_for

Action Preview:
{
  "title": "Daily Summary - 2026-01-23",
  "content": "...",
  "tags": ["daily", "summary"]
}
```

### Approve Action

```bash
# Approve with reason
agent-kernel approve appr_01HXYZ... --reason "Looks good"

# Approve without reason
agent-kernel approve appr_01HXYZ...
```

### Deny Action

```bash
# Deny (reason required)
agent-kernel deny appr_01HXYZ... --reason "Not needed today"
```

**Note:** Workflow pause/resume is not yet implemented. Approvals granted via CLI are recorded but workflows won't automatically resume.

## Configuration

### Agent Profiles

Control approval requirements in agent profile YAML:

```yaml
# configs/agents/my_agent.yaml
agent_profile_id: my_agent
name: My Agent

# Capabilities this agent can use
allowed_capabilities:
  - notes.create@v1
  - tasks.create@v1
  - calendar.sync@v1

# Approval policy
approval_policy:
  # Always require approval for these
  require_approval_for:
    - notes.create@v1
    - email.send@v1

  # Auto-approve these side effect levels
  auto_approve_side_effects:
    - none
    - local

  # Don't auto-approve if risk is higher than this
  max_auto_approve_risk: low
```

### Capability Definitions

Set default approval requirements in capability YAML:

```yaml
# configs/capabilities/notes.create@v1.yaml
capability_id: notes.create@v1
capability_name: Create Note
description: Create a new note in the vault

# Side effect classification
side_effect_level: local

# Default approval requirement
requires_approval_default: false  # Don't require by default

# Or require by default:
requires_approval_default: true
```

## How Approval Decision Works

When an action needs to execute, the system checks in this order:

1. **Pre-approved token?** (from previous approval or --approval-token flag)
   - ✅ Execute immediately

2. **Auto-approve by capability?** (--auto-approve flag)
   - ✅ Execute immediately

3. **Auto-approve by risk?** (--auto-approve-risk flag)
   - ✅ Execute if risk <= threshold

4. **Interactive mode enabled?** (--interactive flag, default)
   - 🔔 Prompt user in terminal
   - ✅ Execute if approved
   - ❌ Skip if denied

5. **Batch mode** (--batch flag)
   - 💾 Save to ApprovalStore
   - ⏸️ Skip action (workflow continues)
   - 📧 User can approve later via CLI

6. **Default**
   - ⏸️ Skip action
   - 💾 Record in ApprovalStore

## Best Practices

### For Development

```bash
# Interactive prompts give you control
agent-kernel run-workflow my_workflow --interactive
```

### For Testing

```bash
# Dry-run shows what would happen
agent-kernel run-workflow my_workflow --dry-run

# Auto-approve low-risk actions
agent-kernel run-workflow my_workflow --auto-approve-risk low
```

### For Automation

```bash
# Auto-approve specific safe capabilities
agent-kernel run-workflow nightly_sync \
  --auto-approve vault.read@v1 \
  --auto-approve notes.search@v1 \
  --batch
```

### For Production

```bash
# Batch mode for scheduled workflows
agent-kernel run-workflow daily_checkin --batch

# Review and approve later
agent-kernel list-approvals
agent-kernel show-approval appr_01XYZ...
agent-kernel approve appr_01XYZ... --reason "Verified"
```

## Security Considerations

### Risk Levels

Always understand what you're auto-approving:

- `none` - **Safe**: Read-only, no changes
- `low` - **Usually safe**: Local writes (notes, tasks)
- `medium` - **Caution**: External API calls, emails
- `high` - **Dangerous**: Deletes, destructive operations

### Audit Trail

All approvals are logged:

- Who approved (user, auto, token)
- When approved
- Why (reason if provided)
- What was approved (full action details)
- Stored in `data/approvals/approvals.db`

### Capability Allowlists

Agent profiles enforce capability allowlists:

```yaml
allowed_capabilities:
  - notes.create@v1
  # Agent can NEVER use capabilities not in this list
```

Even if auto-approved, agents can't use disallowed capabilities.

## Troubleshooting

### "Approval not yet implemented for ID"

You're seeing the old stub. Update to latest version:

```bash
git pull origin main
```

### Workflow Doesn't Resume After Approval

Workflow resume (Option 2) is not implemented. Current system only supports:
- Interactive approval (prompts during execution)
- Auto-approval (skip prompts for trusted actions)

For deferred approval with resume, see `APPROVAL_DESIGN_OPTIONS.md`.

### Approval Stored But Action Not Executed

This is expected behavior in batch mode. Approvals are recorded but:
- Workflow already completed
- Action was skipped at execution time
- Resume functionality not yet implemented

Use `--interactive` mode to approve during execution.

## Examples

### Daily Review Workflow

```bash
# Interactive mode - approve notes as needed
agent-kernel run-workflow daily_checkin --interactive
```

### Vault Sync (Automated)

```bash
# Auto-approve all local writes
agent-kernel run-workflow vault_sync --auto-approve-risk low
```

### Email Campaign (High Risk)

```bash
# Dry-run first
agent-kernel run-workflow email_campaign --dry-run

# Then run interactively
agent-kernel run-workflow email_campaign --interactive
```

### Scheduled Nightly Sync

```bash
# Batch mode for cron
agent-kernel run-workflow nightly_sync \
  --auto-approve-risk low \
  --batch
```

## Related Documentation

- [APPROVAL_DESIGN_OPTIONS.md](../APPROVAL_DESIGN_OPTIONS.md) - Design decisions and future options
- [Agent Profiles](../CONFIGURATION.md#agent-profiles) - Configuring approval policies
- [Capabilities](../CONFIGURATION.md#capabilities) - Side effects and risk levels
