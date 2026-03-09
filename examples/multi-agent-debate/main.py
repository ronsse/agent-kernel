"""Multi-Agent Debate Example.

Demonstrates:
- Two engines proposing competing plans (optimistic vs conservative)
- Plan comparison by risk level and action count
- A simple judge function for plan selection
- Executing only the winning plan

No API keys required. Uses stub engines and in-memory stores.
"""

from __future__ import annotations

import asyncio

from agent_kernel import (
    AgentProfile,
    CapabilityRegistry,
    ContextPacket,
    DeterministicExecutor,
    EngineRegistry,
    Plan,
    ToolBroker,
)
from agent_kernel.core.schemas import (
    ActionRequest,
    ApprovalPolicy,
    CapabilityDef,
    ContextPolicy,
    ModelConfig,
    RiskAssessment,
    RiskLevel,
    SideEffect,
)
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink


# ---------------------------------------------------------------------------
# 1. Stub tool handlers
# ---------------------------------------------------------------------------
def fetch_data(**kwargs: object) -> dict:
    """Simulate fetching data from a source."""
    return {"rows": 100, "source": kwargs.get("source", "unknown")}


def validate_data(**kwargs: object) -> dict:
    """Simulate data validation."""
    return {"valid": True, "errors": 0}


def transform_data(**kwargs: object) -> dict:
    """Simulate a data transformation."""
    return {"transformed": True, "rows": 100}


def deploy(**kwargs: object) -> dict:
    """Simulate deployment."""
    return {"deployed": True, "version": "1.0.1"}


# ---------------------------------------------------------------------------
# 2. Competing engines
# ---------------------------------------------------------------------------
class OptimisticEngine:
    """Engine that proposes an aggressive, higher-risk plan."""

    @property
    def engine_id(self) -> str:
        return "optimistic"

    @property
    def version(self) -> str:
        return "1.0.0"

    async def propose(
        self,
        context: ContextPacket,
        profile: AgentProfile,
        thinking_policy: object = None,
    ) -> Plan:
        return Plan(
            intent=context.intent,
            summary="Move fast: batch process 3 data sources and deploy immediately",
            actions=[
                ActionRequest(
                    capability_name="data.fetch@v1",
                    args={"source": "all", "batch": True},
                    side_effect=SideEffect.NONE,
                ),
                ActionRequest(
                    capability_name="data.transform@v1",
                    args={"mode": "aggressive"},
                    side_effect=SideEffect.LOCAL_WRITE,
                    idempotency_key="transform_batch",
                ),
                ActionRequest(
                    capability_name="deploy@v1",
                    args={"target": "production"},
                    side_effect=SideEffect.LOCAL_WRITE,
                    idempotency_key="deploy_prod",
                ),
            ],
            risk=RiskAssessment(
                level=RiskLevel.MEDIUM,
                reasons=["Batch processing skips validation", "Direct production deploy"],
            ),
        )


class ConservativeEngine:
    """Engine that proposes a cautious, lower-risk plan."""

    @property
    def engine_id(self) -> str:
        return "conservative"

    @property
    def version(self) -> str:
        return "1.0.0"

    async def propose(
        self,
        context: ContextPacket,
        profile: AgentProfile,
        thinking_policy: object = None,
    ) -> Plan:
        return Plan(
            intent=context.intent,
            summary="Process data sources one at a time with validation between each step",
            actions=[
                ActionRequest(
                    capability_name="data.fetch@v1",
                    args={"source": "primary"},
                    side_effect=SideEffect.NONE,
                ),
                ActionRequest(
                    capability_name="data.validate@v1",
                    args={"strict": True},
                    side_effect=SideEffect.NONE,
                ),
                ActionRequest(
                    capability_name="data.transform@v1",
                    args={"mode": "safe"},
                    side_effect=SideEffect.LOCAL_WRITE,
                    idempotency_key="transform_safe",
                ),
                ActionRequest(
                    capability_name="deploy@v1",
                    args={"target": "staging"},
                    side_effect=SideEffect.LOCAL_WRITE,
                    idempotency_key="deploy_staging",
                ),
            ],
            risk=RiskAssessment(
                level=RiskLevel.LOW,
                reasons=["Step-by-step validation", "Staging deploy only"],
            ),
        )


# ---------------------------------------------------------------------------
# 3. Judge function
# ---------------------------------------------------------------------------
RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}


