"""Minimal Agent Example.

Demonstrates the core data flow:
    Context -> Plan -> Execute -> Trace

No API keys required. Uses a stub engine and in-memory stores.
"""

from __future__ import annotations

import asyncio

from agent_kernel import (
    AgentProfile,
    CapabilityRegistry,
    ContextPacket,
    DeterministicExecutor,
    Plan,
    ToolBroker,
)
from agent_kernel.core.schemas import (
    ApprovalPolicy,
    ContextPolicy,
    ModelConfig,
    RiskAssessment,
    SideEffect,
)
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink


# ---------------------------------------------------------------------------
# 1. Stub engine -- returns a hardcoded Plan (no LLM needed)
# ---------------------------------------------------------------------------
class StubEngine:
    """Minimal engine that returns a plan with no actions."""

    @property
    def engine_id(self) -> str:
        return "stub"

    @property
    def version(self) -> str:
        return "1.0.0"

    async def propose(
        self,
        context_packet: ContextPacket,
        agent_profile: AgentProfile,
        thinking_policy: object = None,
    ) -> Plan:
        return Plan(
            intent=context_packet.intent,
            summary=f"Minimal stub plan for: {context_packet.intent}",
            risk=RiskAssessment(level="low", reasons=["No actions"]),
        )


# ---------------------------------------------------------------------------
# 2. Main
# ---------------------------------------------------------------------------
async def main() -> None:
    print("=== Minimal Agent Example ===\n")

    # In-memory trace store (SQLite :memory:)
    trace_store = SQLiteTraceSink(":memory:")

    # Capability registry + tool broker (no tools registered)
    registry = CapabilityRegistry()
    broker = ToolBroker(registry=registry, enable_circuit_breaker=False)

    # Executor
    executor = DeterministicExecutor(tool_broker=broker, trace_store=trace_store)

    # Agent profile (must_cite=False since we have no real context)
    profile = AgentProfile(
        agent_profile_id="minimal",
        name="Minimal Agent",
        llm_config=ModelConfig(provider="stub", model="stub"),
        context_policy=ContextPolicy(must_cite=False),
        approval_policy=ApprovalPolicy(
            auto_approve_side_effects=[SideEffect.NONE, SideEffect.READ],
        ),
    )

    # Build a context packet with a simple intent
    context = ContextPacket(intent="What should I work on today?")

    # Propose a plan with the stub engine
    engine = StubEngine()
    plan = await engine.propose(context, profile)
    print(f"Stub engine proposed plan: {plan.summary}")

    # Execute the plan (no actions, so the executor just creates a trace)
    trace = await executor.execute(
        plan=plan,
        context_packet=context,
        agent_profile=profile,
        engine_id=engine.engine_id,
    )

    # Inspect the trace
    print(f"\nTrace ID: {trace.trace_id}")
    print(f"Outcome:  {trace.outcome.status.value}")
    print(f"Summary:  {trace.outcome.summary}")
    print(f"Plan:     {trace.plan.summary}")


if __name__ == "__main__":
    asyncio.run(main())
