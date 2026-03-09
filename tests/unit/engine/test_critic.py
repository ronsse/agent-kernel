"""Tests for CriticEngine."""

import pytest

from agent_kernel.core.schemas import (
    ActionRequest,
    AgentProfile,
    ContextBudget,
    ContextItem,
    ContextPacket,
    ContextPolicy,
    ContextRef,
    Plan,
    PlanValidation,
    RefType,
)
from agent_kernel.engine.critic import CriticEngine, Critique


@pytest.fixture
def sample_agent_profile() -> AgentProfile:
    """Create a sample agent profile."""
    return AgentProfile(
        agent_profile_id="test_agent",
        name="Test Agent",
        engine="custom",
        allowed_capabilities=["tasks.list@v1", "tasks.create@v1"],
        context_policy=ContextPolicy(max_tokens=4000),
    )


@pytest.fixture
def sample_context_packet() -> ContextPacket:
    """Create a sample context packet with items."""
    return ContextPacket(
        intent="Test intent",
        budget=ContextBudget(max_tokens=4000),
        items=[
            ContextItem(
                ref=ContextRef(ref_type=RefType.TASK, ref_id="task_123"),
                excerpt="Sample task content",
            ),
        ],
    )


@pytest.fixture
def empty_context_packet() -> ContextPacket:
    """Create an empty context packet."""
    return ContextPacket(
        intent="Test intent",
        budget=ContextBudget(max_tokens=4000),
        items=[],
    )


@pytest.fixture
def valid_plan() -> Plan:
    """Create a valid plan with citations."""
    return Plan(
        intent="List tasks",
        summary="A simple plan to list tasks",
        context_refs_used=[
            ContextRef(ref_type=RefType.TASK, ref_id="task_123"),
        ],
        actions=[
            ActionRequest(
                capability_name="tasks.list@v1",
                args={"status": "open"},
            ),
        ],
        confidence=0.9,
    )


@pytest.fixture
def plan_without_actions() -> Plan:
    """Create a plan with no actions."""
    return Plan(
        intent="Do nothing",
        summary="A plan that does nothing",
        actions=[],
    )


@pytest.fixture
def plan_without_citations() -> Plan:
    """Create a plan without citations."""
    return Plan(
        intent="Do something",
        summary="A plan without citations",
        context_refs_used=[],
        actions=[
            ActionRequest(
                capability_name="tasks.list@v1",
                args={},
            ),
        ],
    )


class TestCritique:
    """Tests for Critique dataclass."""

    def test_default_values(self):
        """Test default values."""
        critique = Critique()

        assert critique.issues == []
        assert critique.missing_context == []
        assert critique.risk_flags == []
        assert critique.confidence_adjustment == 0.0
        assert critique.should_revise is False
        assert critique.has_issues is False
        assert critique.issue_count == 0

    def test_has_issues(self):
        """Test has_issues property."""
        critique_with_issues = Critique(issues=["Problem found"])
        critique_with_risks = Critique(risk_flags=["Risk identified"])
        critique_clean = Critique()

        assert critique_with_issues.has_issues is True
        assert critique_with_risks.has_issues is True
        assert critique_clean.has_issues is False

    def test_issue_count(self):
        """Test issue_count property."""
        critique = Critique(
            issues=["Issue 1", "Issue 2"],
            risk_flags=["Risk 1"],
        )

        assert critique.issue_count == 3

    def test_to_dict(self):
        """Test to_dict method."""
        critique = Critique(
            issues=["Test issue"],
            summary="Test summary",
            should_revise=True,
        )

        result = critique.to_dict()

        assert result["issues"] == ["Test issue"]
        assert result["summary"] == "Test summary"
        assert result["should_revise"] is True


