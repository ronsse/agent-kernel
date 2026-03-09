# Trace Analysis Agent & MCP Tools

**Version:** 1.0.1  
**Status:** Design Phase

A self-analyzing agent that can query, summarize, and diagnose issues from the kernel's trace logs.

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                  TRACE ANALYSIS SYSTEM                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  User: "Why did yesterday's workflow fail?"                      │
│                          │                                       │
│                          ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              TRACE ANALYST AGENT                            ││
│  │                                                             ││
│  │  Uses capabilities:                                         ││
│  │  • trace_analysis.query@v1                                  ││
│  │  • trace_analysis.summarize@v1                              ││
│  │  • trace_analysis.diagnose@v1                               ││
│  │  • trace_analysis.metrics@v1                                ││
│  │                                                             ││
│  └─────────────────────────────────────────────────────────────┘│
│                          │                                       │
│                          ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                   TRACE STORE                               ││
│  │                                                             ││
│  │  • DecisionTraces (plans, outcomes)                         ││
│  │  • ToolCallRecords (timing, errors)                         ││
│  │  • Events (immutable log)                                   ││
│  │                                                             ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Trace Analysis Capabilities

### 1. trace_analysis.query@v1

Query traces with filters and search.

```yaml
capability_name: trace_analysis.query@v1
description: Query decision traces with flexible filters and full-text search
input_schema:
  type: object
  properties:
    query:
      type: string
      description: Full-text search query (searches intent, summary)
    time_range:
      type: object
      properties:
        since:
          type: string
          format: date-time
        until:
          type: string
          format: date-time
    filters:
      type: object
      properties:
        agent_profile_id:
          type: string
        engine_id:
          type: string
        outcome_status:
          type: string
          enum: [completed, partial, failed, needs_approval]
        workflow_id:
          type: string
        has_errors:
          type: boolean
    limit:
      type: integer
      default: 20
      maximum: 100
    include_tool_calls:
      type: boolean
      default: true
output_schema:
  type: object
  properties:
    traces:
      type: array
      items:
        $ref: "#/definitions/TraceSummary"
    total_count:
      type: integer
    query_time_ms:
      type: integer
side_effect_level: none
requires_approval_default: false
timeout_ms: 15000
adapter_type: local
```

### 2. trace_analysis.summarize@v1

Generate summaries of trace patterns.

```yaml
capability_name: trace_analysis.summarize@v1
description: Summarize patterns and insights from a set of traces
input_schema:
  type: object
  required:
    - focus
  properties:
    trace_ids:
      type: array
      items:
        type: string
      description: Specific traces to summarize (if empty, uses recent)
    time_range:
      type: object
      properties:
        since:
          type: string
          format: date-time
        until:
          type: string
          format: date-time
    focus:
      type: string
      enum:
        - errors        # Focus on failures and error patterns
        - performance   # Focus on timing and efficiency
        - decisions     # Focus on plans and action patterns
        - approvals     # Focus on approval/denial patterns
        - capabilities  # Focus on which tools are used
      description: What aspect to focus the summary on
    group_by:
      type: string
      enum: [agent, workflow, capability, hour, day]
      default: day
output_schema:
  type: object
  properties:
    summary:
      type: string
      description: Natural language summary
    statistics:
      type: object
      properties:
        total_traces:
          type: integer
        success_rate:
          type: number
        avg_duration_ms:
          type: number
        most_used_capabilities:
          type: array
          items:
            type: object
        error_breakdown:
          type: object
    patterns:
      type: array
      items:
        type: object
        properties:
          pattern:
            type: string
          frequency:
            type: integer
          significance:
            type: string
    recommendations:
      type: array
      items:
        type: string
side_effect_level: none
requires_approval_default: false
timeout_ms: 30000
adapter_type: local
```

### 3. trace_analysis.diagnose@v1

Diagnose specific issues by analyzing related traces.

```yaml
capability_name: trace_analysis.diagnose@v1
description: Diagnose issues by analyzing trace patterns and error chains
input_schema:
  type: object
  required:
    - symptom
  properties:
    symptom:
      type: string
      description: Description of the problem to diagnose
    trace_id:
      type: string
      description: Specific trace to start diagnosis from (optional)
    context:
      type: object
      properties:
        workflow_id:
          type: string
        agent_profile_id:
          type: string
        time_range:
          type: object
    depth:
      type: string
      enum: [quick, thorough, deep]
      default: thorough
      description: How deep to analyze
output_schema:
  type: object
  properties:
    diagnosis:
      type: object
      properties:
        root_cause:
          type: string
        confidence:
          type: string
          enum: [low, medium, high]
        evidence:
          type: array
          items:
            type: object
            properties:
              trace_id:
                type: string
              finding:
                type: string
    related_errors:
      type: array
      items:
        type: object
        properties:
          error_code:
            type: string
          message:
            type: string
          frequency:
            type: integer
    suggested_fixes:
      type: array
      items:
        type: object
        properties:
          fix:
            type: string
          effort:
            type: string
            enum: [low, medium, high]
          impact:
            type: string
    timeline:
      type: array
      items:
        type: object
        properties:
          timestamp:
            type: string
          event:
            type: string
          significance:
            type: string
side_effect_level: none
requires_approval_default: false
timeout_ms: 45000
adapter_type: local
```

