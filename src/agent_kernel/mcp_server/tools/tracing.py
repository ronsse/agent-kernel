"""MCP tools for trace ingestion and status reporting.

Enables Claude Code and other MCP clients to report their
activity to the Agent Kernel for tracking and experience mining.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.core.schemas.plan import Plan
from agent_kernel.core.schemas.trace import (
    CallStatus,
    CostRecord,
    DecisionTrace,
    Outcome,
    OutcomeStatus,
    ToolCallRecord,
)

logger = structlog.get_logger(__name__)

# Map string status to CallStatus enum
_STATUS_MAP = {
    "success": CallStatus.SUCCESS,
    "error": CallStatus.ERROR,
    "failed": CallStatus.FAILED,
    "denied": CallStatus.DENIED,
    "skipped": CallStatus.SKIPPED,
    "timeout": CallStatus.TIMEOUT,
}

# Map string outcome to OutcomeStatus enum
_OUTCOME_MAP = {
    "completed": OutcomeStatus.COMPLETED,
    "partial": OutcomeStatus.PARTIAL,
    "failed": OutcomeStatus.FAILED,
    "needs_approval": OutcomeStatus.NEEDS_APPROVAL,
    "cancelled": OutcomeStatus.CANCELLED,
}


def register_tracing_tools(mcp: Any, stores: Any) -> None:
    """Register trace ingestion tools with the MCP server.

    Args:
        mcp: The FastMCP server instance.
        stores: StoreBundle with access to trace store and graph store.
    """

    @mcp.tool()
    def kernel_trace_ingest(
        agent_id: str,
        intent: str,
        actions: list[dict[str, Any]],
        outcome_status: str = "completed",
        outcome_summary: str = "",
        session_duration_ms: int | None = None,
        cost_usd: float | None = None,
    ) -> dict[str, Any]:
        """Ingest a trace from Claude Code or other MCP clients.

        Report what actions were taken during a session so the
        kernel can track activity, mine experience, and build
        the knowledge graph.

        Args:
            agent_id: Agent identifier
            intent: What the agent was trying to accomplish
            actions: List of tool calls/actions taken.
                Each: capability, input, output, status
            outcome_status: completed, partial, or failed
            outcome_summary: Brief description of outcome
            session_duration_ms: Total session time in ms
            cost_usd: Total LLM cost for this session

        Returns:
            Dict with trace_id and status
        """
        try:
            trace_id = generate_ulid()
            now = datetime.now(UTC)

            # Build tool call records
            tool_calls = []
            for action in actions:
                status_str = action.get("status", "success")
                tcr = ToolCallRecord(
                    tool_call_id=generate_ulid(),
                    capability_name=action.get(
                        "capability", "mcp.unknown@v1"
                    ),
                    started_at=now,
                    ended_at=now,
                    duration_ms=action.get("duration_ms", 0),
                    input=action.get("input", {}),
                    output=action.get("output", {}),
                    status=_STATUS_MAP.get(
                        status_str, CallStatus.SUCCESS
                    ),
                )
                tool_calls.append(tcr)

            # Build Plan (minimal for MCP ingestion)
            plan = Plan(
                intent=intent,
                summary=outcome_summary
                or f"MCP-ingested session: {intent}",
            )

            # Build Outcome
            outcome = Outcome(
                status=_OUTCOME_MAP.get(
                    outcome_status, OutcomeStatus.COMPLETED
                ),
                summary=outcome_summary,
            )

            # Build LLM call records if cost provided
            llm_calls: list[Any] = []
            if cost_usd is not None:
                llm_calls.append({
                    "llm_call_id": generate_ulid(),
                    "stage": "other",
                    "provider": "anthropic",
                    "model": "claude-code",
                    "cost": CostRecord(
                        estimated_cost_usd=cost_usd
                    ).model_dump(),
                    "duration_ms": session_duration_ms or 0,
                })

            # Build the trace
            trace = DecisionTrace(
                trace_id=trace_id,
                run_id=generate_ulid(),
                workflow_id="mcp-ingestion",
                agent_profile_id=agent_id,
                engine_id="mcp-client",
                intent=intent,
                timestamp=now,
                context_packet_id="mcp-ingested",
                plan=plan,
                tool_calls=tool_calls,
                llm_calls=llm_calls,
                outcome=outcome,
            )

            # Write to trace store
            if stores.trace_store is None:
                return {
                    "status": "degraded",
                    "trace_id": trace_id,
                    "message": (
                        "Trace constructed but no "
                        "trace store configured"
                    ),
                }

            stores.trace_store.write(trace)
            logger.info(
                "trace_ingested_via_mcp",
                trace_id=trace_id,
                agent_id=agent_id,
                action_count=len(actions),
            )

            # Decompose into graph if available
            if stores.graph_store is not None:
                try:
                    _decompose_trace_to_graph(
                        trace, stores.graph_store
                    )
                except Exception as e:
                    logger.warning(
                        "graph_decomposition_failed",
                        error=str(e),
                    )

            return {
                "status": "success",
                "trace_id": trace_id,
                "message": (
                    f"Trace ingested with "
                    f"{len(actions)} actions"
                ),
            }

        except Exception as e:
            logger.exception(
                "trace_ingest_failed", error=str(e)
            )
            return {
                "status": "error",
                "error": str(e),
            }

    @mcp.tool()
    def kernel_trace_status(
        agent_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Get recent trace status for an agent.

        Args:
            agent_id: Optional agent to filter by
            limit: Max number of traces to return (default 10)

        Returns:
            Dict with recent traces and counts
        """
        try:
            if stores.trace_store is None:
                return {
                    "status": "error",
                    "error": "No trace store configured",
                }

            traces = stores.trace_store.list_traces(
                limit=limit,
                agent_profile_id=agent_id,
            )

            return {
                "status": "success",
                "count": len(traces),
                "traces": [
                    {
                        "trace_id": t.trace_id,
                        "intent": t.intent,
                        "outcome_status": t.outcome.status.value,
                        "outcome_summary": t.outcome.summary or "",
                        "timestamp": t.timestamp.isoformat(),
                        "tool_call_count": len(t.tool_calls),
                        "success_rate": t.success_rate(),
                    }
                    for t in traces
                ],
            }

        except Exception as e:
            logger.exception(
                "trace_status_failed", error=str(e)
            )
            return {
                "status": "error",
                "error": str(e),
            }


