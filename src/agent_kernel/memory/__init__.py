"""Memory subsystem - document, vector, graph stores and event log.

This module provides interfaces and implementations for:
- Document storage with full-text search
- Vector embeddings for semantic search
- Graph store for entity relationships
- Append-only event log for audit trail
- Entity store for universal entity model (v1.0.4)
- Experience store for experience memory (v1.0.4)
"""

from agent_kernel.memory.document_store import DocumentStore, SQLiteDocumentStore
from agent_kernel.memory.derivation_store import (
    DerivationMappingStore,
    SuppressionRegistry,
)
from agent_kernel.memory.entity_store import EntityStore, SQLiteEntityStore
from agent_kernel.memory.event_log import (
    Event,
    EventLog,
    EventType,
    JSONLEventLog,
    SQLiteEventLog,
)
from agent_kernel.memory.experience_store import ExperienceStore, SQLiteExperienceStore
from agent_kernel.memory.experience_hooks import (
    ExperienceContext,
    ExperienceMatch,
    ExperienceMemoryHooks,
    create_experience_hooks,
    record_workflow_outcome,
)
from agent_kernel.memory.factory import StoreFactory
from agent_kernel.memory.graph_store import GraphStore, SQLiteGraphStore
from agent_kernel.memory.vector_store import (
    SQLiteVectorStore,
    VectorStore,
    create_vector_store,
)

# LanceDB store available only with [vectors] extra
try:
    from agent_kernel.memory.vector_store import (
        LANCEDB_AVAILABLE,
        LanceDBVectorStore,
    )
except ImportError:
    LANCEDB_AVAILABLE = False  # type: ignore[assignment]

__all__ = [
    # Factory
    "StoreFactory",
    # Event log
    "EventType",
    "Event",
    "EventLog",
    "SQLiteEventLog",
    "JSONLEventLog",
    # Document store
    "DocumentStore",
    "SQLiteDocumentStore",
    # Derivation store (v1.1.6)
    "DerivationMappingStore",
    "SuppressionRegistry",
    # Vector store
    "VectorStore",
    "SQLiteVectorStore",
    "LanceDBVectorStore",
    "create_vector_store",
    "LANCEDB_AVAILABLE",
    # Graph store
    "GraphStore",
    "SQLiteGraphStore",
    # Entity store (v1.0.4)
    "EntityStore",
    "SQLiteEntityStore",
    # Experience store (v1.0.4)
    "ExperienceStore",
    "SQLiteExperienceStore",
    # Experience hooks (v1.0.8)
    "ExperienceContext",
    "ExperienceMatch",
    "ExperienceMemoryHooks",
    "create_experience_hooks",
    "record_workflow_outcome",
]
