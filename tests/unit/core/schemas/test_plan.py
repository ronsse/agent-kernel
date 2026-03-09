"""Tests for plan schemas."""


from agent_kernel.core.schemas import (
    ActionRequest,
    ContextRef,
    Plan,
    PlanValidation,
    RefType,
    RiskAssessment,
    RiskLevel,
    SideEffect,
)


class TestActionRequest:
    """Tests for ActionRequest schema."""

    def test_create_action_request(self):
        """Test creating an action request."""
        action = ActionRequest(
            capability_name="tasks.create@v1",
            args={"title": "New task", "priority": "high"},
            side_effect=SideEffect.LOCAL_WRITE,
            requires_approval=False,
            idempotency_key="create_task_123",
        )

        assert action.capability_name == "tasks.create@v1"
        assert action.args["title"] == "New task"
        assert action.side_effect == SideEffect.LOCAL_WRITE
        assert action.action_id is not None

    def test_action_defaults(self):
        """Test action request default values."""
        action = ActionRequest(capability_name="tasks.list@v1")

        assert action.args == {}
        assert action.side_effect == SideEffect.NONE
        assert action.requires_approval is False
        assert action.idempotency_key is None
        assert action.cap_group is None
        assert action.cap_limit is None

    def test_all_side_effects(self):
        """Test all side effect types."""
        for effect in SideEffect:
            action = ActionRequest(
                capability_name="test@v1",
                side_effect=effect,
            )
            assert action.side_effect == effect


class TestRiskAssessment:
    """Tests for RiskAssessment schema."""

    def test_default_risk(self):
        """Test default risk assessment."""
        risk = RiskAssessment()

        assert risk.level == RiskLevel.LOW
        assert risk.reasons == []

    def test_risk_with_reasons(self):
        """Test risk with reasons."""
        risk = RiskAssessment(
            level=RiskLevel.HIGH,
            reasons=[
                "External API call",
                "Modifies production data",
            ],
        )

        assert risk.level == RiskLevel.HIGH
        assert len(risk.reasons) == 2


class TestPlan:
    """Tests for Plan schema."""

    def test_create_plan(self):
        """Test creating a plan."""
        ref = ContextRef(ref_type=RefType.TASK, ref_id="task_1")
        action = ActionRequest(capability_name="tasks.list@v1")

        plan = Plan(
            intent="List my tasks",
            summary="Retrieve and display all open tasks.",
            context_refs_used=[ref],
            actions=[action],
        )

        assert plan.intent == "List my tasks"
        assert plan.summary == "Retrieve and display all open tasks."
        assert len(plan.context_refs_used) == 1
        assert len(plan.actions) == 1
        assert plan.plan_id is not None

    def test_plan_has_external_writes(self):
        """Test checking for external writes."""
        action_read = ActionRequest(
            capability_name="tasks.list@v1",
            side_effect=SideEffect.NONE,
        )
        action_write = ActionRequest(
            capability_name="email.send@v1",
            side_effect=SideEffect.EXTERNAL_WRITE,
        )

        plan_read = Plan(
            intent="Read",
            summary="Read only",
            actions=[action_read],
        )
        plan_write = Plan(
            intent="Write",
            summary="External write",
            actions=[action_read, action_write],
        )

        assert plan_read.has_external_writes() is False
        assert plan_write.has_external_writes() is True

    def test_plan_requires_approval(self):
        """Test checking for approval requirements."""
        action_no_approval = ActionRequest(
            capability_name="tasks.list@v1",
            requires_approval=False,
        )
        action_approval = ActionRequest(
            capability_name="email.send@v1",
            requires_approval=True,
        )

        plan_no = Plan(
            intent="No approval",
            summary="No approval needed",
            actions=[action_no_approval],
        )
        plan_yes = Plan(
            intent="Needs approval",
            summary="Approval required",
            actions=[action_no_approval, action_approval],
        )

        assert plan_no.requires_any_approval() is False
        assert plan_yes.requires_any_approval() is True

    def test_get_capability_names(self):
        """Test getting capability names from plan."""
        plan = Plan(
            intent="Multi-action",
            summary="Multiple actions",
            actions=[
                ActionRequest(capability_name="tasks.list@v1"),
                ActionRequest(capability_name="tasks.create@v1"),
                ActionRequest(capability_name="tasks.list@v1"),  # Duplicate
            ],
        )

        names = plan.get_capability_names()
        assert len(names) == 2
        assert "tasks.list@v1" in names
        assert "tasks.create@v1" in names

    def test_plan_with_questions(self):
        """Test plan with clarifying questions."""
        plan = Plan(
            intent="Unclear task",
            summary="Need more information",
            actions=[],
            questions=[
                "What priority should the task have?",
                "Is there a deadline?",
            ],
        )

        assert len(plan.questions) == 2

    def test_plan_validation_fields(self):
        """Test plan validation fields."""
        plan = Plan(
            intent="Partial info",
            summary="Working with assumptions",
            actions=[],
            validation=PlanValidation(
                missing_info=["Due date", "Priority"],
                assumptions=["Using default project"],
            ),
        )

        assert len(plan.validation.missing_info) == 2
        assert len(plan.validation.assumptions) == 1
