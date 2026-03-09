"""Tests for prompt registry and system prompt refs."""

from __future__ import annotations

from agent_kernel.core.schemas import ContextItem, ContextRef, RefType
from agent_kernel.prompting import PromptRegistry, split_context_items


def _make_prompt_item(ref_id: str, uri: str, layer: str) -> ContextItem:
    ref = ContextRef(
        ref_type=RefType.SPEC,
        ref_id=ref_id,
        uri=uri,
        metadata={"kind": "system_prompt", "layer": layer},
    )
    return ContextItem(
        ref=ref,
        excerpt="",
        summary=None,
        relevance_score=1.0,
        included_reason="test",
    )


def test_prompt_registry_compose_orders_by_layer(tmp_path):
    (tmp_path / "system").mkdir()
    (tmp_path / "workflows").mkdir()
    (tmp_path / "system" / "base_system.md").write_text("BASE", encoding="utf-8")
    (tmp_path / "workflows" / "daily_checkin.system.md").write_text(
        "WORKFLOW", encoding="utf-8"
    )

    base_item = _make_prompt_item(
        "prompt_base_system",
        "prompts:///system/base_system.md",
        "base",
    )
    workflow_item = _make_prompt_item(
        "prompt_workflow_daily_checkin",
        "prompts:///workflows/daily_checkin.system.md",
        "workflow",
    )

    registry = PromptRegistry(base_dir=tmp_path)
    bundle = registry.compose_from_items([workflow_item, base_item])

    assert bundle.text == "BASE\n\nWORKFLOW"
    assert bundle.hash is not None
    assert len(bundle.parts) == 2
    assert bundle.parts[0].prompt_id == "prompt_base_system"


def test_split_context_items_separates_prompts():
    prompt_item = _make_prompt_item(
        "prompt_base_system",
        "prompts:///system/base_system.md",
        "base",
    )
    note_item = ContextItem(
        ref=ContextRef(ref_type=RefType.NOTE, ref_id="note_1", metadata={"title": "Note"}),
        excerpt="Note excerpt",
        summary=None,
        relevance_score=0.5,
        included_reason="test",
    )

    prompt_items, evidence_items = split_context_items([prompt_item, note_item])

    assert len(prompt_items) == 1
    assert len(evidence_items) == 1
    assert evidence_items[0].ref.ref_id == "note_1"
