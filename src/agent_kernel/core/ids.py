"""ID generation utilities using ULID."""

import ulid


def generate_ulid() -> str:
    """Generate a new ULID string.

    ULIDs are:
    - Universally unique
    - Lexicographically sortable
    - Encoded as 26-character string
    - Time-ordered (first 10 chars are timestamp)

    Returns:
        A new ULID as a string.
    """
    return str(ulid.new())


def generate_prefixed_id(prefix: str) -> str:
    """Generate an ID with a prefix.

    Args:
        prefix: Prefix to prepend (e.g., 'trace', 'action', 'plan')

    Returns:
        Prefixed ID like 'trace_01HXYZ...'
    """
    return f"{prefix}_{ulid.new()}"


def ulid_to_timestamp(ulid_str: str) -> float:
    """Extract timestamp from a ULID string.

    Args:
        ulid_str: ULID string (with or without prefix)

    Returns:
        Unix timestamp as float
    """
    # Handle prefixed IDs
    if "_" in ulid_str:
        ulid_str = ulid_str.split("_", 1)[1]

    return ulid.parse(ulid_str).timestamp().timestamp
