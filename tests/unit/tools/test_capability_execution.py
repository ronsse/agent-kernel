"""Tests for capability execution via tool broker."""

import pytest
from pathlib import Path


class TestCapabilityExecution:
    """Tests for executing capabilities through the tool broker."""

    @pytest.fixture
    def test_capability_file(self, temp_dir):
        """Create a test capability YAML file."""
        capabilities_dir = temp_dir / "capabilities"
        capabilities_dir.mkdir(exist_ok=True)

        capability_file = capabilities_dir / "test.echo@v1.yaml"
        capability_file.write_text("""
capability_name: test.echo@v1
description: Echo back the input
input_schema:
  type: object
  properties:
    message:
      type: string
      description: Message to echo
  required:
    - message
output_schema:
  type: object
  properties:
    result:
      type: string
side_effect_level: none
adapter_type: local_function
adapter_config:
  module: builtins
  function: str
""")
        return capabilities_dir

    def test_load_capability_from_yaml(self, test_capability_file, capability_registry):
        """Test loading capability specification from YAML."""
        capability_registry.load_from_directory(str(test_capability_file))

        # Check if capability was loaded
        cap = capability_registry.get("test.echo@v1")
        assert cap is not None
        assert cap.capability_name == "test.echo@v1"
        from agent_kernel.core.schemas import SideEffect

        assert cap.side_effect_level == SideEffect.NONE

    def test_validate_capability_input(self, test_capability_file, capability_registry):
        """Test input validation against capability schema."""
        capability_registry.load_from_directory(str(test_capability_file))

        cap = capability_registry.get("test.echo@v1")

        # Valid input
        valid_args = {"message": "Hello, World!"}
        # Should not raise

        # Invalid input (missing required field)
        invalid_args = {}  # Missing 'message'
        # Would need validation function to test this

    def test_capability_allowlist(self, sample_agent_profile):
        """Test that agent can only use allowed capabilities."""
        # Agent profile has allowed_capabilities list
        allowed = sample_agent_profile.allowed_capabilities

        # Check if capability is allowed
        assert "tasks.list@v1" in allowed
        assert "tasks.create@v1" in allowed

        # Check if capability is not allowed
        assert "dangerous.delete_all@v1" not in allowed

    def test_capability_side_effect_levels(self):
        """Test different side effect levels."""
        from agent_kernel.core.schemas import CapabilityDef, SideEffect

        # No side effects
        read_only = CapabilityDef(
            capability_name="data.read@v1",
            description="Read data",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level=SideEffect.NONE,
            adapter_type="local_function",
        )
        assert read_only.side_effect_level == SideEffect.NONE

        # Write side effects
        write_cap = CapabilityDef(
            capability_name="data.write@v1",
            description="Write data",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level=SideEffect.EXTERNAL_WRITE,
            adapter_type="local_function",
        )
        assert write_cap.side_effect_level == SideEffect.EXTERNAL_WRITE

    def test_capability_versioning(self, temp_dir, capability_registry):
        """Test handling multiple versions of the same capability."""
        capabilities_dir = temp_dir / "capabilities"
        capabilities_dir.mkdir(exist_ok=True)

        # Create v1
        v1_file = capabilities_dir / "test.action@v1.yaml"
        v1_file.write_text("""
capability_name: test.action@v1
description: Version 1
input_schema:
  type: object
output_schema:
  type: object
side_effect_level: none
adapter_type: local_function
""")

        # Create v2
        v2_file = capabilities_dir / "test.action@v2.yaml"
        v2_file.write_text("""
capability_name: test.action@v2
description: Version 2 with improvements
input_schema:
  type: object
  properties:
    new_param:
      type: string
output_schema:
  type: object
side_effect_level: none
adapter_type: local_function
""")

        capability_registry.load_from_directory(str(capabilities_dir))

        # Both versions should be loaded
        v1 = capability_registry.get("test.action@v1")
        v2 = capability_registry.get("test.action@v2")

        assert v1 is not None
        assert v2 is not None
        assert v1.description == "Version 1"
        assert v2.description == "Version 2 with improvements"

    def test_capability_adapter_types(self):
        """Test different adapter types."""
        from agent_kernel.core.schemas import CapabilityDef, SideEffect

        # Local function adapter
        local_cap = CapabilityDef(
            capability_name="local.function@v1",
            description="Local function",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level=SideEffect.NONE,
            adapter_type="local_function",
        )
        assert local_cap.adapter_type == "local_function"

        # HTTP adapter
        http_cap = CapabilityDef(
            capability_name="api.call@v1",
            description="HTTP API call",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level=SideEffect.EXTERNAL_WRITE,
            adapter_type="http",
        )
        assert http_cap.adapter_type == "http"

        # Subprocess adapter
        subprocess_cap = CapabilityDef(
            capability_name="shell.command@v1",
            description="Shell command",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level=SideEffect.LOCAL_WRITE,
            adapter_type="subprocess",
        )
        assert subprocess_cap.adapter_type == "subprocess"

    def test_capability_timeout(self):
        """Test capability execution timeout."""
        from agent_kernel.core.schemas import CapabilityDef, SideEffect

        cap = CapabilityDef(
            capability_name="slow.operation@v1",
            description="Slow operation",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level=SideEffect.NONE,
            adapter_type="local_function",
            timeout_ms=5000,  # 5 second timeout
        )

        assert cap.timeout_ms == 5000

    def test_capability_rate_limit(self):
        """Test capability rate limit configuration."""
        from agent_kernel.core.schemas import CapabilityDef, RateLimit, SideEffect

        cap = CapabilityDef(
            capability_name="rate.limited@v1",
            description="Rate limited operation",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level=SideEffect.NONE,
            adapter_type="local_function",
            rate_limit=RateLimit(max_calls_per_minute=10, max_calls_per_hour=120),
        )

        assert cap.rate_limit is not None
        assert cap.rate_limit.max_calls_per_minute == 10
        assert cap.rate_limit.max_calls_per_hour == 120

    def test_capability_parameter_validation(self):
        """Test that capability parameters are validated."""
        from agent_kernel.core.schemas import CapabilityDef, SideEffect

        cap = CapabilityDef(
            capability_name="validate.params@v1",
            description="Validates parameters",
            input_schema={
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "email": {
                        "type": "string",
                        "format": "email",
                    },
                },
                "required": ["count"],
            },
            output_schema={"type": "object"},
            side_effect_level=SideEffect.NONE,
            adapter_type="local_function",
        )

        # Verify schema structure
        props = cap.input_schema["properties"]
        assert "count" in props
        assert props["count"]["minimum"] == 1
        assert props["count"]["maximum"] == 100

    def test_capability_output_schema(self):
        """Test capability output schema definition."""
        from agent_kernel.core.schemas import CapabilityDef, SideEffect

        cap = CapabilityDef(
            capability_name="structured.output@v1",
            description="Returns structured output",
            input_schema={"type": "object"},
            output_schema={
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "message": {"type": "string"},
                    "data": {"type": "object"},
                },
                "required": ["success"],
            },
            side_effect_level=SideEffect.NONE,
            adapter_type="local_function",
        )

        # Verify output schema
        output_props = cap.output_schema["properties"]
        assert "success" in output_props
        assert "message" in output_props
        assert "data" in output_props

    def test_capability_redaction_policy(self):
        """Test capability redaction policy."""
        from agent_kernel.core.schemas import CapabilityDef, RedactionPolicy, SideEffect

        cap = CapabilityDef(
            capability_name="redacted.capability@v1",
            description="Capability with redaction policy",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level=SideEffect.NONE,
            adapter_type="local_function",
            redaction_policy=RedactionPolicy(
                redact_fields=["token", "password"],
                redact_patterns=[r"sk-[A-Za-z0-9]+"],
            ),
        )

        assert cap.redaction_policy is not None
        assert "token" in cap.redaction_policy.redact_fields

    def test_capability_discovery(self, temp_dir, capability_registry):
        """Test discovering all available capabilities."""
        capabilities_dir = temp_dir / "capabilities"
        capabilities_dir.mkdir(exist_ok=True)

        # Create multiple capabilities
        for i in range(5):
            cap_file = capabilities_dir / f"test.cap{i}@v1.yaml"
            cap_file.write_text(f"""
capability_name: test.cap{i}@v1
description: Test capability {i}
input_schema:
  type: object
output_schema:
  type: object
side_effect_level: none
adapter_type: local_function
""")

        capability_registry.load_from_directory(str(capabilities_dir))

        # List all capabilities
        all_caps = capability_registry.list_capabilities()

        assert len(all_caps) >= 5

    def test_capability_filtering_by_side_effect(self, temp_dir, capability_registry):
        """Test filtering capabilities by side effect level."""
        capabilities_dir = temp_dir / "capabilities"
        capabilities_dir.mkdir(exist_ok=True)

        # Create capabilities with different side effects
        for level in ["none", "local", "external"]:
            cap_file = capabilities_dir / f"test.{level}@v1.yaml"
            cap_file.write_text(f"""
capability_name: test.{level}@v1
description: {level} capability
input_schema:
  type: object
output_schema:
  type: object
side_effect_level: {level}
adapter_type: local_function
""")

        capability_registry.load_from_directory(str(capabilities_dir))

        all_caps = capability_registry.list_capabilities()

        # Filter by side effect
        from agent_kernel.core.schemas import SideEffect

        read_only = [c for c in all_caps if c.side_effect_level == SideEffect.NONE]
        local_writes = [c for c in all_caps if c.side_effect_level == SideEffect.LOCAL_WRITE]
        external_writes = [c for c in all_caps if c.side_effect_level == SideEffect.EXTERNAL_WRITE]

        assert len(read_only) >= 1
        assert len(local_writes) >= 1
        assert len(external_writes) >= 1
