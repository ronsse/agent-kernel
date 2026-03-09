"""Context Graph Hooks - wire trace completion to graph decomposition.

Called after trace completion to decompose traces into graph structure.
Follows the ExperienceMemoryHooks pattern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from agent_kernel.context_graph.ingestion import ContextGraphIngestion
from agent_kernel.core.schemas.knowledge import DecompositionResult
from agent_kernel.core.schemas.trace import DecisionTrace

if TYPE_CHECKING:
    from agent_kernel.context_graph.types import TypeRegistry
    from agent_kernel.core.schemas.experience import LessonLearned, Playbook
    from agent_kernel.memory.event_log import EventLog
    from agent_kernel.memory.graph_store import GraphStore

logger = structlog.get_logger(__name__)


class ContextGraphHooks:
    """Called after trace completion to decompose into graph structure.

    Follows the ExperienceMemoryHooks pattern. Wire this into the
    workflow runner or executor to automatically decompose traces.
    """

    def __init__(
        self,
        graph_store: GraphStore,
        event_log: EventLog | None = None,
        type_registry: TypeRegistry | None = None,
    ) -> None:
        self._ingestion = ContextGraphIngestion(
            graph_store=graph_store,
            event_log=event_log,
            type_registry=type_registry,
        )

    async def on_trace_completed(
        self,
        trace: DecisionTrace,
        success: bool,
    ) -> DecompositionResult:
        """Decompose trace into graph structure.

        This is the PRIMARY ingestion path — called after every
        trace completion to build the event clock.

        Args:
            trace: The completed DecisionTrace.
            success: Whether the trace completed successfully.

        Returns:
            DecompositionResult with created node/edge IDs.
        """
        try:
            result = await self._ingestion.ingest_trace(trace)
            logger.info(
                "trace_decomposed_via_hook",
                trace_id=trace.trace_id,
                success=success,
                trajectory_id=result.trajectory_node_id,
                events=len(result.decision_event_ids),
            )
            return result
        except Exception:
            logger.exception(
                "trace_decomposition_failed",
                trace_id=trace.trace_id,
            )
            raise

    async def on_lesson_created(
        self,
        lesson: LessonLearned,
    ) -> str | None:
        """Sync lesson to INSIGHT node in the graph.

        Args:
            lesson: The newly created lesson.

        Returns:
            The INSIGHT node ID, or None on failure.
        """
        try:
            node_id = await self._ingestion.ingest_lesson(lesson)
            logger.info(
                "lesson_synced_via_hook",
                lesson_id=lesson.lesson_id,
                node_id=node_id,
            )
            return node_id
        except Exception:
            logger.exception(
                "lesson_sync_failed",
                lesson_id=lesson.lesson_id,
            )
            return None

    async def on_playbook_created(
        self,
        playbook: Playbook,
    ) -> str | None:
        """Sync playbook to PRACTICE node in the graph.

        Args:
            playbook: The newly created playbook.

        Returns:
            The PRACTICE node ID, or None on failure.
        """
        try:
            node_id = await self._ingestion.ingest_playbook(playbook)
            logger.info(
                "playbook_synced_via_hook",
                playbook_id=playbook.playbook_id,
                node_id=node_id,
            )
            return node_id
        except Exception:
            logger.exception(
                "playbook_sync_failed",
                playbook_id=playbook.playbook_id,
            )
            return None
