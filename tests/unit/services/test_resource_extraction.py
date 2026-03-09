"""Tests for resource extraction service."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_kernel.services.resource_extraction import (
    ExtractionResult,
    ResourceExtractionService,
    ResourceExtractor,
    ResourceMetadata,
    ResourceSummary,
    ResourceType,
    EXTENSION_MAPPING,
)


class TestResourceType:
    """Tests for ResourceType enum."""

    def test_extension_mapping(self):
        """Test file extension to resource type mapping."""
        assert EXTENSION_MAPPING[".pptx"] == ResourceType.POWERPOINT
        assert EXTENSION_MAPPING[".pdf"] == ResourceType.PDF
        assert EXTENSION_MAPPING[".docx"] == ResourceType.WORD
        assert EXTENSION_MAPPING[".xlsx"] == ResourceType.EXCEL
        assert EXTENSION_MAPPING[".md"] == ResourceType.MARKDOWN


class TestResourceMetadata:
    """Tests for ResourceMetadata dataclass."""

    def test_to_dict(self):
        """Test converting metadata to dict."""
        metadata = ResourceMetadata(
            file_path="/path/to/file.pdf",
            file_name="file.pdf",
            resource_type=ResourceType.PDF,
            file_size=1024,
            page_count=10,
        )

        data = metadata.to_dict()

        assert data["file_path"] == "/path/to/file.pdf"
        assert data["file_name"] == "file.pdf"
        assert data["resource_type"] == "pdf"
        assert data["page_count"] == 10


class TestResourceSummary:
    """Tests for ResourceSummary dataclass."""

    def test_to_dict(self):
        """Test converting summary to dict."""
        summary = ResourceSummary(
            title="Test Document",
            summary="This is a test summary.",
            key_points=["Point 1", "Point 2"],
            topics=["testing", "documentation"],
            confidence=0.85,
        )

        data = summary.to_dict()

        assert data["title"] == "Test Document"
        assert len(data["key_points"]) == 2
        assert data["confidence"] == 0.85

    def test_to_markdown(self):
        """Test generating markdown from summary."""
        metadata = ResourceMetadata(
            file_path="/path/to/slides.pptx",
            file_name="slides.pptx",
            resource_type=ResourceType.POWERPOINT,
            slide_count=15,
        )
        summary = ResourceSummary(
            title="Quarterly Review",
            summary="This is a quarterly review presentation.",
            key_points=["Revenue up 10%", "New product launch"],
            topics=["business", "quarterly-review"],
            action_items=["Follow up on metrics"],
            confidence=0.9,
        )

        markdown = summary.to_markdown("[[slides.pptx]]", metadata)

        assert "# Quarterly Review" in markdown
        assert "[[slides.pptx]]" in markdown
        assert "**Slides:** 15" in markdown
        assert "Revenue up 10%" in markdown
        assert "- [ ] Follow up on metrics" in markdown
        assert "`business`" in markdown


class TestResourceExtractor:
    """Tests for ResourceExtractor."""

    def test_get_resource_type(self):
        """Test determining resource type from file path."""
        extractor = ResourceExtractor()

        assert extractor.get_resource_type("doc.pdf") == ResourceType.PDF
        assert extractor.get_resource_type("slides.pptx") == ResourceType.POWERPOINT
        assert extractor.get_resource_type("doc.docx") == ResourceType.WORD
        assert extractor.get_resource_type("data.xlsx") == ResourceType.EXCEL
        assert extractor.get_resource_type("notes.md") == ResourceType.MARKDOWN
        assert extractor.get_resource_type("file.xyz") == ResourceType.UNKNOWN

    @pytest.mark.asyncio
    async def test_extract_text_file(self):
        """Test extracting content from text file."""
        extractor = ResourceExtractor()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Hello, World!\nThis is a test file.")
            f.flush()
            path = Path(f.name)

        try:
            result = await extractor.extract(path)

            assert result.success
            assert "Hello, World!" in result.raw_text
            assert result.metadata.resource_type == ResourceType.TEXT
            assert result.metadata.word_count == 7
        finally:
            path.unlink()

    @pytest.mark.asyncio
    async def test_extract_markdown_file(self):
        """Test extracting content from markdown file."""
        extractor = ResourceExtractor()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Test Heading\n\nSome content here.")
            f.flush()
            path = Path(f.name)

        try:
            result = await extractor.extract(path)

            assert result.success
            assert "# Test Heading" in result.raw_text
            assert result.metadata.resource_type == ResourceType.MARKDOWN
        finally:
            path.unlink()

    @pytest.mark.asyncio
    async def test_extract_nonexistent_file(self):
        """Test extracting from non-existent file."""
        extractor = ResourceExtractor()

        result = await extractor.extract("/nonexistent/file.pdf")

        assert not result.success
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_extract_unsupported_type(self):
        """Test extracting unsupported file type."""
        extractor = ResourceExtractor()

        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            path = Path(f.name)

        try:
            result = await extractor.extract(path)

            assert not result.success
            assert "Unsupported" in result.error
        finally:
            path.unlink()


@pytest.fixture
def mock_llm_service():
    """Create a mock LLM service."""
    service = MagicMock()
    service.generate = AsyncMock(return_value='''{
        "title": "Test Document",
        "summary": "This is a test document about testing.",
        "key_points": ["Point 1", "Point 2"],
        "topics": ["testing", "documentation"],
        "action_items": ["Review document"],
        "suggested_project": "Testing",
        "confidence": 0.85
    }''')
    return service


class TestResourceExtractionService:
    """Tests for ResourceExtractionService."""

    def test_find_resource_links_wiki_style(self, mock_llm_service):
        """Test finding wiki-style resource links."""
        service = ResourceExtractionService(
            llm_service=mock_llm_service,
        )

        content = "Check out [[presentation.pptx]] and [[document.pdf]]."
        links = service.find_resource_links(content)

        assert "presentation.pptx" in links
        assert "document.pdf" in links

    def test_find_resource_links_markdown_style(self, mock_llm_service):
        """Test finding markdown-style resource links."""
        service = ResourceExtractionService(
            llm_service=mock_llm_service,
        )

        content = "See [slides](./slides.pptx) and [report](report.docx)."
        links = service.find_resource_links(content)

        assert "./slides.pptx" in links
        assert "report.docx" in links

    def test_find_resource_links_mixed(self, mock_llm_service):
        """Test finding mixed format resource links."""
        service = ResourceExtractionService(
            llm_service=mock_llm_service,
        )

        content = """
        Files:
        - [[meeting-notes.pdf]]
        - [quarterly](./quarterly.pptx)
        - data.xlsx
        """
        links = service.find_resource_links(content)

        assert len(links) >= 2  # At least wiki and markdown links

    @pytest.mark.asyncio
    async def test_extract_and_summarize(self, mock_llm_service):
        """Test extracting and summarizing a resource."""
        service = ResourceExtractionService(
            llm_service=mock_llm_service,
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("This is test content for summarization.")
            f.flush()
            path = Path(f.name)

        try:
            extraction, summary = await service.extract_and_summarize(path)

            assert extraction.success
            assert summary is not None
            assert summary.title == "Test Document"
            assert summary.confidence == 0.85
        finally:
            path.unlink()

    @pytest.mark.asyncio
    async def test_create_summary_note(self, mock_llm_service):
        """Test creating summary note in vault."""
        with tempfile.TemporaryDirectory() as vault_dir:
            service = ResourceExtractionService(
                llm_service=mock_llm_service,
                vault_path=vault_dir,
            )

            extraction = ExtractionResult(
                metadata=ResourceMetadata(
                    file_path="/path/to/slides.pptx",
                    file_name="slides.pptx",
                    resource_type=ResourceType.POWERPOINT,
                    content_hash="abc123",
                    slide_count=10,
                ),
                raw_text="Test content",
                success=True,
            )
            summary = ResourceSummary(
                title="Test Slides",
                summary="Test summary",
                key_points=["Point 1"],
                topics=["testing"],
                suggested_project="TestProject",
                confidence=0.9,
            )

            note_path = await service.create_summary_note(extraction, summary)

            assert note_path is not None
            assert Path(note_path).exists()

            content = Path(note_path).read_text()
            assert "# Test Slides" in content
            assert "source_file:" in content
            assert "content_hash: abc123" in content

    @pytest.mark.asyncio
    async def test_create_summary_note_no_vault(self, mock_llm_service):
        """Test creating summary note without vault path."""
        service = ResourceExtractionService(
            llm_service=mock_llm_service,
            vault_path=None,
        )

        extraction = ExtractionResult(
            metadata=ResourceMetadata(
                file_path="/path/to/file.pdf",
                file_name="file.pdf",
                resource_type=ResourceType.PDF,
                content_hash="xyz789",
            ),
            success=True,
        )
        summary = ResourceSummary(title="Test", summary="Test")

        note_path = await service.create_summary_note(extraction, summary)

        assert note_path is None

    def test_is_processed(self, mock_llm_service):
        """Test checking if resource was already processed."""
        with tempfile.TemporaryDirectory() as vault_dir:
            service = ResourceExtractionService(
                llm_service=mock_llm_service,
                vault_path=vault_dir,
            )

            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write("Test content")
                f.flush()
                path = Path(f.name)

            try:
                # Initially not processed
                assert not service.is_processed(path)

                # Mark as processed
                hash_val = service.extractor._compute_content_hash(path)
                service._processed[hash_val] = "/path/to/note.md"

                # Now should be processed
                assert service.is_processed(path)
            finally:
                path.unlink()

    def test_parse_summary_valid_json(self, mock_llm_service):
        """Test parsing valid JSON summary."""
        service = ResourceExtractionService(
            llm_service=mock_llm_service,
        )

        response = '''{
            "title": "Test",
            "summary": "Test summary",
            "key_points": ["Point 1"],
            "topics": ["test"],
            "confidence": 0.8
        }'''

        summary = service._parse_summary(response)

        assert summary.title == "Test"
        assert summary.confidence == 0.8
        assert len(summary.key_points) == 1

    def test_parse_summary_with_code_block(self, mock_llm_service):
        """Test parsing summary wrapped in code block."""
        service = ResourceExtractionService(
            llm_service=mock_llm_service,
        )

        response = '''```json
{
    "title": "Test",
    "summary": "Test summary",
    "confidence": 0.9
}
```'''

        summary = service._parse_summary(response)

        assert summary.title == "Test"
        assert summary.confidence == 0.9

    def test_parse_summary_invalid_json(self, mock_llm_service):
        """Test parsing invalid JSON returns empty summary."""
        service = ResourceExtractionService(
            llm_service=mock_llm_service,
        )

        response = "Not valid JSON"

        summary = service._parse_summary(response)

        assert summary.title == ""
        assert summary.confidence == 0.0


class TestExtractionResult:
    """Tests for ExtractionResult dataclass."""

    def test_to_dict(self):
        """Test converting extraction result to dict."""
        result = ExtractionResult(
            metadata=ResourceMetadata(
                file_path="/path/to/file.pdf",
                file_name="file.pdf",
                resource_type=ResourceType.PDF,
            ),
            raw_text="Test content " * 100,
            extraction_method="pypdf",
            success=True,
        )

        data = result.to_dict()

        assert data["success"]
        assert data["extraction_method"] == "pypdf"
        assert data["raw_text_length"] == len("Test content " * 100)
