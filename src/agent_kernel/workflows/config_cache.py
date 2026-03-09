"""Configuration caching utilities for workflow runner.

Provides efficient caching of configuration files with:
- File modification time tracking for invalidation
- TTL-based cache expiration
- Thread-safe access

Usage:
    cache = ConfigCache()

    # Cache YAML config with mtime invalidation
    config = cache.get_or_load(
        "calendar_sources",
        config_path,
        loader_fn=load_calendar_sources,
    )

    # Invalidate manually if needed
    cache.invalidate("calendar_sources")
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, TypeVar

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T")


@dataclass
class CacheEntry:
    """Single cache entry with metadata."""

    key: str
    value: Any
    created_at: float
    file_mtime: float | None = None
    ttl_seconds: float | None = None

    def is_expired(self, ttl: float | None = None) -> bool:
        """Check if entry is expired based on TTL."""
        effective_ttl = ttl or self.ttl_seconds
        if effective_ttl is None:
            return False
        return (time.time() - self.created_at) > effective_ttl

    def is_stale(self, current_mtime: float | None) -> bool:
        """Check if entry is stale based on file modification time."""
        if self.file_mtime is None or current_mtime is None:
            return False
        return current_mtime > self.file_mtime


class ConfigCache:
    """Thread-safe configuration cache with file-based invalidation.

    Supports:
    - Automatic invalidation when source file changes (mtime tracking)
    - TTL-based expiration
    - Manual invalidation
    """

    def __init__(self, default_ttl: float | None = None) -> None:
        """Initialize cache.

        Args:
            default_ttl: Default TTL in seconds (None = no TTL).
        """
        self._cache: dict[str, CacheEntry] = {}
        self._lock = Lock()
        self._default_ttl = default_ttl

    def get(self, key: str) -> Any | None:
        """Get value from cache (returns None if missing or expired)."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.is_expired(self._default_ttl):
                del self._cache[key]
                return None
            return entry.value

    def set(
        self,
        key: str,
        value: Any,
        file_path: Path | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        """Set a value in the cache.

        Args:
            key: Cache key.
            value: Value to cache.
            file_path: Optional file path for mtime tracking.
            ttl_seconds: Optional TTL override.
        """
        file_mtime = None
        if file_path and file_path.exists():
            file_mtime = file_path.stat().st_mtime

        entry = CacheEntry(
            key=key,
            value=value,
            created_at=time.time(),
            file_mtime=file_mtime,
            ttl_seconds=ttl_seconds or self._default_ttl,
        )

        with self._lock:
            self._cache[key] = entry

        logger.debug(
            "config_cached",
            key=key,
            has_mtime=file_mtime is not None,
            ttl=entry.ttl_seconds,
        )

    def get_or_load(
        self,
        key: str,
        file_path: Path | None,
        loader_fn: Callable[[], T],
        ttl_seconds: float | None = None,
    ) -> T:
        """Get from cache or load using loader function.

        Automatically invalidates if file has been modified.

        Args:
            key: Cache key.
            file_path: Optional file path for mtime checking.
            loader_fn: Function to call if cache miss.
            ttl_seconds: Optional TTL override.

        Returns:
            Cached or freshly loaded value.
        """
        with self._lock:
            entry = self._cache.get(key)

            # Check if valid cached entry exists
            if entry is not None:
                # Check TTL expiration
                if entry.is_expired(ttl_seconds or self._default_ttl):
                    logger.debug("config_cache_expired", key=key)
                    del self._cache[key]
                    entry = None

                # Check file modification
                elif file_path and file_path.exists():
                    current_mtime = file_path.stat().st_mtime
                    if entry.is_stale(current_mtime):
                        logger.debug(
                            "config_cache_stale",
                            key=key,
                            cached_mtime=entry.file_mtime,
                            current_mtime=current_mtime,
                        )
                        del self._cache[key]
                        entry = None

            # Return cached value if valid
            if entry is not None:
                logger.debug("config_cache_hit", key=key)
                return entry.value

        # Load fresh value (outside lock to avoid blocking)
        logger.debug("config_cache_miss", key=key)
        value = loader_fn()

        # Cache the result
        self.set(key, value, file_path, ttl_seconds)
        return value

    def invalidate(self, key: str) -> bool:
        """Invalidate a cache entry.

        Args:
            key: Cache key to invalidate.

        Returns:
            True if entry was found and removed.
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                logger.debug("config_cache_invalidated", key=key)
                return True
            return False

    def invalidate_all(self) -> int:
        """Invalidate all cache entries.

        Returns:
            Number of entries invalidated.
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.debug("config_cache_cleared", count=count)
            return count

    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            return {
                "size": len(self._cache),
                "keys": list(self._cache.keys()),
                "entries": [
                    {
                        "key": entry.key,
                        "age_seconds": time.time() - entry.created_at,
                        "has_mtime": entry.file_mtime is not None,
                        "ttl": entry.ttl_seconds,
                    }
                    for entry in self._cache.values()
                ],
            }


class StoreCache:
    """Cache for database store instances.

    Avoids creating new store connections on every call.
    Stores are kept open and reused until explicitly closed.
    """

    def __init__(self) -> None:
        """Initialize store cache."""
        self._stores: dict[str, Any] = {}
        self._lock = Lock()

    def get_or_create(
        self,
        key: str,
        factory_fn: Callable[[], T],
    ) -> T:
        """Get existing store or create new one.

        Args:
            key: Store identifier.
            factory_fn: Factory function to create store if not cached.

        Returns:
            Cached or newly created store instance.
        """
        with self._lock:
            if key not in self._stores:
                logger.debug("store_cache_creating", key=key)
                self._stores[key] = factory_fn()
            else:
                logger.debug("store_cache_hit", key=key)
            return self._stores[key]

    def close(self, key: str) -> bool:
        """Close and remove a store from cache.

        Args:
            key: Store identifier.

        Returns:
            True if store was found and closed.
        """
        with self._lock:
            store = self._stores.pop(key, None)
            if store is not None:
                if hasattr(store, "close"):
                    store.close()
                logger.debug("store_cache_closed", key=key)
                return True
            return False

    def close_all(self) -> int:
        """Close all cached stores.

        Returns:
            Number of stores closed.
        """
        with self._lock:
            count = 0
            for key, store in list(self._stores.items()):
                if hasattr(store, "close"):
                    store.close()
                count += 1
            self._stores.clear()
            logger.debug("store_cache_closed_all", count=count)
            return count

    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            return {
                "size": len(self._stores),
                "keys": list(self._stores.keys()),
            }


# Decorator for caching function results with mtime invalidation
def cached_config(
    cache: ConfigCache,
    key: str,
    file_path_fn: Callable[..., Path] | None = None,
    ttl_seconds: float | None = None,
):
    """Decorator for caching configuration loading functions.

    Args:
        cache: ConfigCache instance.
        key: Cache key.
        file_path_fn: Optional function to get file path from args.
        ttl_seconds: Optional TTL.

    Example:
        @cached_config(cache, "calendar_sources", lambda self: self._config_path)
        def load_calendar_sources(self) -> dict:
            ...
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            file_path = None
            if file_path_fn:
                file_path = file_path_fn(*args, **kwargs)

            return cache.get_or_load(
                key=key,
                file_path=file_path,
                loader_fn=lambda: fn(*args, **kwargs),
                ttl_seconds=ttl_seconds,
            )
        return wrapper
    return decorator
