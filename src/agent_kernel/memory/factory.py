"""Store factory - creates backend-appropriate store instances.

Selects between SQLite (local) and PostgreSQL (Supabase cloud) backends
based on the store_backend setting.

Usage:
    from agent_kernel.memory.factory import StoreFactory
    from agent_kernel.core.config import get_settings

    settings = get_settings()
    factory = StoreFactory(settings)

    doc_store = factory.create_document_store()
    vector_store = factory.create_vector_store()
    graph_store = factory.create_graph_store()
    # ... etc.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.config import Settings

logger = structlog.get_logger(__name__)


class StoreFactory:
    """Factory for creating store instances based on backend configuration.

    Supports two backends:
    - "sqlite": Local SQLite databases (default, zero-config)
    - "postgres": PostgreSQL via Supabase (cloud, requires credentials)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: Any = None  # Lazy-initialized Postgres pool

    @property
    def backend(self) -> str:
        """Get the configured backend type."""
        return self._settings.store_backend

    def _get_pool(self) -> Any:
        """Get or create the PostgreSQL connection pool."""
        if self._pool is not None:
            return self._pool

        from agent_kernel.memory.postgres.connection import PostgresConnectionPool

        pg_url = self._settings.postgres_url
        if not pg_url:
            msg = (
                "PostgreSQL URL not configured. Set DATABASE_URL to a postgresql:// URL "
                "or set SUPABASE_DB_HOST and SUPABASE_DB_PASSWORD."
            )
            raise ValueError(msg)

        self._pool = PostgresConnectionPool(
            pg_url,
            min_connections=self._settings.postgres_min_connections,
            max_connections=self._settings.postgres_max_connections,
        )
        return self._pool

    def create_document_store(self) -> Any:
        """Create a DocumentStore instance."""
        if self.backend == "postgres":
            from agent_kernel.memory.postgres.document_store import PostgresDocumentStore
            return PostgresDocumentStore(self._get_pool())

        from agent_kernel.memory.document_store import SQLiteDocumentStore
        db_path = Path(self._settings.document_store_path) / "documents.db"
        return SQLiteDocumentStore(db_path)

    def create_vector_store(self) -> Any:
        """Create a VectorStore instance."""
        if self.backend == "postgres":
            from agent_kernel.memory.postgres.vector_store import PostgresVectorStore
            return PostgresVectorStore(
                self._get_pool(),
                dimensions=self._settings.embedding_dimensions,
            )

        from agent_kernel.memory.vector_store import create_vector_store
        db_path = Path(self._settings.document_store_path) / "vectors"
        return create_vector_store(db_path)

    def create_graph_store(self) -> Any:
        """Create a GraphStore instance."""
        if self.backend == "postgres":
            from agent_kernel.memory.postgres.graph_store import PostgresGraphStore
            return PostgresGraphStore(self._get_pool())

        from agent_kernel.memory.graph_store import SQLiteGraphStore
        db_path = Path(self._settings.document_store_path) / "graph.db"
        return SQLiteGraphStore(db_path)

    def create_event_log(self) -> Any:
        """Create an EventLog instance."""
        if self.backend == "postgres":
            from agent_kernel.memory.postgres.event_log import PostgresEventLog
            return PostgresEventLog(self._get_pool())

        from agent_kernel.memory.event_log import SQLiteEventLog
        db_path = Path(self._settings.event_log_path) / "events.db"
        return SQLiteEventLog(db_path)

    def create_entity_store(self) -> Any:
        """Create an EntityStore instance."""
        if self.backend == "postgres":
            from agent_kernel.memory.postgres.entity_store import PostgresEntityStore
            return PostgresEntityStore(self._get_pool())

        from agent_kernel.memory.entity_store import SQLiteEntityStore
        db_path = Path(self._settings.document_store_path) / "entities.db"
        return SQLiteEntityStore(db_path)

    def create_experience_store(self) -> Any:
        """Create an ExperienceStore instance."""
        if self.backend == "postgres":
            from agent_kernel.memory.postgres.experience_store import PostgresExperienceStore
            return PostgresExperienceStore(self._get_pool())

        from agent_kernel.memory.experience_store import SQLiteExperienceStore
        db_path = Path(self._settings.document_store_path) / "experience.db"
        return SQLiteExperienceStore(db_path)

    def create_derivation_mapping_store(self) -> Any:
        """Create a DerivationMappingStore instance."""
        if self.backend == "postgres":
            from agent_kernel.memory.postgres.derivation_store import PostgresDerivationMappingStore
            return PostgresDerivationMappingStore(self._get_pool())

        from agent_kernel.memory.derivation_store import DerivationMappingStore
        db_path = Path(self._settings.document_store_path) / "entities.db"
        return DerivationMappingStore(db_path)

    def create_suppression_registry(self) -> Any:
        """Create a SuppressionRegistry instance."""
        if self.backend == "postgres":
            from agent_kernel.memory.postgres.derivation_store import PostgresSuppressionRegistry
            return PostgresSuppressionRegistry(self._get_pool())

        from agent_kernel.memory.derivation_store import SuppressionRegistry
        db_path = Path(self._settings.document_store_path) / "entities.db"
        return SuppressionRegistry(db_path)

    def create_trace_store(self) -> Any:
        """Create a TraceStore instance.

        For postgres backend, returns a PostgresTraceSink.
        For sqlite backend, returns a MultiSinkTraceStore with SQLite + optional JSONL.
        """
        if self.backend == "postgres":
            from agent_kernel.tracing.sinks.postgres_sink import PostgresTraceSink
            primary = PostgresTraceSink(self._get_pool())

            # Optionally add JSONL sink for local backup
            if self._settings.trace_jsonl_enabled:
                from agent_kernel.tracing.sinks.jsonl_sink import JSONLTraceSink
                from agent_kernel.tracing.trace_store import MultiSinkTraceStore
                jsonl_sink = JSONLTraceSink(self._settings.trace_jsonl_path)
                return MultiSinkTraceStore(primary, [jsonl_sink])

            return primary

        from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink
        trace_db = Path(self._settings.trace_store_path) / "traces.db"
        primary = SQLiteTraceSink(trace_db)

        if self._settings.trace_jsonl_enabled:
            from agent_kernel.tracing.sinks.jsonl_sink import JSONLTraceSink
            from agent_kernel.tracing.trace_store import MultiSinkTraceStore
            jsonl_sink = JSONLTraceSink(self._settings.trace_jsonl_path)
            return MultiSinkTraceStore(primary, [jsonl_sink])

        return primary

    def close(self) -> None:
        """Close the connection pool if postgres backend."""
        if self._pool is not None:
            self._pool.close()
            self._pool = None
            logger.info("store_factory_pool_closed")
