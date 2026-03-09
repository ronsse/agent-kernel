"""Prompt serialization layer (TOON toggle).

This module is framework-agnostic and engine-agnostic. Engines can call a
serializer to render ContextPacket into a compact string for prompts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from agent_kernel.core.schemas.agent import AgentProfile
from agent_kernel.core.schemas.context import ContextItem, ContextPacket, RefType

PromptFormat = Literal["markdown", "json", "toon", "mixed"]


class PromptSerializer(Protocol):
    """Serializer interface for rendering prompts."""

    format_id: PromptFormat

    def render(self, context_packet: ContextPacket, agent_profile: AgentProfile) -> str:
        """Render ContextPacket for LLM prompts."""


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _summary_from_item(item: ContextItem, max_chars: int = 240) -> str:
    if item.summary:
        return _truncate(item.summary, max_chars)
    return _truncate(item.excerpt, max_chars)


def _group_name(ref_type: RefType) -> str:
    if ref_type == RefType.TASK:
        return "tasks"
    if ref_type == RefType.EVENT:
        return "events"
    if ref_type == RefType.EMAIL:
        return "emails"
    if ref_type == RefType.SKILL:
        return "skills"
    if ref_type in {RefType.NOTE, RefType.DOCUMENT}:
        return "notes"
    if ref_type in {RefType.RULE, RefType.SPEC}:
        return "specs"
    return "other"


def _note_record(item: ContextItem) -> dict[str, Any]:
    meta = item.ref.metadata
    return {
        "id": item.ref.ref_id,
        "title": _safe_text(meta.get("title") or meta.get("name")),
        "summary": _summary_from_item(item),
        "score": item.relevance_score,
        "uri": _safe_text(item.ref.uri),
    }


def _task_record(item: ContextItem) -> dict[str, Any]:
    meta = item.ref.metadata
    return {
        "id": item.ref.ref_id,
        "title": _safe_text(meta.get("title") or meta.get("name")),
        "status": _safe_text(meta.get("status")),
        "due": _safe_text(meta.get("due")),
        "priority": _safe_text(meta.get("priority")),
        "project": _safe_text(meta.get("project")),
        "score": item.relevance_score,
        "uri": _safe_text(item.ref.uri),
    }


def _event_record(item: ContextItem) -> dict[str, Any]:
    meta = item.ref.metadata
    return {
        "id": item.ref.ref_id,
        "title": _safe_text(meta.get("title") or meta.get("name")),
        "start": _safe_text(meta.get("start")),
        "end": _safe_text(meta.get("end")),
        "location": _safe_text(meta.get("location")),
        "score": item.relevance_score,
        "uri": _safe_text(item.ref.uri),
    }


def _email_record(item: ContextItem) -> dict[str, Any]:
    meta = item.ref.metadata
    return {
        "id": item.ref.ref_id,
        "from": _safe_text(meta.get("from")),
        "subject": _safe_text(meta.get("subject")),
        "date": _safe_text(meta.get("date")),
        "score": item.relevance_score,
        "uri": _safe_text(item.ref.uri),
    }


def _spec_record(item: ContextItem) -> dict[str, Any]:
    meta = item.ref.metadata
    return {
        "id": item.ref.ref_id,
        "title": _safe_text(meta.get("title") or meta.get("name")),
        "uri": _safe_text(item.ref.uri),
    }


def _skill_record(item: ContextItem) -> dict[str, Any]:
    meta = item.ref.metadata
    return {
        "id": item.ref.ref_id,
        "name": _safe_text(meta.get("name") or meta.get("title")),
        "summary": _summary_from_item(item),
        "allowed_tools": meta.get("allowed_tools", []),
        "uri": _safe_text(item.ref.uri),
    }


def _other_record(item: ContextItem) -> dict[str, Any]:
    meta = item.ref.metadata
    return {
        "id": item.ref.ref_id,
        "type": item.ref.ref_type.value,
        "title": _safe_text(meta.get("title") or meta.get("name")),
        "summary": _summary_from_item(item),
        "score": item.relevance_score,
        "uri": _safe_text(item.ref.uri),
    }


def _to_structured_payload(packet: ContextPacket) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {
        "tasks": [],
        "events": [],
        "emails": [],
        "notes": [],
        "skills": [],
        "specs": [],
        "other": [],
    }

    for item in packet.items:
        group = _group_name(item.ref.ref_type)
        if group == "tasks":
            grouped[group].append(_task_record(item))
        elif group == "events":
            grouped[group].append(_event_record(item))
        elif group == "emails":
            grouped[group].append(_email_record(item))
        elif group == "notes":
            grouped[group].append(_note_record(item))
        elif group == "specs":
            grouped[group].append(_spec_record(item))
        elif group == "skills":
            grouped[group].append(_skill_record(item))
        else:
            grouped[group].append(_other_record(item))

    return {
        "intent": packet.intent,
        "context_packs": packet.context_packs,
        "items": grouped,
    }


def _try_toon_encode(payload: dict[str, Any]) -> str | None:
    """Try to encode with an optional TOON library.

    Returns None if the encoder is not available or fails.
    """

    try:
        from toon_format import encode as toon_encode  # type: ignore
    except Exception:
        return None

    try:
        return toon_encode(payload)
    except Exception:
        return None


def _encode_payload(payload: dict[str, Any]) -> str:
    """Encode payload in TOON if available, otherwise compact JSON."""

    toon_text = _try_toon_encode(payload)
    if toon_text is not None:
        return toon_text

    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True)
class MarkdownSerializer:
    format_id: PromptFormat = "markdown"
    max_excerpt_chars: int = 240

    def render(self, context_packet: ContextPacket, agent_profile: AgentProfile) -> str:
        lines: list[str] = [f"INTENT: {context_packet.intent}", ""]
        if not context_packet.items:
            lines.append("No context items available.")
            return "\n".join(lines)

        for item in context_packet.items:
            ref = item.ref
            title = _safe_text(ref.metadata.get("title") or ref.metadata.get("name"))
            header = f"- [{ref.ref_type.value}] {title} ({ref.ref_id})"
            lines.append(header)
            excerpt = _truncate(item.excerpt, self.max_excerpt_chars)
            lines.append(f"  excerpt: {excerpt}")
        return "\n".join(lines)


@dataclass(frozen=True)
class JsonSerializer:
    format_id: PromptFormat = "json"

    def render(self, context_packet: ContextPacket, agent_profile: AgentProfile) -> str:
        payload = _to_structured_payload(context_packet)
        return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)


@dataclass(frozen=True)
class ToonSerializer:
    """Serialize structured arrays into TOON when available."""

    format_id: PromptFormat = "toon"

    def render(self, context_packet: ContextPacket, agent_profile: AgentProfile) -> str:
        payload = _to_structured_payload(context_packet)
        return _encode_payload(payload)


@dataclass(frozen=True)
class MixedSerializer:
    """TOON for structured data + Markdown excerpts."""

    format_id: PromptFormat = "mixed"
    toon: PromptSerializer = ToonSerializer()
    md: PromptSerializer = MarkdownSerializer()

    def render(self, context_packet: ContextPacket, agent_profile: AgentProfile) -> str:
        toon_part = self.toon.render(context_packet, agent_profile)
        md_part = self.md.render(context_packet, agent_profile)
        return "\n\n".join(
            [
                "==STRUCTURED_DATA==",
                toon_part,
                "==EXCERPTS==",
                md_part,
            ]
        )


def get_prompt_serializer(format_id: PromptFormat) -> PromptSerializer:
    """Factory for prompt serializers."""

    if format_id == "markdown":
        return MarkdownSerializer()
    if format_id == "json":
        return JsonSerializer()
    if format_id == "toon":
        return ToonSerializer()
    if format_id == "mixed":
        return MixedSerializer()
    return MarkdownSerializer()
