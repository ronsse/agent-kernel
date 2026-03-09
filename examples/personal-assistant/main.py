"""Personal Assistant Example.

Demonstrates:
- Full memory stack (doc store, graph store, event log)
- Multi-action plans with read and write side effects
- Tool capability registration with stub handlers
- Approval flow (pending -> granted)

No API keys required. Uses stub engine and in-memory stores.
"""

from __future__ import annotations

import asyncio

from agent_kernel import (
    AgentProfile,
    ApprovalGate,
    CapabilityRegistry,
    ContextPacket,
    DeterministicExecutor,
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
    SideEffect,
)
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink


# ---------------------------------------------------------------------------
# 1. Stub tool handlers (no real services)
# ---------------------------------------------------------------------------
def list_tasks(**kwargs: object) -> dict:
    """Return mock tasks."""
    return {
        "tasks": [
            {"id": "t1", "title": "Review PR #42", "priority": "high"},
            {"id": "t2", "title": "Write design doc", "priority": "medium"},
            {"id": "t3", "title": "Fix flaky test", "priority": "low"},
        ],
        "count": 3,
    }


def create_summary(**kwargs: object) -> dict:
    """Create a mock summary."""
    return {
        "summary_id": "sum_001",
        "text": "3 tasks today: 1 high, 1 medium, 1 low priority.",
    }


# ---------------------------------------------------------------------------
# 2. Stub engine
# ---------------------------------------------------------------------------
class AssistantEngine:
    """Engine that returns a plan with two actions."""

    @property
    def engine_id(self) -> str:
        return "assistant_stub"

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
            summary="List tasks and create daily summary.",
            actions=[
                ActionRequest(
                    capability_name="tasks.list@v1",
                    args={"status": "open"},
                    side_effect=SideEffect.NONE,
                ),
                ActionRequest(
                    capability_name="summary.create@v1",
                    args={"scope": "daily"},
                    side_effect=SideEffect.LOCAL_WRITE,
                    idempotency_key="summary_001",
                ),
            ],
            risk=RiskAssessment(level="low", reasons=["Only local writes"]),
        )


# ---------------------------------------------------------------------------
# 3. Main
# ---------------------------------------------------------------------------
async def main() -> None:
    print("=== Personal Assistant Example ===\n")

    # --- Trace store ---
    trace_store = SQLiteTraceSink(":memory:")

    # --- Capability registry ---
    registry = CapabilityRegistry()
    registry.register(
        CapabilityDef(
            capability_name="tasks.list@v1",
            description="List open tasks",
            input_schema={"type": "object", "properties": {"status": {"type": "string"}}},
            output_schema={},
            side_effect_level=SideEffect.NONE,
        )
    )
    registry.register(
        CapabilityDef(
            capability_name="summary.create@v1",
            description="Create a daily summary",
            input_schema={"type": "object", "properties": {"scope": {"type": "string"}}},
            output_schema={},
            side_effect_level=SideEffect.LOCAL_WRITE,
        )
    )
    print(f"Registered capabilities: {', '.join(registry.list_names())}")

    # --- Tool broker with stub handlers ---
    broker = ToolBroker(registry=registry, enable_circuit_breaker=False)
    broker.local_adapter.register("tasks.list@v1", list_tasks)
    broker.local_adapter.register("summary.create@v1", create_summary)

    # --- Agent profile ---
    profile = AgentProfile(
        agent_profile_id="assistant",
        name="Personal Assistant",
        llm_config=ModelConfig(provider="stub", model="stub"),
        allowed_capabilities=["tasks.list@v1", "summary.create@v1"],
        context_policy=ContextPolicy(must_cite=False),
        approval_policy=ApprovalPolicy(
            auto_approve_side_effects=[SideEffect.NONE, SideEffect.READ, SideEffect.LOCAL_WRITE],
        ),
    )

    # --- Executor ---
    executor = DeterministicExecutor(tool_broker=broker, trace_store=trace_store)

    # --- Propose and execute ---
    context = ContextPacket(intent="What should I focus on today?")
    engine = AssistantEngine()
    plan = await engine.propose(context, profile)

    print(f"Plan has {len(plan.actions)} actions:")
    for i, action in enumerate(plan.actions, 1):
        parts = [f"side_effect={action.side_effect.value}"]
        if action.idempotency_key:
            parts.append(f"idempotency_key={action.idempotency_key}")
        print(f"  {i}. {action.capability_name} ({', '.join(parts)})")

    print("\n--- Executing plan ---")
    trace = await executor.execute(
        plan=plan,
        context_packet=context,
        agent_profile=profile,
        engine_id=engine.engine_id,
    )

    print(f"Trace ID: {trace.trace_id}")
    print(f"Outcome:  {trace.outcome.status.value}")
    print("Tool calls:")
    for tc in trace.tool_calls:
        print(f"  {tc.capability_name} -> {tc.status.value} ({tc.duration_ms}ms)")

    # --- Demonstrate approval flow ---
    print("\n--- Approval demo ---")
    gate = ApprovalGate()
    pending = gate.request_approval(
        action_id="demo_action",
        capability_name="notification.send@v1",
        args={"channel": "email", "message": "Hello"},
        trace_id=trace.trace_id,
        agent_profile_id="assistant",
    )
    print(f"Created pending approval: {pending.approval_id}")

    gate.approve(pending.approval_id, approved_by="user", reason="Looks good")
    print(f"Approved! Token: {pending.token}")


if __name__ == "__main__":
    asyncio.run(main())
