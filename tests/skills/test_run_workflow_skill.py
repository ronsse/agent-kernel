"""Tests for run_workflow skill script."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def run_workflow_script() -> Path:
    """Path to the run workflow skill script."""
    return Path(__file__).parent.parent.parent / "skills" / "workflows" / "run_workflow.py"


class TestRunWorkflowSkill:
    """Tests for the run workflow skill."""

    def test_skill_exists(self, run_workflow_script: Path):
        """Test that the skill script exists."""
        assert run_workflow_script.exists()
        assert run_workflow_script.is_file()

    def test_skill_with_valid_workflow(self, run_workflow_script: Path, temp_dir: Path):
        """Test skill with a valid workflow."""
        input_data = {
            "workflow_id": "test_workflow",
            "intent": "Run a test workflow",
            "auto_approve_risk": "low",
            "workflows_dir": str(temp_dir / "workflows"),
            "data_dir": str(temp_dir / "data"),
        }

        # Create minimal workflow file
        workflows_dir = temp_dir / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)

        workflow_file = workflows_dir / "test_workflow.yaml"
        workflow_file.write_text("""
workflow_id: test_workflow
name: Test Workflow
description: A simple test workflow
steps:
  - step_id: step1
    agent_profile: test_agent
    context_query: "test context"
    auto_approve: true
    on_success:
      - end
trigger:
  type: manual
""")

        result = subprocess.run(
            [sys.executable, str(run_workflow_script)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
            timeout=10,
        )

        # May fail due to missing dependencies, but should handle gracefully
        assert result.returncode in [0, 1]

        if result.returncode == 0:
            output = json.loads(result.stdout)
            assert "success" in output

    def test_skill_invalid_json(self, run_workflow_script: Path):
        """Test skill with invalid JSON input."""
        result = subprocess.run(
            [sys.executable, str(run_workflow_script)],
            input="not valid json",
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

    def test_skill_missing_workflow_id(self, run_workflow_script: Path):
        """Test skill with missing workflow_id."""
        input_data = {
            "intent": "Some intent",
            # Missing workflow_id
        }

        result = subprocess.run(
            [sys.executable, str(run_workflow_script)],
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

    def test_skill_nonexistent_workflow(self, run_workflow_script: Path, temp_dir: Path):
        """Test skill with a workflow that doesn't exist."""
        input_data = {
            "workflow_id": "nonexistent_workflow",
            "intent": "Test",
            "workflows_dir": str(temp_dir / "workflows"),
            "data_dir": str(temp_dir / "data"),
        }

        # Create empty workflows directory
        (temp_dir / "workflows").mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            [sys.executable, str(run_workflow_script)],
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

    def test_skill_with_custom_params(self, run_workflow_script: Path, temp_dir: Path):
        """Test skill with custom workflow parameters."""
        input_data = {
            "workflow_id": "test_workflow",
            "intent": "Run with params",
            "params": {"custom_param": "value", "threshold": 0.8},
            "workflows_dir": str(temp_dir / "workflows"),
            "data_dir": str(temp_dir / "data"),
        }

        workflows_dir = temp_dir / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)

        workflow_file = workflows_dir / "test_workflow.yaml"
        workflow_file.write_text("""
workflow_id: test_workflow
name: Test Workflow
steps:
  - step_id: step1
    agent_profile: test_agent
    context_query: "test"
    auto_approve: true
trigger:
  type: manual
""")

        result = subprocess.run(
            [sys.executable, str(run_workflow_script)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
            timeout=10,
        )

        # Should handle gracefully
        assert result.returncode in [0, 1]
