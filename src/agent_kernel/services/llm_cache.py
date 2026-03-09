"""Semantic cache for LLM responses.

SQLite-backed, tier-aware LLM response cache. A cached response from
a lower tier / lower effort must NOT satisfy a higher-tier request,
but a higher-tier response CAN satisfy a lower-tier request.

Key invariant:
    cached_effort_rank >= requested_effort_rank
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

EFFORT_RANK: dict[str, int] = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}


@dataclass
class CacheEntry:
    """A cached LLM response."""

    prompt_hash: str
    model: str
    tier: int
    reasoning_effort: str
    response_content: str
    response_model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = "stop"
    created_at: str = ""
    ttl_seconds: int = 86400
    hit_count: int = 0
    last_hit_at: str | None = None


class LLMSemanticCache:
    """SQLite-backed tier-aware LLM response cache.

    The cache key is ``(prompt_hash, model)``. Lookups additionally
    filter by ``reasoning_effort_rank >= requested_rank`` so that
    a cheaper cached result never satisfies a more expensive request.
    """

    def __init__(
        self,
        db_path: str | Path,
        default_ttl_seconds: int = 86400,
        enabled: bool = True,
    ) -> None:
        self._db_path = Path(db_path)
        self._default_ttl = default_ttl_seconds
        self._enabled = enabled
        self._hit_count = 0
        self._miss_count = 0

        if self._enabled:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self._db_path), check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._init_schema()
            logger.info(
                "llm_cache_initialized",
                db_path=str(self._db_path),
                default_ttl=default_ttl_seconds,
            )
        else:
            self._conn = None  # type: ignore[assignment]

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS llm_cache (
                prompt_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                tier INTEGER NOT NULL,
                reasoning_effort TEXT NOT NULL,
                reasoning_effort_rank INTEGER NOT NULL,
                response_content TEXT NOT NULL,
                response_model TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                finish_reason TEXT DEFAULT 'stop',
                created_at TEXT NOT NULL,
                ttl_seconds INTEGER NOT NULL,
                hit_count INTEGER DEFAULT 0,
                last_hit_at TEXT,
                PRIMARY KEY (prompt_hash, model, tier, reasoning_effort)
            );

            CREATE INDEX IF NOT EXISTS idx_cache_lookup
                ON llm_cache(prompt_hash, model, reasoning_effort_rank);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def compute_prompt_hash(system_prompt: str, user_prompt: str) -> str:
        """SHA-256 of normalised prompts."""
        normalised = (system_prompt.strip() + "\n---\n" + user_prompt.strip())
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()

    def lookup(
        self,
        prompt_hash: str,
        model: str,
        requested_tier: int,  # noqa: ARG002
        requested_effort: str,
    ) -> CacheEntry | None:
        """Look up a cached response.

        Returns the best-quality match where
        ``reasoning_effort_rank >= requested_rank`` and the entry
        has not expired.
        """
        if not self._enabled:
            return None

        requested_rank = EFFORT_RANK.get(requested_effort, 2)
        now_iso = datetime.now(UTC).isoformat()

        cursor = self._conn.execute(
            """
            SELECT * FROM llm_cache
            WHERE prompt_hash = ?
              AND model = ?
              AND reasoning_effort_rank >= ?
              AND datetime(created_at, '+' || ttl_seconds || ' seconds') > datetime(?)
            ORDER BY reasoning_effort_rank DESC, created_at DESC
            LIMIT 1
            """,
            (prompt_hash, model, requested_rank, now_iso),
        )
        row = cursor.fetchone()
        if row is None:
            self._miss_count += 1
            return None

        # Update hit counter
        self._conn.execute(
            """
            UPDATE llm_cache
            SET hit_count = hit_count + 1, last_hit_at = ?
            WHERE prompt_hash = ? AND model = ? AND tier = ? AND reasoning_effort = ?
            """,
            (
                now_iso, row["prompt_hash"], row["model"],
                row["tier"], row["reasoning_effort"],
            ),
        )
        self._conn.commit()

        self._hit_count += 1
        return self._row_to_entry(row)

    def store(
        self,
        prompt_hash: str,
        model: str,
        tier: int,
        reasoning_effort: str,
        response: Any,
        ttl_seconds: int | None = None,
    ) -> None:
        """Store an LLM response in the cache."""
        if not self._enabled:
            return

        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        rank = EFFORT_RANK.get(reasoning_effort, 2)
        now_iso = datetime.now(UTC).isoformat()

        # Extract fields from LLMResponse
        content = response.content if hasattr(response, "content") else str(response)
        resp_model = response.model if hasattr(response, "model") else model
        input_tokens = getattr(response, "input_tokens", 0)
        output_tokens = getattr(response, "output_tokens", 0)
        total_tokens = getattr(response, "total_tokens", 0)
        finish_reason = getattr(response, "finish_reason", "stop")

        self._conn.execute(
            """
            INSERT OR REPLACE INTO llm_cache
            (prompt_hash, model, tier, reasoning_effort, reasoning_effort_rank,
             response_content, response_model, input_tokens, output_tokens,
             total_tokens, finish_reason, created_at, ttl_seconds,
             hit_count, last_hit_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
            """,
            (
                prompt_hash, model, tier, reasoning_effort, rank,
                content, resp_model, input_tokens, output_tokens,
                total_tokens, finish_reason, now_iso, ttl,
            ),
        )
        self._conn.commit()

    def invalidate(self, prompt_hash: str, model: str | None = None) -> int:
        """Delete matching cache entries.

        Returns number of deleted entries.
        """
        if not self._enabled:
            return 0

        if model:
            cursor = self._conn.execute(
                "DELETE FROM llm_cache WHERE prompt_hash = ? AND model = ?",
                (prompt_hash, model),
            )
        else:
            cursor = self._conn.execute(
                "DELETE FROM llm_cache WHERE prompt_hash = ?",
                (prompt_hash,),
            )
        self._conn.commit()
        return cursor.rowcount

    def cleanup_expired(self) -> int:
        """Delete expired entries. Returns count deleted."""
        if not self._enabled:
            return 0

        now_iso = datetime.now(UTC).isoformat()
        cursor = self._conn.execute(
            """
            DELETE FROM llm_cache
            WHERE datetime(created_at, '+' || ttl_seconds || ' seconds') <= datetime(?)
            """,
            (now_iso,),
        )
        self._conn.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info("llm_cache_expired_cleanup", deleted=deleted)
        return deleted

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        if not self._enabled:
            return {"enabled": False, "total_entries": 0}

        cursor = self._conn.execute("SELECT COUNT(*) as cnt FROM llm_cache")
        total = cursor.fetchone()["cnt"]

        cursor = self._conn.execute(
            "SELECT SUM(hit_count) as total_hits FROM llm_cache"
        )
        row = cursor.fetchone()
        total_hits = row["total_hits"] or 0

        return {
            "enabled": True,
            "total_entries": total,
            "total_hits_stored": total_hits,
            "session_hits": self._hit_count,
            "session_misses": self._miss_count,
            "session_hit_rate": (
                self._hit_count / (self._hit_count + self._miss_count)
                if (self._hit_count + self._miss_count) > 0
                else 0.0
            ),
        }

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> CacheEntry:
        return CacheEntry(
            prompt_hash=row["prompt_hash"],
            model=row["model"],
            tier=row["tier"],
            reasoning_effort=row["reasoning_effort"],
            response_content=row["response_content"],
            response_model=row["response_model"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            total_tokens=row["total_tokens"],
            finish_reason=row["finish_reason"],
            created_at=row["created_at"],
            ttl_seconds=row["ttl_seconds"],
            hit_count=row["hit_count"],
            last_hit_at=row["last_hit_at"],
        )
