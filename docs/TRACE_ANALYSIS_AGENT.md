# Trace Analysis Agent

The **Trace Analysis Agent** enables self-diagnostic capabilities for the agent system. It can query, summarize, and diagnose decision traces to help you understand system behavior, identify failures, and improve performance.

## Overview

The trace analysis agent was designed to answer questions like:
- "Why did yesterday's workflow fail?"
- "What are the most common errors in the last 24 hours?"
- "Which tool is the slowest?"
- "Show me all traces that failed due to timeouts"

## Components

### Capabilities

Three new capabilities power the trace analysis agent:

#### 1. `trace_analysis.query@v1`
Query and filter decision traces by various criteria.

**Parameters:**
- `limit` (int): Maximum traces to return (default: 20, max: 100)
- `offset` (int): Pagination offset (default: 0)
- `agent_profile_id` (string): Filter by agent
- `workflow_id` (string): Filter by workflow
- `status` (enum): Filter by outcome status (completed, failed, partial, needs_approval, cancelled)
- `has_errors` (boolean): Filter traces with/without errors
- `since_hours` (int): Only traces from last N hours (max: 720)
- `trace_ids` (array): Specific trace IDs to retrieve
- `sort_by` (enum): Sort by timestamp, duration, or tool_count

**Example:**
```bash
# Query failed traces from the last 24 hours
agent-kernel run-workflow trace_analysis --intent "Show me all failed traces from the last 24 hours"
```

#### 2. `trace_analysis.summarize@v1`
Summarize patterns and statistics across multiple traces.

**Parameters:**
- `trace_ids` (array): Specific traces to summarize
- `agent_profile_id` (string): Summarize traces for an agent
- `workflow_id` (string): Summarize traces for a workflow
- `since_hours` (int): Summarize traces from last N hours (default: 24, max: 720)
- `focus` (enum): What to focus on - errors, performance, decisions, tool_usage, or all

**Returns:**
- Summary statistics (total traces, time range, agents, workflows)
- Performance metrics (avg/median/max duration, avg tool calls)
- Outcome breakdown (completed, failed, partial, success rate)
- Error analysis (total errors, error rate, common error messages)
- Tool usage statistics (most used capabilities, success rates)
- Reasoning metadata (average tier, escalation rate, critic usage)
- Insights and patterns

**Example:**
```bash
# Summarize performance for the vault_sync workflow
agent-kernel run-workflow trace_analysis --intent "Summarize performance of vault_sync workflow from the last week"
```

#### 3. `trace_analysis.diagnose@v1`
Diagnose issues and provide recommendations.

**Parameters:**
- `trace_id` (string): Specific trace to diagnose
- `symptom` (enum): The symptom to investigate
  - `failure` - Workflow failures
  - `timeout` - Timeout issues
  - `error` - General errors
  - `slow_performance` - Performance problems
  - `approval_denied` - Approval denials
  - `no_results` - Empty results
  - `unexpected_outcome` - Unexpected behavior
- `workflow_id` (string): Diagnose recent failures for a workflow
- `agent_profile_id` (string): Diagnose issues for an agent
- `since_hours` (int): Look back N hours (default: 24, max: 168)
- `include_context` (boolean): Include context details (default: true)

**Returns:**
- Diagnosis (symptom, affected traces, first/last occurrence, frequency)
- Root cause analysis (category, description, evidence)
- Error details (trace IDs, timestamps, error codes/messages, capabilities)
- Performance analysis (normal vs affected duration, slowest step)
- Recommendations (priority, action, rationale)
- Related traces

**Root Cause Categories:**
- `configuration` - Config issues
- `data` - Data problems
- `external_service` - External service failures
- `tool_error` - Tool execution errors
- `planning_error` - Plan validation failures
- `timeout` - Timeout issues
- `approval` - Approval denials
- `unknown` - Unable to determine

**Example:**
```bash
# Diagnose why daily_checkin is failing
agent-kernel run-workflow trace_analysis --intent "Diagnose why daily_checkin workflow is failing"
```

