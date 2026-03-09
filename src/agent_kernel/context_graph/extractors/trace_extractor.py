"""Deterministic trace extraction utilities.

Helpers for extracting structured data from DecisionTraces without LLM calls.
Used by the TraceDecomposer for deterministic decomposition.
"""

from __future__ import annotations

from typing import Any

from agent_kernel.core.schemas.trace import DecisionTrace, ToolCallRecord


def extract_capabilities(trace: DecisionTrace) -> list[str]:
    """Extract unique capability names from a trace's tool calls."""
    return list({
        tc.capability_name
        for tc in trace.tool_calls
        if tc.capability_name
    })


def extract_entity_refs(trace: DecisionTrace) -> list[dict[str, str]]:
    """Extract entity references from trace citations and artifacts.

    Returns list of dicts with ref_type, ref_id, and source.
    """
    refs: list[dict[str, str]] = []
    seen: set[str] = set()

    # From plan citations
    if trace.plan and trace.plan.citations:
        for citation in trace.plan.citations:
            key = f"{citation.ref_type.value}:{citation.ref_id}"
            if key not in seen:
                refs.append({
                    "ref_type": citation.ref_type.value,
                    "ref_id": citation.ref_id,
                    "source": "citation",
                })
                seen.add(key)

    # From outcome artifacts
    if trace.outcome and trace.outcome.artifacts:
        for artifact in trace.outcome.artifacts:
            key = f"{artifact.ref_type.value}:{artifact.ref_id}"
            if key not in seen:
                refs.append({
                    "ref_type": artifact.ref_type.value,
                    "ref_id": artifact.ref_id,
                    "source": "artifact",
                })
                seen.add(key)

    return refs


def summarize_tool_call(
    tool_call: ToolCallRecord,
    max_input_len: int = 200,
    max_output_len: int = 200,
) -> dict[str, Any]:
    """Create a concise summary of a tool call for graph properties."""
    input_summary = _truncate_dict_str(tool_call.input, max_input_len)
    output_summary = (
        _truncate_dict_str(tool_call.output, max_output_len)
        if tool_call.output
        else None
    )

    return {
        "capability_name": tool_call.capability_name,
        "status": tool_call.status.value,
        "duration_ms": tool_call.duration_ms,
        "input_summary": input_summary,
        "output_summary": output_summary,
    }


def _truncate_dict_str(data: dict[str, Any], max_len: int) -> str:
    """Convert dict to string with truncation."""
    parts = []
    for key, value in data.items():
        val_str = str(value)
        if len(val_str) > 50:
            val_str = val_str[:47] + "..."
        parts.append(f"{key}={val_str}")

    result = ", ".join(parts)
    if len(result) > max_len:
        result = result[: max_len - 3] + "..."
    return result
