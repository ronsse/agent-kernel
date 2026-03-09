"""Unit tests for VaultIndexer."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.services.index_state import EntityIndexState, IndexStateStore
from agent_kernel.services.vault_indexer import (
    IndexResult,
    IndexSummary,
    VaultIndexer,
)
from agent_kernel.tools.builtin.obsidian import ObsidianVault


class TestVaultIndexer:
    """Tests for VaultIndexer."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        # Create a temporary directory for the vault
        self.temp_dir = tempfile.mkdtemp()
        self.vault_path = Path(self.temp_dir)

        # Create a sample note
        self.sample_note_path = self.vault_path / "test-note.md"
        self.sample_note_path.write_text(
            "---\ntags: [test]\n---\n\n# Test Note\n\nThis is test content."
        )

        # Create a note with existing ID
        self.note_with_id_path = self.vault_path / "with-id.md"
        self.note_with_id_path.write_text(
            "---\nid: note_existing123\ntags: [test]\n---\n\n# With ID\n\nContent."
        )

        # Create vault
        self.vault = ObsidianVault(self.vault_path)

    def teardown_method(self) -> None:
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init(self) -> None:
        """Test VaultIndexer initialization."""
        indexer = VaultIndexer(vault=self.vault)

        assert indexer.vault == self.vault
        assert indexer.document_store is None
        assert indexer.graph_store is None
        assert indexer.vector_store is None
        assert indexer.chunk_size == 500
        assert indexer.chunk_overlap == 50

    def test_compute_content_hash(self) -> None:
        """Test content hash computation."""
        indexer = VaultIndexer(vault=self.vault)

        hash1 = indexer._compute_content_hash("test content")
        hash2 = indexer._compute_content_hash("test content")
        hash3 = indexer._compute_content_hash("different content")

        assert hash1 == hash2
        assert hash1 != hash3
        assert len(hash1) == 16

    def test_get_stable_id_existing(self) -> None:
        """Test getting stable ID from existing frontmatter."""
        indexer = VaultIndexer(vault=self.vault)
        note = self.vault.read_note("with-id.md")

        note_id, was_generated = indexer._get_stable_id(note)

        assert note_id == "note_existing123"
        assert was_generated is False

    def test_get_stable_id_generate(self) -> None:
        """Test generating stable ID for note without one."""
        indexer = VaultIndexer(vault=self.vault)
        note = self.vault.read_note("test-note.md")

        note_id, was_generated = indexer._get_stable_id(note)

        assert note_id.startswith("note_")
        assert len(note_id) > 10
        assert was_generated is True

    def test_inject_stable_id(self) -> None:
        """Test injecting stable ID into note."""
        indexer = VaultIndexer(vault=self.vault)

        success = indexer._inject_stable_id("test-note.md", "note_injected123")

        assert success is True

        # Re-read note and verify ID
        note = self.vault.read_note("test-note.md")
        assert note.frontmatter.get("id") == "note_injected123"

    def test_inject_stable_id_no_frontmatter(self) -> None:
        """Test injecting ID into note without frontmatter."""
        # Create note without frontmatter
        no_fm_path = self.vault_path / "no-frontmatter.md"
        no_fm_path.write_text("# No Frontmatter\n\nJust content.")

        indexer = VaultIndexer(vault=self.vault)

        success = indexer._inject_stable_id("no-frontmatter.md", "note_new123")

        assert success is True

        # Verify
        content = no_fm_path.read_text()
        assert "id: note_new123" in content
        assert "# No Frontmatter" in content

    def test_chunk_content(self) -> None:
        """Test content chunking."""
        indexer = VaultIndexer(vault=self.vault, chunk_size=100, chunk_overlap=10)

        content = "First paragraph with some content.\n\n" * 5

        chunks = indexer._chunk_content(content)

        assert len(chunks) > 0
        for chunk in chunks:
            assert "text" in chunk
            assert "start_offset" in chunk
            assert "end_offset" in chunk
            assert len(chunk["text"]) <= 200  # Allow some flexibility

    def test_index_note_basic(self) -> None:
        """Test basic note indexing without stores."""
        indexer = VaultIndexer(vault=self.vault)

        result = asyncio.run(indexer.index_note("test-note.md"))

        assert isinstance(result, IndexResult)
        assert result.path == "test-note.md"
        assert result.action in ["created", "updated"]
        assert result.error is None

    def test_index_note_not_found(self) -> None:
        """Test indexing non-existent note."""
        indexer = VaultIndexer(vault=self.vault)

        result = asyncio.run(indexer.index_note("nonexistent.md"))

        assert result.action == "error"
        assert result.error == "Note not found"

    def test_index_note_unchanged(self) -> None:
        """Test that unchanged notes are skipped."""
        indexer = VaultIndexer(vault=self.vault)

        # First index
        result1 = asyncio.run(indexer.index_note("test-note.md"))
        assert result1.action in ["created", "updated"]

        # Second index should be unchanged
        result2 = asyncio.run(indexer.index_note("test-note.md"))
        assert result2.action == "unchanged"

    def test_index_note_force(self) -> None:
        """Test forced re-indexing."""
        indexer = VaultIndexer(vault=self.vault)

        # First index
        asyncio.run(indexer.index_note("test-note.md"))

        # Forced second index
        result = asyncio.run(indexer.index_note("test-note.md", force=True))
        assert result.action == "updated"

    def test_skip_enrichment_when_already_enriched(self) -> None:
        """Skip enrichment if content unchanged and already enriched."""
        index_state_store = IndexStateStore(self.vault_path / "index_state.db")
        mock_enrichment = MagicMock()
        mock_enrichment.enrich_entity = AsyncMock()
        indexer = VaultIndexer(
            vault=self.vault,
            index_state_store=index_state_store,
            enrichment_service=mock_enrichment,
            enable_enrichment=True,
        )

        note = self.vault.read_note("with-id.md")
        assert note is not None

        content_hash = indexer._compute_content_hash(note.content)
        index_state_store.save(
            EntityIndexState(
                entity_id="note_existing123",
                entity_type="note",
                source_path="with-id.md",
                content_hash=content_hash,
                enriched_at=utc_now(),
            )
        )

        result = asyncio.run(indexer.index_note("with-id.md"))

        assert result.enriched is False
        mock_enrichment.enrich_entity.assert_not_called()
        index_state_store.close()

    def test_index_note_with_document_store(self) -> None:
        """Test indexing with document store."""
        mock_doc_store = MagicMock()
        mock_doc_store.get = MagicMock(return_value=None)
        mock_doc_store.put = MagicMock()
        mock_doc_store.store = None  # Prevent MagicMock auto-attr

        indexer = VaultIndexer(vault=self.vault, document_store=mock_doc_store)

        result = asyncio.run(indexer.index_note("test-note.md"))

        assert result.action in ["created", "updated"]
        mock_doc_store.put.assert_called_once()

    def test_index_note_with_graph_store(self) -> None:
        """Test indexing with graph store."""
        mock_graph_store = MagicMock()
        mock_graph_store.upsert_node = MagicMock()
        mock_graph_store.upsert_edge = MagicMock()
        mock_graph_store.get_edges = MagicMock(return_value=[])
        mock_graph_store.delete_edges_from_source = MagicMock(return_value=0)

        indexer = VaultIndexer(vault=self.vault, graph_store=mock_graph_store)

        result = asyncio.run(indexer.index_note("test-note.md"))

        assert result.action in ["created", "updated"]
        assert result.graph_updated is True
        assert mock_graph_store.upsert_node.called

    def test_index_folder(self) -> None:
        """Test folder indexing."""
        # Create subfolder with notes
        subfolder = self.vault_path / "subfolder"
        subfolder.mkdir()
        (subfolder / "sub-note.md").write_text("# Sub Note\n\nContent.")

        indexer = VaultIndexer(vault=self.vault)

        summary = asyncio.run(indexer.index_folder())

        assert isinstance(summary, IndexSummary)
        assert summary.total_notes >= 3  # Original + with-id + sub-note
        assert summary.errors == 0
        assert summary.completed_at is not None

    def test_index_folder_specific(self) -> None:
        """Test indexing specific folder."""
        # Create subfolder with notes
        subfolder = self.vault_path / "specific"
        subfolder.mkdir()
        (subfolder / "note1.md").write_text("# Note 1\n\nContent.")
        (subfolder / "note2.md").write_text("# Note 2\n\nContent.")

        indexer = VaultIndexer(vault=self.vault)

        summary = asyncio.run(indexer.index_folder(folder="specific"))

        assert summary.total_notes == 2

    def test_index_changed(self) -> None:
        """Test indexing specific changed paths."""
        indexer = VaultIndexer(vault=self.vault)

        summary = asyncio.run(
            indexer.index_changed(["test-note.md", "with-id.md"])
        )

        assert summary.total_notes == 2
        assert summary.errors == 0


