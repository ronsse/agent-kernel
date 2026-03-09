"""Context subsystem - deterministic context assembly.

This module provides the ContextAssembler for gathering and
ranking relevant context from multiple sources.

v1.0.2 additions:
- ContextPackResolver for loading and matching context packs
- SourceRegistry for schema-aware filter validation
- BaselineRetrievalPlanner, InstructedRetrievalPlanner for retrieval planning
- RetrievalExecutor for directive execution
- RetrievalGateRunner and gates for quality checks

v1.0.4 additions:
- PlaybookResolver for matching behavioral playbooks
- PlaybookMatch, PlaybookResolutionResult for playbook resolution
"""

from agent_kernel.context.assembler import ContextAssembler
from agent_kernel.context.executor import (
    DirectiveResult,
    ExecutionResult,
    RetrievalExecutor,
)
from agent_kernel.context.gates import (
    CoverageGate,
    ExperienceWarningGate,
    PackPresenceGate,
    ParityGate,
    PlaybookCoverageGate,
    RecencyGate,
    RetrievalGate,
    RetrievalGateRunner,
    SchemaAwareFiltersGate,
    SourceConstraintEnforcementGate,
)
from agent_kernel.context.pack_resolver import ContextPackResolver
from agent_kernel.context.planner import (
    BaselineRetrievalPlanner,
    InstructedRetrievalPlanner,
    RetrievalPlanner,
)
from agent_kernel.context.playbook_resolver import (
    PlaybookMatch,
    PlaybookResolutionResult,
    PlaybookResolver,
    create_playbook_resolver,
)
from agent_kernel.context.source_registry import SourceRegistry

__all__ = [
    # Assembler
    "ContextAssembler",
    # Pack Resolver (v1.0.2)
    "ContextPackResolver",
    # Source Registry (v1.0.2)
    "SourceRegistry",
    # Planners (v1.0.2)
    "RetrievalPlanner",
    "BaselineRetrievalPlanner",
    "InstructedRetrievalPlanner",
    # Executor (v1.0.2)
    "RetrievalExecutor",
    "DirectiveResult",
    "ExecutionResult",
    # Gates (v1.0.2)
    "RetrievalGate",
    "RetrievalGateRunner",
    "PackPresenceGate",
    "SchemaAwareFiltersGate",
    "CoverageGate",
    "RecencyGate",
    "ParityGate",
    # v1.0.4 Gates
    "SourceConstraintEnforcementGate",
    "ExperienceWarningGate",
    "PlaybookCoverageGate",
    # Playbook Resolver (v1.0.4)
    "PlaybookResolver",
    "PlaybookMatch",
    "PlaybookResolutionResult",
    "create_playbook_resolver",
]
