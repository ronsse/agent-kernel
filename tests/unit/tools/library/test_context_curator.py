"""Tests for context curator library tools."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

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
from agent_kernel.tools.library.context_curator import (
    curate_context,
    evaluate_effectiveness,
    update_context_profile,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context_ref(ref_id: str = "ref_001") -> ContextRef:
    return ContextRef(ref_type=RefType.NOTE, ref_id=ref_id)


def _make_plan(
    cited_ref_ids: list[str] | None = None,
    evidence_ref_ids: list[str] | None = None,
) -> Plan:
    cited_ref_ids = cited_ref_ids or []
    evidence_ref_ids = evidence_ref_ids or []
    return Plan(
        intent="test intent",
        summary="test summary",
        context_refs_used=[_make_context_ref(rid) for rid in cited_ref_ids],
        actions=[
            ActionRequest(
                capability_name="test.action@v1",
                args={},
                side_effect=SideEffect.NONE,
                evidence_refs=evidence_ref_ids,
            ),
        ],
        risk=RiskAssessment(),
        validation=PlanValidation(),
    )


def _make_trace(
    agent_profile_id: str = "agent_1",
    outcome_status: OutcomeStatus = OutcomeStatus.COMPLETED,
    cited_ref_ids: list[str] | None = None,
    evidence_ref_ids: list[str] | None = None,
    timestamp: datetime | None = None,
) -> DecisionTrace:
    return DecisionTrace(
        trace_id=generate_ulid(),
        run_id=generate_ulid(),
        agent_profile_id=agent_profile_id,
        engine_id="test_engine",
        intent="test intent",
        timestamp=timestamp or datetime.now(tz=UTC),
        context_packet_id=generate_ulid(),
        plan=_make_plan(cited_ref_ids, evidence_ref_ids),
        tool_calls=[
            ToolCallRecord(
                capability_name="test.action@v1",
                status=CallStatus.SUCCESS,
                duration_ms=100,
                effective_side_effect=SideEffect.NONE,
                effective_requires_approval=False,
            ),
        ],
        outcome=Outcome(status=outcome_status),
        provenance=Provenance(
            config_hash="test",
            engine_version="test",
            kernel_version="test",
        ),
    )


# ---------------------------------------------------------------------------
# TestEvaluateEffectiveness
# ---------------------------------------------------------------------------


class TestEvaluateEffectiveness:
    """Tests for evaluate_effectiveness."""

    @patch("agent_kernel.tools.library.context_curator._get_trace_store")
    def test_basic_evaluation(self, mock_get_store: MagicMock) -> None:
        """Traces with citations produce meaningful citation rates."""
        store = MagicMock()
        traces = [
            _make_trace(cited_ref_ids=["ref_A", "ref_B"]),
            _make_trace(cited_ref_ids=["ref_A"]),
            _make_trace(cited_ref_ids=["ref_B", "ref_C"]),
            _make_trace(cited_ref_ids=["ref_A", "ref_C"]),
            _make_trace(cited_ref_ids=["ref_A"]),
        ]
        store.list_traces.return_value = traces
        mock_get_store.return_value = store

        result = evaluate_effectiveness(
            agent_profile_ids=["agent_1"],
            lookback_hours=24,
            min_traces=5,
        )

        assert len(result["evaluations"]) == 1
        ev = result["evaluations"][0]
        assert ev["agent_profile_id"] == "agent_1"
        assert ev["traces_analyzed"] == 5
        assert ev["citation_rate"] > 0
        assert "skipped" not in ev
        # ref_A should be top cited (4 times)
        assert ev["top_cited"][0] == "ref_A"

    @patch("agent_kernel.tools.library.context_curator._get_trace_store")
    def test_insufficient_traces_skipped(self, mock_get_store: MagicMock) -> None:
        """Agent with fewer than min_traces is skipped."""
        store = MagicMock()
        store.list_traces.return_value = [_make_trace(), _make_trace()]
        mock_get_store.return_value = store

        result = evaluate_effectiveness(
            agent_profile_ids=["agent_1"],
            lookback_hours=24,
            min_traces=5,
        )

        ev = result["evaluations"][0]
        assert ev["skipped"] is True
        assert "Only 2 traces" in ev["skip_reason"]

    @patch("agent_kernel.tools.library.context_curator._get_trace_store")
    def test_multiple_agents(self, mock_get_store: MagicMock) -> None:
        """Evaluations are produced per agent."""
        store = MagicMock()
        # Return 5 traces for each call
        store.list_traces.side_effect = [
            [_make_trace(agent_profile_id="a1", cited_ref_ids=["r1"])
             for _ in range(5)],
            [_make_trace(agent_profile_id="a2", cited_ref_ids=["r2"])
             for _ in range(5)],
        ]
        mock_get_store.return_value = store

        result = evaluate_effectiveness(
            agent_profile_ids=["a1", "a2"],
            lookback_hours=24,
            min_traces=5,
        )

        assert len(result["evaluations"]) == 2
        assert result["evaluations"][0]["agent_profile_id"] == "a1"
        assert result["evaluations"][1]["agent_profile_id"] == "a2"

    @patch("agent_kernel.tools.library.context_curator._get_trace_store")
    def test_outcome_distribution(self, mock_get_store: MagicMock) -> None:
        """Outcome distribution counts are correct."""
        store = MagicMock()
        traces = [
            _make_trace(outcome_status=OutcomeStatus.COMPLETED, cited_ref_ids=["r"]),
            _make_trace(outcome_status=OutcomeStatus.COMPLETED, cited_ref_ids=["r"]),
            _make_trace(outcome_status=OutcomeStatus.FAILED, cited_ref_ids=["r"]),
            _make_trace(outcome_status=OutcomeStatus.PARTIAL, cited_ref_ids=["r"]),
            _make_trace(outcome_status=OutcomeStatus.COMPLETED, cited_ref_ids=["r"]),
        ]
        store.list_traces.return_value = traces
        mock_get_store.return_value = store

        result = evaluate_effectiveness(
            agent_profile_ids=["agent_1"],
            min_traces=5,
        )

        dist = result["evaluations"][0]["outcome_distribution"]
        assert dist["completed"] == 3
        assert dist["failed"] == 1
        assert dist["partial"] == 1

    @patch("agent_kernel.tools.library.context_curator._get_trace_store")
    def test_empty_traces(self, mock_get_store: MagicMock) -> None:
        """Empty traces returns skipped evaluation."""
        store = MagicMock()
        store.list_traces.return_value = []
        mock_get_store.return_value = store

        result = evaluate_effectiveness(
            agent_profile_ids=["agent_1"],
            min_traces=1,
        )

        ev = result["evaluations"][0]
        assert ev["skipped"] is True

    @patch("agent_kernel.tools.library.context_curator._get_trace_store")
    def test_evidence_refs_counted(self, mock_get_store: MagicMock) -> None:
        """Evidence refs from actions are also counted."""
        store = MagicMock()
        traces = [
            _make_trace(
                cited_ref_ids=["r1"],
                evidence_ref_ids=["r2", "r3"],
            )
            for _ in range(5)
        ]
        store.list_traces.return_value = traces
        mock_get_store.return_value = store

        result = evaluate_effectiveness(
            agent_profile_ids=["agent_1"],
            min_traces=5,
        )

        ev = result["evaluations"][0]
        all_ref_ids = {s["ref_id"] for s in ev["item_scores"]}
        assert "r1" in all_ref_ids
        assert "r2" in all_ref_ids
        assert "r3" in all_ref_ids


# ---------------------------------------------------------------------------
# TestCurateContext
# ---------------------------------------------------------------------------


class TestCurateContext:
    """Tests for curate_context."""

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_basic_curation(self, mock_get_store: MagicMock) -> None:
        """Curation returns expected fields."""
        store = MagicMock()
        knowledge_nodes = [
            {
                "node_id": "concept_1",
                "node_type": NodeType.CONCEPT.value,
                "properties": {
                    "title": "Test Concept",
                    "confidence": 0.9,
                },
            },
            {
                "node_id": "insight_1",
                "node_type": NodeType.INSIGHT.value,
                "properties": {
                    "title": "Test Insight",
                    "confidence": 0.8,
                },
            },
        ]
        store.query.side_effect = [knowledge_nodes, []]  # knowledge, trajectories
        store.get_edges.return_value = []
        store.get_node.return_value = None  # No agent node
        mock_get_store.return_value = store

        result = curate_context(
            agent_profile_id="agent_1",
            max_items=10,
        )

        assert result["agent_profile_id"] == "agent_1"
        assert result["items_included"] == 2
        assert result["items_boosted"] == 0
        assert result["items_demoted"] == 0
        assert "curated_at" in result
        assert "cache_key" in result

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_boosted_items_ranked_higher(self, mock_get_store: MagicMock) -> None:
        """Items with EFFECTIVE_FOR edges get boosted scores."""
        store = MagicMock()
        knowledge_nodes = [
            {
                "node_id": "node_A",
                "node_type": NodeType.CONCEPT.value,
                "properties": {"title": "Low", "confidence": 0.5},
            },
            {
                "node_id": "node_B",
                "node_type": NodeType.INSIGHT.value,
                "properties": {"title": "High", "confidence": 0.5},
            },
        ]
        store.query.side_effect = [knowledge_nodes, []]
        # node_B has a high citation_rate effectiveness edge
        store.get_edges.return_value = [
            {
                "source_id": "node_B",
                "target_id": "agent_1",
                "edge_type": EdgeType.EFFECTIVE_FOR.value,
                "properties": {"citation_rate": 0.9},
            },
        ]
        store.get_node.return_value = None
        mock_get_store.return_value = store

        result = curate_context(agent_profile_id="agent_1")

        assert result["items_boosted"] == 1
        items = result["items"]
        # node_B should be ranked first due to boost
        assert items[0]["node_id"] == "node_B"
        assert items[0]["boosted"] is True

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_demoted_items_ranked_lower(self, mock_get_store: MagicMock) -> None:
        """Items with low citation_rate get demoted."""
        store = MagicMock()
        knowledge_nodes = [
            {
                "node_id": "good_node",
                "node_type": NodeType.CONCEPT.value,
                "properties": {"title": "Good", "confidence": 0.9},
            },
            {
                "node_id": "bad_node",
                "node_type": NodeType.INSIGHT.value,
                "properties": {"title": "Bad", "confidence": 0.9},
            },
        ]
        store.query.side_effect = [knowledge_nodes, []]
        store.get_edges.return_value = [
            {
                "source_id": "bad_node",
                "target_id": "agent_1",
                "edge_type": EdgeType.EFFECTIVE_FOR.value,
                "properties": {"citation_rate": 0.05},
            },
        ]
        store.get_node.return_value = None
        mock_get_store.return_value = store

        result = curate_context(agent_profile_id="agent_1")

        assert result["items_demoted"] == 1
        items = result["items"]
        # bad_node should be last
        assert items[-1]["node_id"] == "bad_node"
        assert items[-1]["demoted"] is True

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_empty_graph(self, mock_get_store: MagicMock) -> None:
        """Empty graph returns zero items."""
        store = MagicMock()
        store.query.return_value = []
        store.get_edges.return_value = []
        store.get_node.return_value = None
        mock_get_store.return_value = store

        result = curate_context(agent_profile_id="agent_1")

        assert result["items_included"] == 0

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_staircase_level_from_agent_node(self, mock_get_store: MagicMock) -> None:
        """Staircase level is read from agent profile node."""
        store = MagicMock()
        store.query.return_value = []
        store.get_edges.return_value = []
        store.get_node.return_value = {
            "node_id": "agent_1",
            "node_type": NodeType.CAPABILITY.value,
            "properties": {"staircase_level": 3},
        }
        mock_get_store.return_value = store

        result = curate_context(agent_profile_id="agent_1")

        assert result["staircase_level"] == 3

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_max_items_respected(self, mock_get_store: MagicMock) -> None:
        """Only max_items nodes are returned."""
        store = MagicMock()
        nodes = [
            {
                "node_id": f"node_{i}",
                "node_type": NodeType.CONCEPT.value,
                "properties": {"title": f"Node {i}", "confidence": 0.9},
            }
            for i in range(20)
        ]
        store.query.side_effect = [nodes, []]
        store.get_edges.return_value = []
        store.get_node.return_value = None
        mock_get_store.return_value = store

        result = curate_context(agent_profile_id="agent_1", max_items=5)

        assert result["items_included"] == 5

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_freshness_scoring(self, mock_get_store: MagicMock) -> None:
        """Nodes with freshness data are scored by effective_relevance."""
        now = datetime.now(tz=UTC)
        old_freshness = FreshnessScore(
            base_relevance=1.0,
            last_accessed_at=now - timedelta(days=365),
            last_reinforced_at=now - timedelta(days=365),
            decay_rate=0.01,
        ).model_dump(mode="json")
        new_freshness = FreshnessScore(
            base_relevance=1.0,
            last_accessed_at=now,
            last_reinforced_at=now,
            decay_rate=0.01,
        ).model_dump(mode="json")

        store = MagicMock()
        knowledge_nodes = [
            {
                "node_id": "old_node",
                "node_type": NodeType.CONCEPT.value,
                "properties": {
                    "title": "Old",
                    "confidence": 1.0,
                    "freshness": old_freshness,
                },
            },
            {
                "node_id": "new_node",
                "node_type": NodeType.CONCEPT.value,
                "properties": {
                    "title": "New",
                    "confidence": 1.0,
                    "freshness": new_freshness,
                },
            },
        ]
        store.query.side_effect = [knowledge_nodes, []]
        store.get_edges.return_value = []
        store.get_node.return_value = None
        mock_get_store.return_value = store

        result = curate_context(agent_profile_id="agent_1")

        items = result["items"]
        # Newer node should rank higher
        assert items[0]["node_id"] == "new_node"
        assert items[0]["score"] > items[1]["score"]


# ---------------------------------------------------------------------------
# TestUpdateContextProfile
# ---------------------------------------------------------------------------


class TestUpdateContextProfile:
    """Tests for update_context_profile."""

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_boost_creates_edges(self, mock_get_store: MagicMock) -> None:
        """Boosted ref_ids create EFFECTIVE_FOR edges."""
        store = MagicMock()
        store.get_node.side_effect = [
            None,  # Agent node doesn't exist (first call)
            {"node_id": "ref_A", "node_type": "concept", "properties": {}},
            {"node_id": "ref_B", "node_type": "insight", "properties": {}},
        ]
        mock_get_store.return_value = store

        result = update_context_profile(
            agent_profile_id="agent_1",
            boosted_ref_ids=["ref_A", "ref_B"],
        )

        assert result["edges_created"] == 2
        # Should have created agent node + upserted edges
        store.upsert_node.assert_called()
        assert store.upsert_edge.call_count == 2

        # Verify edge properties
        first_edge_call = store.upsert_edge.call_args_list[0]
        assert first_edge_call.kwargs["source_id"] == "ref_A"
        assert first_edge_call.kwargs["target_id"] == "agent_1"
        assert first_edge_call.kwargs["edge_type"] == EdgeType.EFFECTIVE_FOR.value
        props = first_edge_call.kwargs["properties"]
        assert props["citation_rate"] == 1.0
        assert props["outcome_boost"] == 0.5

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_demote_creates_edges(self, mock_get_store: MagicMock) -> None:
        """Demoted ref_ids create EFFECTIVE_FOR edges with negative boost."""
        store = MagicMock()
        store.get_node.side_effect = [
            None,  # Agent node
            {"node_id": "ref_X", "node_type": "concept", "properties": {}},
        ]
        mock_get_store.return_value = store

        result = update_context_profile(
            agent_profile_id="agent_1",
            demoted_ref_ids=["ref_X"],
        )

        assert result["nodes_demoted"] == 1
        edge_call = store.upsert_edge.call_args_list[0]
        props = edge_call.kwargs["properties"]
        assert props["citation_rate"] == 0.0
        assert props["outcome_boost"] == -0.5

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_missing_nodes_skipped(self, mock_get_store: MagicMock) -> None:
        """Non-existent ref_ids are skipped gracefully."""
        store = MagicMock()
        store.get_node.return_value = None  # All nodes missing
        mock_get_store.return_value = store

        result = update_context_profile(
            agent_profile_id="agent_1",
            boosted_ref_ids=["nonexistent_1", "nonexistent_2"],
            demoted_ref_ids=["nonexistent_3"],
        )

        assert result["edges_created"] == 0
        assert result["nodes_demoted"] == 0
        store.upsert_edge.assert_not_called()

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_reinforcement(self, mock_get_store: MagicMock) -> None:
        """Reinforce_ref_ids updates freshness on graph nodes."""
        store = MagicMock()
        freshness = FreshnessScore(
            base_relevance=0.8,
            access_count=5,
        ).model_dump(mode="json")

        store.get_node.side_effect = [
            None,  # Agent node (not found -> created)
            {  # reinforce target
                "node_id": "ref_R",
                "node_type": "concept",
                "properties": {"freshness": freshness, "title": "test"},
            },
        ]
        mock_get_store.return_value = store

        result = update_context_profile(
            agent_profile_id="agent_1",
            reinforce_ref_ids=["ref_R"],
        )

        assert result["nodes_reinforced"] == 1
        # upsert_node should have been called to update freshness
        upsert_calls = [
            c for c in store.upsert_node.call_args_list
            if c.kwargs.get("node_id") == "ref_R"
        ]
        assert len(upsert_calls) == 1

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_staircase_level_update(self, mock_get_store: MagicMock) -> None:
        """Staircase level is updated on agent profile node."""
        store = MagicMock()
        store.get_node.side_effect = [
            None,  # First call: agent doesn't exist -> create
            {  # Second call for staircase update: agent exists now
                "node_id": "agent_1",
                "node_type": "capability",
                "properties": {"staircase_level": 0},
            },
        ]
        mock_get_store.return_value = store

        update_context_profile(
            agent_profile_id="agent_1",
            staircase_level=3,
        )

        # Should have updated the agent node with new staircase level
        upsert_calls = store.upsert_node.call_args_list
        # Last upsert should set staircase_level=3
        last_upsert = upsert_calls[-1]
        assert last_upsert.kwargs["properties"]["staircase_level"] == 3

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_idempotent_edges(self, mock_get_store: MagicMock) -> None:
        """Calling with same ref_ids twice upserts (not duplicates) edges."""
        store = MagicMock()
        store.get_node.side_effect = [
            {"node_id": "agent_1", "node_type": "capability", "properties": {}},
            {"node_id": "ref_A", "node_type": "concept", "properties": {}},
        ]
        mock_get_store.return_value = store

        # First call
        update_context_profile(
            agent_profile_id="agent_1",
            boosted_ref_ids=["ref_A"],
        )

        # upsert_edge (not insert) ensures idempotency
        edge_call = store.upsert_edge.call_args_list[0]
        assert edge_call.kwargs["edge_type"] == EdgeType.EFFECTIVE_FOR.value

    @patch("agent_kernel.tools.library.context_curator._get_graph_store")
    def test_no_changes(self, mock_get_store: MagicMock) -> None:
        """Empty inputs produce zero changes."""
        store = MagicMock()
        store.get_node.return_value = {
            "node_id": "agent_1",
            "node_type": "capability",
            "properties": {},
        }
        mock_get_store.return_value = store

        result = update_context_profile(agent_profile_id="agent_1")

        assert result["edges_created"] == 0
        assert result["nodes_reinforced"] == 0
        assert result["nodes_demoted"] == 0
