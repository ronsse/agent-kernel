"""Context Graph - the connective tissue between stores.

The context graph decomposes traces into traversable graph structure,
tracks knowledge nodes with freshness decay, and enables relevance-weighted
retrieval across episodic and semantic memory.

Key components:
- TraceDecomposer: Traces → TRAJECTORY + DECISION_EVENT nodes (event clock)
- ContextGraphIngestion: Multi-source ingestion orchestrator
- TypeRegistry: Tracks discovered node/edge types
- FreshnessCalculator: Time-decay relevance scoring
- ContextGraphHooks: Wire trace completion → decomposition
"""

from agent_kernel.context_graph.decomposer import (
    DecompositionResult,
    TraceDecomposer,
)
from agent_kernel.context_graph.freshness import FreshnessCalculator
from agent_kernel.context_graph.hooks import ContextGraphHooks
from agent_kernel.context_graph.ingestion import ContextGraphIngestion
from agent_kernel.context_graph.types import TypeRegistry

__all__ = [
    "ContextGraphHooks",
    "ContextGraphIngestion",
    "DecompositionResult",
    "FreshnessCalculator",
    "TraceDecomposer",
    "TypeRegistry",
]