class TestIndexResult:
    """Tests for IndexResult dataclass."""

    def test_to_dict(self) -> None:
        """Test IndexResult to_dict conversion."""
        result = IndexResult(
            note_id="note_123",
            path="test.md",
            action="created",
            graph_updated=True,
            vector_updated=False,
            stable_id_added=True,
        )

        d = result.to_dict()

        assert d["note_id"] == "note_123"
        assert d["path"] == "test.md"
        assert d["action"] == "created"
        assert d["graph_updated"] is True
        assert d["vector_updated"] is False
        assert d["stable_id_added"] is True
        assert d["error"] is None


class TestIndexSummary:
    """Tests for IndexSummary dataclass."""

    def test_to_dict(self) -> None:
        """Test IndexSummary to_dict conversion."""
        from datetime import datetime

        summary = IndexSummary(
            started_at=datetime(2026, 1, 14, 10, 0, 0),
            completed_at=datetime(2026, 1, 14, 10, 0, 5),
            total_notes=10,
            created=3,
            updated=5,
            unchanged=2,
            errors=0,
        )

        d = summary.to_dict()

        assert d["total_notes"] == 10
        assert d["created"] == 3
        assert d["updated"] == 5
        assert d["unchanged"] == 2
        assert d["errors"] == 0
        assert "2026-01-14" in d["started_at"]
        assert "2026-01-14" in d["completed_at"]
