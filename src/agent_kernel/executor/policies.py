"""Policy Configuration System.

Defines and manages policies for:
- Approval requirements
- Rate limiting
- Data redaction
- Scope restrictions
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field

from agent_kernel.core.schemas.base import utc_now

logger = structlog.get_logger(__name__)


class ApprovalMode(str, Enum):
    """Approval requirement modes."""

    ALWAYS = "always"  # Always require approval
    NEVER = "never"  # Never require approval
    CONDITIONAL = "conditional"  # Based on conditions
    DEFAULT = "default"  # Use capability default


class RedactionMode(str, Enum):
    """Redaction modes for sensitive data."""

    MASK = "mask"  # Replace with asterisks
    HASH = "hash"  # Replace with hash
    REMOVE = "remove"  # Remove entirely
    TRUNCATE = "truncate"  # Show first/last few chars


class ApprovalCondition(BaseModel):
    """Condition for conditional approval."""

    field: str  # Field to check
    operator: str  # eq, ne, gt, lt, contains, matches
    value: Any  # Value to compare


class ApprovalPolicy(BaseModel):
    """Policy for approval requirements."""

    name: str
    description: str = ""
    mode: ApprovalMode = ApprovalMode.DEFAULT
    conditions: list[ApprovalCondition] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)  # Glob patterns
    agents: list[str] = Field(default_factory=list)  # Agent profile IDs
    enabled: bool = True

    def matches_capability(self, capability_name: str) -> bool:
        """Check if policy matches a capability."""
        if not self.capabilities:
            return True

        for pattern in self.capabilities:
            if self._matches_glob(pattern, capability_name):
                return True
        return False

    def matches_agent(self, agent_profile_id: str) -> bool:
        """Check if policy matches an agent."""
        if not self.agents:
            return True
        return agent_profile_id in self.agents

    def evaluate_conditions(self, args: dict[str, Any]) -> bool:
        """Evaluate conditions against args.

        Returns True if ALL conditions match (approval required).
        """
        if not self.conditions:
            return True

        for condition in self.conditions:
            value = args.get(condition.field)
            if not self._evaluate_condition(condition, value):
                return False
        return True

    @staticmethod
    def _matches_glob(pattern: str, name: str) -> bool:
        """Match a glob pattern against a name."""
        # Convert glob to regex
        regex = pattern.replace(".", r"\.").replace("*", ".*")
        return bool(re.match(f"^{regex}$", name))

    @staticmethod
    def _evaluate_condition(
        condition: ApprovalCondition,
        value: Any,
    ) -> bool:
        """Evaluate a single condition."""
        if condition.operator == "eq":
            return value == condition.value
        if condition.operator == "ne":
            return value != condition.value
        if condition.operator == "gt":
            return value is not None and value > condition.value
        if condition.operator == "lt":
            return value is not None and value < condition.value
        if condition.operator == "contains":
            return condition.value in str(value) if value else False
        if condition.operator == "matches":
            return bool(re.match(condition.value, str(value))) if value else False
        if condition.operator == "exists":
            return value is not None
        return False


class RateLimitPolicy(BaseModel):
    """Policy for rate limiting."""

    name: str
    description: str = ""
    max_calls: int  # Maximum calls allowed
    window_seconds: int  # Time window
    capabilities: list[str] = Field(default_factory=list)  # Glob patterns
    agents: list[str] = Field(default_factory=list)
    enabled: bool = True

    def matches_capability(self, capability_name: str) -> bool:
        """Check if policy matches a capability."""
        if not self.capabilities:
            return True
        return any(
            ApprovalPolicy._matches_glob(p, capability_name)
            for p in self.capabilities
        )


class RedactionPattern(BaseModel):
    """Pattern for data redaction."""

    name: str
    pattern: str  # Regex pattern to match
    mode: RedactionMode = RedactionMode.MASK
    replacement: str = "***"  # Custom replacement for MASK mode


class RedactionPolicy(BaseModel):
    """Policy for data redaction."""

    name: str
    description: str = ""
    patterns: list[RedactionPattern] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)  # Fields to redact
    enabled: bool = True


class ScopeRestriction(BaseModel):
    """Scope restriction policy."""

    name: str
    description: str = ""
    allowed_projects: list[str] = Field(default_factory=list)
    allowed_folders: list[str] = Field(default_factory=list)
    denied_capabilities: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    enabled: bool = True


class PolicyConfig(BaseModel):
    """Complete policy configuration."""

    version: str = "1.0"
    approval_policies: list[ApprovalPolicy] = Field(default_factory=list)
    rate_limit_policies: list[RateLimitPolicy] = Field(default_factory=list)
    redaction_policies: list[RedactionPolicy] = Field(default_factory=list)
    scope_restrictions: list[ScopeRestriction] = Field(default_factory=list)


class ExternalBrokerConfig(BaseModel):
    """Broker configuration for external agent integrations."""

    enabled: bool = True
    endpoint: str | None = None


class ExternalAdapterConfig(BaseModel):
    """Allowlist configuration for a single external adapter."""

    enabled: bool = False
    approval_required: bool = True
    allowed_domains: list[str] = Field(default_factory=list)


class ExternalAdapterAllowlist(BaseModel):
    """Allowlist configuration for external adapters."""

    version: str = "1.0"
    broker: ExternalBrokerConfig = Field(default_factory=ExternalBrokerConfig)
    adapters: dict[str, ExternalAdapterConfig] = Field(default_factory=dict)

    def get_adapter(self, name: str) -> ExternalAdapterConfig | None:
        """Get adapter config by name."""
        return self.adapters.get(name)



def load_external_adapter_allowlist(
    path: Path | str | None,
) -> ExternalAdapterAllowlist | None:
    """Load external adapter allowlist from YAML."""
    if not path:
        return None
    path = Path(path)
    if not path.exists():
        return None

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ExternalAdapterAllowlist.model_validate(data)


@dataclass
class RateLimitState:
    """State for rate limiting."""

    call_times: list[datetime] = field(default_factory=list)

    def record_call(self) -> None:
        """Record a call."""
        self.call_times.append(utc_now())

    def count_in_window(self, window_seconds: int) -> int:
        """Count calls within window."""
        cutoff = utc_now() - timedelta(seconds=window_seconds)
        # Clean old entries
        self.call_times = [t for t in self.call_times if t > cutoff]
        return len(self.call_times)


class PolicyManager:
    """Manages and applies policies."""

    def __init__(self) -> None:
        """Initialize policy manager."""
        self._config = PolicyConfig()
        self._rate_limit_state: dict[str, RateLimitState] = {}

    def load_from_yaml(self, path: Path | str) -> None:
        """Load policy configuration from YAML file."""
        path = Path(path)
        if not path.exists():
            logger.warning("policy_file_not_found", path=str(path))
            return

        with open(path) as f:
            data = yaml.safe_load(f)

        self._config = PolicyConfig.model_validate(data)
        logger.info(
            "policies_loaded",
            path=str(path),
            approval_count=len(self._config.approval_policies),
            rate_limit_count=len(self._config.rate_limit_policies),
            redaction_count=len(self._config.redaction_policies),
        )

    def load_from_directory(self, directory: Path | str) -> None:
        """Load all policy files from a directory."""
        directory = Path(directory)
        if not directory.exists():
            return

        for path in directory.glob("*.yaml"):
            self.load_from_yaml(path)

        for path in directory.glob("*.yml"):
            self.load_from_yaml(path)

    def set_config(self, config: PolicyConfig) -> None:
        """Set policy configuration directly."""
        self._config = config

    def requires_approval(
        self,
        capability_name: str,
        agent_profile_id: str,
        args: dict[str, Any],
        capability_default: bool = False,
    ) -> bool:
        """Check if an action requires approval.

        Args:
            capability_name: The capability being called.
            agent_profile_id: The agent making the call.
            args: Action arguments.
            capability_default: Default from capability definition.

        Returns:
            True if approval is required.
        """
        for policy in self._config.approval_policies:
            if not policy.enabled:
                continue

            if not policy.matches_capability(capability_name):
                continue

            if not policy.matches_agent(agent_profile_id):
                continue

            if policy.mode == ApprovalMode.ALWAYS:
                return True
            if policy.mode == ApprovalMode.NEVER:
                return False
            if policy.mode == ApprovalMode.CONDITIONAL:
                if policy.evaluate_conditions(args):
                    return True
            elif policy.mode == ApprovalMode.DEFAULT:
                return capability_default

        return capability_default

    def check_rate_limit(
        self,
        capability_name: str,
        agent_profile_id: str,
    ) -> tuple[bool, str | None]:
        """Check if rate limit allows a call.

        Args:
            capability_name: The capability being called.
            agent_profile_id: The agent making the call.

        Returns:
            Tuple of (allowed, error_message).
        """
        for policy in self._config.rate_limit_policies:
            if not policy.enabled:
                continue

            if not policy.matches_capability(capability_name):
                continue

            # Create key for this combination
            key = f"{policy.name}:{agent_profile_id}:{capability_name}"
            state = self._rate_limit_state.setdefault(key, RateLimitState())

            current_count = state.count_in_window(policy.window_seconds)
            if current_count >= policy.max_calls:
                msg = (
                    f"Rate limit exceeded: {policy.max_calls} calls "
                    f"per {policy.window_seconds}s"
                )
                return False, msg

            state.record_call()

        return True, None

    def redact_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Apply redaction policies to data.

        Args:
            data: Data to redact.

        Returns:
            Redacted copy of data.
        """
        result = dict(data)

        for policy in self._config.redaction_policies:
            if not policy.enabled:
                continue

            # Redact specific fields
            for field_name in policy.fields:
                if field_name in result:
                    result[field_name] = self._redact_value(
                        result[field_name],
                        RedactionMode.MASK,
                    )

            # Apply pattern-based redaction
            for pattern in policy.patterns:
                result = self._apply_pattern_redaction(result, pattern)

        return result

    def check_scope(
        self,
        capability_name: str,
        agent_profile_id: str,
        project_id: str | None = None,
        folder: str | None = None,
    ) -> tuple[bool, str | None]:
        """Check if operation is within allowed scope.

        Args:
            capability_name: The capability being called.
            agent_profile_id: The agent.
            project_id: Optional project ID.
            folder: Optional folder path.

        Returns:
            Tuple of (allowed, error_message).
        """
        for policy in self._config.scope_restrictions:
            if not policy.enabled:
                continue

            if policy.agents and agent_profile_id not in policy.agents:
                continue

            # Check denied capabilities
            for pattern in policy.denied_capabilities:
                if ApprovalPolicy._matches_glob(pattern, capability_name):
                    return False, f"Capability denied by policy: {policy.name}"

            # Check project restrictions
            if policy.allowed_projects and project_id:
                if project_id not in policy.allowed_projects:
                    return False, f"Project not allowed: {project_id}"

            # Check folder restrictions
            if policy.allowed_folders and folder:
                allowed = any(
                    folder.startswith(f) for f in policy.allowed_folders
                )
                if not allowed:
                    return False, f"Folder not allowed: {folder}"

        return True, None

    def _redact_value(self, value: Any, mode: RedactionMode) -> Any:
        """Redact a single value."""
        if value is None:
            return None

        str_value = str(value)

        if mode == RedactionMode.MASK:
            if len(str_value) <= 4:
                return "***"
            return str_value[:2] + "*" * (len(str_value) - 4) + str_value[-2:]
        if mode == RedactionMode.HASH:
            import hashlib
            return f"[HASH:{hashlib.sha256(str_value.encode()).hexdigest()[:8]}]"
        if mode == RedactionMode.REMOVE:
            return "[REDACTED]"
        if mode == RedactionMode.TRUNCATE:
            if len(str_value) <= 8:
                return str_value
            return f"{str_value[:4]}...{str_value[-4:]}"

        return value

    def _apply_pattern_redaction(
        self,
        data: dict[str, Any],
        pattern: RedactionPattern,
    ) -> dict[str, Any]:
        """Apply pattern-based redaction to data."""
        import json

        # Convert to string for pattern matching
        data_str = json.dumps(data)

        def replacer(match: re.Match) -> str:
            matched = match.group(0)
            return self._redact_value(matched, pattern.mode)

        redacted_str = re.sub(pattern.pattern, replacer, data_str)

        try:
            return json.loads(redacted_str)
        except json.JSONDecodeError:
            return data


# Default patterns for sensitive data
DEFAULT_REDACTION_PATTERNS = [
    RedactionPattern(
        name="api_keys",
        pattern=r"sk-[a-zA-Z0-9]{20,}",
        mode=RedactionMode.MASK,
    ),
    RedactionPattern(
        name="bearer_tokens",
        pattern=r"Bearer\s+[a-zA-Z0-9\._-]+",
        mode=RedactionMode.MASK,
    ),
    RedactionPattern(
        name="passwords",
        pattern=r'"password"\s*:\s*"[^"]*"',
        mode=RedactionMode.REMOVE,
    ),
    RedactionPattern(
        name="secrets",
        pattern=r'"secret"\s*:\s*"[^"]*"',
        mode=RedactionMode.REMOVE,
    ),
]
