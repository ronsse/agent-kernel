"""Tests for the LLM Enrichment Service (v1.0.1)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_kernel.services.enrichment import (
    DEFAULT_CLASSIFICATIONS,
    EnrichmentResult,
    EnrichmentService,
)


class TestEnrichmentResult:
    """Tests for EnrichmentResult dataclass."""

    def test_default_values(self) -> None:
        """Test default initialization."""
        result = EnrichmentResult()
        assert result.auto_tags == []
        assert result.auto_class is None
        assert result.auto_summary is None
        assert result.success is True
        assert result.error is None

    def test_to_frontmatter_with_all_fields(self) -> None:
        """Test converting to frontmatter dict with all fields."""
        result = EnrichmentResult(
            auto_tags=["python", "testing"],
            auto_class="documentation",
            auto_summary="A test summary.",
        )
        fm = result.to_frontmatter()
        assert fm == {
            "tags": ["python", "testing"],
            "class": "documentation",
            "summary": "A test summary.",
        }

    def test_to_frontmatter_partial(self) -> None:
        """Test converting to frontmatter with only some fields."""
        result = EnrichmentResult(
            auto_tags=["meeting"],
            auto_class="meeting",
        )
        fm = result.to_frontmatter()
        assert fm == {
            "tags": ["meeting"],
            "class": "meeting",
        }
        assert "summary" not in fm

    def test_to_frontmatter_empty(self) -> None:
        """Test converting empty result to frontmatter."""
        result = EnrichmentResult()
        fm = result.to_frontmatter()
        assert fm == {}

    def test_to_dict(self) -> None:
        """Test to_dict includes all fields."""
        expected_tag_conf = 0.9
        expected_class_conf = 0.85
        result = EnrichmentResult(
            auto_tags=["test"],
            auto_class="notes",
            tag_confidence=expected_tag_conf,
            class_confidence=expected_class_conf,
        )
        d = result.to_dict()
        assert d["auto_tags"] == ["test"]
        assert d["auto_class"] == "notes"
        assert d["tag_confidence"] == expected_tag_conf
        assert d["class_confidence"] == expected_class_conf
        assert d["success"] is True


class TestEnrichmentService:
    """Tests for EnrichmentService."""

    @pytest.fixture
    def mock_llm_service(self) -> MagicMock:
        """Create a mock LLM service."""
        mock = MagicMock()
        mock.generate = AsyncMock()
        return mock

    @pytest.fixture
    def enrichment_service(self, mock_llm_service: MagicMock) -> EnrichmentService:
        """Create an EnrichmentService with mock LLM."""
        return EnrichmentService(llm_service=mock_llm_service)

    @pytest.mark.asyncio
    async def test_enrich_success(
        self,
        mock_llm_service: MagicMock,
        enrichment_service: EnrichmentService,
    ) -> None:
        """Test successful enrichment."""
        mock_llm_service.generate.return_value = """{
            "tags": ["python", "architecture"],
            "class": "documentation",
            "summary": "Notes about Python architecture.",
            "tag_confidence": 0.92,
            "class_confidence": 0.88
        }"""

        result = await enrichment_service.enrich(
            content="# Python Architecture\n\nSome notes about design patterns.",
            title="Python Architecture",
        )

        assert result.success is True
        assert result.auto_tags == ["python", "architecture"]
        assert result.auto_class == "documentation"
        assert result.auto_summary == "Notes about Python architecture."
        expected_tag_conf = 0.92
        expected_class_conf = 0.88
        assert result.tag_confidence == expected_tag_conf
        assert result.class_confidence == expected_class_conf

    @pytest.mark.asyncio
    async def test_enrich_handles_markdown_code_blocks(
        self,
        mock_llm_service: MagicMock,
        enrichment_service: EnrichmentService,
    ) -> None:
        """Test that markdown code blocks are stripped from response."""
        mock_llm_service.generate.return_value = """```json
{
    "tags": ["meeting"],
    "class": "meeting",
    "summary": null,
    "tag_confidence": 0.85,
    "class_confidence": 0.95
}
```"""

        result = await enrichment_service.enrich(
            content="Meeting notes from standup.",
            title="Daily Standup",
        )

        assert result.success is True
        assert result.auto_tags == ["meeting"]
        assert result.auto_class == "meeting"
        assert result.auto_summary is None

    @pytest.mark.asyncio
    async def test_enrich_handles_invalid_json(
        self,
        mock_llm_service: MagicMock,
        enrichment_service: EnrichmentService,
    ) -> None:
        """Test handling of invalid JSON response."""
        mock_llm_service.generate.return_value = "This is not valid JSON"

        result = await enrichment_service.enrich(
            content="Some content.",
            title="Test",
        )

        assert result.success is False
        assert result.error is not None
        assert "JSON" in result.error

    @pytest.mark.asyncio
    async def test_enrich_handles_llm_exception(
        self,
        mock_llm_service: MagicMock,
        enrichment_service: EnrichmentService,
    ) -> None:
        """Test handling of LLM service exception."""
        mock_llm_service.generate.side_effect = Exception("API error")

        result = await enrichment_service.enrich(
            content="Some content.",
            title="Test",
        )

        assert result.success is False
        assert result.error == "API error"

    @pytest.mark.asyncio
    async def test_enrich_truncates_long_content(
        self,
        mock_llm_service: MagicMock,
        enrichment_service: EnrichmentService,
    ) -> None:
        """Test that long content is truncated."""
        mock_llm_service.generate.return_value = '{"tags": [], "class": "notes"}'

        long_content = "x" * 10000
        await enrichment_service.enrich(content=long_content, title="Test")

        # Check the user prompt was truncated
        call_args = mock_llm_service.generate.call_args
        user_prompt = call_args.kwargs["user_prompt"]
        assert "[Content truncated...]" in user_prompt

    @pytest.mark.asyncio
    async def test_enrich_normalizes_tags(
        self,
        mock_llm_service: MagicMock,
        enrichment_service: EnrichmentService,
    ) -> None:
        """Test that tags are normalized to lowercase hyphenated format."""
        mock_llm_service.generate.return_value = """{
            "tags": ["Python Code", "Data_Science", "Machine Learning!"],
            "class": "reference"
        }"""

        result = await enrichment_service.enrich(content="Test", title="Test")

        assert result.auto_tags == ["python-code", "data-science", "machine-learning"]

    @pytest.mark.asyncio
    async def test_enrich_rejects_invalid_classification(
        self,
        mock_llm_service: MagicMock,
        enrichment_service: EnrichmentService,
    ) -> None:
        """Test that invalid classifications are rejected."""
        mock_llm_service.generate.return_value = """{
            "tags": ["test"],
            "class": "invalid-classification-not-in-list"
        }"""

        result = await enrichment_service.enrich(content="Test", title="Test")

        assert result.success is True
        assert result.auto_class is None  # Invalid class rejected

    @pytest.mark.asyncio
    async def test_enrich_with_existing_tags(
        self,
        mock_llm_service: MagicMock,
        enrichment_service: EnrichmentService,
    ) -> None:
        """Test that existing tags are passed to LLM."""
        response = '{"tags": ["related"], "class": "notes"}'
        mock_llm_service.generate.return_value = response

        await enrichment_service.enrich(
            content="Test content.",
            title="Test",
            existing_tags=["project/foo", "meeting"],
        )

        # Check existing tags were included in prompt
        call_args = mock_llm_service.generate.call_args
        user_prompt = call_args.kwargs["user_prompt"]
        assert "project/foo" in user_prompt
        assert "meeting" in user_prompt

    @pytest.mark.asyncio
    async def test_enrich_clamps_confidence_values(
        self,
        mock_llm_service: MagicMock,
        enrichment_service: EnrichmentService,
    ) -> None:
        """Test that confidence values are clamped to 0.0-1.0."""
        mock_llm_service.generate.return_value = """{
            "tags": ["test"],
            "class": "notes",
            "tag_confidence": 1.5,
            "class_confidence": -0.5
        }"""

        result = await enrichment_service.enrich(content="Test", title="Test")

        assert result.tag_confidence == 1.0  # Clamped to max
        assert result.class_confidence == 0.0  # Clamped to min

    def test_normalize_tag_various_inputs(
        self,
        enrichment_service: EnrichmentService,
    ) -> None:
        """Test tag normalization with various inputs."""
        # Test cases: (input, expected)
        cases = [
            ("Python", "python"),
            ("data_science", "data-science"),
            ("Machine Learning", "machine-learning"),
            ("Test!@#$Tag", "testtag"),
            ("project/sub-project", "project/sub-project"),
            ("---hyphen---test---", "hyphen-test"),
            ("  spaces  ", "spaces"),
        ]

        for input_tag, expected in cases:
            result = enrichment_service._normalize_tag(input_tag)
            assert result == expected, f"Failed for input: {input_tag}"


class TestEnrichmentServiceBatch:
    """Tests for batch enrichment."""

    @pytest.fixture
    def mock_llm_service(self) -> MagicMock:
        """Create a mock LLM service."""
        mock = MagicMock()
        mock.generate = AsyncMock(
            return_value='{"tags": ["test"], "class": "notes"}'
        )
        return mock

    @pytest.fixture
    def enrichment_service(self, mock_llm_service: MagicMock) -> EnrichmentService:
        """Create an EnrichmentService with mock LLM."""
        return EnrichmentService(llm_service=mock_llm_service)

    @pytest.mark.asyncio
    async def test_batch_enrich_multiple_notes(
        self,
        mock_llm_service: MagicMock,
        enrichment_service: EnrichmentService,
    ) -> None:
        """Test batch enrichment of multiple notes."""
        notes: list[dict[str, Any]] = [
            {"content": "Note 1", "title": "Title 1", "tags": []},
            {"content": "Note 2", "title": "Title 2", "tags": []},
            {"content": "Note 3", "title": "Title 3", "tags": []},
        ]

        results = await enrichment_service.batch_enrich(notes, concurrency=2)

        expected_count = len(notes)
        assert len(results) == expected_count
        assert all(r.success for r in results)
        assert mock_llm_service.generate.call_count == expected_count

    @pytest.mark.asyncio
    async def test_batch_enrich_handles_exceptions(
        self,
        mock_llm_service: MagicMock,
        enrichment_service: EnrichmentService,
    ) -> None:
        """Test batch enrichment handles individual failures."""
        call_count = 0
        fail_on_call = 2

        async def mock_generate(*_args: Any, **_kwargs: Any) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == fail_on_call:
                msg = "API error on second call"
                raise RuntimeError(msg)
            return '{"tags": ["ok"], "class": "notes"}'

        mock_llm_service.generate.side_effect = mock_generate

        notes: list[dict[str, Any]] = [
            {"content": "Note 1", "title": "Title 1"},
            {"content": "Note 2", "title": "Title 2"},
            {"content": "Note 3", "title": "Title 3"},
        ]

        results = await enrichment_service.batch_enrich(notes, concurrency=1)

        expected_count = len(notes)
        assert len(results) == expected_count
        assert results[0].success is True
        assert results[1].success is False  # The one that failed
        assert results[2].success is True


class TestDefaultClassifications:
    """Tests for default classifications."""

    def test_default_classifications_exist(self) -> None:
        """Test that default classifications are defined."""
        assert len(DEFAULT_CLASSIFICATIONS) > 0
        assert "meeting" in DEFAULT_CLASSIFICATIONS
        assert "architecture" in DEFAULT_CLASSIFICATIONS
        assert "documentation" in DEFAULT_CLASSIFICATIONS

    def test_custom_classifications(self) -> None:
        """Test that custom classifications can be provided."""
        mock_llm = MagicMock()
        custom = ["custom1", "custom2"]

        service = EnrichmentService(
            llm_service=mock_llm,
            classifications=custom,
        )

        assert service.classifications == custom
