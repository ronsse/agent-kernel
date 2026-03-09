"""Tests for configuration caching utilities."""

import time
from pathlib import Path

import pytest

from agent_kernel.workflows.config_cache import (
    CacheEntry,
    ConfigCache,
    StoreCache,
)


class TestCacheEntry:
    """Tests for CacheEntry dataclass."""

    def test_is_expired_no_ttl(self):
        """Entry without TTL never expires."""
        entry = CacheEntry(
            key="test",
            value="value",
            created_at=time.time() - 3600,  # 1 hour ago
            ttl_seconds=None,
        )
        assert not entry.is_expired()

    def test_is_expired_within_ttl(self):
        """Entry within TTL is not expired."""
        entry = CacheEntry(
            key="test",
            value="value",
            created_at=time.time() - 10,  # 10 seconds ago
            ttl_seconds=60,
        )
        assert not entry.is_expired()

    def test_is_expired_past_ttl(self):
        """Entry past TTL is expired."""
        entry = CacheEntry(
            key="test",
            value="value",
            created_at=time.time() - 120,  # 2 minutes ago
            ttl_seconds=60,
        )
        assert entry.is_expired()

    def test_is_stale_no_mtime(self):
        """Entry without mtime is never stale."""
        entry = CacheEntry(
            key="test",
            value="value",
            created_at=time.time(),
            file_mtime=None,
        )
        assert not entry.is_stale(time.time())

    def test_is_stale_file_modified(self):
        """Entry is stale if file has newer mtime."""
        cached_mtime = time.time() - 60
        entry = CacheEntry(
            key="test",
            value="value",
            created_at=time.time() - 30,
            file_mtime=cached_mtime,
        )
        # File was modified after caching
        current_mtime = time.time()
        assert entry.is_stale(current_mtime)

    def test_is_stale_file_not_modified(self):
        """Entry is not stale if file mtime unchanged."""
        cached_mtime = time.time()
        entry = CacheEntry(
            key="test",
            value="value",
            created_at=time.time(),
            file_mtime=cached_mtime,
        )
        assert not entry.is_stale(cached_mtime)


class TestConfigCache:
    """Tests for ConfigCache."""

    def test_get_set_basic(self):
        """Test basic get/set operations."""
        cache = ConfigCache()

        cache.set("key1", {"data": "value1"})
        result = cache.get("key1")

        assert result == {"data": "value1"}

    def test_get_missing_key(self):
        """Get returns None for missing key."""
        cache = ConfigCache()
        assert cache.get("nonexistent") is None

    def test_ttl_expiration(self):
        """Test TTL-based expiration."""
        cache = ConfigCache(default_ttl=0.1)  # 100ms TTL

        cache.set("key", "value")
        assert cache.get("key") == "value"

        # Wait for expiration
        time.sleep(0.15)
        assert cache.get("key") is None

    def test_get_or_load_cache_hit(self):
        """Test get_or_load returns cached value."""
        cache = ConfigCache()
        load_count = 0

        def loader():
            nonlocal load_count
            load_count += 1
            return {"loaded": True}

        # First call loads
        result1 = cache.get_or_load("key", None, loader)
        assert result1 == {"loaded": True}
        assert load_count == 1

        # Second call uses cache
        result2 = cache.get_or_load("key", None, loader)
        assert result2 == {"loaded": True}
        assert load_count == 1  # Not called again

    def test_get_or_load_file_invalidation(self, temp_dir):
        """Test get_or_load invalidates on file change."""
        cache = ConfigCache()
        config_file = temp_dir / "config.yaml"
        load_count = 0

        def loader():
            nonlocal load_count
            load_count += 1
            return {"version": load_count}

        # Create initial file
        config_file.write_text("version: 1")

        # First load
        result1 = cache.get_or_load("config", config_file, loader)
        assert result1 == {"version": 1}
        assert load_count == 1

        # Cache hit (file unchanged)
        result2 = cache.get_or_load("config", config_file, loader)
        assert result2 == {"version": 1}
        assert load_count == 1

        # Modify file (update mtime)
        time.sleep(0.01)  # Ensure mtime changes
        config_file.write_text("version: 2")

        # Should reload due to mtime change
        result3 = cache.get_or_load("config", config_file, loader)
        assert result3 == {"version": 2}
        assert load_count == 2

    def test_invalidate(self):
        """Test manual invalidation."""
        cache = ConfigCache()

        cache.set("key", "value")
        assert cache.get("key") == "value"

        result = cache.invalidate("key")
        assert result is True
        assert cache.get("key") is None

    def test_invalidate_nonexistent(self):
        """Invalidate returns False for missing key."""
        cache = ConfigCache()
        assert cache.invalidate("nonexistent") is False

    def test_invalidate_all(self):
        """Test clearing all entries."""
        cache = ConfigCache()

        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")

        count = cache.invalidate_all()
        assert count == 3
        assert cache.get("key1") is None
        assert cache.get("key2") is None
        assert cache.get("key3") is None

    def test_stats(self):
        """Test cache statistics."""
        cache = ConfigCache()

        cache.set("key1", "value1", ttl_seconds=60)
        cache.set("key2", "value2", ttl_seconds=120)

        stats = cache.stats()
        assert stats["size"] == 2
        assert set(stats["keys"]) == {"key1", "key2"}
        assert len(stats["entries"]) == 2


