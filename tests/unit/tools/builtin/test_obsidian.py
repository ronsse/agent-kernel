"""Tests for Obsidian vault integration."""

from datetime import datetime

import pytest

from agent_kernel.tools.builtin.obsidian import (
    ObsidianNote,
    ObsidianVault,
    obsidian_create,
    obsidian_daily,
    obsidian_list,
    obsidian_read,
    obsidian_search,
    obsidian_update,
    set_vault,
)


@pytest.fixture
def vault(tmp_path):
    """Create a test vault."""
    vault = ObsidianVault(tmp_path)
    set_vault(vault)
    return vault


@pytest.fixture
def sample_notes(vault, tmp_path):
    """Create sample notes in the vault."""
    # Create some test notes
    (tmp_path / "note1.md").write_text("# Note One\n\nThis is the first note.")
    (tmp_path / "note2.md").write_text(
        "---\ntags:\n  - important\n  - work\n---\n\n# Note Two\n\nAnother note."
    )

    # Create subfolder
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "project1.md").write_text(
        "# Project One\n\nProject documentation.\n\n[[note1]]"
    )

    return vault


class TestObsidianNote:
    """Tests for ObsidianNote."""

    def test_to_dict(self):
        """Test converting note to dict."""
        note = ObsidianNote(
            path="test.md",
            title="Test",
            content="Content",
            tags=["tag1"],
        )

        data = note.to_dict()

        assert data["path"] == "test.md"
        assert data["title"] == "Test"
        assert "tag1" in data["tags"]


class TestObsidianVault:
    """Tests for ObsidianVault."""

    def test_read_note(self, sample_notes, tmp_path):
        """Test reading a note."""
        note = sample_notes.read_note("note1.md")

        assert note is not None
        assert note.title == "Note One"
        assert "first note" in note.content

    def test_read_note_without_extension(self, sample_notes):
        """Test reading note without .md extension."""
        note = sample_notes.read_note("note1")

        assert note is not None
        assert note.title == "Note One"

    def test_read_nonexistent(self, vault):
        """Test reading nonexistent note."""
        note = vault.read_note("nonexistent")
        assert note is None

    def test_parse_frontmatter(self, sample_notes):
        """Test parsing frontmatter."""
        note = sample_notes.read_note("note2")

        assert note is not None
        assert "important" in note.tags
        assert "work" in note.tags

    def test_extract_links(self, sample_notes):
        """Test extracting wiki links."""
        note = sample_notes.read_note("Projects/project1")

        assert note is not None
        assert "note1" in note.links

    def test_create_note(self, vault, tmp_path):
        """Test creating a note."""
        note = vault.create_note(
            path="new_note",
            content="# New Note\n\nContent here.",
        )

        assert note is not None
        assert note.title == "New Note"
        assert (tmp_path / "new_note.md").exists()

    def test_create_note_with_frontmatter(self, vault, tmp_path):
        """Test creating note with frontmatter."""
        note = vault.create_note(
            path="with_frontmatter",
            content="# Note\n\nContent.",
            frontmatter={"tags": ["test"], "status": "draft"},
        )

        assert note is not None
        assert "test" in note.tags

        # Verify frontmatter in file
        content = (tmp_path / "with_frontmatter.md").read_text()
        assert "status: draft" in content

    def test_create_note_in_subfolder(self, vault, tmp_path):
        """Test creating note in subfolder."""
        vault.create_note(
            path="Subfolder/deep/note",
            content="Deep note",
        )

        assert (tmp_path / "Subfolder" / "deep" / "note.md").exists()

    def test_update_note(self, sample_notes):
        """Test updating a note."""
        updated = sample_notes.update_note(
            path="note1",
            content="# Updated Note\n\nNew content.",
        )

        assert updated is not None
        assert "New content" in updated.content

    def test_update_note_append(self, sample_notes):
        """Test appending to a note."""
        original = sample_notes.read_note("note1")
        updated = sample_notes.update_note(
            path="note1",
            content="Appended text.",
            append=True,
        )

        assert updated is not None
        assert "Appended text" in updated.content
        assert "first note" in updated.content  # Original still there

    def test_update_nonexistent(self, vault):
        """Test updating nonexistent note."""
        result = vault.update_note("nonexistent", content="test")
        assert result is None

    def test_delete_note(self, sample_notes, tmp_path):
        """Test deleting a note."""
        assert (tmp_path / "note1.md").exists()

        deleted = sample_notes.delete_note("note1")

        assert deleted is True
        assert not (tmp_path / "note1.md").exists()

    def test_delete_nonexistent(self, vault):
        """Test deleting nonexistent note."""
        deleted = vault.delete_note("nonexistent")
        assert deleted is False

    def test_list_notes(self, sample_notes):
        """Test listing notes."""
        notes = sample_notes.list_notes()

        assert len(notes) == 3
        assert any("note1" in n for n in notes)
        assert any("project1" in n for n in notes)

    def test_list_notes_folder(self, sample_notes):
        """Test listing notes in folder."""
        notes = sample_notes.list_notes(folder="Projects")

        assert len(notes) == 1
        assert "project1" in notes[0]

    def test_list_notes_non_recursive(self, sample_notes):
        """Test non-recursive listing."""
        notes = sample_notes.list_notes(recursive=False)

        assert len(notes) == 2  # Only top-level notes
        assert not any("project1" in n for n in notes)

    def test_search(self, sample_notes):
        """Test searching notes."""
        results = sample_notes.search("first")

        assert len(results) >= 1
        assert any("note1" in r[0].path for r in results)

    def test_search_in_folder(self, sample_notes):
        """Test searching in folder."""
        results = sample_notes.search("project", folder="Projects")

        assert len(results) >= 1
        assert all("Projects" in r[0].path for r in results)

    def test_daily_note(self, vault, tmp_path):
        """Test daily note creation."""
        today = datetime.now()

        # Initially no daily note
        note = vault.get_daily_note(today)
        assert note is None

        # Create daily note
        created = vault.create_daily_note(today)
        assert created is not None
        assert "daily" in created.frontmatter.get("type", "")

        # Now it exists
        note = vault.get_daily_note(today)
        assert note is not None