class TestCriticEngine:
    """Tests for CriticEngine."""

    def test_initialization(self):
        """Test engine initialization."""
        engine = CriticEngine(use_stub=True)
        assert engine._use_stub is True

    @pytest.mark.asyncio
    async def test_stub_critique_valid_plan(
        self,
        valid_plan: Plan,
        sample_context_packet: ContextPacket,
    ):
        """Test stub critique for a valid plan."""
        engine = CriticEngine(use_stub=True)

        critique = await engine.critique(valid_plan, sample_context_packet)

        # Valid plan should have minimal issues
        assert critique.should_revise is False
        assert critique.summary == "Stub critique generated"

    @pytest.mark.asyncio
    async def test_stub_critique_empty_plan(
        self,
        plan_without_actions: Plan,
        sample_context_packet: ContextPacket,
    ):
        """Test stub critique for a plan with no actions."""
        engine = CriticEngine(use_stub=True)

        critique = await engine.critique(
            plan_without_actions, sample_context_packet
        )

        assert "no actions" in critique.issues[0].lower()
        assert critique.confidence_adjustment < 0
        assert critique.should_revise is True

    @pytest.mark.asyncio
    async def test_stub_critique_missing_citations(
        self,
        plan_without_citations: Plan,
        sample_context_packet: ContextPacket,
    ):
        """Test stub critique for plan missing citations."""
        engine = CriticEngine(use_stub=True)

        critique = await engine.critique(
            plan_without_citations, sample_context_packet
        )

        # Should flag missing citations when context exists
        assert any("cite" in issue.lower() for issue in critique.issues)

    @pytest.mark.asyncio
    async def test_stub_critique_with_assumptions(
        self,
        sample_context_packet: ContextPacket,
    ):
        """Test stub critique for plan with assumptions."""
        plan = Plan(
            intent="Test",
            summary="Test plan",
            actions=[
                ActionRequest(capability_name="tasks.list@v1", args={}),
            ],
            validation=PlanValidation(
                assumptions=["User is available", "System is working"],
            ),
        )

        engine = CriticEngine(use_stub=True)
        critique = await engine.critique(plan, sample_context_packet)

        # Should note assumptions as missing context
        assert len(critique.missing_context) > 0

    @pytest.mark.asyncio
    async def test_stub_critique_approval_required(
        self,
        empty_context_packet: ContextPacket,
    ):
        """Test stub critique for actions requiring approval."""
        plan = Plan(
            intent="Test",
            summary="Test plan",
            actions=[
                ActionRequest(
                    capability_name="external.api@v1",
                    args={},
                    requires_approval=True,
                ),
            ],
        )

        engine = CriticEngine(use_stub=True)
        critique = await engine.critique(plan, empty_context_packet)

        # Should flag actions requiring approval
        assert len(critique.risk_flags) > 0

    @pytest.mark.asyncio
    async def test_fallback_critique_on_error(
        self,
        valid_plan: Plan,
        sample_context_packet: ContextPacket,
    ):
        """Test fallback critique when no LLM is available."""
        # No LLM service and not using stub
        engine = CriticEngine(use_stub=False, llm_service=None)

        # Should return stub critique since LLM is None
        critique = await engine.critique(valid_plan, sample_context_packet)

        assert critique is not None

    def test_extract_json_code_block(self):
        """Test JSON extraction from code blocks."""
        engine = CriticEngine(use_stub=True)

        content = '''Here's my analysis:

```json
{"issues": ["test"], "summary": "found issue"}
```

Done.'''

        json_str = engine._extract_json(content)
        assert '"issues"' in json_str
        assert '"test"' in json_str

    def test_extract_json_raw(self):
        """Test JSON extraction from raw content."""
        engine = CriticEngine(use_stub=True)

        content = 'Analysis: {"issues": [], "summary": "clean"}'

        json_str = engine._extract_json(content)
        assert '"issues"' in json_str

    @pytest.mark.asyncio
    async def test_critique_with_agent_profile(
        self,
        valid_plan: Plan,
        sample_context_packet: ContextPacket,
        sample_agent_profile: AgentProfile,
    ):
        """Test critique includes agent profile context."""
        engine = CriticEngine(use_stub=True)

        # Should not error with agent profile
        critique = await engine.critique(
            valid_plan, sample_context_packet, sample_agent_profile
        )

        assert critique is not None