### Agent Profile

**Agent ID:** `trace_analyst`

**Configuration:**
- Engine: Custom (OpenAI GPT-4o)
- Temperature: 0.2 (low for analytical consistency)
- Max tokens: 4096
- Thinking preset: Standard with quality gates

**Allowed Capabilities:**
- `trace_analysis.query@v1`
- `trace_analysis.summarize@v1`
- `trace_analysis.diagnose@v1`
- `notes.create@v1` (requires approval)
- `notes.search@v1`

### Workflow

**Workflow ID:** `trace_analysis`

**Trigger:** Manual (can be scheduled with cron)

**Steps:**
1. Assemble context
2. Propose plan
3. Validate
4. Gate approvals
5. Execute
6. Write back
7. Emit trace

## Usage Examples

### Basic Queries

```bash
# List recent traces
agent-kernel run-workflow trace_analysis --intent "Show me the last 10 traces"

# Query errors
agent-kernel run-workflow trace_analysis --intent "Show all traces with errors from the last 6 hours"

# Filter by workflow
agent-kernel run-workflow trace_analysis --intent "Show me all vault_sync traces from today"
```

### Performance Analysis

```bash
# Summarize overall performance
agent-kernel run-workflow trace_analysis --intent "Summarize system performance from the last 24 hours"

# Focus on slow operations
agent-kernel run-workflow trace_analysis --intent "Summarize performance issues, focusing on slow operations"

# Analyze specific workflow
agent-kernel run-workflow trace_analysis --intent "Analyze performance of daily_checkin workflow"
```

### Diagnostics

```bash
# Diagnose failures
agent-kernel run-workflow trace_analysis --intent "Diagnose why workflows are failing"

# Specific symptom
agent-kernel run-workflow trace_analysis --intent "Diagnose timeout issues in the last 48 hours"

# Investigate specific trace
agent-kernel run-workflow trace_analysis --intent "Diagnose trace ID trace_01HXYZ..."
```

### Creating Analysis Reports

The trace analyst can create notes with its findings (requires approval):

```bash
agent-kernel run-workflow trace_analysis --intent "Analyze system health from the last week and create a summary note"
```

## Scheduled Health Checks

You can schedule regular system health checks by modifying the workflow trigger:

```yaml
# In configs/workflows/trace_analysis.yaml
trigger:
  type: cron
  schedule: "0 */6 * * *"  # Every 6 hours
```

Then the agent will automatically analyze traces and alert you to issues.

## Implementation Details

### Adapter Functions

All three capabilities are implemented in `src/agent_kernel/tools/adapters/trace_adapter.py`:

- `query_traces()` - Queries the SQLite trace store with filtering
- `summarize_traces()` - Aggregates statistics using Python collections
- `diagnose_traces()` - Analyzes error patterns and generates recommendations

### Data Source

The adapter uses `SQLiteTraceSink` to read from the trace database at `data/traces/traces.db`.

### Performance

- Query operations are read-only with no side effects
- No approval required for trace analysis
- Reasonable limits prevent excessive resource usage:
  - Query: max 100 traces per request
  - Summarize: analyzes up to 1000 traces
  - Diagnose: looks back up to 168 hours (1 week)

## Limitations

- Cannot query traces older than what's in the database (subject to retention policies)
- Analysis quality depends on trace completeness
- Pattern detection is statistical, not causal
- Recommendations are heuristic-based

## Future Enhancements

Potential improvements:
- Time series analysis for trend detection
- Anomaly detection using statistical models
- Integration with external monitoring tools
- Automatic issue ticketing for persistent failures
- Machine learning for root cause prediction

## Related Documentation

- [Design Doc: Tracing](design/07-tracing.md)
- [Design Doc: Trace Analysis](design/09-trace-analysis.md)
- [Agent Profiles](CONFIGURATION.md#agent-profiles)
- [Workflows](CONFIGURATION.md#workflows)
