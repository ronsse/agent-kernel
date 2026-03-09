"""Tool Broker - the central execution gateway for all tools.

The broker is the ONLY place where tools are executed. It handles:
- Input validation against capability schemas
- Allowlist enforcement per AgentProfile
- Approval policy gating
- Execution via adapters with automatic retry
- Circuit breaker for failing tools
- ToolCallRecord logging
"""

from __future__ import annotations

from typing import Any

import jsonschema
import structlog

from agent_kernel.core.errors import (
    ApprovalRequiredError,
    CapabilityNotAllowedError,
    CapabilityNotFoundError,
    RateLimitedError,
    ToolExecutionError,
)
from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas import (
    AgentProfile,
    CallStatus,
    CapabilityDef,
    ErrorRecord,
    ToolCallRecord,
    normalize_side_effect_level,
)
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.memory.event_log import EventLog, EventType
from agent_kernel.tools.adapters.base import ToolAdapter
from agent_kernel.tools.adapters.local_function import LocalFunctionAdapter
from agent_kernel.tools.registry import CapabilityRegistry
from agent_kernel.tools.retry import (
    CircuitBreakerRegistry,
    CircuitOpenError,
    RetryConfig,
    retry_with_backoff,
)

logger = structlog.get_logger(__name__)


class ToolBroker:
    """Central gateway for tool execution.

    The broker enforces all policies and logging before and after
    tool execution. No component should call tools directly.
    """

    def __init__(
        self,
        registry: CapabilityRegistry,
        event_log: EventLog | None = None,
        retry_config: RetryConfig | None = None,
        enable_circuit_breaker: bool = True,
        timeout_manager: Any | None = None,
        enable_rate_limiting: bool = True,
        idempotency_store: Any | None = None,
    ) -> None:
        """Initialize the tool broker.

        Args:
            registry: The capability registry.
            event_log: Optional event log for recording events.
            retry_config: Configuration for automatic retries. None disables retries.
            enable_circuit_breaker: Whether to enable circuit breaker for failing tools.
            timeout_manager: Optional AdaptiveTimeoutManager for P99-based timeouts.
            enable_rate_limiting: Whether to enforce per-capability rate limits.
            idempotency_store: Optional IdempotencyStore for deduplication.
        """
        from agent_kernel.tools.rate_limiter import RateLimiterRegistry

        self._registry = registry
        self._event_log = event_log
        self._adapters: list[ToolAdapter] = []
        self._local_adapter = LocalFunctionAdapter()
        self._adapters.append(self._local_adapter)
        self._tool_call_records: list[ToolCallRecord] = []

        # Retry, circuit breaker, and adaptive timeout configuration
        self._retry_config = retry_config
        self._circuit_breakers = CircuitBreakerRegistry() if enable_circuit_breaker else None
        self._timeout_manager = timeout_manager

        # Rate limiting and idempotency
        self._rate_limiters = RateLimiterRegistry() if enable_rate_limiting else None
        self._idempotency_store = idempotency_store

        logger.info(
            "tool_broker_initialized",
            retry_enabled=retry_config is not None,
            circuit_breaker_enabled=enable_circuit_breaker,
            rate_limiting_enabled=enable_rate_limiting,
            idempotency_enabled=idempotency_store is not None,
        )

    @property
    def registry(self) -> CapabilityRegistry:
        """Get the capability registry."""
        return self._registry

    @property
    def local_adapter(self) -> LocalFunctionAdapter:
        """Get the local function adapter for registering functions."""
        return self._local_adapter

    def add_adapter(self, adapter: ToolAdapter) -> None:
        """Add a tool adapter.

        Args:
            adapter: The adapter to add.
        """
        self._adapters.append(adapter)
        logger.debug("adapter_added", adapter_type=type(adapter).__name__)

    def get_recent_calls(self, limit: int = 50) -> list[ToolCallRecord]:
        """Get recent tool call records.

        Args:
            limit: Maximum records to return.

        Returns:
            List of recent ToolCallRecords.
        """
        return self._tool_call_records[-limit:]

    def clear_records(self) -> None:
        """Clear in-memory tool call records."""
        self._tool_call_records.clear()

    def get_circuit_breaker_states(self) -> dict[str, str]:
        """Get current state of all circuit breakers.

        Returns:
            Dict mapping capability names to their circuit states.
            Empty dict if circuit breaker is disabled.
        """
        if self._circuit_breakers is None:
            return {}
        return self._circuit_breakers.get_all_states()

    def get_open_circuits(self) -> list[str]:
        """Get list of capabilities with open circuits.

        Returns:
            List of capability names where circuit is open.
            Empty list if circuit breaker is disabled.
        """
        if self._circuit_breakers is None:
            return []
        return self._circuit_breakers.get_open_circuits()

    def reset_circuit_breaker(self, capability_name: str) -> bool:
        """Reset circuit breaker for a specific capability.

        Args:
            capability_name: The capability to reset.

        Returns:
            True if breaker was found and reset, False otherwise.
        """
        if self._circuit_breakers is None:
            return False
        return self._circuit_breakers.reset(capability_name)

    def reset_all_circuit_breakers(self) -> None:
        """Reset all circuit breakers."""
        if self._circuit_breakers is not None:
            self._circuit_breakers.reset_all()

    def get_rate_limit_stats(self) -> dict[str, Any]:
        """Get current rate limit status for all registered capabilities.

        Returns:
            Dict mapping capability names to their RateLimitResult.
            Empty dict if rate limiting is disabled.
        """
        if self._rate_limiters is None:
            return {}
        return self._rate_limiters.get_all_stats()

    def validate_input(
        self,
        capability: CapabilityDef,
        args: dict[str, Any],
    ) -> list[str]:
        """Validate input arguments against capability schema.

        Args:
            capability: The capability definition.
            args: The input arguments.

        Returns:
            List of validation errors (empty if valid).
        """
        if not capability.input_schema:
            return []

        try:
            jsonschema.validate(instance=args, schema=capability.input_schema)
            return []
        except jsonschema.ValidationError as e:
            return [e.message]
        except jsonschema.SchemaError as e:
            logger.error(
                "invalid_capability_schema",
                capability_name=capability.capability_name,
                error=str(e),
            )
            return [f"Invalid capability schema: {e.message}"]

    def validate_output(
        self,
        capability: CapabilityDef,
        output: dict[str, Any],
    ) -> list[str]:
        """Validate output against capability schema.

        Args:
            capability: The capability definition.
            output: The tool output.

        Returns:
            List of validation errors (empty if valid).
        """
        if not capability.output_schema:
            return []

        try:
            jsonschema.validate(instance=output, schema=capability.output_schema)
            return []
        except jsonschema.ValidationError as e:
            return [e.message]

    def check_allowlist(
        self,
        capability_name: str,
        agent_profile: AgentProfile,
    ) -> bool:
        """Check if capability is allowed for agent.

        Args:
            capability_name: The capability name.
            agent_profile: The agent profile.

        Returns:
            True if allowed, False otherwise.
        """
        return agent_profile.can_use_capability(capability_name)

    def check_approval_required(
        self,
        capability: CapabilityDef,
        agent_profile: AgentProfile,
    ) -> bool:
        """Check if execution requires approval.

        Args:
            capability: The capability definition.
            agent_profile: The agent profile.

        Returns:
            True if approval is required.
        """
        # Check if capability requires approval by default
        if capability.requires_approval_default:
            return True

        # Check agent profile's approval policy
        if agent_profile.requires_approval_for(capability.capability_name):
            return True

        # Check side effect level
        effective_side_effect = normalize_side_effect_level(
            capability.side_effect_level
        )
        if effective_side_effect not in agent_profile.approval_policy.auto_approve_side_effects:
            return True

        return False

    async def execute(
        self,
        capability_name: str,
        args: dict[str, Any],
        agent_profile: AgentProfile,
        action_id: str | None = None,
        approval_token: str | None = None,
    ) -> ToolCallRecord:
        """Execute a tool capability.

        This is the main entry point for tool execution.

        Args:
            capability_name: The capability to execute.
            args: Input arguments.
            agent_profile: The agent profile for policy enforcement.
            action_id: Optional related action ID.
            approval_token: Optional approval token if required.

        Returns:
            ToolCallRecord with execution details.

        Raises:
            CapabilityNotFoundError: If capability not registered.
            CapabilityNotAllowedError: If not in agent's allowlist.
            ApprovalRequiredError: If approval needed but not provided.
            ToolExecutionError: If execution fails.
        """
        tool_call_id = generate_ulid()
        started_at = utc_now()

        # Log start event
        if self._event_log:
            self._event_log.emit(
                EventType.TOOL_CALLED,
                source="tool_broker",
                entity_id=tool_call_id,
                entity_type="tool_call",
                data={
                    "capability_name": capability_name,
                    "agent_profile_id": agent_profile.agent_profile_id,
                },
            )

        try:
            # Get capability
            capability = self._registry.get(capability_name)
            if capability is None:
                raise CapabilityNotFoundError(capability_name)

            # Check allowlist
            if not self.check_allowlist(capability_name, agent_profile):
                raise CapabilityNotAllowedError(
                    capability_name,
                    agent_profile.agent_profile_id,
                )

            # Validate input
            input_errors = self.validate_input(capability, args)
            if input_errors:
                raise ToolExecutionError(
                    f"Input validation failed: {'; '.join(input_errors)}",
                    capability_name,
                )

            # Check rate limit
            if self._rate_limiters is not None and capability.rate_limit is not None:
                limiter = self._rate_limiters.get_limiter(
                    capability_name, capability.rate_limit
                )
                rl_result = limiter.check_and_record()
                if not rl_result.allowed:
                    ended_at = utc_now()
                    duration_ms = int((ended_at - started_at).total_seconds() * 1000)
                    record = ToolCallRecord(
                        tool_call_id=tool_call_id,
                        capability_name=capability_name,
                        started_at=started_at,
                        ended_at=ended_at,
                        duration_ms=duration_ms,
                        input=self._redact_input(capability, args),
                        output={},
                        status=CallStatus.ERROR,
                        error=ErrorRecord(
                            code="RATE_LIMITED",
                            message=(
                                f"Rate limited: retry after {rl_result.wait_seconds:.1f}s"
                            ),
                            retryable=True,
                        ),
                        related_action_id=action_id,
                    )
                    self._tool_call_records.append(record)
                    if self._event_log:
                        self._event_log.emit(
                            EventType.TOOL_FAILED,
                            source="tool_broker",
                            entity_id=tool_call_id,
                            entity_type="tool_call",
                            data={
                                "error_code": "RATE_LIMITED",
                                "wait_seconds": rl_result.wait_seconds,
                            },
                        )
                    return record

            # Check idempotency
            idempotency_key = args.get("idempotency_key")
            if self._idempotency_store is not None and idempotency_key:
                idem_result = self._idempotency_store.check(idempotency_key)
                if idem_result.is_duplicate:
                    ended_at = utc_now()
                    duration_ms = int((ended_at - started_at).total_seconds() * 1000)
                    record = ToolCallRecord(
                        tool_call_id=tool_call_id,
                        capability_name=capability_name,
                        started_at=started_at,
                        ended_at=ended_at,
                        duration_ms=duration_ms,
                        input=self._redact_input(capability, args),
                        output={
                            "deduplicated": True,
                            "original_tool_call_id": idem_result.original_tool_call_id,
                        },
                        status=CallStatus.SKIPPED,
                        related_action_id=action_id,
                    )
                    self._tool_call_records.append(record)
                    return record

            # Check approval
            if self.check_approval_required(capability, agent_profile):
                if approval_token is None:
                    raise ApprovalRequiredError(action_id or tool_call_id, capability_name)

            # Find adapter
            adapter = self._find_adapter(capability.adapter_type)
            if adapter is None:
                raise ToolExecutionError(
                    f"No adapter for type: {capability.adapter_type}",
                    capability_name,
                )

            # Check circuit breaker
            if self._circuit_breakers is not None:
                breaker = self._circuit_breakers.get_breaker(capability_name)
                if not breaker.allow_request():
                    raise CircuitOpenError(capability_name)

            # Resolve timeout: adaptive if available, else capability default
            timeout_ms = capability.timeout_ms
            if self._timeout_manager is not None:
                timeout_ms = self._timeout_manager.get_timeout(
                    capability_name, capability.timeout_ms
                )

            # Execute with optional retry
            if self._retry_config is not None:
                result, retry_stats = await retry_with_backoff(
                    adapter.execute,
                    capability_name,
                    args,
                    timeout_ms,
                    config=self._retry_config,
                    is_retryable=lambda r: r.retryable and not r.success,
                )
                if retry_stats.retries > 0:
                    logger.info(
                        "tool_execution_retried",
                        capability_name=capability_name,
                        total_attempts=retry_stats.total_attempts,
                        retries=retry_stats.retries,
                        total_delay_ms=retry_stats.total_delay_ms,
                        final_success=retry_stats.final_success,
                    )
            else:
                result = await adapter.execute(
                    capability_name,
                    args,
                    timeout_ms,
                )

            # Record to circuit breaker
            if self._circuit_breakers is not None:
                breaker = self._circuit_breakers.get_breaker(capability_name)
                if result.success:
                    breaker.record_success()
                elif result.retryable:
                    # Only count retryable failures toward circuit breaker
                    breaker.record_failure()

            # Calculate duration
            ended_at = utc_now()
            duration_ms = int((ended_at - started_at).total_seconds() * 1000)

            # Build record
            if result.success:
                # Validate output
                output_errors = self.validate_output(capability, result.output)
                if output_errors:
                    logger.warning(
                        "output_validation_failed",
                        capability_name=capability_name,
                        errors=output_errors,
                    )

                record = ToolCallRecord(
                    tool_call_id=tool_call_id,
                    capability_name=capability_name,
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=duration_ms,
                    input=self._redact_input(capability, args),
                    output=self._redact_output(capability, result.output),
                    status=CallStatus.SUCCESS,
                    related_action_id=action_id,
                )

                if self._event_log:
                    self._event_log.emit(
                        EventType.TOOL_SUCCEEDED,
                        source="tool_broker",
                        entity_id=tool_call_id,
                        entity_type="tool_call",
                        data={"duration_ms": duration_ms},
                    )

                # Record idempotency key after success
                if (
                    self._idempotency_store is not None
                    and idempotency_key
                ):
                    self._idempotency_store.record(
                        idempotency_key, tool_call_id, capability_name
                    )
            else:
                record = ToolCallRecord(
                    tool_call_id=tool_call_id,
                    capability_name=capability_name,
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=duration_ms,
                    input=self._redact_input(capability, args),
                    output={},
                    status=CallStatus.ERROR,
                    error=ErrorRecord(
                        code=result.error_code or "EXECUTION_ERROR",
                        message=result.error or "Unknown error",
                        retryable=result.retryable,
                    ),
                    related_action_id=action_id,
                )

                if self._event_log:
                    self._event_log.emit(
                        EventType.TOOL_FAILED,
                        source="tool_broker",
                        entity_id=tool_call_id,
                        entity_type="tool_call",
                        data={
                            "error_code": result.error_code,
                            "error": result.error,
                        },
                    )

            self._tool_call_records.append(record)
            return record

        except (CapabilityNotFoundError, CapabilityNotAllowedError, ApprovalRequiredError):
            # Re-raise policy errors
            raise

        except ToolExecutionError:
            # Re-raise execution errors
            raise

        except Exception as e:
            # Wrap unexpected errors
            ended_at = utc_now()
            duration_ms = int((ended_at - started_at).total_seconds() * 1000)

            record = ToolCallRecord(
                tool_call_id=tool_call_id,
                capability_name=capability_name,
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=duration_ms,
                input=args,
                output={},
                status=CallStatus.ERROR,
                error=ErrorRecord(
                    code="UNEXPECTED_ERROR",
                    message=str(e),
                    retryable=False,
                ),
                related_action_id=action_id,
            )
            self._tool_call_records.append(record)

            if self._event_log:
                self._event_log.emit(
                    EventType.TOOL_FAILED,
                    source="tool_broker",
                    entity_id=tool_call_id,
                    entity_type="tool_call",
                    data={"error": str(e)},
                )

            raise ToolExecutionError(str(e), capability_name, e)

    def _find_adapter(self, adapter_type: str) -> ToolAdapter | None:
        """Find an adapter that supports the given type."""
        for adapter in self._adapters:
            if adapter.supports(adapter_type):
                return adapter
        return None

    def _redact_input(
        self,
        capability: CapabilityDef,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Redact sensitive fields from input."""
        if not capability.redaction_policy:
            return args

        redacted = dict(args)
        for field in capability.redaction_policy.redact_fields:
            if field in redacted:
                redacted[field] = "[REDACTED]"

        return redacted

    def _redact_output(
        self,
        capability: CapabilityDef,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        """Redact sensitive fields from output."""
        if not capability.redaction_policy:
            return output

        redacted = dict(output)
        for field in capability.redaction_policy.redact_fields:
            if field in redacted:
                redacted[field] = "[REDACTED]"

        return redacted
