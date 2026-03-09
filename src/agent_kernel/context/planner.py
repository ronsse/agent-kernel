"""Retrieval Planner for v1.0.2 flexible context retrieval.

The RetrievalPlanner creates retrieval plans from intent and scope.
Two implementations:
- BaselineRetrievalPlanner: Deterministic, no LLM
- InstructedRetrievalPlanner: LLM-powered, for complex constraints
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from agent_kernel.context.source_registry import SourceRegistry
from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas import ContextPolicy
from agent_kernel.core.schemas.context_pack import ContextPack
from agent_kernel.core.schemas.retrieval import (
    RetrievalDirective,
    RetrievalFilter,
    RetrievalPlan,
    RetrievalScope,
)

if TYPE_CHECKING:
    from agent_kernel.services.llm import LLMService

logger = structlog.get_logger(__name__)


class RetrievalPlanner(ABC):
    """Abstract base class for retrieval planners."""

    @abstractmethod
    async def plan(
        self,
        scope: RetrievalScope,
        packs: list[ContextPack],
        policy: ContextPolicy,
    ) -> RetrievalPlan:
        """Create a retrieval plan.

        Args:
            scope: The retrieval scope (intent, project, workflow, etc.)
            packs: Context packs to include.
            policy: Retrieval policy from agent profile.

        Returns:
            A validated RetrievalPlan.
        """


class BaselineRetrievalPlanner(RetrievalPlanner):
    """Deterministic retrieval planner - no LLM calls.

    Generates standard directives:
    - Semantic search on notes (if vector store available)
    - Recent notes within time window
    - Graph expansion from seed nodes
    - Open tasks (if task retrieval requested)
    - Upcoming events (if calendar retrieval requested)
    """

    def __init__(
        self,
        source_registry: SourceRegistry | None = None,
        default_recency_days: int = 14,
        default_calendar_days: int = 7,
    ) -> None:
        """Initialize the baseline planner.

        Args:
            source_registry: Registry for source validation.
            default_recency_days: Default time window for recent notes.
            default_calendar_days: Default time window for calendar events.
        """
        self._source_registry = source_registry
        self._default_recency_days = default_recency_days
        self._default_calendar_days = default_calendar_days
        logger.info("baseline_retrieval_planner_initialized")

    async def plan(
        self,
        scope: RetrievalScope,
        packs: list[ContextPack],
        policy: ContextPolicy,
    ) -> RetrievalPlan:
        """Create a baseline retrieval plan.

        This is deterministic - same inputs produce same plan.
        """
        directives: list[RetrievalDirective] = []
        assumptions: list[str] = []

        # Directive 1: Semantic search on notes
        if policy.max_notes > 0:
            directives.append(
                RetrievalDirective(
                    directive_id=generate_ulid(),
                    source_id="obsidian",
                    entity_type="note",
                    query=scope.intent,
                    filters=self._build_project_filters(scope),
                    top_k=policy.max_notes,
                    min_score=0.5,
                    reason="Semantic search for relevant notes",
                )
            )

        # Directive 2: Recent notes
        if policy.max_notes > 0:
            recency_days = scope.time_range_days or self._default_recency_days
            cutoff = datetime.now(UTC) - timedelta(days=recency_days)

            directives.append(
                RetrievalDirective(
                    directive_id=generate_ulid(),
                    source_id="obsidian",
                    entity_type="note",
                    filters=[
                        RetrievalFilter(
                            field="modified_at",
                            op="gt",
                            value=cutoff.isoformat(),
                        ),
                        *self._build_project_filters(scope),
                    ],
                    top_k=min(10, policy.max_notes),
                    recency_boost=True,
                    reason=f"Recent notes (last {recency_days} days)",
                )
            )
            assumptions.append(f"Using {recency_days}-day recency window for recent notes")

        # Directive 3: Open tasks
        if policy.max_tasks > 0:
            directives.append(
                RetrievalDirective(
                    directive_id=generate_ulid(),
                    source_id="tasks",
                    entity_type="task",
                    filters=[
                        RetrievalFilter(field="status", op="neq", value="done"),
                        RetrievalFilter(field="status", op="neq", value="canceled"),
                        *self._build_task_project_filters(scope),
                    ],
                    top_k=policy.max_tasks,
                    reason="Open tasks",
                )
            )

        # Directive 4: Tasks due soon
        if policy.max_tasks > 0:
            due_cutoff = datetime.now(UTC) + timedelta(days=7)
            directives.append(
                RetrievalDirective(
                    directive_id=generate_ulid(),
                    source_id="tasks",
                    entity_type="task",
                    filters=[
                        RetrievalFilter(field="due_date", op="lt", value=due_cutoff.isoformat()),
                        RetrievalFilter(field="status", op="neq", value="done"),
                    ],
                    top_k=min(10, policy.max_tasks),
                    reason="Tasks due within 7 days",
                )
            )

        # Directive 5: Upcoming calendar events
        if policy.max_events > 0:
            calendar_days = self._default_calendar_days
            time_min = datetime.now(UTC)
            time_max = time_min + timedelta(days=calendar_days)

            directives.append(
                RetrievalDirective(
                    directive_id=generate_ulid(),
                    source_id="calendar",
                    entity_type="calendar_event",
                    filters=[
                        RetrievalFilter(field="start", op="gte", value=time_min.isoformat()),
                        RetrievalFilter(field="start", op="lt", value=time_max.isoformat()),
                    ],
                    top_k=policy.max_events,
                    reason=f"Calendar events in next {calendar_days} days",
                )
            )
            assumptions.append(f"Looking ahead {calendar_days} days for calendar events")

        # Directive 6: Graph neighbor expansion (if project specified)
        if scope.project_id:
            directives.append(
                RetrievalDirective(
                    directive_id=generate_ulid(),
                    source_id="graph",
                    entity_type="graph_node",
                    filters=[
                        RetrievalFilter(
                            field="node_id",
                            op="prefix",
                            value=f"project:{scope.project_id}",
                        ),
                    ],
                    top_k=20,
                    reason=f"Graph neighbors of project {scope.project_id}",
                )
            )

        plan = RetrievalPlan(
            retrieval_plan_id=generate_ulid(),
            intent=scope.intent,
            mode="baseline",
            packs_used=[p.pack_id for p in packs],
            directives=directives,
            assumptions=assumptions,
        )

        logger.debug(
            "baseline_plan_created",
            plan_id=plan.retrieval_plan_id,
            directive_count=len(directives),
            pack_count=len(packs),
        )

        return plan

    def _build_project_filters(
        self,
        scope: RetrievalScope,
    ) -> list[RetrievalFilter]:
        """Build project scope filters for notes."""
        filters = []
        if scope.project_id:
            filters.append(
                RetrievalFilter(
                    field="frontmatter.project",
                    op="eq",
                    value=scope.project_id,
                )
            )
        if scope.path:
            filters.append(
                RetrievalFilter(
                    field="path",
                    op="prefix",
                    value=scope.path,
                )
            )
        return filters

    def _build_task_project_filters(
        self,
        scope: RetrievalScope,
    ) -> list[RetrievalFilter]:
        """Build project scope filters for tasks."""
        filters = []
        if scope.path:
            filters.append(
                RetrievalFilter(
                    field="source_path",
                    op="prefix",
                    value=scope.path,
                )
            )
        return filters


class InstructedRetrievalPlanner(RetrievalPlanner):
    """LLM-powered retrieval planner for complex constraints.

    Uses intent + context packs + source descriptors to generate
    structured RetrievalPlan validated against schemas.

    Only used when:
    - Baseline fails coverage gates
    - Intent has complex constraints (time ranges, exclusions, multi-source)
    - Agent profile specifies instructed mode
    """

    def __init__(
        self,
        llm_service: LLMService,
        source_registry: SourceRegistry,
        model: str | None = None,
    ) -> None:
        """Initialize the instructed planner.

        Args:
            llm_service: LLM service for plan generation.
            source_registry: Registry for source schema information.
            model: Optional specific model to use.
        """
        self._llm_service = llm_service
        self._source_registry = source_registry
        self._model = model
        logger.info("instructed_retrieval_planner_initialized")

    async def plan(
        self,
        scope: RetrievalScope,
        packs: list[ContextPack],
        policy: ContextPolicy,
    ) -> RetrievalPlan:
        """Create an LLM-instructed retrieval plan.

        The LLM receives:
        - User intent
        - Context pack specs (system rules)
        - Source descriptors (available fields/operators)
        - Policy constraints

        It outputs a structured RetrievalPlan that is validated
        against SourceRegistry before execution.
        """
        # Build prompt with source schemas
        source_schemas = self._build_source_schema_prompt()
        pack_specs = self._build_pack_specs_prompt(packs)

        prompt = f"""You are a retrieval planning assistant. Given a user intent and available sources,
