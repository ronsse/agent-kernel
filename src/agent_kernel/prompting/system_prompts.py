"""Helpers for handling system prompt refs in context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from agent_kernel.core.schemas.context import ContextItem, ContextRef

SYSTEM_PROMPT_KIND = "system_prompt"

LAYER_ORDER = {
    "base": 0,
    "vault": 1,
    "project": 2,
    "workflow": 3,
    "agent": 4,
}


def is_system_prompt_ref(ref: ContextRef) -> bool:
    """Return True when a ContextRef is a system prompt reference."""
    if not isinstance(ref.metadata, dict):
        return False
    return ref.metadata.get("kind") == SYSTEM_PROMPT_KIND


@dataclass(frozen=True, order=True)
class PromptRefSortKey:
    """Sort key for system prompt refs."""

    layer_rank: int
    priority: int
    ref_id: str


def prompt_ref_sort_key(ref: ContextRef) -> PromptRefSortKey:
    """Create a stable sort key for system prompt refs."""
    meta = ref.metadata if isinstance(ref.metadata, dict) else {}
    layer = str(meta.get("layer") or "base")
    priority_raw = meta.get("priority", 50)
    try:
        priority = int(priority_raw)
    except (TypeError, ValueError):
        priority = 50
    return PromptRefSortKey(
        layer_rank=LAYER_ORDER.get(layer, 99),
        priority=int(priority),
        ref_id=str(ref.ref_id),
    )


def sort_prompt_refs(refs: Iterable[ContextRef]) -> list[ContextRef]:
    """Sort prompt refs by layer, then priority, then ref_id."""
    return sorted(refs, key=prompt_ref_sort_key)


def split_context_items(
    items: Iterable[ContextItem],
) -> tuple[list[ContextItem], list[ContextItem]]:
    """Split items into system prompt items and evidence items."""
    prompt_items: list[ContextItem] = []
    evidence_items: list[ContextItem] = []
    for item in items:
        if is_system_prompt_ref(item.ref):
            prompt_items.append(item)
        else:
            evidence_items.append(item)
    return prompt_items, evidence_items
