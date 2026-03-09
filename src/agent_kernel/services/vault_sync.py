"""Vault sync service for reuse across CLI and workflows."""

from __future__ import annotations

from typing import Any

import structlog

from agent_kernel.core.config import get_settings
from agent_kernel.memory.document_store import SQLiteDocumentStore
from agent_kernel.memory.graph_store import SQLiteGraphStore
from agent_kernel.memory.vector_store import SQLiteVectorStore
from agent_kernel.services.enrichment_registry import get_enrichment_registry
from agent_kernel.services.index_state import IndexStateStore
from agent_kernel.services.vault_indexer import IndexSummary, VaultIndexer
from agent_kernel.tools.builtin.obsidian import ObsidianVault

logger = structlog.get_logger(__name__)


async def run_vault_sync(
    *,
    force: bool = False,
    folder: str | None = None,
    inject_ids: bool = True,
    with_embeddings: bool = False,
    embedding_model: str | None = None,
    with_enrichment: bool = False,
    enrichment_model: str | None = None,
    vault_path: str | None = None,
    summarization_skip_override: str | None = None,
    summarize_all: bool = False,
) -> IndexSummary:
    """Run vault sync with optional enrichment and embeddings."""
    from agent_kernel.core.schemas.enrichment_config import EnrichmentThresholds

    settings = get_settings()
    data_dir = settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    resolved_vault_path = vault_path or settings.obsidian_vault_path
    if not resolved_vault_path:
        raise ValueError("No vault path specified (set OBSIDIAN_VAULT_PATH)")

    resolved_embedding_model = embedding_model or settings.embedding_model
    resolved_enrichment_model = enrichment_model or settings.enrichment_model

    enrichment_registry = get_enrichment_registry(
        config_dir=settings.configs_dir / "enrichment"
    )
    source_config = enrichment_registry.get_or_default("obsidian")

    if summarize_all:
        source_config = source_config.model_copy(
            update={
                "thresholds": EnrichmentThresholds(
                    min_char_count=0,
                    min_word_count=0,
                    excluded_paths=[],
                    excluded_tags=[],
                    excluded_classifications=[],
                    excluded_entity_types=[],
                )
            }
        )
    elif summarization_skip_override:
        updated_thresholds = source_config.thresholds.model_copy(
            update={"skip_behavior": summarization_skip_override}
        )
        source_config = source_config.model_copy(update={"thresholds": updated_thresholds})

    document_store = SQLiteDocumentStore(data_dir / "documents" / "documents.db")
    vector_store = SQLiteVectorStore(data_dir / "vectors" / "vectors.db")
    graph_store = SQLiteGraphStore(data_dir / "graph" / "graph.db")
    index_state_store = IndexStateStore(data_dir / "index_state.db")

    embedding_service = None
    if with_embeddings:
        from agent_kernel.services.embedding import OpenAIEmbeddingService

        api_key = settings.openai_api_key
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set (required for embeddings)")

        embedding_service = OpenAIEmbeddingService(
            api_key=api_key,
            model=resolved_embedding_model,
        )
        logger.info(
            "embedding_service_created",
            model=resolved_embedding_model,
            dimensions=embedding_service.dimensions,
        )

    enrichment_service = None
    if with_enrichment:
        from agent_kernel.services.enrichment import EnrichmentService
        from agent_kernel.services.llm import OpenAILLMService

        api_key = settings.openai_api_key
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set (required for enrichment)")

        llm_service = OpenAILLMService(
            api_key=api_key,
            default_model=resolved_enrichment_model,
        )
        enrichment_service = EnrichmentService(
            llm_service=llm_service,
            model=resolved_enrichment_model,
        )
        logger.info("enrichment_service_created", model=resolved_enrichment_model)

    vault = ObsidianVault(resolved_vault_path)
    indexer = VaultIndexer(
        vault=vault,
        document_store=document_store,
        graph_store=graph_store,
        vector_store=vector_store,
        embedding_service=embedding_service,
        index_state_store=index_state_store,
        enrichment_service=enrichment_service,
        enable_enrichment=with_enrichment,
        source_enrichment_config=source_config,
        enrichment_registry=enrichment_registry,
    )

    try:
        return await indexer.index_folder(
            folder=folder,
            force=force,
            inject_ids=inject_ids,
        )
    finally:
        document_store.close()
        vector_store.close()
        graph_store.close()
        index_state_store.close()
