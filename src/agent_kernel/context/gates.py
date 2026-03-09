"""Retrieval Gates for v1.0.2 flexible context retrieval.

Coverage gates verify retrieval quality before packing the ContextPacket.
Gates are deterministic validators that check various quality criteria.

v1.0.4 additions:
- SourceConstraintEnforcementGate: Verify source constraints are respected
- ExperienceWarningGate: Inject warnings from similar failures
- PlaybookCoverageGate: Verify playbook requirements are met
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.context.source_registry import SourceRegistry
from agent_kernel.core.schemas import ContextItem, RefType
from agent_kernel.core.schemas.context_pack import ContextPack
from agent_kernel.core.schemas.retrieval import (
    CoverageGateResult,
    RetrievalPlan,
    RetrievalQualityReport,
)

if TYPE_CHECKING:
    from agent_kernel.context.playbook_resolver import PlaybookResolutionResult
    from agent_kernel.memory.experience_store import ExperienceStore
    from agent_kernel.services.index_state import IndexStateStore

logger = structlog.get_logger(__name__)


class RetrievalGate(ABC):
    """Abstract base class for retrieval gates."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of this gate."""

    @abstractmethod
    def check(
        self,
        items: list[ContextItem],
        packs: list[ContextPack],
        plan: RetrievalPlan,
        **kwargs: Any,
    ) -> CoverageGateResult:
        """Run the gate check.

        Args:
            items: Retrieved context items.
            packs: Context packs that should be included.
            plan: The retrieval plan that was executed.
            **kwargs: Additional context for specific gates.

        Returns:
            CoverageGateResult indicating pass/fail.
        """


class PackPresenceGate(RetrievalGate):
    """Verify that required context packs are represented in items.

    This gate checks that for packs with include_policy="always",
    at least one of their refs is present in the context items.
    """

    @property
    def name(self) -> str:
        return "PackPresenceGate"

    def check(
        self,
        items: list[ContextItem],
        packs: list[ContextPack],
        plan: RetrievalPlan,
        **kwargs: Any,
    ) -> CoverageGateResult:
        # Get ref_ids from items
        item_ref_ids = {item.ref.ref_id for item in items}

        # Check "always" packs have their refs present
        missing_packs = []
        for pack in packs:
            if pack.include_policy == "always":
                # Check if any of the pack's refs are in items
                pack_ref_ids = {ref.ref_id for ref in pack.refs}
                if not pack_ref_ids & item_ref_ids:
                    missing_packs.append(pack.pack_id)

        if missing_packs:
            return CoverageGateResult(
                gate=self.name,
                passed=False,
                severity="warning",
                details=f"Missing required packs: {missing_packs}",
            )

        return CoverageGateResult(
            gate=self.name,
            passed=True,
            details=f"All {len(packs)} packs represented",
        )


class SchemaAwareFiltersGate(RetrievalGate):
    """Verify that all filters in the plan are schema-valid.

    This gate validates that the retrieval plan only uses
    fields and operators that exist in the source descriptors.
    """

    def __init__(self, source_registry: SourceRegistry) -> None:
        self._source_registry = source_registry

    @property
    def name(self) -> str:
        return "SchemaAwareFiltersGate"

    def check(
        self,
        items: list[ContextItem],
        packs: list[ContextPack],
        plan: RetrievalPlan,
        **kwargs: Any,
    ) -> CoverageGateResult:
        invalid_filters = []

        for directive in plan.directives:
            for f in directive.filters:
                is_valid, error = self._source_registry.validate_filter(
                    directive.source_id,
                    f.field,
                    f.op,
                )
                if not is_valid:
                    invalid_filters.append(
                        f"{directive.source_id}.{f.field} {f.op}: {error}"
                    )

        if invalid_filters:
            return CoverageGateResult(
                gate=self.name,
                passed=False,
                severity="error",
                details=f"Invalid filters: {invalid_filters[:3]}...",  # Truncate
            )

        return CoverageGateResult(
            gate=self.name,
            passed=True,
            details=f"All filters valid across {len(plan.directives)} directives",
        )


