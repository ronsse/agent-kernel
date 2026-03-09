"""Base schema utilities and common types."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_kernel.core.ids import generate_ulid

# Schema version for v1.0.2 design patch (context retrieval)
SCHEMA_VERSION = "1.0.2"


@lru_cache(maxsize=1)
def get_kernel_version() -> str:
    """Get kernel version from git or package metadata.

    Returns git SHA if available, otherwise returns "dev".
    Result is cached for efficiency.
    """
    try:
        # Try to get git commit SHA
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    # Fallback to package version or dev
    try:
        from importlib.metadata import version

        return version("agent-kernel")
    except Exception:
        return "dev"


def utc_now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware and in UTC.

    Args:
        dt: A datetime object.

    Returns:
        Timezone-aware datetime in UTC.

    Raises:
        ValueError: If datetime is naive (no timezone).
    """
    if dt.tzinfo is None:
        msg = "Naive datetime not allowed. Use timezone-aware datetime."
        raise ValueError(msg)
    # Convert to UTC if not already
    return dt.astimezone(UTC)


class KernelModel(BaseModel):
    """Base model for all kernel schemas.

    Features:
    - Strict validation (extra fields forbidden)
    - Frozen for immutability where appropriate
    - JSON-compatible serialization
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
        populate_by_name=True,
    )


class VersionedModel(KernelModel):
    """Base model for persisted records with version tracking.

    All persisted schemas (traces, plans, context packets, etc.) should
    inherit from this to ensure schema evolution is trackable.

    Features:
    - schema_version: Version of the schema used to create this record
    - kernel_version: Version of the kernel that created this record
    """

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Schema version used when this record was created",
    )
    kernel_version: str = Field(
        default_factory=get_kernel_version,
        description="Kernel version (git SHA or semver) that created this record",
    )

    @field_validator("schema_version", mode="before")
    @classmethod
    def _default_schema_version(cls, v: Any) -> str:
        """Default schema version if not provided (for backwards compat)."""
        if v is None or v == "":
            return SCHEMA_VERSION
        return v

    @field_validator("kernel_version", mode="before")
    @classmethod
    def _default_kernel_version(cls, v: Any) -> str:
        """Default kernel version if not provided (for backwards compat)."""
        if v is None or v == "":
            return get_kernel_version()
        return v


class TimestampedModel(KernelModel):
    """Base model with automatic timestamps."""

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class IdentifiedModel(KernelModel):
    """Base model with automatic ULID generation."""

    @classmethod
    def generate_id(cls, prefix: str = "") -> str:
        """Generate a new ID with optional prefix."""
        base_id = generate_ulid()
        return f"{prefix}_{base_id}" if prefix else base_id


def to_json_dict(model: BaseModel) -> dict[str, Any]:
    """Convert model to JSON-compatible dict."""
    return model.model_dump(mode="json")