def judge_plans(plan_a: Plan, plan_b: Plan) -> tuple[Plan, str, str]:
    """Select the best plan based on risk level (prefer lower risk).

    Returns (selected_plan, engine_name, reason).
    """
    risk_a = RISK_ORDER.get(plan_a.risk.level, 99)
    risk_b = RISK_ORDER.get(plan_b.risk.level, 99)

    if risk_a < risk_b:
        return plan_a, "optimistic", f"Lower risk ({plan_a.risk.level.value} vs {plan_b.risk.level.value})"
    if risk_b < risk_a:
        return plan_b, "conservative", f"Lower risk ({plan_b.risk.level.value} vs {plan_a.risk.level.value})"

    # Tie-break: prefer more actions (more thorough)
    if len(plan_a.actions) >= len(plan_b.actions):
        return plan_a, "optimistic", "Same risk, more thorough plan"
    return plan_b, "conservative", "Same risk, more thorough plan"


# ---------------------------------------------------------------------------
# 4. Main
# ---------------------------------------------------------------------------
async def main() -> None:
    print("=== Multi-Agent Debate Example ===\n")

    # --- Setup capabilities ---
    registry = CapabilityRegistry()
    for name, desc, se in [
        ("data.fetch@v1", "Fetch data from a source", SideEffect.NONE),
        ("data.validate@v1", "Validate data quality", SideEffect.NONE),
        ("data.transform@v1", "Transform data", SideEffect.LOCAL_WRITE),
        ("deploy@v1", "Deploy to environment", SideEffect.LOCAL_WRITE),
    ]:
        registry.register(CapabilityDef(
            capability_name=name,
            description=desc,
            input_schema={"type": "object"},
            output_schema={},
            side_effect_level=se,
        ))

    broker = ToolBroker(registry=registry, enable_circuit_breaker=False)
    broker.local_adapter.register("data.fetch@v1", fetch_data)
    broker.local_adapter.register("data.validate@v1", validate_data)
    broker.local_adapter.register("data.transform@v1", transform_data)
    broker.local_adapter.register("deploy@v1", deploy)

    trace_store = SQLiteTraceSink(":memory:")

    # --- Agent profile (shared for both engines) ---
    profile = AgentProfile(
        agent_profile_id="debater",
        name="Debate Agent",
        llm_config=ModelConfig(provider="stub", model="stub"),
        allowed_capabilities=[
            "data.fetch@v1", "data.validate@v1", "data.transform@v1", "deploy@v1",
        ],
        context_policy=ContextPolicy(must_cite=False),
        approval_policy=ApprovalPolicy(
            auto_approve_side_effects=[SideEffect.NONE, SideEffect.READ, SideEffect.LOCAL_WRITE],
        ),
    )

    # --- Register engines ---
    engine_registry = EngineRegistry()
    optimistic = OptimisticEngine()
    conservative = ConservativeEngine()
    engine_registry.register(optimistic)
    engine_registry.register(conservative)

    # --- Each engine proposes a plan ---
    context = ContextPacket(intent="Process and deploy data pipeline updates")

    plan_opt = await optimistic.propose(context, profile)
    plan_con = await conservative.propose(context, profile)

    print("--- Optimistic Engine ---")
    print(f"Plan: {plan_opt.summary}")
    print(f"Risk: {plan_opt.risk.level.value} | Actions: {len(plan_opt.actions)}")

    print("\n--- Conservative Engine ---")
    print(f"Plan: {plan_con.summary}")
    print(f"Risk: {plan_con.risk.level.value} | Actions: {len(plan_con.actions)}")

    # --- Judge selects the best plan ---
    selected, winner, reason = judge_plans(plan_opt, plan_con)

    print("\n--- Judge Decision ---")
    print(f"Selected: {winner}")
    print(f"Reason: {reason}")

    # --- Execute the selected plan ---
    executor = DeterministicExecutor(tool_broker=broker, trace_store=trace_store)

    print("\n--- Executing selected plan ---")
    trace = await executor.execute(
        plan=selected,
        context_packet=context,
        agent_profile=profile,
        engine_id=winner,
    )

    succeeded = sum(1 for tc in trace.tool_calls if tc.status.value == "success")
    print(f"Trace ID: {trace.trace_id}")
    print(f"Outcome:  {trace.outcome.status.value}")
    print(f"Tool calls: {len(trace.tool_calls)} total, {succeeded} succeeded")


if __name__ == "__main__":
    asyncio.run(main())
