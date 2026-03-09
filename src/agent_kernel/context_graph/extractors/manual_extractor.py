"""Manual knowledge entry utilities.

Helpers for creating knowledge nodes from user-provided data.
"""

from __future__ import annotations

from typing import Any

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.knowledge import (
    ConceptProperties,
    DataObjectProperties,
    DomainProperties,
    FreshnessScore,
    KnowledgeSource,
    KnowledgeTier,
    SystemProperties,
)


def build_domain_properties(
    title: str,
    description: str | None = None,
    domain_scope: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build properties dict for a DOMAIN node."""
    now = utc_now()
    props = DomainProperties(
        title=title,
        description=description,
        knowledge_source=KnowledgeSource.MANUAL,
        confidence=1.0,
        freshness=FreshnessScore(
            last_accessed_at=now,
            last_reinforced_at=now,
        ),
        tier=KnowledgeTier.HOT,
        tags=tags or [],
        domain_scope=domain_scope,
    )
    return props.model_dump(mode="json")


def build_system_properties(
    title: str,
    description: str | None = None,
    system_type: str | None = None,
    url: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build properties dict for a SYSTEM node."""
    now = utc_now()
    props = SystemProperties(
        title=title,
        description=description,
        knowledge_source=KnowledgeSource.MANUAL,
        confidence=1.0,
        freshness=FreshnessScore(
            last_accessed_at=now,
            last_reinforced_at=now,
        ),
        tier=KnowledgeTier.HOT,
        tags=tags or [],
        system_type=system_type,
        url=url,
    )
    return props.model_dump(mode="json")


def build_concept_properties(
    title: str,
    description: str | None = None,
    aliases: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build properties dict for a CONCEPT node."""
    now = utc_now()
    props = ConceptProperties(
        title=title,
        description=description,
        knowledge_source=KnowledgeSource.MANUAL,
        confidence=1.0,
        freshness=FreshnessScore(
            last_accessed_at=now,
            last_reinforced_at=now,
        ),
        tier=KnowledgeTier.HOT,
        tags=tags or [],
        aliases=aliases or [],
    )
    return props.model_dump(mode="json")


def build_data_object_properties(
    title: str,
    description: str | None = None,
    object_type: str | None = None,
    system_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build properties dict for a DATA_OBJECT node."""
    now = utc_now()
    props = DataObjectProperties(
        title=title,
        description=description,
        knowledge_source=KnowledgeSource.MANUAL,
        confidence=1.0,
        freshness=FreshnessScore(
            last_accessed_at=now,
            last_reinforced_at=now,
        ),
        tier=KnowledgeTier.HOT,
        tags=tags or [],
        object_type=object_type,
        system_id=system_id,
    )
    return props.model_dump(mode="json")