def _decompose_trace_to_graph(
    trace: DecisionTrace, graph_store: Any
) -> None:
    """Decompose a trace into graph nodes and edges.

    Lightweight version creating basic trajectory nodes.
    Full decomposition happens via context graph ingestion.
    """
    now = datetime.now(UTC).isoformat()
    trajectory_id = f"trajectory:{trace.trace_id}"

    try:
        # Create trajectory node
        graph_store.upsert_node(
            node_id=trajectory_id,
            node_type=NodeType.TRAJECTORY.value,
            properties={
                "name": trace.intent[:100],
                "intent": trace.intent,
                "outcome_status": trace.outcome.status.value,
                "agent_profile_id": trace.agent_profile_id,
                "timestamp": trace.timestamp.isoformat(),
                "created_at": now,
                "updated_at": now,
            },
        )

        # Create edges for each tool call
        for tc in trace.tool_calls:
            cap_node_id = f"capability:{tc.capability_name}"

            graph_store.upsert_node(
                node_id=cap_node_id,
                node_type=NodeType.CAPABILITY.value,
                properties={
                    "name": tc.capability_name,
                    "capability_name": tc.capability_name,
                    "created_at": now,
                    "updated_at": now,
                },
            )

            graph_store.upsert_edge(
                source_id=trajectory_id,
                target_id=cap_node_id,
                edge_type=EdgeType.TRAJECTORY_TOUCHED.value,
                properties={
                    "status": tc.status.value,
                    "duration_ms": tc.duration_ms,
                },
            )
    except Exception:
        logger.debug("graph_decomposition_skipped")
