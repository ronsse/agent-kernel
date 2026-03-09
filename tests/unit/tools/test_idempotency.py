"""Tests for SQLite idempotency store."""

from datetime import timedelta

import pytest

from agent_kernel.tools.idempotency import IdempotencyResult, IdempotencyStore


class TestIdempotencyStore:
    """Tests for IdempotencyStore."""

    @pytest.fixture()
    def store(self, tmp_path):
        """Create a fresh IdempotencyStore for each test."""
        s = IdempotencyStore(
            db_path=tmp_path / "idempotency.db",
            default_ttl=timedelta(hours=1),
        )
        yield s
        s.close()

    def test_new_key_not_duplicate(self, store):
        """A never-seen key should not be a duplicate."""
        result = store.check("brand-new-key")

        assert result.is_duplicate is False
        assert result.original_tool_call_id is None
        assert result.original_executed_at is None

    def test_recorded_key_is_duplicate(self, store):
        """A recorded key should be detected as a duplicate."""
        store.record("my-key", "tool_call_001", "tasks.create@v1")

        result = store.check("my-key")

        assert result.is_duplicate is True
        assert result.original_tool_call_id == "tool_call_001"
        assert result.original_executed_at is not None

    def test_expired_key_not_duplicate(self, store):
        """An expired key should not be treated as a duplicate."""
        # Record with a TTL of 0 seconds (expires immediately)
        store.record(
            "ephemeral-key",
            "tool_call_002",
            "tasks.create@v1",
            ttl=timedelta(seconds=0),
        )

        result = store.check("ephemeral-key")

        assert result.is_duplicate is False

    def test_cleanup_removes_expired(self, store):
        """cleanup_expired should remove only expired entries."""
        # Record one expired entry and one valid entry
        store.record(
            "expired-key",
            "tool_call_003",
            "tasks.create@v1",
            ttl=timedelta(seconds=0),
        )
        store.record(
            "valid-key",
            "tool_call_004",
            "tasks.create@v1",
            ttl=timedelta(hours=1),
        )

        deleted = store.cleanup_expired()

        assert deleted == 1
        assert store.check("expired-key").is_duplicate is False
        assert store.check("valid-key").is_duplicate is True

    def test_different_keys_independent(self, store):
        """Different keys should not interfere with each other."""
        store.record("key-alpha", "tool_call_a", "cap.a@v1")
        store.record("key-beta", "tool_call_b", "cap.b@v1")

        result_alpha = store.check("key-alpha")
        result_beta = store.check("key-beta")
        result_gamma = store.check("key-gamma")

        assert result_alpha.is_duplicate is True
        assert result_alpha.original_tool_call_id == "tool_call_a"
        assert result_beta.is_duplicate is True
        assert result_beta.original_tool_call_id == "tool_call_b"
        assert result_gamma.is_duplicate is False

    def test_record_overwrites(self, store):
        """Recording the same key twice should overwrite the original."""
        store.record("overwrite-key", "tool_call_old", "cap.a@v1")
        store.record("overwrite-key", "tool_call_new", "cap.b@v1")

        result = store.check("overwrite-key")

        assert result.is_duplicate is True
        assert result.original_tool_call_id == "tool_call_new"
