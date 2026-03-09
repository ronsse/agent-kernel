"""Tests for system health checker."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from agent_kernel.services.health import (
    ComponentStatus,
    HealthChecker,
)

# --- Fixtures ---


@pytest.fixture
def mock_document_store() -> MagicMock:
    store = MagicMock()
    store.count.return_value = 42
    return store


@pytest.fixture
def mock_vector_store() -> MagicMock:
    store = MagicMock()
    store.count.return_value = 128
    return store


@pytest.fixture
def mock_graph_store() -> MagicMock:
    store = MagicMock()
    store.count_nodes.return_value = 15
    store.count_edges.return_value = 30
    return store


@pytest.fixture
def mock_event_log() -> MagicMock:
    log = MagicMock()
    log.count.return_value = 200
    return log


@pytest.fixture
def mock_trace_store() -> MagicMock:
    store = MagicMock()
    store.list_traces.return_value = [{"trace_id": "t1"}]
    return store


@pytest.fixture
def mock_workflow_store() -> MagicMock:
    store = MagicMock()
    store.list_runs.return_value = [{"run_id": "r1"}]
    return store


@pytest.fixture
def mock_experience_store() -> MagicMock:
    store = MagicMock()
    store.list_cases.return_value = [{"case_id": "c1"}]
    return store


@pytest.fixture
def mock_llm_service() -> MagicMock:
    service = MagicMock()
    service._default_model = "gpt-4o"
    service._provider = "openai"
    return service


@pytest.fixture
def all_healthy_checker(
    mock_document_store: MagicMock,
    mock_vector_store: MagicMock,
    mock_graph_store: MagicMock,
    mock_event_log: MagicMock,
    mock_trace_store: MagicMock,
    mock_workflow_store: MagicMock,
    mock_experience_store: MagicMock,
    mock_llm_service: MagicMock,
) -> HealthChecker:
    return HealthChecker(
        document_store=mock_document_store,
        vector_store=mock_vector_store,
        graph_store=mock_graph_store,
        event_log=mock_event_log,
        trace_store=mock_trace_store,
        workflow_store=mock_workflow_store,
        experience_store=mock_experience_store,
        llm_service=mock_llm_service,
    )


# --- Tests ---


class TestAllHealthy:
    def test_overall_status_is_healthy(
        self, all_healthy_checker: HealthChecker
    ) -> None:
        result = all_healthy_checker.check_all()
        assert result.status == ComponentStatus.HEALTHY

    def test_all_components_healthy(
        self, all_healthy_checker: HealthChecker
    ) -> None:
        result = all_healthy_checker.check_all()
        for component in result.components:
            assert component.status == ComponentStatus.HEALTHY

    def test_counts_are_correct(
        self, all_healthy_checker: HealthChecker
    ) -> None:
        result = all_healthy_checker.check_all()
        assert result.healthy_count == 8
        assert result.total_count == 8

    def test_checked_at_is_set(
        self, all_healthy_checker: HealthChecker
    ) -> None:
        result = all_healthy_checker.check_all()
        assert isinstance(result.checked_at, datetime)


class TestUnconfiguredComponents:
    def test_all_unconfigured(self) -> None:
        checker = HealthChecker()
        result = checker.check_all()
        assert result.status == ComponentStatus.HEALTHY
        assert result.healthy_count == 0
        assert result.total_count == 0

    def test_all_components_are_unconfigured(self) -> None:
        checker = HealthChecker()
        result = checker.check_all()
        for component in result.components:
            assert component.status == ComponentStatus.UNCONFIGURED

    def test_component_count_is_eight(self) -> None:
        checker = HealthChecker()
        result = checker.check_all()
        assert len(result.components) == 8


class TestUnhealthyComponent:
    def test_single_unhealthy_makes_overall_unhealthy(
        self, mock_document_store: MagicMock
    ) -> None:
        mock_document_store.count.side_effect = RuntimeError("db locked")
        checker = HealthChecker(document_store=mock_document_store)
        result = checker.check_all()
        assert result.status == ComponentStatus.UNHEALTHY

    def test_unhealthy_component_has_error_message(
        self, mock_document_store: MagicMock
    ) -> None:
        mock_document_store.count.side_effect = RuntimeError("db locked")
        checker = HealthChecker(document_store=mock_document_store)
        result = checker.check_all()
        doc = next(
            c for c in result.components if c.name == "document_store"
        )
        assert doc.status == ComponentStatus.UNHEALTHY
        assert "db locked" in doc.message

    def test_unhealthy_among_healthy(
        self,
        mock_document_store: MagicMock,
        mock_vector_store: MagicMock,
        mock_event_log: MagicMock,
    ) -> None:
        mock_vector_store.count.side_effect = OSError("disk full")
        checker = HealthChecker(
            document_store=mock_document_store,
            vector_store=mock_vector_store,
            event_log=mock_event_log,
        )
        result = checker.check_all()
        assert result.status == ComponentStatus.UNHEALTHY
        assert result.healthy_count == 2
        assert result.total_count == 3


class TestMixedHealth:
    def test_healthy_plus_unconfigured_is_healthy(
        self,
        mock_document_store: MagicMock,
        mock_event_log: MagicMock,
    ) -> None:
        checker = HealthChecker(
            document_store=mock_document_store,
            event_log=mock_event_log,
        )
        result = checker.check_all()
        assert result.status == ComponentStatus.HEALTHY
        assert result.healthy_count == 2
        assert result.total_count == 2

    def test_unconfigured_does_not_count(
        self,
        mock_document_store: MagicMock,
    ) -> None:
        checker = HealthChecker(document_store=mock_document_store)
        result = checker.check_all()
        assert result.total_count == 1
        unconfigured = [
            c for c in result.components
            if c.status == ComponentStatus.UNCONFIGURED
        ]
        assert len(unconfigured) == 7


class TestLatency:
    def test_latency_is_populated(
        self, all_healthy_checker: HealthChecker
    ) -> None:
        result = all_healthy_checker.check_all()
        for component in result.components:
            assert component.latency_ms is not None
            assert component.latency_ms >= 0.0

    def test_unhealthy_has_latency(
        self, mock_document_store: MagicMock
    ) -> None:
        mock_document_store.count.side_effect = RuntimeError("fail")
        checker = HealthChecker(document_store=mock_document_store)
        result = checker.check_all()
        doc = next(
            c for c in result.components if c.name == "document_store"
        )
        assert doc.latency_ms is not None
        assert doc.latency_ms >= 0.0


class TestComponentDetails:
    def test_document_store_details(
        self, mock_document_store: MagicMock
    ) -> None:
        checker = HealthChecker(document_store=mock_document_store)
        result = checker.check_all()
        doc = next(
            c for c in result.components if c.name == "document_store"
        )
        assert doc.details["count"] == 42
        assert "42 documents" in doc.message

    def test_vector_store_details(
        self, mock_vector_store: MagicMock
    ) -> None:
        checker = HealthChecker(vector_store=mock_vector_store)
        result = checker.check_all()
        vec = next(
            c for c in result.components if c.name == "vector_store"
        )
        assert vec.details["count"] == 128

    def test_graph_store_details(
        self, mock_graph_store: MagicMock
    ) -> None:
        checker = HealthChecker(graph_store=mock_graph_store)
        result = checker.check_all()
        graph = next(
            c for c in result.components if c.name == "graph_store"
        )
        assert graph.details["node_count"] == 15
        assert graph.details["edge_count"] == 30

    def test_event_log_details(
        self, mock_event_log: MagicMock
    ) -> None:
        checker = HealthChecker(event_log=mock_event_log)
        result = checker.check_all()
        evt = next(
            c for c in result.components if c.name == "event_log"
        )
        assert evt.details["count"] == 200

    def test_llm_service_details(
        self, mock_llm_service: MagicMock
    ) -> None:
        checker = HealthChecker(llm_service=mock_llm_service)
        result = checker.check_all()
        llm = next(
            c for c in result.components if c.name == "llm_service"
        )
        assert llm.details["model"] == "gpt-4o"
        assert llm.details["provider"] == "openai"
        assert "gpt-4o" in llm.message


class TestExceptionHandling:
    def test_exception_caught_per_component(self) -> None:
        bad_store = MagicMock()
        bad_store.count.side_effect = ConnectionError("refused")
        good_store = MagicMock()
        good_store.count.return_value = 10

        checker = HealthChecker(
            document_store=bad_store,
            vector_store=good_store,
        )
        result = checker.check_all()
        doc = next(
            c for c in result.components if c.name == "document_store"
        )
        vec = next(
            c for c in result.components if c.name == "vector_store"
        )
        assert doc.status == ComponentStatus.UNHEALTHY
        assert vec.status == ComponentStatus.HEALTHY

    def test_multiple_failures(self) -> None:
        bad_doc = MagicMock()
        bad_doc.count.side_effect = RuntimeError("doc fail")
        bad_vec = MagicMock()
        bad_vec.count.side_effect = RuntimeError("vec fail")

        checker = HealthChecker(
            document_store=bad_doc,
            vector_store=bad_vec,
        )
        result = checker.check_all()
        assert result.status == ComponentStatus.UNHEALTHY
        assert result.healthy_count == 0
        assert result.total_count == 2
