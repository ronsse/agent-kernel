"""Executor subsystem - deterministic plan execution.

This module provides:
- DeterministicExecutor: Validates and executes plans
- ApprovalGate: Manages approval workflow
- QualityGateRunner: Deterministic plan validation
- Notifiers: Pluggable approval notification callbacks
"""

from agent_kernel.executor.approval import (
    ApprovalGate,
    ApprovalNotifyCallback,
    PendingApproval,
)
from agent_kernel.executor.executor import DeterministicExecutor
from agent_kernel.executor.notifiers import (
    log_only_approval_notifier,
)
from agent_kernel.executor.quality_gates import (
    GateFailure,
    GateResult,
    GateSeverity,
    QualityGateRunner,
)

__all__ = [
    "ApprovalGate",
    "ApprovalNotifyCallback",
    "DeterministicExecutor",
    "GateFailure",
    "GateResult",
    "GateSeverity",
    "PendingApproval",
    "QualityGateRunner",
    "log_only_approval_notifier",
]
