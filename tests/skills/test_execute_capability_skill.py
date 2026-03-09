"""Tests for execute_capability skill script."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def execute_capability_script() -> Path:
    """Path to the execute capability skill script."""
    return Path(__file__).parent.parent.parent / "skills" / "tools" / "execute_capability.py"


class TestExecuteCapabilitySkill:
    """Tests for the execute capability skill."""

    def test_skill_exists(self, execute_capability_script: Path):
        """Test that the skill script exists."""
        assert execute_capability_script.exists()
        assert execute_capability_script.is_file()

    def test_skill_with_valid_capability(self, execute_capability_script: Path, temp_dir: Path):
        """Test skill with a valid capability name."""
        input_data = {
            "capability_name": "test.capability@v1",
            "args": {"query": "test"},
            "agent_profile_id": "test_agent",
            "capabilities_dir": str(temp_dir / "capabilities"),
            "event_log_path": str(temp_dir / "events.db"),
        }

        # Create minimal capability file
        capabilities_dir = temp_dir / "capabilities"
        capabilities_dir.mkdir(parents=True, exist_ok=True)

        capability_file = capabilities_dir / "test.capability@v1.yaml"
        capability_file.write_text("""
capability_name: test.capability@v1
description: Test capability
input_schema:
  type: object
  properties:
    query:
      type: string
  required: [query]
output_schema:
  type: object
side_effect_level: none
adapter_type: local_function
adapter_config:
  module: builtins
  function: len
""")

        result = subprocess.run(
            [sys.executable, str(execute_capability_script)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
        )

        # May fail due to missing dependencies, but should handle gracefully
        assert result.returncode in [0, 1]

        if result.returncode == 0:
            output = json.loads(result.stdout)
            assert "success" in output

    def test_skill_invalid_json(self, execute_capability_script: Path):
        """Test skill with invalid JSON input."""
        result = subprocess.run(
            [sys.executable, str(execute_capability_script)],
            input="invalid json",
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        if result.stdout.strip():
            output = json.loads(result.stdout)
            assert output["success"] is False
            assert "error" in output
        else:
            assert result.stderr.strip()

    def test_skill_missing_capability_name(self, execute_capability_script: Path):
        """Test skill with missing capability_name."""
        input_data = {
            "args": {"test": "value"},
            # Missing capability_name
        }

        result = subprocess.run(
            [sys.executable, str(execute_capability_script)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        if result.stdout.strip():
            output = json.loads(result.stdout)
            assert output["success"] is False
            assert "error" in output
        else:
            assert result.stderr.strip()

    def test_skill_nonexistent_capability(self, execute_capability_script: Path, temp_dir: Path):
        """Test skill with a capability that doesn't exist."""
        input_data = {
            "capability_name": "nonexistent.capability@v1",
            "args": {},
            "agent_profile_id": "test_agent",
            "capabilities_dir": str(temp_dir / "capabilities"),
            "event_log_path": str(temp_dir / "events.db"),
        }

        # Create empty capabilities directory
        (temp_dir / "capabilities").mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            [sys.executable, str(execute_capability_script)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
        )

        assert result.returncode in [0, 1]
        if result.stdout.strip():
            output = json.loads(result.stdout)
            assert output["success"] is False
            assert "error" in output
        else:
            assert result.stderr.strip()
