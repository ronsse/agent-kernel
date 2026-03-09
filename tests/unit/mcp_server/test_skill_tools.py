"""Tests for skill MCP tools."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from agent_kernel.core.schemas.skill import (
    SkillLoadResult,
    SkillManifest,
    SkillOrigin,
    SkillResourceRef,
)
from agent_kernel.mcp_server.server import StoreBundle
from agent_kernel.mcp_server.tools.skill import register_skill_tools


class FakeMCP:
    def __init__(self):
        self._tools = {}

    def tool(self, name=None, description=None, **kwargs):
        def decorator(fn):
            self._tools[name] = fn
            return fn
        return decorator

    def get_tool(self, name):
        return self._tools[name]


def _make_manifest(skill_id="test-skill", name="Test Skill"):
    return SkillManifest(
        skill_id=skill_id,
        name=name,
        description="A test skill",
        origin=SkillOrigin(
            kind="local",
            path="/skills/test-skill",
            installed_at=datetime(2026, 1, 1, tzinfo=UTC),
            content_hash="abc123",
        ),
        metadata={"category": "testing"},
    )


def _make_stores(skill_store=None):
    stores = MagicMock(spec=StoreBundle)
    stores.skill_store = skill_store
    return stores


class TestSkillSearch:
    def test_search_returns_manifests(self):
        skill = MagicMock()
        skill.search_sync.return_value = [_make_manifest()]
        stores = _make_stores(skill_store=skill)

        mcp = FakeMCP()
        register_skill_tools(mcp, stores)

        result = mcp.get_tool("skill_search")(query="test")

        assert len(result["skills"]) == 1
        assert result["skills"][0]["skill_id"] == "test-skill"
        assert result["skills"][0]["name"] == "Test Skill"

    def test_search_no_skill_store(self):
        stores = _make_stores(skill_store=None)

        mcp = FakeMCP()
        register_skill_tools(mcp, stores)

        result = mcp.get_tool("skill_search")(query="test")

        assert result["skills"] == []
        assert "error" in result


class TestSkillLoad:
    def test_load_returns_content(self):
        skill = MagicMock()
        manifest = _make_manifest()
        skill.load_sync.return_value = SkillLoadResult(
            manifest=manifest,
            resources=[
                SkillResourceRef(
                    path="SKILL.md",
                    kind="skill_md",
                    hash="abc",
                    bytes=100,
                ),
            ],
            files={"SKILL.md": "# Test Skill\n\nDo the thing."},
        )
        stores = _make_stores(skill_store=skill)

        mcp = FakeMCP()
        register_skill_tools(mcp, stores)

        result = mcp.get_tool("skill_load")(skill_id="test-skill")

        assert result["manifest"]["skill_id"] == "test-skill"
        assert "SKILL.md" in result["files"]
        assert len(result["resources"]) == 1

    def test_load_not_found(self):
        skill = MagicMock()
        skill.load_sync.return_value = None
        stores = _make_stores(skill_store=skill)

        mcp = FakeMCP()
        register_skill_tools(mcp, stores)

        result = mcp.get_tool("skill_load")(skill_id="nonexistent")

        assert "error" in result


class TestSkillList:
    def test_list_all_skills(self):
        skill = MagicMock()
        skill.list_manifests_sync.return_value = [
            _make_manifest("skill-a", "Skill A"),
            _make_manifest("skill-b", "Skill B"),
        ]
        stores = _make_stores(skill_store=skill)

        mcp = FakeMCP()
        register_skill_tools(mcp, stores)

        result = mcp.get_tool("skill_list")()

        assert len(result["skills"]) == 2
        assert result["skills"][0]["skill_id"] == "skill-a"
        assert result["skills"][1]["skill_id"] == "skill-b"