class CoverageGate(RetrievalGate):
    """Verify adequate coverage of different entity types.

    This gate checks that we have minimum representation of
    notes, tasks, and events when they are expected.
    """

    def __init__(
        self,
        min_notes: int = 1,
        min_tasks: int = 0,
        min_events: int = 0,
    ) -> None:
        self._min_notes = min_notes
        self._min_tasks = min_tasks
        self._min_events = min_events

    @property
    def name(self) -> str:
        return "CoverageGate"

    def check(
        self,
        items: list[ContextItem],
        packs: list[ContextPack],
        plan: RetrievalPlan,
        **kwargs: Any,
    ) -> CoverageGateResult:
        # Count by type
        counts = {
            "notes": 0,
            "tasks": 0,
            "events": 0,
        }

        for item in items:
            if item.ref.ref_type in (RefType.NOTE, RefType.DOCUMENT, RefType.RULE, RefType.SPEC):
                counts["notes"] += 1
            elif item.ref.ref_type == RefType.TASK:
                counts["tasks"] += 1
            elif item.ref.ref_type == RefType.EVENT:
                counts["events"] += 1

        # Check minimums
        failures = []
        if counts["notes"] < self._min_notes:
            failures.append(f"notes: {counts['notes']} < {self._min_notes}")
        if counts["tasks"] < self._min_tasks:
            failures.append(f"tasks: {counts['tasks']} < {self._min_tasks}")
        if counts["events"] < self._min_events:
            failures.append(f"events: {counts['events']} < {self._min_events}")

        if failures:
            return CoverageGateResult(
                gate=self.name,
                passed=False,
                severity="warning",
                details=f"Insufficient coverage: {failures}",
            )

        return CoverageGateResult(
            gate=self.name,
            passed=True,
            details=f"Coverage OK: notes={counts['notes']}, tasks={counts['tasks']}, events={counts['events']}",
        )


class RecencyGate(RetrievalGate):
    """Verify that recency constraints are satisfied when requested.

    If the plan has recency_boost directives, check that we have
    at least some recent items in the results.
    """

    def __init__(self, recency_days: int = 7, min_recent: int = 1) -> None:
        self._recency_days = recency_days
        self._min_recent = min_recent

    @property
    def name(self) -> str:
        return "RecencyGate"

    def check(
        self,
        items: list[ContextItem],
        packs: list[ContextPack],
        plan: RetrievalPlan,
        **kwargs: Any,
    ) -> CoverageGateResult:
        # Check if any directive requested recency boost
        has_recency_request = any(d.recency_boost for d in plan.directives)

        if not has_recency_request:
            return CoverageGateResult(
                gate=self.name,
                passed=True,
                details="No recency constraint requested",
            )

        # Count items with recent modified_at
        now = datetime.now(UTC)
        recent_count = 0

        for item in items:
            modified_at = item.ref.metadata.get("modified_at")
            if modified_at:
                try:
                    if isinstance(modified_at, str):
                        mod_dt = datetime.fromisoformat(modified_at.replace("Z", "+00:00"))
                    else:
                        mod_dt = modified_at
                    days_ago = (now - mod_dt).days
                    if days_ago <= self._recency_days:
                        recent_count += 1
                except (ValueError, TypeError):
                    pass

        if recent_count < self._min_recent:
            return CoverageGateResult(
                gate=self.name,
                passed=False,
                severity="warning",
                details=f"Only {recent_count} items from last {self._recency_days} days (need {self._min_recent})",
            )

        return CoverageGateResult(
            gate=self.name,
            passed=True,
            details=f"{recent_count} recent items found",
        )


class ParityGate(RetrievalGate):
    """Verify that retrieved items are not stale vs canonical source.

    This gate checks IndexState for hash mismatches between
    derived indexes and canonical content.
    """

    def __init__(self, index_state_store: IndexStateStore | None = None) -> None:
        self._index_state_store = index_state_store

    @property
    def name(self) -> str:
        return "ParityGate"

    def check(
        self,
        items: list[ContextItem],
        packs: list[ContextPack],
        plan: RetrievalPlan,
        **kwargs: Any,
    ) -> CoverageGateResult:
        if not self._index_state_store:
            return CoverageGateResult(
                gate=self.name,
                passed=True,
                details="No IndexStateStore configured, skipping parity check",
            )

        stale_items = []

        for item in items:
            ref_id = item.ref.ref_id
            item_hash = item.ref.hash

            if not item_hash:
                continue

            # Look up index state
            state = self._index_state_store.get(ref_id)
            if state and state.content_hash != item_hash:
                stale_items.append(ref_id)

        if stale_items:
            return CoverageGateResult(
                gate=self.name,
                passed=False,
                severity="warning",
                details=f"{len(stale_items)} items have stale index data: {stale_items[:3]}...",
            )

        return CoverageGateResult(
            gate=self.name,
            passed=True,
            details=f"All {len(items)} items have current index data",
        )


