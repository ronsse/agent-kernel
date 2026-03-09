"""FastAPI REST Server for Agent Kernel.

Provides HTTP endpoints for:
- Workflow execution
- Trace inspection
- Approval management
- Capability listing
- Knowledge graph queries
- Trace ingestion from external agents
- Context assembly for prompt enrichment
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone
from typing import Any

from agent_kernel._import_utils import require_extra

require_extra("fastapi", "api", "API server")

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from agent_kernel.context_graph.ingestion import ContextGraphIngestion
from agent_kernel.context_graph.query import (
    ContextGraphQuery,
    ContextGraphQueryService,
)
from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.plan import Plan
from agent_kernel.core.schemas.trace import (
    DecisionTrace,
    Outcome,
    OutcomeStatus,
    ToolCallRecord,
)

logger = structlog.get_logger(__name__)

# Project root: src/agent_kernel/api/server.py → up 4 levels to project root
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent


# =============================================================================
# Request/Response Models
# =============================================================================


class WorkflowRunRequest(BaseModel):
    """Request to run a workflow."""

    workflow_id: str
    intent: str | None = None
    project_id: str | None = None


class WorkflowRunResponse(BaseModel):
    """Response from workflow run."""

    trace_id: str | None = None
    success: bool
    message: str
    artifacts: list[str] = Field(default_factory=list)
    status: str | None = None
    run_id: str | None = None
    pending_approvals: list[dict] | None = None


class ApprovalRequest(BaseModel):
    """Request to approve/deny an action."""

    approval_id: str
    approved: bool
    reason: str | None = None
    approved_by: str = "api_user"


class ApprovalResponse(BaseModel):
    """Response from approval action."""

    success: bool
    action_id: str
    approved: bool


class PendingApprovalItem(BaseModel):
    """A pending approval item."""

    approval_id: str
    action_id: str
    capability_name: str
    agent_profile_id: str
    requested_at: str
    expires_at: str | None


class CapabilityItem(BaseModel):
    """A capability definition."""

    name: str
    description: str
    adapter_type: str
    requires_approval: bool


class TraceItem(BaseModel):
    """A trace summary."""

    trace_id: str
    agent_profile_id: str
    outcome_status: str
    created_at: str
    tool_call_count: int
    estimated_cost_usd: float | None = None  # sum of llm_calls[*].response.usage.estimated_cost_usd


class TraceSummary(BaseModel):
    """Response with trace summary."""

    traces: list[TraceItem]
    total_count: int


# =============================================================================
# Bridge Request/Response Models (External Agent ↔ Kernel)
# =============================================================================


class KnowledgeSearchRequest(BaseModel):
    """Request to search the knowledge graph."""

    query: str
    node_types: list[str] | None = None
    tags: list[str] | None = None
    include_trajectories: bool = True
    limit: int = 20


class KnowledgeSearchResultItem(BaseModel):
    """A single search result from the knowledge graph."""

    node_id: str
    node_type: str
    title: str
    description: str
    relevance_score: float
    freshness_score: float
    confidence: float


class KnowledgeSearchResponse(BaseModel):
    """Response from knowledge search."""

    results: list[KnowledgeSearchResultItem]
    total_candidates: int
    query_time_ms: int


class KnowledgeAddRequest(BaseModel):
    """Request to add a knowledge node."""

    node_type: str
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    source: str = "external"
    edges: list[dict[str, str]] = Field(default_factory=list)


class KnowledgeAddResponse(BaseModel):
    """Response from adding a knowledge node."""

    node_id: str
    success: bool


class EntityHistoryItem(BaseModel):
    """A trajectory that touched an entity."""

    node_id: str
    intent: str
    outcome_status: str
    relevance_score: float
    created_at: str


class EntityHistoryResponse(BaseModel):
    """Response from entity history query."""

    entity_node_id: str
    trajectories: list[EntityHistoryItem]


class TraceIngestRequest(BaseModel):
    """Lightweight trace from an external agent."""

    agent_id: str
    intent: str
    actions: list[dict[str, Any]] = Field(default_factory=list)
    outcome: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None


class TraceIngestResponse(BaseModel):
    """Response from trace ingestion."""

    trace_id: str
    trajectory_node_id: str | None
    success: bool


class ContextAssembleRequest(BaseModel):
    """Request to assemble context for prompt enrichment."""

    intent: str
    agent_id: str
    max_tokens: int = 2000


class ContextEnrichmentItem(BaseModel):
    """A single item in the enrichment response."""

    type: str
    title: str
    excerpt: str
    relevance_score: float
    source: str


class ContextAssembleResponse(BaseModel):
    """Response with assembled context for prompt injection."""

    packet_id: str
    items: list[ContextEnrichmentItem]
    enrichment_text: str
    token_estimate: int


# Agent profile ID mapping (external agent ID → kernel profile ID)
DEFAULT_AGENT_PROFILE_MAP: dict[str, str] = {}


# =============================================================================
# API Factory
# =============================================================================


def create_app(
    workflow_runner: Any | None = None,
    trace_store: Any | None = None,
    approval_gate: Any | None = None,
    capability_registry: Any | None = None,
    context_graph_query: ContextGraphQueryService | None = None,
    context_graph_ingestion: ContextGraphIngestion | None = None,
    context_assembler: Any | None = None,
    event_log: Any | None = None,
    agent_profile_map: dict[str, str] | None = None,
    workflow_store: Any | None = None,
    lifespan: Any | None = None,
) -> FastAPI:
    """Create FastAPI application.

    Args:
        workflow_runner: WorkflowRunner instance.
        trace_store: TraceStore instance.
        approval_gate: ApprovalGate instance (deprecated, use workflow_store).
        capability_registry: CapabilityRegistry instance.
        context_graph_query: ContextGraphQueryService for knowledge queries.
        context_graph_ingestion: ContextGraphIngestion for trace decomposition.
        context_assembler: ContextAssembler for prompt enrichment.
        event_log: EventLog for audit trail.
        agent_profile_map: External agent ID → kernel profile ID mapping.
        workflow_store: WorkflowRunStore for persistent approval CRUD.
        lifespan: Optional FastAPI lifespan context manager for startup/shutdown hooks.

    Returns:
        Configured FastAPI app.
    """
    if approval_gate is not None and workflow_store is None:
        logger.warning(
            "approval_gate_deprecated",
            msg="approval_gate is deprecated; pass workflow_store instead",
        )

    fastapi_kwargs: dict[str, Any] = {
        "title": "Agent Kernel API",
        "description": "REST API for Agent Kernel operations",
        "version": "0.2.0",
    }
    if lifespan is not None:
        fastapi_kwargs["lifespan"] = lifespan

    app = FastAPI(**fastapi_kwargs)

    # Store dependencies
    app.state.workflow_runner = workflow_runner
    app.state.trace_store = trace_store
    app.state.approval_gate = approval_gate
    app.state.capability_registry = capability_registry
    app.state.context_graph_query = context_graph_query
    app.state.context_graph_ingestion = context_graph_ingestion
    app.state.context_assembler = context_assembler
    app.state.event_log = event_log
    app.state.agent_profile_map = agent_profile_map or DEFAULT_AGENT_PROFILE_MAP
    app.state.workflow_store = workflow_store

    # Mount static files (dashboard CSS, etc.)
    static_dir = _PROJECT_ROOT / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Configure Jinja2 templates
    templates_dir = _PROJECT_ROOT / "templates"
    if templates_dir.exists():
        templates = Jinja2Templates(directory=str(templates_dir))
        _register_template_filters(templates)
        app.state.templates = templates
    else:
        app.state.templates = None

    # Register routes
    _register_routes(app)

    return app


def _register_template_filters(templates: Jinja2Templates) -> None:
    """Register custom Jinja2 filters on the template environment."""

    def timeago(value: Any) -> str:
        """Convert a datetime or ISO string to a relative time string."""
        if value is None:
            return "never"
        if isinstance(value, str):
            try:
                # Handle ISO format with/without timezone
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return str(value)
        if not isinstance(value, datetime):
            return str(value)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        diff = now - value
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"

    def summarize(value: str | None, length: int = 200) -> str:
        """Truncate a string to `length` chars with ellipsis."""
        if not value:
            return ""
        if len(value) <= length:
            return value
        return value[:length] + "…"

    def cost_fmt(value: float | None) -> str:
        """Format a float as a cost string, e.g. '$0.0042'."""
        if value is None or value == 0:
            return "—"
        return f"${value:.4f}"

    def run_status(value: Any) -> str:
        """Extract string status from WorkflowRun or status enum."""
        if hasattr(value, "value"):
            return value.value
        return str(value)

    def run_duration(run: Any) -> str:
        """Compute human-readable duration from a WorkflowRun object."""
        started = getattr(run, "started_at", None)
        ended = getattr(run, "ended_at", None)
        if started is None or ended is None:
            return "—"
        try:
            diff = ended - started
            total_sec = int(diff.total_seconds())
            if total_sec < 0:
                return "—"
            if total_sec < 60:
                return f"{total_sec}s"
            minutes = total_sec // 60
            seconds = total_sec % 60
            return f"{minutes}m {seconds}s"
        except Exception:
            return "—"

    templates.env.filters["timeago"] = timeago
    templates.env.filters["summarize"] = summarize
    templates.env.filters["cost_fmt"] = cost_fmt
    templates.env.filters["run_status"] = run_status
    templates.env.filters["duration"] = run_duration


def _sum_trace_cost(t: Any) -> float | None:
    """Sum estimated_cost_usd across all llm_calls for a trace.

    Returns the total as a float if at least one llm_call has cost data,
    otherwise returns None to distinguish "no data" from "zero cost".

    Args:
        t: A DecisionTrace-like object with a ``llm_calls`` attribute.

    Returns:
        Total estimated cost in USD, or None if no cost data found.
    """
    total = 0.0
    found = False
    for llm_call in getattr(t, "llm_calls", None) or []:
        resp = getattr(llm_call, "response", None)
        usage = getattr(resp, "usage", None) if resp else None
        cost = getattr(usage, "estimated_cost_usd", None) if usage else None
        if cost is not None:
            total += float(cost)
            found = True
    return total if found else None



def _register_routes(app: FastAPI) -> None:
    """Register all routes."""

    # =========================================================================
    # Health & Status
    # =========================================================================

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {"status": "healthy", "service": "agent-kernel"}

    @app.get("/status")
    async def status():
        """Service status endpoint."""
        return {
            "status": "running",
            "components": {
                "workflow_runner": app.state.workflow_runner is not None,
                "trace_store": app.state.trace_store is not None,
                "approval_gate": app.state.approval_gate is not None,
                "workflow_store": app.state.workflow_store is not None,
                "capability_registry": app.state.capability_registry is not None,
                "context_graph_query": app.state.context_graph_query is not None,
                "context_graph_ingestion": (
                    app.state.context_graph_ingestion is not None
                ),
                "context_assembler": app.state.context_assembler is not None,
            },
        }

    # =========================================================================
    # Workflow Endpoints
    # =========================================================================

    @app.post("/workflows/{workflow_id}/run", response_model=WorkflowRunResponse)
    async def run_workflow(workflow_id: str, request: WorkflowRunRequest | None = None):
        """Run a workflow."""
        if app.state.workflow_runner is None:
            raise HTTPException(
                status_code=503,
                detail="Workflow runner not configured",
            )

        intent = request.intent if request else None
        project_id = request.project_id if request else None

        try:
            result = await app.state.workflow_runner.run(
                workflow_id=workflow_id,
                intent=intent,
                project_id=project_id,
            )

            # Extract trace_id from the result's trace object
            trace_id = result.trace.trace_id if result.trace else None
            # Build message from result state
            if result.needs_approval:
                message = "Workflow paused — waiting for approval"
            elif result.success:
                message = "Workflow completed successfully"
            elif result.error:
                message = f"Workflow failed: {result.error}"
            else:
                message = f"Workflow finished with status: {result.status.value}"

            # Build approval info if waiting
            pending_approvals = None
            if result.needs_approval and app.state.workflow_store:
                from agent_kernel.core.schemas.workflow import (
                    ApprovalRequestStatus as WfApprovalStatus,
                )

                pending = app.state.workflow_store.get_pending_approvals(
                    result.run_id,
                )
                if pending:
                    pending_approvals = [
                        {
                            "approval_id": p.approval_id,
                            "capability_name": p.capability_name,
                            "action_preview": p.action_preview,
                        }
                        for p in pending
                    ]

            return WorkflowRunResponse(
                trace_id=trace_id,
                success=result.success,
                message=message,
                status=result.status.value,
                run_id=result.run_id,
                pending_approvals=pending_approvals,
            )

        except Exception as e:
            logger.error("workflow_run_error", workflow_id=workflow_id, error=str(e))
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/workflows/runs")
    async def list_workflow_runs(
        limit: int = 20,
        workflow_id: str | None = None,
    ):
        """List recent workflow runs.

        Args:
            limit: Maximum number of runs to return (default 20).
            workflow_id: Filter by workflow ID (optional).

        Returns:
            JSON object with ``runs`` list.
        """
        if app.state.workflow_store is None:
            return {"runs": []}

        try:
            runs = app.state.workflow_store.list_runs(
                limit=limit,
                workflow_id=workflow_id,
            )
            return {
                "runs": [
                    {
                        "run_id": r.run_id,
                        "workflow_id": r.workflow_id,
                        "status": r.status.value,
                        "started_at": (
                            r.started_at.isoformat() if r.started_at else None
                        ),
                        "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                        "intent": r.intent,
                    }
                    for r in runs
                ]
            }
        except Exception as e:
            logger.error("list_workflow_runs_error", error=str(e))
            return {"runs": []}

    @app.get("/workflows")
    async def list_workflows():
        """List available workflows."""
        if app.state.workflow_runner is None:
            return {"workflows": []}

        try:
            specs = app.state.workflow_runner.list_workflows()
            return {
                "workflows": [
                    {
                        "workflow_id": s.workflow_id,
                        "name": s.name,
                        "description": s.description,
                        "trigger_type": s.trigger.type.value,
                    }
                    for s in specs
                ]
            }
        except Exception:
            return {"workflows": []}

    # =========================================================================
    # Trace Endpoints
    # =========================================================================

    @app.get("/traces", response_model=TraceSummary)
    async def list_traces(limit: int = 20, agent_profile_id: str | None = None):
        """List recent traces."""
        if app.state.trace_store is None:
            return TraceSummary(traces=[], total_count=0)

        try:
            traces = app.state.trace_store.list(
                limit=limit,
                agent_profile_id=agent_profile_id,
            )

            items = [
                TraceItem(
                    trace_id=t.trace_id,
                    agent_profile_id=t.agent_profile_id,
                    outcome_status=t.outcome.status.value if t.outcome else "unknown",
                    created_at=t.created_at.isoformat() if t.created_at else "",
                    tool_call_count=len(t.tool_calls),
                    estimated_cost_usd=_sum_trace_cost(t),
                )
                for t in traces
            ]

            total = app.state.trace_store.count(agent_profile_id=agent_profile_id)

            return TraceSummary(traces=items, total_count=total)

        except Exception as e:
            logger.error("list_traces_error", error=str(e))
            return TraceSummary(traces=[], total_count=0)

    @app.get("/traces/{trace_id}")
    async def get_trace(trace_id: str):
        """Get a specific trace."""
        if app.state.trace_store is None:
            raise HTTPException(
                status_code=503,
                detail="Trace store not configured",
            )

        trace = app.state.trace_store.get(trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="Trace not found")

        return trace.model_dump()

    # =========================================================================
    # Approval Endpoints
    # =========================================================================

    @app.get("/approvals/pending")
    async def list_pending_approvals(agent_profile_id: str | None = None):
        """List pending approvals from persistent store."""
        store = app.state.workflow_store
        if store is not None:
            from agent_kernel.core.schemas.workflow import (
                ApprovalRequestStatus as WfApprovalStatus,
            )

            pending = store.list_approval_requests(
                status=WfApprovalStatus.PENDING,
            )
            return {
                "pending": [
                    {
                        "approval_id": p.approval_id,
                        "action_id": p.action_id,
                        "capability_name": p.capability_name,
                        "run_id": p.run_id,
                        "workflow_id": p.workflow_id,
                        "requested_at": p.requested_at.isoformat(),
                        "expires_at": (
                            p.expires_at.isoformat() if p.expires_at else None
                        ),
                        "action_preview": p.action_preview,
                    }
                    for p in pending
                ]
            }

        # Legacy fallback: in-memory ApprovalGate
        if app.state.approval_gate is None:
            return {"pending": []}

        try:
            pending_legacy = app.state.approval_gate.list_pending(
                agent_profile_id=agent_profile_id
            )
            return {
                "pending": [
                    PendingApprovalItem(
                        approval_id=p.approval_id,
                        action_id=p.action_id,
                        capability_name=p.capability_name,
                        agent_profile_id=p.agent_profile_id,
                        requested_at=p.requested_at.isoformat(),
                        expires_at=(
                            p.expires_at.isoformat() if p.expires_at else None
                        ),
                    ).model_dump()
                    for p in pending_legacy
                ]
            }
        except Exception as e:
            logger.error("list_approvals_error", error=str(e))
            return {"pending": []}

    @app.post("/approvals/respond", response_model=ApprovalResponse)
    async def respond_to_approval(request: ApprovalRequest):
        """Approve or deny a pending action using persistent store."""
        store = app.state.workflow_store
        if store is not None:
            from agent_kernel.core.schemas.base import utc_now
            from agent_kernel.core.schemas.workflow import (
                ApprovalRequestStatus as WfApprovalStatus,
            )

            approval = store.get_approval_request(request.approval_id)
            if approval is None:
                raise HTTPException(
                    status_code=404, detail="Approval not found"
                )
            if approval.status != WfApprovalStatus.PENDING:
                raise HTTPException(
                    status_code=409,
                    detail=f"Approval already {approval.status.value}",
                )

            approval.status = (
                WfApprovalStatus.APPROVED
                if request.approved
                else WfApprovalStatus.DENIED
            )
            approval.resolver = request.approved_by
            approval.resolved_at = utc_now()
            approval.reason = request.reason
            store.update_approval_request(approval)

            # Trigger workflow resume if approved
            runner = app.state.workflow_runner
            if request.approved and runner is not None:
                try:
                    tokens = {approval.action_id: approval.approval_id}
                    await runner.resume(
                        approval.run_id, approval_tokens=tokens
                    )
                except Exception as e:
                    logger.error(
                        "workflow_resume_failed",
                        error=str(e),
                        run_id=approval.run_id,
                    )

            return ApprovalResponse(
                success=True,
                action_id=approval.action_id,
                approved=request.approved,
            )

        # Legacy fallback: in-memory ApprovalGate
        if app.state.approval_gate is None:
            raise HTTPException(
                status_code=503,
                detail="No approval store configured",
            )

        try:
            if request.approved:
                record = app.state.approval_gate.approve(
                    approval_id=request.approval_id,
                    approved_by=request.approved_by,
                    reason=request.reason,
                )
            else:
                record = app.state.approval_gate.deny(
                    approval_id=request.approval_id,
                    denied_by=request.approved_by,
                    reason=request.reason,
                )

            if record is None:
                raise HTTPException(
                    status_code=404,
                    detail="Approval not found or already processed",
                )

            return ApprovalResponse(
                success=True,
                action_id=record.action_id,
                approved=record.approved,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error("approval_error", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))

    # =========================================================================
    # Capability Endpoints
    # =========================================================================

    @app.get("/capabilities")
    async def list_capabilities():
        """List available capabilities."""
        if app.state.capability_registry is None:
            return {"capabilities": []}

        try:
            caps = app.state.capability_registry.list()
            return {
                "capabilities": [
                    CapabilityItem(
                        name=c.capability_name,
                        description=c.description,
                        adapter_type=c.adapter_type,
                        requires_approval=c.requires_approval_default,
                    ).model_dump()
                    for c in caps
                ]
            }

        except Exception as e:
            logger.error("list_capabilities_error", error=str(e))
            return {"capabilities": []}

    @app.get("/capabilities/{capability_name}")
    async def get_capability(capability_name: str):
        """Get a specific capability."""
        if app.state.capability_registry is None:
            raise HTTPException(
                status_code=503,
                detail="Capability registry not configured",
            )

        cap = app.state.capability_registry.get(capability_name)
        if cap is None:
            raise HTTPException(status_code=404, detail="Capability not found")

        return cap.model_dump()

    # =========================================================================
    # Knowledge Graph Endpoints (External Agent Bridge)
    # =========================================================================

    @app.post("/knowledge/search", response_model=KnowledgeSearchResponse)
    async def search_knowledge(request: KnowledgeSearchRequest):
        """Search the knowledge graph for relevant nodes.

        Used by external agents via kernel.knowledge.search tool.
        """
        if app.state.context_graph_query is None:
            raise HTTPException(
                status_code=503,
                detail="Context graph query not configured",
            )

        try:
            keywords = request.query.lower().split()

            # Build query with optional trajectory inclusion
            node_types = request.node_types
            if request.include_trajectories and node_types is None:
                node_types = None  # Query all types (default includes knowledge)

            q = ContextGraphQuery(
                keywords=keywords,
                node_types=node_types,
                tags=request.tags,
                limit=request.limit,
            )

            result = await app.state.context_graph_query.query(q)

            # Also search trajectories if requested and no explicit type filter
            trajectory_results = []
            if request.include_trajectories and request.node_types is None:
                trajectory_results = (
                    await app.state.context_graph_query.find_similar_trajectories(
                        intent=request.query,
                        limit=min(5, request.limit // 4),
                    )
                )

            items = []
            for node in result.nodes:
                items.append(KnowledgeSearchResultItem(
                    node_id=node.node_id,
                    node_type=node.node_type,
                    title=node.properties.get("title", ""),
                    description=node.properties.get("description", "")[:300],
                    relevance_score=node.relevance_score,
                    freshness_score=node.freshness_score,
                    confidence=node.confidence,
                ))

            for traj in trajectory_results:
                items.append(KnowledgeSearchResultItem(
                    node_id=traj.node_id,
                    node_type=traj.node_type,
                    title=traj.properties.get("intent", ""),
                    description=traj.properties.get("outcome_summary", "")[:300],
                    relevance_score=traj.relevance_score,
                    freshness_score=traj.freshness_score,
                    confidence=traj.confidence,
                ))

            # Sort combined results by relevance
            items.sort(key=lambda x: x.relevance_score, reverse=True)
            items = items[:request.limit]

            return KnowledgeSearchResponse(
                results=items,
                total_candidates=result.total_candidates + len(trajectory_results),
                query_time_ms=result.query_time_ms,
            )

        except Exception as e:
            logger.error("knowledge_search_error", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/knowledge/add", response_model=KnowledgeAddResponse)
    async def add_knowledge(request: KnowledgeAddRequest):
        """Add a knowledge node to the context graph.

        Used by external agents via kernel.knowledge.add tool.
        """
        if app.state.context_graph_ingestion is None:
            raise HTTPException(
                status_code=503,
                detail="Context graph ingestion not configured",
            )

        try:
            properties: dict[str, Any] = {
                "title": request.title,
                "description": request.description,
                "tags": request.tags,
                "confidence": request.confidence,
                "knowledge_source": request.source,
            }

            edges = None
            if request.edges:
                edges = [
                    {
                        "target_id": e.get("target_id", ""),
                        "edge_type": e.get("edge_type", "related_to"),
                        "properties": {},
                    }
                    for e in request.edges
                    if e.get("target_id")
                ]

            node_id = await app.state.context_graph_ingestion.ingest_manual(
                node_type=request.node_type,
                properties=properties,
                edges=edges,
            )

            return KnowledgeAddResponse(node_id=node_id, success=True)

        except Exception as e:
            logger.error("knowledge_add_error", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/knowledge/{node_id}/history",
        response_model=EntityHistoryResponse,
    )
    async def get_entity_history(node_id: str):
        """Get the event clock for an entity — which trajectories touched it.

        Used by external agents via kernel.knowledge.history tool.
        """
        if app.state.context_graph_query is None:
            raise HTTPException(
                status_code=503,
                detail="Context graph query not configured",
            )

        try:
            trajectories = await app.state.context_graph_query.get_entity_history(
                node_id,
            )

            items = [
                EntityHistoryItem(
                    node_id=t.node_id,
                    intent=t.properties.get("intent", ""),
                    outcome_status=t.properties.get("outcome_status", ""),
                    relevance_score=t.relevance_score,
                    created_at=t.properties.get("created_at", ""),
                )
                for t in trajectories
            ]

            return EntityHistoryResponse(
                entity_node_id=node_id,
                trajectories=items,
            )

        except Exception as e:
            logger.error("entity_history_error", node_id=node_id, error=str(e))
            raise HTTPException(status_code=500, detail=str(e))

    # =========================================================================
    # Trace Ingestion Endpoint (External Agent Bridge)
    # =========================================================================

    @app.post("/traces/ingest", response_model=TraceIngestResponse)
    async def ingest_trace(request: TraceIngestRequest):
        """Ingest a lightweight trace from an external agent.

        Accepts a simplified trace payload from an external agent and constructs
        a full DecisionTrace for graph decomposition.
        """
        if app.state.context_graph_ingestion is None:
            raise HTTPException(
                status_code=503,
                detail="Context graph ingestion not configured",
            )

        try:
            # Map external agent_id to kernel agent_profile_id
            profile_map: dict[str, str] = app.state.agent_profile_map
            agent_profile_id = profile_map.get(
                request.agent_id, f"{request.agent_id}_agent",
            )

            # Build tool call records from actions
            # Bridge sends: capability_name, input_summary, output_summary
            # Also accept: capability, input, output (SDK format)
            tool_calls = []
            for action in request.actions:
                cap = (
                    action.get("capability_name")
                    or action.get("capability", "unknown@v1")
                )
                inp = action.get("input_summary") or action.get("input", {})
                # Normalise input to dict
                if isinstance(inp, str):
                    inp = {"summary": inp} if inp else {}
                out = action.get("output_summary") or action.get("output")
                if isinstance(out, str):
                    out = {"summary": out} if out else None
                tool_calls.append(ToolCallRecord(
                    capability_name=cap,
                    input=inp,
                    output=out,
                    status=action.get("status", "success"),
                    duration_ms=action.get("duration_ms", 0),
                ))

            # Build outcome
            outcome_status_str = request.outcome.get("status", "completed")
            try:
                outcome_status = OutcomeStatus(outcome_status_str)
            except ValueError:
                outcome_status = OutcomeStatus.COMPLETED

            outcome = Outcome(
                status=outcome_status,
                summary=request.outcome.get("summary"),
            )

            # Build minimal DecisionTrace
            trace_id = generate_ulid()
            trace = DecisionTrace(
                trace_id=trace_id,
                agent_profile_id=agent_profile_id,
                engine_id="external",
                intent=request.intent,
                context_packet_id=f"external:{request.session_id or generate_ulid()}",
                plan=Plan(
                    intent=request.intent,
                    summary=f"External agent action: {request.intent}",
                ),
                tool_calls=tool_calls,
                outcome=outcome,
            )

            # Persist trace to trace store
            if app.state.trace_store is not None:
                app.state.trace_store.write(trace)

            # Decompose into context graph
            result = await app.state.context_graph_ingestion.ingest_trace(trace)

            # Emit audit event
            if app.state.event_log:
                from agent_kernel.memory.event_log import EventType

                app.state.event_log.emit(
                    event_type=EventType.TRACE_COMPLETED,
                    source="api.traces.ingest",
                    entity_id=trace_id,
                    entity_type="trace",
                    payload={
                        "agent_id": request.agent_id,
                        "agent_profile_id": agent_profile_id,
                        "intent": request.intent,
                        "origin": "external",
                    },
                )

            logger.info(
                "external_trace_ingested",
                trace_id=trace_id,
                agent_id=request.agent_id,
                trajectory_id=result.trajectory_node_id,
            )

            return TraceIngestResponse(
                trace_id=trace_id,
                trajectory_node_id=result.trajectory_node_id,
                success=True,
            )

        except Exception as e:
            logger.error("trace_ingest_error", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))

    # =========================================================================
    # Context Assembly Endpoint (External Agent Bridge)
    # =========================================================================

    @app.post("/context/assemble", response_model=ContextAssembleResponse)
    async def assemble_context(request: ContextAssembleRequest):
        """Assemble enrichment context for an external agent's prompt.

        Returns pre-formatted markdown and structured items that
        an external plugin can inject into the system prompt.
        """
        cg_query: ContextGraphQueryService | None = app.state.context_graph_query
        if cg_query is None:
            raise HTTPException(
                status_code=503,
                detail="Context graph query not configured",
            )

        try:
            # Retrieve relevant knowledge
            knowledge_nodes = await cg_query.find_relevant_knowledge(
                intent=request.intent,
                limit=request.max_tokens // 100,  # Rough items estimate
            )

            # Retrieve similar trajectories
            trajectories = await cg_query.find_similar_trajectories(
                intent=request.intent,
                limit=5,
            )

            # Build response items
            items: list[ContextEnrichmentItem] = []

            for node in knowledge_nodes:
                items.append(ContextEnrichmentItem(
                    type="knowledge",
                    title=node.properties.get("title", ""),
                    excerpt=node.properties.get("description", "")[:200],
                    relevance_score=node.relevance_score,
                    source="context_graph",
                ))

            for traj in trajectories:
                items.append(ContextEnrichmentItem(
                    type="trajectory",
                    title=traj.properties.get("intent", ""),
                    excerpt=_format_trajectory_summary(traj.properties),
                    relevance_score=traj.relevance_score,
                    source="context_graph",
                ))

            # Build enrichment markdown
            enrichment_text = _build_enrichment_text(items)
            token_estimate = len(enrichment_text) // 4

            # Trim if over budget
            if token_estimate > request.max_tokens:
                # Remove lowest-relevance items until within budget
                items.sort(key=lambda x: x.relevance_score, reverse=True)
                while token_estimate > request.max_tokens and items:
                    items.pop()
                    enrichment_text = _build_enrichment_text(items)
                    token_estimate = len(enrichment_text) // 4

            return ContextAssembleResponse(
                packet_id=generate_ulid(),
                items=items,
                enrichment_text=enrichment_text,
                token_estimate=token_estimate,
            )

        except Exception as e:
            logger.error("context_assemble_error", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))

    # =========================================================================
    # Dashboard Routes (HTML)
    # =========================================================================

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request):
        """Main dashboard page."""
        if app.state.templates is None:
            return HTMLResponse("Templates not configured", status_code=200)

        pending = _get_pending_approvals(app)
        resolved_recent = _get_recent_resolved(app)
        runs = _get_recent_runs(app)
        stats = _compute_stats(app, pending, runs)

        return app.state.templates.TemplateResponse(
            request=request,
            name="dashboard/dashboard.html",
            context={
                "pending": pending,
                "resolved_recent": resolved_recent,
                "runs": runs,
                "stats": stats,
                "pending_count": len(pending),
            },
        )

    @app.get("/dashboard/partials/approvals", response_class=HTMLResponse)
    async def dashboard_approvals_partial(request: Request):
        """Polled approvals partial — returns HTML fragment with OOB swaps."""
        if app.state.templates is None:
            return HTMLResponse("Templates not configured", status_code=200)

        pending = _get_pending_approvals(app)
        resolved_recent = _get_recent_resolved(app)
        pending_count = len(pending)

        return app.state.templates.TemplateResponse(
            request=request,
            name="dashboard/partials/approvals.html",
            context={
                "pending": pending,
                "resolved_recent": resolved_recent,
                "pending_count": pending_count,
            },
        )

    @app.post("/dashboard/approvals/{approval_id}/approve", response_class=HTMLResponse)
    async def dashboard_approve(request: Request, approval_id: str):
        """Approve a pending action — returns resolved approval card HTML."""
        from agent_kernel.core.schemas.base import utc_now
        from agent_kernel.core.schemas.workflow import (
            ApprovalRequestStatus as WfApprovalStatus,
        )

        store = app.state.workflow_store
        if store is None:
            raise HTTPException(status_code=503, detail="Workflow store not configured")

        approval = store.get_approval_request(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="Approval not found")

        if approval.status != WfApprovalStatus.PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"Approval already {approval.status.value}",
            )

        approval.status = WfApprovalStatus.APPROVED
        approval.resolver = "dashboard"
        approval.resolved_at = utc_now()
        store.update_approval_request(approval)

        # Trigger workflow resume (fire-and-forget)
        runner = app.state.workflow_runner
        if runner is not None:
            try:
                import asyncio
                tokens = {approval.action_id: approval.approval_id}
                asyncio.create_task(runner.resume(approval.run_id, approval_tokens=tokens))
            except Exception as e:
                logger.error(
                    "dashboard_workflow_resume_failed",
                    error=str(e),
                    run_id=approval.run_id,
                )

        if app.state.templates is None:
            return HTMLResponse(f"<p>Approved {approval_id}</p>")

        return app.state.templates.TemplateResponse(
            request=request,
            name="dashboard/partials/approval_resolved.html",
            context={"approval": approval},
        )

    @app.post("/dashboard/approvals/{approval_id}/deny", response_class=HTMLResponse)
    async def dashboard_deny(request: Request, approval_id: str):
        """Deny a pending action — returns resolved approval card HTML."""
        from agent_kernel.core.schemas.base import utc_now
        from agent_kernel.core.schemas.workflow import (
            ApprovalRequestStatus as WfApprovalStatus,
        )

        store = app.state.workflow_store
        if store is None:
            raise HTTPException(status_code=503, detail="Workflow store not configured")

        approval = store.get_approval_request(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="Approval not found")

        if approval.status != WfApprovalStatus.PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"Approval already {approval.status.value}",
            )

        approval.status = WfApprovalStatus.DENIED
        approval.resolver = "dashboard"
        approval.resolved_at = utc_now()
        store.update_approval_request(approval)

        if app.state.templates is None:
            return HTMLResponse(f"<p>Denied {approval_id}</p>")

        return app.state.templates.TemplateResponse(
            request=request,
            name="dashboard/partials/approval_resolved.html",
            context={"approval": approval},
        )

    @app.get("/dashboard/partials/runs", response_class=HTMLResponse)
    async def dashboard_runs_partial(request: Request):
        """Polled runs partial — returns HTML fragment with workflow runs table."""
        if app.state.templates is None:
            return HTMLResponse("Templates not configured", status_code=200)

        runs = _get_recent_runs(app)

        return app.state.templates.TemplateResponse(
            request=request,
            name="dashboard/partials/runs.html",
            context={"runs": runs},
        )

    @app.get("/dashboard/partials/stats", response_class=HTMLResponse)
    async def dashboard_stats_partial(request: Request):
        """Polled stats partial — returns HTML fragment with stats bar."""
        if app.state.templates is None:
            return HTMLResponse("Templates not configured", status_code=200)

        pending = _get_pending_approvals(app)
        runs = _get_recent_runs(app)
        stats = _compute_stats(app, pending, runs)

        return app.state.templates.TemplateResponse(
            request=request,
            name="dashboard/partials/stats_bar.html",
            context={"stats": stats},
        )

    @app.get("/dashboard/partials/trace/{trace_id}", response_class=HTMLResponse)
    async def dashboard_trace_detail(request: Request, trace_id: str):
        """On-demand trace detail partial — returns HTML fragment for trace drill-down."""
        import json

        if app.state.templates is None:
            return HTMLResponse("Templates not configured", status_code=200)

        trace_store = app.state.trace_store
        if trace_store is None:
            return HTMLResponse("<p>Trace store not configured</p>", status_code=200)

        trace = trace_store.get(trace_id)
        if trace is None:
            return HTMLResponse(
                f"<p>Trace <code>{trace_id}</code> not found</p>",
                status_code=200,
            )

        # Compute total LLM cost
        total_cost: float | None = None
        llm_calls = getattr(trace, "llm_calls", None) or []
        for call in llm_calls:
            response = getattr(call, "response", None)
            if response:
                usage = getattr(response, "usage", None)
                if usage:
                    cost = getattr(usage, "estimated_cost_usd", None)
                    if cost is not None:
                        if total_cost is None:
                            total_cost = 0.0
                        total_cost += cost

        # Build raw JSON for the toggle
        try:
            trace_dict = trace.model_dump(mode="json")
            trace_json = json.dumps(trace_dict, indent=2, default=str)
        except Exception:
            trace_json = "{}"

        return app.state.templates.TemplateResponse(
            request=request,
            name="dashboard/partials/trace_detail.html",
            context={
                "trace": trace,
                "total_cost": total_cost,
                "trace_json": trace_json,
            },
        )


# =============================================================================
# Dashboard Helpers
# =============================================================================


def _get_pending_approvals(app: FastAPI) -> list[Any]:
    """Fetch pending approvals from workflow store."""
    store = app.state.workflow_store
    if store is None:
        return []
    try:
        from agent_kernel.core.schemas.workflow import (
            ApprovalRequestStatus as WfApprovalStatus,
        )
        return store.list_approval_requests(status=WfApprovalStatus.PENDING)
    except Exception as e:
        logger.error("dashboard_get_pending_approvals_error", error=str(e))
        return []


def _get_recent_resolved(app: FastAPI) -> list[Any]:
    """Fetch recently resolved approvals (approved + denied), most recent first."""
    store = app.state.workflow_store
    if store is None:
        return []
    try:
        from agent_kernel.core.schemas.workflow import (
            ApprovalRequestStatus as WfApprovalStatus,
        )
        approved = store.list_approval_requests(status=WfApprovalStatus.APPROVED)
        denied = store.list_approval_requests(status=WfApprovalStatus.DENIED)
        combined = approved + denied
        # Sort by resolved_at descending, None last
        combined.sort(
            key=lambda a: a.resolved_at if a.resolved_at else a.requested_at,
            reverse=True,
        )
        return combined[:10]
    except Exception as e:
        logger.error("dashboard_get_recent_resolved_error", error=str(e))
        return []


def _get_recent_runs(app: FastAPI) -> list[Any]:
    """Fetch recent workflow runs."""
    store = app.state.workflow_store
    if store is None:
        return []
    try:
        return store.list_runs(limit=20)
    except Exception as e:
        logger.error("dashboard_get_recent_runs_error", error=str(e))
        return []


def _compute_stats(app: FastAPI, pending: list[Any], runs: list[Any]) -> dict[str, Any]:
    """Compute dashboard stats dict."""
    from agent_kernel.core.schemas.workflow import WorkflowRunStatus

    pending_count = len(pending)

    # Runs today and success rate
    runs_today = 0
    completed = 0
    failed = 0
    today_runs: list[Any] = []

    from datetime import datetime, timezone
    today = datetime.now(tz=timezone.utc).date()

    for run in runs:
        started = run.started_at
        if started is not None:
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if started.date() == today:
                today_runs.append(run)

    runs_today = len(today_runs)
    for run in today_runs:
        if run.status == WorkflowRunStatus.COMPLETED:
            completed += 1
        elif run.status == WorkflowRunStatus.FAILED:
            failed += 1

    total_finished = completed + failed
    success_rate = (completed / total_finished) if total_finished > 0 else None

    # Cost from trace store (best-effort)
    cost_today: float | None = None
    trace_store = app.state.trace_store
    if trace_store is not None:
        try:
            traces = trace_store.list(limit=100)
            cost_today = 0.0
            for t in traces:
                if hasattr(t, "llm_calls") and t.llm_calls:
                    for llm_call in t.llm_calls:
                        if hasattr(llm_call, "response") and llm_call.response:
                            usage = getattr(llm_call.response, "usage", None)
                            if usage and hasattr(usage, "estimated_cost_usd"):
                                cost = usage.estimated_cost_usd
                                if cost:
                                    cost_today += cost
        except Exception:
            cost_today = None

    return {
        "pending_count": pending_count,
        "runs_today": runs_today,
        "success_rate": success_rate,
        "cost_today": cost_today,
    }


# =============================================================================
# Helpers
# =============================================================================


def _format_trajectory_summary(props: dict[str, Any]) -> str:
    """Format trajectory properties into a brief summary."""
    intent = props.get("intent", "")
    outcome = props.get("outcome_summary", "")
    status = props.get("outcome_status", "")
    caps = props.get("capabilities_used", [])

    parts = [f"Past: {intent}"]
    if outcome:
        parts.append(f"Outcome ({status}): {outcome}")
    if caps:
        parts.append(f"Used: {', '.join(caps[:3])}")
    return " | ".join(parts)


def _build_enrichment_text(items: list[ContextEnrichmentItem]) -> str:
    """Build pre-formatted markdown for prompt injection."""
    if not items:
        return ""

    knowledge_items = [i for i in items if i.type == "knowledge"]
    trajectory_items = [i for i in items if i.type == "trajectory"]

    sections = []

    if knowledge_items:
        lines = ["## Relevant Knowledge"]
        for item in knowledge_items:
            lines.append(f"- **{item.title}**: {item.excerpt}")
        sections.append("\n".join(lines))

    if trajectory_items:
        lines = ["## Similar Past Actions"]
        for item in trajectory_items:
            lines.append(f"- {item.excerpt}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


# =============================================================================
# App Instance
# =============================================================================

# Default app instance (for development/testing)
app = create_app()
