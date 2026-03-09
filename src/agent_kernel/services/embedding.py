"""Embedding Service - Text to vector embeddings.

Provides a unified interface for generating embeddings:
- OpenAI (text-embedding-3-small, text-embedding-3-large, etc.)
- Local models via sentence-transformers (optional)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class EmbeddingResult:
    """Result from an embedding request."""

    vectors: list[list[float]]
    model: str
    total_tokens: int = 0
    dimensions: int = 0
    raw_response: dict = field(default_factory=dict)

    @property
    def vector(self) -> list[float]:
        """Get the first vector (for single text input)."""
        if not self.vectors:
            return []
        return self.vectors[0]

    @property
    def estimated_cost_usd(self) -> float:
        """Estimate cost based on token count.

        Uses approximate OpenAI pricing.
        """
        # Pricing per 1M tokens
        pricing = {
            "text-embedding-3-small": 0.02,
            "text-embedding-3-large": 0.13,
            "text-embedding-ada-002": 0.10,
        }

        model_lower = self.model.lower()
        price_per_million = 0.02  # Default

        for model_key, price in pricing.items():
            if model_key in model_lower:
                price_per_million = price
                break

        return (self.total_tokens / 1_000_000) * price_per_million


class EmbeddingService(ABC):
    """Abstract base class for embedding services."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Get the embedding dimensions for the current model."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Embed a single text string.

        Args:
            text: Text to embed.

        Returns:
            Embedding vector.
        """

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in a batch.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.
        """

    @abstractmethod
    async def embed_with_metadata(self, text: str) -> EmbeddingResult:
        """Embed with full metadata (tokens, dimensions, etc.).

        Args:
            text: Text to embed.

        Returns:
            EmbeddingResult with vector and metadata.
        """


class OpenAIEmbeddingService(EmbeddingService):
    """OpenAI embedding service."""

    # Model dimensions
    MODEL_DIMENSIONS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        base_url: str | None = None,
    ) -> None:
        """Initialize OpenAI embedding service.

        Args:
            api_key: OpenAI API key (or OPENAI_API_KEY env var).
            model: Embedding model to use.
            base_url: Custom base URL for API.
        """
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OpenAI API key required. Set OPENAI_API_KEY or pass api_key."
            )

        self._model = model
        self._base_url = base_url
        self._client = None
        self._dimensions = self.MODEL_DIMENSIONS.get(model, 1536)

        logger.info(
            "openai_embedding_service_initialized",
            model=model,
            dimensions=self._dimensions,
        )

    @property
    def dimensions(self) -> int:
        """Get embedding dimensions."""
        return self._dimensions

    def _get_client(self):
        """Lazy-load the OpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as err:
                raise ImportError(
                    "openai package required. Install with: pip install openai"
                ) from err

            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
            )
        return self._client

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        result = await self.embed_with_metadata(text)
        return result.vector

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in a batch."""
        if not texts:
            return []

        client = self._get_client()

        logger.debug(
            "openai_embedding_batch_request",
            model=self._model,
            count=len(texts),
        )

        response = await client.embeddings.create(
            model=self._model,
            input=texts,
        )

        # Sort by index to ensure correct order
        embeddings = sorted(response.data, key=lambda x: x.index)
        vectors = [e.embedding for e in embeddings]

        logger.info(
            "openai_embedding_batch_response",
            model=self._model,
            count=len(vectors),
            total_tokens=response.usage.total_tokens,
        )

        return vectors

    async def embed_with_metadata(self, text: str) -> EmbeddingResult:
        """Embed with full metadata."""
        client = self._get_client()

        logger.debug(
            "openai_embedding_request",
            model=self._model,
            text_length=len(text),
        )

        response = await client.embeddings.create(
            model=self._model,
            input=text,
        )

        vector = response.data[0].embedding

        logger.info(
            "openai_embedding_response",
            model=self._model,
            dimensions=len(vector),
            total_tokens=response.usage.total_tokens,
        )

        return EmbeddingResult(
            vectors=[vector],
            model=self._model,
            total_tokens=response.usage.total_tokens,
            dimensions=len(vector),
            raw_response=response.model_dump(),
        )


class LocalEmbeddingService(EmbeddingService):
    """Local embedding service using sentence-transformers.

    Requires: pip install sentence-transformers
    """

    # Common model dimensions
    MODEL_DIMENSIONS = {
        "all-MiniLM-L6-v2": 384,
        "all-mpnet-base-v2": 768,
        "multi-qa-mpnet-base-dot-v1": 768,
        "paraphrase-MiniLM-L6-v2": 384,
    }

    def __init__(
        self,
        model: str = "all-MiniLM-L6-v2",
        device: str | None = None,
    ) -> None:
        """Initialize local embedding service.

        Args:
            model: Sentence-transformers model name.
            device: Device to use ('cpu', 'cuda', 'mps', or None for auto).
        """
        self._model_name = model
        self._device = device
        self._model = None
        self._dimensions = self.MODEL_DIMENSIONS.get(model, 384)

        logger.info(
            "local_embedding_service_initialized",
            model=model,
            device=device or "auto",
        )

    @property
    def dimensions(self) -> int:
        """Get embedding dimensions."""
        return self._dimensions

    def _get_model(self):
        """Lazy-load the sentence-transformers model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as err:
                raise ImportError(
                    "sentence-transformers required. "
                    "Install with: pip install sentence-transformers"
                ) from err

            self._model = SentenceTransformer(
                self._model_name,
                device=self._device,
            )
            # Update dimensions from actual model
            self._dimensions = self._model.get_sentence_embedding_dimension()

            logger.info(
                "sentence_transformer_loaded",
                model=self._model_name,
                dimensions=self._dimensions,
            )

        return self._model

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        model = self._get_model()
        # sentence-transformers is sync, so we run it directly
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in a batch."""
        if not texts:
            return []

        model = self._get_model()

        logger.debug(
            "local_embedding_batch_request",
            model=self._model_name,
            count=len(texts),
        )

        # Batch encode is efficient for sentence-transformers
        embeddings = model.encode(texts, convert_to_numpy=True)

        logger.info(
            "local_embedding_batch_response",
            model=self._model_name,
            count=len(embeddings),
        )

        return [e.tolist() for e in embeddings]

    async def embed_with_metadata(self, text: str) -> EmbeddingResult:
        """Embed with metadata (limited for local models)."""
        vector = await self.embed(text)

        return EmbeddingResult(
            vectors=[vector],
            model=self._model_name,
            total_tokens=0,  # Not tracked for local models
            dimensions=len(vector),
        )


def create_embedding_service(
    provider: str = "openai",
    api_key: str | None = None,
    model: str | None = None,
    **kwargs: object,
) -> EmbeddingService:
    """Create an embedding service for the specified provider.

    Args:
        provider: Provider name (openai, local).
        api_key: Optional API key (for OpenAI).
        model: Optional model name.
        **kwargs: Additional provider-specific arguments.

    Returns:
        Configured embedding service.

    Raises:
        ValueError: If provider is not supported.
    """
    provider_lower = provider.lower()

    if provider_lower == "openai":
        return OpenAIEmbeddingService(
            api_key=api_key,
            model=model or "text-embedding-3-small",
            base_url=kwargs.get("base_url"),
        )
    if provider_lower in ("local", "sentence-transformers", "st"):
        return LocalEmbeddingService(
            model=model or "all-MiniLM-L6-v2",
            device=kwargs.get("device"),
        )
    raise ValueError(
        f"Unsupported embedding provider: {provider}. "
        "Supported: openai, local"
    )
