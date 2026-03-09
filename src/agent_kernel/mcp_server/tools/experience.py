"""Experience tools (Layer 4) - ExperienceStore operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from mcp.server import FastMCP

    from agent_kernel.mcp_server.server import StoreBundle

logger = structlog.get_logger(__name__)


def register_experience_tools(mcp: FastMCP, stores: StoreBundle) -> None:
    """Register experience memory tools with the MCP server."""

    @mcp.tool(
        name="experience_cases",
        description=(
            "Search past experience cases — records of what the agent did "
            "in previous runs, what tools it used, and what outcomes resulted. "
            "Useful for learning from past successes and failures."
        ),
    )
    def experience_cases(
        workflow_id: str | None = None,
        label: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search experience cases.

        Args:
            workflow_id: Filter by workflow ID.
            label: Filter by outcome label (success, partial, failure, regression).
            limit: Maximum results.
        """
        exp = stores.experience_store
        if not exp:
            return {"cases": [], "error": "Experience store not available"}

        from agent_kernel.core.schemas.experience import OutcomeLabel

        outcome_label = None
        if label:
            try:
                outcome_label = OutcomeLabel(label)
            except ValueError:
                return {
                    "cases": [],
                    "error": (
                        f"Invalid label '{label}'. "
                        "Use: success, partial, failure, regression"
                    ),
                }

        cases = exp.find_similar_cases(
            workflow_id=workflow_id,
            label=outcome_label,
            limit=limit,
        )

        return {
            "cases": [
                {
                    "case_id": c.case_id,
                    "trace_id": c.trace_id,
                    "intent": c.intent,
                    "workflow_id": c.workflow_id,
                    "label": (
                        c.label.value
                        if hasattr(c.label, "value")
                        else str(c.label)
                    ),
                    "capability_names": c.capability_names,
                    "context_summary": c.context_summary,
                    "plan_summary": c.plan_summary,
                    "outcome_summary": c.outcome_summary,
                }
                for c in cases
            ],
        }

    @mcp.tool(
        name="experience_lessons",
        description=(
            "List lessons learned from past agent runs. Lessons are actionable "
            "guidance mined from experience cases, scoped to specific workflows "
            "or capabilities."
        ),
    )
    def experience_lessons(
        workflow_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List lessons learned.

        Args:
            workflow_id: Filter by workflow scope.
            status: Filter by status (active, deprecated, candidate).
            limit: Maximum results.
        """
        exp = stores.experience_store
        if not exp:
            return {"lessons": [], "error": "Experience store not available"}

        from agent_kernel.core.schemas.experience import LessonScope

        scope = LessonScope(workflow_id=workflow_id) if workflow_id else None

        lessons = exp.list_lessons(
            scope=scope,
            status=status,
            limit=limit,
        )

        return {
            "lessons": [
                {
                    "lesson_id": le.lesson_id,
                    "title": le.title,
                    "lesson_text": le.lesson_text,
                    "scope": {
                        "workflow_id": le.scope.workflow_id,
                        "capability_name": le.scope.capability_name,
                        "entity_type": le.scope.entity_type,
                        "project_id": le.scope.project_id,
                    } if le.scope else {},
                    "confidence": le.confidence,
                    "status": le.status,
                    "source_case_ids": le.source_case_ids,
                }
                for le in lessons
            ],
        }

    @mcp.tool(
        name="experience_playbooks",
        description=(
            "List playbooks — versioned behavioral patterns that codify "
            "best practices for specific workflows. Playbooks include "
            "checklists, pitfalls, and recommended thinking tiers."
        ),
    )
    def experience_playbooks(
        workflow_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List playbooks.

        Args:
            workflow_id: Filter by workflow scope.
            limit: Maximum results.
        """
        exp = stores.experience_store
        if not exp:
            return {"playbooks": [], "error": "Experience store not available"}

        if workflow_id:
            playbooks = exp.find_playbooks(workflow_id=workflow_id)
        else:
            playbooks = exp.list_playbooks(status="active", limit=limit)

        return {
            "playbooks": [
                {
                    "playbook_id": pb.playbook_id,
                    "name": pb.name,
                    "checklist": pb.checklist,
                    "pitfalls": pb.pitfalls,
                    "recommended_thinking_tier": pb.recommended_thinking_tier,
                    "status": pb.status,
                    "required_entity_types": pb.required_entity_types,
                    "required_sources": pb.required_sources,
                }
                for pb in playbooks
            ],
        }
