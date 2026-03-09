"""Unit tests for IndexStateStore."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_kernel.services.index_state import (
    EntityIndexState,
    IndexStateStore,
    IndexStatus,
)


@pytest.fixture
def temp_db_path() -> Path:
    """Create a temporary database path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def store(temp_db_path: Path) -> IndexStateStore:
    """Create a test IndexStateStore."""
    store = IndexStateStore(temp_db_path)
    yield store
    store.close()
    temp_db_path.unlink(missing_ok=True)


class TestEntityIndexState:
    """Tests for EntityIndexState dataclass."""

    def test_default_status_is_pending(self) -> None:
        """Test that default status is pending."""
        state = EntityIndexState(entity_id="note_123", entity_type="note")
        assert state.doc_status == IndexStatus.PENDING
        assert state.graph_status == IndexStatus.PENDING
        assert state.vector_status == IndexStatus.PENDING

    def test_is_fully_indexed_false_when_pending(self) -> None:
        """Test is_fully_indexed is False when any store is pending."""
        state = EntityIndexState(entity_id="note_123", entity_type="note")
        assert state.is_fully_indexed is False

    def test_is_fully_indexed_true_when_all_indexed(self) -> None:
        """Test is_fully_indexed is True when all stores are indexed."""
        state = EntityIndexState(
            entity_id="note_123",
            entity_type="note",
            doc_status=IndexStatus.INDEXED,
            graph_status=IndexStatus.INDEXED,
            vector_status=IndexStatus.INDEXED,
        )
        assert state.is_fully_indexed is True

    def test_needs_indexing_true_when_pending(self) -> None:
        """Test needs_indexing is True when any store is pending."""
        state = EntityIndexState(entity_id="note_123", entity_type="note")
        assert state.needs_indexing is True

    def test_needs_indexing_true_when_stale(self) -> None:
        """Test needs_indexing is True when any store is stale."""
        state = EntityIndexState(
            entity_id="note_123",
            entity_type="note",
            doc_status=IndexStatus.INDEXED,
            graph_status=IndexStatus.STALE,
            vector_status=IndexStatus.INDEXED,
        )
        assert state.needs_indexing is True

    def test_to_dict(self) -> None:
        """Test conversion to dictionary."""
        state = EntityIndexState(entity_id="note_123", entity_type="note")
        result = state.to_dict()
        assert result["entity_id"] == "note_123"
        assert result["entity_type"] == "note"
        assert result["doc_status"] == "pending"
        assert "is_fully_indexed" in result
        assert "needs_indexing" in result


