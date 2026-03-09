"""Tests for Scheduler and CronExpression."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_kernel.scheduler.scheduler import (
    CronExpression,
    CronField,
    ScheduledJob,
    Scheduler,
    WorkflowTriggerConfig,
)
from agent_kernel.workflows.spec import TriggerType, WorkflowSpec, WorkflowTrigger


class TestCronField:
    """Tests for CronField parsing."""

    def test_parse_asterisk(self):
        """Test parsing asterisk (all values)."""
        field = CronField.parse("*", 0, 59)
        assert 0 in field.values
        assert 59 in field.values
        assert len(field.values) == 60

    def test_parse_single_value(self):
        """Test parsing single value."""
        field = CronField.parse("5", 0, 59)
        assert field.values == {5}

    def test_parse_range(self):
        """Test parsing range."""
        field = CronField.parse("1-5", 0, 59)
        assert field.values == {1, 2, 3, 4, 5}

    def test_parse_step(self):
        """Test parsing step values."""
        field = CronField.parse("*/15", 0, 59)
        assert field.values == {0, 15, 30, 45}

    def test_parse_step_with_range(self):
        """Test parsing step with range."""
        field = CronField.parse("0-30/10", 0, 59)
        assert field.values == {0, 10, 20, 30}

    def test_parse_list(self):
        """Test parsing comma-separated list."""
        field = CronField.parse("1,5,10,15", 0, 59)
        assert field.values == {1, 5, 10, 15}

    def test_matches(self):
        """Test value matching."""
        field = CronField.parse("0,30", 0, 59)
        assert field.matches(0) is True
        assert field.matches(30) is True
        assert field.matches(15) is False

    def test_next_value(self):
        """Test getting next matching value."""
        field = CronField.parse("0,15,30,45", 0, 59)
        assert field.next_value(10) == 15
        assert field.next_value(0) == 0
        assert field.next_value(50) == 0  # Wraps around


class TestCronExpression:
    """Tests for CronExpression parsing and matching."""

    def test_parse_simple(self):
        """Test parsing simple expression."""
        cron = CronExpression("0 9 * * *")
        assert cron.minute.values == {0}
        assert cron.hour.values == {9}

    def test_parse_invalid(self):
        """Test parsing invalid expression."""
        with pytest.raises(ValueError):
            CronExpression("invalid")

        with pytest.raises(ValueError):
            CronExpression("* * *")  # Too few fields

    def test_matches_exact(self):
        """Test exact time matching."""
        cron = CronExpression("30 9 * * *")  # 9:30 AM daily

        # Should match
        dt = datetime(2026, 1, 15, 9, 30, 0, tzinfo=UTC)
        assert cron.matches(dt) is True

        # Should not match
        dt = datetime(2026, 1, 15, 9, 31, 0, tzinfo=UTC)
        assert cron.matches(dt) is False

    def test_matches_weekday(self):
        """Test day-of-week matching."""
        cron = CronExpression("0 9 * * 1-5")  # 9:00 AM Monday-Friday

        # Monday (2026-01-12)
        dt = datetime(2026, 1, 12, 9, 0, 0, tzinfo=UTC)
        assert cron.matches(dt) is True

        # Saturday (2026-01-17)
        dt = datetime(2026, 1, 17, 9, 0, 0, tzinfo=UTC)
        assert cron.matches(dt) is False

    def test_next_run_simple(self):
        """Test calculating next run time."""
        cron = CronExpression("0 * * * *")  # Every hour at :00

        # If it's 9:30, next run should be 10:00
        after = datetime(2026, 1, 15, 9, 30, 0, tzinfo=UTC)
        next_run = cron.next_run(after)

        assert next_run.hour == 10
        assert next_run.minute == 0

    def test_next_run_every_15_minutes(self):
        """Test next run for every 15 minutes."""
        cron = CronExpression("*/15 * * * *")

        after = datetime(2026, 1, 15, 9, 5, 0, tzinfo=UTC)
        next_run = cron.next_run(after)

        assert next_run.minute == 15


class TestScheduledJob:
    """Tests for ScheduledJob."""

    def test_init(self):
        """Test job initialization."""
        job = ScheduledJob(
            workflow_id="daily_checkin",
            trigger_type=TriggerType.CRON,
            schedule="0 9 * * *",
        )

        assert job.workflow_id == "daily_checkin"
        assert job.trigger_type == TriggerType.CRON
        assert job.enabled is True
        assert job.run_count == 0


class TestScheduler:
    """Tests for Scheduler."""

    @pytest.fixture
    def mock_runner(self):
        """Create a mock workflow runner."""
        runner = MagicMock()
        runner.run = AsyncMock(return_value=MagicMock(success=True))
        return runner

    def test_init(self, mock_runner):
        """Test scheduler initialization."""
        scheduler = Scheduler(mock_runner)
        assert len(scheduler._jobs) == 0

    def test_register_workflow(self, mock_runner):
        """Test registering a workflow."""
        scheduler = Scheduler(mock_runner)
        spec = WorkflowSpec(
            workflow_id="test_workflow",
            name="Test Workflow",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.CRON, schedule="0 9 * * *"),
            steps=[],
        )

        job = scheduler.register_workflow(spec)

        assert job.workflow_id == "test_workflow"
        assert job.trigger_type == TriggerType.CRON
        assert "test_workflow" in scheduler._jobs

    def test_register_manual_workflow(self, mock_runner):
        """Test registering a manual workflow."""
        scheduler = Scheduler(mock_runner)
        spec = WorkflowSpec(
            workflow_id="manual_workflow",
            name="Manual Workflow",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
        )

        job = scheduler.register_workflow(spec)

        assert job.trigger_type == TriggerType.MANUAL
        assert job.next_run is None

    @pytest.mark.asyncio
    async def test_trigger_manual(self, mock_runner):
        """Test manually triggering a workflow."""
        scheduler = Scheduler(mock_runner)
        spec = WorkflowSpec(
            workflow_id="test_workflow",
            name="Test",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
        )
        scheduler.register_workflow(spec)

        await scheduler.trigger_manual("test_workflow")

        mock_runner.run.assert_called_once()
        job = scheduler.get_job("test_workflow")
        assert job.run_count == 1

    def test_list_jobs(self, mock_runner):
        """Test listing jobs."""
        scheduler = Scheduler(mock_runner)

        for i in range(3):
            spec = WorkflowSpec(
                workflow_id=f"workflow_{i}",
                name=f"Workflow {i}",
                agent_profile_id="test_agent",
                trigger=WorkflowTrigger(type=TriggerType.MANUAL),
                steps=[],
            )
            scheduler.register_workflow(spec)

        jobs = scheduler.list_jobs()
        assert len(jobs) == 3

    def test_enable_disable_job(self, mock_runner):
        """Test enabling/disabling jobs."""
        scheduler = Scheduler(mock_runner)
        spec = WorkflowSpec(
            workflow_id="test",
            name="Test",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
        )
        scheduler.register_workflow(spec)

        # Disable
        scheduler.disable_job("test")
        job = scheduler.get_job("test")
        assert job.enabled is False

        # Enable
        scheduler.enable_job("test")
        assert job.enabled is True

    def test_calculate_next_cron(self, mock_runner):
        """Test cron calculation."""
        scheduler = Scheduler(mock_runner)

        # Valid expression
        next_run = scheduler._calculate_next_cron("0 9 * * *")
        assert next_run is not None
        assert next_run > datetime.now(UTC)

    def test_calculate_next_cron_invalid(self, mock_runner):
        """Test invalid cron expression handling."""
        scheduler = Scheduler(mock_runner)

        # Invalid expression should fall back gracefully
        next_run = scheduler._calculate_next_cron("invalid")
        assert next_run is not None

    def test_register_workflow_trigger(self, mock_runner):
        """Test registering a workflow with workflow trigger type."""
        scheduler = Scheduler(mock_runner)

        # Register source workflow
        source_spec = WorkflowSpec(
            workflow_id="source_workflow",
            name="Source Workflow",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
        )
        scheduler.register_workflow(source_spec)

        # Register target workflow with workflow trigger
        target_spec = WorkflowSpec(
            workflow_id="target_workflow",
            name="Target Workflow",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(
                type=TriggerType.WORKFLOW,
                source_workflow_id="source_workflow",
            ),
            steps=[],
        )
        job = scheduler.register_workflow(target_spec)

        assert job.trigger_type == TriggerType.WORKFLOW
        assert job.source_workflow_id == "source_workflow"
        assert "source_workflow" in scheduler._workflow_triggers
        assert len(scheduler._workflow_triggers["source_workflow"]) == 1
        assert (
            scheduler._workflow_triggers["source_workflow"][0].target_workflow_id
            == "target_workflow"
        )

    def test_register_on_complete_chain(self, mock_runner):
        """Test registering a workflow with on_complete chain."""
        scheduler = Scheduler(mock_runner)

        # Register target workflows first
        for i in range(2):
            target_spec = WorkflowSpec(
                workflow_id=f"target_{i}",
                name=f"Target {i}",
                agent_profile_id="test_agent",
                trigger=WorkflowTrigger(type=TriggerType.MANUAL),
                steps=[],
            )
            scheduler.register_workflow(target_spec)

        # Register source with on_complete chain
        source_spec = WorkflowSpec(
            workflow_id="source_workflow",
            name="Source Workflow",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
            on_complete=["target_0", "target_1"],
        )
        scheduler.register_workflow(source_spec)

        assert "source_workflow" in scheduler._on_complete_chains
        assert scheduler._on_complete_chains["source_workflow"] == [
            "target_0",
            "target_1",
        ]

    @pytest.mark.asyncio
    async def test_handle_workflow_completed_triggers_dependents(self, mock_runner):
        """Test that workflow completion triggers dependent workflows."""
        mock_runner.run = AsyncMock(
            return_value=MagicMock(
                success=True,
                run_id="run_123",
                trace=MagicMock(trace_id="trace_123"),
            )
        )
        scheduler = Scheduler(mock_runner)

        # Register source workflow
        source_spec = WorkflowSpec(
            workflow_id="source_workflow",
            name="Source Workflow",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
        )
        scheduler.register_workflow(source_spec)

        # Register target workflow with workflow trigger
        target_spec = WorkflowSpec(
            workflow_id="target_workflow",
            name="Target Workflow",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(
                type=TriggerType.WORKFLOW,
                source_workflow_id="source_workflow",
            ),
            steps=[],
        )
        scheduler.register_workflow(target_spec)

        # Trigger workflow completion
        triggered = await scheduler.handle_workflow_completed(
            workflow_id="source_workflow",
            success=True,
            run_id="source_run_123",
        )

        assert "target_workflow" in triggered
        mock_runner.run.assert_called_once_with(
            workflow_id="target_workflow",
            intent="Triggered by completion of source_workflow",
        )

    @pytest.mark.asyncio
    async def test_handle_workflow_completed_on_complete_chain(self, mock_runner):
        """Test that workflow completion triggers on_complete chain."""
        mock_runner.run = AsyncMock(
            return_value=MagicMock(
                success=True,
                run_id="run_123",
                trace=MagicMock(trace_id="trace_123"),
            )
        )
        scheduler = Scheduler(mock_runner)

        # Register target workflow
        target_spec = WorkflowSpec(
            workflow_id="target_workflow",
            name="Target Workflow",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
        )
        scheduler.register_workflow(target_spec)

        # Register source with on_complete chain
        source_spec = WorkflowSpec(
            workflow_id="source_workflow",
            name="Source Workflow",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
            on_complete=["target_workflow"],
        )
        scheduler.register_workflow(source_spec)

        # Trigger workflow completion
        triggered = await scheduler.handle_workflow_completed(
            workflow_id="source_workflow",
            success=True,
        )

        assert "target_workflow" in triggered
        mock_runner.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_workflow_completed_skips_on_failure(self, mock_runner):
        """Test that on_success_only workflows are skipped on failure."""
        mock_runner.run = AsyncMock(return_value=MagicMock(success=True))
        scheduler = Scheduler(mock_runner)

        # Register source workflow
        source_spec = WorkflowSpec(
            workflow_id="source_workflow",
            name="Source Workflow",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
        )
        scheduler.register_workflow(source_spec)

        # Register target with on_success_only=True (default)
        target_spec = WorkflowSpec(
            workflow_id="target_workflow",
            name="Target Workflow",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(
                type=TriggerType.WORKFLOW,
                source_workflow_id="source_workflow",
                on_success_only=True,
            ),
            steps=[],
        )
        scheduler.register_workflow(target_spec)

        # Trigger with failure
        triggered = await scheduler.handle_workflow_completed(
            workflow_id="source_workflow",
            success=False,
        )

        assert triggered == []
        mock_runner.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_workflow_completed_triggers_on_failure_if_configured(
        self, mock_runner
    ):
        """Test that workflows with on_success_only=False trigger on failure."""
        mock_runner.run = AsyncMock(return_value=MagicMock(success=True))
        scheduler = Scheduler(mock_runner)

        # Register source workflow
        source_spec = WorkflowSpec(
            workflow_id="source_workflow",
            name="Source Workflow",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
        )
        scheduler.register_workflow(source_spec)

        # Register target with on_success_only=False
        target_spec = WorkflowSpec(
            workflow_id="target_workflow",
            name="Target Workflow",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(
                type=TriggerType.WORKFLOW,
                source_workflow_id="source_workflow",
                on_success_only=False,
            ),
            steps=[],
        )
        scheduler.register_workflow(target_spec)

        # Trigger with failure - should still trigger
        triggered = await scheduler.handle_workflow_completed(
            workflow_id="source_workflow",
            success=False,
        )

        assert "target_workflow" in triggered
        mock_runner.run.assert_called_once()

    def test_get_workflow_triggers(self, mock_runner):
        """Test getting list of workflows triggered by completion."""
        scheduler = Scheduler(mock_runner)

        # Register source workflow
        source_spec = WorkflowSpec(
            workflow_id="source_workflow",
            name="Source",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
            on_complete=["chain_target"],
        )
        scheduler.register_workflow(source_spec)

        # Register workflow trigger target
        trigger_spec = WorkflowSpec(
            workflow_id="trigger_target",
            name="Trigger Target",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(
                type=TriggerType.WORKFLOW,
                source_workflow_id="source_workflow",
            ),
            steps=[],
        )
        scheduler.register_workflow(trigger_spec)

        # Also register chain_target
        chain_spec = WorkflowSpec(
            workflow_id="chain_target",
            name="Chain Target",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
        )
        scheduler.register_workflow(chain_spec)

        triggers = scheduler.get_workflow_triggers("source_workflow")
        assert "trigger_target" in triggers
        assert "chain_target" in triggers
        assert len(triggers) == 2

    def test_get_triggered_by(self, mock_runner):
        """Test getting list of workflows that trigger this workflow."""
        scheduler = Scheduler(mock_runner)

        # Register source workflows
        for i in range(2):
            spec = WorkflowSpec(
                workflow_id=f"source_{i}",
                name=f"Source {i}",
                agent_profile_id="test_agent",
                trigger=WorkflowTrigger(type=TriggerType.MANUAL),
                steps=[],
                on_complete=["target_workflow"] if i == 0 else [],
            )
            scheduler.register_workflow(spec)

        # Register target with workflow trigger from source_1
        target_spec = WorkflowSpec(
            workflow_id="target_workflow",
            name="Target",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(
                type=TriggerType.WORKFLOW,
                source_workflow_id="source_1",
            ),
            steps=[],
        )
        scheduler.register_workflow(target_spec)

        sources = scheduler.get_triggered_by("target_workflow")
        assert "source_0" in sources  # From on_complete chain
        assert "source_1" in sources  # From workflow trigger
        assert len(sources) == 2

    @pytest.mark.asyncio
    async def test_trigger_manual_cascades_to_dependents(self, mock_runner):
        """Test that trigger_manual cascades to dependent workflows."""
        call_count = 0

        async def track_runs(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return MagicMock(
                success=True,
                run_id=f"run_{call_count}",
                trace=MagicMock(trace_id=f"trace_{call_count}"),
            )

        mock_runner.run = AsyncMock(side_effect=track_runs)
        scheduler = Scheduler(mock_runner)

        # Register target first
        target_spec = WorkflowSpec(
            workflow_id="target_workflow",
            name="Target",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
        )
        scheduler.register_workflow(target_spec)

        # Register source with on_complete
        source_spec = WorkflowSpec(
            workflow_id="source_workflow",
            name="Source",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
            on_complete=["target_workflow"],
        )
        scheduler.register_workflow(source_spec)

        # Trigger source workflow
        await scheduler.trigger_manual("source_workflow")

        # Should have run both source and target
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_trigger_manual_no_cascade_when_disabled(self, mock_runner):
        """Test that trigger_manual doesn't cascade when trigger_dependents=False."""
        mock_runner.run = AsyncMock(
            return_value=MagicMock(
                success=True,
                run_id="run_123",
                trace=MagicMock(trace_id="trace_123"),
            )
        )
        scheduler = Scheduler(mock_runner)

        # Register target first
        target_spec = WorkflowSpec(
            workflow_id="target_workflow",
            name="Target",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
        )
        scheduler.register_workflow(target_spec)

        # Register source with on_complete
        source_spec = WorkflowSpec(
            workflow_id="source_workflow",
            name="Source",
            agent_profile_id="test_agent",
            trigger=WorkflowTrigger(type=TriggerType.MANUAL),
            steps=[],
            on_complete=["target_workflow"],
        )
        scheduler.register_workflow(source_spec)

        # Trigger with cascade disabled
        await scheduler.trigger_manual("source_workflow", trigger_dependents=False)

        # Should have only run source
        assert mock_runner.run.call_count == 1
