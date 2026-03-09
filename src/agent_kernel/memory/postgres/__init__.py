"""PostgreSQL/Supabase implementations of memory stores.

Provides cloud-backed implementations of all memory store interfaces
using PostgreSQL (via Supabase) with psycopg2.

Requires: pip install psycopg2-binary
For vector support: enable pgvector extension in Supabase dashboard.
"""

from agent_kernel.memory.postgres.connection import PostgresConnectionPool
from agent_kernel.memory.postgres.document_store import PostgresDocumentStore
from agent_kernel.memory.postgres.vector_store import PostgresVectorStore
from agent_kernel.memory.postgres.graph_store import PostgresGraphStore
from agent_kernel.memory.postgres.event_log import PostgresEventLog
from agent_kernel.memory.postgres.entity_store import PostgresEntityStore
from agent_kernel.memory.postgres.experience_store import PostgresExperienceStore
from agent_kernel.memory.postgres.derivation_store import (
    PostgresDerivationMappingStore,
    PostgresSuppressionRegistry,
)

__all__ = [
    "PostgresConnectionPool",
    "PostgresDocumentStore",
    "PostgresVectorStore",
    "PostgresGraphStore",
    "PostgresEventLog",
    "PostgresEntityStore",
    "PostgresExperienceStore",
    "PostgresDerivationMappingStore",
    "PostgresSuppressionRegistry",
]
