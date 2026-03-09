"""Integration tests for embedding service with real API calls.

These tests require valid API keys and make real API calls.

Run with: pytest tests/integration/test_embedding_integration.py -v
"""

import os

import pytest

from agent_kernel.services.embedding import (
    OpenAIEmbeddingService,
    create_embedding_service,
)

# Skip if no API key available
pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)


class TestOpenAIEmbeddingIntegration:
    """Integration tests for OpenAI embedding service."""

    @pytest.mark.asyncio
    async def test_embed_single_text(self):
        """Test embedding a single text."""
        service = OpenAIEmbeddingService()

        vector = await service.embed("Hello, world!")

        assert isinstance(vector, list)
        assert len(vector) == 1536  # text-embedding-3-small dimensions
        assert all(isinstance(v, float) for v in vector)

    @pytest.mark.asyncio
    async def test_embed_batch(self):
        """Test batch embedding."""
        service = OpenAIEmbeddingService()

        vectors = await service.embed_batch([
            "First text",
            "Second text",
            "Third text",
        ])

        assert len(vectors) == 3
        assert all(len(v) == 1536 for v in vectors)

    @pytest.mark.asyncio
    async def test_embed_with_metadata(self):
        """Test embedding with metadata."""
        service = OpenAIEmbeddingService()

        result = await service.embed_with_metadata("Test embedding")

        assert len(result.vector) == 1536
        assert result.total_tokens > 0
        assert result.dimensions == 1536
        assert result.model == "text-embedding-3-small"

    @pytest.mark.asyncio
    async def test_similar_texts_have_high_similarity(self):
        """Test that similar texts have similar embeddings."""
        import numpy as np

        service = OpenAIEmbeddingService()

        vectors = await service.embed_batch([
            "The cat sat on the mat",
            "A cat was sitting on a mat",
            "Python programming language",
        ])

        # Compute cosine similarities
        def cosine_sim(a, b):
            return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

        # Similar sentences should have higher similarity
        sim_similar = cosine_sim(vectors[0], vectors[1])
        sim_different = cosine_sim(vectors[0], vectors[2])

        assert sim_similar > sim_different
        assert sim_similar > 0.8  # Similar sentences should be >0.8

    @pytest.mark.asyncio
    async def test_large_model(self):
        """Test with text-embedding-3-large."""
        service = OpenAIEmbeddingService(model="text-embedding-3-large")

        vector = await service.embed("Test with large model")

        assert len(vector) == 3072  # Large model dimensions


class TestFactoryIntegration:
    """Integration tests for create_embedding_service factory."""

    @pytest.mark.asyncio
    async def test_create_and_use_openai(self):
        """Test creating and using OpenAI service via factory."""
        service = create_embedding_service(provider="openai")

        vector = await service.embed("Factory test")

        assert len(vector) == 1536
