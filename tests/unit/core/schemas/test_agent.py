"""Tests for agent schemas."""


from agent_kernel.core.schemas import (
    AgentProfile,
    ApprovalPolicy,
    ContextPolicy,
    ModelConfig,
    RiskLevel,
    SideEffect,
)


class TestModelConfig:
    """Tests for ModelConfig schema."""

    def test_default_config(self):
        """Test default model configuration."""
        config = ModelConfig()

        assert config.provider == "openai"
        assert config.model == "gpt-4o"
        assert config.temperature == 0.3
        assert config.max_tokens == 4096

    def test_custom_config(self):
        """Test custom model configuration."""
        config = ModelConfig(
            provider="anthropic",
            model="claude-3-opus",
            temperature=0.7,
            max_tokens=8192,
            base_url="https://custom.api.com",
        )

        assert config.provider == "anthropic"
        assert config.model == "claude-3-opus"
        assert config.base_url == "https://custom.api.com"


class TestContextPolicy:
    """Tests for ContextPolicy schema."""

    def test_default_policy(self):
        """Test default context policy."""
        policy = ContextPolicy()

        assert policy.max_tokens == 4000
        assert policy.must_cite is True
        assert policy.allowed_scopes == []

    def test_custom_policy(self):
        """Test custom context policy."""
        policy = ContextPolicy(
            max_tokens=8000,
            max_notes=20,
            must_cite=False,
            allowed_scopes=["project_a", "project_b"],
            redaction_rules=["password", "api_key"],
        )

        assert policy.max_tokens == 8000
        assert policy.must_cite is False
        assert len(policy.allowed_scopes) == 2


class TestApprovalPolicy:
    """Tests for ApprovalPolicy schema."""

    def test_default_policy(self):
        """Test default approval policy."""
        policy = ApprovalPolicy()

        assert policy.require_approval_for == []
        assert SideEffect.NONE in policy.auto_approve_side_effects
        assert policy.max_auto_approve_risk == RiskLevel.LOW

    def test_strict_policy(self):
        """Test strict approval policy."""
        policy = ApprovalPolicy(
            require_approval_for=[
                "email.send@v1",
                "calendar.create@v1",
            ],
            auto_approve_side_effects=[],
            max_auto_approve_risk=RiskLevel.LOW,
        )

        assert len(policy.require_approval_for) == 2
        assert policy.auto_approve_side_effects == []


class TestAgentProfile:
    """Tests for AgentProfile schema."""

    def test_create_profile(self):
        """Test creating an agent profile."""
        profile = AgentProfile(
            agent_profile_id="daily_review",
            name="Daily Review Agent",
            description="Reviews tasks daily",
            engine="custom",
            allowed_capabilities=[
                "tasks.list@v1",
                "tasks.create@v1",
            ],
        )

        assert profile.agent_profile_id == "daily_review"
        assert profile.name == "Daily Review Agent"
        assert len(profile.allowed_capabilities) == 2

    def test_can_use_capability_exact(self):
        """Test exact capability matching."""
        profile = AgentProfile(
            agent_profile_id="test",
            name="Test",
            allowed_capabilities=["tasks.list@v1", "notes.search@v1"],
        )

        assert profile.can_use_capability("tasks.list@v1") is True
        assert profile.can_use_capability("notes.search@v1") is True
        assert profile.can_use_capability("tasks.create@v1") is False

    def test_can_use_capability_base_name(self):
        """Test base name capability matching."""
        profile = AgentProfile(
            agent_profile_id="test",
            name="Test",
            allowed_capabilities=["tasks.list@v1"],
        )

        # Should match when checking base name
        assert profile.can_use_capability("tasks.list@v2") is True  # Base matches
        assert profile.can_use_capability("notes.list@v1") is False

    def test_requires_approval_for(self):
        """Test approval requirement checking."""
        profile = AgentProfile(
            agent_profile_id="test",
            name="Test",
            allowed_capabilities=["email.send@v1", "tasks.list@v1"],
            approval_policy=ApprovalPolicy(
                require_approval_for=["email.send@v1"],
            ),
        )

        assert profile.requires_approval_for("email.send@v1") is True
        assert profile.requires_approval_for("tasks.list@v1") is False

    def test_profile_with_all_options(self):
        """Test profile with all configuration options."""
        profile = AgentProfile(
            agent_profile_id="full_config",
            name="Fully Configured Agent",
            description="An agent with all options set",
            engine="langgraph",
            llm_config=ModelConfig(
                provider="anthropic",
                model="claude-3-sonnet",
                temperature=0.5,
            ),
            allowed_capabilities=[
                "tasks.list@v1",
                "tasks.create@v1",
                "notes.search@v1",
                "calendar.list@v1",
            ],
            context_policy=ContextPolicy(
                max_tokens=8000,
                max_notes=30,
                must_cite=True,
            ),
            approval_policy=ApprovalPolicy(
                require_approval_for=["calendar.create@v1"],
                max_auto_approve_risk=RiskLevel.MEDIUM,
            ),
            output_schema_version="2.0.0",
        )

        assert profile.engine == "langgraph"
        assert profile.llm_config.provider == "anthropic"
        assert len(profile.allowed_capabilities) == 4
        assert profile.output_schema_version == "2.0.0"
