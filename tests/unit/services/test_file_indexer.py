"""Tests for FileIndexer service."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_kernel.services.file_indexer import (
    FileIndexer,
    FileIndexResult,
    FileIndexSummary,
)


@pytest.fixture
def tmp_dir():
    """Create a temporary directory with test files."""
    with tempfile.TemporaryDirectory() as d:
        # Create test files
        (Path(d) / "document.pdf").write_bytes(b"fake pdf content")
        (Path(d) / "spreadsheet.xlsx").write_bytes(b"fake xlsx content")
        (Path(d) / "notes.txt").write_text("Hello world from a text file")
        (Path(d) / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake")
        (Path(d) / "unsupported.xyz").write_bytes(b"unsupported")

        # Create subdirectory
        sub = Path(d) / "subdir"
        sub.mkdir()
        (sub / "nested.docx").write_bytes(b"fake docx content")

        yield d


@pytest.fixture
def mock_stores():
    """Create mock store instances."""
    doc_store = MagicMock()
    doc_store.store = MagicMock(return_value=None)

    graph_store = MagicMock()
    graph_store.upsert_node = MagicMock(return_value=None)
    graph_store.upsert_edge = MagicMock(return_value=None)

    vector_store = MagicMock()
    vector_store.upsert = MagicMock(return_value=None)

    embedding_service = AsyncMock()
    embedding_service.embed = AsyncMock(return_value=[0.1] * 384)

    return doc_store, graph_store, vector_store, embedding_service


@pytest.fixture
def mock_index_state():
    """Create a mock IndexStateStore."""
    store = MagicMock()
    store.get = MagicMock(return_value=None)
    store.get_by_path = MagicMock(return_value=None)
    store.save = MagicMock()
    store.mark_stale = MagicMock()
    store.update_doc_status = MagicMock()
    store.update_graph_status = MagicMock()
    store.update_vector_status = MagicMock()
    store.list_by_entity_type = MagicMock(return_value=[])
    return store


@pytest.fixture
def mock_extractor():
    """Create a mock ResourceExtractor."""
    from agent_kernel.services.resource_extraction import (
        ExtractionResult,
        ResourceMetadata,
        ResourceType,
    )

    extractor = MagicMock()
    extractor.get_resource_type = MagicMock(return_value=ResourceType.TEXT)

    async def fake_extract(path):
        p = Path(path)
        if p.suffix == ".txt":
            return ExtractionResult(
                metadata=ResourceMetadata(
                    file_path=str(p),
                    file_name=p.name,
                    resource_type=ResourceType.TEXT,
                ),
                raw_text=p.read_text() if p.exists() else "",
                extraction_method="text",
                success=True,
            )
        if p.suffix in (".pdf", ".docx", ".xlsx"):
            return ExtractionResult(
                metadata=ResourceMetadata(
                    file_path=str(p),
                    file_name=p.name,
                    resource_type=ResourceType.PDF if p.suffix == ".pdf" else ResourceType.WORD,
                ),
                raw_text=f"Extracted text from {p.name}",
                extraction_method="mock",
                success=True,
            )
        return ExtractionResult(
            metadata=ResourceMetadata(
                file_path=str(p),
                file_name=p.name,
                resource_type=ResourceType.UNKNOWN,
            ),
            success=False,
            error="unsupported",
        )

    extractor.extract = AsyncMock(side_effect=fake_extract)
    return extractor


class TestFileIndexer:
    """Tests for FileIndexer."""

    @pytest.mark.asyncio
    async def test_index_text_file(self, tmp_dir, mock_stores, mock_index_state, mock_extractor):
        doc_store, graph_store, vector_store, embedding_service = mock_stores

        indexer = FileIndexer(
            document_store=doc_store,
            graph_store=graph_store,
            vector_store=vector_store,
            embedding_service=embedding_service,
            index_state_store=mock_index_state,
            extractor=mock_extractor,
        )

        result = await indexer.index_file(Path(tmp_dir) / "notes.txt")

        assert result.action == "created"
        assert result.file_id.startswith("file_")
        assert result.graph_updated is True
        assert result.vector_updated is True
        assert result.error is None

        # Document store should have been called
        doc_store.store.assert_called_once()
        # Graph should have a node
        graph_store.upsert_node.assert_called()
        # Vector should have an embedding
        vector_store.upsert.assert_called()

    @pytest.mark.asyncio
    async def test_index_media_file_pointer_only(self, tmp_dir, mock_stores, mock_index_state, mock_extractor):
        """Media files get graph nodes but no text extraction or vector embedding."""
        doc_store, graph_store, vector_store, embedding_service = mock_stores

        indexer = FileIndexer(
            document_store=doc_store,
            graph_store=graph_store,
            vector_store=vector_store,
            embedding_service=embedding_service,
            index_state_store=mock_index_state,
            extractor=mock_extractor,
        )

        result = await indexer.index_file(Path(tmp_dir) / "image.png")

        assert result.action == "created"
        assert result.graph_updated is True
        # Media files don't have extractable text, so no vector embedding
        assert result.vector_updated is False

    @pytest.mark.asyncio
    async def test_index_unsupported_file(self, tmp_dir, mock_stores, mock_index_state, mock_extractor):
        doc_store, graph_store, vector_store, embedding_service = mock_stores

        indexer = FileIndexer(
            document_store=doc_store,
            graph_store=graph_store,
            index_state_store=mock_index_state,
            extractor=mock_extractor,
        )

        result = await indexer.index_file(Path(tmp_dir) / "unsupported.xyz")

        assert result.action == "error"
        assert "Unsupported" in result.error

    @pytest.mark.asyncio
    async def test_index_missing_file(self, mock_stores, mock_index_state, mock_extractor):
        doc_store, graph_store, vector_store, embedding_service = mock_stores

        indexer = FileIndexer(
            document_store=doc_store,
            graph_store=graph_store,
            index_state_store=mock_index_state,
            extractor=mock_extractor,
        )

        result = await indexer.index_file("/nonexistent/file.pdf")

        assert result.action == "error"
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_unchanged_file_skipped(self, tmp_dir, mock_stores, mock_index_state, mock_extractor):
        doc_store, graph_store, vector_store, embedding_service = mock_stores

        indexer = FileIndexer(
            document_store=doc_store,
            graph_store=graph_store,
            vector_store=vector_store,
            embedding_service=embedding_service,
            index_state_store=mock_index_state,
            extractor=mock_extractor,
        )

        file_path = Path(tmp_dir) / "notes.txt"

        # First index
        result1 = await indexer.index_file(file_path)
        assert result1.action == "created"

        # Second index — should be unchanged
        result2 = await indexer.index_file(file_path)
        assert result2.action == "unchanged"

    @pytest.mark.asyncio
    async def test_force_reindex(self, tmp_dir, mock_stores, mock_index_state, mock_extractor):
        doc_store, graph_store, vector_store, embedding_service = mock_stores

        indexer = FileIndexer(
            document_store=doc_store,
            graph_store=graph_store,
            vector_store=vector_store,
            embedding_service=embedding_service,
            index_state_store=mock_index_state,
            extractor=mock_extractor,
        )

        file_path = Path(tmp_dir) / "notes.txt"

        # First index
        await indexer.index_file(file_path)

        # Force re-index
        result = await indexer.index_file(file_path, force=True)
        assert result.action in ("created", "updated")

    @pytest.mark.asyncio
    async def test_stale_detection_missing_file(self, mock_stores, mock_index_state, mock_extractor):
        """When a previously indexed file is missing, mark it stale."""
        doc_store, graph_store, vector_store, embedding_service = mock_stores

        from agent_kernel.services.index_state import EntityIndexState, IndexStatus

        # Pretend we previously indexed this file
        existing_state = EntityIndexState(
            entity_id="file_OLDID123",
            entity_type="file",
            source_path="/gone/file.pdf",
            content_hash="abc123",
        )
        mock_index_state.get_by_path = MagicMock(return_value=existing_state)

        indexer = FileIndexer(
            document_store=doc_store,
            graph_store=graph_store,
            index_state_store=mock_index_state,
            extractor=mock_extractor,
        )

        result = await indexer.index_file("/gone/file.pdf")

        assert result.action == "stale"
        assert result.file_id == "file_OLDID123"

    @pytest.mark.asyncio
    async def test_index_directory(self, tmp_dir, mock_stores, mock_index_state, mock_extractor):
        doc_store, graph_store, vector_store, embedding_service = mock_stores

        indexer = FileIndexer(
            document_store=doc_store,
            graph_store=graph_store,
            vector_store=vector_store,
            embedding_service=embedding_service,
            index_state_store=mock_index_state,
            extractor=mock_extractor,
        )

        summary = await indexer.index_directory(tmp_dir)

        assert isinstance(summary, FileIndexSummary)
        assert summary.total_files > 0
        # Should have indexed .pdf, .xlsx, .txt, .png, and subdir/nested.docx
        assert summary.created >= 4  # at least the supported files
        assert summary.errors == 0

    @pytest.mark.asyncio
    async def test_index_directory_excludes_git(self, tmp_dir, mock_stores, mock_index_state, mock_extractor):
        """Should exclude .git directories."""
        doc_store, graph_store, vector_store, embedding_service = mock_stores

        # Create a .git dir with a file
        git_dir = Path(tmp_dir) / ".git"
        git_dir.mkdir()
        (git_dir / "config.txt").write_text("git config")

        indexer = FileIndexer(
            document_store=doc_store,
            graph_store=graph_store,
            index_state_store=mock_index_state,
            extractor=mock_extractor,
        )

        summary = await indexer.index_directory(tmp_dir)

        # .git/config.txt should NOT be indexed
        indexed_paths = [r.path for r in summary.results]
        assert not any(".git" in p for p in indexed_paths)

    @pytest.mark.asyncio
    async def test_enrichment(self, tmp_dir, mock_stores, mock_index_state, mock_extractor):
        """Test LLM enrichment integration."""
        doc_store, graph_store, vector_store, embedding_service = mock_stores

        from agent_kernel.services.enrichment import EnrichmentResult

        mock_enrichment = MagicMock()
        mock_enrichment.enrich = AsyncMock(
            return_value=EnrichmentResult(
                auto_tags=["report", "data"],
                auto_class="reference",
                auto_summary="A text file with hello world content.",
                tag_confidence=0.85,
                class_confidence=0.90,
                auto_importance=0.5,
                success=True,
            )
        )

        indexer = FileIndexer(
            document_store=doc_store,
            graph_store=graph_store,
            vector_store=vector_store,
            embedding_service=embedding_service,
            index_state_store=mock_index_state,
            enrichment_service=mock_enrichment,
            extractor=mock_extractor,
            enable_enrichment=True,
        )

        result = await indexer.index_file(Path(tmp_dir) / "notes.txt")

        assert result.enriched is True
        assert result.auto_tags == ["report", "data"]
        assert result.auto_summary == "A text file with hello world content."

    @pytest.mark.asyncio
    async def test_reconcile(self, tmp_dir, mock_stores, mock_index_state, mock_extractor):
        """Test reconciliation detects missing files."""
        doc_store, graph_store, vector_store, embedding_service = mock_stores

        from agent_kernel.services.index_state import EntityIndexState

        # Simulate two indexed files: one exists, one doesn't
        states = [
            EntityIndexState(
                entity_id="file_EXISTS",
                entity_type="file",
                source_path=str(Path(tmp_dir) / "notes.txt"),
                content_hash="abc",
            ),
            EntityIndexState(
                entity_id="file_GONE",
                entity_type="file",
                source_path="/gone/deleted.pdf",
                content_hash="def",
            ),
        ]
        mock_index_state.list_by_entity_type = MagicMock(return_value=states)

        indexer = FileIndexer(
            graph_store=graph_store,
            index_state_store=mock_index_state,
            extractor=mock_extractor,
        )

        report = await indexer.reconcile(dry_run=True)

        assert "/gone/deleted.pdf" in report["missing"]
        # Dry run should not mark stale
        mock_index_state.mark_stale.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_marks_stale(self, tmp_dir, mock_stores, mock_index_state, mock_extractor):
        """Test reconciliation marks missing files as stale when not dry_run."""
        doc_store, graph_store, vector_store, embedding_service = mock_stores

        from agent_kernel.services.index_state import EntityIndexState

        states = [
            EntityIndexState(
                entity_id="file_GONE",
                entity_type="file",
                source_path="/gone/deleted.pdf",
                content_hash="def",
            ),
        ]
        mock_index_state.list_by_entity_type = MagicMock(return_value=states)

        indexer = FileIndexer(
            graph_store=graph_store,
            index_state_store=mock_index_state,
            extractor=mock_extractor,
        )

        report = await indexer.reconcile(dry_run=False)

        assert len(report["stale_marked"]) == 1


class TestFileIndexResult:
    """Test result serialization."""

    def test_to_dict(self):
        result = FileIndexResult(
            file_id="file_01ABC",
            path="/tmp/test.pdf",
            action="created",
            resource_type="pdf",
            graph_updated=True,
            vector_updated=True,
            enriched=False,
        )

        d = result.to_dict()
        assert d["file_id"] == "file_01ABC"
        assert d["action"] == "created"
        assert d["resource_type"] == "pdf"
        assert d["graph_updated"] is True


class TestFileIndexSummary:
    """Test summary serialization."""

    def test_to_dict(self):
        from agent_kernel.core.schemas.base import utc_now

        summary = FileIndexSummary(
            started_at=utc_now(),
            completed_at=utc_now(),
            total_files=5,
            created=3,
            updated=1,
            unchanged=1,
        )

        d = summary.to_dict()
        assert d["total_files"] == 5
        assert d["created"] == 3
        assert d["started_at"] is not None
