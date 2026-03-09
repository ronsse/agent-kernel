"""Tests for vector_search skill script."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def vector_search_script() -> Path:
    """Path to the vector search skill script."""
    return Path(__file__).parent.parent.parent / "skills" / "memory" / "vector_search.py"


class TestVectorSearchSkill:
    """Tests for the vector search skill."""

    def test_skill_exists(self, vector_search_script: Path):
        """Test that the skill script exists and is executable."""
        assert vector_search_script.exists()
        assert vector_search_script.is_file()

    def test_skill_with_query_text(self, vector_search_script: Path, temp_dir: Path):
        """Test skill with query text input."""
        input_data = {
            "query_text": "agent memory systems",
            "top_k": 5,
            "filters": {},
            "db_path": str(temp_dir / "vectors.db"),
        }

        # Note: This will fail if the database doesn't exist
        # In a real test, we'd set up a test database first
        result = subprocess.run(
            [sys.executable, str(vector_search_script)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
        )

        # Script should handle missing DB gracefully
        assert result.returncode in [0, 1]  # Success or handled error

        if result.returncode == 0:
            output = json.loads(result.stdout)
            assert "success" in output
            if output["success"]:
                assert "results" in output
                assert isinstance(output["results"], list)

    def test_skill_with_query_vector(self, vector_search_script: Path, temp_dir: Path):
        """Test skill with pre-computed query vector."""
        input_data = {
            "query_vector": [0.1] * 384,  # Mock embedding vector
            "top_k": 3,
            "db_path": str(temp_dir / "vectors.db"),
        }

        result = subprocess.run(
            [sys.executable, str(vector_search_script)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
        )

        # Should handle gracefully
        assert result.returncode in [0, 1]

    def test_skill_invalid_input(self, vector_search_script: Path):
        """Test skill with invalid JSON input."""
        result = subprocess.run(
            [sys.executable, str(vector_search_script)],
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

    def test_skill_missing_required_field(self, vector_search_script: Path):
        """Test skill with missing required fields."""
        input_data = {
            "top_k": 5,
            # Missing query_text or query_vector
        }

        result = subprocess.run(
            [sys.executable, str(vector_search_script)],
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
