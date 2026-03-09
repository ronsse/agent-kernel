"""Tests verifying feedback loop components are wired into CLI workflow commands.

These tests mock constructors and verify that _run_workflow_async passes the
correct feedback loop components (experience store, adaptive timeout, cost
anomaly detector, experience miner) to ToolBroker, ContextAssembler, and
WorkflowRunner.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_MOD = "agent_kernel.cli.main"


@pytest.fixture
def patched_cli(tmp_path: Path):
    """Patch all heavy constructors in _run_workflow_async.

    Yields a dict of mock constructors so tests can inspect call_args.
    """
    # Simple mocks for all stores
    event_log = MagicMock()
    trace_sink = MagicMock()
    jsonl_sink = MagicMock()
    multi_trace = MagicMock()
    doc_store = MagicMock()
    vec_store = MagicMock()
    graph_store = MagicMock()
    exp_store = MagicMock()
    wf_store = MagicMock()
    timeout_mgr = MagicMock()

    # Runner needs awaitable run()
    runner = MagicMock()
    runner.register_engine = MagicMock()
    runner.run = AsyncMock(
        return_value=MagicMock(success=True, run_id="r1", trace=None),
    )

    # Settings
    settings = MagicMock()
    settings.configs_dir = tmp_path / "configs"
    settings.skills_dir = tmp_path / "skills"
    settings.tool_broker_retry_enabled = False
    settings.tool_broker_circuit_breaker_enabled = True
    settings.default_llm_provider = "openai"
    settings.openai_api_key = "k"
    settings.anthropic_api_key = None
    settings.openai_model = "gpt-4o"
    settings.anthropic_model = None
    settings.openai_base_url = None

    # Mock constructors to inspect call_args
    mock_broker_cls = MagicMock(return_value=MagicMock())
    mock_assembler_cls = MagicMock(return_value=MagicMock())
    mock_runner_cls = MagicMock(return_value=runner)
    mock_cost_cls = MagicMock(return_value=MagicMock())
    mock_miner_cls = MagicMock(return_value=MagicMock())
    mock_timeout_cls = MagicMock(return_value=timeout_mgr)
    mock_exp_store_cls = MagicMock(return_value=exp_store)
    mock_multi_cls = MagicMock(return_value=multi_trace)

    targets = {
        f"{_MOD}.get_settings": MagicMock(return_value=settings),
        f"{_MOD}.get_data_dir": MagicMock(
            return_value=tmp_path / "data",
        ),
        f"{_MOD}.SQLiteEventLog": MagicMock(return_value=event_log),
        f"{_MOD}.SQLiteTraceSink": MagicMock(return_value=trace_sink),
        f"{_MOD}.JSONLTraceSink": MagicMock(return_value=jsonl_sink),
        f"{_MOD}.MultiSinkTraceStore": mock_multi_cls,
        f"{_MOD}.SQLiteDocumentStore": MagicMock(
            return_value=doc_store,
        ),
        f"{_MOD}.create_vector_store": MagicMock(
            return_value=vec_store,
        ),
        f"{_MOD}.SQLiteGraphStore": MagicMock(
            return_value=graph_store,
        ),
        f"{_MOD}.SQLiteExperienceStore": mock_exp_store_cls,
        f"{_MOD}.AdaptiveTimeoutManager": mock_timeout_cls,
        f"{_MOD}.ToolBroker": mock_broker_cls,
        f"{_MOD}.register_builtin_tools": MagicMock(),
        f"{_MOD}._configure_library_tools": MagicMock(),
        f"{_MOD}._configure_skill_scripts": MagicMock(),
        f"{_MOD}._configure_mcp_adapter": AsyncMock(),
        f"{_MOD}.ContextAssembler": mock_assembler_cls,
        f"{_MOD}.DeterministicExecutor": MagicMock(
            return_value=MagicMock(),
        ),
        f"{_MOD}.CostAnomalyDetector": mock_cost_cls,
        f"{_MOD}.ExperienceMiner": mock_miner_cls,
        f"{_MOD}.SQLiteWorkflowRunStore": MagicMock(
            return_value=wf_store,
        ),
        f"{_MOD}.WorkflowRunner": mock_runner_cls,
        f"{_MOD}.CapabilityRegistry": MagicMock(
            return_value=MagicMock(),
        ),
        f"{_MOD}.CustomEngine": MagicMock(return_value=MagicMock()),
        "agent_kernel.services.llm.create_llm_service": MagicMock(
            return_value=MagicMock(),
        ),
    }

    for target, mock_obj in targets.items():
        patch(target, mock_obj).start()

    yield {
        "broker_cls": mock_broker_cls,
        "assembler_cls": mock_assembler_cls,
        "runner_cls": mock_runner_cls,
        "cost_cls": mock_cost_cls,
        "miner_cls": mock_miner_cls,
        "timeout_cls": mock_timeout_cls,
        "exp_store_cls": mock_exp_store_cls,
        "multi_cls": mock_multi_cls,
        "timeout_mgr": timeout_mgr,
        "exp_store": exp_store,
        "multi_trace": multi_trace,
    }

    patch.stopall()


async def _invoke() -> None:
    """Call _run_workflow_async with default args."""
    from agent_kernel.cli.main import _run_workflow_async  # noqa: PLC0415

    await _run_workflow_async(
        workflow_id="test_wf",
        intent=None,
        project_id=None,
        auto_approve_capabilities=[],
        auto_approve_risk=None,
        interactive=False,
        dry_run=False,
    )


class TestRunWorkflowAsyncWiring:
    """Verify _run_workflow_async wires feedback components."""

    @pytest.mark.asyncio
    async def test_adaptive_timeout_wired_to_broker(
        self, patched_cli: dict
    ) -> None:
        """ToolBroker receives AdaptiveTimeoutManager."""
        await _invoke()
        cls = patched_cli["broker_cls"]
        cls.assert_called_once()
        kw = cls.call_args.kwargs
        assert "timeout_manager" in kw
        assert kw["timeout_manager"] is patched_cli["timeout_mgr"]

    @pytest.mark.asyncio
    async def test_experience_store_wired_to_assembler(
        self, patched_cli: dict
    ) -> None:
        """ContextAssembler receives the experience store."""
        await _invoke()
        cls = patched_cli["assembler_cls"]
        cls.assert_called_once()
        kw = cls.call_args.kwargs
        assert "experience_store" in kw
        assert kw["experience_store"] is patched_cli["exp_store"]

    @pytest.mark.asyncio
    async def test_trace_store_wired_to_runner(
        self, patched_cli: dict
    ) -> None:
        """WorkflowRunner receives trace_store."""
        await _invoke()
        cls = patched_cli["runner_cls"]
        cls.assert_called_once()
        kw = cls.call_args.kwargs
        assert "trace_store" in kw
        assert kw["trace_store"] is patched_cli["multi_trace"]

    @pytest.mark.asyncio
    async def test_cost_anomaly_detector_wired_to_runner(
        self, patched_cli: dict
    ) -> None:
        """WorkflowRunner receives CostAnomalyDetector."""
        await _invoke()
        cls = patched_cli["runner_cls"]
        cls.assert_called_once()
        kw = cls.call_args.kwargs
        assert "cost_anomaly_detector" in kw
        cost = patched_cli["cost_cls"].return_value
        assert kw["cost_anomaly_detector"] is cost

    @pytest.mark.asyncio
    async def test_experience_miner_wired_to_runner(
        self, patched_cli: dict
    ) -> None:
        """WorkflowRunner receives ExperienceMiner."""
        await _invoke()
        cls = patched_cli["runner_cls"]
        cls.assert_called_once()
        kw = cls.call_args.kwargs
        assert "experience_miner" in kw
        miner = patched_cli["miner_cls"].return_value
        assert kw["experience_miner"] is miner
