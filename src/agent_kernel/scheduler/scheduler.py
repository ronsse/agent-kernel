"""Scheduler - manages scheduled workflow execution.

Supports cron schedules, manual triggers, event-based triggers, and workflow triggers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.workflows.runner import WorkflowRunner
from agent_kernel.workflows.spec import TriggerType, WorkflowSpec

if TYPE_CHECKING:
    from agent_kernel.memory.event_log import EventLog

logger = structlog.get_logger(__name__)


@dataclass
class CronField:
    """Represents a single cron field."""

    values: set[int]
    min_val: int
    max_val: int

    @classmethod
    def parse(cls, expr: str, min_val: int, max_val: int) -> CronField:
        """Parse a cron field expression."""
        values: set[int] = set()

        for part in expr.split(","):
            if part == "*":
                values.update(range(min_val, max_val + 1))
            elif "/" in part:
                # Step values: */5 or 0-30/5
                base, step = part.split("/")
                step_int = int(step)
                if base == "*":
                    values.update(range(min_val, max_val + 1, step_int))
                elif "-" in base:
                    start, end = map(int, base.split("-"))
                    values.update(range(start, end + 1, step_int))
                else:
                    start = int(base)
                    values.update(range(start, max_val + 1, step_int))
            elif "-" in part:
                # Range: 1-5
                start, end = map(int, part.split("-"))
                values.update(range(start, end + 1))
            else:
                # Single value
                values.add(int(part))

        return cls(values=values, min_val=min_val, max_val=max_val)

    def matches(self, value: int) -> bool:
        """Check if value matches this field."""
        return value in self.values

    def next_value(self, current: int, wrap: bool = True) -> int | None:
        """Get next matching value >= current."""
        for v in sorted(self.values):
            if v >= current:
                return v
        return min(self.values) if wrap and self.values else None


class CronExpression:
    """Parses and evaluates cron expressions.

    Standard 5-field cron format:
    minute hour day-of-month month day-of-week

    Examples:
    - "0 9 * * 1-5"  = 9:00 AM Monday-Friday
    - "*/15 * * * *" = Every 15 minutes
    - "0 0 1 * *"    = Midnight on first of month
    """

    def __init__(self, expression: str) -> None:
        """Parse cron expression.

        Args:
            expression: Standard 5-field cron expression.
        """
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError(
                f"Invalid cron expression: expected 5 fields, got {len(parts)}"
            )

        self.minute = CronField.parse(parts[0], 0, 59)
        self.hour = CronField.parse(parts[1], 0, 23)
        self.day_of_month = CronField.parse(parts[2], 1, 31)
        self.month = CronField.parse(parts[3], 1, 12)
        self.day_of_week = CronField.parse(parts[4], 0, 6)  # 0=Sunday
        self.expression = expression

    def matches(self, dt: datetime) -> bool:
        """Check if datetime matches this expression."""
        # Day of week: Python uses Monday=0, cron uses Sunday=0
        dow = (dt.weekday() + 1) % 7

        return (
            self.minute.matches(dt.minute)
            and self.hour.matches(dt.hour)
            and self.day_of_month.matches(dt.day)
            and self.month.matches(dt.month)
            and self.day_of_week.matches(dow)
        )

    def next_run(self, after: datetime | None = None) -> datetime:
        """Calculate next run time after given datetime.

        Args:
            after: Start time (default: now).

        Returns:
            Next matching datetime.
        """
        if after is None:
            after = utc_now()

        # Start from next minute
        current = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

        # Search up to 2 years ahead
        max_iterations = 365 * 24 * 60 * 2

        for _ in range(max_iterations):
            if self.matches(current):
                return current
            current += timedelta(minutes=1)

        # Fallback: return 1 day from now
        return after + timedelta(days=1)


# Type for event handlers
EventHandler = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]


@dataclass
class EventTrigger:
    """Event-based trigger configuration."""

    event_type: str
    filter_pattern: str | None = None
    handler: EventHandler | None = None


@dataclass
class WorkflowTriggerConfig:
    """Configuration for a workflow-triggered workflow."""

    target_workflow_id: str
    source_workflow_id: str
    on_success_only: bool = True


class ScheduledJob:
    """A scheduled workflow job."""

    def __init__(
        self,
        workflow_id: str,
        trigger_type: TriggerType,
        schedule: str | None = None,
        next_run: datetime | None = None,
        source_workflow_id: str | None = None,
    ) -> None:
        self.workflow_id = workflow_id
        self.trigger_type = trigger_type
        self.schedule = schedule
        self.next_run = next_run
        self.source_workflow_id = source_workflow_id  # For workflow triggers
        self.last_run: datetime | None = None
        self.run_count = 0
        self.enabled = True


class Scheduler:
    """Manages scheduled workflow execution.

    Supports:
    - Manual triggers
    - Cron schedules
    - Event-based triggers
    - Workflow triggers (triggered when another workflow completes)
    """

    def __init__(
        self,
        workflow_runner: WorkflowRunner,
        event_log: EventLog | None = None,
    ) -> None:
        """Initialize scheduler.

        Args:
            workflow_runner: The workflow runner to use.
            event_log: Optional event log for subscribing to workflow events.
        """
        self._runner = workflow_runner
        self._event_log = event_log
        self._jobs: dict[str, ScheduledJob] = {}
        self._running = False
        self._task: asyncio.Task | None = None
        # Map: source_workflow_id -> list of WorkflowTriggerConfig
        self._workflow_triggers: dict[str, list[WorkflowTriggerConfig]] = {}
        # Map: workflow_id -> list of workflow_ids to trigger on completion
        self._on_complete_chains: dict[str, list[str]] = {}
        logger.info("scheduler_initialized")

    def register_workflow(self, spec: WorkflowSpec) -> ScheduledJob:
        """Register a workflow for scheduling.

        Args:
            spec: The workflow specification.

        Returns:
            The scheduled job.
        """
        job = ScheduledJob(
            workflow_id=spec.workflow_id,
            trigger_type=spec.trigger.type,
            schedule=spec.trigger.schedule,
            source_workflow_id=spec.trigger.source_workflow_id,
        )

        if spec.trigger.type == TriggerType.CRON and spec.trigger.schedule:
            job.next_run = self._calculate_next_cron(spec.trigger.schedule)

        # Handle workflow triggers
        if (
            spec.trigger.type == TriggerType.WORKFLOW
            and spec.trigger.source_workflow_id
        ):
            source_id = spec.trigger.source_workflow_id
            if source_id not in self._workflow_triggers:
                self._workflow_triggers[source_id] = []
            self._workflow_triggers[source_id].append(
                WorkflowTriggerConfig(
                    target_workflow_id=spec.workflow_id,
                    source_workflow_id=source_id,
                    on_success_only=spec.trigger.on_success_only,
                )
            )
            logger.info(
                "workflow_trigger_registered",
                target_workflow_id=spec.workflow_id,
                source_workflow_id=source_id,
            )

        # Handle on_complete chains
        if spec.on_complete:
            self._on_complete_chains[spec.workflow_id] = list(spec.on_complete)
            logger.info(
                "on_complete_chain_registered",
                workflow_id=spec.workflow_id,
                chain=spec.on_complete,
            )

        self._jobs[spec.workflow_id] = job
        logger.info(
            "workflow_registered",
            workflow_id=spec.workflow_id,
            trigger_type=spec.trigger.type.value,
        )
        return job

    async def trigger_manual(
        self,
        workflow_id: str,
        intent: str | None = None,
        project_id: str | None = None,
        trigger_dependents: bool = True,
    ) -> Any:
        """Manually trigger a workflow.

        Args:
            workflow_id: The workflow to trigger.
            intent: Optional intent override.
            project_id: Optional project scope.
            trigger_dependents: Whether to trigger dependent workflows on completion.

        Returns:
            WorkflowResult from the run.
        """
        logger.info("manual_trigger", workflow_id=workflow_id)

        job = self._jobs.get(workflow_id)
        if job:
            job.last_run = utc_now()
            job.run_count += 1

        result = await self._runner.run(
            workflow_id=workflow_id,
            intent=intent,
            project_id=project_id,
        )

        # Trigger dependent workflows if requested and workflow succeeded
        if trigger_dependents and result.success:
            await self.handle_workflow_completed(
                workflow_id=workflow_id,
                success=result.success,
                run_id=result.run_id,
                trace_id=result.trace.trace_id if result.trace else None,
            )

        return result

    async def start(self, poll_interval: float = 60.0) -> None:
        """Start the scheduler loop.

        Args:
            poll_interval: Seconds between schedule checks.
        """
        if self._running:
            logger.warning("scheduler_already_running")
            return

        self._running = True
        logger.info("scheduler_started", poll_interval=poll_interval)

        while self._running:
            await self._check_schedules()
            await asyncio.sleep(poll_interval)

    def stop(self) -> None:
        """Stop the scheduler loop."""
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("scheduler_stopped")

    async def _check_schedules(self) -> None:
        """Check for due schedules and run them."""
        now = utc_now()

        for job in self._jobs.values():
            if not job.enabled:
                continue

            if job.trigger_type != TriggerType.CRON:
                continue

            if job.next_run and now >= job.next_run:
                try:
                    await self._run_job(job)
                except Exception as e:
                    logger.error(
                        "scheduled_run_failed",
                        workflow_id=job.workflow_id,
                        error=str(e),
                    )

                # Calculate next run
                if job.schedule:
                    job.next_run = self._calculate_next_cron(job.schedule)

    async def _run_job(self, job: ScheduledJob) -> None:
        """Run a scheduled job."""
        logger.info("running_scheduled_job", workflow_id=job.workflow_id)

        job.last_run = utc_now()
        job.run_count += 1

        result = await self._runner.run(workflow_id=job.workflow_id)

        # Trigger dependent workflows if the job succeeded
        if result.success:
            await self.handle_workflow_completed(
                workflow_id=job.workflow_id,
                success=result.success,
                run_id=result.run_id,
                trace_id=result.trace.trace_id if result.trace else None,
            )

    def _calculate_next_cron(self, cron_expr: str) -> datetime:
        """Calculate next run time from cron expression.

        Args:
            cron_expr: Cron expression (e.g., "0 9 * * 1-5").

        Returns:
            Next run datetime.
        """
        try:
            cron = CronExpression(cron_expr)
            return cron.next_run()
        except ValueError as e:
            logger.warning(
                "invalid_cron_expression",
                expression=cron_expr,
                error=str(e),
            )
            # Fallback to 1 hour from now
            return utc_now() + timedelta(hours=1)

    def list_jobs(self) -> list[ScheduledJob]:
        """List all scheduled jobs."""
        return list(self._jobs.values())

    def get_job(self, workflow_id: str) -> ScheduledJob | None:
        """Get a job by workflow ID."""
        return self._jobs.get(workflow_id)

    def enable_job(self, workflow_id: str) -> bool:
        """Enable a job."""
        job = self._jobs.get(workflow_id)
        if job:
            job.enabled = True
            return True
        return False

    def disable_job(self, workflow_id: str) -> bool:
        """Disable a job."""
        job = self._jobs.get(workflow_id)
        if job:
            job.enabled = False
            return True
        return False

    async def handle_workflow_completed(
        self,
        workflow_id: str,
        success: bool = True,
        run_id: str | None = None,
        trace_id: str | None = None,
    ) -> list[str]:
        """Handle a workflow completion event.

        Triggers any workflows that are waiting for this workflow to complete.
        This includes both:
        1. Workflows with trigger.type == WORKFLOW and trigger.source_workflow_id
        2. Workflows listed in the completed workflow's on_complete field

        Args:
            workflow_id: The ID of the workflow that completed.
            success: Whether the workflow completed successfully.
            run_id: The run ID of the completed workflow.
            trace_id: The trace ID of the completed workflow.

        Returns:
            List of workflow IDs that were triggered.
        """
        triggered: list[str] = []

        # 1. Trigger workflows registered with workflow trigger type
        workflow_triggers = self._workflow_triggers.get(workflow_id, [])
        for trigger_config in workflow_triggers:
            if trigger_config.on_success_only and not success:
                logger.debug(
                    "skipping_workflow_trigger_due_to_failure",
                    target_workflow_id=trigger_config.target_workflow_id,
                    source_workflow_id=workflow_id,
                )
                continue

            job = self._jobs.get(trigger_config.target_workflow_id)
            if job and job.enabled:
                logger.info(
                    "triggering_workflow_from_completion",
                    target_workflow_id=trigger_config.target_workflow_id,
                    source_workflow_id=workflow_id,
                    trigger_type="workflow_trigger",
                )
                try:
                    await self._runner.run(
                        workflow_id=trigger_config.target_workflow_id,
                        intent=f"Triggered by completion of {workflow_id}",
                    )
                    triggered.append(trigger_config.target_workflow_id)
                    job.last_run = utc_now()
                    job.run_count += 1
                except Exception as e:
                    logger.error(
                        "workflow_trigger_failed",
                        target_workflow_id=trigger_config.target_workflow_id,
                        source_workflow_id=workflow_id,
                        error=str(e),
                    )

        # 2. Trigger workflows from on_complete chain
        on_complete_workflows = self._on_complete_chains.get(workflow_id, [])
        for target_id in on_complete_workflows:
            # Skip if already triggered via workflow trigger
            if target_id in triggered:
                continue

            job = self._jobs.get(target_id)
            if job and job.enabled:
                logger.info(
                    "triggering_workflow_from_on_complete",
                    target_workflow_id=target_id,
                    source_workflow_id=workflow_id,
                    trigger_type="on_complete_chain",
                )
                try:
                    await self._runner.run(
                        workflow_id=target_id,
                        intent=f"Chained from {workflow_id} via on_complete",
                    )
                    triggered.append(target_id)
                    if job:
                        job.last_run = utc_now()
                        job.run_count += 1
                except Exception as e:
                    logger.error(
                        "on_complete_trigger_failed",
                        target_workflow_id=target_id,
                        source_workflow_id=workflow_id,
                        error=str(e),
                    )

        if triggered:
            logger.info(
                "workflow_completion_triggered_workflows",
                source_workflow_id=workflow_id,
                triggered_count=len(triggered),
                triggered_workflows=triggered,
            )

        return triggered

    def get_workflow_triggers(self, workflow_id: str) -> list[str]:
        """Get list of workflows triggered by a workflow's completion.

        Args:
            workflow_id: The source workflow ID.

        Returns:
            List of workflow IDs that will be triggered.
        """
        triggers = self._workflow_triggers.get(workflow_id, [])
        on_complete = self._on_complete_chains.get(workflow_id, [])

        result = [t.target_workflow_id for t in triggers]
        result.extend([w for w in on_complete if w not in result])
        return result

    def get_triggered_by(self, workflow_id: str) -> list[str]:
        """Get list of workflows that trigger this workflow.

        Args:
            workflow_id: The target workflow ID.

        Returns:
            List of source workflow IDs.
        """
        sources: list[str] = []

        # Check workflow triggers
        for source_id, triggers in self._workflow_triggers.items():
            for trigger in triggers:
                if trigger.target_workflow_id == workflow_id:
                    sources.append(source_id)

        # Check on_complete chains
        for source_id, chain in self._on_complete_chains.items():
            if workflow_id in chain and source_id not in sources:
                sources.append(source_id)

        return sources
