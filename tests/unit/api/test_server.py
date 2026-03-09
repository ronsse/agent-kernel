"""Tests for FastAPI REST Server."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from agent_kernel.api.server import create_app


@pytest.fixture
def mock_workflow_runner():
    """Create mock workflow runner."""
    runner = MagicMock()
    mock_trace = MagicMock()
    mock_trace.trace_id = "trace_123"
    mock_status = MagicMock()
    mock_status.value = "completed"
    runner.run = AsyncMock(return_value=MagicMock(
        trace=mock_trace,
        success=True,
        error=None,
        needs_approval=False,
        status=mock_status,
        run_id="run_123",
    ))
    runner.list_workflows = MagicMock(return_value=[])
    return runner


@pytest.fixture
def mock_trace_store():
    """Create mock trace store."""
    store = MagicMock()
    store.list = MagicMock(return_value=[])
    store.count = MagicMock(return_value=0)
    store.get = MagicMock(return_value=None)
    return store


@pytest.fixture
def mock_approval_gate():
    """Create mock approval gate."""
    gate = MagicMock()
    gate.list_pending = MagicMock(return_value=[])
    gate.approve = MagicMock(return_value=None)
    gate.deny = MagicMock(return_value=None)
    return gate


@pytest.fixture
def mock_capability_registry():
    """Create mock capability registry."""
    registry = MagicMock()
    registry.list = MagicMock(return_value=[])
    registry.get = MagicMock(return_value=None)
    return registry


@pytest.fixture
def client():
    """Create test client with no dependencies."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def full_client(
    mock_workflow_runner,
    mock_trace_store,
    mock_approval_gate,
    mock_capability_registry,
):
    """Create test client with all dependencies."""
    app = create_app(
        workflow_runner=mock_workflow_runner,
        trace_store=mock_trace_store,
        approval_gate=mock_approval_gate,
        capability_registry=mock_capability_registry,
    )
    return TestClient(app)


class TestHealthEndpoints:
    """Tests for health endpoints."""

    def test_health(self, client):
        """Test health endpoint."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_status(self, client):
        """Test status endpoint."""
        response = client.get("/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"

    def test_status_with_components(self, full_client):
        """Test status with all components configured."""
        response = full_client.get("/status")

        data = response.json()
        assert data["components"]["workflow_runner"] is True
        assert data["components"]["trace_store"] is True


class TestWorkflowEndpoints:
    """Tests for workflow endpoints."""

    def test_run_workflow_no_runner(self, client):
        """Test running workflow without runner configured."""
        response = client.post("/workflows/test/run")

        assert response.status_code == 503

    def test_run_workflow(self, full_client, mock_workflow_runner):
        """Test running a workflow."""
        response = full_client.post(
            "/workflows/daily_checkin/run",
            json={"workflow_id": "daily_checkin"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "trace_id" in data
        mock_workflow_runner.run.assert_called_once()

    def test_list_workflows_empty(self, full_client):
        """Test listing workflows when empty."""
        response = full_client.get("/workflows")

        assert response.status_code == 200
        data = response.json()
        assert data["workflows"] == []


class TestTraceEndpoints:
    """Tests for trace endpoints."""

    def test_list_traces_empty(self, full_client):
        """Test listing traces when empty."""
        response = full_client.get("/traces")

        assert response.status_code == 200
        data = response.json()
        assert data["traces"] == []
        assert data["total_count"] == 0

    def test_get_trace_not_found(self, full_client):
        """Test getting nonexistent trace."""
        response = full_client.get("/traces/nonexistent")

        assert response.status_code == 404

    def test_list_traces_no_store(self, client):
        """Test listing traces without store configured."""
        response = client.get("/traces")

        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 0


class TestApprovalEndpoints:
    """Tests for approval endpoints."""

    def test_list_pending_empty(self, full_client):
        """Test listing pending when empty."""
        response = full_client.get("/approvals/pending")

        assert response.status_code == 200
        data = response.json()
        assert data["pending"] == []

    def test_approve_not_found(self, full_client, mock_approval_gate):
        """Test approving nonexistent approval."""
        mock_approval_gate.approve.return_value = None

        response = full_client.post(
            "/approvals/respond",
            json={
                "approval_id": "nonexistent",
                "approved": True,
            },
        )

        assert response.status_code == 404

    def test_approve_success(self, full_client, mock_approval_gate):
        """Test successful approval."""
        mock_approval_gate.approve.return_value = MagicMock(
            action_id="action_123",
            approved=True,
        )

        response = full_client.post(
            "/approvals/respond",
            json={
                "approval_id": "approval_123",
                "approved": True,
                "reason": "Looks good",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["approved"] is True

    def test_deny_success(self, full_client, mock_approval_gate):
        """Test successful denial."""
        mock_approval_gate.deny.return_value = MagicMock(
            action_id="action_123",
            approved=False,
        )

        response = full_client.post(
            "/approvals/respond",
            json={
                "approval_id": "approval_123",
                "approved": False,
                "reason": "Not allowed",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["approved"] is False


class TestCapabilityEndpoints:
    """Tests for capability endpoints."""

    def test_list_capabilities_empty(self, full_client):
        """Test listing capabilities when empty."""
        response = full_client.get("/capabilities")

        assert response.status_code == 200
        data = response.json()
        assert data["capabilities"] == []

    def test_get_capability_not_found(self, full_client):
        """Test getting nonexistent capability."""
        response = full_client.get("/capabilities/unknown")

        assert response.status_code == 404

    def test_list_capabilities_no_registry(self, client):
        """Test listing without registry configured."""
        response = client.get("/capabilities")

        assert response.status_code == 200
        data = response.json()
        assert data["capabilities"] == []
