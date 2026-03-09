"""
Agent Kernel - Framework-agnostic agent foundation.

A local-first, framework-agnostic foundation for building AI agent systems
with strict schema contracts, pluggable engines, and comprehensive tracing.
"""

try:
    from agent_kernel._version import __version__
except ImportError:
    __version__ = "0.0.0+unknown"

# Re-export key components for convenient access
from agent_kernel.context import ContextAssembler
from agent_kernel.core.config import Settings, get_settings
from agent_kernel.core.schemas import (
    ActionRequest,
    AgentProfile,
    ContextPacket,
    ContextRef,
    DecisionTrace,
    Plan,
    ToolCallRecord,
)
from agent_kernel.engine import AgentEngine, CustomEngine, EngineRegistry
from agent_kernel.executor import ApprovalGate, DeterministicExecutor
from agent_kernel.memory import (
    DocumentStore,
    EventLog,
    EventType,
    GraphStore,
    SQLiteDocumentStore,
    SQLiteEventLog,
    SQLiteGraphStore,
    SQLiteVectorStore,
    VectorStore,
)

# Services (LLM, embeddings, etc.)
from agent_kernel.services import (
    AnthropicLLMService,
    EmbeddingService,
    LLMService,
    OpenAIEmbeddingService,
    OpenAILLMService,
    create_embedding_service,
    create_llm_service,
)
from agent_kernel.tools import CapabilityRegistry, ToolBroker
from agent_kernel.tracing import MultiSinkTraceStore, TraceStore
from agent_kernel.workflows import WorkflowRunner, WorkflowSpec

__all__ = [
    # Version
    "__version__",
    # Config
    "Settings",
    "get_settings",
    # Schemas
    "ContextRef",
    "ContextPacket",
    "ActionRequest",
    "Plan",
    "ToolCallRecord",
    "DecisionTrace",
    "AgentProfile",
    # Memory
    "DocumentStore",
    "SQLiteDocumentStore",
    "VectorStore",
    "SQLiteVectorStore",
    "GraphStore",
    "SQLiteGraphStore",
    "EventLog",
    "SQLiteEventLog",
    "EventType",
    # Tools
    "CapabilityRegistry",
    "ToolBroker",
    # Context
    "ContextAssembler",
    # Engine
    "AgentEngine",
    "CustomEngine",
    "EngineRegistry",
    # Executor
    "DeterministicExecutor",
    "ApprovalGate",
    # Tracing
    "TraceStore",
    "MultiSinkTraceStore",
    # Workflows
    "WorkflowSpec",
    "WorkflowRunner",
    # Services
    "LLMService",
    "OpenAILLMService",
    "AnthropicLLMService",
    "create_llm_service",
    "EmbeddingService",
    "OpenAIEmbeddingService",
    "create_embedding_service",
]