# =============================================================================
# v1.0.4 Experience/Playbook Gates
# =============================================================================


class SourceConstraintEnforcementGate(RetrievalGate):
    """Verify that source constraints are not violated.
    
    Ensures no forbidden content is persisted or cached against
    SourceDescriptor constraints (e.g., can_store_text=false).
    """

    def __init__(self, source_registry: SourceRegistry | None = None) -> None:
        self._source_registry = source_registry

    @property
    def name(self) -> str:
        return "SourceConstraintEnforcementGate"

    def check(
        self,
        items: list[ContextItem],
        packs: list[ContextPack],
        plan: RetrievalPlan,
        **kwargs: Any,
    ) -> CoverageGateResult:
        if not self._source_registry:
            return CoverageGateResult(
                gate=self.name,
                passed=True,
                details="No source registry configured, skipping constraint check",
            )

        violations = []

        for item in items:
            source_id = item.ref.source_id if hasattr(item.ref, "source_id") else None
            if not source_id:
                continue

            descriptor = self._source_registry.get_source(source_id)
            if not descriptor:
                continue

            # Check can_store_text constraint
            if descriptor.constraints:
                can_store = descriptor.constraints.can_store_text
                if not can_store and item.excerpt and len(item.excerpt) > 100:
                    violations.append(
                        f"{source_id}: content should not be stored (>100 chars)"
                    )

        if violations:
            return CoverageGateResult(
                gate=self.name,
                passed=False,
                severity="error",
                details=f"Source constraint violations: {violations[:3]}",
            )

        return CoverageGateResult(
            gate=self.name,
            passed=True,
            details="All source constraints respected",
        )


class ExperienceWarningGate(RetrievalGate):
    """Inject warnings from similar past failures.
    
    If similar failures exist for this workflow/capability combination,
    this gate adds warnings to the quality report.
    """

    def __init__(self, experience_store: ExperienceStore | None = None) -> None:
        self._experience_store = experience_store

    @property
    def name(self) -> str:
        return "ExperienceWarningGate"

    def check(
        self,
        items: list[ContextItem],
        packs: list[ContextPack],
        plan: RetrievalPlan,
        **kwargs: Any,
    ) -> CoverageGateResult:
        if not self._experience_store:
            return CoverageGateResult(
                gate=self.name,
                passed=True,
                details="No experience store configured",
            )

        workflow_id = kwargs.get("workflow_id")
        capability_names = kwargs.get("capability_names", [])

        # Look for similar failures
        from agent_kernel.core.schemas.experience import OutcomeLabel

        failures = self._experience_store.find_similar_cases(
            workflow_id=workflow_id,
            capability_names=capability_names,
            label=OutcomeLabel.FAILURE,
            limit=3,
        )

        if not failures:
            return CoverageGateResult(
                gate=self.name,
                passed=True,
                details="No similar failures found",
            )

        # Build warning message from failures
        warnings = []
        for case in failures:
            category = case.failure_category.value if case.failure_category else "unknown"
            warnings.append(f"Previous failure ({category}): {case.outcome_summary or case.intent[:50]}")

        # Get relevant lessons
        lessons = self._experience_store.list_lessons(status="active", limit=3)
        for lesson in lessons:
            if lesson.scope.workflow_id == workflow_id:
                warnings.append(f"Lesson: {lesson.title}")

        return CoverageGateResult(
            gate=self.name,
            passed=True,  # Always passes, just adds warnings
            severity="warning",
            details=f"Experience warnings: {len(failures)} similar failures. {warnings[:2]}",
        )


