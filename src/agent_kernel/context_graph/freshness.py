"""Freshness scoring utilities for knowledge nodes."""

from __future__ import annotations

from datetime import datetime

import structlog

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.knowledge import FreshnessScore, KnowledgeTier

logger = structlog.get_logger(__name__)


class FreshnessCalculator:
    """Utilities for calculating and updating freshness scores."""

    @staticmethod
    def effective_relevance(
        freshness: FreshnessScore,
        now: datetime | None = None,
    ) -> float:
        """Calculate effective relevance with time decay."""
        return freshness.effective_relevance(now)

    @staticmethod
    def record_access(freshness: FreshnessScore) -> FreshnessScore:
        """Return a new FreshnessScore with updated access tracking."""
        now = utc_now()
        return FreshnessScore(
            base_relevance=freshness.base_relevance,
            last_accessed_at=now,
            last_reinforced_at=freshness.last_reinforced_at,
            access_count=freshness.access_count + 1,
            decay_rate=freshness.decay_rate,
            pinned=freshness.pinned,
        )

    @staticmethod
    def record_reinforcement(freshness: FreshnessScore) -> FreshnessScore:
        """Return a new FreshnessScore with reinforcement timestamp updated."""
        now = utc_now()
        return FreshnessScore(
            base_relevance=freshness.base_relevance,
            last_accessed_at=freshness.last_accessed_at,
            last_reinforced_at=now,
            access_count=freshness.access_count,
            decay_rate=freshness.decay_rate,
            pinned=freshness.pinned,
        )

    @staticmethod
    def determine_tier(
        freshness: FreshnessScore,
        hot_days: int = 90,
        warm_days: int = 365,
        now: datetime | None = None,
    ) -> KnowledgeTier:
        """Determine the retention tier based on freshness.

        Args:
            freshness: The freshness score to evaluate.
            hot_days: Days since last access to stay HOT.
            warm_days: Days since last access to stay WARM (before COLD).
            now: Current time (defaults to utc_now()).

        Returns:
            The appropriate KnowledgeTier.
        """
        if freshness.pinned:
            return KnowledgeTier.HOT

        if now is None:
            now = utc_now()

        last_touch = max(freshness.last_accessed_at, freshness.last_reinforced_at)
        days_elapsed = (now - last_touch).total_seconds() / 86400.0

        if days_elapsed <= hot_days:
            return KnowledgeTier.HOT
        if days_elapsed <= hot_days + warm_days:
            return KnowledgeTier.WARM
        return KnowledgeTier.COLD
