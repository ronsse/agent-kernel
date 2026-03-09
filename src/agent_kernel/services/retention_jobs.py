"""Retention and Compaction Jobs (v1.0.4).

Implements policy-driven data lifecycle management:
- TraceCompactorJob: Compress old traces → ExperienceCase
- VectorPrunerJob: Drop stale chunk embeddings
- GraphPrunerJob: Remove low-confidence auto edges
- CacheJanitorJob: Enforce cache TTL and size limits

References:
- Design Patch v1.0.4: Universal Context System
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
import yaml

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import SCHEMA_VERSION, get_kernel_version, utc_now
from agent_kernel.core.schemas.experience import (
    ExperienceCase,
    FailureCategory,
    OutcomeLabel,
)
from agent_kernel.core.schemas.retention import (
    DEFAULT_RETENTION_POLICY,
    RetentionPolicy,
)

if TYPE_CHECKING:
    from agent_kernel.memory.experience_store import ExperienceStore
    from agent_kernel.memory.graph_store import GraphStore
    from agent_kernel.memory.vector_store import VectorStore
    from agent_kernel.tracing.trace_store import TraceStore

logger = structlog.get_logger(__name__)


@dataclass
class JobResult:
    """Result of a retention job run."""

    job_id: str
    job_type: str
    started_at: datetime
    ended_at: datetime | None = None
    status: str = "running"
    items_processed: int = 0
    items_deleted: int = 0
    items_compacted: int = 0
    bytes_freed: int = 0
    errors: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


class RetentionJob(ABC):
    """Base class for retention jobs."""

    @property
    @abstractmethod
    def job_type(self) -> str:
        """Get the job type identifier."""
        ...

    @abstractmethod
    def run(self, dry_run: bool = False) -> JobResult:
        """Run the job.
        
        Args:
            dry_run: If True, don't actually delete/compact, just report what would happen
            
        Returns:
            Result of the job run
        """
        ...


class TraceCompactorJob(RetentionJob):
    """Compacts old traces into ExperienceCases.
    
    Traces older than hot_days are compacted:
    - Create ExperienceCase with summaries
    - Optionally delete heavy fields (tool I/O)
    """

    def __init__(
        self,
        trace_store: TraceStore,
        experience_store: ExperienceStore,
        policy: RetentionPolicy | None = None,
    ) -> None:
        self._trace_store = trace_store
        self._experience_store = experience_store
        self._policy = policy or DEFAULT_RETENTION_POLICY

    @property
    def job_type(self) -> str:
        return "trace_compactor"

    def run(self, dry_run: bool = False) -> JobResult:
        """Run trace compaction."""
        job_id = f"job_{generate_ulid()}"
        started_at = utc_now()
        result = JobResult(
            job_id=job_id,
            job_type=self.job_type,
            started_at=started_at,
        )

        try:
            # Calculate cutoff dates
            hot_cutoff = started_at - timedelta(days=self._policy.traces.hot_days)
            warm_cutoff = started_at - timedelta(days=self._policy.traces.warm_days)

            # Get traces older than hot_days
            traces = self._trace_store.list_traces(before=hot_cutoff, limit=1000)
            result.items_processed = len(traces)

            for trace in traces:
                try:
                    # Check if case already exists
                    existing_case = self._experience_store.get_case_for_trace(trace.trace_id)
                    if existing_case:
                        continue

                    # Create ExperienceCase from trace
                    case = self._create_case_from_trace(trace)
                    
                    if not dry_run:
                        self._experience_store.put_case(case)
                        result.items_compacted += 1

                        # If older than warm_days, could delete trace details here
                        # (not implemented yet - would need trace_store.compact_trace method)

                except Exception as e:
                    result.errors.append(f"Error compacting trace {trace.trace_id}: {e}")

            result.status = "completed"

        except Exception as e:
            result.status = "failed"
            result.errors.append(f"Job failed: {e}")
            logger.error("trace_compactor_failed", error=str(e))

        result.ended_at = utc_now()
        result.details["dry_run"] = dry_run
        result.details["hot_cutoff"] = hot_cutoff.isoformat()

        logger.info(
            "trace_compactor_completed",
            job_id=job_id,
            processed=result.items_processed,
            compacted=result.items_compacted,
            errors=len(result.errors),
        )

        return result

    def _create_case_from_trace(self, trace: Any) -> ExperienceCase:
        """Create an ExperienceCase from a DecisionTrace."""
        now = utc_now()

        # Extract relevant info from trace
        capability_names = []
        sources_used = []
        entity_types_used = []

        if trace.tool_calls:
            for call in trace.tool_calls:
                if call.capability_name and call.capability_name not in capability_names:
                    capability_names.append(call.capability_name)

        if trace.context_packet and trace.context_packet.items:
            for item in trace.context_packet.items:
                ref = item.ref
                if ref.source_id and ref.source_id not in sources_used:
                    sources_used.append(ref.source_id)
                if ref.entity_type and ref.entity_type not in entity_types_used:
                    entity_types_used.append(ref.entity_type)

        # Determine outcome label
        label = OutcomeLabel.UNKNOWN
        if trace.outcome:
            if trace.outcome.status == "success":
                label = OutcomeLabel.SUCCESS
            elif trace.outcome.status == "failure":
                label = OutcomeLabel.FAILURE
            elif trace.outcome.status == "partial":
                label = OutcomeLabel.PARTIAL

        return ExperienceCase(
            case_id=f"case_{generate_ulid()}",
            trace_id=trace.trace_id,
            intent=trace.context_packet.intent if trace.context_packet else "",
            context_summary=self._summarize_context(trace.context_packet) if trace.context_packet else None,
            plan_summary=self._summarize_plan(trace.plan) if trace.plan else None,
            outcome_summary=self._summarize_outcome(trace.outcome) if trace.outcome else None,
            workflow_id=trace.workflow_id,
            agent_profile_id=trace.agent_profile_id,
            capability_names=capability_names,
            sources_used=sources_used,
            entity_types_used=entity_types_used,
            label=label,
            created_at=now,
            updated_at=now,
        )

    def _summarize_context(self, context_packet: Any) -> str:
        """Summarize a context packet."""
        if not context_packet:
            return ""
        item_count = len(context_packet.items) if context_packet.items else 0
        return f"Context with {item_count} items for intent: {context_packet.intent[:100]}"

    def _summarize_plan(self, plan: Any) -> str:
        """Summarize a plan."""
        if not plan:
            return ""
        action_count = len(plan.actions) if plan.actions else 0
        return f"Plan with {action_count} actions"

    def _summarize_outcome(self, outcome: Any) -> str:
        """Summarize an outcome."""
        if not outcome:
            return ""
        return f"Outcome: {outcome.status}"


class VectorPrunerJob(RetentionJob):
    """Prunes old/excess vector embeddings.
    
    - Drop chunk vectors for archived entities
    - Drop chunks older than retention period
    - Keep summary vectors longer
    """

    def __init__(
        self,
        vector_store: VectorStore,
        policy: RetentionPolicy | None = None,
    ) -> None:
        self._vector_store = vector_store
        self._policy = policy or DEFAULT_RETENTION_POLICY

    @property
    def job_type(self) -> str:
        return "vector_pruner"

    def run(self, dry_run: bool = False) -> JobResult:
        """Run vector pruning."""
        job_id = f"job_{generate_ulid()}"
        started_at = utc_now()
        result = JobResult(
            job_id=job_id,
            job_type=self.job_type,
            started_at=started_at,
        )

        try:
            chunk_cutoff = started_at - timedelta(
                days=self._policy.vectors.keep_chunk_embeddings_days
            )

            # Get vectors to prune (chunks older than cutoff)
            vectors = self._vector_store.list_vectors(limit=10000)
            result.items_processed = len(vectors)

            for vector in vectors:
                metadata = vector.get("metadata", {})
                embedding_type = metadata.get("embedding_type") or metadata.get("view_type")
                created_at_str = metadata.get("created_at")

                # Only prune chunks, not summaries
                if embedding_type != "chunk":
                    continue

                # Check age
                if created_at_str:
                    try:
                        created_at = datetime.fromisoformat(created_at_str)
                        if created_at > chunk_cutoff:
                            continue  # Too recent to prune
                    except (ValueError, TypeError):
                        pass  # Couldn't parse date, consider for pruning

                if not dry_run:
                    self._vector_store.delete(vector["item_id"])
                    result.items_deleted += 1

            # Check total vector count
            total_count = len(vectors)
            if total_count > self._policy.vectors.max_total_vectors:
                result.details["over_limit"] = True
                result.details["total_vectors"] = total_count
                result.details["limit"] = self._policy.vectors.max_total_vectors

            result.status = "completed"

        except Exception as e:
            result.status = "failed"
            result.errors.append(f"Job failed: {e}")
            logger.error("vector_pruner_failed", error=str(e))

        result.ended_at = utc_now()
        result.details["dry_run"] = dry_run

        logger.info(
            "vector_pruner_completed",
            job_id=job_id,
            processed=result.items_processed,
            deleted=result.items_deleted,
            errors=len(result.errors),
        )

        return result


class GraphPrunerJob(RetentionJob):
    """Prunes low-confidence auto-generated graph edges.
    
    - Delete auto edges below confidence threshold
    - Delete old auto edges
    - Never delete human-created edges
    """

    def __init__(
        self,
        graph_store: GraphStore,
        policy: RetentionPolicy | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._policy = policy or DEFAULT_RETENTION_POLICY

    @property
    def job_type(self) -> str:
        return "graph_pruner"

    def run(self, dry_run: bool = False) -> JobResult:
        """Run graph pruning."""
        job_id = f"job_{generate_ulid()}"
        started_at = utc_now()
        result = JobResult(
            job_id=job_id,
            job_type=self.job_type,
            started_at=started_at,
        )

        try:
            age_cutoff = started_at - timedelta(
                days=self._policy.graph.prune_auto_edges_older_than_days
            )
            confidence_threshold = self._policy.graph.prune_auto_edges_below_confidence

            # Get all edges (would need graph_store.list_edges method)
            # For now, this is a stub
            result.details["confidence_threshold"] = confidence_threshold
            result.details["age_cutoff"] = age_cutoff.isoformat()
            result.details["note"] = "Full implementation requires graph_store.list_edges"

            result.status = "completed"

        except Exception as e:
            result.status = "failed"
            result.errors.append(f"Job failed: {e}")
            logger.error("graph_pruner_failed", error=str(e))

        result.ended_at = utc_now()
        result.details["dry_run"] = dry_run

        logger.info(
            "graph_pruner_completed",
            job_id=job_id,
            processed=result.items_processed,
            deleted=result.items_deleted,
            errors=len(result.errors),
        )

        return result


class CacheJanitorJob(RetentionJob):
    """Enforces document cache TTL and size limits.
    
    - Remove stale cached documents for live-fetch sources
    - Enforce maximum cache size
    """

    def __init__(
        self,
        document_store: Any,  # DocumentStore
        policy: RetentionPolicy | None = None,
    ) -> None:
        self._document_store = document_store
        self._policy = policy or DEFAULT_RETENTION_POLICY

    @property
    def job_type(self) -> str:
        return "cache_janitor"

    def run(self, dry_run: bool = False) -> JobResult:
        """Run cache cleanup."""
        job_id = f"job_{generate_ulid()}"
        started_at = utc_now()
        result = JobResult(
            job_id=job_id,
            job_type=self.job_type,
            started_at=started_at,
        )

        try:
            ttl_minutes = self._policy.document_cache.requires_live_fetch_ttl_minutes
            max_size_mb = self._policy.document_cache.max_cache_size_mb

            result.details["ttl_minutes"] = ttl_minutes
            result.details["max_size_mb"] = max_size_mb
            result.details["note"] = "Full implementation requires document_store.list_cached"

            result.status = "completed"

        except Exception as e:
            result.status = "failed"
            result.errors.append(f"Job failed: {e}")
            logger.error("cache_janitor_failed", error=str(e))

        result.ended_at = utc_now()
        result.details["dry_run"] = dry_run

        logger.info(
            "cache_janitor_completed",
            job_id=job_id,
            processed=result.items_processed,
            deleted=result.items_deleted,
            errors=len(result.errors),
        )

        return result


class RetentionJobRunner:
    """Runs all retention jobs according to policy."""

    def __init__(
        self,
        trace_store: TraceStore | None = None,
        experience_store: ExperienceStore | None = None,
        vector_store: VectorStore | None = None,
        graph_store: GraphStore | None = None,
        document_store: Any | None = None,
        policy: RetentionPolicy | None = None,
    ) -> None:
        self._policy = policy or DEFAULT_RETENTION_POLICY
        self._jobs: list[RetentionJob] = []

        if trace_store and experience_store:
            self._jobs.append(TraceCompactorJob(
                trace_store=trace_store,
                experience_store=experience_store,
                policy=self._policy,
            ))

        if vector_store:
            self._jobs.append(VectorPrunerJob(
                vector_store=vector_store,
                policy=self._policy,
            ))

        if graph_store:
            self._jobs.append(GraphPrunerJob(
                graph_store=graph_store,
                policy=self._policy,
            ))

        if document_store:
            self._jobs.append(CacheJanitorJob(
                document_store=document_store,
                policy=self._policy,
            ))

    def run_all(self, dry_run: bool = False) -> list[JobResult]:
        """Run all configured retention jobs.
        
        Args:
            dry_run: If True, don't actually modify data
            
        Returns:
            List of job results
        """
        results = []

        for job in self._jobs:
            logger.info("starting_retention_job", job_type=job.job_type)
            result = job.run(dry_run=dry_run)
            results.append(result)

        return results

    def run_job(self, job_type: str, dry_run: bool = False) -> JobResult | None:
        """Run a specific retention job.
        
        Args:
            job_type: Type of job to run
            dry_run: If True, don't actually modify data
            
        Returns:
            Job result or None if job not found
        """
        for job in self._jobs:
            if job.job_type == job_type:
                return job.run(dry_run=dry_run)
        return None


def load_retention_policy(config_path: Path | str) -> RetentionPolicy:
    """Load retention policy from YAML config file.
    
    Args:
        config_path: Path to retention.yaml
        
    Returns:
        Loaded RetentionPolicy
    """
    config_path = Path(config_path)
    if not config_path.exists():
        logger.warning("retention_config_not_found", path=str(config_path))
        return DEFAULT_RETENTION_POLICY

    with open(config_path) as f:
        data = yaml.safe_load(f)

    if not data or "retention" not in data:
        return DEFAULT_RETENTION_POLICY

    return RetentionPolicy(**data["retention"])
