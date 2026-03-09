"""Tests for SkillGraphSync - skill manifest → knowledge graph sync."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from agent_kernel.core.schemas.graph import NodeType
from agent_kernel.core.schemas.skill import SkillManifest, SkillOrigin
from agent_kernel.skills.graph_sync import SkillGraphSync


def _make_manifest(
    skill_id: str = "test-skill",
    name: str = "Test Skill",
    description: str = "A test skill",
    content_hash: str = "abc123",
) -> SkillManifest:
    return SkillManifest(
        skill_id=skill_id,
        name=name,
        description=description,
        origin=SkillOrigin(
            kind="local",
            path="/skills/test-skill",
            installed_at=datetime(2026, 1, 1, tzinfo=UTC),
            content_hash=content_hash,
        ),
        metadata={"category": "testing"},
    )


class TestSkillGraphSync:
    """Tests for SkillGraphSync."""

    def test_sync_creates_node(self):
        """sync_skill creates a SKILL node in the graph."""
        skill_store = MagicMock()
        graph_store = MagicMock()
        graph_store.get_node.return_value = None  # No existing node

        sync = SkillGraphSync(skill_store, graph_store)
        manifest = _make_manifest()

        node_id = sync.sync_skill(manifest)

        assert node_id == "skill_test-skill"
        graph_store.upsert_node.assert_called_once()
        call_args = graph_store.upsert_node.call_args[0][0]
        assert call_args["node_id"] == "skill_test-skill"
        assert call_args["node_type"] == NodeType.SKILL.value
        assert call_args["properties"]["title"] == "Test Skill"
        assert call_args["properties"]["content_hash"] == "abc123"
        assert call_args["properties"]["skill_id"] == "test-skill"

    def test_sync_idempotent_on_same_hash(self):
        """sync_skill skips update when content_hash is unchanged."""
        skill_store = MagicMock()
        graph_store = MagicMock()
        graph_store.get_node.return_value = {
            "node_id": "skill_test-skill",
            "properties": {"content_hash": "abc123"},
        }

        sync = SkillGraphSync(skill_store, graph_store)
        manifest = _make_manifest(content_hash="abc123")

        node_id = sync.sync_skill(manifest)

        assert node_id is None  # No update needed
        graph_store.upsert_node.assert_not_called()

    def test_sync_updates_on_hash_change(self):
        """sync_skill updates node when content_hash has changed."""
        skill_store = MagicMock()
        graph_store = MagicMock()
        graph_store.get_node.return_value = {
            "node_id": "skill_test-skill",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "properties": {"content_hash": "old_hash"},
        }

        sync = SkillGraphSync(skill_store, graph_store)
        manifest = _make_manifest(content_hash="new_hash")

        node_id = sync.sync_skill(manifest)

        assert node_id == "skill_test-skill"
        graph_store.upsert_node.assert_called_once()
        call_args = graph_store.upsert_node.call_args[0][0]
        assert call_args["properties"]["content_hash"] == "new_hash"

    def test_sync_all_creates_multiple_nodes(self):
        """sync_all creates nodes for all manifests."""
        manifests = [
            _make_manifest(skill_id="skill-a", content_hash="hash_a"),
            _make_manifest(skill_id="skill-b", content_hash="hash_b"),
        ]
        skill_store = MagicMock()
        skill_store.list_manifests_sync.return_value = manifests

        graph_store = MagicMock()
        graph_store.get_node.return_value = None  # All new
        graph_store.query.return_value = []  # No stale

        sync = SkillGraphSync(skill_store, graph_store)
        count = sync.sync_all()

        assert count == 2
        assert graph_store.upsert_node.call_count == 2

    def test_remove_stale_deletes_orphaned_nodes(self):
        """remove_stale removes graph nodes for deleted skills."""
        manifests = [_make_manifest(skill_id="skill-a")]
        skill_store = MagicMock()
        skill_store.list_manifests_sync.return_value = manifests

        graph_store = MagicMock()
        graph_store.query.return_value = [
            {"node_id": "skill_skill-a"},  # Still exists
            {"node_id": "skill_deleted-skill"},  # Stale
        ]

        sync = SkillGraphSync(skill_store, graph_store)
        removed = sync.remove_stale()

        assert removed == 1
        graph_store.delete_node.assert_called_once_with("skill_deleted-skill")

    def test_remove_stale_no_orphans(self):
        """remove_stale does nothing when all nodes are current."""
        manifests = [
            _make_manifest(skill_id="skill-a"),
            _make_manifest(skill_id="skill-b"),
        ]
        skill_store = MagicMock()
        skill_store.list_manifests_sync.return_value = manifests

        graph_store = MagicMock()
        graph_store.query.return_value = [
            {"node_id": "skill_skill-a"},
            {"node_id": "skill_skill-b"},
        ]

        sync = SkillGraphSync(skill_store, graph_store)
        removed = sync.remove_stale()

        assert removed == 0
        graph_store.delete_node.assert_not_called()

    def test_sync_includes_metadata(self):
        """sync_skill includes manifest metadata in node properties."""
        skill_store = MagicMock()
        graph_store = MagicMock()
        graph_store.get_node.return_value = None

        sync = SkillGraphSync(skill_store, graph_store)
        manifest = _make_manifest()

        sync.sync_skill(manifest)

        call_args = graph_store.upsert_node.call_args[0][0]
        assert call_args["properties"]["metadata"] == {"category": "testing"}
        assert call_args["label"] == "Test Skill"
        assert call_args["uri"] == "/skills/test-skill"

    def test_sync_includes_allowed_tools(self):
        """sync_skill stores allowed_tools in properties."""
        skill_store = MagicMock()
        graph_store = MagicMock()
        graph_store.get_node.return_value = None

        sync = SkillGraphSync(skill_store, graph_store)
        manifest = _make_manifest()
        manifest.allowed_tools = ["Read", "Write", "Bash"]

        sync.sync_skill(manifest)

        call_args = graph_store.upsert_node.call_args[0][0]
        assert call_args["properties"]["allowed_tools"] == ["Read", "Write", "Bash"]
