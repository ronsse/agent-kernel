"""Tool-Heavy Workflow Example.

Demonstrates:
- Multiple tool capabilities with different side effects
- Approval gates (external writes require approval)
- Retry configuration
- Circuit breaker setup
- Full approval flow: pending -> grant -> re-execute

No API keys required. Uses stub handlers and in-memory stores.
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
from agent_kernel.tools.retry import RetryConfig
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink


# ---------------------------------------------------------------------------
# 1. Stub tool handlers
# ---------------------------------------------------------------------------
def fetch_data(**kwargs: object) -> dict:
    """Simulate fetching data from an external API."""
    return {"rows": 250, "source": kwargs.get("source", "api")}


def transform_data(**kwargs: object) -> dict:
    """Simulate transforming data locally."""
    return {"transformed": True, "rows_out": 248, "dropped": 2}


def send_notification(**kwargs: object) -> dict:
    """Simulate sending an external notification."""
    return {
        "sent": True,
        "channel": kwargs.get("channel", "email"),
        "recipients": 1,
    }


# ---------------------------------------------------------------------------
# 2. Stub engine
# ---------------------------------------------------------------------------
class WorkflowEngine:
    """Engine that returns a plan with 3 actions of increasing side effects."""

    @property
    def engine_id(self) -> str:
        return "workflow_stub"

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
            summary="Fetch data, transform locally, then notify stakeholders.",
            actions=[
                ActionRequest(
                    capability_name="data.fetch@v1",
                    args={"source": "api", "limit": 500},
                    side_effect=SideEffect.NONE,
                ),
                ActionRequest(
                    capability_name="data.transform@v1",
                    args={"mode": "clean", "drop_nulls": True},
                    side_effect=SideEffect.LOCAL_WRITE,
                    idempotency_key="transform_001",
                ),
                ActionRequest(
                    capability_name="notification.send@v1",
                    args={"channel": "email", "message": "Pipeline complete"},
                    side_effect=SideEffect.EXTERNAL_WRITE,
                    requires_approval=True,
                    idempotency_key="notify_001",
                ),
            ],
            risk=RiskAssessment(
                level="medium",
                reasons=["External notification to stakeholders"],
            ),
        )


# ---------------------------------------------------------------------------
# 3. Main
# ---------------------------------------------------------------------------
async def main() -> None:
    print("=== Tool-Heavy Workflow Example ===\n")

    # --- Register capabilities ---
    registry = CapabilityRegistry()
    registry.register(CapabilityDef(
        capability_name="data.fetch@v1",
        description="Fetch data from external source",
        input_schema={"type": "object", "properties": {"source": {"type": "string"}}},
        output_schema={},
        side_effect_level=SideEffect.NONE,
    ))
    registry.register(CapabilityDef(
        capability_name="data.transform@v1",
        description="Transform data locally",
        input_schema={"type": "object", "properties": {"mode": {"type": "string"}}},
        output_schema={},
        side_effect_level=SideEffect.LOCAL_WRITE,
    ))
    registry.register(CapabilityDef(
        capability_name="notification.send@v1",
        description="Send notification to external channel",
        input_schema={"type": "object", "properties": {"channel": {"type": "string"}}},
        output_schema={},
        side_effect_level=SideEffect.EXTERNAL_WRITE,
        requires_approval_default=True,
    ))

    print(f"Registered: {', '.join(registry.list_names())}")

    # --- Retry config ---
    retry_config = RetryConfig(max_retries=2, base_delay_ms=100, max_delay_ms=500)
    print(f"Retry config: max_retries={retry_config.max_retries}, base_delay={retry_config.base_delay_ms}ms")

    # --- Tool broker with retry + circuit breaker ---
    broker = ToolBroker(
        registry=registry,
        retry_config=retry_config,
        enable_circuit_breaker=True,
    )
    broker.local_adapter.register("data.fetch@v1", fetch_data)
    broker.local_adapter.register("data.transform@v1", transform_data)
    broker.local_adapter.register("notification.send@v1", send_notification)

    # --- Trace store ---
    trace_store = SQLiteTraceSink(":memory:")

    # --- Approval gate ---
    approval_gate = ApprovalGate()

    # --- Agent profile: auto-approve reads and local, require approval for external ---
    profile = AgentProfile(
        agent_profile_id="pipeline",
        name="Pipeline Agent",
        llm_config=ModelConfig(provider="stub", model="stub"),
        allowed_capabilities=[
            "data.fetch@v1", "data.transform@v1", "notification.send@v1",
        ],
        context_policy=ContextPolicy(must_cite=False),
        approval_policy=ApprovalPolicy(
            auto_approve_side_effects=[SideEffect.NONE, SideEffect.READ, SideEffect.LOCAL_WRITE],
            require_approval_for=["notification.send@v1"],
        ),
    )

    # --- Executor ---
    executor = DeterministicExecutor(
        tool_broker=broker,
        trace_store=trace_store,
        approval_gate=approval_gate,
    )

    # --- First execution: notification should require approval ---
    context = ContextPacket(intent="Run data pipeline and notify stakeholders")
    engine = WorkflowEngine()
    plan = await engine.propose(context, profile)

    print("\n--- First execution (notification requires approval) ---")
    trace = await executor.execute(
        plan=plan,
        context_packet=context,
        agent_profile=profile,
        engine_id=engine.engine_id,
    )

    print(f"Outcome: {trace.outcome.status.value}")
    for tc in trace.tool_calls:
        suffix = ""
        if tc.status.value == "skipped":
            suffix = " (needs approval)"
        print(f"  {tc.capability_name} -> {tc.status.value}{suffix}")

    # --- Grant approval for the notification action ---
    print("\n--- Granting approval ---")
    pending_list = approval_gate.list_pending()
    approval_tokens: dict[str, str] = {}
    for pending in pending_list:
        approval_gate.approve(pending.approval_id, approved_by="admin", reason="Approved")
        approval_tokens[pending.action_id] = pending.token
        print(f"Approved action: {pending.action_id}")

    # --- Re-execute the SAME plan with approval tokens ---
    # In a real workflow, you'd resume the same plan (same action IDs).
    print("\n--- Re-executing with approval ---")
    trace2 = await executor.execute(
        plan=plan,
        context_packet=context,
        agent_profile=profile,
        engine_id=engine.engine_id,
        approval_tokens=approval_tokens,
    )

    print(f"Outcome: {trace2.outcome.status.value}")
    for tc in trace2.tool_calls:
        print(f"  {tc.capability_name} -> {tc.status.value}")

    # --- Circuit breaker status ---
    print("\n--- Circuit breaker status ---")
    states = broker.get_circuit_breaker_states()
    if states:
        for cap, state in states.items():
            print(f"  {cap}: {state}")
    else:
        print("All circuits: closed (healthy)")


if __name__ == "__main__":
    asyncio.run(main())