### 4. trace_analysis.metrics@v1

Get aggregate metrics and statistics.

```yaml
capability_name: trace_analysis.metrics@v1
description: Get aggregate metrics and performance statistics
input_schema:
  type: object
  properties:
    time_range:
      type: object
      properties:
        since:
          type: string
          format: date-time
        until:
          type: string
          format: date-time
    granularity:
      type: string
      enum: [hour, day, week]
      default: day
    metrics:
      type: array
      items:
        type: string
        enum:
          - trace_count
          - success_rate
          - avg_duration
          - p95_duration
          - error_rate
          - approval_rate
          - tool_call_count
          - tokens_used
      default: [trace_count, success_rate, avg_duration, error_rate]
    group_by:
      type: string
      enum: [agent, workflow, capability, none]
      default: none
output_schema:
  type: object
  properties:
    time_series:
      type: array
      items:
        type: object
        properties:
          timestamp:
            type: string
          metrics:
            type: object
    aggregates:
      type: object
      description: Overall aggregates for the period
    comparisons:
      type: object
      properties:
        vs_previous_period:
          type: object
          description: Comparison with previous equivalent period
side_effect_level: none
requires_approval_default: false
timeout_ms: 20000
adapter_type: local
```

---

## Trace Analyst Agent

### Agent Profile

```yaml
# configs/agents/trace_analyst.yaml
agent_profile_id: trace_analyst
name: Trace Analysis Agent
description: |
  Analyzes system traces to understand behavior, diagnose issues,
  and provide insights about agent execution patterns.

engine: custom

model_config:
  provider: openai
  model: gpt-4o
  temperature: 0.2  # Lower for analytical tasks
  max_tokens: 4096

allowed_capabilities:
  # Trace analysis tools
  - trace_analysis.query@v1
  - trace_analysis.summarize@v1
  - trace_analysis.diagnose@v1
  - trace_analysis.metrics@v1
  # Can create notes with findings
  - notes.create@v1

context_policy:
  max_tokens: 6000
  max_notes: 5
  max_tasks: 0
  max_events: 0
  must_cite: true
  allowed_scopes: []

approval_policy:
  require_approval_for: []
  auto_approve_side_effects:
    - none
    - local
  max_auto_approve_risk: low

output_schema_version: "1.0.0"
```

### Example Interactions

**User:** "Why did the daily check-in fail yesterday?"

**Agent Plan:**
```json
{
  "summary": "Investigating yesterday's daily_checkin workflow failure by querying failed traces and diagnosing the root cause.",
  "actions": [
    {
      "capability_name": "trace_analysis.query@v1",
      "args": {
        "filters": {
          "workflow_id": "daily_checkin",
          "outcome_status": "failed"
        },
        "time_range": {
          "since": "2024-01-14T00:00:00Z",
          "until": "2024-01-15T00:00:00Z"
        }
      }
    },
    {
      "capability_name": "trace_analysis.diagnose@v1",
      "args": {
        "symptom": "daily_checkin workflow failed",
        "context": {
          "workflow_id": "daily_checkin"
        },
        "depth": "thorough"
      }
    }
  ]
}
```

**User:** "What's our error rate this week compared to last week?"

**Agent Plan:**
```json
{
  "summary": "Comparing error rates between this week and last week using metrics analysis.",
  "actions": [
    {
      "capability_name": "trace_analysis.metrics@v1",
      "args": {
        "time_range": {
          "since": "2024-01-07T00:00:00Z",
          "until": "2024-01-14T00:00:00Z"
        },
        "metrics": ["error_rate", "success_rate", "trace_count"],
        "granularity": "day"
      }
    }
  ]
}
```

**User:** "Which tools are causing the most timeouts?"

**Agent Plan:**
```json
{
  "summary": "Analyzing timeout patterns across tool calls to identify problematic capabilities.",
  "actions": [
    {
      "capability_name": "trace_analysis.summarize@v1",
      "args": {
        "focus": "errors",
        "group_by": "capability",
        "time_range": {
          "since": "2024-01-01T00:00:00Z"
        }
      }
    }
  ]
}
```

---

## Implementation

### Trace Analysis Service

