"""Unit tests for schema migrations."""

import pytest

from agent_kernel.core.migrations import (
    UPCASTERS,
    can_upcast,
    upcast,
)


class TestUpcasterRegistry:
    """Tests for the upcaster registry."""

    def test_upcasters_registered(self) -> None:
        """Test that v1.0.0 -> v1.0.1 upcasters are registered."""
        # At least one upcaster should be registered
        assert len(UPCASTERS) > 0
        # The key format should be (from_version, to_version)
        for key in UPCASTERS.keys():
            assert isinstance(key, tuple)
            assert len(key) == 2


class TestCanUpcast:
    """Tests for can_upcast function."""

    def test_same_version(self) -> None:
        """Test that same version always returns True."""
        assert can_upcast("1.0.0", "1.0.0") is True
        assert can_upcast("1.0.1", "1.0.1") is True

    def test_v1_0_0_to_v1_0_1(self) -> None:
        """Test that v1.0.0 can upcast to v1.0.1."""
        assert can_upcast("1.0.0", "1.0.1") is True


class TestUpcast:
    """Tests for upcast function."""

    def test_same_version_noop(self) -> None:
        """Test that upcasting same version is a no-op."""
        payload = {"schema_version": "1.0.1", "data": "test"}
        result = upcast(payload, "1.0.1")
        assert result["data"] == "test"
        assert result["schema_version"] == "1.0.1"

    def test_v1_0_0_to_v1_0_1_adds_missing_fields(self) -> None:
        """Test that v1.0.0 payload gets missing v1.0.1 fields."""
        # Simulate an old DecisionTrace-like payload
        payload = {
            "schema_version": "1.0.0",
            "run_id": "run_123",
            "tool_calls": [
                {"capability_name": "test@v1", "status": "success"},
            ],
        }
        result = upcast(payload, "1.0.1")

        # Should add workflow_id
        assert "workflow_id" in result
        assert result["workflow_id"] == "run_123"  # Defaults to run_id

        # Should add llm_calls
        assert "llm_calls" in result
        assert result["llm_calls"] == []

        # Should add kernel_version
        assert "kernel_version" in result

        # Tool calls should have new policy fields
        tc = result["tool_calls"][0]
        assert "effective_side_effect" in tc
        assert "effective_requires_approval" in tc

    def test_upcast_preserves_existing_data(self) -> None:
        """Test that upcast preserves existing data."""
        payload = {
            "schema_version": "1.0.0",
            "run_id": "run_xyz",
            "intent": "Test intent",
            "custom_field": "preserved",
        }
        result = upcast(payload, "1.0.1")

        assert result["intent"] == "Test intent"
        assert result["custom_field"] == "preserved"
        assert result["schema_version"] == "1.0.1"

    def test_upcast_updates_version(self) -> None:
        """Test that upcast updates the schema version."""
        payload = {"schema_version": "1.0.0"}
        result = upcast(payload, "1.0.1")
        assert result["schema_version"] == "1.0.1"

    def test_upcast_unknown_version_fails(self) -> None:
        """Test that upcast fails for unknown version paths."""
        payload = {"schema_version": "0.1.0"}
        with pytest.raises(ValueError, match="No upcaster chain"):
            upcast(payload, "1.0.1")
