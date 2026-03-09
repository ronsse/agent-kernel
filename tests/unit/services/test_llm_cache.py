"""Tests for LLM semantic cache."""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest
from agent_kernel.services.llm_cache import LLMSemanticCache


@dataclass
class FakeLLMResponse:
    content: str
    model: str
    input_tokens: int = 100
    output_tokens: int = 50
    total_tokens: int = 150
    finish_reason: str = "stop"


@pytest.fixture
def cache(tmp_path):
    db = tmp_path / "cache.db"
    return LLMSemanticCache(db, default_ttl_seconds=3600)


class TestPromptHash:
    def test_deterministic(self, cache: LLMSemanticCache):
        h1 = cache.compute_prompt_hash("sys", "user")
        h2 = cache.compute_prompt_hash("sys", "user")
        assert h1 == h2

    def test_different_prompts(self, cache: LLMSemanticCache):
        h1 = cache.compute_prompt_hash("sys", "user1")
        h2 = cache.compute_prompt_hash("sys", "user2")
        assert h1 != h2

    def test_whitespace_normalized(self, cache: LLMSemanticCache):
        h1 = cache.compute_prompt_hash("sys ", " user")
        h2 = cache.compute_prompt_hash("sys", "user")
        assert h1 == h2


class TestStoreAndLookup:
    def test_basic_round_trip(self, cache: LLMSemanticCache):
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="hello", model="gpt-4o")
        cache.store(ph, "gpt-4o", tier=1, reasoning_effort="medium", response=resp)

        entry = cache.lookup(ph, "gpt-4o", requested_tier=1, requested_effort="medium")
        assert entry is not None
        assert entry.response_content == "hello"
        assert entry.model == "gpt-4o"
        assert entry.input_tokens == 100

    def test_model_mismatch_returns_none(self, cache: LLMSemanticCache):
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="hello", model="gpt-4o")
        cache.store(ph, "gpt-4o", tier=1, reasoning_effort="medium", response=resp)

        entry = cache.lookup(
            ph, "gpt-4o-mini",
            requested_tier=1, requested_effort="medium",
        )
        assert entry is None

    def test_prompt_hash_mismatch(self, cache: LLMSemanticCache):
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="hello", model="gpt-4o")
        cache.store(ph, "gpt-4o", tier=1, reasoning_effort="medium", response=resp)

        other_ph = cache.compute_prompt_hash("sys", "other")
        entry = cache.lookup(
            other_ph, "gpt-4o",
            requested_tier=1, requested_effort="medium",
        )
        assert entry is None


class TestTierInvariant:
    """Core tier-aware cache invariant:
    - Lower cached effort MUST NOT satisfy higher request
    - Higher cached effort CAN satisfy lower request
    """

    def test_lower_cached_rejected(self, cache: LLMSemanticCache):
        """Lower cached effort must not satisfy higher request."""
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="medium-result", model="gpt-4o")
        cache.store(ph, "gpt-4o", tier=1, reasoning_effort="medium", response=resp)

        entry = cache.lookup(ph, "gpt-4o", requested_tier=1, requested_effort="high")
        assert entry is None, "Lower cached effort must not match"

    def test_higher_cached_accepted(self, cache: LLMSemanticCache):
        """Higher cached effort can satisfy lower request."""
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="high-result", model="gpt-4o")
        cache.store(ph, "gpt-4o", tier=2, reasoning_effort="high", response=resp)

        entry = cache.lookup(ph, "gpt-4o", requested_tier=1, requested_effort="medium")
        assert entry is not None
        assert entry.response_content == "high-result"

    def test_same_effort_accepted(self, cache: LLMSemanticCache):
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="result", model="gpt-4o")
        cache.store(ph, "gpt-4o", tier=1, reasoning_effort="medium", response=resp)

        entry = cache.lookup(ph, "gpt-4o", requested_tier=1, requested_effort="medium")
        assert entry is not None

    def test_none_effort_rejected_for_low(self, cache: LLMSemanticCache):
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="result", model="gpt-4o")
        cache.store(ph, "gpt-4o", tier=0, reasoning_effort="none", response=resp)

        entry = cache.lookup(ph, "gpt-4o", requested_tier=1, requested_effort="low")
        assert entry is None


class TestTTL:
    def test_expired_entry_not_returned(self, tmp_path):
        cache = LLMSemanticCache(tmp_path / "cache.db", default_ttl_seconds=1)
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="old", model="gpt-4o")
        cache.store(ph, "gpt-4o", tier=1, reasoning_effort="medium", response=resp)

        # Wait for expiration
        time.sleep(1.5)

        entry = cache.lookup(ph, "gpt-4o", requested_tier=1, requested_effort="medium")
        assert entry is None

    def test_custom_ttl_per_entry(self, tmp_path):
        cache = LLMSemanticCache(tmp_path / "cache.db", default_ttl_seconds=3600)
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="short-lived", model="gpt-4o")
        cache.store(
            ph, "gpt-4o", tier=1,
            reasoning_effort="medium", response=resp,
            ttl_seconds=1,
        )

        time.sleep(1.5)
        entry = cache.lookup(ph, "gpt-4o", requested_tier=1, requested_effort="medium")
        assert entry is None


class TestInvalidateAndCleanup:
    def test_invalidate_by_hash_and_model(self, cache: LLMSemanticCache):
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="result", model="gpt-4o")
        cache.store(ph, "gpt-4o", tier=1, reasoning_effort="medium", response=resp)

        deleted = cache.invalidate(ph, "gpt-4o")
        assert deleted == 1
        assert cache.lookup(ph, "gpt-4o", 1, "medium") is None

    def test_invalidate_by_hash_only(self, cache: LLMSemanticCache):
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="result", model="gpt-4o")
        cache.store(ph, "gpt-4o", tier=1, reasoning_effort="medium", response=resp)
        cache.store(ph, "gpt-4o", tier=2, reasoning_effort="high", response=resp)

        deleted = cache.invalidate(ph)
        assert deleted == 2

    def test_cleanup_expired(self, tmp_path):
        cache = LLMSemanticCache(tmp_path / "cache.db", default_ttl_seconds=1)
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="old", model="gpt-4o")
        cache.store(ph, "gpt-4o", tier=1, reasoning_effort="medium", response=resp)

        time.sleep(1.5)
        deleted = cache.cleanup_expired()
        assert deleted == 1
        assert cache.stats()["total_entries"] == 0


class TestStats:
    def test_stats_after_operations(self, cache: LLMSemanticCache):
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="result", model="gpt-4o")
        cache.store(ph, "gpt-4o", tier=1, reasoning_effort="medium", response=resp)

        # Miss
        cache.lookup(cache.compute_prompt_hash("sys", "other"), "gpt-4o", 1, "medium")
        # Hit
        cache.lookup(ph, "gpt-4o", 1, "medium")

        stats = cache.stats()
        assert stats["enabled"] is True
        assert stats["total_entries"] == 1
        assert stats["session_hits"] == 1
        assert stats["session_misses"] == 1


class TestDisabled:
    def test_disabled_cache_returns_none(self, tmp_path):
        cache = LLMSemanticCache(tmp_path / "cache.db", enabled=False)
        ph = cache.compute_prompt_hash("sys", "user")
        resp = FakeLLMResponse(content="result", model="gpt-4o")
        cache.store(ph, "gpt-4o", tier=1, reasoning_effort="medium", response=resp)
        assert cache.lookup(ph, "gpt-4o", 1, "medium") is None
        assert cache.stats()["enabled"] is False
