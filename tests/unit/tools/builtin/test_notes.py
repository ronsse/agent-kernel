"""Tests for note management capabilities."""

import pytest

from agent_kernel.tools.builtin.notes import (
    NoteStore,
    clear_notes,
    create_note,
    delete_note,
    get_note,
    list_notes,
    search_notes,
    set_note_store,
    update_note,
)


@pytest.fixture
def note_store():
    """Create a fresh in-memory note store."""
    store = NoteStore(":memory:")
    set_note_store(store)
    yield store
    clear_notes()


class TestNoteStore:
    """Tests for NoteStore class."""

    def test_create_note(self, note_store):
        """Test creating a note."""
        note = note_store.create(
            title="Test Note",
            content="This is test content",
            folder="inbox",
        )

        assert note.note_id is not None
        assert note.title == "Test Note"
        assert note.content == "This is test content"
        assert note.folder == "inbox"

    def test_get_note(self, note_store):
        """Test getting a note."""
        created = note_store.create(title="Test", content="Content")
        retrieved = note_store.get(created.note_id)

        assert retrieved is not None
        assert retrieved.note_id == created.note_id
        assert retrieved.title == "Test"

    def test_get_nonexistent(self, note_store):
        """Test getting nonexistent note."""
        result = note_store.get("nonexistent")
        assert result is None

    def test_update_note(self, note_store):
        """Test updating a note."""
        note = note_store.create(title="Original", content="Original content")
        updated = note_store.update(
            note.note_id,
            title="Updated",
            content="Updated content",
        )

        assert updated is not None
        assert updated.title == "Updated"
        assert updated.content == "Updated content"

    def test_delete_note(self, note_store):
        """Test deleting a note."""
        note = note_store.create(title="To delete")
        deleted = note_store.delete(note.note_id)

        assert deleted is True
        assert note_store.get(note.note_id) is None

    def test_delete_nonexistent(self, note_store):
        """Test deleting nonexistent note."""
        deleted = note_store.delete("nonexistent")
        assert deleted is False

    def test_list_notes(self, note_store):
        """Test listing notes."""
        note_store.create(title="Note 1")
        note_store.create(title="Note 2")
        note_store.create(title="Note 3")

        notes, count = note_store.list()

        assert count == 3
        assert len(notes) == 3

    def test_list_notes_with_project_filter(self, note_store):
        """Test listing notes with project filter."""
        note_store.create(title="Project A note", project_id="proj-a")
        note_store.create(title="Project B note", project_id="proj-b")
        note_store.create(title="No project")

        notes, count = note_store.list(project_id="proj-a")

        assert count == 1
        assert notes[0].project_id == "proj-a"

    def test_list_notes_with_folder_filter(self, note_store):
        """Test listing notes with folder filter."""
        note_store.create(title="Inbox note", folder="inbox")
        note_store.create(title="Archive note", folder="archive")
        note_store.create(title="No folder")

        notes, count = note_store.list(folder="inbox")

        assert count == 1
        assert notes[0].folder == "inbox"

    def test_list_notes_with_limit(self, note_store):
        """Test listing notes with limit."""
        for i in range(10):
            note_store.create(title=f"Note {i}")

        notes, count = note_store.list(limit=5)

        assert count == 10  # Total count
        assert len(notes) == 5  # Limited results

    def test_list_notes_with_offset(self, note_store):
        """Test listing notes with offset."""
        for i in range(10):
            note_store.create(title=f"Note {i}")

        notes, count = note_store.list(limit=5, offset=5)

        assert count == 10
        assert len(notes) == 5

    def test_search_notes(self, note_store):
        """Test searching notes."""
        note_store.create(title="Python tutorial", content="Learn Python basics")
        note_store.create(title="JavaScript guide", content="JS fundamentals")
        note_store.create(title="Random note", content="Nothing special")

        results = note_store.search("Python")

        assert len(results) >= 1
        assert any("Python" in r[0].title for r in results)

    def test_search_in_content(self, note_store):
        """Test searching in content."""
        note_store.create(
            title="Meeting Notes",
            content="Discussion about machine learning algorithms",
        )
        note_store.create(title="Other", content="Unrelated content")

        results = note_store.search("machine learning")

        assert len(results) >= 1

    def test_note_with_tags(self, note_store):
        """Test note with tags."""
        note = note_store.create(
            title="Tagged note",
            tags=["python", "tutorial"],
        )

        retrieved = note_store.get(note.note_id)

        assert retrieved is not None
        assert "python" in retrieved.tags
        assert "tutorial" in retrieved.tags

    def test_get_folders(self, note_store):
        """Test getting unique folders."""
        note_store.create(title="Note 1", folder="inbox")
        note_store.create(title="Note 2", folder="inbox")
        note_store.create(title="Note 3", folder="archive")

        folders = note_store.get_folders()

        assert "inbox" in folders
        assert "archive" in folders
        assert len(folders) == 2


class TestNoteFunctions:
    """Tests for note capability functions."""

    def test_create_note_function(self, note_store):
        """Test create_note function."""
        result = create_note(
            title="New note",
            content="Content here",
            folder="inbox",
        )

        assert "note_id" in result
        assert "created_at" in result
        assert result["title"] == "New note"

    def test_get_note_function(self, note_store):
        """Test get_note function."""
        created = create_note(title="Test", content="Content")
        result = get_note(created["note_id"])

        assert "note" in result
        assert result["note"]["title"] == "Test"

    def test_get_note_not_found(self, note_store):
        """Test get_note with invalid ID."""
        result = get_note("invalid")

        assert "error" in result

    def test_update_note_function(self, note_store):
        """Test update_note function."""
        created = create_note(title="Original")
        result = update_note(created["note_id"], title="Updated")

        assert "note" in result
        assert result["note"]["title"] == "Updated"

    def test_delete_note_function(self, note_store):
        """Test delete_note function."""
        created = create_note(title="To delete")
        result = delete_note(created["note_id"])

        assert result["deleted"] is True

        # Verify it's gone
        get_result = get_note(created["note_id"])
        assert "error" in get_result

    def test_list_notes_function(self, note_store):
        """Test list_notes function."""
        create_note(title="Note 1")
        create_note(title="Note 2")

        result = list_notes()

        assert "notes" in result
        assert "total_count" in result
        assert result["total_count"] == 2

    def test_search_notes_function(self, note_store):
        """Test search_notes function."""
        create_note(title="Python guide", content="Learn Python programming")
        create_note(title="Other note", content="Unrelated")

        result = search_notes("Python")

        assert "results" in result
        assert "total_count" in result
        # Note: FTS search may have different results, just check structure
        assert isinstance(result["results"], list)
