"""Tests for prompt serializers."""

from __future__ import annotations

import json

from agent_kernel.core.schemas import (
    ContextBudget,
    ContextItem,
    ContextPacket,
    ContextRef,
    RefType,
    RetrievalReport,
)
from agent_kernel.prompting.serializers import (
    JsonSerializer,
    MarkdownSerializer,
    MixedSerializer,
    ToonSerializer,
    get_prompt_serializer,
)


def _make_item(
    ref_type: RefType,
    ref_id: str,
    title: str,
    *,
    excerpt: str = "Sample excerpt",
    metadata: dict | None = None,
) -> ContextItem:
    meta = {"title": title}
    if metadata:
        meta.update(metadata)
    return ContextItem(
        ref=ContextRef(ref_type=ref_type, ref_id=ref_id, metadata=meta),
        excerpt=excerpt,
        summary=None,
        relevance_score=0.5,
        included_reason="test_fixture",
    )


def _make_packet(items: list[ContextItem]) -> ContextPacket:
    return ContextPacket(
        intent="Plan my day",
        budget=ContextBudget(max_tokens=4000, max_items=20),
        items=items,
        retrieval_report=RetrievalReport(items_considered=5, items_selected=len(items)),
    )


def test_toon_serializer_fallback_json(sample_agent_profile):
    items = [
        _make_item(RefType.NOTE, "note_1", "Test Note"),
        _make_item(
            RefType.TASK,
            "task_1",
            "Test Task",
            metadata={"status": "open", "priority": "high"},
        ),
        _make_item(
            RefType.EVENT,
            "event_1",
            "Test Event",
            metadata={"start": "2025-01-01T10:00:00Z", "end": "2025-01-01T11:00:00Z"},
        ),
    ]
    packet = _make_packet(items)

    serializer = ToonSerializer()
    rendered = serializer.render(packet, sample_agent_profile)
    payload = json.loads(rendered)

    assert payload["intent"] == packet.intent
    assert "items" in payload
    assert len(payload["items"]["notes"]) == 1
    assert len(payload["items"]["tasks"]) == 1
    assert len(payload["items"]["events"]) == 1


def test_mixed_serializer_sections(sample_agent_profile):
    items = [
        _make_item(RefType.NOTE, "note_2", "Second Note", excerpt="Note excerpt"),
    ]
    packet = _make_packet(items)

    serializer = MixedSerializer()
    rendered = serializer.render(packet, sample_agent_profile)

    assert "==STRUCTURED_DATA==" in rendered
    assert "==EXCERPTS==" in rendered
    assert "INTENT: Plan my day" in rendered


def test_json_serializer_output(sample_agent_profile):
    items = [_make_item(RefType.NOTE, "note_3", "Third Note")]
    packet = _make_packet(items)

    serializer = JsonSerializer()
    rendered = serializer.render(packet, sample_agent_profile)
    payload = json.loads(rendered)

    assert payload["intent"] == packet.intent
    assert payload["items"]["notes"][0]["id"] == "note_3"


def test_get_prompt_serializer():
    assert isinstance(get_prompt_serializer("markdown"), MarkdownSerializer)
    assert isinstance(get_prompt_serializer("json"), JsonSerializer)
    assert isinstance(get_prompt_serializer("toon"), ToonSerializer)
    assert isinstance(get_prompt_serializer("mixed"), MixedSerializer)
