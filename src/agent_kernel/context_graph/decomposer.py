"""Trace Decomposer - the core engine that turns DecisionTraces into graph structure.

This creates the EVENT CLOCK — the traversable record of what happened,
in what order, touching which entities, with what reasoning.

For each trace, creates:
1. TRAJECTORY node (the walk itself)
2. DECISION_EVENT nodes (one per tool call / key decision)
3. Edges: TRAJECTORY_TOUCHED → entities, TRAJECTORY_DECIDED → events
4. CO_OCCURS_WITH edges between entities that appeared together
5. Links to existing nodes (capabilities, workflows) or creates them
"""

from __future__ import annotations

from itertools import combinations
from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.core.schemas.knowledge import (
    DecisionEventProperties,
    DecompositionResult,
    TrajectoryProperties,
)
from agent_kernel.core.schemas.trace import DecisionTrace

if TYPE_CHECKING:
    from agent_kernel.context_graph.types import TypeRegistry
    from agent_kernel.memory.event_log import EventLog
    from agent_kernel.memory.graph_store import GraphStore

logger = structlog.get_logger(__name__)


class TraceDecomposer:
    """Decomposes a DecisionTrace into graph nodes and edges.

    This creates the EVENT CLOCK — the traversable record of what happened,
    in what order, touching which entities, with what reasoning.
    """

    def __init__(
        self,
        graph_store: GraphStore,
        event_log: EventLog | None = None,
        type_registry: TypeRegistry | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._event_log = event_log
        self._type_registry = type_registry

    async def decompose(self, trace: DecisionTrace) -> DecompositionResult:
        """Decompose a trace into graph structure.

        Creates TRAJECTORY node, DECISION_EVENT nodes, entity links,
        and co-occurrence edges. Returns created node/edge IDs.
        """
        nodes_created = 0
        edges_created = 0

        # 1. Create TRAJECTORY node
        trajectory_id = await self._create_trajectory_node(trace)
        nodes_created += 1

        # 2. Create DECISION_EVENT nodes for each tool call
        event_ids = await self._create_decision_events(trace, trajectory_id)
        nodes_created += len(event_ids)
        edges_created += len(event_ids)  # TRAJECTORY_DECIDED edges

        # 3. Link trajectory to entities it touched via context_refs
        entity_links = await self._link_entities(trace, trajectory_id)
        edges_created += len(entity_links)

        # 4. Update co-occurrence edges between entities in this trajectory
        co_occurrence_count = await self._update_co_occurrences(entity_links)
        edges_created += co_occurrence_count

        # 5. Record type usage
        if self._type_registry:
            await self._type_registry.record_type_usage(
                NodeType.TRAJECTORY.value,
                "node",
                {"trace_id": trace.trace_id},
            )

        # 6. Emit event
        if self._event_log:
            from agent_kernel.memory.event_log import EventType

            self._event_log.emit(
                event_type=EventType.TRAJECTORY_CREATED,
                source="context_graph.decomposer",
                entity_id=trajectory_id,
                entity_type="trajectory",
                payload={
                    "trace_id": trace.trace_id,
                    "decision_events": len(event_ids),
                    "entities_linked": len(entity_links),
                    "co_occurrences": co_occurrence_count,
                },
            )

        result = DecompositionResult(
            trajectory_node_id=trajectory_id,
            decision_event_ids=event_ids,
            entities_linked=len(entity_links),
            co_occurrence_edges_updated=co_occurrence_count,
            nodes_created=nodes_created,
            edges_created=edges_created,
        )

        logger.info(
            "trace_decomposed",
            trace_id=trace.trace_id,
            trajectory_id=trajectory_id,
            events=len(event_ids),
            entities=len(entity_links),
            co_occurrences=co_occurrence_count,
        )

        return result

    async def _create_trajectory_node(self, trace: DecisionTrace) -> str:
        """Create the TRAJECTORY node representing the agent's walk."""
        trajectory_id = f"trajectory:{trace.trace_id}"

        # Extract capabilities used from tool calls
        capabilities_used = list({
            tc.capability_name
            for tc in trace.tool_calls
            if tc.capability_name
        })

        # Build properties
        props = TrajectoryProperties(
            trace_id=trace.trace_id,
            agent_profile_id=trace.agent_profile_id,
            intent=trace.intent,
            workflow_id=trace.workflow_id or None,
            outcome_status=trace.outcome.status.value,
            outcome_summary=trace.outcome.summary,
            entities_touched=[],  # Populated during _link_entities
            capabilities_used=capabilities_used,
            step_count=len(trace.tool_calls),
            duration_ms=trace.total_duration_ms(),
            reasoning_tier=(
                trace.reasoning.final_tier if trace.reasoning else 1
            ),
        )

        self._graph_store.upsert_node(
            node_id=trajectory_id,
            node_type=NodeType.TRAJECTORY.value,
            properties=props.model_dump(mode="json"),
        )

        return trajectory_id

    async def _create_decision_events(
        self,
        trace: DecisionTrace,
        trajectory_id: str,
    ) -> list[str]:
        """Create DECISION_EVENT nodes for each tool call."""
        event_ids: list[str] = []

        for idx, tool_call in enumerate(trace.tool_calls):
            event_id = f"decision_event:{trace.trace_id}:{idx}"

            # Summarize input/output (truncate large payloads)
            input_summary = self._summarize_dict(tool_call.input, max_len=200)
            output_summary = (
                self._summarize_dict(tool_call.output, max_len=200)
                if tool_call.output
                else None
            )

            props = DecisionEventProperties(
                trace_id=trace.trace_id,
                step_order=idx,
                action_type="tool_call",
                capability_name=tool_call.capability_name,
                decision_rationale=None,  # Could extract from plan
                input_summary=input_summary,
                output_summary=output_summary,
                status=tool_call.status.value,
                duration_ms=tool_call.duration_ms,
            )

            self._graph_store.upsert_node(
                node_id=event_id,
                node_type=NodeType.DECISION_EVENT.value,
                properties=props.model_dump(mode="json"),
            )

            # Link trajectory → decision event
            self._graph_store.upsert_edge(
                source_id=trajectory_id,
                target_id=event_id,
                edge_type=EdgeType.TRAJECTORY_DECIDED.value,
                properties={"step_order": idx},
            )

            # Link to previous event for causal chain
            if event_ids:
                self._graph_store.upsert_edge(
                    source_id=event_id,
                    target_id=event_ids[-1],
                    edge_type=EdgeType.PRECEDED_BY.value,
                    properties={"step_order": idx},
                )

            event_ids.append(event_id)

        return event_ids

    async def _link_entities(
        self,
        trace: DecisionTrace,
        trajectory_id: str,
    ) -> list[str]:
        """Link trajectory to entities it touched via context_refs and artifacts.

        Returns list of entity node IDs that were linked.
        """
        entity_node_ids: list[str] = []

        # Link to entities from plan citations
        if trace.plan and trace.plan.context_refs_used:
            for citation in trace.plan.context_refs_used:
                node_id = f"{citation.ref_type.value}:{citation.ref_id}"
                # Check if node exists, if not just create a lightweight reference
                existing = self._graph_store.get_node(node_id)
                if existing is None:
                    self._graph_store.upsert_node(
                        node_id=node_id,
                        node_type=citation.ref_type.value,
                        properties={
                            "ref_id": citation.ref_id,
                            "source": "trace_decomposition",
                        },
                    )

                step_order = len(entity_node_ids)
                self._graph_store.upsert_edge(
                    source_id=trajectory_id,
                    target_id=node_id,
                    edge_type=EdgeType.TRAJECTORY_TOUCHED.value,
                    properties={"step_order": step_order},
                )
                entity_node_ids.append(node_id)

        # Link to outcome artifacts
        if trace.outcome and trace.outcome.artifacts:
            for artifact in trace.outcome.artifacts:
                node_id = f"{artifact.ref_type.value}:{artifact.ref_id}"
                existing = self._graph_store.get_node(node_id)
                if existing is None:
                    self._graph_store.upsert_node(
                        node_id=node_id,
                        node_type=artifact.ref_type.value,
                        properties={
                            "ref_id": artifact.ref_id,
                            "source": "trace_decomposition",
                        },
                    )

                self._graph_store.upsert_edge(
                    source_id=trajectory_id,
                    target_id=node_id,
                    edge_type=EdgeType.TRAJECTORY_PRODUCED.value,
                    properties={},
                )
                if node_id not in entity_node_ids:
                    entity_node_ids.append(node_id)

        # Update trajectory node with entities_touched
        trajectory_node = self._graph_store.get_node(trajectory_id)
        if trajectory_node:
            props = trajectory_node["properties"]
            props["entities_touched"] = entity_node_ids
            self._graph_store.upsert_node(
                node_id=trajectory_id,
                node_type=NodeType.TRAJECTORY.value,
                properties=props,
            )

        return entity_node_ids

    async def _update_co_occurrences(self, entity_ids: list[str]) -> int:
        """Update CO_OCCURS_WITH edge weights for entity pairs in this trajectory.

        Every pair of entities that appeared together in the same trajectory
        gets a co-occurrence edge. This is the structural learning mechanism —
        informed walks producing co-occurrence statistics that encode structure.

        Returns number of co-occurrence edges created or updated.
        """
        if len(entity_ids) < 2:
            return 0

        count = 0
        for id_a, id_b in combinations(entity_ids, 2):
            # Normalize order for consistent edge direction
            source, target = (id_a, id_b) if id_a < id_b else (id_b, id_a)

            # Try to get existing co-occurrence edge
            existing_edges = self._graph_store.get_edges(
                source,
                direction="outgoing",
                edge_type=EdgeType.CO_OCCURS_WITH.value,
            )

            existing = None
            for edge in existing_edges:
                if edge["target_id"] == target:
                    existing = edge
                    break

            if existing:
                # Increment weight
                props = existing.get("properties", {})
                weight = props.get("weight", 1) + 1
                props["weight"] = weight
                props["last_seen"] = utc_now().isoformat()
                self._graph_store.upsert_edge(
                    source_id=source,
                    target_id=target,
                    edge_type=EdgeType.CO_OCCURS_WITH.value,
                    properties=props,
                )
            else:
                # Create new co-occurrence edge
                self._graph_store.upsert_edge(
                    source_id=source,
                    target_id=target,
                    edge_type=EdgeType.CO_OCCURS_WITH.value,
                    properties={
                        "weight": 1,
                        "first_seen": utc_now().isoformat(),
                        "last_seen": utc_now().isoformat(),
                    },
                )

            count += 1

        return count

    @staticmethod
    def _summarize_dict(data: dict[str, Any], max_len: int = 200) -> str:
        """Create a short string summary of a dict."""
        parts = []
        for key, value in data.items():
            val_str = str(value)
            if len(val_str) > 50:
                val_str = val_str[:47] + "..."
            parts.append(f"{key}={val_str}")

        result = ", ".join(parts)
        if len(result) > max_len:
            result = result[: max_len - 3] + "..."
        return result