class TestIndexStateStore:
    """Tests for IndexStateStore."""

    def test_save_and_get(self, store: IndexStateStore) -> None:
        """Test saving and retrieving an index state."""
        state = EntityIndexState(
            entity_id="note_abc123",
            entity_type="note",
            source_path="notes/test.md",
            content_hash="abc123",
        )
        store.save(state)

        retrieved = store.get("note_abc123")
        assert retrieved is not None
        assert retrieved.entity_id == "note_abc123"
        assert retrieved.entity_type == "note"
        assert retrieved.source_path == "notes/test.md"
        assert retrieved.content_hash == "abc123"

    def test_get_nonexistent_returns_none(self, store: IndexStateStore) -> None:
        """Test that getting a nonexistent entity returns None."""
        result = store.get("nonexistent")
        assert result is None

    def test_get_by_path(self, store: IndexStateStore) -> None:
        """Test retrieving by source path."""
        state = EntityIndexState(
            entity_id="note_xyz",
            entity_type="note",
            source_path="projects/test.md",
        )
        store.save(state)

        retrieved = store.get_by_path("projects/test.md")
        assert retrieved is not None
        assert retrieved.entity_id == "note_xyz"

    def test_update_doc_status(self, store: IndexStateStore) -> None:
        """Test updating document status."""
        state = EntityIndexState(entity_id="note_1", entity_type="note")
        store.save(state)

        store.update_doc_status("note_1", IndexStatus.INDEXED)

        updated = store.get("note_1")
        assert updated is not None
        assert updated.doc_status == IndexStatus.INDEXED
        assert updated.doc_indexed_at is not None

    def test_update_graph_status(self, store: IndexStateStore) -> None:
        """Test updating graph status."""
        state = EntityIndexState(entity_id="note_2", entity_type="note")
        store.save(state)

        store.update_graph_status("note_2", IndexStatus.INDEXED)

        updated = store.get("note_2")
        assert updated is not None
        assert updated.graph_status == IndexStatus.INDEXED
        assert updated.graph_indexed_at is not None

    def test_update_vector_status(self, store: IndexStateStore) -> None:
        """Test updating vector status."""
        state = EntityIndexState(entity_id="note_3", entity_type="note")
        store.save(state)

        store.update_vector_status("note_3", IndexStatus.INDEXED)

        updated = store.get("note_3")
        assert updated is not None
        assert updated.vector_status == IndexStatus.INDEXED
        assert updated.vector_indexed_at is not None

    def test_mark_stale(self, store: IndexStateStore) -> None:
        """Test marking an entity as stale."""
        state = EntityIndexState(
            entity_id="note_4",
            entity_type="note",
            doc_status=IndexStatus.INDEXED,
            graph_status=IndexStatus.INDEXED,
            vector_status=IndexStatus.INDEXED,
        )
        store.save(state)

        store.mark_stale("note_4", "new_hash_xyz")

        updated = store.get("note_4")
        assert updated is not None
        assert updated.doc_status == IndexStatus.STALE
        assert updated.graph_status == IndexStatus.STALE
        assert updated.vector_status == IndexStatus.STALE
        assert updated.content_hash == "new_hash_xyz"

    def test_list_pending(self, store: IndexStateStore) -> None:
        """Test listing entities that need indexing."""
        # Create a mix of states
        store.save(EntityIndexState(
            entity_id="pending_1", entity_type="note"
        ))
        store.save(EntityIndexState(
            entity_id="indexed_1",
            entity_type="note",
            doc_status=IndexStatus.INDEXED,
            graph_status=IndexStatus.INDEXED,
            vector_status=IndexStatus.INDEXED,
        ))
        store.save(EntityIndexState(
            entity_id="stale_1",
            entity_type="note",
            doc_status=IndexStatus.STALE,
            graph_status=IndexStatus.INDEXED,
            vector_status=IndexStatus.INDEXED,
        ))

        pending = store.list_pending()
        entity_ids = [s.entity_id for s in pending]

        assert "pending_1" in entity_ids
        assert "stale_1" in entity_ids
        assert "indexed_1" not in entity_ids

    def test_list_fully_indexed(self, store: IndexStateStore) -> None:
        """Test listing fully indexed entities."""
        store.save(EntityIndexState(
            entity_id="full_1",
            entity_type="note",
            doc_status=IndexStatus.INDEXED,
            graph_status=IndexStatus.INDEXED,
            vector_status=IndexStatus.INDEXED,
        ))
        store.save(EntityIndexState(
            entity_id="partial_1",
            entity_type="note",
            doc_status=IndexStatus.INDEXED,
            graph_status=IndexStatus.PENDING,
            vector_status=IndexStatus.INDEXED,
        ))

        fully_indexed = store.list_fully_indexed()
        entity_ids = [s.entity_id for s in fully_indexed]

        assert "full_1" in entity_ids
        assert "partial_1" not in entity_ids

    def test_get_statistics(self, store: IndexStateStore) -> None:
        """Test getting indexing statistics."""
        store.save(EntityIndexState(entity_id="stat_1", entity_type="note"))
        store.save(EntityIndexState(
            entity_id="stat_2",
            entity_type="note",
            doc_status=IndexStatus.INDEXED,
            graph_status=IndexStatus.INDEXED,
            vector_status=IndexStatus.INDEXED,
        ))
        store.save(EntityIndexState(
            entity_id="stat_3",
            entity_type="task",
            doc_status=IndexStatus.FAILED,
        ))

        stats = store.get_statistics()

        assert stats["total"] == 3  # noqa: PLR2004
        assert stats["fully_indexed"] == 1
        assert stats["needs_indexing"] == 2  # pending and failed  # noqa: PLR2004
        assert stats["failed"] == 1
        assert "note" in stats["by_type"]
        assert "task" in stats["by_type"]

    def test_delete(self, store: IndexStateStore) -> None:
        """Test deleting an index state."""
        store.save(EntityIndexState(entity_id="to_delete", entity_type="note"))

        assert store.get("to_delete") is not None

        result = store.delete("to_delete")
        assert result is True

        assert store.get("to_delete") is None

    def test_delete_nonexistent(self, store: IndexStateStore) -> None:
        """Test deleting a nonexistent entity returns False."""
        result = store.delete("nonexistent")
        assert result is False

    def test_error_tracking(self, store: IndexStateStore) -> None:
        """Test error tracking when status is failed."""
        state = EntityIndexState(entity_id="error_test", entity_type="note")
        store.save(state)

        store.update_doc_status(
            "error_test",
            IndexStatus.FAILED,
            error="Connection timeout",
        )

        updated = store.get("error_test")
        assert updated is not None
        assert updated.doc_status == IndexStatus.FAILED
        assert updated.last_error == "Connection timeout"
        assert updated.error_count == 1

        # Another failure should increment error count
        store.update_doc_status(
            "error_test",
            IndexStatus.FAILED,
            error="Another error",
        )

        updated = store.get("error_test")
        assert updated is not None
        assert updated.error_count == 2  # noqa: PLR2004
