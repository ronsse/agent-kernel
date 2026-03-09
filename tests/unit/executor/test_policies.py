"""Tests for Policy Configuration System."""


from agent_kernel.executor.policies import (
    ApprovalCondition,
    ApprovalMode,
    ApprovalPolicy,
    PolicyConfig,
    PolicyManager,
    RateLimitPolicy,
    RedactionMode,
    RedactionPattern,
    RedactionPolicy,
    ScopeRestriction,
)


class TestApprovalPolicy:
    """Tests for ApprovalPolicy."""

    def test_matches_capability_exact(self):
        """Test exact capability matching."""
        policy = ApprovalPolicy(
            name="test",
            capabilities=["tasks.delete@v1"],
        )

        assert policy.matches_capability("tasks.delete@v1") is True
        assert policy.matches_capability("tasks.create@v1") is False

    def test_matches_capability_glob(self):
        """Test glob capability matching."""
        policy = ApprovalPolicy(
            name="test",
            capabilities=["*.delete@*"],
        )

        assert policy.matches_capability("tasks.delete@v1") is True
        assert policy.matches_capability("notes.delete@v1") is True
        assert policy.matches_capability("tasks.create@v1") is False

    def test_matches_capability_empty(self):
        """Test empty capabilities (matches all)."""
        policy = ApprovalPolicy(name="test")

        assert policy.matches_capability("anything") is True

    def test_matches_agent(self):
        """Test agent matching."""
        policy = ApprovalPolicy(
            name="test",
            agents=["agent1", "agent2"],
        )

        assert policy.matches_agent("agent1") is True
        assert policy.matches_agent("agent3") is False

    def test_evaluate_conditions_eq(self):
        """Test condition evaluation with eq operator."""
        policy = ApprovalPolicy(
            name="test",
            conditions=[
                ApprovalCondition(field="priority", operator="eq", value="high"),
            ],
        )

        assert policy.evaluate_conditions({"priority": "high"}) is True
        assert policy.evaluate_conditions({"priority": "low"}) is False

    def test_evaluate_conditions_gt(self):
        """Test condition evaluation with gt operator."""
        policy = ApprovalPolicy(
            name="test",
            conditions=[
                ApprovalCondition(field="amount", operator="gt", value=100),
            ],
        )

        assert policy.evaluate_conditions({"amount": 150}) is True
        assert policy.evaluate_conditions({"amount": 50}) is False

    def test_evaluate_conditions_contains(self):
        """Test condition evaluation with contains operator."""
        policy = ApprovalPolicy(
            name="test",
            conditions=[
                ApprovalCondition(field="tags", operator="contains", value="urgent"),
            ],
        )

        assert policy.evaluate_conditions({"tags": "urgent,important"}) is True
        assert policy.evaluate_conditions({"tags": "normal"}) is False

    def test_evaluate_conditions_multiple(self):
        """Test multiple conditions (AND logic)."""
        policy = ApprovalPolicy(
            name="test",
            conditions=[
                ApprovalCondition(field="priority", operator="eq", value="high"),
                ApprovalCondition(field="amount", operator="gt", value=100),
            ],
        )

        # Both must match
        assert policy.evaluate_conditions({"priority": "high", "amount": 150}) is True
        assert policy.evaluate_conditions({"priority": "high", "amount": 50}) is False
        assert policy.evaluate_conditions({"priority": "low", "amount": 150}) is False


