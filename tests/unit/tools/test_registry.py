"""Tests for capability registry."""

from pathlib import Path

import pytest

from agent_kernel.core.errors import CapabilityNotFoundError
from agent_kernel.core.schemas import CapabilityDef, SideEffect
from agent_kernel.tools.registry import CapabilityRegistry


class TestCapabilityRegistry:
    """Tests for CapabilityRegistry."""

    def test_register_capability(self):
        """Test registering a capability."""
        registry = CapabilityRegistry()

        capability = CapabilityDef(
            capability_name="test.action@v1",
            description="A test capability",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )

        registry.register(capability)

        assert registry.has("test.action@v1")
        assert registry.get("test.action@v1") == capability

    def test_get_or_raise(self):
        """Test get_or_raise method."""
        registry = CapabilityRegistry()

        capability = CapabilityDef(
            capability_name="exists@v1",
            description="Exists",
            input_schema={},
            output_schema={},
        )
        registry.register(capability)

        # Should work for existing
        result = registry.get_or_raise("exists@v1")
        assert result.capability_name == "exists@v1"

        # Should raise for non-existing
        with pytest.raises(CapabilityNotFoundError):
            registry.get_or_raise("nonexistent@v1")

    def test_list_capabilities(self):
        """Test listing capabilities."""
        registry = CapabilityRegistry()

        for i in range(3):
            registry.register(CapabilityDef(
                capability_name=f"cap_{i}@v1",
                description=f"Capability {i}",
                input_schema={},
                output_schema={},
            ))

        caps = registry.list_capabilities()
        assert len(caps) == 3

        names = registry.list_names()
        assert "cap_0@v1" in names
        assert "cap_1@v1" in names
        assert "cap_2@v1" in names

    def test_load_from_yaml(self, temp_dir: Path):
        """Test loading capability from YAML."""
        yaml_content = """
capability_name: tasks.list@v1
description: List tasks
input_schema:
  type: object
  properties:
    status:
      type: string
output_schema:
  type: object
side_effect_level: none
requires_approval_default: false
timeout_ms: 10000
adapter_type: local
"""
        yaml_path = temp_dir / "tasks.list@v1.yaml"
        yaml_path.write_text(yaml_content)

        registry = CapabilityRegistry()
        cap = registry.load_from_yaml(yaml_path)

        assert cap.capability_name == "tasks.list@v1"
        assert cap.description == "List tasks"
        assert cap.side_effect_level == SideEffect.NONE
        assert cap.timeout_ms == 10000

    def test_load_from_directory(self, temp_dir: Path):
        """Test loading all capabilities from directory."""
        # Create multiple YAML files
        for i in range(3):
            yaml_content = f"""
capability_name: cap_{i}@v1
description: Capability {i}
input_schema:
  type: object
output_schema:
  type: object
side_effect_level: none
"""
            (temp_dir / f"cap_{i}@v1.yaml").write_text(yaml_content)

        registry = CapabilityRegistry()
        caps = registry.load_from_directory(temp_dir)

        assert len(caps) == 3
        assert registry.has("cap_0@v1")
        assert registry.has("cap_1@v1")
        assert registry.has("cap_2@v1")

    def test_clear_registry(self):
        """Test clearing the registry."""
        registry = CapabilityRegistry()
        registry.register(CapabilityDef(
            capability_name="test@v1",
            description="Test",
            input_schema={},
            output_schema={},
        ))

        assert registry.has("test@v1")

        registry.clear()

        assert not registry.has("test@v1")
        assert len(registry.list_capabilities()) == 0

    def test_capability_properties(self):
        """Test capability base_name and version properties."""
        registry = CapabilityRegistry()
        cap = CapabilityDef(
            capability_name="tasks.create@v2",
            description="Create task",
            input_schema={},
            output_schema={},
        )
        registry.register(cap)

        retrieved = registry.get("tasks.create@v2")
        assert retrieved.base_name == "tasks.create"
        assert retrieved.version == "v2"
