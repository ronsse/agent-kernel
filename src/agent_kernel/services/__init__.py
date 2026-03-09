"""Services module - External service integrations.

This module provides:
- LLM service for language model completions
- Embedding service for vector embeddings
- Vault indexer for Obsidian integration
- Index state tracking for eventual consistency (v1.0.1)
- LLM enrichment for auto.* fields (v1.0.1)
- Enrichment config registry for source-specific configs (v1.0.5)
- Task parser for Obsidian markdown tasks (v1.0.5)
- Task manager for unified task management (v1.0.5)
- Resource extraction service for linked file summarization (v1.0.6)
"""

from agent_kernel.services.embedding import (
    EmbeddingResult,
    EmbeddingService,
    LocalEmbeddingService,
    OpenAIEmbeddingService,
    create_embedding_service,
)
from agent_kernel.services.enrichment import (
    DEFAULT_CLASSIFICATIONS,
    EnrichmentResult,
    EnrichmentService,
)
from agent_kernel.services.enrichment_registry import (
    EnrichmentConfigRegistry,
    get_enrichment_registry,
)
from agent_kernel.services.index_state import (
    EntityIndexState,
    IndexStateStore,
    IndexStatus,
)
from agent_kernel.services.llm import (
    AnthropicLLMService,
    CachedLLMService,
    LLMResponse,
    LLMService,
    OpenAILLMService,
    create_llm_service,
)
from agent_kernel.services.llm_cache import CacheEntry, LLMSemanticCache
from agent_kernel.services.task_parser import (
    ObsidianTaskParser,
    ParsedTask,
    ParseResult,
    TaskFormat,
    TaskParser,  # Backwards compat alias
    TaskRenderer,
    extract_tasks,
    get_task_parser,
    get_task_renderer,
)
from agent_kernel.services.task_manager import (
    SortOrder,
    TaskAction,
    TaskFilter,
    TaskManager,
    TaskView,
    get_task_manager,
)
from agent_kernel.services.vault_indexer import (
    IndexResult,
    IndexSummary,
    VaultIndexer,
)
from agent_kernel.services.vault_watcher import (
    VaultWatcher,
    create_vault_watcher,
)
from agent_kernel.services.resource_extraction import (
    ExtractionResult,
    ResourceExtractionService,
    ResourceExtractor,
    ResourceMetadata,
    ResourceSummary,
    ResourceType,
)
from agent_kernel.services.experience_miner import ExperienceMiner
from agent_kernel.services.approval_optimizer import (
    ApprovalAnalysis,
    ApprovalPolicyOptimizer,
    PolicyRecommendation,
)
from agent_kernel.services.health import (
    ComponentHealth,
    ComponentStatus,
    HealthChecker,
    SystemHealth,
)
from agent_kernel.services.workflow_debug import (
    WorkflowDebugInfo,
    collect_debug_info,
)

__all__ = [
    # LLM
    "LLMService",
    "LLMResponse",
    "OpenAILLMService",
    "AnthropicLLMService",
    "CachedLLMService",
    "create_llm_service",
    # LLM Cache (v1.2)
    "LLMSemanticCache",
    "CacheEntry",
    # Embedding
    "EmbeddingService",
    "EmbeddingResult",
    "OpenAIEmbeddingService",
    "LocalEmbeddingService",
    "create_embedding_service",
    # Enrichment (v1.0.1)
    "EnrichmentService",
    "EnrichmentResult",
    "DEFAULT_CLASSIFICATIONS",
    # Enrichment Registry (v1.0.5)
    "EnrichmentConfigRegistry",
    "get_enrichment_registry",
    # Vault Indexer
    "VaultIndexer",
    "IndexResult",
    "IndexSummary",
    # Vault Watcher
    "VaultWatcher",
    "create_vault_watcher",
    # Index State (v1.0.1)
    "IndexStateStore",
    "EntityIndexState",
    "IndexStatus",
    # Task Parser (v1.0.5)
    "ObsidianTaskParser",
    "TaskParser",  # Backwards compat alias
    "ParsedTask",
    "ParseResult",
    "TaskFormat",
    "TaskRenderer",
    "extract_tasks",
    "get_task_parser",
    "get_task_renderer",
    # Task Manager (v1.0.5)
    "TaskManager",
    "TaskView",
    "TaskAction",
    "TaskFilter",
    "SortOrder",
    "get_task_manager",
    # Resource Extraction (v1.0.6)
    "ResourceExtractionService",
    "ResourceExtractor",
    "ExtractionResult",
    "ResourceSummary",
    "ResourceMetadata",
    "ResourceType",
    # Health Checker (v1.2)
    "HealthChecker",
    "SystemHealth",
    "ComponentHealth",
    "ComponentStatus",
    # Experience Miner (v1.2)
    "ExperienceMiner",
    # Approval Policy Optimizer (v1.2)
    "ApprovalPolicyOptimizer",
    "ApprovalAnalysis",
    "PolicyRecommendation",
    # Workflow Debug
    "WorkflowDebugInfo",
    "collect_debug_info",
]