class TestPolicyManager:
    """Tests for PolicyManager."""

    def test_init(self):
        """Test manager initialization."""
        manager = PolicyManager()
        assert manager._config is not None

    def test_load_from_yaml(self, tmp_path):
        """Test loading policies from YAML."""
        policy_file = tmp_path / "policies.yaml"
        policy_file.write_text("""
version: "1.0"
approval_policies:
  - name: test_policy
    mode: always
    capabilities:
      - "test.*"
""")

        manager = PolicyManager()
        manager.load_from_yaml(policy_file)

        assert len(manager._config.approval_policies) == 1
        assert manager._config.approval_policies[0].name == "test_policy"

    def test_requires_approval_always(self):
        """Test approval required with ALWAYS mode."""
        config = PolicyConfig(
            approval_policies=[
                ApprovalPolicy(
                    name="always",
                    mode=ApprovalMode.ALWAYS,
                    capabilities=["*.delete@*"],
                ),
            ]
        )

        manager = PolicyManager()
        manager.set_config(config)

        assert manager.requires_approval(
            "tasks.delete@v1", "agent1", {}, False
        ) is True

    def test_requires_approval_never(self):
        """Test approval not required with NEVER mode."""
        config = PolicyConfig(
            approval_policies=[
                ApprovalPolicy(
                    name="never",
                    mode=ApprovalMode.NEVER,
                    capabilities=["*.list@*"],
                ),
            ]
        )

        manager = PolicyManager()
        manager.set_config(config)

        assert manager.requires_approval(
            "tasks.list@v1", "agent1", {}, True
        ) is False

    def test_requires_approval_conditional(self):
        """Test conditional approval."""
        config = PolicyConfig(
            approval_policies=[
                ApprovalPolicy(
                    name="conditional",
                    mode=ApprovalMode.CONDITIONAL,
                    conditions=[
                        ApprovalCondition(field="amount", operator="gt", value=1000),
                    ],
                    capabilities=["*"],
                ),
            ]
        )

        manager = PolicyManager()
        manager.set_config(config)

        # High amount requires approval
        assert manager.requires_approval(
            "payment.send@v1", "agent1", {"amount": 5000}, False
        ) is True

        # Low amount doesn't
        assert manager.requires_approval(
            "payment.send@v1", "agent1", {"amount": 100}, False
        ) is False

    def test_check_rate_limit_allowed(self):
        """Test rate limit allows calls within limit."""
        config = PolicyConfig(
            rate_limit_policies=[
                RateLimitPolicy(
                    name="test",
                    max_calls=5,
                    window_seconds=60,
                ),
            ]
        )

        manager = PolicyManager()
        manager.set_config(config)

        # First 5 calls should be allowed
        for _ in range(5):
            allowed, _ = manager.check_rate_limit("test@v1", "agent1")
            assert allowed is True

    def test_check_rate_limit_exceeded(self):
        """Test rate limit blocks calls over limit."""
        config = PolicyConfig(
            rate_limit_policies=[
                RateLimitPolicy(
                    name="test",
                    max_calls=2,
                    window_seconds=60,
                ),
            ]
        )

        manager = PolicyManager()
        manager.set_config(config)

        # First 2 calls allowed
        allowed, _ = manager.check_rate_limit("test@v1", "agent1")
        assert allowed is True
        allowed, _ = manager.check_rate_limit("test@v1", "agent1")
        assert allowed is True

        # Third call should be blocked
        allowed, msg = manager.check_rate_limit("test@v1", "agent1")
        assert allowed is False
        assert "Rate limit exceeded" in msg

    def test_redact_data_fields(self):
        """Test field-based redaction."""
        config = PolicyConfig(
            redaction_policies=[
                RedactionPolicy(
                    name="test",
                    fields=["password", "secret"],
                ),
            ]
        )

        manager = PolicyManager()
        manager.set_config(config)

        data = {
            "username": "user1",
            "password": "supersecret123",
            "secret": "topsecret",
        }

        redacted = manager.redact_data(data)

        assert redacted["username"] == "user1"  # Not redacted
        assert "supersecret123" not in redacted["password"]
        assert "*" in redacted["password"]

    def test_redact_data_patterns(self):
        """Test pattern-based redaction."""
        config = PolicyConfig(
            redaction_policies=[
                RedactionPolicy(
                    name="test",
                    patterns=[
                        RedactionPattern(
                            name="api_key",
                            pattern=r"sk-[a-zA-Z0-9]+",
                            mode=RedactionMode.MASK,
                        ),
                    ],
                ),
            ]
        )

        manager = PolicyManager()
        manager.set_config(config)

        data = {"api_key": "sk-abcdefghij1234567890"}

        redacted = manager.redact_data(data)

        assert "sk-abcdefghij1234567890" not in str(redacted)

    def test_check_scope_allowed(self):
        """Test scope check allows valid operations."""
        config = PolicyConfig(
            scope_restrictions=[
                ScopeRestriction(
                    name="test",
                    allowed_projects=["project1", "project2"],
                    agents=["agent1"],
                ),
            ]
        )

        manager = PolicyManager()
        manager.set_config(config)

        allowed, _ = manager.check_scope(
            "test@v1", "agent1", project_id="project1"
        )
        assert allowed is True

    def test_check_scope_denied_capability(self):
        """Test scope check denies blocked capabilities."""
        config = PolicyConfig(
            scope_restrictions=[
                ScopeRestriction(
                    name="test",
                    denied_capabilities=["system.*"],
                ),
            ]
        )

        manager = PolicyManager()
        manager.set_config(config)

        allowed, msg = manager.check_scope("system.execute@v1", "agent1")
        assert allowed is False
        assert "denied" in msg.lower()

    def test_check_scope_folder_restriction(self):
        """Test folder-based scope restriction."""
        config = PolicyConfig(
            scope_restrictions=[
                ScopeRestriction(
                    name="test",
                    allowed_folders=["/home/user/", "/tmp/"],
                ),
            ]
        )

        manager = PolicyManager()
        manager.set_config(config)

        # Allowed folder
        allowed, _ = manager.check_scope(
            "files.read@v1", "agent1", folder="/home/user/docs"
        )
        assert allowed is True

        # Denied folder
        allowed, msg = manager.check_scope(
            "files.read@v1", "agent1", folder="/etc/passwd"
        )
        assert allowed is False


class TestRedactionModes:
    """Tests for different redaction modes."""

    def test_mask_mode(self):
        """Test MASK redaction mode."""
        manager = PolicyManager()

        result = manager._redact_value("secretpassword", RedactionMode.MASK)

        assert result.startswith("se")
        assert result.endswith("rd")
        assert "*" in result

    def test_hash_mode(self):
        """Test HASH redaction mode."""
        manager = PolicyManager()

        result = manager._redact_value("secret", RedactionMode.HASH)

        assert result.startswith("[HASH:")
        assert len(result) > 10

    def test_remove_mode(self):
        """Test REMOVE redaction mode."""
        manager = PolicyManager()

        result = manager._redact_value("secret", RedactionMode.REMOVE)

        assert result == "[REDACTED]"

    def test_truncate_mode(self):
        """Test TRUNCATE redaction mode."""
        manager = PolicyManager()

        result = manager._redact_value("mysecretpassword", RedactionMode.TRUNCATE)

        assert result == "myse...word"