generate a structured retrieval plan.

## User Intent
{scope.intent}

## Available Sources
{source_schemas}

## Context Packs (System Specs)
{pack_specs}

## Constraints
- max_notes: {policy.max_notes}
- max_tasks: {policy.max_tasks}
- max_events: {policy.max_events}
- max_tokens: {policy.max_tokens}

Generate a JSON retrieval plan with directives. Each directive must:
1. Use a valid source_id from the available sources
2. Use valid field names and operators for that source
3. Include a reason explaining why this directive is needed

Output format:
```json
{{
  "directives": [
    {{
      "source_id": "string",
      "entity_type": "string",
      "query": "optional semantic query",
      "filters": [
        {{"field": "string", "op": "string", "value": "any"}}
      ],
      "top_k": 10,
      "reason": "why this directive"
    }}
  ],
  "assumptions": ["list of assumptions made"]
}}
```
"""

        try:
            response = await self._llm_service.complete(
                prompt=prompt,
                model=self._model,
                temperature=0.3,
            )

            # Parse and validate response
            plan = self._parse_llm_response(response, scope, packs)
            plan = self._validate_plan(plan)

            logger.debug(
                "instructed_plan_created",
                plan_id=plan.retrieval_plan_id,
                directive_count=len(plan.directives),
            )

            return plan

        except Exception as e:
            logger.warning(
                "instructed_plan_failed",
                error=str(e),
                falling_back="baseline",
            )
            # Fall back to baseline planner
            baseline = BaselineRetrievalPlanner(self._source_registry)
            return await baseline.plan(scope, packs, policy)

    def _build_source_schema_prompt(self) -> str:
        """Build prompt section describing available sources."""
        lines = []
        for source in self._source_registry.list_sources():
            lines.append(f"\n### {source.source_id}")
            lines.append(f"Description: {source.description}")
            lines.append("Fields:")
            for field in source.fields:
                ops = ", ".join(field.allowed_ops)
                lines.append(f"  - {field.name} ({field.type}): ops=[{ops}]")
            if source.constraints.requires_live_fetch:
                lines.append("Note: Requires live fetch (not indexed)")
            lines.append("")
        return "\n".join(lines)

    def _build_pack_specs_prompt(self, packs: list[ContextPack]) -> str:
        """Build prompt section describing included context packs."""
        if not packs:
            return "No context packs specified."

        lines = []
        for pack in packs:
            lines.append(f"- {pack.name}: {pack.description or 'No description'}")
        return "\n".join(lines)

    def _parse_llm_response(
        self,
        response: str,
        scope: RetrievalScope,
        packs: list[ContextPack],
    ) -> RetrievalPlan:
        """Parse LLM response into RetrievalPlan."""
        import json
        import re

        # Extract JSON from markdown code block if present
        json_match = re.search(r"```json\s*(.*?)\s*```", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = response

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # If parsing fails, return empty plan
            return RetrievalPlan(
                retrieval_plan_id=generate_ulid(),
                intent=scope.intent,
                mode="instructed",
                packs_used=[p.pack_id for p in packs],
                directives=[],
                assumptions=["Failed to parse LLM response"],
            )

        directives = []
        for d in data.get("directives", []):
            filters = [
                RetrievalFilter(
                    field=f.get("field", ""),
                    op=f.get("op", "eq"),
                    value=f.get("value"),
                )
                for f in d.get("filters", [])
            ]
            directives.append(
                RetrievalDirective(
                    directive_id=generate_ulid(),
                    source_id=d.get("source_id", "obsidian"),
                    entity_type=d.get("entity_type", "note"),
                    query=d.get("query"),
                    filters=filters,
                    top_k=d.get("top_k", 10),
                    reason=d.get("reason"),
                )
            )

        return RetrievalPlan(
            retrieval_plan_id=generate_ulid(),
            intent=scope.intent,
            mode="instructed",
            packs_used=[p.pack_id for p in packs],
            directives=directives,
            assumptions=data.get("assumptions", []),
        )

    def _validate_plan(self, plan: RetrievalPlan) -> RetrievalPlan:
        """Validate plan against source registry.

        Removes invalid directives and logs warnings.
        """
        valid_directives = []

        for directive in plan.directives:
            # Validate source exists
            if not self._source_registry.has_source(directive.source_id):
                logger.warning(
                    "invalid_source_in_plan",
                    source_id=directive.source_id,
                )
                continue

            # Validate filters
            valid_filters = []
            for f in directive.filters:
                is_valid, error = self._source_registry.validate_filter(
                    directive.source_id,
                    f.field,
                    f.op,
                )
                if is_valid:
                    valid_filters.append(f)
                else:
                    logger.warning(
                        "invalid_filter_in_plan",
                        source_id=directive.source_id,
                        field=f.field,
                        op=f.op,
                        error=error,
                    )

            directive.filters = valid_filters
            valid_directives.append(directive)

        plan.directives = valid_directives
        return plan
