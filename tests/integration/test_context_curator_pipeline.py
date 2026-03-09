"""Integration tests for the context curator pipeline.

Tests the full flow: ingest traces → evaluate → curate → update profile,
using real SQLite stores (no mocks).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.context import ContextRef, RefType
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.core.schemas.knowledge import FreshnessScore
from agent_kernel.core.schemas.plan import (
    ActionRequest,
    Plan,
    PlanValidation,
    RiskAssessment,
    SideEffect,
)
from agent_kernel.core.schemas.trace import (
    CallStatus,
    DecisionTrace,
    Outcome,
    OutcomeStatus,
    Provenance,
    ToolCallRecord,
)
from agent_kernel.memory.graph_store import SQLiteGraphStore
from agent_kernel.tools.library.context_curator import (
    curate_context,
    evaluate_effectiveness,
    update_context_profile,
)
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline_env(temp_dir: Path):
    """Set up trace store and graph store for pipeline tests."""
    trace_store = SQLiteTraceSink(temp_dir / "traces.db")
    graph_store = SQLiteGraphStore(temp_dir / "graph.db")
    yield {"trace_store": trace_store, "graph_store": graph_store}
    trace_store.close()
    graph_store.close()


def _make_trace(
    agent_id: str,
    cited_ref_ids: list[str],
    outcome: OutcomeStatus = OutcomeStatus.COMPLETED,
    evidence_refs: list[str] | None = None,
    timestamp: datetime | None = None,
) -> DecisionTrace:
    return DecisionTrace(
        trace_id=generate_ulid(),
        run_id=generate_ulid(),
        agent_profile_id=agent_id,
        engine_id="test_engine",
        intent="daily review",
        timestamp=timestamp or datetime.now(tz=UTC),
        context_packet_id=generate_ulid(),
        plan=Plan(
            intent="daily review",
            summary="Review tasks",
            context_refs_used=[
                ContextRef(ref_type=RefType.NOTE, ref_id=rid)
                for rid in cited_ref_ids
            ],
            actions=[
                ActionRequest(
                    capability_name="test.action@v1",
                    args={},
                    side_effect=SideEffect.NONE,
                    evidence_refs=evidence_refs or [],
                ),
            ],
            risk=RiskAssessment(),
            validation=PlanValidation(),
        ),
        tool_calls=[
            ToolCallRecord(
                capability_name="test.action@v1",
                status=CallStatus.SUCCESS,
                duration_ms=50,
                effective_side_effect=SideEffect.NONE,
                effective_requires_approval=False,
            ),
        ],
        outcome=Outcome(status=outcome),
        provenance=Provenance(
            config_hash="test",
            engine_version="test",
            kernel_version="test",
        ),
    )


# ---------------------------------------------------------------------------
# Pipeline Tests
# ---------------------------------------------------------------------------


class TestContextCuratorPipeline:
    """Integration tests for the full curator pipeline."""

    def test_full_pipeline(self, pipeline_env: dict) -> None:
        """Full pipeline: ingest → evaluate → curate → update profile."""
        trace_store = pipeline_env["trace_store"]
        graph_store = pipeline_env["graph_store"]

        agent_id = "test_curator_agent"

        # -------------------------------------------------------------------
        # Step 1: Ingest traces and knowledge nodes
        # -------------------------------------------------------------------

        # Create knowledge nodes in the graph
        now = datetime.now(tz=UTC)
        freshness = FreshnessScore(
            base_relevance=1.0,
            last_accessed_at=now,
            last_reinforced_at=now,
        ).model_dump(mode="json")

        for i in range(5):
            graph_store.upsert_node(
                node_id=f"concept_{i}",
                node_type=NodeType.CONCEPT.value,
                properties={
                    "title": f"Concept {i}",
                    "confidence": 0.9,
                    "freshness": freshness,
                },
            )

        # Write traces that cite some of these nodes
        # concept_0 and concept_1 are frequently cited, concept_4 never
        traces = [
            _make_trace(agent_id, ["concept_0", "concept_1"]),
            _make_trace(agent_id, ["concept_0", "concept_2"]),
            _make_trace(agent_id, ["concept_0", "concept_1", "concept_3"]),
            _make_trace(agent_id, ["concept_1"]),
            _make_trace(
                agent_id,
                ["concept_0"],
                outcome=OutcomeStatus.FAILED,
            ),
        ]
        for trace in traces:
            trace_store.write(trace)

        # Verify traces were stored
        assert trace_store.count(agent_profile_id=agent_id) == 5

        # -------------------------------------------------------------------
        # Step 2: Evaluate effectiveness
        # -------------------------------------------------------------------

        with patch(
            "agent_kernel.tools.library.context_curator._get_trace_store",
            return_value=trace_store,
        ):
            eval_result = evaluate_effectiveness(
                agent_profile_ids=[agent_id],
                lookback_hours=24,
                min_traces=5,
            )

        assert len(eval_result["evaluations"]) == 1
        ev = eval_result["evaluations"][0]
        assert ev["traces_analyzed"] == 5
        assert ev["citation_rate"] > 0
        assert "skipped" not in ev

        # concept_0 should be top cited (appears in 4/5 traces)
        assert ev["top_cited"][0] == "concept_0"

        # -------------------------------------------------------------------
        # Step 3: Update context profile based on evaluation
        # -------------------------------------------------------------------

        # Boost frequently cited, demote never cited
        top_cited = ev["top_cited"][:3]
        never_cited = [
            s["ref_id"]
            for s in ev["item_scores"]
            if s["citation_count"] == 0
        ]

        with patch(
            "agent_kernel.tools.library.context_curator._get_graph_store",
            return_value=graph_store,
        ):
            update_result = update_context_profile(
                agent_profile_id=agent_id,
                boosted_ref_ids=top_cited,
                demoted_ref_ids=never_cited,
                reinforce_ref_ids=top_cited,
                staircase_level=1,
            )

        assert update_result["edges_created"] == len(top_cited)
        assert update_result["nodes_reinforced"] == len(top_cited)

        # Verify edges exist in graph
        edges = graph_store.get_edges(
            agent_id,
            direction="incoming",
            edge_type=EdgeType.EFFECTIVE_FOR.value,
        )
        assert len(edges) >= len(top_cited)

        # Verify staircase level
        agent_node = graph_store.get_node(agent_id)
        assert agent_node is not None
        assert agent_node["properties"]["staircase_level"] == 1

        # -------------------------------------------------------------------
        # Step 4: Curate context using learned preferences
        # -------------------------------------------------------------------

        with patch(
            "agent_kernel.tools.library.context_curator._get_graph_store",
            return_value=graph_store,
        ):
            curate_result = curate_context(
                agent_profile_id=agent_id,
                max_items=10,
            )

        assert curate_result["items_included"] > 0
        assert curate_result["staircase_level"] == 1

        # Boosted items should be in the curated list with high scores
        curated_ids = [item["node_id"] for item in curate_result["items"]]
        for boosted_id in top_cited:
            assert boosted_id in curated_ids

    def test_evaluate_with_no_knowledge_nodes(self, pipeline_env: dict) -> None:
        """Evaluation works even when graph has no knowledge nodes."""
        trace_store = pipeline_env["trace_store"]

        agent_id = "bare_agent"
        for i in range(5):
            trace_store.write(
                _make_trace(agent_id, [f"ref_{i}"])
            )

        with patch(
            "agent_kernel.tools.library.context_curator._get_trace_store",
            return_value=trace_store,
        ):
            result = evaluate_effectiveness(
                agent_profile_ids=[agent_id],
                min_traces=5,
            )

        assert result["evaluations"][0]["traces_analyzed"] == 5

    def test_curate_empty_graph(self, pipeline_env: dict) -> None:
        """Curation on empty graph returns zero items gracefully."""
        graph_store = pipeline_env["graph_store"]

        with patch(
            "agent_kernel.tools.library.context_curator._get_graph_store",
            return_value=graph_store,
        ):
            result = curate_context(agent_profile_id="new_agent")

        assert result["items_included"] == 0

    def test_update_creates_agent_node(self, pipeline_env: dict) -> None:
        """Update profile creates agent node if it doesn't exist."""
        graph_store = pipeline_env["graph_store"]

        # Create a knowledge node to boost
        graph_store.upsert_node(
            node_id="concept_X",
            node_type=NodeType.CONCEPT.value,
            properties={"title": "X"},
        )

        with patch(
            "agent_kernel.tools.library.context_curator._get_graph_store",
            return_value=graph_store,
        ):
            update_context_profile(
                agent_profile_id="new_agent",
                boosted_ref_ids=["concept_X"],
                staircase_level=2,
            )

        agent_node = graph_store.get_node("new_agent")
        assert agent_node is not None
        assert agent_node["properties"]["staircase_level"] == 2

    def test_freshness_reinforcement_persists(self, pipeline_env: dict) -> None:
        """Reinforcement updates persist in the graph store."""
        graph_store = pipeline_env["graph_store"]

        # Create node with old freshness
        old_time = datetime.now(tz=UTC) - timedelta(days=30)
        freshness = FreshnessScore(
            base_relevance=0.8,
            last_accessed_at=old_time,
            last_reinforced_at=old_time,
            access_count=2,
        ).model_dump(mode="json")

        graph_store.upsert_node(
            node_id="stale_concept",
            node_type=NodeType.CONCEPT.value,
            properties={"title": "Stale", "freshness": freshness},
        )

        with patch(
            "agent_kernel.tools.library.context_curator._get_graph_store",
            return_value=graph_store,
        ):
            update_context_profile(
                agent_profile_id="agent_1",
                reinforce_ref_ids=["stale_concept"],
            )

        # Verify freshness was updated
        node = graph_store.get_node("stale_concept")
        assert node is not None
        updated_freshness = node["properties"]["freshness"]
        # last_reinforced_at should be more recent than old_time
        reinforced_at = datetime.fromisoformat(
            updated_freshness["last_reinforced_at"]
        )
        assert reinforced_at > old_time
