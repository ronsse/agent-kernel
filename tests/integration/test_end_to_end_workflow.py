"""Integration tests for end-to-end workflows."""

from pathlib import Path

import pytest
from agent_kernel.memory.event_log import EventType


class TestEndToEndWorkflow:
    """Integration tests for complete workflows."""

    @pytest.fixture
    def workflow_environment(self, temp_dir):
        """Set up a complete workflow environment."""
        # Create necessary directories
        data_dir = temp_dir / "data"
        data_dir.mkdir(exist_ok=True)

        (data_dir / "documents").mkdir(exist_ok=True)
        (data_dir / "vectors").mkdir(exist_ok=True)
        (data_dir / "graphs").mkdir(exist_ok=True)
        (data_dir / "traces").mkdir(exist_ok=True)

        configs_dir = temp_dir / "configs"
        (configs_dir / "workflows").mkdir(parents=True, exist_ok=True)
        (configs_dir / "agents").mkdir(parents=True, exist_ok=True)
        (configs_dir / "capabilities").mkdir(parents=True, exist_ok=True)

        return {
            "data_dir": data_dir,
            "configs_dir": configs_dir,
            "temp_dir": temp_dir,
        }

    def test_simple_workflow_execution(
        self, workflow_environment, document_store, graph_store, capability_registry
    ):
        """Test executing a simple workflow from start to finish."""
        env = workflow_environment

        # 1. Create some context data
        note_id = document_store.put(
            "test_note",
            "This is a test note about workflow testing",
            {"title": "Workflow Test", "tags": ["test", "workflow"], "item_type": "note"},
        )

        graph_store.upsert_node("test_note", "note", {"title": "Workflow Test"})

        # 2. Create a simple capability
        # In real scenario, this would be loaded from YAML
        from agent_kernel.core.schemas import CapabilityDef

        test_cap = CapabilityDef(
            capability_name="test.echo@v1",
            description="Echo back input",
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            side_effect_level="none",
            adapter_type="local_function",
        )

        # 3. Verify document was stored
        retrieved = document_store.get(note_id)
        assert retrieved is not None
        assert retrieved["content"] == "This is a test note about workflow testing"

        # 4. Verify graph node was created
        node = graph_store.get_node("test_note")
        assert node is not None
        assert node["properties"]["title"] == "Workflow Test"

    def test_context_assembly_to_planning(
        self, workflow_environment, document_store, vector_store, graph_store
    ):
        """Test the flow from context assembly to plan generation."""
        # 1. Create rich context
        # Create multiple related notes
        note_ids = []
        for i in range(5):
            note_id = document_store.put(
                f"note_{i}",
                f"Note {i} about agent systems and planning",
                {"title": f"Note {i}", "tags": ["agents", "planning"], "item_type": "note"},
            )
            note_ids.append(note_id)

            # Add to graph
            graph_store.upsert_node(
                f"note_{i}",
                "note",
                {"title": f"Note {i}", "index": i},
            )

        # Create relationships
        for i in range(len(note_ids) - 1):
            graph_store.upsert_edge(
                f"note_{i}",
                f"note_{i+1}",
                "references",
                {"strength": 0.8},
            )

        # 2. Query for relevant context
        # In real workflow, this would use semantic search
        all_notes = [document_store.get(nid) for nid in note_ids]

        assert len(all_notes) == 5
        assert all(note is not None for note in all_notes)

        # 3. Assemble context packet
        # Simulate selecting top 3 most relevant
        selected = all_notes[:3]

        # 4. Verify context is ready for planning
        assert len(selected) == 3
        for note in selected:
            assert "agents" in note["metadata"]["tags"]

    def test_planning_to_execution(self, workflow_environment, event_log):
        """Test the flow from plan generation to execution."""
        from agent_kernel.core.schemas import (
            ActionRequest,
            Plan,
            RiskAssessment,
            RiskLevel,
            SideEffect,
        )

        # 1. Create a plan (simulating what an agent would generate)
        plan = Plan(
            intent="Test task execution",
            summary="Execute a test task to verify the system works",
            context_refs_used=[],
            actions=[
                ActionRequest(
                    capability_name="test.task@v1",
                    args={"action": "verify_system"},
                    side_effect=SideEffect.NONE,
                    requires_approval=False,
                )
            ],
            risk=RiskAssessment(level=RiskLevel.LOW, reasons=[]),
        )

        # 2. Validate plan structure
        assert plan.intent == "Test task execution"
        assert len(plan.actions) == 1
        assert plan.risk.level == RiskLevel.LOW

        # 3. Check if action requires approval
        action = plan.actions[0]
        assert action.requires_approval is False  # Can auto-execute

        # 4. Simulate execution logging
        from agent_kernel.core.ids import generate_ulid

        event = event_log.emit(
            event_type=EventType.TOOL_CALLED,
            source="test",
            payload={
                "capability": action.capability_name,
                "args": action.args,
                "result": {"status": "success"},
            },
            metadata={"plan_id": generate_ulid(), "test": True},
        )

        assert event.event_id is not None

        # 5. Verify event was logged
        recent_events = event_log.get_events(limit=1)
        assert len(recent_events) == 1
        assert recent_events[0].event_type == EventType.TOOL_CALLED

    def test_execution_to_trace_storage(self, workflow_environment, trace_store):
        """Test storing execution traces."""
        from agent_kernel.core.ids import generate_ulid
        from agent_kernel.core.schemas import (
            DecisionTrace,
            Outcome,
            OutcomeStatus,
            Plan,
            Provenance,
            RiskAssessment,
            RiskLevel,
            ToolCallRecord,
        )
        from agent_kernel.core.schemas.trace import CallStatus

        # 1. Create a complete decision trace
        plan = Plan(
            intent="Test trace storage",
            summary="Store a test trace",
            context_refs_used=[],
            actions=[],
            risk=RiskAssessment(level=RiskLevel.LOW, reasons=[]),
        )

        tool_call = ToolCallRecord(
            tool_call_id=generate_ulid(),
            capability_name="test.capability@v1",
            input={"test": "value"},
            output={"status": "success"},
            duration_ms=150,
            status=CallStatus.SUCCESS,
        )

        trace = DecisionTrace(
            trace_id=generate_ulid(),
            run_id=generate_ulid(),
            workflow_id="test_workflow",
            agent_profile_id="test_agent",
            engine_id="test_engine",
            intent="Test trace storage",
            context_packet_id=generate_ulid(),
            plan=plan,
            tool_calls=[tool_call],
            outcome=Outcome(status=OutcomeStatus.COMPLETED),
            provenance=Provenance(
                config_hash="test",
                engine_version="0.0.0",
                kernel_version="0.0.0",
            ),
        )

        # 2. Store the trace
        trace_store.write(trace)

        # 3. Retrieve and verify
        retrieved = trace_store.get(trace.trace_id)
        assert retrieved is not None
        assert retrieved.trace_id == trace.trace_id
        assert retrieved.agent_profile_id == "test_agent"
        assert len(retrieved.tool_calls) == 1

    def test_full_cycle_with_memory_update(
        self, workflow_environment, document_store, graph_store, event_log
    ):
        """Test a full cycle that updates memory stores."""
        from agent_kernel.core.ids import generate_ulid

        # 1. Initial state: Create a task
        task_id = "task_" + generate_ulid()

        graph_store.upsert_node(
            task_id,
            "task",
            {"title": "Test Task", "status": "pending", "created": "2026-01-25"},
        )

        # 2. Simulate workflow execution
        event_log.emit(
            event_type=EventType.WORKFLOW_STARTED,
            source="test",
            payload={"workflow_id": "test_workflow", "task_id": task_id},
        )

        # 3. Simulate task status update
        graph_store.upsert_node(
            task_id,
            "task",
            {"title": "Test Task", "status": "in_progress", "created": "2026-01-25"},
        )

        event_log.emit(
            event_type=EventType.TASK_UPDATED,
            source="test",
            payload={
                "task_id": task_id,
                "old_status": "pending",
                "new_status": "in_progress",
            },
        )

        # 4. Create a note about the task
        note_id = document_store.put(
            "note_task_" + task_id,
            "Working on test task",
            {"related_task": task_id, "item_type": "note"},
        )

        # 5. Link note to task in graph
        graph_store.upsert_node(note_id, "note", {})
        graph_store.upsert_edge(note_id, task_id, "about")

        # 6. Complete the task
        graph_store.upsert_node(
            task_id,
            "task",
            {"title": "Test Task", "status": "completed", "created": "2026-01-25"},
        )

        event_log.emit(
            event_type=EventType.TASK_COMPLETED,
            source="test",
            payload={"task_id": task_id},
        )

        # 7. Verify final state
        final_task = graph_store.get_node(task_id)
        assert final_task["properties"]["status"] == "completed"

        # 8. Verify note is linked
        edges = graph_store.get_edges(note_id, direction="outgoing")
        task_edges = [e for e in edges if e["target_id"] == task_id]
        assert len(task_edges) == 1

        # 9. Verify events were logged
        recent_events = event_log.get_events(limit=10)
        event_types = {e.event_type for e in recent_events}
        assert EventType.WORKFLOW_STARTED in event_types
        assert EventType.TASK_COMPLETED in event_types

    def test_multi_step_workflow(self, workflow_environment, document_store, graph_store):
        """Test a workflow with multiple sequential steps."""
        from agent_kernel.core.ids import generate_ulid

        workflow_id = "multi_step_test"
        steps_completed = []

        # Step 1: Gather information
        info_note = document_store.put(
            "info_" + generate_ulid(),
            "Gathered information for workflow",
            {"workflow_id": workflow_id, "step": 1, "item_type": "note"},
        )
        steps_completed.append("gather_info")

        # Step 2: Analyze
        analysis_note = document_store.put(
            "analysis_" + generate_ulid(),
            "Analysis results",
            {"workflow_id": workflow_id, "step": 2, "item_type": "note"},
        )
        steps_completed.append("analyze")

        # Step 3: Create action items
        task_id = "task_" + generate_ulid()
        graph_store.upsert_node(
            task_id,
            "task",
            {"title": "Action Item", "workflow_id": workflow_id, "step": 3},
        )
        steps_completed.append("create_actions")

        # Step 4: Link everything together
        graph_store.upsert_node(workflow_id, "workflow", {"name": "Multi-Step Test"})
        graph_store.upsert_node(info_note, "note", {})
        graph_store.upsert_node(analysis_note, "note", {})

        graph_store.upsert_edge(workflow_id, info_note, "created")
        graph_store.upsert_edge(workflow_id, analysis_note, "created")
        graph_store.upsert_edge(workflow_id, task_id, "created")
        graph_store.upsert_edge(info_note, analysis_note, "led_to")
        graph_store.upsert_edge(analysis_note, task_id, "led_to")

        # Verify workflow completion
        assert len(steps_completed) == 3

        # Verify graph structure
        subgraph = graph_store.get_subgraph([workflow_id], depth=1)
        created_items = {
            e["target_id"]
            for e in graph_store.get_edges(workflow_id, direction="outgoing")
            if e["edge_type"] == "created"
        }

        assert len(created_items) == 3
        assert info_note in created_items
        assert analysis_note in created_items
        assert task_id in created_items
