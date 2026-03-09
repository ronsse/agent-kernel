"""Workflow specification models.

Defines the structure for workflow YAML files.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from agent_kernel.core.schemas.base import KernelModel


class TriggerType(str, Enum):
    """Types of workflow triggers."""

    MANUAL = "manual"
    CRON = "cron"
    EVENT = "event"
    FILE_WATCH = "file_watch"
    WORKFLOW = "workflow"  # Triggered when another workflow completes


class WorkflowTrigger(KernelModel):
    """Trigger configuration for a workflow."""

    type: TriggerType = TriggerType.MANUAL
    schedule: str | None = None  # Cron expression for cron trigger
    event_type: str | None = None  # Event type for event trigger
    path: str | None = None  # Path for file_watch trigger
    patterns: list[str] | None = None  # Glob patterns for file_watch trigger
    source_workflow_id: str | None = None  # Workflow ID for workflow trigger
    # Whether to only trigger on successful completion (default: True)
    on_success_only: bool = True


class WorkflowStep(str, Enum):
    """Standard workflow steps."""

    VAULT_SYNC = "vault_sync"
    ASSEMBLE_CONTEXT = "assemble_context"
    PROPOSE_PLAN = "propose_plan"
    VALIDATE = "validate"
    GATE_APPROVALS = "gate_approvals"
    EXECUTE = "execute"
    WRITE_BACK = "write_back"
    EMIT_TRACE = "emit_trace"


class OnError(str, Enum):
    """Error handling behavior."""

    HALT = "halt"  # Stop workflow
    CONTINUE = "continue"  # Continue with next step
    RETRY = "retry"  # Retry failed step


class RetryConfig(KernelModel):
    """Configuration for retry behavior."""

    max_retries: int = 3
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    exponential_backoff: bool = True
    backoff_multiplier: float = 2.0
    retryable_errors: list[str] = Field(
        default_factory=lambda: ["timeout", "rate_limit", "temporary"]
    )

    def get_delay(self, attempt: int) -> float:
        """Get delay for a retry attempt.

        Args:
            attempt: The attempt number (1-based).

        Returns:
            Delay in seconds.
        """
        if not self.exponential_backoff:
            return self.initial_delay_seconds

        delay = self.initial_delay_seconds * (self.backoff_multiplier ** (attempt - 1))
        return min(delay, self.max_delay_seconds)


class WriteBackConfig(KernelModel):
    """Configuration for write-back step."""

    create_summary_note: bool = True
    update_graph: bool = True
    update_vectors: bool = False
    note_template: str | None = None
    track_processed: bool = False
    notify: list[str] = Field(default_factory=list)


class VaultSyncConfig(KernelModel):
    """Configuration for vault sync step."""

    force: bool = False
    folder: str | None = None
    inject_ids: bool = True
    with_embeddings: bool = False
    embedding_model: str | None = None
    with_enrichment: bool = False
    enrichment_model: str | None = None
    summarization_skip: str | None = None
    summarize_all: bool = False


class EmptyCheck(KernelModel):
    """Pre-check to skip workflow when there's no work to do.

    Before running the workflow steps, execute this capability and
    skip the workflow if the result is empty.
    """

    capability: str = Field(description="Capability to call for the check")
    args: dict[str, Any] = Field(default_factory=dict)
    empty_key: str | None = Field(
        default=None,
        description="JSON key to check in result; if None, checks top-level list",
    )


class WorkflowSpec(KernelModel):
    """Workflow specification loaded from YAML.

    Defines a complete workflow including trigger, steps, and behavior.
    """

    workflow_id: str
    name: str
    description: str = ""
    trigger: WorkflowTrigger = Field(default_factory=WorkflowTrigger)
    agent_profile_id: str
    steps: list[str | dict[str, Any]] = Field(default_factory=lambda: [
        "assemble_context",
        "propose_plan",
        "validate",
        "gate_approvals",
        "execute",
        "write_back",
        "emit_trace",
    ])
    on_error: OnError = OnError.HALT
    retry: RetryConfig = Field(default_factory=RetryConfig)
    write_back: WriteBackConfig = Field(default_factory=WriteBackConfig)
    vault_sync: VaultSyncConfig = Field(default_factory=VaultSyncConfig)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    context_config: dict[str, Any] = Field(default_factory=dict)
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    concurrency: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Workflows to trigger on completion (declarative chaining)
    on_complete: list[str] = Field(default_factory=list)

    # Integration tier override (v1.0.8)
    # 1 = RULE_BASED (no kernel), 2 = KERNEL_LITE, 3 = FULL_KERNEL
    # If not set, determined automatically by IntegrationTierRouter
    integration_tier: int | None = Field(
        default=None,
        ge=1,
        le=3,
        description="Integration depth: 1=rule-based, 2=kernel-lite, 3=full-kernel",
    )

    # Skip LLM planning for deterministic workflows (v1.0.8)
    # When True, uses deterministic sync instead of LLM plan generation
    skip_llm_planning: bool = Field(
        default=False,
        description="Skip LLM planning, use deterministic sync capability instead",
    )

    # Deterministic sync capability to use when skip_llm_planning=True
    deterministic_capability: str | None = Field(
        default=None,
        description="Capability to invoke directly (e.g., 'tasks.sync_to_graph@v1')",
    )

    # Empty-poll guard: skip workflow if pre-check finds no work
    empty_check: EmptyCheck | None = Field(
        default=None,
        description="Pre-check capability; skip workflow if result is empty",
    )

    def has_step(self, step: str) -> bool:
        """Check if workflow includes a step."""
        return step in self.steps
