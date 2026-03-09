"""Import utilities for optional dependency management.

Provides helpers to check for optional dependencies and raise
helpful error messages when they are missing.
"""

from __future__ import annotations


def require_extra(package: str, extra: str, feature: str = "") -> None:
    """Check that an optional dependency is installed.

    Raises ImportError with a helpful pip install command if the
    package is not available.

    Args:
        package: The Python package name to check (e.g., "fastapi").
        extra: The pip extras name (e.g., "api").
        feature: Optional human-readable feature description.

    Raises:
        ImportError: If the package is not installed.
    """
    try:
        __import__(package)
    except ImportError:
        feature_msg = f" for {feature}" if feature else ""
        msg = (
            f"'{package}' is required{feature_msg} but not installed. "
            f"Install it with:\n\n"
            f"    pip install agentkernel[{extra}]\n"
        )
        raise ImportError(msg) from None
