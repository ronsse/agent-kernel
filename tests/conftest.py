"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from agent_kernel.core.schemas import (
    ActionRequest,
    AgentProfile,
    ApprovalPolicy,
    ContextBudget,
    ContextItem,
    ContextPacket,
    ContextPolicy,
    ContextRef,
    ModelConfig,
    Plan,
    PlanValidation,
    RefType,
    RetrievalReport,
    RiskAssessment,
    RiskLevel,
    SideEffect,
)
from agent_kernel.memory.document_store import SQLiteDocumentStore
from agent_kernel.memory.event_log import SQLiteEventLog
from agent_kernel.memory.graph_store import SQLiteGraphStore
from agent_kernel.memory.vector_store import (
    LANCEDB_AVAILABLE,
    LanceDBVectorStore,
    SQLiteVectorStore,
    create_vector_store,
)
from agent_kernel.tools.registry import CapabilityRegistry
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def trace_store(temp_dir: Path) -> Generator[SQLiteTraceSink, None, None]:
    """Create a temporary trace store."""
    store = SQLiteTraceSink(temp_dir / "traces.db")
    yield store
    store.close()


@pytest.fixture
def event_log(temp_dir: Path) -> Generator[SQLiteEventLog, None, None]:
    """Create a temporary event log."""
    log = SQLiteEventLog(temp_dir / "events.db")
    yield log
    log.close()


@pytest.fixture
def document_store(temp_dir: Path) -> Generator[SQLiteDocumentStore, None, None]:
    """Create a temporary document store."""
    store = SQLiteDocumentStore(temp_dir / "documents.db")
    yield store
    store.close()


@pytest.fixture
def vector_store(temp_dir: Path) -> Generator[SQLiteVectorStore, None, None]:
    """Create a temporary SQLite vector store."""
    store = SQLiteVectorStore(temp_dir / "vectors.db")
    yield store
    store.close()


@pytest.fixture
def lancedb_vector_store(temp_dir: Path) -> Generator[LanceDBVectorStore | None, None, None]:
    """Create a temporary LanceDB vector store (skips if LanceDB not installed)."""
    if not LANCEDB_AVAILABLE:
        yield None
        return

    store = LanceDBVectorStore(temp_dir / "vectors.lance")
    yield store
    store.close()


@pytest.fixture
def factory_vector_store(temp_dir: Path) -> Generator[SQLiteVectorStore | LanceDBVectorStore, None, None]:
    """Create a vector store using the factory function."""
    store = create_vector_store(temp_dir / "vectors", prefer_lancedb=True)
    yield store
    store.close()


@pytest.fixture
def graph_store(temp_dir: Path) -> Generator[SQLiteGraphStore, None, None]:
    """Create a temporary graph store."""
    store = SQLiteGraphStore(temp_dir / "graph.db")
    yield store
    store.close()


@pytest.fixture
def capability_registry() -> CapabilityRegistry:
    """Create a capability registry with test capabilities."""
    registry = CapabilityRegistry()
    return registry


@pytest.fixture
def sample_context_ref() -> ContextRef:
    """Create a sample context reference."""
    return ContextRef(
        ref_type=RefType.NOTE,
        ref_id="note_123",
        uri="obsidian://vault/notes/test.md",
        hash="abc123",
        metadata={"title": "Test Note", "tags": ["test"]},
    )


@pytest.fixture
def sample_context_item(sample_context_ref: ContextRef) -> ContextItem:
    """Create a sample context item."""
    return ContextItem(
        ref=sample_context_ref,
        excerpt="This is a test note with some content.",
        summary="A test note",
        relevance_score=0.85,
        included_reason="keyword_match",
    )


@pytest.fixture
def sample_context_packet(sample_context_item: ContextItem) -> ContextPacket:
    """Create a sample context packet."""
    return ContextPacket(
        intent="Review my tasks for today",
        project_id="project_123",
        budget=ContextBudget(max_tokens=4000, max_items=20),
        items=[sample_context_item],
        retrieval_report=RetrievalReport(
            items_considered=10,
            items_selected=1,
        ),
    )


@pytest.fixture
def sample_action_request() -> ActionRequest:
    """Create a sample action request."""
    return ActionRequest(
        capability_name="tasks.list@v1",
        args={"status": "open", "limit": 10},
        side_effect=SideEffect.NONE,
        requires_approval=False,
    )


@pytest.fixture
def sample_plan(
    sample_context_ref: ContextRef,
    sample_action_request: ActionRequest,
) -> Plan:
    """Create a sample plan."""
    return Plan(
        intent="Review my tasks for today",
        summary="List open tasks and summarize priorities.",
        context_refs_used=[sample_context_ref],
        actions=[sample_action_request],
        risk=RiskAssessment(level=RiskLevel.LOW, reasons=[]),
        validation=PlanValidation(),
    )


@pytest.fixture
def sample_agent_profile() -> AgentProfile:
    """Create a sample agent profile."""
    return AgentProfile(
        agent_profile_id="test_agent",
        name="Test Agent",
        description="An agent for testing",
        engine="custom",
        llm_config=ModelConfig(
            provider="openai",
            model="gpt-4o",
            temperature=0.3,
        ),
        allowed_capabilities=[
            "tasks.list@v1",
            "tasks.create@v1",
            "notes.search@v1",
        ],
        context_policy=ContextPolicy(
            max_tokens=4000,
            max_notes=10,
            must_cite=True,
        ),
        approval_policy=ApprovalPolicy(
            auto_approve_side_effects=[SideEffect.NONE],
            max_auto_approve_risk=RiskLevel.LOW,
        ),
    )