class PlaybookCoverageGate(RetrievalGate):
    """Verify that playbook requirements are met.
    
    If a playbook requires certain entity types or sources,
    ensure the ContextPacket includes them or flag for escalation.
    """

    def __init__(self, playbook_result: PlaybookResolutionResult | None = None) -> None:
        self._playbook_result = playbook_result

    @property
    def name(self) -> str:
        return "PlaybookCoverageGate"

    def check(
        self,
        items: list[ContextItem],
        packs: list[ContextPack],
        plan: RetrievalPlan,
        **kwargs: Any,
    ) -> CoverageGateResult:
        # Allow runtime override
        playbook_result = kwargs.get("playbook_result") or self._playbook_result

        if not playbook_result or not playbook_result.primary_playbook:
            return CoverageGateResult(
                gate=self.name,
                passed=True,
                details="No playbook requirements",
            )

        pb = playbook_result
        missing_entity_types = []
        missing_sources = []

        # Get entity types and sources from items
        found_entity_types: set[str] = set()
        found_sources: set[str] = set()

        for item in items:
            if hasattr(item.ref, "entity_type") and item.ref.entity_type:
                found_entity_types.add(item.ref.entity_type)
            if hasattr(item.ref, "source_id") and item.ref.source_id:
                found_sources.add(item.ref.source_id)

            # Also check metadata for legacy refs
            entity_type = item.ref.metadata.get("entity_type")
            source_id = item.ref.metadata.get("source_id")
            if entity_type:
                found_entity_types.add(entity_type)
            if source_id:
                found_sources.add(source_id)

        # Check required entity types
        for req_type in pb.required_entity_types:
            if req_type not in found_entity_types:
                missing_entity_types.append(req_type)

        # Check required sources
        for req_source in pb.required_sources:
            if req_source not in found_sources:
                missing_sources.append(req_source)

        if missing_entity_types or missing_sources:
            details = []
            if missing_entity_types:
                details.append(f"Missing entity types: {missing_entity_types}")
            if missing_sources:
                details.append(f"Missing sources: {missing_sources}")

            return CoverageGateResult(
                gate=self.name,
                passed=False,
                severity="warning",
                details="; ".join(details),
            )

        return CoverageGateResult(
            gate=self.name,
            passed=True,
            details=f"Playbook requirements met (types: {list(found_entity_types)}, sources: {list(found_sources)})",
        )


class RetrievalGateRunner:
    """Runs multiple retrieval gates and produces a quality report."""

    def __init__(
        self,
        gates: list[RetrievalGate] | None = None,
        source_registry: SourceRegistry | None = None,
        index_state_store: IndexStateStore | None = None,
    ) -> None:
        """Initialize the gate runner.

        Args:
            gates: Custom gates to run. If None, uses default gates.
            source_registry: Registry for schema validation.
            index_state_store: Store for parity checking.
        """
        if gates:
            self._gates = gates
        else:
            # Default gates
            self._gates: list[RetrievalGate] = [
                PackPresenceGate(),
                CoverageGate(),
                RecencyGate(),
            ]

            if source_registry:
                self._gates.append(SchemaAwareFiltersGate(source_registry))

            if index_state_store:
                self._gates.append(ParityGate(index_state_store))

        logger.info(
            "retrieval_gate_runner_initialized",
            gate_count=len(self._gates),
            gate_names=[g.name for g in self._gates],
        )

    def run(
        self,
        items: list[ContextItem],
        packs: list[ContextPack],
        plan: RetrievalPlan,
        **kwargs: Any,
    ) -> RetrievalQualityReport:
        """Run all gates and produce quality report.

        Args:
            items: Retrieved context items.
            packs: Context packs included.
            plan: The retrieval plan executed.
            **kwargs: Additional context for specific gates.

        Returns:
            RetrievalQualityReport with gate results.
        """
        gate_results: list[CoverageGateResult] = []
        warnings: list[str] = []

        for gate in self._gates:
            try:
                result = gate.check(items, packs, plan, **kwargs)
                gate_results.append(result)

                if not result.passed and result.severity == "warning":
                    warnings.append(f"{gate.name}: {result.details}")

            except Exception as e:
                logger.warning(
                    "gate_check_failed",
                    gate=gate.name,
                    error=str(e),
                )
                gate_results.append(
                    CoverageGateResult(
                        gate=gate.name,
                        passed=False,
                        severity="error",
                        details=f"Gate error: {e}",
                    )
                )

        report = RetrievalQualityReport(
            mode=plan.mode,
            packs_included=[p.pack_id for p in packs],
            directives_executed=len(plan.directives),
            candidates_considered=len(items),  # Before dedup
            items_selected=len(items),
            gate_results=gate_results,
            warnings=warnings,
        )

        logger.debug(
            "gates_run",
            passed=report.all_gates_passed,
            has_errors=report.has_errors,
            has_warnings=report.has_warnings,
        )

        return report

    def add_gate(self, gate: RetrievalGate) -> None:
        """Add a gate to the runner."""
        self._gates.append(gate)

    def remove_gate(self, gate_name: str) -> bool:
        """Remove a gate by name."""
        for i, gate in enumerate(self._gates):
            if gate.name == gate_name:
                self._gates.pop(i)
                return True
        return False
