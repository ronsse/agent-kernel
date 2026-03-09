# Thinking Policy & Reasoning Depth

> **Purpose:** Design guidance for incorporating "deep thinking" capabilities into agents, including model selection, reasoning budget control, and escalation strategies.

---

## The Three Components of "Advanced Thinking"

What feels like "GPT-5 Pro thinking" comes from three sources:

| Component | What It Is | Who Controls It |
|-----------|-----------|-----------------|
| **Model Capability** | Raw intelligence ceiling of the model | Model provider (you can't recreate with architecture) |
| **Deliberation Budget** | How long the model is allowed to think | You control via `reasoning.effort` API parameter |
| **Workflow Scaffolding** | Good context, decomposition, critique loops, validation | You build this in your kernel |

**Key insight:** You can control 2 out of 3. Architecture helps you use capability efficiently.

---

## Approach Options

### Option 1: Always Call Best Model (Simple, Expensive)

```yaml
strategy: always_best
model: gpt-5-turbo
reasoning_effort: high
```

**Pros:** Simplest, most consistent quality  
**Cons:** Higher cost + latency; wastes capability on easy tasks  
**When:** Low volume, reliability matters most

### Option 2: Same Model, Variable Reasoning Budget ⭐ RECOMMENDED DEFAULT

Use OpenAI's `reasoning.effort` parameter:

| Effort | Use Case |
|--------|----------|
| `none` / `low` | Lightweight tasks, routing, classification |
| `medium` | Normal planning, standard workflows |
| `high` | Deep analysis, ambiguity resolution |

```python
# Same model, different "thinking depth"
response = client.responses.create(
    model="gpt-5",
    reasoning={"effort": "high"},  # or "medium", "low"
    ...
)
```

**Note:** GPT-5 also has separate `verbosity` control for output length (doesn't affect thinking depth).

### Option 3: Adaptive Escalation ⭐ BEST FOR PRODUCTION

**Pattern: Attempt → Gate → Escalate**

```
┌─────────────────┐
│  Try Cheap/Fast │ (lower effort or smaller model)
└────────┬────────┘
         ▼
┌─────────────────┐
│  Quality Gates  │ (schema valid? citations? constraints?)
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
  PASS      FAIL
    │         │
    ▼         ▼
 Execute   Escalate (higher effort / better model)
```

**Why this beats pre-classification:** Evidence-driven. You escalate based on actual failure signals, not predictions.

### Option 4: Solver + Critic (Two-Pass)

```
Pass A (Solver) ──→ Propose solution/plan
         │
         ▼
Pass B (Critic) ──→ Challenge, find gaps, check constraints
         │
         ▼
Pass C (Revise) ──→ Improve plan using critique (optional)
```

**Configurations:**
- Strong solver + cheap critic
- Cheap solver + strong critic

**When:** High reliability required, don't trust single-pass reasoning.

### Option 5: Multi-Candidate + Judge

Generate 3-5 candidate plans, have a judge select/merge.

**When:** High stakes + high ambiguity only. Cost multiplier is significant.

### Option 6: Fine-tune for Repeated Patterns

Distill common tasks (classification, routing, summarization) to smaller models.

**When:** High volume of repetitive internal tasks.

---

## Key Kernel Component: ThinkingPolicyController

### Purpose

Decide how much cognition to buy for each task.

### Interface

```python
@dataclass
class ThinkingPolicy:
    """Output of ThinkingPolicyController."""
    model_id: str
    reasoning_effort: Literal["none", "low", "medium", "high"]
    max_tokens: int
    run_critic_pass: bool
    generate_candidates: int  # 1 = single pass, 3-5 = multi-candidate
    escalation_reason: str | None = None


class ThinkingPolicyController:
    """Decides reasoning budget for each task."""
    
    def determine_policy(
        self,
        intent: str,
        agent_profile: AgentProfile,
        context_size: int,
        risk_level: Literal["low", "medium", "high"],
        prior_attempts: list[PriorAttempt] | None = None,
    ) -> ThinkingPolicy:
        """
        Inputs:
        - intent: Task description ("deep analysis", "quick answer", etc.)
        - agent_profile: Allowed models, max cost, latency budget
        - context_size: Token count of context (big context often needs more reasoning)
        - risk_level: External writes, calendar changes, etc.
        - prior_attempts: Results of previous attempts (for escalation)
        
        Returns:
        - ThinkingPolicy with model selection and reasoning parameters
        """
```

### Tier Configuration

```yaml
# configs/thinking_tiers.yaml
tiers:
  0:
    name: "routing"
    description: "Classification, routing, simple extraction"
    model: "gpt-4o-mini"
    reasoning_effort: "low"
    max_tokens: 500
    
  1:
    name: "standard"
    description: "Normal planning, most tasks"
    model: "gpt-4o"
    reasoning_effort: "medium"
    max_tokens: 2000
    
  2:
    name: "deep"
    description: "Complex analysis, ambiguous tasks"
    model: "gpt-5"
    reasoning_effort: "high"
    max_tokens: 4000
    
  3:
    name: "deep_with_critic"
    description: "High stakes, requires verification"
    model: "gpt-5"
    reasoning_effort: "high"
    max_tokens: 4000
    run_critic: true

escalation_triggers:
  - schema_validation_failed
  - quality_gates_failed
  - confidence_below_threshold: 0.7
  - risk_level: high
  - explicit_deep_analysis_request
```

---

## Quality Gates (Deterministic Validation)

Run after every plan generation, before execution:

```python
class QualityGateRunner:
    """Deterministic validators for plan quality."""
    
    def validate(
        self,
        plan: Plan,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
    ) -> GateResult:
        """
        Gates:
        1. Plan validates against JSON schema (Structured Outputs helps)
        2. All actions cite required context_refs_used
        3. No external side effects without approval token
        4. Constraints satisfied (deadlines, project scope)
        5. If plan cites "note X", ensure note X is in ContextPacket
        6. Confidence above threshold
        """
```

### Gate Result

```python
@dataclass
class GateResult:
    passed: bool
    failures: list[str]
    warnings: list[str]
    confidence: float
    should_escalate: bool
    escalation_reason: str | None
```

---

## Escalation Manager

```python
class EscalationManager:
    """Manages attempt → gate → escalate flow."""
    
    async def execute_with_escalation(
        self,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
        max_escalations: int = 2,
    ) -> Plan:
        """
        1. Get initial thinking policy
        2. Generate plan with current policy
        3. Run quality gates
        4. If failed and escalations remain:
           - Bump to next tier
           - Retry with higher reasoning budget
        5. Return best plan (or raise if all attempts failed)
        """
```

---

## Critic Engine (Optional)

For high-reliability tasks, add a critic pass:

```python
class CriticEngine:
    """Challenges plans and finds gaps."""
    
    async def critique(
        self,
        plan: Plan,
        context_packet: ContextPacket,
    ) -> Critique:
        """
        Returns structured critique:
        - issues: list of problems found
        - missing_context: what information is needed
        - risk_flags: potential risks
        - recommended_changes: suggested improvements
        """


@dataclass
class Critique:
    issues: list[str]
    missing_context: list[str]
    risk_flags: list[str]
    recommended_changes: list[str]
    confidence_adjustment: float  # -0.3 to +0.1
```

---

## Plan Schema Extensions

Add quality signals to Plan (not chain-of-thought, just useful metadata):

```python
class PlanQuality(BaseModel):
    """Quality signals for escalation decisions."""
    confidence: float = Field(ge=0.0, le=1.0, description="Model's confidence")
    uncertainties: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    verification_steps: list[str] = Field(default_factory=list)
```

---

## Trace Schema Extensions

Capture reasoning decisions for analysis:

```python
class ReasoningMetadata(BaseModel):
    """Captured in DecisionTrace for analysis."""
    tier_used: int
    model_id: str
    reasoning_effort: str
    escalation_count: int
    escalation_reasons: list[str]
    gate_failures: list[str]
    critic_used: bool
    total_reasoning_tokens: int
```

---

## Implementation Priority

### Phase 1: Minimum Viable Deep Thinking

1. **Use Structured Outputs** for Plan schema (reliable orchestration)
2. **Implement Attempt + Gate + Escalate** using `reasoning.effort` as primary lever
3. **Add confidence field** to Plan for escalation decisions

### Phase 2: Enhanced Reliability

4. **Add Critic pass** for tasks marked "deep analysis" or high-risk
5. **Implement ThinkingPolicyController** with tier configuration
6. **Add ReasoningMetadata** to traces for analysis

### Phase 3: Optimization

7. **Build evaluation harness** to tune thresholds
8. **Consider multi-candidate** for highest-stakes decisions
9. **Fine-tune routing models** if volume justifies

---

## Decision: When to Just Call a Better Model

**For genuinely deep analysis: yes, often use the best model.**

Architecture helps you:
- Use expensive models efficiently (not for easy tasks)
- Validate outputs before execution
- Escalate automatically when needed
- Keep costs predictable

But the ceiling is still the model's capability.

---

## Summary

| Strategy | When to Use |
|----------|-------------|
| Variable `reasoning.effort` | Default for most tasks |
| Attempt + Gate + Escalate | Production systems with cost constraints |
| Solver + Critic | High-reliability requirements |
| Multi-candidate + Judge | Rare, highest-stakes decisions |
| Always best model | Low volume, reliability > cost |

**The key insight:** Don't pre-classify complexity. Try fast, validate, escalate on evidence.

---

# v1.0.3 Implementation

> This section documents the actual implementation of the thinking policy system.

## Components Built

### 1. ThinkingConfig Schema (`core/schemas/thinking.py`)

Master configuration attached to `AgentProfile`:

```python
class ThinkingConfig(KernelModel):
    mode: Literal["standard", "deep", "adaptive"]
    tiers: dict[int, ThinkingTierConfig]  # 0-3
    retrieval: RetrievalConfig
    verification: VerificationConfig
    escalation: EscalationConfig
    gates: QualityGatesConfig
```

#### Sub-Configurations

| Config | Purpose | Key Fields |
|--------|---------|------------|
| `ThinkingTierConfig` | Per-tier model/reasoning settings | `model`, `reasoning_effort`, `use_critic`, `max_tokens` |
| `RetrievalConfig` | Context retrieval strategies | `semantic_search`, `graph_expansion`, `recency_boost` |
| `VerificationConfig` | Critic/validation options | `use_critic`, `critic_model`, `max_revisions` |
| `EscalationConfig` | Auto-escalation rules | `triggers`, `confidence_threshold`, `require_approval_to_escalate` |
| `QualityGatesConfig` | Gate toggles | `coverage_gate`, `parity_gate`, `recency_gate` |

### 2. ThinkingPolicyController (`engine/thinking_policy.py`)

Controls thinking decisions and escalation:

```python
class ThinkingPolicyController:
    def create_session(agent_profile) -> ThinkingSession
    def get_policy(session) -> ThinkingPolicy
    def evaluate_for_escalation(session, plan, quality_report, critique) -> (bool, trigger, reason)
    async def escalate(session, trigger, reason) -> bool
    def get_retrieval_config(session) -> dict
```

#### ThinkingSession

Tracks state across escalation attempts:

```python
@dataclass
class ThinkingSession:
    config: ThinkingConfig
    current_tier: ThinkingTier  # 0-3
    attempts: list[EscalationAttempt]
    gate_failures: list[str]
    critic_issues: list[str]
    escalation_count: int
```

#### ThinkingPolicy

Output for a single attempt:

```python
@dataclass
class ThinkingPolicy:
    model_id: str
    reasoning_effort: ReasoningEffort
    max_tokens: int
    tier: ThinkingTier
    tier_name: str
    run_critic: bool
    max_context_tokens: int
    requires_approval_to_escalate: bool
```

### 3. Predefined Presets

Three built-in configurations:

```python
STANDARD_THINKING = ThinkingConfig(
    mode="standard",
    escalation=EscalationConfig(enabled=False),
)

DEEP_THINKING = ThinkingConfig(
    mode="deep",
    escalation=EscalationConfig(start_tier=2, max_tier=3),
    verification=VerificationConfig(use_critic=True),
    retrieval=RetrievalConfig(graph_expansion=True),
)

ADAPTIVE_THINKING = ThinkingConfig(
    mode="adaptive",
    escalation=EscalationConfig(
        enabled=True,
        triggers=["schema_validation_failed", "quality_gates_failed", "low_confidence"],
    ),
)
```

### 4. AgentProfile Integration

`AgentProfile` now has optional `thinking_config`:

```yaml
# configs/agents/deep_analyst.yaml
agent_profile_id: deep_analyst
name: Deep Analyst
engine: custom
thinking_config:
  mode: adaptive
  escalation:
    enabled: true
    start_tier: 1
    max_tier: 3
    triggers:
      - quality_gates_failed
      - low_confidence
    require_approval_to_escalate: false
  retrieval:
    semantic_search: true
    graph_expansion: true
  verification:
    use_critic: true
```

### 5. Context Assembler Integration

New method `assemble_with_thinking()`:

```python
async def assemble_with_thinking(
    intent: str,
    agent_profile: AgentProfile,
    retrieval_config: dict | RetrievalConfig,
    max_context_tokens: int,
) -> ContextPacket
```

Features enabled based on tier:
- **Tier 0-1**: Keyword + semantic search
- **Tier 2+**: Graph expansion enabled
- **Tier 3**: Iterative retrieval available

### 6. WorkflowRunner Integration

New method `run_with_thinking()`:

```python
async def run_with_thinking(
    workflow_id: str,
    intent: str | None = None,
) -> WorkflowResult
```

Implements the full escalation loop:
1. Create ThinkingSession
2. Assemble context with tier-appropriate settings
3. Generate plan
4. Run validation + critic
5. If failed and can escalate: bump tier and retry
6. Execute final plan with reasoning metadata

### 7. CLI Commands

| Command | Description |
|---------|-------------|
| `show-thinking-config <agent_id>` | Display thinking config for an agent |
| `list-thinking-presets` | Show available presets |
| `run-workflow-thinking <workflow_id>` | Run workflow with adaptive escalation |

---

## Escalation Flow

```
┌─────────────────────────────────────┐
│  ThinkingPolicyController           │
│  create_session(agent_profile)      │
└─────────────────┬───────────────────┘
                  ▼
         ┌────────────────┐
         │ Tier 1 Attempt │
         └───────┬────────┘
                 ▼
    ┌─────────────────────────┐
    │ Assemble Context        │  ← Uses tier's retrieval config
    │ Generate Plan           │  ← Uses tier's model/effort
    │ Run Validation + Critic │
    └───────────┬─────────────┘
                │
         ┌──────┴──────┐
         │             │
     Pass ✓        Fail ✗
         │             │
         ▼             ▼
    Execute      evaluate_for_escalation()
         │             │
         │        ┌────┴────┐
         │        │         │
         │    Can Escalate  Cannot
         │        │         │
         │        ▼         ▼
         │   Tier 2 Attempt Return Best Plan
         │        │
         │        ▼
         └──► (repeat)
```

## Escalation Triggers

| Trigger | When Fired |
|---------|------------|
| `schema_validation_failed` | Plan doesn't match schema |
| `quality_gates_failed` | Coverage/recency/parity gates fail |
| `low_confidence` | Plan confidence below threshold |
| `critic_rejection` | Critic says `should_revise: true` |
| `high_risk` | Risk level is high |
| `explicit_request` | User explicitly requests deep thinking |

## Human-in-the-Loop Approval

Escalation can require approval:

```yaml
escalation:
  require_approval_to_escalate: true   # All escalations need approval
  require_approval_for_tier_3: true    # Only tier 3 needs approval
```

When approval is required:
1. Controller calls `approval_callback(reason, current_tier, target_tier)`
2. If callback returns `False`, escalation is blocked
3. Best available plan is used

---

## Usage Example

```python
from agent_kernel.engine import ThinkingPolicyController, ADAPTIVE_THINKING
from agent_kernel.workflows.runner import WorkflowRunner

# Create controller
controller = ThinkingPolicyController(default_config=ADAPTIVE_THINKING)

# Create runner with thinking support
runner = WorkflowRunner(
    context_assembler=assembler,
    executor=executor,
    thinking_policy_controller=controller,
)

# Run with automatic escalation
result = await runner.run_with_thinking("daily_checkin")

# Check reasoning metadata
if result.trace:
    print(f"Final tier: {result.trace.reasoning.final_tier}")
    print(f"Escalations: {result.trace.reasoning.escalation_count}")
```

---

## Files Changed/Added

| File | Change |
|------|--------|
| `core/schemas/thinking.py` | **New** - ThinkingConfig and sub-schemas |
| `core/schemas/agent.py` | Added `thinking_config` field |
| `core/schemas/__init__.py` | Export new schemas |
| `engine/thinking_policy.py` | **Rewritten** - Full ThinkingPolicyController |
| `engine/critic.py` | Added `from_thinking_config()` factory |
| `engine/__init__.py` | Updated exports |
| `context/assembler.py` | Added `assemble_with_thinking()` |
| `workflows/runner.py` | Added `run_with_thinking()` |
| `cli/main.py` | Added thinking policy commands |

---

## Next Steps

1. ~~**Wire `reasoning.effort` to LLM calls**~~ ✅ **DONE** (v1.0.4)
2. ~~**Persist ThinkingSession**~~ ✅ **DONE** (v1.2) — `to_dict()`/`from_dict()` on ThinkingSession and AdaptiveThinkingSession
3. ~~**Add metrics**~~ ✅ **DONE** (v1.2) — `compute_thinking_metrics()` + `thinking-stats` CLI command
4. ~~**Fine-tune thresholds**~~ ✅ **DONE** (v1.2) — Configurable `EscalationConfig` thresholds replace hardcoded values

---

# v1.0.4 Additions: Reasoning Effort Wired to LLM

> Added in v1.0.4 - `reasoning_effort` now flows from AgentProfile to LLM API calls.

## Overview

The `reasoning_effort` parameter in `ModelConfig` now affects actual LLM behavior:

- **OpenAI o-series models**: Passed directly as `reasoning.effort` parameter
- **Anthropic Claude**: Translated to extended thinking with `budget_tokens`

## Configuration

```yaml
# configs/agents/work_context_agent.yaml
llm_config:
  provider: openai
  model: gpt-4o
  reasoning_effort: medium  # none | low | medium | high
```

## Implementation

### AgentProfile → CustomEngine → LLMService

```python
# core/schemas/agent.py
class ModelConfig(KernelModel):
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description="Reasoning effort for thinking policy",
    )

# engine/custom_engine.py
response = await self._llm_service.generate(
    ...
    reasoning_effort=agent_profile.llm_config.reasoning_effort,
)

# services/llm.py (Anthropic)
THINKING_BUDGET_MAP = {
    "none": 0,
    "low": 0,
    "medium": 4000,
    "high": 10000,
}
```

### Anthropic Extended Thinking

For Claude models, `reasoning_effort` maps to extended thinking:

| Effort | budget_tokens | Effect |
|--------|--------------|--------|
| `none` | 0 | No extended thinking |
| `low` | 0 | No extended thinking |
| `medium` | 4000 | Moderate deliberation |
| `high` | 10000 | Deep reasoning |

```python
if reasoning_effort and reasoning_effort in ("medium", "high"):
    request["thinking"] = {
        "type": "enabled",
        "budget_tokens": THINKING_BUDGET_MAP[reasoning_effort],
    }
```

---

# v1.0.4 Additions: Tool Retry and Circuit Breaker

> Added in v1.0.4 - Resilient tool execution with exponential backoff and circuit breaker.

## Overview

The `ToolBroker` now supports:
- **Retry with exponential backoff** for transient failures
- **Circuit breaker** to prevent cascading failures
- Configurable via settings or workflow YAML

## RetryConfig

```python
@dataclass
class RetryConfig:
    max_retries: int = 3
    base_delay_ms: int = 1000
    max_delay_ms: int = 30000
    exponential_base: float = 2.0
    jitter_factor: float = 0.1
    retryable_errors: set[str] = {"TIMEOUT", "RATE_LIMITED", "SERVICE_UNAVAILABLE"}
```

## Circuit Breaker

Prevents repeated calls to failing services:

```
CLOSED ──[failure]──► threshold reached ──► OPEN
   ▲                                           │
   │                                           │
   └────────[success]◄── HALF_OPEN ◄──[timeout]┘
```

## Configuration

### Global Settings (config.py)

```python
tool_broker_retry_enabled: bool = True
tool_broker_retry_max_retries: int = 3
tool_broker_retry_base_delay_ms: int = 1000
tool_broker_circuit_breaker_enabled: bool = True
tool_broker_circuit_breaker_failure_threshold: int = 5
```

### Per-Workflow (YAML)

```yaml
# configs/workflows/task_sync.yaml
retry_config:
  max_retries: 4
  base_delay_ms: 3000
  max_delay_ms: 60000
  exponential_base: 2.0
  jitter_factor: 0.3
```

## Files Added/Modified

| File | Change |
|------|--------|
| `tools/retry.py` | **New** - RetryConfig, CircuitBreaker, retry utilities |
| `tools/broker.py` | Integrated retry and circuit breaker |
| `core/config.py` | Added retry/circuit breaker settings |
| `cli/main.py` | Wired settings to ToolBroker creation |

---

# v1.2 Additions: Optimization & Observability

> Added in v1.2 — LLM semantic cache, trace-based feedback loops, thinking metrics, and configurable thresholds.

## LLM Semantic Cache

**Location:** `src/agent_kernel/services/llm_cache.py`

SQLite-backed, tier-aware LLM response cache. Prevents duplicate API calls when the same prompt is re-evaluated at the same or lower reasoning effort.

**Key invariant:** A cached response from a lower effort tier must NOT satisfy a higher-effort request, but a higher-effort response CAN satisfy a lower-effort request (`cached_effort_rank >= requested_effort_rank`).

```python
EFFORT_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
```

### LLMSemanticCache

```python
class LLMSemanticCache:
    def __init__(db_path, default_ttl_seconds=86400, enabled=True)
    def compute_prompt_hash(system_prompt, user_prompt) -> str
    def lookup(prompt_hash, model, requested_tier, requested_effort) -> CacheEntry | None
    def store(prompt_hash, model, tier, reasoning_effort, response, ttl_seconds=None)
    def invalidate(prompt_hash, model=None)
    def cleanup_expired() -> int
    def stats() -> dict
```

### CachedLLMService

**Location:** `src/agent_kernel/services/llm.py`

Wrapper around any `LLMService` that adds caching:

```python
class CachedLLMService(LLMService):
    def __init__(inner: LLMService, cache: LLMSemanticCache, event_log=None)
    def set_tier_context(tier: int, reasoning_effort: str)
    # Delegates generate/generate_with_metadata through cache
    # Stream bypasses cache
```

### Configuration

```python
# core/config.py Settings
llm_cache_enabled: bool = True
llm_cache_db_path: str = "./data/llm_cache.db"
llm_cache_default_ttl_seconds: int = 86400
```

### Events

| Event Type | When |
|------------|------|
| `LLM_CACHE_HIT` | Cache returns a valid entry |
| `LLM_CACHE_MISS` | No valid cache entry found |

---

## Trace-Based Feedback Loops

### AdaptiveTimeoutManager

**Location:** `src/agent_kernel/tools/adaptive_timeout.py`

Per-capability P99-based timeout tuning from historical `ToolCallRecord` data.

```python
class AdaptiveTimeoutManager:
    def __init__(trace_store, buffer_factor=1.2, min_samples=10,
                 cache_ttl_seconds=300, lookback_hours=168)
    def get_timeout(capability_name, default_timeout_ms=30000) -> int
    def get_all_stats() -> dict[str, CapabilityLatencyStats]
    def refresh_stats()
```

**Integration with ToolBroker:**

```python
broker = ToolBroker(
    registry=registry,
    timeout_manager=AdaptiveTimeoutManager(trace_store=trace_store),
)
# broker.execute() uses adaptive timeout when available
```

### SuccessRateRouter

**Location:** `src/agent_kernel/engine/success_rate_router.py`

Standalone model routing by historical success rate from traces.

```python
class SuccessRateRouter:
    def __init__(trace_store, min_success_rate=0.85, min_samples=20,
                 max_cost_per_call=None, cache_ttl_seconds=300)
    def recommend(workflow_id=None, budget_usd=None,
                  candidate_models=None) -> list[ModelRecommendation]
    def best_model(workflow_id=None, fallback="gpt-4o") -> str
```

### CostAnomalyDetector

**Location:** `src/agent_kernel/engine/cost_anomaly.py`

Rolling cost anomaly detection with event emission. Compares per-trace costs against a rolling window and flags outliers exceeding a configurable std-dev threshold.

```python
class CostAnomalyDetector:
    def __init__(event_log=None, trace_store=None, std_dev_threshold=2.0,
                 window_size=50, lookback_hours=168, min_data_points=10)
    def check(trace) -> AnomalyReport | None
    def refresh_from_traces()
    def get_rolling_stats() -> dict
```

**Integration with WorkflowRunner:**

```python
runner = WorkflowRunner(
    ...,
    cost_anomaly_detector=CostAnomalyDetector(event_log=event_log),
)
# run_with_thinking() checks for anomalies after trace creation
```

**Events:** Emits `COST_ANOMALY` event when deviation exceeds threshold.

---

## Thinking Metrics & CLI

**Location:** `src/agent_kernel/engine/thinking_metrics.py`

Aggregates reasoning metadata from `DecisionTrace` objects.

```python
@dataclass
class ThinkingMetrics:
    total_traces: int
    traces_with_reasoning: int
    tier_distribution: dict[int, int]      # tier -> count
    escalation_count: int
    escalation_rate: float
    gate_failure_counts: dict[str, int]    # failure_type -> count
    critic_utilization_rate: float
    model_success_rates: dict[str, float]  # model -> rate
    tokens_per_tier: dict[int, float]      # tier -> avg tokens
    cost_per_workflow: dict[str, float]    # workflow -> total USD

def compute_thinking_metrics(traces: list) -> ThinkingMetrics
```

### CLI Command

```bash
agent-kernel thinking-stats [--workflow ID] [--since-hours N] [--agent ID]
```

Renders Rich tables showing tier distribution, escalation rates, gate failures, model success rates, and cost per workflow.

---

## ThinkingSession Persistence

`ThinkingSession` and `AdaptiveThinkingSession` now support serialization for checkpoint persistence:

```python
# Serialize (excludes non-serializable approval_callback)
data = session.to_dict()

# Restore
session = ThinkingSession.from_dict(data, config, approval_callback=cb)
```

`AdaptiveThinkingSession.to_dict()` adds adaptive-specific fields: `workflow_id`, `tier_adjustment`, `model_override`, `timeout_adjustment_ms`.

Used by WorkflowRunner to persist session state when `WAITING_APPROVAL`, and restore on `resume()`.

---

## Configurable Quality Gate Thresholds

**Location:** `src/agent_kernel/core/schemas/thinking.py` → `EscalationConfig`

Replaces hardcoded values in `AdaptiveThinkingPolicyController`:

```python
class EscalationConfig(KernelModel):
    # ... existing fields ...
    high_escalation_rate_threshold: float = 0.3   # Trigger tier bump
    low_success_rate_threshold: float = 0.7       # Trigger model override
    model_success_threshold: float = 0.85         # Min viable model rate
```

These are read by `AdaptiveThinkingPolicyController` from the agent profile's `ThinkingConfig.escalation` instead of using hardcoded constants.

---

## Files Added/Modified (v1.2)

### New Files

| File | Purpose |
|------|---------|
| `services/llm_cache.py` | SQLite tier-aware LLM response cache |
| `tools/adaptive_timeout.py` | Per-capability P99 timeout tuning |
| `engine/success_rate_router.py` | Model routing by success rate |
| `engine/cost_anomaly.py` | Rolling cost anomaly detection |
| `engine/thinking_metrics.py` | Thinking metrics computation |

### Modified Files

| File | Changes |
|------|---------|
| `services/llm.py` | Added `CachedLLMService` wrapper |
| `core/config.py` | Cache and threshold settings |
| `memory/event_log.py` | `LLM_CACHE_HIT`, `LLM_CACHE_MISS`, `COST_ANOMALY` events |
| `engine/thinking_policy.py` | `to_dict()`/`from_dict()` on ThinkingSession |
| `engine/adaptive_thinking.py` | Session serialization, configurable thresholds |
| `core/schemas/thinking.py` | Threshold fields on EscalationConfig |
| `tools/broker.py` | Optional `AdaptiveTimeoutManager` |
| `workflows/runner.py` | Cache tier context, cost anomaly check |
| `cli/main.py` | `thinking-stats` command |