class TestStoreCache:
    """Tests for StoreCache."""

    def test_get_or_create_basic(self):
        """Test basic store creation and caching."""
        cache = StoreCache()
        create_count = 0

        class MockStore:
            def __init__(self):
                nonlocal create_count
                create_count += 1
                self.id = create_count

            def close(self):
                pass

        # First call creates
        store1 = cache.get_or_create("db", MockStore)
        assert store1.id == 1
        assert create_count == 1

        # Second call returns cached
        store2 = cache.get_or_create("db", MockStore)
        assert store2.id == 1
        assert create_count == 1
        assert store1 is store2

    def test_close_single(self):
        """Test closing a single store."""
        cache = StoreCache()
        closed = []

        class MockStore:
            def close(self):
                closed.append(True)

        cache.get_or_create("db", MockStore)
        result = cache.close("db")

        assert result is True
        assert len(closed) == 1

    def test_close_nonexistent(self):
        """Close returns False for missing store."""
        cache = StoreCache()
        assert cache.close("nonexistent") is False

    def test_close_all(self):
        """Test closing all stores."""
        cache = StoreCache()
        closed = []

        class MockStore:
            def __init__(self, name):
                self.name = name

            def close(self):
                closed.append(self.name)

        cache.get_or_create("db1", lambda: MockStore("db1"))
        cache.get_or_create("db2", lambda: MockStore("db2"))
        cache.get_or_create("db3", lambda: MockStore("db3"))

        count = cache.close_all()
        assert count == 3
        assert set(closed) == {"db1", "db2", "db3"}

    def test_stats(self):
        """Test store cache statistics."""
        cache = StoreCache()

        class MockStore:
            pass

        cache.get_or_create("db1", MockStore)
        cache.get_or_create("db2", MockStore)

        stats = cache.stats()
        assert stats["size"] == 2
        assert set(stats["keys"]) == {"db1", "db2"}


class TestCacheThreadSafety:
    """Tests for thread safety of caches."""

    def test_config_cache_concurrent_access(self):
        """Test ConfigCache handles concurrent access."""
        import threading

        cache = ConfigCache()
        results = []
        errors = []

        def worker(worker_id):
            try:
                for i in range(100):
                    key = f"key_{worker_id}_{i}"
                    cache.set(key, {"worker": worker_id, "i": i})
                    value = cache.get(key)
                    if value is not None:
                        results.append(value)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) > 0

    def test_store_cache_concurrent_access(self):
        """Test StoreCache handles concurrent access."""
        import threading

        cache = StoreCache()
        create_count = [0]
        lock = threading.Lock()

        class MockStore:
            def __init__(self):
                with lock:
                    create_count[0] += 1

            def close(self):
                pass

        results = []
        errors = []

        def worker(worker_id):
            try:
                for _ in range(50):
                    store = cache.get_or_create("shared_store", MockStore)
                    results.append(store)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # Store should only be created once
        assert create_count[0] == 1
        # All results should be the same instance
        assert all(r is results[0] for r in results)
