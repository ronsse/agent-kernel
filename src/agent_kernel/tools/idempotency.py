"""SQLite-backed idempotency key store with TTL.

Prevents duplicate tool executions by tracking idempotency keys
with automatic expiration.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class IdempotencyResult:
    """Result of an idempotency check.

    Attributes:
        is_duplicate: Whether the key has been recorded (and not expired).
        original_tool_call_id: The tool_call_id from the original execution.
        original_executed_at: When the original execution occurred.
    """

    is_duplicate: bool
    original_tool_call_id: str | None = None
    original_executed_at: datetime | None = None


class IdempotencyStore:
    """SQLite-backed idempotency key store with TTL.

    Tracks idempotency keys to prevent duplicate tool executions.
    Keys expire automatically after a configurable TTL.

    Usage:
        store = IdempotencyStore("data/idempotency.db")
        result = store.check("my-unique-key")
        if not result.is_duplicate:
            # Execute the operation
            store.record("my-unique-key", tool_call_id, capability_name)
    """

    def __init__(
        self,
        db_path: str | Path,
        default_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        """Initialize the idempotency store.

        Args:
            db_path: Path to the SQLite database file.
            default_ttl: Default time-to-live for idempotency keys.
        """
        self._db_path = Path(db_path)
        self._default_ttl = default_ttl

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

        logger.info(
            "idempotency_store_initialized",
            db_path=str(self._db_path),
            default_ttl_seconds=int(default_ttl.total_seconds()),
        )

    def _init_schema(self) -> None:
        """Create the idempotency keys table if it doesn't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                idempotency_key TEXT PRIMARY KEY,
                tool_call_id TEXT NOT NULL,
                capability_name TEXT NOT NULL,
                executed_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_idem_expires
                ON idempotency_keys(expires_at);
        """)
        self._conn.commit()

    def check(self, idempotency_key: str) -> IdempotencyResult:
        """Check whether an idempotency key has already been recorded.

        Only returns a duplicate if the key exists and has not expired.

        Args:
            idempotency_key: The key to check.

        Returns:
            IdempotencyResult indicating whether this is a duplicate.
        """
        now_iso = datetime.now(UTC).isoformat()

        cursor = self._conn.execute(
            """
            SELECT tool_call_id, executed_at
            FROM idempotency_keys
            WHERE idempotency_key = ?
              AND expires_at > ?
            """,
            (idempotency_key, now_iso),
        )
        row = cursor.fetchone()

        if row is None:
            return IdempotencyResult(is_duplicate=False)

        return IdempotencyResult(
            is_duplicate=True,
            original_tool_call_id=row["tool_call_id"],
            original_executed_at=datetime.fromisoformat(row["executed_at"]),
        )

    def record(
        self,
        idempotency_key: str,
        tool_call_id: str,
        capability_name: str,
        ttl: timedelta | None = None,
    ) -> None:
        """Record an idempotency key after successful execution.

        Uses INSERT OR REPLACE so that recording the same key twice
        overwrites the previous entry.

        Args:
            idempotency_key: The unique key for this operation.
            tool_call_id: The tool call ID of the execution.
            capability_name: The capability that was executed.
            ttl: Custom TTL for this key. Defaults to the store's default_ttl.
        """
        effective_ttl = ttl if ttl is not None else self._default_ttl
        now = datetime.now(UTC)
        executed_at = now.isoformat()
        expires_at = (now + effective_ttl).isoformat()

        self._conn.execute(
            """
            INSERT OR REPLACE INTO idempotency_keys
            (idempotency_key, tool_call_id, capability_name, executed_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (idempotency_key, tool_call_id, capability_name, executed_at, expires_at),
        )
        self._conn.commit()

    def cleanup_expired(self) -> int:
        """Delete expired idempotency keys.

        Returns:
            Number of expired keys deleted.
        """
        now_iso = datetime.now(UTC).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM idempotency_keys WHERE expires_at <= ?",
            (now_iso,),
        )
        self._conn.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info("idempotency_keys_cleaned", deleted=deleted)
        return deleted

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
