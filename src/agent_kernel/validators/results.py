"""Shared validation result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class CheckStatus(StrEnum):
    """Status for a single validation check."""

    PASS = "pass"  # noqa: S105
    WARN = "warn"
    ERROR = "error"
    SKIP = "skip"


@dataclass
class ValidationCheck:
    """Result of a single validation check."""

    name: str
    status: CheckStatus
    message: str
    detail: str | None = None


@dataclass
class ValidationResult:
    """Aggregate result for a validation target."""

    target: str
    checks: list[ValidationCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True if no checks have ERROR status."""
        return self.error_count == 0

    @property
    def error_count(self) -> int:
        """Number of checks with ERROR status."""
        return sum(1 for c in self.checks if c.status == CheckStatus.ERROR)

    @property
    def warn_count(self) -> int:
        """Number of checks with WARN status."""
        return sum(1 for c in self.checks if c.status == CheckStatus.WARN)