```python
class TraceAnalysisService:
    """Service layer for trace analysis capabilities."""
    
    def __init__(
        self,
        trace_store: TraceStore,
        event_log: EventLog,
    ):
        self.traces = trace_store
        self.events = event_log
    
    async def query(
        self,
        query: str | None = None,
        time_range: dict | None = None,
        filters: dict | None = None,
        limit: int = 20,
        include_tool_calls: bool = True,
    ) -> dict:
        """Query traces with filters."""
        
        # Parse time range
        since = None
        until = None
        if time_range:
            since = datetime.fromisoformat(time_range.get("since", ""))
            until = datetime.fromisoformat(time_range.get("until", ""))
        
        # Build filter conditions
        outcome_status = None
        if filters and "outcome_status" in filters:
            outcome_status = OutcomeStatus(filters["outcome_status"])
        
        # Query traces
        traces = await self.traces.query(
            since=since,
            until=until,
            agent_profile_id=filters.get("agent_profile_id") if filters else None,
            engine_id=filters.get("engine_id") if filters else None,
            outcome_status=outcome_status,
            intent_search=query,
            limit=limit,
        )
        
        # Build response
        return {
            "traces": [self._to_summary(t, include_tool_calls) for t in traces],
            "total_count": len(traces),
            "query_time_ms": 0,  # TODO: measure
        }
    
    async def summarize(
        self,
        focus: str,
        trace_ids: list[str] | None = None,
        time_range: dict | None = None,
        group_by: str = "day",
    ) -> dict:
        """Summarize trace patterns."""
        
        # Get traces
        if trace_ids:
            traces = [await self.traces.get(tid) for tid in trace_ids]
            traces = [t for t in traces if t is not None]
        else:
            traces = await self.traces.query(limit=100)
        
        # Analyze based on focus
        if focus == "errors":
            return self._summarize_errors(traces)
        elif focus == "performance":
            return self._summarize_performance(traces)
        elif focus == "decisions":
            return self._summarize_decisions(traces)
        elif focus == "approvals":
            return self._summarize_approvals(traces)
        elif focus == "capabilities":
            return self._summarize_capabilities(traces)
        
        return {"summary": "Unknown focus area", "statistics": {}}
    
    async def diagnose(
        self,
        symptom: str,
        trace_id: str | None = None,
        context: dict | None = None,
        depth: str = "thorough",
    ) -> dict:
        """Diagnose an issue."""
        
        # Gather evidence
        related_traces = []
        
        if trace_id:
            trace = await self.traces.get(trace_id)
            if trace:
                related_traces.append(trace)
        
        # Search for similar issues
        search_results = await self.traces.search(symptom, limit=10)
        related_traces.extend(search_results)
        
        # Analyze patterns
        errors = self._extract_error_patterns(related_traces)
        timeline = self._build_timeline(related_traces)
        
        # Generate diagnosis
        return {
            "diagnosis": {
                "root_cause": self._infer_root_cause(errors, symptom),
                "confidence": "medium",
                "evidence": [
                    {"trace_id": t.trace_id, "finding": self._describe_finding(t)}
                    for t in related_traces[:5]
                ],
            },
            "related_errors": errors,
            "suggested_fixes": self._suggest_fixes(errors),
            "timeline": timeline,
        }
    
    async def metrics(
        self,
        time_range: dict | None = None,
        granularity: str = "day",
        metrics: list[str] | None = None,
        group_by: str = "none",
    ) -> dict:
        """Get aggregate metrics."""
        
        stats = await self.traces.get_statistics(
            since=datetime.fromisoformat(time_range["since"]) if time_range else None,
            until=datetime.fromisoformat(time_range["until"]) if time_range else None,
        )
        
        return {
            "time_series": [],  # TODO: Build time series
            "aggregates": {
                "total_traces": stats.total_traces,
                "success_rate": stats.success_rate,
                "avg_duration_ms": stats.avg_duration_ms,
                "total_tool_calls": stats.total_tool_calls,
            },
            "comparisons": {},
        }
```

### Registering Capabilities

```python
# In tool adapter registration

def register_trace_analysis_tools(
    broker: ToolBroker,
    trace_service: TraceAnalysisService,
):
    """Register trace analysis capabilities."""
    
    adapter = broker.adapters["local"]
    
    adapter.register(
        "trace_analysis.query@v1",
        trace_service.query,
    )
    adapter.register(
        "trace_analysis.summarize@v1",
        trace_service.summarize,
    )
    adapter.register(
        "trace_analysis.diagnose@v1",
        trace_service.diagnose,
    )
    adapter.register(
        "trace_analysis.metrics@v1",
        trace_service.metrics,
    )
```

---

## CLI Integration

```bash
# Quick trace queries from CLI (uses trace analyst internally)
agent-kernel analyze "why did daily_checkin fail yesterday"

# Or run the full agent workflow
agent-kernel run-workflow trace_analysis --intent "summarize this week's errors"

# Direct metrics
agent-kernel trace-metrics --since 7d --granularity day
```

---

## Future: MCP Server

Once MCP is allowed, expose trace analysis as an MCP server:

```json
{
  "mcpServers": {
    "agent-kernel-traces": {
      "command": "agent-kernel",
      "args": ["mcp-server", "traces"],
      "env": {
        "DATABASE_URL": "sqlite:///./data/agent_kernel.db"
      }
    }
  }
}
```

This allows Claude Desktop or other MCP clients to query traces directly.

---

## Related Documents

- [07-tracing.md](07-tracing.md) - Trace storage
- [03-tools.md](03-tools.md) - Capability definitions
- [05-engines.md](05-engines.md) - Agent engines