class TestCapabilityFunctions:
    """Tests for Obsidian capability functions."""

    def test_obsidian_read(self, sample_notes):
        """Test obsidian_read function."""
        result = obsidian_read("note1")

        assert "note" in result
        assert result["note"]["title"] == "Note One"

    def test_obsidian_read_not_found(self, vault):
        """Test obsidian_read with invalid path."""
        result = obsidian_read("nonexistent")

        assert "error" in result

    def test_obsidian_create(self, vault, tmp_path):
        """Test obsidian_create function."""
        result = obsidian_create(
            path="new_note",
            content="Test content",
            title="Test Title",
            tags=["test"],
        )

        assert "path" in result
        assert (tmp_path / "new_note.md").exists()

    def test_obsidian_search(self, sample_notes):
        """Test obsidian_search function."""
        result = obsidian_search("note")

        assert "results" in result
        assert result["total_count"] >= 1

    def test_obsidian_list(self, sample_notes):
        """Test obsidian_list function."""
        result = obsidian_list()

        assert "notes" in result
        assert result["total_count"] == 3

    def test_obsidian_daily(self, vault):
        """Test obsidian_daily function."""
        result = obsidian_daily()

        assert "note" in result
        assert result["note"]["path"].startswith("Daily/")

    def test_obsidian_update(self, sample_notes):
        """Test obsidian_update function."""
        result = obsidian_update(
            path="note1",
            content="Updated content",
            tags=["updated"],
        )

        assert "note" in result
        assert "updated" in result["note"]["tags"]

    def test_obsidian_update_skip_if_no_change(self, sample_notes):
        """Skip update when no changes are detected."""
        note = sample_notes.read_note("note1")
        result = obsidian_update(
            path="note1",
            content=note.content,
            skip_if_no_change=True,
        )

        assert result.get("skipped") is True
        assert result["note"]["content"] == note.content

    def test_obsidian_update_not_found(self, vault):
        """Test obsidian_update with invalid path."""
        result = obsidian_update("nonexistent", content="test")

        assert "error" in result
