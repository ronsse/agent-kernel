"""Integration tests for workflow execution."""

from pathlib import Path

import pytest

from agent_kernel.context.assembler import ContextAssembler
from agent_kernel.core.schemas import CapabilityDef, SideEffect
from agent_kernel.engine.custom_engine import CustomEngine
from agent_kernel.executor.executor import DeterministicExecutor
from agent_kernel.memory.document_store import SQLiteDocumentStore
from agent_kernel.memory.event_log import SQLiteEventLog
from agent_kernel.memory.graph_store import SQLiteGraphStore
from agent_kernel.memory.vector_store import SQLiteVectorStore
from agent_kernel.tools.broker import ToolBroker
from agent_kernel.tools.registry import CapabilityRegistry
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink
from agent_kernel.workflows.runner import WorkflowRunner


@pytest.fixture
def full_system(temp_dir: Path):
    """Set up a complete system for integration testing."""
    # Memory stores
    event_log = SQLiteEventLog(temp_dir / "events.db")
    document_store = SQLiteDocumentStore(temp_dir / "documents.db")
    vector_store = SQLiteVectorStore(temp_dir / "vectors.db")
    graph_store = SQLiteGraphStore(temp_dir / "graph.db")
    trace_store = SQLiteTraceSink(temp_dir / "traces.db")

    # Capability registry
    registry = CapabilityRegistry()
    registry.register(CapabilityDef(
        capability_name="tasks.list@v1",
        description="List tasks",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        side_effect_level=SideEffect.NONE,
    ))
    registry.register(CapabilityDef(
        capability_name="tasks.create@v1",
        description="Create task",
        input_schema={"type": "object", "required": ["title"]},
        output_schema={"type": "object"},
        side_effect_level=SideEffect.LOCAL_WRITE,
    ))
    registry.register(CapabilityDef(
        capability_name="notes.search@v1",
        description="Search notes",
        input_schema={"type": "object", "required": ["query"]},
        output_schema={"type": "object"},
        side_effect_level=SideEffect.NONE,
    ))

    # Tool broker with implementations
    broker = ToolBroker(registry, event_log)
    broker.local_adapter.register(
        "tasks.list@v1",
        lambda **kwargs: {"tasks": [{"id": "t1", "title": "Test task"}], "total_count": 1},
    )
    broker.local_adapter.register(
        "tasks.create@v1",
        lambda title, **kwargs: {"task_id": "new_task_123", "title": title},
    )
    broker.local_adapter.register(
        "notes.search@v1",
        lambda query, **kwargs: {"results": [], "total_count": 0},
    )

    # Context assembler
    assembler = ContextAssembler(
        document_store=document_store,
        vector_store=vector_store,
        graph_store=graph_store,
    )

    # Executor
    executor = DeterministicExecutor(
        tool_broker=broker,
        trace_store=trace_store,
        event_log=event_log,
    )

    # Workflow runner
    runner = WorkflowRunner(
        context_assembler=assembler,
        executor=executor,
        event_log=event_log,
        configs_dir=temp_dir,
    )

    # Register engine
    engine = CustomEngine()
    runner.register_engine(engine)

    yield {
        "runner": runner,
        "trace_store": trace_store,
        "event_log": event_log,
        "document_store": document_store,
        "temp_dir": temp_dir,
    }

    # Cleanup
    event_log.close()
    document_store.close()
    vector_store.close()
    graph_store.close()
    trace_store.close()


class TestWorkflowIntegration:
    """Integration tests for workflow execution."""

    def setup_workflow_files(self, temp_dir: Path):
        """Set up workflow and agent profile YAML files."""
        # Create directories
        (temp_dir / "workflows").mkdir(exist_ok=True)
        (temp_dir / "agents").mkdir(exist_ok=True)
        (temp_dir / "capabilities").mkdir(exist_ok=True)

        # Agent profile
        agent_yaml = """
agent_profile_id: test_agent
name: Test Agent
description: Agent for integration testing
engine: custom
llm_config:
  provider: openai
  model: gpt-4o
  temperature: 0.3
allowed_capabilities:
  - tasks.list@v1
  - tasks.create@v1
  - notes.search@v1
context_policy:
  max_tokens: 4000
  must_cite: false
approval_policy:
  auto_approve_side_effects:
    - none
    - local
"""
        (temp_dir / "agents" / "test_agent.yaml").write_text(agent_yaml)

        # Workflow
        workflow_yaml = """
workflow_id: test_workflow
name: Test Workflow
description: A workflow for integration testing
trigger:
  type: manual
agent_profile_id: test_agent
steps:
  - assemble_context
  - propose_plan
  - validate
  - execute
  - emit_trace
on_error: halt
"""
        (temp_dir / "workflows" / "test_workflow.yaml").write_text(workflow_yaml)

    @pytest.mark.asyncio
    async def test_run_workflow_end_to_end(self, full_system):
        """Test running a complete workflow."""
        runner = full_system["runner"]
        temp_dir = full_system["temp_dir"]
        trace_store = full_system["trace_store"]

        self.setup_workflow_files(temp_dir)

        # Run the workflow
        result = await runner.run(
            workflow_id="test_workflow",
            intent="List my tasks for today",
        )

        assert result.success is True
        assert result.trace is not None
        assert result.error is None

        # Verify trace was stored
        stored_trace = trace_store.get(result.trace.trace_id)
        assert stored_trace is not None
        assert stored_trace.agent_profile_id == "test_agent"

    @pytest.mark.asyncio
    async def test_workflow_not_found(self, full_system):
        """Test running non-existent workflow."""
        runner = full_system["runner"]
        temp_dir = full_system["temp_dir"]

        self.setup_workflow_files(temp_dir)

        result = await runner.run(
            workflow_id="nonexistent_workflow",
        )

        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_workflow_events_logged(self, full_system):
        """Test that workflow events are logged."""
        runner = full_system["runner"]
        temp_dir = full_system["temp_dir"]
        event_log = full_system["event_log"]

        self.setup_workflow_files(temp_dir)

        # Get initial count
        from agent_kernel.memory.event_log import EventType
        initial_count = event_log.count(event_type=EventType.WORKFLOW_STARTED)

        await runner.run(
            workflow_id="test_workflow",
            intent="Test event logging",
        )

        # Should have logged workflow started event
        final_count = event_log.count(event_type=EventType.WORKFLOW_STARTED)
        assert final_count > initial_count
