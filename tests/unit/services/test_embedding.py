"""Tests for embedding service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_kernel.services.embedding import (
    EmbeddingResult,
    OpenAIEmbeddingService,
    create_embedding_service,
)


class TestEmbeddingResult:
    """Tests for EmbeddingResult."""

    def test_create_result(self):
        """Test creating an embedding result."""
        result = EmbeddingResult(
            vectors=[[0.1, 0.2, 0.3]],
            model="text-embedding-3-small",
            total_tokens=10,
            dimensions=3,
        )

        assert result.vectors == [[0.1, 0.2, 0.3]]
        assert result.vector == [0.1, 0.2, 0.3]
        assert result.model == "text-embedding-3-small"
        assert result.dimensions == 3

    def test_vector_property_empty(self):
        """Test vector property with empty vectors."""
        result = EmbeddingResult(
            vectors=[],
            model="test",
        )

        assert result.vector == []

    def test_estimated_cost(self):
        """Test cost estimation."""
        result = EmbeddingResult(
            vectors=[[0.1] * 1536],
            model="text-embedding-3-small",
            total_tokens=1000,
            dimensions=1536,
        )

        # text-embedding-3-small: $0.02/1M tokens
        expected = (1000 / 1_000_000) * 0.02
        assert abs(result.estimated_cost_usd - expected) < 0.0001


class TestOpenAIEmbeddingService:
    """Tests for OpenAIEmbeddingService."""

    def test_init_with_api_key(self):
        """Test initialization with API key."""
        service = OpenAIEmbeddingService(
            api_key="test-key-123",
            model="text-embedding-3-large",
        )

        assert service._api_key == "test-key-123"
        assert service._model == "text-embedding-3-large"
        assert service.dimensions == 3072

    def test_init_requires_api_key(self):
        """Test that initialization requires API key."""
        import os
        orig_key = os.environ.pop("OPENAI_API_KEY", None)

        try:
            with pytest.raises(ValueError, match="API key required"):
                OpenAIEmbeddingService(api_key=None)
        finally:
            if orig_key:
                os.environ["OPENAI_API_KEY"] = orig_key

    def test_dimensions_by_model(self):
        """Test that dimensions are set correctly by model."""
        service_small = OpenAIEmbeddingService(
            api_key="test",
            model="text-embedding-3-small",
        )
        assert service_small.dimensions == 1536

        service_large = OpenAIEmbeddingService(
            api_key="test",
            model="text-embedding-3-large",
        )
        assert service_large.dimensions == 3072

    @pytest.mark.asyncio
    async def test_embed_calls_openai(self):
        """Test that embed calls OpenAI API."""
        service = OpenAIEmbeddingService(api_key="test-key")

        # Mock response
        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1, 0.2, 0.3]
        mock_embedding.index = 0

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_response.usage = MagicMock(total_tokens=5)
        mock_response.model_dump.return_value = {}

        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)

        service._client = mock_client

        result = await service.embed("Hello world")

        assert result == [0.1, 0.2, 0.3]
        mock_client.embeddings.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_batch(self):
        """Test batch embedding."""
        service = OpenAIEmbeddingService(api_key="test-key")

        # Mock response with multiple embeddings
        mock_embeddings = [
            MagicMock(embedding=[0.1, 0.2], index=0),
            MagicMock(embedding=[0.3, 0.4], index=1),
        ]

        mock_response = MagicMock()
        mock_response.data = mock_embeddings
        mock_response.usage = MagicMock(total_tokens=10)

        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)

        service._client = mock_client

        results = await service.embed_batch(["text 1", "text 2"])

        assert len(results) == 2
        assert results[0] == [0.1, 0.2]
        assert results[1] == [0.3, 0.4]

    @pytest.mark.asyncio
    async def test_embed_batch_empty(self):
        """Test batch embedding with empty list."""
        service = OpenAIEmbeddingService(api_key="test-key")

        results = await service.embed_batch([])

        assert results == []

    @pytest.mark.asyncio
    async def test_embed_with_metadata(self):
        """Test embedding with metadata."""
        service = OpenAIEmbeddingService(api_key="test-key")

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 1536
        mock_embedding.index = 0

        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_response.usage = MagicMock(total_tokens=15)
        mock_response.model_dump.return_value = {"id": "test"}

        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_response)

        service._client = mock_client

        result = await service.embed_with_metadata("Test text")

        assert isinstance(result, EmbeddingResult)
        assert len(result.vector) == 1536
        assert result.total_tokens == 15
        assert result.dimensions == 1536


class TestCreateEmbeddingService:
    """Tests for create_embedding_service factory."""

    def test_create_openai_service(self):
        """Test creating OpenAI embedding service."""
        service = create_embedding_service(
            provider="openai",
            api_key="test-key",
            model="text-embedding-3-small",
        )

        assert isinstance(service, OpenAIEmbeddingService)
        assert service._model == "text-embedding-3-small"

    def test_create_openai_case_insensitive(self):
        """Test provider name is case insensitive."""
        service = create_embedding_service(
            provider="OpenAI",
            api_key="test-key",
        )

        assert isinstance(service, OpenAIEmbeddingService)

    def test_unsupported_provider(self):
        """Test error for unsupported provider."""
        with pytest.raises(ValueError, match="Unsupported embedding provider"):
            create_embedding_service(provider="unknown")

    def test_local_provider_alias(self):
        """Test local provider aliases."""
        # These should all work (if sentence-transformers is installed)
        # For now, just test that the provider is recognized
        for alias in ["local", "sentence-transformers", "st"]:
            try:
                service = create_embedding_service(provider=alias)
                # If we get here, it worked (sentence-transformers installed)
                assert service is not None
            except ImportError:
                # Expected if sentence-transformers not installed
                pass
