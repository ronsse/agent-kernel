"""Agent Engine subsystem - pluggable LLM planners.

This module provides:
- AgentEngine protocol: Interface for all planners
- CustomEngine: Direct LLM-based planning
- EngineRegistry: Manage available engines
- ThinkingPolicyController: Reasoning budget decisions (v1.0.3)
- EscalationManager: Attempt → Gate → Escalate flow
- IntegrationTierRouter: Select integration depth for workflows (v1.0.8)
"""

from agent_kernel.engine.agent_engine import AgentEngine
from agent_kernel.engine.critic import CriticEngine, Critique
from agent_kernel.engine.custom_engine import CustomEngine
from agent_kernel.engine.integration_tier import (
    IntegrationTier,
    IntegrationTierRouter,
    TierDecision,
    TierRule,
    get_tier_router,
    select_integration_tier,
)
from agent_kernel.engine.registry import EngineRegistry
from agent_kernel.engine.thinking_policy import (
    EscalationAttempt,
    ThinkingPolicy,
    ThinkingPolicyController,
    ThinkingSession,
)
from agent_kernel.engine.adaptive_thinking import (
    AdaptiveThinkingPolicyController,
    AdaptiveThinkingSession,
    ModelPerformanceStats,
    WorkflowPerformanceStats,
    create_adaptive_controller,
)
from agent_kernel.engine.cost_anomaly import AnomalyReport, CostAnomalyDetector
from agent_kernel.engine.success_rate_router import ModelRecommendation, SuccessRateRouter
from agent_kernel.engine.thinking_metrics import ThinkingMetrics, compute_thinking_metrics

# Re-export from schemas for convenience
from agent_kernel.core.schemas.thinking import (
    ADAPTIVE_THINKING,
    DEEP_THINKING,
    STANDARD_THINKING,
    EscalationConfig,
    QualityGatesConfig,
    RetrievalConfig,
    ThinkingConfig,
    ThinkingTierConfig,
    VerificationConfig,
)
from agent_kernel.core.schemas.llm import ReasoningEffort

__all__ = [
    # Core engines
    "AgentEngine",
    "CriticEngine",
    "Critique",
    "CustomEngine",
    "EngineRegistry",
    # Thinking policy (v1.0.3)
    "ThinkingPolicy",
    "ThinkingPolicyController",
    "ThinkingSession",
    "EscalationAttempt",
    "ReasoningEffort",
    # Integration tier (v1.0.8)
    "IntegrationTier",
    "IntegrationTierRouter",
    "TierDecision",
    "TierRule",
    "get_tier_router",
    "select_integration_tier",
    # Adaptive thinking (v1.1.7 - trace-based optimization)
    "AdaptiveThinkingPolicyController",
    "AdaptiveThinkingSession",
    "WorkflowPerformanceStats",
    "ModelPerformanceStats",
    "create_adaptive_controller",
    # Config schemas
    "ThinkingConfig",
    "ThinkingTierConfig",
    "RetrievalConfig",
    "VerificationConfig",
    "EscalationConfig",
    "QualityGatesConfig",
    # Predefined configs
    "STANDARD_THINKING",
    "DEEP_THINKING",
    "ADAPTIVE_THINKING",
    # Feedback loops (v1.2)
    "CostAnomalyDetector",
    "AnomalyReport",
    "SuccessRateRouter",
    "ModelRecommendation",
    # Thinking metrics (v1.2)
    "ThinkingMetrics",
    "compute_thinking_metrics",
]
