"""Custom exceptions for the Agent Kernel."""


class AgentKernelError(Exception):
    """Base exception for all Agent Kernel errors."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or "AGENT_KERNEL_ERROR"


# =============================================================================
# Schema Errors
# =============================================================================


class SchemaValidationError(AgentKernelError):
    """Raised when schema validation fails."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message, code="SCHEMA_VALIDATION_ERROR")
        self.errors = errors or []


class PlanValidationError(AgentKernelError):
    """Raised when plan validation fails."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message, code="PLAN_VALIDATION_ERROR")
        self.errors = errors or []


# =============================================================================
# Tool Errors
# =============================================================================


class ToolError(AgentKernelError):
    """Base exception for tool-related errors."""



class CapabilityNotFoundError(ToolError):
    """Raised when a capability is not registered."""

    def __init__(self, capability_name: str) -> None:
        super().__init__(
            f"Capability not found: {capability_name}",
            code="CAPABILITY_NOT_FOUND",
        )
        self.capability_name = capability_name


class CapabilityNotAllowedError(ToolError):
    """Raised when an agent tries to use a disallowed capability."""

    def __init__(self, capability_name: str, agent_profile_id: str) -> None:
        msg = (
            f"Capability '{capability_name}' not allowed for agent "
            f"'{agent_profile_id}'"
        )
        super().__init__(msg, code="CAPABILITY_NOT_ALLOWED")
        self.capability_name = capability_name
        self.agent_profile_id = agent_profile_id


class ToolExecutionError(ToolError):
    """Raised when tool execution fails."""

    def __init__(
        self,
        message: str,
        capability_name: str,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message, code="TOOL_EXECUTION_ERROR")
        self.capability_name = capability_name
        self.cause = cause


class ToolTimeoutError(ToolError):
    """Raised when tool execution times out."""

    def __init__(self, capability_name: str, timeout_ms: int) -> None:
        super().__init__(
            f"Tool '{capability_name}' timed out after {timeout_ms}ms",
            code="TOOL_TIMEOUT",
        )
        self.capability_name = capability_name
        self.timeout_ms = timeout_ms


class RateLimitedError(ToolError):
    """Raised when a capability is rate limited."""

    def __init__(self, capability_name: str, wait_seconds: float) -> None:
        super().__init__(
            f"Capability '{capability_name}' rate limited, "
            f"retry after {wait_seconds:.1f}s",
            code="RATE_LIMITED",
        )
        self.capability_name = capability_name
        self.wait_seconds = wait_seconds


# =============================================================================
# Approval Errors
# =============================================================================


class ApprovalError(AgentKernelError):
    """Base exception for approval-related errors."""



class ApprovalRequiredError(ApprovalError):
    """Raised when an action requires approval but none was provided."""

    def __init__(self, action_id: str, capability_name: str) -> None:
        super().__init__(
            f"Action '{action_id}' ({capability_name}) requires approval",
            code="APPROVAL_REQUIRED",
        )
        self.action_id = action_id
        self.capability_name = capability_name


class ApprovalDeniedError(ApprovalError):
    """Raised when an action's approval was denied."""

    def __init__(self, action_id: str, reason: str | None = None) -> None:
        super().__init__(
            f"Approval denied for action '{action_id}'" +
            (f": {reason}" if reason else ""),
            code="APPROVAL_DENIED",
        )
        self.action_id = action_id
        self.reason = reason


class InvalidApprovalTokenError(ApprovalError):
    """Raised when an approval token is invalid."""

    def __init__(self, action_id: str) -> None:
        super().__init__(
            f"Invalid approval token for action '{action_id}'",
            code="INVALID_APPROVAL_TOKEN",
        )
        self.action_id = action_id


# =============================================================================
# Engine Errors
# =============================================================================


class EngineError(AgentKernelError):
    """Base exception for agent engine errors."""



class EngineNotFoundError(EngineError):
    """Raised when an engine is not registered."""

    def __init__(self, engine_id: str) -> None:
        super().__init__(
            f"Engine not found: {engine_id}",
            code="ENGINE_NOT_FOUND",
        )
        self.engine_id = engine_id


class PlanGenerationError(EngineError):
    """Raised when plan generation fails."""

    def __init__(self, message: str, engine_id: str) -> None:
        super().__init__(message, code="PLAN_GENERATION_ERROR")
        self.engine_id = engine_id


class LLMCircuitOpenError(EngineError):
    """Raised when LLM circuit breaker is open."""

    def __init__(self, model: str) -> None:
        super().__init__(
            f"LLM circuit breaker open for model: {model}",
            code="LLM_CIRCUIT_OPEN",
        )
        self.model = model


# =============================================================================
# Context Errors
# =============================================================================


class ContextError(AgentKernelError):
    """Base exception for context-related errors."""



class ContextAssemblyError(ContextError):
    """Raised when context assembly fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="CONTEXT_ASSEMBLY_ERROR")


class CitationError(ContextError):
    """Raised when a citation references non-existent context."""

    def __init__(self, ref_id: str) -> None:
        super().__init__(
            f"Citation references unknown context: {ref_id}",
            code="CITATION_ERROR",
        )
        self.ref_id = ref_id


# =============================================================================
# Workflow Errors
# =============================================================================


class WorkflowError(AgentKernelError):
    """Base exception for workflow errors."""



class WorkflowNotFoundError(WorkflowError):
    """Raised when a workflow is not found."""

    def __init__(self, workflow_id: str) -> None:
        super().__init__(
            f"Workflow not found: {workflow_id}",
            code="WORKFLOW_NOT_FOUND",
        )
        self.workflow_id = workflow_id


class WorkflowExecutionError(WorkflowError):
    """Raised when workflow execution fails."""

    def __init__(self, message: str, workflow_id: str, step: str | None = None) -> None:
        super().__init__(message, code="WORKFLOW_EXECUTION_ERROR")
        self.workflow_id = workflow_id
        self.step = step


# =============================================================================
# Storage Errors
# =============================================================================


class StorageError(AgentKernelError):
    """Base exception for storage-related errors."""



class TraceNotFoundError(StorageError):
    """Raised when a trace is not found."""

    def __init__(self, trace_id: str) -> None:
        super().__init__(
            f"Trace not found: {trace_id}",
            code="TRACE_NOT_FOUND",
        )
        self.trace_id = trace_id


class DocumentNotFoundError(StorageError):
    """Raised when a document is not found."""

    def __init__(self, doc_id: str) -> None:
        super().__init__(
            f"Document not found: {doc_id}",
            code="DOCUMENT_NOT_FOUND",
        )
        self.doc_id = doc_id


# =============================================================================
# Validation Errors
# =============================================================================


class ConfigValidationError(AgentKernelError):
    """Raised when configuration validation fails."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message, code="CONFIG_VALIDATION_ERROR")
        self.errors = errors or []


class SkillValidationError(AgentKernelError):
    """Raised when skill validation fails."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message, code="SKILL_VALIDATION_ERROR")
        self.errors = errors or []
