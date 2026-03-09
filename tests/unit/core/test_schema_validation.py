"""Tests for schema validation and pydantic models."""

import pytest
from pydantic import ValidationError


class TestSchemaValidation:
    """Tests for schema validation using Pydantic models."""

    def test_context_ref_validation(self):
        """Test ContextRef schema validation."""
        from agent_kernel.core.schemas import ContextRef, RefType

        # Valid context ref
        ref = ContextRef(
            ref_type=RefType.NOTE,
            ref_id="note_123",
            uri="obsidian://vault/test.md",
            hash="abc123",
            metadata={"title": "Test"},
        )

        assert ref.ref_type == RefType.NOTE
        assert ref.ref_id == "note_123"

        # Missing required field should fail
        with pytest.raises(ValidationError):
            ContextRef(
                ref_type=RefType.NOTE,
                # Missing ref_id
                uri="obsidian://vault/test.md",
            )

    def test_action_request_validation(self):
        """Test ActionRequest schema validation."""
        from agent_kernel.core.schemas import ActionRequest, SideEffect

        # Valid action request
        action = ActionRequest(
            capability_name="tasks.list@v1",
            args={"status": "open"},
            side_effect=SideEffect.NONE,
            requires_approval=False,
        )

        assert action.capability_name == "tasks.list@v1"
        assert action.side_effect == SideEffect.NONE

        # capability_name must include @version suffix
        with pytest.raises(ValidationError, match="@version suffix"):
            ActionRequest(
                capability_name="invalid_format",
                args={},
                side_effect=SideEffect.NONE,
            )

    def test_plan_validation(self):
        """Test Plan schema validation."""
        from agent_kernel.core.schemas import (
            ActionRequest,
            Plan,
            RiskAssessment,
            RiskLevel,
            SideEffect,
        )

        action = ActionRequest(
            capability_name="test.action@v1",
            args={},
            side_effect=SideEffect.NONE,
            requires_approval=False,
        )

        # Valid plan
        plan = Plan(
            intent="Test intent",
            summary="Test summary",
            context_refs_used=[],
            actions=[action],
            risk=RiskAssessment(level=RiskLevel.LOW, reasons=[]),
        )

        assert len(plan.actions) == 1
        assert plan.risk.level == RiskLevel.LOW

        # Plan without actions should be valid
        plan_no_actions = Plan(
            intent="Just analysis",
            summary="No actions needed",
            context_refs_used=[],
            actions=[],
            risk=RiskAssessment(level=RiskLevel.LOW, reasons=[]),
        )

        assert len(plan_no_actions.actions) == 0

    def test_context_packet_validation(self):
        """Test ContextPacket schema validation."""
        from agent_kernel.core.schemas import (
            ContextBudget,
            ContextItem,
            ContextPacket,
            ContextRef,
            RefType,
            RetrievalReport,
        )

        ref = ContextRef(
            ref_type=RefType.NOTE,
            ref_id="note_1",
            uri="test://note",
            hash="hash1",
        )

        item = ContextItem(
            ref=ref,
            excerpt="Test excerpt",
            summary="Test summary",
            relevance_score=0.9,
            included_reason="high_relevance",
        )

        # Valid context packet
        packet = ContextPacket(
            intent="Test intent",
            project_id="project_1",
            budget=ContextBudget(max_tokens=4000, max_items=20),
            items=[item],
            retrieval_report=RetrievalReport(
                items_considered=10,
                items_selected=1,
            ),
        )

        assert len(packet.items) == 1
        assert packet.budget.max_tokens == 4000

    def test_decision_trace_validation(self):
        """Test DecisionTrace schema validation."""
        from agent_kernel.core.schemas import (
            CallStatus,
            DecisionTrace,
            Plan,
            Provenance,
            RiskAssessment,
            RiskLevel,
            ToolCallRecord,
        )
        from agent_kernel.core.ids import generate_ulid

        plan = Plan(
            intent="Test",
            summary="Test",
            context_refs_used=[],
            actions=[],
            risk=RiskAssessment(level=RiskLevel.LOW, reasons=[]),
        )

        tool_call = ToolCallRecord(
            tool_call_id=generate_ulid(),
            capability_name="test@v1",
            input={},
            output={"status": "success"},
            duration_ms=100,
            status=CallStatus.SUCCESS,
        )

        # Valid trace
        trace = DecisionTrace(
            trace_id=generate_ulid(),
            run_id=generate_ulid(),
            workflow_id="test_workflow",
            agent_profile_id="test_agent",
            engine_id="test_engine",
            intent="Test intent",
            context_packet_id=generate_ulid(),
            plan=plan,
            tool_calls=[tool_call],
            outcome={"status": "completed"},
            provenance=Provenance(
                config_hash="config_hash",
                engine_version="engine_v1",
                kernel_version="kernel_v1",
            ),
        )

        assert len(trace.tool_calls) == 1
        assert trace.outcome.status.value == "completed"

    def test_agent_profile_validation(self):
        """Test AgentProfile schema validation."""
        from agent_kernel.core.schemas import (
            AgentProfile,
            ApprovalPolicy,
            ContextPolicy,
            ModelConfig,
            RiskLevel,
            SideEffect,
        )

        # Valid agent profile
        profile = AgentProfile(
            agent_profile_id="test_agent",
            name="Test Agent",
            description="A test agent",
            engine="custom",
            llm_config=ModelConfig(
                provider="openai",
                model="gpt-4",
                temperature=0.7,
            ),
            allowed_capabilities=["tasks.list@v1", "notes.search@v1"],
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

        assert profile.name == "Test Agent"
        assert len(profile.allowed_capabilities) == 2
        assert profile.context_policy.must_cite is True

    def test_capability_spec_validation(self):
        """Test CapabilityDef schema validation."""
        from agent_kernel.core.schemas import CapabilityDef, SideEffect

        # Valid capability
        cap = CapabilityDef(
            capability_name="test.action@v1",
            description="Test action",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            output_schema={"type": "object"},
            side_effect_level=SideEffect.NONE,
            adapter_type="local_function",
        )

        assert cap.capability_name == "test.action@v1"
        assert cap.side_effect_level == SideEffect.NONE

        # Missing required fields
        with pytest.raises(ValidationError):
            CapabilityDef(
                capability_name="test@v1",
                # Missing description, input_schema, etc.
            )

    def test_risk_assessment_validation(self):
        """Test RiskAssessment schema validation."""
        from agent_kernel.core.schemas import RiskAssessment, RiskLevel

        # Valid risk assessment
        risk = RiskAssessment(
            level=RiskLevel.MEDIUM,
            reasons=["Writes to external API", "Modifies user data"],
        )

        assert risk.level == RiskLevel.MEDIUM
        assert len(risk.reasons) == 2

        # Empty reasons is valid
        risk_no_reasons = RiskAssessment(
            level=RiskLevel.LOW,
            reasons=[],
        )

        assert len(risk_no_reasons.reasons) == 0

    def test_tool_call_record_validation(self):
        """Test ToolCallRecord schema validation."""
        from agent_kernel.core.schemas import CallStatus, ToolCallRecord
        from agent_kernel.core.ids import generate_ulid

        # Valid tool call record
        record = ToolCallRecord(
            tool_call_id=generate_ulid(),
            capability_name="test@v1",
            input={"param": "value"},
            output={"output": "success"},
            duration_ms=250,
            status=CallStatus.SUCCESS,
        )

        assert record.status == CallStatus.SUCCESS
        assert record.duration_ms == 250

        # Failed tool call
        failed_record = ToolCallRecord(
            tool_call_id=generate_ulid(),
            capability_name="test@v1",
            input={},
            output={},
            error={"code": "error", "message": "Something went wrong"},
            duration_ms=100,
            status=CallStatus.ERROR,
        )

        assert failed_record.status == CallStatus.ERROR
        assert failed_record.error is not None

    def test_context_budget_validation(self):
        """Test ContextBudget schema validation."""
        from agent_kernel.core.schemas import ContextBudget

        # Valid budget
        budget = ContextBudget(
            max_tokens=4000,
            max_items=20,
        )

        assert budget.max_tokens == 4000
        assert budget.max_items == 20

        # Negative values are rejected (ge=0 constraint)
        with pytest.raises(ValidationError, match="greater_than_equal"):
            ContextBudget(
                max_tokens=-100,
                max_items=20,
            )

    def test_model_config_validation(self):
        """Test ModelConfig schema validation."""
        from agent_kernel.core.schemas import ModelConfig

        # Valid config
        config = ModelConfig(
            provider="openai",
            model="gpt-4",
            temperature=0.7,
            max_tokens=2000,
        )

        assert config.provider == "openai"
        assert config.temperature == 0.7

        # Temperature must be in [0.0, 1.0] range
        with pytest.raises(ValidationError, match="less_than_equal"):
            ModelConfig(
                provider="openai",
                model="gpt-4",
                temperature=2.0,
            )

    def test_side_effect_enum(self):
        """Test SideEffect enum values."""
        from agent_kernel.core.schemas import SideEffect

        assert SideEffect.NONE.value == "none"
        assert SideEffect.LOCAL_WRITE.value == "local"
        assert SideEffect.EXTERNAL_WRITE.value == "external"

    def test_risk_level_ordering(self):
        """Test RiskLevel enum ordering."""
        from agent_kernel.core.schemas import RiskLevel

        # Risk levels should have meaningful comparison
        assert RiskLevel.LOW.value == "low"
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"

    def test_ref_type_enum(self):
        """Test RefType enum values."""
        from agent_kernel.core.schemas import RefType

        assert RefType.NOTE.value == "note"
        assert RefType.TASK.value == "task"
        assert RefType.EVENT.value == "event"
        assert RefType.GRAPH_NODE.value == "graph_node"

    def test_schema_serialization(self):
        """Test that schemas can be serialized to JSON."""
        from agent_kernel.core.schemas import (
            ActionRequest,
            SideEffect,
        )

        action = ActionRequest(
            capability_name="test@v1",
            args={"key": "value"},
            side_effect=SideEffect.NONE,
            requires_approval=True,
        )

        # Serialize to dict
        action_dict = action.model_dump()

        assert action_dict["capability_name"] == "test@v1"
        assert action_dict["side_effect"] == "none"

        # Serialize to JSON string
        action_json = action.model_dump_json()
        assert "test@v1" in action_json

    def test_schema_deserialization(self):
        """Test that schemas can be deserialized from JSON."""
        from agent_kernel.core.schemas import ActionRequest, SideEffect

        data = {
            "capability_name": "test@v1",
            "args": {"param": "value"},
            "side_effect": "external",
            "requires_approval": True,
        }

        # Deserialize from dict
        action = ActionRequest(**data)

        assert action.capability_name == "test@v1"
        assert action.side_effect == SideEffect.EXTERNAL_WRITE
        assert action.requires_approval is True
