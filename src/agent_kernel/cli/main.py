"""CLI main module - Typer-based command-line interface."""

from __future__ import annotations

import asyncio
from enum import Enum
from pathlib import Path
import os
from typing import Optional

import structlog
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.traceback import install as _rich_traceback_install


class OutputFormat(str, Enum):
    """Output format for CLI commands."""

    TEXT = "text"
    JSON = "json"

from agent_kernel.context.assembler import ContextAssembler
from agent_kernel.core.config import Settings, get_settings
from agent_kernel.engine.cost_anomaly import CostAnomalyDetector
from agent_kernel.engine.custom_engine import CustomEngine
from agent_kernel.executor.executor import DeterministicExecutor
from agent_kernel.memory.document_store import SQLiteDocumentStore
from agent_kernel.memory.event_log import SQLiteEventLog
from agent_kernel.memory.experience_store import SQLiteExperienceStore
from agent_kernel.memory.graph_store import SQLiteGraphStore
from agent_kernel.memory.vector_store import SQLiteVectorStore, create_vector_store
from agent_kernel.services.experience_miner import ExperienceMiner
from agent_kernel.skills import SkillPolicy, register_skill_scripts
from agent_kernel.tools.adaptive_timeout import AdaptiveTimeoutManager
from agent_kernel.tools.adapters.mcp import MCPToolAdapter
from agent_kernel.tools.broker import ToolBroker
from agent_kernel.tools.builtin.register import register_builtin_tools
from agent_kernel.tools.library import configure_library_tools
from agent_kernel.tools.mcp.server_manager import MCPServerManager
from agent_kernel.tools.registry import CapabilityRegistry
from agent_kernel.tools.retry import RetryConfig
from agent_kernel.tracing.sinks.jsonl_sink import JSONLTraceSink
from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink
from agent_kernel.tracing.trace_store import MultiSinkTraceStore
from agent_kernel.workflows.runner import WorkflowResult, WorkflowRunner
from agent_kernel.workflows.store import SQLiteWorkflowRunStore

logger = structlog.get_logger(__name__)

app = typer.Typer(
    name="agent-kernel",
    help="Agent Kernel - Framework-agnostic agent foundation",
    add_completion=False,
)
console = Console()

# Security: never print exception locals (can include OAuth tokens / secrets)
try:
    _rich_traceback_install(show_locals=False, max_frames=50)
except Exception:
    pass


def get_data_dir() -> Path:
    """Get the data directory, creating if needed."""
    settings = get_settings()
    data_dir = settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _build_approval_gate(
    settings: "Settings",  # noqa: F821
    event_log: "EventLog | None" = None,  # noqa: F821
) -> "ApprovalGate":  # noqa: F821
    """Build an ApprovalGate with notification callback from settings."""
    from agent_kernel.executor.approval import ApprovalGate

    callback = None
    channel = settings.approval_notify_channel.strip().lower()

    if channel == "log":
        from agent_kernel.executor.notifiers import log_only_approval_notifier

        callback = log_only_approval_notifier()
        logger.info("approval_notification_channel", channel="log")

    return ApprovalGate(
        event_log=event_log,
        on_approval_requested=callback,
    )


async def _configure_mcp_adapter(broker: ToolBroker, configs_dir: Path) -> None:
    """Configure MCP adapter if MCP configs exist."""
    manager = MCPServerManager(configs_dir)
    adapter = MCPToolAdapter()
    configured = await manager.configure_adapter(adapter)
    if configured:
        broker.add_adapter(adapter)
        logger.info("mcp_adapter_configured")


def _configure_library_tools(broker: ToolBroker, configs_dir: Path) -> None:
    """Load local library tool mappings."""
    settings = get_settings()
    if settings.mcp_tools_repo_path and not os.environ.get("MCP_TOOLS_REPO_PATH"):
        os.environ["MCP_TOOLS_REPO_PATH"] = settings.mcp_tools_repo_path
    configure_library_tools(broker, configs_dir)


def _configure_skill_scripts(
    broker: ToolBroker,
    registry: CapabilityRegistry,
    settings: Settings,
) -> None:
    if not settings.skills_enable_scripts:
        return
    allowed_skills = [s.strip() for s in settings.skills_allowed_script_skills.split(",") if s.strip()]
    allowed_origins = [
        s.strip() for s in settings.skills_allowed_script_origins.split(",") if s.strip()
    ]
    extensions = [
        s.strip() for s in settings.skills_script_extensions.split(",") if s.strip()
    ]
    policy = SkillPolicy.from_settings(
        allow_script_execution=settings.skills_enable_scripts,
        allowed_skill_ids=allowed_skills,
        allowed_origins=allowed_origins,
    )
    register_skill_scripts(
        registry=registry,
        broker=broker,
        skills_dir=settings.skills_dir,
        policy=policy,
        extensions=extensions,
        timeout_ms=settings.skills_script_timeout_ms,
    )


@app.command()
def init(
    force: bool = typer.Option(False, "--force", "-f", help="Reinitialize if exists"),
) -> None:
    """Initialize the agent kernel database and directories."""
    data_dir = get_data_dir()

    console.print("[bold blue]Initializing Agent Kernel...[/bold blue]")
    console.print(f"Data directory: {data_dir}")

    # Create subdirectories
    subdirs = ["traces", "documents", "events", "vectors", "graph", "workflows"]
    for subdir in subdirs:
        (data_dir / subdir).mkdir(parents=True, exist_ok=True)
        console.print(f"  Created: {data_dir / subdir}")

    # Initialize databases
    trace_db = data_dir / "traces" / "traces.db"
    if not trace_db.exists() or force:
        trace_store = SQLiteTraceSink(trace_db)
        trace_store.close()
        console.print(f"  Initialized: {trace_db}")

    event_db = data_dir / "events" / "events.db"
    if not event_db.exists() or force:
        event_log = SQLiteEventLog(event_db)
        event_log.close()
        console.print(f"  Initialized: {event_db}")

    doc_db = data_dir / "documents" / "documents.db"
    if not doc_db.exists() or force:
        doc_store = SQLiteDocumentStore(doc_db)
        doc_store.close()
        console.print(f"  Initialized: {doc_db}")

    vector_base = data_dir / "vectors" / "vectors"
    vector_db = vector_base.with_suffix(".db")
    vector_lance = vector_base.with_suffix(".lance")
    if (not vector_db.exists() and not vector_lance.exists()) or force:
        vector_store = create_vector_store(vector_base)
        vector_store.close()
        console.print(f"  Initialized vector store: {vector_base}")

    graph_db = data_dir / "graph" / "graph.db"
    if not graph_db.exists() or force:
        graph_store = SQLiteGraphStore(graph_db)
        graph_store.close()
        console.print(f"  Initialized: {graph_db}")

    workflow_db = data_dir / "workflows" / "workflows.db"
    if not workflow_db.exists() or force:
        workflow_store = SQLiteWorkflowRunStore(workflow_db)
        workflow_store.close()
        console.print(f"  Initialized: {workflow_db}")

    console.print("[bold green]Initialization complete![/bold green]")


@app.command("run-workflow")
def run_workflow(
    workflow_id: str = typer.Argument(..., help="Workflow ID to run"),
    intent: str | None = typer.Option(
        None, "--intent", "-i", help="Override intent"
    ),
    project: str | None = typer.Option(
        None, "--project", "-p", help="Project scope"
    ),
    approve: bool = typer.Option(
        False, "--approve", help="Auto-approve required actions (deprecated, use --auto-approve-risk)"
    ),
    auto_approve: list[str] = typer.Option(
        None, "--auto-approve", "-a", help="Auto-approve specific capabilities (e.g., notes.create@v1)"
    ),
    auto_approve_risk: str | None = typer.Option(
        None, "--auto-approve-risk", help="Auto-approve all actions up to risk level (none, low, medium, high)"
    ),
    interactive: bool = typer.Option(
        True, "--interactive/--batch", help="Prompt for approvals interactively vs. defer"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview actions without executing (shows what would need approval)"
    ),
) -> None:
    """Run a workflow by ID."""
    console.print(f"[bold blue]Running workflow: {workflow_id}[/bold blue]")

    try:
        # Map old --approve flag to --auto-approve-risk low for backwards compatibility
        effective_auto_approve_risk = auto_approve_risk
        if approve and not auto_approve_risk:
            effective_auto_approve_risk = "low"

        result = asyncio.run(_run_workflow_async(
            workflow_id,
            intent,
            project,
            auto_approve or [],
            effective_auto_approve_risk,
            interactive,
            dry_run,
        ))

        if result.success:
            console.print(Panel(
                f"[green]Workflow completed successfully![/green]\n"
                f"Run ID: {result.run_id}\n"
                f"Trace ID: {result.trace.trace_id if result.trace else 'N/A'}",
                title="Success",
            ))
        else:
            console.print(Panel(
                f"[red]Workflow failed[/red]\n"
                f"Run ID: {result.run_id}\n"
                f"Error: {result.error}\n"
                f"Step: {result.step_failed or 'N/A'}",
                title="Error",
            ))

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


async def _run_workflow_async(
    workflow_id: str,
    intent: str | None,
    project_id: str | None,
    auto_approve_capabilities: list[str],
    auto_approve_risk: str | None,
    interactive: bool,
    dry_run: bool,
):
    """Run workflow asynchronously."""
    settings = get_settings()
    data_dir = get_data_dir()

    # Initialize components
    event_log = SQLiteEventLog(data_dir / "events" / "events.db")
    trace_store = SQLiteTraceSink(data_dir / "traces" / "traces.db")
    document_store = SQLiteDocumentStore(data_dir / "documents" / "documents.db")
    vector_store = create_vector_store(data_dir / "vectors" / "vectors")
    graph_store = SQLiteGraphStore(data_dir / "graph" / "graph.db")
    experience_store = SQLiteExperienceStore(data_dir / "experience" / "experience.db")

    # Set up multi-sink trace store
    jsonl_sink = JSONLTraceSink(data_dir / "traces" / "traces.jsonl")
    multi_trace_store = MultiSinkTraceStore(trace_store, [jsonl_sink])

    # Set up adaptive timeout manager
    timeout_manager = AdaptiveTimeoutManager(trace_store=multi_trace_store)

    # Set up capability registry and tool broker
    registry = CapabilityRegistry()
    registry.load_from_directory(settings.configs_dir / "capabilities")

    # Configure retry for tool execution
    retry_config = None
    if settings.tool_broker_retry_enabled:
        retry_config = RetryConfig(
            max_retries=settings.tool_broker_retry_max_retries,
            base_delay_ms=settings.tool_broker_retry_base_delay_ms,
            max_delay_ms=settings.tool_broker_retry_max_delay_ms,
        )

    broker = ToolBroker(
        registry=registry,
        event_log=event_log,
        retry_config=retry_config,
        enable_circuit_breaker=settings.tool_broker_circuit_breaker_enabled,
        timeout_manager=timeout_manager,
    )
    register_builtin_tools(broker)
    _configure_library_tools(broker, settings.configs_dir)
    _configure_skill_scripts(broker, registry, settings)
    await _configure_mcp_adapter(broker, settings.configs_dir)

    # Set up context assembler
    assembler = ContextAssembler(
        document_store=document_store,
        vector_store=vector_store,
        graph_store=graph_store,
        skills_dir=settings.skills_dir,
        packs_config_dir=settings.configs_dir / "context_packs",
        sources_config_dir=settings.configs_dir / "sources",
        experience_store=experience_store,
    )

    # Set up approval gate with optional notification
    approval_gate = _build_approval_gate(settings, event_log)

    # Set up executor
    executor = DeterministicExecutor(
        tool_broker=broker,
        trace_store=multi_trace_store,
        approval_gate=approval_gate,
        event_log=event_log,
        auto_approve_capabilities=auto_approve_capabilities,
        auto_approve_risk=auto_approve_risk,
        interactive_approval=interactive and not dry_run,
        dry_run=dry_run,
    )

    # Register custom engine (LLM-backed)
    from agent_kernel.services.llm import create_llm_service

    try:
        provider = settings.default_llm_provider
        provider_configs = {
            "openai": (settings.openai_api_key, settings.openai_model),
            "anthropic": (settings.anthropic_api_key, settings.anthropic_model),
        }
        api_key, model = provider_configs.get(
            provider, (settings.openai_api_key, settings.openai_model)
        )
        llm_service = create_llm_service(
            provider=provider,
            api_key=api_key or None,
            model=model or None,
            base_url=settings.openai_base_url or None,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise

    # Set up feedback loop components
    cost_anomaly_detector = CostAnomalyDetector(
        event_log=event_log,
        trace_store=multi_trace_store,
    )
    experience_miner = ExperienceMiner(
        experience_store=experience_store,
        event_log=event_log,
        llm_service=llm_service,
    )

    # Set up workflow runner with persistent store
    workflow_store = SQLiteWorkflowRunStore(data_dir / "workflows" / "workflows.db")
    runner = WorkflowRunner(
        context_assembler=assembler,
        executor=executor,
        event_log=event_log,
        configs_dir=settings.configs_dir,
        workflow_store=workflow_store,
        trace_store=multi_trace_store,
        cost_anomaly_detector=cost_anomaly_detector,
        experience_miner=experience_miner,
    )

    engine = CustomEngine(llm_service=llm_service, capability_registry=registry)
    runner.register_engine(engine)

    approval_tokens = {"*": "auto"} if auto_approve_risk else None

    try:
        return await runner.run(
            workflow_id=workflow_id,
            intent=intent,
            project_id=project_id,
            approval_tokens=approval_tokens,
        )
    finally:
        # Cleanup
        event_log.close()
        multi_trace_store.close()
        document_store.close()
        vector_store.close()
        graph_store.close()
        workflow_store.close()
        if hasattr(experience_store, "close"):
            experience_store.close()


@app.command("list-traces")
def list_traces(
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum traces to show"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Filter by agent"),
    format: OutputFormat = typer.Option(OutputFormat.TEXT, "--format", help="Output format: text or json"),
) -> None:
    """List recent traces."""
    import json as json_lib

    data_dir = get_data_dir()
    trace_db = data_dir / "traces" / "traces.db"

    if not trace_db.exists():
        if format == OutputFormat.JSON:
            print(json_lib.dumps({"traces": [], "count": 0}))
        else:
            console.print("[yellow]No traces found. Run 'init' first.[/yellow]")
        return

    trace_store = SQLiteTraceSink(trace_db)

    try:
        traces = trace_store.list_traces(
            limit=limit,
            agent_profile_id=agent,
        )

        if format == OutputFormat.JSON:
            print(json_lib.dumps({
                "traces": [
                    {
                        "trace_id": t.trace_id,
                        "timestamp": t.timestamp.isoformat(),
                        "agent_profile_id": t.agent_profile_id,
                        "intent": t.intent,
                        "status": t.outcome.status.value,
                        "workflow_id": getattr(t, "workflow_id", None),
                        "tool_calls": len(t.tool_calls) if t.tool_calls else 0,
                    }
                    for t in traces
                ],
                "count": len(traces),
            }, indent=2, default=str))
            return

        if not traces:
            console.print("[yellow]No traces found.[/yellow]")
            return

        table = Table(title=f"Recent Traces ({len(traces)})")
        table.add_column("Trace ID", style="cyan")
        table.add_column("Timestamp", style="green")
        table.add_column("Agent", style="blue")
        table.add_column("Intent", style="white", max_width=40)
        table.add_column("Status", style="bold")

        for trace in traces:
            status_color = {
                "completed": "green",
                "partial": "yellow",
                "failed": "red",
                "needs_approval": "magenta",
            }.get(trace.outcome.status.value, "white")

            table.add_row(
                trace.trace_id[:12] + "...",
                trace.timestamp.strftime("%Y-%m-%d %H:%M"),
                trace.agent_profile_id,
                trace.intent[:40] + ("..." if len(trace.intent) > 40 else ""),
                f"[{status_color}]{trace.outcome.status.value}[/{status_color}]",
            )

        console.print(table)

    finally:
        trace_store.close()


@app.command("show-trace")
def show_trace(
    trace_id: str = typer.Argument(..., help="Trace ID to show"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
    details: bool = typer.Option(False, "--details", "-d", help="Show detailed input/output for each action"),
) -> None:
    """Show details of a specific trace."""
    import json as json_lib

    data_dir = get_data_dir()
    trace_db = data_dir / "traces" / "traces.db"

    if not trace_db.exists():
        console.print("[red]No traces database found.[/red]")
        raise typer.Exit(1)

    trace_store = SQLiteTraceSink(trace_db)

    def format_value(value: Any, max_length: int = 300) -> str:
        """Format a value for display."""
        if value is None:
            return "(none)"
        if isinstance(value, str):
            if len(value) > max_length:
                return value[:max_length] + "..."
            return value
        if isinstance(value, (list, dict)):
            try:
                formatted = json_lib.dumps(value, indent=2, default=str)
                if len(formatted) > max_length:
                    if isinstance(value, list):
                        return f"[{len(value)} items]"
                    if isinstance(value, dict):
                        return f"({len(value)} keys)"
                    return formatted[:max_length] + "..."
                return formatted
            except Exception:
                return str(value)[:max_length]
        return str(value)[:max_length]

    def extract_key_outputs(output: dict) -> dict:
        """Extract important fields from output."""
        if not output:
            return {}
        important = ["count", "total", "created", "updated", "deleted", "skipped",
                    "tasks", "projects", "events", "labels", "success", "error",
                    "message", "status", "changes", "synced_count", "failed_count"]
        result = {}
        for key in important:
            if key in output:
                val = output[key]
                if isinstance(val, list):
                    result[key] = f"({len(val)} items)"
                else:
                    result[key] = val
        if not result:
            for key, val in list(output.items())[:8]:
                if isinstance(val, list):
                    result[key] = f"({len(val)} items)"
                elif isinstance(val, dict):
                    result[key] = f"({len(val)} keys)"
                else:
                    result[key] = val
        return result

    try:
        # Try to find trace by prefix
        if len(trace_id) < 26:
            traces = trace_store.list_traces(limit=100)
            matching = [t for t in traces if t.trace_id.startswith(trace_id)]
            if len(matching) == 1:
                trace = matching[0]
            elif len(matching) > 1:
                console.print(f"[yellow]Multiple traces match '{trace_id}':[/yellow]")
                for t in matching:
                    console.print(f"  {t.trace_id}")
                return
            else:
                trace = None
        else:
            trace = trace_store.get(trace_id)

        if trace is None:
            console.print(f"[red]Trace not found: {trace_id}[/red]")
            raise typer.Exit(1)

        if json_output:
            console.print(trace.model_dump_json(indent=2))
            return

        # Pretty print trace
        console.print(Panel(
            f"[bold]Trace ID:[/bold] {trace.trace_id}\n"
            f"[bold]Run ID:[/bold] {trace.run_id}\n"
            f"[bold]Agent:[/bold] {trace.agent_profile_id}\n"
            f"[bold]Engine:[/bold] {trace.engine_id}\n"
            f"[bold]Timestamp:[/bold] {trace.timestamp}\n"
            f"[bold]Intent:[/bold] {trace.intent}",
            title="Trace Details",
        ))

        # Plan summary
        console.print(Panel(
            f"[bold]Summary:[/bold] {trace.plan.summary}\n"
            f"[bold]Actions:[/bold] {len(trace.plan.actions)}\n"
            f"[bold]Risk:[/bold] {trace.plan.risk.level.value}\n"
            f"[bold]Citations:[/bold] {len(trace.plan.context_refs_used)}",
            title="Plan",
        ))

        # Tool calls
        if trace.tool_calls:
            if details:
                # Detailed view with input/output
                console.print("\n[bold cyan]Tool Calls (Detailed)[/bold cyan]\n")
                for i, tc in enumerate(trace.tool_calls, 1):
                    status_color = {
                        "success": "green",
                        "error": "red",
                        "denied": "yellow",
                        "skipped": "dim",
                    }.get(tc.status.value, "white")
                    status_emoji = "✅" if tc.status.value == "success" else "❌" if tc.status.value in ("error", "failed") else "⚠️"

                    console.print(f"[bold]{i}. {tc.capability_name}[/bold] {status_emoji}")
                    console.print(f"   [bold]Status:[/bold] [{status_color}]{tc.status.value}[/{status_color}]  [bold]Duration:[/bold] {tc.duration_ms}ms")

                    # Input parameters
                    if tc.input:
                        console.print("   [bold]Input:[/bold]")
                        for key, val in tc.input.items():
                            formatted = format_value(val, 150)
                            if "\n" in formatted:
                                console.print(f"     [cyan]{key}:[/cyan]")
                                for line in formatted.split("\n")[:5]:
                                    console.print(f"       {line}")
                            else:
                                console.print(f"     [cyan]{key}:[/cyan] {formatted}")

                    # Output summary
                    if tc.output:
                        key_outputs = extract_key_outputs(tc.output)
                        if key_outputs:
                            console.print("   [bold]Output:[/bold]")
                            for key, val in key_outputs.items():
                                console.print(f"     [green]{key}:[/green] {val}")

                    # Error
                    if tc.error:
                        error_msg = tc.error.message if hasattr(tc.error, 'message') else str(tc.error)
                        console.print(f"   [bold red]Error:[/bold red] {error_msg[:200]}")

                    console.print()
            else:
                # Simple table view
                table = Table(title="Tool Calls")
                table.add_column("Capability", style="cyan")
                table.add_column("Status", style="bold")
                table.add_column("Duration", style="green")

                for tc in trace.tool_calls:
                    status_color = {
                        "success": "green",
                        "error": "red",
                        "denied": "yellow",
                        "skipped": "dim",
                    }.get(tc.status.value, "white")

                    table.add_row(
                        tc.capability_name,
                        f"[{status_color}]{tc.status.value}[/{status_color}]",
                        f"{tc.duration_ms}ms",
                    )

                console.print(table)
                console.print("[dim]Tip: Use --details or -d to see input/output for each action[/dim]")

        # Outcome
        outcome_color = {
            "completed": "green",
            "partial": "yellow",
            "failed": "red",
            "needs_approval": "magenta",
        }.get(trace.outcome.status.value, "white")

        status_val = trace.outcome.status.value
        console.print(Panel(
            f"[bold]Status:[/bold] [{outcome_color}]{status_val}[/{outcome_color}]\n"
            f"[bold]Summary:[/bold] {trace.outcome.summary or 'N/A'}\n"
            f"[bold]Artifacts:[/bold] {len(trace.outcome.artifacts)}",
            title="Outcome",
        ))

    finally:
        trace_store.close()


@app.command("list-capabilities")
def list_capabilities() -> None:
    """List registered capabilities."""
    settings = get_settings()
    registry = CapabilityRegistry()

    caps_dir = settings.configs_dir / "capabilities"
    if not caps_dir.exists():
        console.print("[yellow]No capabilities directory found.[/yellow]")
        return

    try:
        capabilities = registry.load_from_directory(caps_dir)

        if not capabilities:
            console.print("[yellow]No capabilities found.[/yellow]")
            return

        table = Table(title=f"Registered Capabilities ({len(capabilities)})")
        table.add_column("Name", style="cyan")
        table.add_column("Description", style="white", max_width=50)
        table.add_column("Side Effect", style="yellow")
        table.add_column("Approval", style="magenta")

        for cap in capabilities:
            side_effect = (
                cap.side_effect_level.value
                if hasattr(cap.side_effect_level, "value")
                else str(cap.side_effect_level)
            )
            table.add_row(
                cap.capability_name,
                cap.description[:50] + ("..." if len(cap.description) > 50 else ""),
                side_effect,
                "Yes" if cap.requires_approval_default else "No",
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Error loading capabilities: {e}[/red]")


@app.command("list-workflows")
def list_workflows() -> None:
    """List available workflows."""
    settings = get_settings()
    workflows_dir = settings.configs_dir / "workflows"

    if not workflows_dir.exists():
        console.print("[yellow]No workflows directory found.[/yellow]")
        return

    import yaml

    table = Table(title="Available Workflows")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Description", style="white", max_width=50)
    table.add_column("Agent", style="blue")
    table.add_column("Trigger", style="green")

    for yaml_file in sorted(workflows_dir.glob("*.yaml")):
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)

            trigger_type = data.get("trigger", {}).get("type", "manual")

            table.add_row(
                data.get("workflow_id", yaml_file.stem),
                data.get("name", ""),
                (data.get("description", "")[:50] or "").replace("\n", " "),
                data.get("agent_profile_id", ""),
                trigger_type,
            )
        except Exception as e:
            console.print(f"[yellow]Error loading {yaml_file}: {e}[/yellow]")

    console.print(table)


@app.command("list-approvals")
def list_approvals(
    workflow: str | None = typer.Option(None, "--workflow", "-w", help="Filter by workflow ID"),
    run: str | None = typer.Option(None, "--run", "-r", help="Filter by workflow run ID"),
    limit: int = typer.Option(50, "--limit", "-n", help="Maximum approvals to show"),
    format: OutputFormat = typer.Option(OutputFormat.TEXT, "--format", help="Output format: text or json"),
) -> None:
    """List pending approval requests."""
    import json as json_lib

    from agent_kernel.core.schemas.workflow import ApprovalRequestStatus

    data_dir = get_data_dir()
    workflows_db = data_dir / "workflows" / "workflows.db"

    if not workflows_db.exists():
        if format == OutputFormat.JSON:
            print(json_lib.dumps({"approvals": [], "count": 0}))
        else:
            console.print("[yellow]No approval requests found.[/yellow]")
            console.print("Approval requests are created when workflows need approval.")
        return

    store = SQLiteWorkflowRunStore(workflows_db)

    try:
        pending = store.list_approval_requests(
            status=ApprovalRequestStatus.PENDING,
            limit=limit,
        )

        # Apply optional filters in Python (store API doesn't support them directly)
        if workflow:
            pending = [a for a in pending if a.workflow_id == workflow]
        if run:
            pending = [a for a in pending if a.run_id == run]

        if format == OutputFormat.JSON:
            print(json_lib.dumps({
                "approvals": [
                    {
                        "approval_id": a.approval_id,
                        "workflow_id": a.workflow_id,
                        "run_id": a.run_id,
                        "action_id": a.action_id,
                        "capability_name": a.capability_name,
                        "effective_side_effect": a.effective_side_effect.value,
                        "status": a.status.value,
                        "requested_at": a.requested_at.isoformat(),
                        "action_preview": a.action_preview,
                    }
                    for a in pending
                ],
                "count": len(pending),
            }, indent=2, default=str))
            return

        if not pending:
            console.print("[yellow]No pending approval requests.[/yellow]")
            return

        table = Table(title="Pending Approval Requests")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Workflow", style="blue")
        table.add_column("Action", style="bold")
        table.add_column("Side Effect", style="yellow")
        table.add_column("Requested", style="white")

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)

        for approval in pending:
            # Calculate time ago
            delta = now - approval.requested_at.replace(tzinfo=timezone.utc)
            if delta.days > 0:
                time_ago = f"{delta.days}d ago"
            elif delta.seconds >= 3600:
                time_ago = f"{delta.seconds // 3600}h ago"
            else:
                time_ago = f"{delta.seconds // 60}m ago"

            table.add_row(
                approval.approval_id[:12] + "...",
                approval.workflow_id,
                approval.capability_name,
                approval.effective_side_effect.value,
                time_ago,
            )

        console.print(table)
        console.print(f"\n[dim]Use 'agent-kernel show-approval <id>' to see details[/dim]")
        console.print(f"[dim]Use 'agent-kernel approve <id>' to approve[/dim]")

    finally:
        store.close()


@app.command("show-approval")
def show_approval(
    approval_id: str = typer.Argument(..., help="Approval ID"),
) -> None:
    """Show details of an approval request."""
    data_dir = get_data_dir()
    workflows_db = data_dir / "workflows" / "workflows.db"

    if not workflows_db.exists():
        console.print(f"[red]Approval not found: {approval_id}[/red]")
        return

    store = SQLiteWorkflowRunStore(workflows_db)

    try:
        approval = store.get_approval_request(approval_id)

        # Support partial ID matching (CLI shows truncated IDs)
        if not approval:
            all_approvals = store.list_approval_requests()
            for a in all_approvals:
                if a.approval_id.startswith(approval_id):
                    approval = a
                    break

        if not approval:
            console.print(f"[red]Approval not found: {approval_id}[/red]")
            return

        # Display approval details
        console.print()
        console.print(Panel(
            f"[bold]Approval Request[/bold]\n"
            f"Status: [{_approval_status_color(approval.status.value)}]{approval.status.value}[/]",
            title=f"Approval {approval.approval_id[:16]}...",
            border_style="cyan" if approval.status.value == "pending" else "dim",
        ))

        details_table = Table(show_header=False, box=None, padding=(0, 2))
        details_table.add_column("Field", style="cyan", no_wrap=True)
        details_table.add_column("Value")

        details_table.add_row("Workflow", approval.workflow_id)
        details_table.add_row("Run ID", approval.run_id)
        details_table.add_row("Action", approval.capability_name)
        details_table.add_row("Side Effect", approval.effective_side_effect.value)
        details_table.add_row("Requested At", approval.requested_at.isoformat())

        if approval.expires_at:
            details_table.add_row("Expires At", approval.expires_at.isoformat())

        if approval.resolved_at:
            details_table.add_row("Resolved At", approval.resolved_at.isoformat())
            details_table.add_row("Resolver", approval.resolver or "N/A")

        if approval.reason:
            details_table.add_row("Reason", approval.reason)

        if approval.policy_basis:
            details_table.add_row("Policy Basis", approval.policy_basis)

        console.print(details_table)

        # Show action preview
        if approval.action_preview:
            console.print("\n[bold]Action Preview:[/bold]")
            import json
            from rich.syntax import Syntax

            syntax = Syntax(
                json.dumps(approval.action_preview, indent=2),
                "json",
                theme="monokai",
                line_numbers=False,
            )
            console.print(syntax)

        console.print()

        if approval.status.value == "pending":
            console.print("[dim]Use 'agent-kernel approve {id}' to approve[/dim]")
            console.print("[dim]Use 'agent-kernel deny {id}' to deny[/dim]")

    finally:
        store.close()


@app.command("approve")
def approve(
    approval_id: str = typer.Argument(..., help="Approval ID"),
    reason: str | None = typer.Option(
        None, "--reason", "-r", help="Approval reason"
    ),
    resume: bool = typer.Option(
        True, "--resume/--no-resume", help="Resume the workflow after approval"
    ),
) -> None:
    """Approve a pending action and optionally resume the workflow."""
    from datetime import datetime, timezone
    from agent_kernel.core.schemas.workflow import ApprovalRequestStatus

    data_dir = get_data_dir()
    workflows_db = data_dir / "workflows" / "workflows.db"

    if not workflows_db.exists():
        console.print(f"[red]Approval not found: {approval_id}[/red]")
        return

    store = SQLiteWorkflowRunStore(workflows_db)

    try:
        approval = store.get_approval_request(approval_id)

        if approval and approval.status == ApprovalRequestStatus.PENDING:
            approval.status = ApprovalRequestStatus.APPROVED
            approval.resolver = "cli_user"
            approval.resolved_at = datetime.now(timezone.utc)
            approval.reason = reason
            store.update_approval_request(approval)

        if approval:
            console.print(Panel(
                f"[green]Approval granted[/green]\n"
                f"Workflow: {approval.workflow_id}\n"
                f"Action: {approval.capability_name}\n"
                f"Reason: {reason or 'No reason provided'}",
                title="Approved",
                border_style="green",
            ))

            if resume:
                console.print("[bold blue]Resuming workflow...[/bold blue]")
                try:
                    result = asyncio.run(
                        _resume_workflow_async(
                            run_id=approval.run_id,
                            approval_tokens={approval.action_id: approval.approval_id},
                        )
                    )
                    if result.success:
                        console.print(Panel(
                            f"[green]Workflow resumed and completed![/green]\n"
                            f"Run ID: {result.run_id}\n"
                            f"Trace ID: {result.trace.trace_id if result.trace else 'N/A'}",
                            title="Success",
                        ))
                    else:
                        console.print(Panel(
                            f"[red]Workflow resumed but failed[/red]\n"
                            f"Run ID: {result.run_id}\n"
                            f"Error: {result.error}",
                            title="Error",
                        ))
                except Exception as e:
                    console.print(f"[red]Failed to resume workflow: {e}[/red]")
                    console.print("[dim]The approval was recorded. Use 'agent-kernel resume-workflow' to retry.[/dim]")
        else:
            console.print(f"[red]Failed to approve. Approval may not exist or already processed.[/red]")

    finally:
        store.close()


@app.command("deny")
def deny(
    approval_id: str = typer.Argument(..., help="Approval ID"),
    reason: str = typer.Option(..., "--reason", "-r", help="Reason for denial (required)"),
) -> None:
    """Deny a pending action."""
    from datetime import datetime, timezone
    from agent_kernel.core.schemas.workflow import ApprovalRequestStatus

    data_dir = get_data_dir()
    workflows_db = data_dir / "workflows" / "workflows.db"

    if not workflows_db.exists():
        console.print(f"[red]Approval not found: {approval_id}[/red]")
        return

    store = SQLiteWorkflowRunStore(workflows_db)

    try:
        approval = store.get_approval_request(approval_id)

        if approval and approval.status == ApprovalRequestStatus.PENDING:
            approval.status = ApprovalRequestStatus.DENIED
            approval.resolver = "cli_user"
            approval.resolved_at = datetime.now(timezone.utc)
            approval.reason = reason
            store.update_approval_request(approval)

        if approval:
            console.print(Panel(
                f"[red]✗ Approval denied[/red]\n"
                f"Workflow: {approval.workflow_id}\n"
                f"Action: {approval.capability_name}\n"
                f"Reason: {reason}",
                title="Denied",
                border_style="red",
            ))
        else:
            console.print(f"[red]Failed to deny. Approval may not exist or already processed.[/red]")

    finally:
        store.close()


def _approval_status_color(status: str) -> str:
    """Get color for approval status."""
    colors = {
        "pending": "yellow",
        "approved": "green",
        "denied": "red",
        "expired": "dim",
    }
    return colors.get(status, "white")


async def _resume_workflow_async(
    run_id: str,
    approval_tokens: dict[str, str],
) -> WorkflowResult:
    """Resume a paused workflow asynchronously."""
    settings = get_settings()
    data_dir = get_data_dir()

    # Initialize stores
    event_log = SQLiteEventLog(data_dir / "events" / "events.db")
    trace_store = SQLiteTraceSink(data_dir / "traces" / "traces.db")
    document_store = SQLiteDocumentStore(data_dir / "documents" / "documents.db")
    vector_store = create_vector_store(data_dir / "vectors" / "vectors")
    graph_store = SQLiteGraphStore(data_dir / "graph" / "graph.db")
    experience_store = SQLiteExperienceStore(data_dir / "experience" / "experience.db")
    workflow_store = SQLiteWorkflowRunStore(data_dir / "workflows" / "workflows.db")

    jsonl_sink = JSONLTraceSink(data_dir / "traces" / "traces.jsonl")
    multi_trace_store = MultiSinkTraceStore(trace_store, [jsonl_sink])

    # Set up adaptive timeout manager
    timeout_manager = AdaptiveTimeoutManager(trace_store=multi_trace_store)

    registry = CapabilityRegistry()
    registry.load_from_directory(settings.configs_dir / "capabilities")

    retry_config = None
    if settings.tool_broker_retry_enabled:
        retry_config = RetryConfig(
            max_retries=settings.tool_broker_retry_max_retries,
            base_delay_ms=settings.tool_broker_retry_base_delay_ms,
            max_delay_ms=settings.tool_broker_retry_max_delay_ms,
        )

    broker = ToolBroker(
        registry=registry,
        event_log=event_log,
        retry_config=retry_config,
        enable_circuit_breaker=settings.tool_broker_circuit_breaker_enabled,
        timeout_manager=timeout_manager,
    )
    register_builtin_tools(broker)
    _configure_library_tools(broker, settings.configs_dir)
    _configure_skill_scripts(broker, registry, settings)
    await _configure_mcp_adapter(broker, settings.configs_dir)

    assembler = ContextAssembler(
        document_store=document_store,
        vector_store=vector_store,
        graph_store=graph_store,
        skills_dir=settings.skills_dir,
        packs_config_dir=settings.configs_dir / "context_packs",
        sources_config_dir=settings.configs_dir / "sources",
        experience_store=experience_store,
    )

    approval_gate = _build_approval_gate(settings, event_log)

    executor = DeterministicExecutor(
        tool_broker=broker,
        trace_store=multi_trace_store,
        approval_gate=approval_gate,
        event_log=event_log,
        interactive_approval=True,
    )

    # Register engine
    from agent_kernel.services.llm import create_llm_service

    llm_service = create_llm_service(
        provider=settings.default_llm_provider,
        api_key=(
            settings.openai_api_key
            if settings.default_llm_provider == "openai"
            else settings.anthropic_api_key
        )
        or None,
        model=(
            settings.openai_model
            if settings.default_llm_provider == "openai"
            else settings.anthropic_model
        )
        or None,
        base_url=getattr(settings, "openai_base_url", None),
    )

    # Set up feedback loop components
    cost_anomaly_detector = CostAnomalyDetector(
        event_log=event_log,
        trace_store=multi_trace_store,
    )
    experience_miner = ExperienceMiner(
        experience_store=experience_store,
        event_log=event_log,
        llm_service=llm_service,
    )

    runner = WorkflowRunner(
        context_assembler=assembler,
        executor=executor,
        event_log=event_log,
        configs_dir=settings.configs_dir,
        workflow_store=workflow_store,
        trace_store=multi_trace_store,
        cost_anomaly_detector=cost_anomaly_detector,
        experience_miner=experience_miner,
    )

    engine = CustomEngine(llm_service=llm_service, capability_registry=registry)
    runner.register_engine(engine)

    try:
        return await runner.resume(
            run_id=run_id,
            approval_tokens=approval_tokens,
        )
    finally:
        event_log.close()
        multi_trace_store.close()
        document_store.close()
        vector_store.close()
        graph_store.close()
        workflow_store.close()
        if hasattr(experience_store, "close"):
            experience_store.close()


@app.command("resume-workflow")
def resume_workflow(
    run_id: str = typer.Argument(..., help="Workflow run ID to resume"),
    approval_token: list[str] = typer.Option(
        None, "--approval-token", "-t",
        help="Approval token as action_id=approval_id",
    ),
) -> None:
    """Resume a paused workflow run.

    Use this to manually resume a workflow that is in WAITING_APPROVAL status.
    Approval tokens map action IDs to their approval IDs.
    """
    tokens: dict[str, str] = {}
    if approval_token:
        for token in approval_token:
            if "=" not in token:
                console.print(f"[red]Invalid token format: {token}[/red]")
                console.print("[dim]Expected format: action_id=approval_id[/dim]")
                raise typer.Exit(1)
            action_id, approval_id = token.split("=", 1)
            tokens[action_id] = approval_id

    console.print(f"[bold blue]Resuming workflow run: {run_id}[/bold blue]")

    try:
        result = asyncio.run(_resume_workflow_async(run_id, tokens))

        if result.success:
            console.print(Panel(
                f"[green]Workflow resumed and completed![/green]\n"
                f"Run ID: {result.run_id}\n"
                f"Trace ID: {result.trace.trace_id if result.trace else 'N/A'}",
                title="Success",
            ))
        elif result.needs_approval:
            console.print(Panel(
                f"[yellow]Workflow paused again for approval[/yellow]\n"
                f"Run ID: {result.run_id}\n"
                f"Use 'agent-kernel list-approvals' to see pending approvals",
                title="Waiting",
            ))
        else:
            console.print(Panel(
                f"[red]Workflow failed[/red]\n"
                f"Run ID: {result.run_id}\n"
                f"Error: {result.error}\n"
                f"Step: {result.step_failed or 'N/A'}",
                title="Error",
            ))
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command("list-runs")
def list_runs(
    workflow: str | None = typer.Option(
        None, "--workflow", "-w", help="Filter by workflow ID"
    ),
    status: str | None = typer.Option(
        None, "--status", "-s", help="Filter by status (pending, running, completed, failed, waiting_approval)"
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum runs to show"),
    format: OutputFormat = typer.Option(OutputFormat.TEXT, "--format", help="Output format: text or json"),
) -> None:
    """List workflow runs."""
    import json as json_lib

    from agent_kernel.core.schemas.workflow import WorkflowRunStatus

    data_dir = get_data_dir()
    workflow_db = data_dir / "workflows" / "workflows.db"

    if not workflow_db.exists():
        if format == OutputFormat.JSON:
            print(json_lib.dumps({"runs": [], "count": 0}))
        else:
            console.print("[yellow]No workflow runs found. Run 'init' first.[/yellow]")
        return

    store = SQLiteWorkflowRunStore(workflow_db)

    try:
        status_filter = None
        if status:
            try:
                status_filter = WorkflowRunStatus(status)
            except ValueError:
                valid = ", ".join(s.value for s in WorkflowRunStatus)
                console.print(f"[red]Invalid status: {status}. Valid: {valid}[/red]")
                raise typer.Exit(1)

        runs = store.list_runs(
            workflow_id=workflow,
            status=status_filter,
            limit=limit,
        )

        if format == OutputFormat.JSON:
            print(json_lib.dumps({
                "runs": [
                    {
                        "run_id": r.run_id,
                        "workflow_id": r.workflow_id,
                        "status": r.status.value,
                        "intent": r.intent,
                        "last_step": r.last_step,
                        "started_at": r.started_at.isoformat() if r.started_at else None,
                        "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                        "retry_count": r.retry_count,
                        "trace_ids": r.trace_ids,
                    }
                    for r in runs
                ],
                "count": len(runs),
            }, indent=2, default=str))
            return

        if not runs:
            console.print("[yellow]No workflow runs found.[/yellow]")
            return

        table = Table(title=f"Workflow Runs ({len(runs)})")
        table.add_column("Run ID", style="cyan", no_wrap=True)
        table.add_column("Workflow", style="blue")
        table.add_column("Status", style="bold")
        table.add_column("Last Step", style="white")
        table.add_column("Started", style="green")
        table.add_column("Traces", style="dim")

        for run in runs:
            status_color = {
                "pending": "yellow",
                "running": "blue",
                "completed": "green",
                "failed": "red",
                "waiting_approval": "magenta",
            }.get(run.status.value, "white")

            started = (
                run.started_at.strftime("%Y-%m-%d %H:%M")
                if run.started_at
                else "N/A"
            )

            table.add_row(
                run.run_id[:12] + "...",
                run.workflow_id,
                f"[{status_color}]{run.status.value}[/{status_color}]",
                run.last_step or "N/A",
                started,
                str(len(run.trace_ids)),
            )

        console.print(table)

    finally:
        store.close()


@app.command("workflow-debug")
def workflow_debug(
    run_id: str = typer.Argument(..., help="Workflow run ID (or prefix)"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
    show_events: bool = typer.Option(
        False, "--events", "-e", help="Show full event timeline"
    ),
) -> None:
    """Debug a workflow run — shows run state, traces, tool calls, events, and diagnostics."""
    import json as json_lib

    from agent_kernel.services.workflow_debug import collect_debug_info

    data_dir = get_data_dir()
    workflow_db = data_dir / "workflows" / "workflows.db"

    if not workflow_db.exists():
        console.print("[red]No workflow database found. Run 'init' first.[/red]")
        raise typer.Exit(1)

    trace_db = data_dir / "traces" / "traces.db"
    event_db = data_dir / "events" / "events.db"

    store = SQLiteWorkflowRunStore(workflow_db)
    trace_store = SQLiteTraceSink(trace_db) if trace_db.exists() else None
    event_log = SQLiteEventLog(event_db) if event_db.exists() else None

    try:
        # Prefix matching (same pattern as show-trace)
        resolved_id = run_id
        if len(run_id) < 26:
            runs = store.list_runs(limit=100)
            matching = [r for r in runs if r.run_id.startswith(run_id)]
            if len(matching) == 1:
                resolved_id = matching[0].run_id
            elif len(matching) > 1:
                console.print(f"[yellow]Multiple runs match '{run_id}':[/yellow]")
                for r in matching:
                    console.print(f"  {r.run_id}  ({r.workflow_id})")
                raise typer.Exit(1)

        # Need a trace store stub if DB doesn't exist
        if trace_store is None:
            from agent_kernel.tracing.trace_store import TraceStore

            class _EmptyTraceStore(TraceStore):
                def write(self, trace: Any) -> None:
                    pass

                def get(self, trace_id: str) -> None:
                    return None

                def list_traces(self, **kwargs: Any) -> list:
                    return []

                def close(self) -> None:
                    pass

            trace_store = _EmptyTraceStore()

        if event_log is None:
            from agent_kernel.memory.event_log import EventLog as _EventLogBase

            class _EmptyEventLog(_EventLogBase):
                def append(self, event: Any) -> None:
                    pass

                def get_events(self, **kwargs: Any) -> list:
                    return []

                def count(self, **kwargs: Any) -> int:
                    return 0

                def close(self) -> None:
                    pass

            event_log = _EmptyEventLog()

        try:
            info = collect_debug_info(
                run_id=resolved_id,
                workflow_store=store,
                trace_store=trace_store,
                event_log=event_log,
            )
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

        if json_output:
            console.print(json_lib.dumps(info.to_dict(), indent=2, default=str))
            return

        # -- Run Overview --
        run = info.run
        status_color = {
            "queued": "yellow",
            "running": "blue",
            "completed": "green",
            "failed": "red",
            "waiting_approval": "magenta",
            "cancelled": "dim",
        }.get(run.status.value, "white")

        started_str = (
            run.started_at.strftime("%Y-%m-%d %H:%M:%S") if run.started_at else "N/A"
        )
        ended_str = (
            run.ended_at.strftime("%Y-%m-%d %H:%M:%S") if run.ended_at else "N/A"
        )
        duration_str = f"{info.duration_ms}ms" if info.duration_ms is not None else "N/A"

        console.print(
            Panel(
                f"[bold]Run ID:[/bold] {run.run_id}\n"
                f"[bold]Workflow:[/bold] {run.workflow_id}\n"
                f"[bold]Status:[/bold] [{status_color}]{run.status.value}[/{status_color}]\n"
                f"[bold]Intent:[/bold] {run.intent or 'N/A'}\n"
                f"[bold]Started:[/bold] {started_str}\n"
                f"[bold]Ended:[/bold] {ended_str}\n"
                f"[bold]Duration:[/bold] {duration_str}\n"
                f"[bold]Last Step:[/bold] {run.last_step or 'N/A'}\n"
                f"[bold]Retries:[/bold] {run.retry_count}",
                title="Run Overview",
            )
        )

        # -- Error --
        if run.error:
            console.print(
                Panel(
                    f"[bold]Code:[/bold] {run.error.code}\n"
                    f"[bold]Message:[/bold] {run.error.message}",
                    title="Error",
                    border_style="red",
                )
            )

        # -- Checkpoint --
        if info.checkpoint:
            cp = info.checkpoint
            console.print(
                Panel(
                    f"[bold]Step Index:[/bold] {cp.step_index}\n"
                    f"[bold]Step Name:[/bold] {cp.step_name}\n"
                    f"[bold]Resume From:[/bold] step {cp.resume_from_index}\n"
                    f"[dim]Use 'agent-kernel resume-workflow {run.run_id}' to resume[/dim]",
                    title="Checkpoint",
                    border_style="yellow",
                )
            )

        # -- Pending Approvals --
        if info.pending_approvals:
            approval_table = Table(title="Pending Approvals")
            approval_table.add_column("Approval ID", style="cyan", no_wrap=True)
            approval_table.add_column("Capability", style="blue")
            approval_table.add_column("Side Effect", style="yellow")
            approval_table.add_column("Requested At", style="green")

            for appr in info.pending_approvals:
                approval_table.add_row(
                    appr.approval_id[:12] + "...",
                    appr.capability_name,
                    appr.effective_side_effect.value,
                    appr.requested_at.strftime("%Y-%m-%d %H:%M"),
                )

            console.print(approval_table)

        # -- Traces --
        if info.traces:
            trace_table = Table(title=f"Traces ({len(info.traces)})")
            trace_table.add_column("Trace ID", style="cyan", no_wrap=True)
            trace_table.add_column("Intent", style="white")
            trace_table.add_column("Outcome", style="bold")
            trace_table.add_column("Tool Calls", style="green")
            trace_table.add_column("Timestamp", style="dim")

            outcome_colors = {
                "completed": "green",
                "partial": "yellow",
                "failed": "red",
                "needs_approval": "magenta",
            }

            for trace in info.traces:
                oc = outcome_colors.get(trace.outcome.status.value, "white")
                trace_table.add_row(
                    trace.trace_id[:12] + "...",
                    (trace.intent[:40] + "...") if len(trace.intent) > 40 else trace.intent,
                    f"[{oc}]{trace.outcome.status.value}[/{oc}]",
                    str(len(trace.tool_calls)),
                    trace.timestamp.strftime("%H:%M:%S"),
                )

            console.print(trace_table)

        # -- Tool Call Summary --
        summary = info.tool_call_summary
        if summary:
            rate = summary["success_rate"]
            rate_color = "green" if rate >= 0.9 else "yellow" if rate >= 0.7 else "red"

            console.print(
                Panel(
                    f"[bold]Total:[/bold] {summary['total']}\n"
                    f"[bold]Successes:[/bold] [green]{summary['successes']}[/green]\n"
                    f"[bold]Failures:[/bold] [red]{summary['failures']}[/red]\n"
                    f"[bold]Success Rate:[/bold] [{rate_color}]{rate:.1%}[/{rate_color}]\n"
                    f"[bold]Avg Duration:[/bold] {summary['avg_duration_ms']}ms",
                    title="Tool Call Summary",
                )
            )

            # Tool Calls Detail table
            tc_table = Table(title="Tool Calls Detail")
            tc_table.add_column("Capability", style="cyan")
            tc_table.add_column("Status", style="bold")
            tc_table.add_column("Duration", style="green")
            tc_table.add_column("Error", style="red")

            tc_status_colors = {
                "success": "green",
                "error": "red",
                "failed": "red",
                "denied": "yellow",
                "skipped": "dim",
                "timeout": "red",
            }

            for trace in info.traces:
                for tc in trace.tool_calls:
                    sc = tc_status_colors.get(tc.status.value, "white")
                    error_msg = ""
                    if tc.error:
                        error_msg = tc.error.message[:60] if hasattr(tc.error, "message") else str(tc.error)[:60]
                    tc_table.add_row(
                        tc.capability_name,
                        f"[{sc}]{tc.status.value}[/{sc}]",
                        f"{tc.duration_ms}ms",
                        error_msg,
                    )

            console.print(tc_table)

        # -- Event Timeline --
        if show_events and info.events:
            event_table = Table(title=f"Event Timeline ({len(info.events)})")
            event_table.add_column("Time", style="dim", no_wrap=True)
            event_table.add_column("Event Type", style="cyan")
            event_table.add_column("Source", style="blue")
            event_table.add_column("Payload", style="white")

            for evt in info.events:
                payload_str = ""
                if evt.payload:
                    items = [f"{k}={v}" for k, v in list(evt.payload.items())[:3]]
                    payload_str = ", ".join(items)
                    if len(payload_str) > 80:
                        payload_str = payload_str[:77] + "..."

                event_table.add_row(
                    evt.occurred_at.strftime("%H:%M:%S.%f")[:12],
                    evt.event_type.value,
                    evt.source,
                    payload_str,
                )

            console.print(event_table)
        elif show_events:
            console.print("[dim]No events found for this run.[/dim]")

    finally:
        store.close()
        if hasattr(trace_store, "close"):
            trace_store.close()
        if hasattr(event_log, "close"):
            event_log.close()


# =============================================================================
# Vault Commands
# =============================================================================


@app.command("obsidian-sync")
@app.command("vault-sync")
def vault_sync(
    force: bool = typer.Option(
        False, "--force", "-f", help="Force re-index even if unchanged"
    ),
    folder: str | None = typer.Option(
        None, "--folder", help="Specific folder to sync"
    ),
    vault_path: str | None = typer.Option(
        None, "--vault", "-v", help="Vault path (overrides OBSIDIAN_VAULT_PATH)"
    ),
    no_ids: bool = typer.Option(
        False, "--no-ids", help="Don't inject stable IDs"
    ),
    with_embeddings: bool = typer.Option(
        False, "--with-embeddings", "-e", help="Generate embeddings (requires OPENAI_API_KEY)"
    ),
    embedding_model: str | None = typer.Option(
        None, "--embedding-model", help="Embedding model (default: from config or text-embedding-3-small)"
    ),
    with_enrichment: bool = typer.Option(
        False, "--with-enrichment", help="Enable LLM enrichment (auto-tags, requires OPENAI_API_KEY)"
    ),
    enrichment_model: str | None = typer.Option(
        None, "--enrichment-model", help="LLM model for enrichment (default: from config or gpt-4o-mini)"
    ),
    summarization_skip: str | None = typer.Option(
        None, "--summarization-skip", 
        help="Override summarization skip behavior: skip_entirely | enrich_no_summary"
    ),
    summarize_all: bool = typer.Option(
        False, "--summarize-all", help="Override thresholds and summarize all notes"
    ),
) -> None:
    """Sync Obsidian vault to kernel indexes.
    
    Use --with-embeddings to generate vector embeddings for semantic search.
    Use --with-enrichment to generate auto-tags and classification.
    Both require OPENAI_API_KEY to be set in your environment.
    
    Summarization thresholds are configured in .env (SUMMARIZATION_*).
    
    Configuration can be set via environment variables or .env file:
        OBSIDIAN_VAULT_PATH, EMBEDDING_MODEL, ENRICHMENT_MODEL
    """
    settings = get_settings()

    # Resolve vault path: CLI arg > env var
    resolved_vault_path = vault_path or settings.obsidian_vault_path
    if not resolved_vault_path:
        console.print(
            "[red]Error: No vault path specified[/red]"
        )
        console.print("Set the path via --vault flag or OBSIDIAN_VAULT_PATH in .env:")
        console.print("  agent-kernel vault-sync --vault /path/to/vault")
        console.print("  OBSIDIAN_VAULT_PATH=/path/to/your/vault")
        raise typer.Exit(1)

    # Resolve model defaults from settings
    resolved_embedding_model = embedding_model or settings.embedding_model
    resolved_enrichment_model = enrichment_model or settings.enrichment_model

    console.print(
        f"[bold blue]Syncing vault: {resolved_vault_path}[/bold blue]"
    )
    if with_embeddings:
        console.print(f"[cyan]Embeddings enabled: {resolved_embedding_model}[/cyan]")
    if with_enrichment:
        console.print(f"[cyan]Enrichment enabled: {resolved_enrichment_model}[/cyan]")
        if summarize_all:
            console.print("[cyan]Summarization: all notes (--summarize-all)[/cyan]")
        elif summarization_skip:
            console.print(f"[cyan]Summarization skip behavior: {summarization_skip}[/cyan]")
        else:
            console.print("[dim]Summarization: using .env thresholds[/dim]")

    try:
        result = asyncio.run(_vault_sync_async(
            force=force,
            folder=folder,
            inject_ids=not no_ids,
            with_embeddings=with_embeddings,
            embedding_model=resolved_embedding_model,
            with_enrichment=with_enrichment,
            enrichment_model=resolved_enrichment_model,
            vault_path=resolved_vault_path,
            summarization_skip_override=summarization_skip,
            summarize_all=summarize_all,
        ))

        # Display results
        console.print(Panel(
            f"[bold]Total notes:[/bold] {result.total_notes}\n"
            f"[green]Created:[/green] {result.created}\n"
            f"[yellow]Updated:[/yellow] {result.updated}\n"
            f"[dim]Unchanged:[/dim] {result.unchanged}\n"
            f"[red]Errors:[/red] {result.errors}",
            title="Sync Complete",
        ))

        if result.errors > 0:
            console.print("\n[yellow]Errors encountered:[/yellow]")
            for r in result.results:
                if r.error:
                    console.print(f"  • {r.path}: {r.error}")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


async def _vault_sync_async(
    force: bool,
    folder: str | None,
    inject_ids: bool,
    with_embeddings: bool = False,
    embedding_model: str = "text-embedding-3-small",
    with_enrichment: bool = False,
    enrichment_model: str = "gpt-4o-mini",
    vault_path: str | None = None,
    summarization_skip_override: str | None = None,
    summarize_all: bool = False,
):
    """Run vault sync asynchronously."""
    from agent_kernel.services.vault_sync import run_vault_sync

    return await run_vault_sync(
        force=force,
        folder=folder,
        inject_ids=inject_ids,
        with_embeddings=with_embeddings,
        embedding_model=embedding_model,
        with_enrichment=with_enrichment,
        enrichment_model=enrichment_model,
        vault_path=vault_path,
        summarization_skip_override=summarization_skip_override,
        summarize_all=summarize_all,
    )


@app.command("obsidian-watch")
@app.command("vault-watch")
def vault_watch(
    debounce: float = typer.Option(
        10.0, "--debounce", "-d", help="Debounce seconds"
    ),
    batch_interval: float = typer.Option(
        30.0, "--batch", "-b", help="Batch processing interval"
    ),
) -> None:
    """Watch vault for changes and index automatically."""
    settings = get_settings()

    if not settings.obsidian_vault_path:
        console.print("[red]Error: OBSIDIAN_VAULT_PATH not set in .env[/red]")
        raise typer.Exit(1)

    console.print(
        f"[bold blue]Watching vault: {settings.obsidian_vault_path}[/bold blue]"
    )
    console.print(f"Debounce: {debounce}s, Batch interval: {batch_interval}s")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    try:
        asyncio.run(_vault_watch_async(debounce, batch_interval))
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped watching[/yellow]")


async def _vault_watch_async(debounce: float, batch_interval: float):
    """Run vault watcher asynchronously."""
    from agent_kernel.services.vault_watcher import VaultWatcher
    from agent_kernel.tools.builtin.obsidian import ObsidianVault

    settings = get_settings()
    data_dir = get_data_dir()

    # Initialize stores
    document_store = SQLiteDocumentStore(data_dir / "documents" / "documents.db")
    vector_store = create_vector_store(data_dir / "vectors" / "vectors")
    graph_store = SQLiteGraphStore(data_dir / "graph" / "graph.db")

    # Create watcher
    vault = ObsidianVault(settings.obsidian_vault_path)
    watcher = VaultWatcher(
        vault=vault,
        document_store=document_store,
        graph_store=graph_store,
        vector_store=vector_store,
        debounce_seconds=debounce,
        batch_interval=batch_interval,
    )

    # Register callback
    def on_complete(summary):
        console.print(
            f"[green]Indexed {summary.total_notes} notes "
            f"({summary.created} new, {summary.updated} updated)[/green]"
        )

    watcher.on_index_complete(on_complete)

    try:
        await watcher.start()

        # Keep running until interrupted
        while True:
            await asyncio.sleep(1)

    finally:
        await watcher.stop()
        document_store.close()
        vector_store.close()
        graph_store.close()


@app.command("obsidian-status")
@app.command("vault-status")
def vault_status() -> None:
    """Show vault and index status."""
    settings = get_settings()

    if not settings.obsidian_vault_path:
        console.print("[red]Error: OBSIDIAN_VAULT_PATH not set[/red]")
        raise typer.Exit(1)

    from agent_kernel.tools.builtin.obsidian import ObsidianVault

    vault = ObsidianVault(settings.obsidian_vault_path)

    # Count vault notes
    vault_notes = vault.list_notes(recursive=True)
    vault_count = len(vault_notes)

    data_dir = get_data_dir()

    # Count indexed notes
    doc_db = data_dir / "documents" / "documents.db"
    indexed_count = 0
    if doc_db.exists():
        doc_store = SQLiteDocumentStore(doc_db)
        # Query count - simple sync query
        import sqlite3

        with sqlite3.connect(str(doc_db)) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE doc_id LIKE 'obsidian:%'"
            )
            indexed_count = cursor.fetchone()[0]
        doc_store.close()

    # Display status
    console.print(Panel(
        f"[bold]Vault path:[/bold] {settings.obsidian_vault_path}\n"
        f"[bold]Vault exists:[/bold] {vault.vault_path.exists()}\n"
        f"[bold]Notes in vault:[/bold] {vault_count}\n"
        f"[bold]Notes indexed:[/bold] {indexed_count}\n"
        f"[bold]Sync status:[/bold] "
        + ("[green]In sync[/green]" if vault_count == indexed_count
           else f"[yellow]{vault_count - indexed_count} notes to sync[/yellow]"),
        title="Vault Status",
    ))

    # Check for notes without IDs
    notes_without_ids = 0
    for path in vault_notes[:100]:  # Sample first 100
        note = vault.read_note(path)
        if note and "id" not in note.frontmatter:
            notes_without_ids += 1

    if notes_without_ids > 0:
        console.print(
            f"\n[yellow]⚠ ~{notes_without_ids}+ notes without stable IDs[/yellow]"
        )
        console.print("Run [bold]vault-sync[/bold] to inject IDs")


@app.command("task-sync")
def task_sync(
    adapter: str = typer.Argument(
        ..., help="Adapter ID (e.g., 'memory')"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would sync without syncing"
    ),
    tags: str | None = typer.Option(
        None, "--tags", "-t", help="Filter by tags (comma-separated)"
    ),
    include_completed: bool = typer.Option(
        False, "--include-completed", help="Include completed tasks"
    ),
) -> None:
    """Sync tasks to an external system.

    Syncs tasks from the kernel graph to an external task management system.
    Use 'memory' adapter for testing without external API calls.

    Example:
        agent-kernel task-sync memory --dry-run
    """
    console.print(f"[bold blue]Syncing tasks to: {adapter}[/bold blue]")

    try:
        result = asyncio.run(_task_sync_async(
            adapter_id=adapter,
            dry_run=dry_run,
            tags=tags.split(",") if tags else None,
            include_completed=include_completed,
        ))

        # Display results
        console.print(Panel(
            f"[bold]Total tasks:[/bold] {result.total_tasks}\n"
            f"[green]Created:[/green] {result.created}\n"
            f"[yellow]Updated:[/yellow] {result.updated}\n"
            f"[blue]Completed:[/blue] {result.completed}\n"
            f"[dim]Skipped:[/dim] {result.skipped}\n"
            f"[red]Failed:[/red] {result.failed}\n"
            f"[cyan]Pending approval:[/cyan] {result.pending_approval}",
            title="Task Sync Complete" if not dry_run else "Task Sync (Dry Run)",
        ))

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


async def _task_sync_async(  # noqa: ANN202
    adapter_id: str,
    dry_run: bool,
    tags: list[str] | None,
    include_completed: bool,
):
    """Run task sync asynchronously. Returns SyncSummary."""
    from agent_kernel.integrations.task_sync import MemoryTaskAdapter  # noqa: PLC0415
    from agent_kernel.integrations.task_sync_service import (  # noqa: PLC0415
        TaskSyncConfig,
        TaskSyncService,
    )

    data_dir = get_data_dir()

    # Initialize graph store
    graph_store = SQLiteGraphStore(data_dir / "graph" / "graph.db")

    # Create sync service
    sync_service = TaskSyncService(graph_store=graph_store)

    # Register available adapters
    if adapter_id == "memory":
        sync_service.register_adapter(MemoryTaskAdapter())
    else:
        # For real adapters, they would be loaded from configuration
        msg = f"Adapter '{adapter_id}' not configured. Available: memory"
        raise ValueError(msg)

    # Create config
    config = TaskSyncConfig(
        tags=tags,
        include_completed=include_completed,
        dry_run=dry_run,
    )

    try:
        return await sync_service.sync_to_adapter(adapter_id, config)
    finally:
        graph_store.close()


@app.command("calendar-import")
def calendar_import(
    adapter: str = typer.Argument(
        ..., help="Adapter ID (e.g., 'memory')"
    ),
    days_ahead: int = typer.Option(
        30, "--days-ahead", "-a", help="Days ahead to import"
    ),
    days_back: int = typer.Option(
        7, "--days-back", "-b", help="Days back to import"
    ),
    calendar_id: str | None = typer.Option(
        None, "--calendar", "-c", help="Specific calendar ID"
    ),
) -> None:
    """Import calendar events from an external system.

    Imports calendar events from an external calendar into the kernel graph.
    This is a read-only operation that does not require approval.

    Example:
        agent-kernel calendar-import memory
    """
    console.print(f"[bold blue]Importing calendar from: {adapter}[/bold blue]")

    try:
        result = asyncio.run(_calendar_import_async(
            adapter_id=adapter,
            days_ahead=days_ahead,
            days_back=days_back,
            calendar_id=calendar_id,
        ))

        # Display results
        console.print(Panel(
            f"[bold]Total events:[/bold] {result.total_events}\n"
            f"[green]Created:[/green] {result.created}\n"
            f"[yellow]Updated:[/yellow] {result.updated}\n"
            f"[red]Failed:[/red] {result.failed}",
            title="Calendar Import Complete",
        ))

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


async def _calendar_import_async(  # noqa: ANN202
    adapter_id: str,
    days_ahead: int,
    days_back: int,
    calendar_id: str | None,
):
    """Run calendar import asynchronously. Returns CalendarSyncSummary."""
    from agent_kernel.integrations.calendar_sync import (  # noqa: PLC0415
        MemoryCalendarAdapter,
    )
    from agent_kernel.integrations.calendar_sync_service import (  # noqa: PLC0415
        CalendarSyncConfig,
        CalendarSyncService,
    )

    data_dir = get_data_dir()

    # Initialize graph store
    graph_store = SQLiteGraphStore(data_dir / "graph" / "graph.db")

    # Create sync service
    sync_service = CalendarSyncService(graph_store=graph_store)

    # Register available adapters
    if adapter_id == "memory":
        sync_service.register_adapter(MemoryCalendarAdapter())
    else:
        # For real adapters, they would be loaded from configuration
        msg = f"Adapter '{adapter_id}' not configured. Available: memory"
        raise ValueError(msg)

    # Create config
    config = CalendarSyncConfig(
        days_ahead=days_ahead,
        days_back=days_back,
        calendar_ids=[calendar_id] if calendar_id else None,
    )

    try:
        return await sync_service.import_events(adapter_id, config)
    finally:
        graph_store.close()


# =============================================================================
# Context Retrieval Commands (v1.0.2)
# =============================================================================


@app.command("list-context-packs")
def list_context_packs() -> None:
    """List configured context packs.

    Shows all context packs loaded from configs/context_packs/.
    """
    settings = get_settings()
    packs_dir = settings.configs_dir / "context_packs"

    if not packs_dir.exists():
        console.print("[yellow]No context_packs directory found.[/yellow]")
        return

    from agent_kernel.context.pack_resolver import ContextPackResolver

    resolver = ContextPackResolver(config_dir=packs_dir)
    packs = resolver.list_packs()

    if not packs:
        console.print("[yellow]No context packs found.[/yellow]")
        return

    table = Table(title=f"Context Packs ({len(packs)})")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Priority", style="green")
    table.add_column("Policy", style="yellow")
    table.add_column("Refs", style="magenta")

    for pack in sorted(packs, key=lambda p: p.priority):
        table.add_row(
            pack.pack_id,
            pack.name,
            str(pack.priority),
            pack.include_policy,
            str(len(pack.refs)),
        )

    console.print(table)


@app.command("show-context-pack")
def show_context_pack(
    pack_id: str = typer.Argument(..., help="Context pack ID"),
) -> None:
    """Show details of a specific context pack."""
    settings = get_settings()
    packs_dir = settings.configs_dir / "context_packs"

    from agent_kernel.context.pack_resolver import ContextPackResolver

    resolver = ContextPackResolver(config_dir=packs_dir if packs_dir.exists() else None)
    pack = resolver.get_pack(pack_id)

    if pack is None:
        console.print(f"[red]Context pack not found: {pack_id}[/red]")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold]ID:[/bold] {pack.pack_id}\n"
        f"[bold]Name:[/bold] {pack.name}\n"
        f"[bold]Priority:[/bold] {pack.priority}\n"
        f"[bold]Policy:[/bold] {pack.include_policy}\n"
        f"[bold]Description:[/bold] {pack.description or 'N/A'}",
        title="Context Pack",
    ))

    # Show selectors
    if pack.selectors:
        sel_table = Table(title="Selectors")
        sel_table.add_column("Vault", style="cyan")
        sel_table.add_column("Project", style="green")
        sel_table.add_column("Workflow", style="yellow")
        sel_table.add_column("Path Globs", style="magenta")

        for sel in pack.selectors:
            sel_table.add_row(
                sel.vault_id or "-",
                sel.project_id or "-",
                sel.workflow_id or "-",
                ", ".join(sel.path_globs) if sel.path_globs else "-",
            )

        console.print(sel_table)

    # Show refs
    if pack.refs:
        ref_table = Table(title="References")
        ref_table.add_column("Type", style="cyan")
        ref_table.add_column("ID", style="green")
        ref_table.add_column("URI", style="dim")

        for ref in pack.refs:
            ref_table.add_row(
                ref.ref_type.value,
                ref.ref_id,
                ref.uri or "-",
            )

        console.print(ref_table)


@app.command("list-sources")
def list_sources() -> None:
    """List configured source descriptors.

    Shows all source descriptors loaded from configs/sources/.
    """
    settings = get_settings()
    sources_dir = settings.configs_dir / "sources"

    if not sources_dir.exists():
        console.print("[yellow]No sources directory found.[/yellow]")
        return

    from agent_kernel.context.source_registry import SourceRegistry

    registry = SourceRegistry(config_dir=sources_dir)
    sources = registry.list_sources()

    if not sources:
        console.print("[yellow]No source descriptors found.[/yellow]")
        return

    table = Table(title=f"Source Descriptors ({len(sources)})")
    table.add_column("ID", style="cyan")
    table.add_column("Description", style="white", max_width=40)
    table.add_column("Fields", style="green")
    table.add_column("Live Fetch", style="yellow")

    for source in sources:
        table.add_row(
            source.source_id,
            source.description[:40] + ("..." if len(source.description) > 40 else ""),
            str(len(source.fields)),
            "Yes" if source.constraints.requires_live_fetch else "No",
        )

    console.print(table)


@app.command("show-source")
def show_source(
    source_id: str = typer.Argument(..., help="Source ID"),
) -> None:
    """Show details of a specific source descriptor."""
    settings = get_settings()
    sources_dir = settings.configs_dir / "sources"

    from agent_kernel.context.source_registry import SourceRegistry

    registry = SourceRegistry(config_dir=sources_dir if sources_dir.exists() else None)
    source = registry.get(source_id)

    if source is None:
        console.print(f"[red]Source not found: {source_id}[/red]")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold]ID:[/bold] {source.source_id}\n"
        f"[bold]Description:[/bold] {source.description}\n"
        f"[bold]Can Store Text:[/bold] {source.constraints.can_store_text}\n"
        f"[bold]Requires Live Fetch:[/bold] {source.constraints.requires_live_fetch}\n"
        f"[bold]Max Retention:[/bold] {source.constraints.max_retention_days or 'Unlimited'} days",
        title="Source Descriptor",
    ))

    # Show fields
    if source.fields:
        field_table = Table(title=f"Fields ({len(source.fields)})")
        field_table.add_column("Name", style="cyan")
        field_table.add_column("Type", style="green")
        field_table.add_column("Allowed Ops", style="yellow", max_width=30)
        field_table.add_column("Description", style="dim", max_width=30)

        for field in source.fields:
            field_table.add_row(
                field.name,
                field.type,
                ", ".join(field.allowed_ops[:5]) + ("..." if len(field.allowed_ops) > 5 else ""),
                (field.description or "")[:30] + ("..." if field.description and len(field.description) > 30 else ""),
            )

        console.print(field_table)


@app.command("explain-retrieval")
def explain_retrieval(
    packet_id: str = typer.Argument(..., help="ContextPacket ID or trace ID"),
) -> None:
    """Explain how context was retrieved for a packet.

    Shows the retrieval plan, gates run, and selection details.
    """
    data_dir = get_data_dir()
    trace_db = data_dir / "traces" / "traces.db"

    if not trace_db.exists():
        console.print("[red]No traces database found.[/red]")
        raise typer.Exit(1)

    trace_store = SQLiteTraceSink(trace_db)

    try:
        # Find trace by packet_id or trace_id prefix
        traces = trace_store.list_traces(limit=100)
        matching = [
            t for t in traces
            if t.trace_id.startswith(packet_id) or
            t.context_packet_id.startswith(packet_id)
        ]

        if not matching:
            console.print(f"[red]No trace found for: {packet_id}[/red]")
            raise typer.Exit(1)

        trace = matching[0]

        # Display retrieval information
        console.print(Panel(
            f"[bold]Trace ID:[/bold] {trace.trace_id}\n"
            f"[bold]Packet ID:[/bold] {trace.context_packet_id}\n"
            f"[bold]Intent:[/bold] {trace.intent}",
            title="Retrieval Explanation",
        ))

        # Show retrieval report from trace
        # Note: The full retrieval details are in context_packet which
        # may need to be reconstructed from trace data
        console.print(Panel(
            "[dim]Retrieval details are logged in the context packet.\n"
            "Use show-trace for full trace details.[/dim]",
            title="Retrieval Details",
        ))

    finally:
        trace_store.close()


@app.command("list-adapters")
def list_adapters() -> None:
    """List available sync adapters.

    Shows all registered adapters for task and calendar sync.
    """
    # Create table for task adapters
    task_table = Table(title="Task Sync Adapters")
    task_table.add_column("ID", style="cyan")
    task_table.add_column("Name", style="green")
    task_table.add_column("Requires Approval", style="yellow")
    task_table.add_column("Status", style="dim")

    # Built-in adapters
    task_table.add_row("memory", "In-Memory Tasks", "No", "[green]Available[/green]")
    task_table.add_row(
        "linear", "Linear Issues", "Yes", "[dim]Not configured[/dim]"
    )

    console.print(task_table)
    console.print()

    # Create table for calendar adapters
    cal_table = Table(title="Calendar Sync Adapters")
    cal_table.add_column("ID", style="cyan")
    cal_table.add_column("Name", style="green")
    cal_table.add_column("Requires Approval", style="yellow")
    cal_table.add_column("Status", style="dim")

    cal_table.add_row(
        "memory", "In-Memory Calendar", "No", "[green]Available[/green]"
    )
    cal_table.add_row(
        "google", "Google Calendar", "Yes", "[dim]Not configured[/dim]"
    )
    cal_table.add_row(
        "outlook", "Outlook Calendar", "Yes", "[dim]Not configured[/dim]"
    )

    console.print(cal_table)


# ============================================================================
# v1.0.3: Thinking Policy Commands
# ============================================================================


@app.command("show-thinking-config")
def show_thinking_config(
    agent_id: str = typer.Argument(..., help="Agent profile ID"),
) -> None:
    """Show thinking configuration for an agent.

    Displays the thinking policy tiers, retrieval settings,
    verification options, and escalation configuration.
    """
    settings = get_settings()
    agent_path = settings.configs_dir / "agents" / f"{agent_id}.yaml"

    if not agent_path.exists():
        console.print(f"[red]Agent profile not found: {agent_id}[/red]")
        raise typer.Exit(1)

    import yaml

    from agent_kernel.core.schemas import AgentProfile
    from agent_kernel.core.schemas.thinking import STANDARD_THINKING

    with open(agent_path) as f:
        data = yaml.safe_load(f)

    profile = AgentProfile(**data)
    thinking = profile.thinking_config or STANDARD_THINKING

    # Main config
    console.print(Panel(
        f"[bold]Agent:[/bold] {profile.name} ({profile.agent_profile_id})\n"
        f"[bold]Mode:[/bold] {thinking.mode}\n"
        f"[bold]Starting Tier:[/bold] {thinking.escalation.start_tier}\n"
        f"[bold]Max Tier:[/bold] {thinking.escalation.max_tier}\n"
        f"[bold]Escalation Enabled:[/bold] {thinking.escalation.enabled}",
        title="Thinking Configuration",
    ))

    # Tiers table
    tier_table = Table(title="Thinking Tiers")
    tier_table.add_column("Tier", style="cyan")
    tier_table.add_column("Name", style="bold")
    tier_table.add_column("Model", style="green")
    tier_table.add_column("Effort", style="yellow")
    tier_table.add_column("Critic", style="magenta")
    tier_table.add_column("Max Tokens", style="dim")

    for tier_num in sorted(thinking.tiers.keys()):
        tier = thinking.tiers[tier_num]
        tier_table.add_row(
            str(tier_num),
            tier.name,
            tier.model,
            tier.reasoning_effort,
            "✓" if tier.use_critic else "-",
            str(tier.max_tokens),
        )

    console.print(tier_table)

    # Retrieval config
    ret = thinking.retrieval
    console.print(Panel(
        f"[bold]Semantic Search:[/bold] {ret.semantic_search}\n"
        f"[bold]Keyword Search:[/bold] {ret.keyword_search}\n"
        f"[bold]Graph Expansion:[/bold] {ret.graph_expansion} (hops: {ret.graph_expansion_hops})\n"
        f"[bold]Recency Boost:[/bold] {ret.recency_boost} (days: {ret.recency_days})\n"
        f"[bold]Iterative Retrieval:[/bold] {ret.iterative_retrieval}",
        title="Retrieval Settings",
    ))

    # Escalation triggers
    triggers = ", ".join(thinking.escalation.triggers) if thinking.escalation.triggers else "None"
    console.print(Panel(
        f"[bold]Triggers:[/bold] {triggers}\n"
        f"[bold]Confidence Threshold:[/bold] {thinking.escalation.confidence_threshold}\n"
        f"[bold]Max Escalations:[/bold] {thinking.escalation.max_escalations}\n"
        f"[bold]Approval Required:[/bold] {thinking.escalation.require_approval_to_escalate}",
        title="Escalation Policy",
    ))


@app.command("list-thinking-presets")
def list_thinking_presets() -> None:
    """List available thinking configuration presets.

    Shows the built-in STANDARD, DEEP, and ADAPTIVE presets.
    """
    from agent_kernel.core.schemas.thinking import (
        ADAPTIVE_THINKING,
        DEEP_THINKING,
        STANDARD_THINKING,
    )

    table = Table(title="Thinking Presets")
    table.add_column("Preset", style="cyan")
    table.add_column("Mode", style="bold")
    table.add_column("Start Tier", style="green")
    table.add_column("Max Tier", style="yellow")
    table.add_column("Escalation", style="magenta")
    table.add_column("Critic", style="dim")

    presets = [
        ("STANDARD", STANDARD_THINKING),
        ("DEEP", DEEP_THINKING),
        ("ADAPTIVE", ADAPTIVE_THINKING),
    ]

    for name, config in presets:
        table.add_row(
            name,
            config.mode,
            str(config.escalation.start_tier),
            str(config.escalation.max_tier),
            "Yes" if config.escalation.enabled else "No",
            "Yes" if config.verification.use_critic else "No",
        )

    console.print(table)

    console.print("\n[dim]Use these presets in agent configs via:[/dim]")
    console.print('[cyan]thinking_config: DEEP_THINKING[/cyan]')


@app.command("thinking-stats")
def thinking_stats(
    workflow_id: str | None = typer.Option(
        None, "--workflow", "-w", help="Filter by workflow ID"
    ),
    since_hours: int = typer.Option(
        168, "--since-hours", help="Lookback period in hours (default 168 = 7 days)"
    ),
    agent_profile_id: str | None = typer.Option(
        None, "--agent", "-a", help="Filter by agent profile ID"
    ),
    format: OutputFormat = typer.Option(OutputFormat.TEXT, "--format", help="Output format: text or json"),
) -> None:
    """Show thinking policy metrics from trace history.

    Displays tier usage distribution, escalation rates, gate failures,
    critic utilization, model success rates, and cost per workflow.

    Examples:
        agent-kernel thinking-stats
        agent-kernel thinking-stats --workflow daily_checkin
        agent-kernel thinking-stats --since-hours 720
    """
    import json as json_lib
    from datetime import datetime, timedelta, timezone

    from agent_kernel.engine.thinking_metrics import compute_thinking_metrics
    from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink

    data_dir = get_data_dir()
    trace_db = data_dir / "traces" / "traces.db"

    if not trace_db.exists():
        if format == OutputFormat.JSON:
            print(json_lib.dumps({"error": "no_trace_database"}))
        else:
            console.print("[yellow]No trace database found. Run some workflows first.[/yellow]")
        return

    trace_store = SQLiteTraceSink(trace_db)
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    try:
        traces = trace_store.list_traces(
            since=since,
            workflow_id=workflow_id,
            agent_profile_id=agent_profile_id,
            limit=1000,
        )
    finally:
        trace_store.close()

    if not traces:
        if format == OutputFormat.JSON:
            print(json_lib.dumps({"error": "no_traces_found"}))
        else:
            console.print("[yellow]No traces found for the specified filters.[/yellow]")
        return

    metrics = compute_thinking_metrics(traces)

    if format == OutputFormat.JSON:
        print(json_lib.dumps({
            "total_traces": metrics.total_traces,
            "traces_with_reasoning": metrics.traces_with_reasoning,
            "escalation_count": metrics.escalation_count,
            "escalation_rate": metrics.escalation_rate,
            "critic_utilization_rate": metrics.critic_utilization_rate,
            "tier_distribution": {str(k): v for k, v in metrics.tier_distribution.items()},
            "tokens_per_tier": {str(k): v for k, v in metrics.tokens_per_tier.items()},
            "gate_failure_counts": metrics.gate_failure_counts,
            "model_success_rates": metrics.model_success_rates,
            "cost_per_workflow": metrics.cost_per_workflow,
        }, indent=2, default=str))
        return

    # Overview
    console.print(Panel(
        f"[bold]Total Traces:[/bold] {metrics.total_traces}\n"
        f"[bold]With Reasoning:[/bold] {metrics.traces_with_reasoning}\n"
        f"[bold]Escalation Rate:[/bold] {metrics.escalation_rate:.1%}\n"
        f"[bold]Critic Utilization:[/bold] {metrics.critic_utilization_rate:.1%}",
        title="Thinking Metrics Overview",
    ))

    # Tier distribution
    if metrics.tier_distribution:
        tier_table = Table(title="Tier Distribution")
        tier_table.add_column("Tier", style="cyan")
        tier_table.add_column("Count", style="bold")
        tier_table.add_column("Percentage")
        tier_table.add_column("Avg Tokens")

        for tier in sorted(metrics.tier_distribution.keys()):
            count = metrics.tier_distribution[tier]
            pct = count / metrics.traces_with_reasoning * 100 if metrics.traces_with_reasoning > 0 else 0
            avg_tokens = metrics.tokens_per_tier.get(tier, 0)
            tier_table.add_row(
                str(tier),
                str(count),
                f"{pct:.0f}%",
                f"{avg_tokens:.0f}" if avg_tokens > 0 else "-",
            )
        console.print(tier_table)

    # Gate failures
    if metrics.gate_failure_counts:
        gate_table = Table(title="Gate Failures")
        gate_table.add_column("Gate", style="red")
        gate_table.add_column("Count", style="bold")

        for gate, count in sorted(
            metrics.gate_failure_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            gate_table.add_row(gate, str(count))
        console.print(gate_table)

    # Model success rates
    if metrics.model_success_rates:
        model_table = Table(title="Model Success Rates")
        model_table.add_column("Model", style="cyan")
        model_table.add_column("Success Rate", style="bold")

        for model_id, rate in sorted(
            metrics.model_success_rates.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            color = "green" if rate >= 0.9 else "yellow" if rate >= 0.7 else "red"
            model_table.add_row(model_id, f"[{color}]{rate:.1%}[/{color}]")
        console.print(model_table)

    # Cost per workflow
    if metrics.cost_per_workflow:
        cost_table = Table(title="Cost per Workflow")
        cost_table.add_column("Workflow", style="cyan")
        cost_table.add_column("Total Cost (USD)", style="bold")

        for wf_id, cost in sorted(
            metrics.cost_per_workflow.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            cost_table.add_row(wf_id, f"${cost:.4f}")
        console.print(cost_table)


@app.command("run-workflow-thinking")
def run_workflow_thinking(
    workflow_id: str = typer.Argument(..., help="Workflow ID to run"),
    intent: str = typer.Option(None, "--intent", "-i", help="Override intent"),
    project: str = typer.Option(None, "--project", "-p", help="Project scope"),
    approve: bool = typer.Option(
        False, "--approve", help="Auto-approve required actions"
    ),
) -> None:
    """Run a workflow with thinking policy and auto-escalation.

    This uses the agent's ThinkingConfig to enable adaptive reasoning
    with automatic tier escalation when quality gates fail.
    """
    console.print(f"[bold blue]Running workflow with thinking: {workflow_id}[/bold blue]")

    async def _run() -> None:
        data_dir = get_data_dir()

        # Initialize stores
        event_log = SQLiteEventLog(data_dir / "events" / "events.db")
        sqlite_sink = SQLiteTraceSink(data_dir / "traces" / "traces.db")
        jsonl_sink = JSONLTraceSink(data_dir / "traces" / "traces.jsonl")
        trace_store = MultiSinkTraceStore(sqlite_sink, [jsonl_sink])
        doc_store = SQLiteDocumentStore(data_dir / "documents" / "documents.db")
        vector_store = create_vector_store(data_dir / "vectors" / "vectors")
        graph_store = SQLiteGraphStore(data_dir / "graph" / "graph.db")
        experience_store = SQLiteExperienceStore(data_dir / "experience" / "experience.db")

        try:
            # Initialize components
            registry = CapabilityRegistry()
            settings = get_settings()
            caps_dir = settings.configs_dir / "capabilities"
            if caps_dir.exists():
                registry.load_from_directory(caps_dir)

            # Configure retry for tool execution
            retry_config = None
            if settings.tool_broker_retry_enabled:
                retry_config = RetryConfig(
                    max_retries=settings.tool_broker_retry_max_retries,
                    base_delay_ms=settings.tool_broker_retry_base_delay_ms,
                    max_delay_ms=settings.tool_broker_retry_max_delay_ms,
                )

            # Set up adaptive timeout manager
            timeout_manager = AdaptiveTimeoutManager(trace_store=trace_store)

            broker = ToolBroker(
                registry=registry,
                retry_config=retry_config,
                enable_circuit_breaker=settings.tool_broker_circuit_breaker_enabled,
                timeout_manager=timeout_manager,
            )
            register_builtin_tools(broker, doc_store, graph_store)
            _configure_library_tools(broker, settings.configs_dir)
            _configure_skill_scripts(broker, registry, settings)
            await _configure_mcp_adapter(broker, settings.configs_dir)

            assembler = ContextAssembler(
                document_store=doc_store,
                vector_store=vector_store,
                graph_store=graph_store,
                skills_dir=settings.skills_dir,
                packs_config_dir=settings.configs_dir / "context_packs",
                sources_config_dir=settings.configs_dir / "sources",
                experience_store=experience_store,
            )

            executor = DeterministicExecutor(
                broker=broker,
                trace_store=trace_store,
            )

            # Register engine (LLM-backed)
            from agent_kernel.services.llm import create_llm_service

            try:
                llm_service = create_llm_service(
                    provider=settings.default_llm_provider,
                    api_key=(
                        settings.openai_api_key
                        if settings.default_llm_provider == "openai"
                        else settings.anthropic_api_key
                    )
                    or None,
                    model=(
                        settings.openai_model
                        if settings.default_llm_provider == "openai"
                        else settings.anthropic_model
                    )
                    or None,
                    base_url=getattr(settings, "openai_base_url", None),
                )
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                raise

            # Set up feedback loop components
            cost_anomaly_detector = CostAnomalyDetector(
                event_log=event_log,
                trace_store=trace_store,
            )
            experience_miner = ExperienceMiner(
                experience_store=experience_store,
                event_log=event_log,
                llm_service=llm_service,
            )

            workflow_store = SQLiteWorkflowRunStore(data_dir / "workflows" / "workflows.db")
            runner = WorkflowRunner(
                context_assembler=assembler,
                executor=executor,
                event_log=event_log,
                configs_dir=settings.configs_dir,
                trace_store=trace_store,
                workflow_store=workflow_store,
                cost_anomaly_detector=cost_anomaly_detector,
                experience_miner=experience_miner,
            )

            engine = CustomEngine(llm_service=llm_service, capability_registry=registry)
            runner.register_engine(engine)

            # Run with thinking
            approval_tokens = {"*": "auto"} if approve else None
            result = await runner.run_with_thinking(
                workflow_id=workflow_id,
                intent=intent,
                project_id=project,
                approval_tokens=approval_tokens,
            )

            if result.success:
                # Show reasoning metadata if available
                if result.trace and result.trace.reasoning:
                    reasoning = result.trace.reasoning
                    console.print(Panel(
                        f"[bold]Initial Tier:[/bold] {reasoning.initial_tier}\n"
                        f"[bold]Final Tier:[/bold] {reasoning.final_tier} ({reasoning.tier_name})\n"
                        f"[bold]Attempts:[/bold] {reasoning.total_attempts}\n"
                        f"[bold]Escalations:[/bold] {reasoning.escalation_count}\n"
                        f"[bold]Critic Used:[/bold] {reasoning.critic_used}",
                        title="Thinking Summary",
                    ))

                console.print(Panel(
                    f"Workflow completed successfully!\n"
                    f"Run ID: {result.run_id}\n"
                    f"Trace ID: {result.trace.trace_id if result.trace else 'N/A'}",
                    title="[green]Success[/green]",
                ))
            else:
                console.print(Panel(
                    f"Workflow failed: {result.error}\n"
                    f"Step: {result.step_failed or 'N/A'}",
                    title="[red]Error[/red]",
                ))

        finally:
            event_log.close()
            sqlite_sink.close()
            jsonl_sink.close()
            doc_store.close()
            vector_store.close()
            graph_store.close()
            workflow_store.close()
            if hasattr(experience_store, "close"):
                experience_store.close()

    asyncio.run(_run())


@app.command("obsidian-search")
@app.command("search")
def search_notes(
    query: str = typer.Argument(..., help="Search query"),
    strategy: str = typer.Option(
        "hierarchical",
        "--strategy",
        "-s",
        help="Search strategy: hierarchical, hybrid, vector, keyword, graph",
    ),
    limit: int = typer.Option(10, "--limit", "-l", help="Max results"),
    show_chunks: bool = typer.Option(
        False, "--chunks", "-c", help="Show chunk-level results"
    ),
) -> None:
    """Search notes using hybrid search.

    Combines vector search (semantic), keyword search (FTS), and graph expansion.

    Strategies:
        hierarchical: Summary → Graph → Chunks (recommended)
        hybrid: All sources with score fusion
        vector: Semantic similarity only
        keyword: Keyword/FTS only
        graph: Relationship-based

    Example:
        agent-kernel search "how does authentication work"
        agent-kernel search "project architecture" --strategy vector
    """
    console.print(f"[bold blue]Searching: {query}[/bold blue]")
    console.print(f"[cyan]Strategy: {strategy}[/cyan]")

    try:
        result = asyncio.run(_search_async(
            query=query,
            strategy=strategy,
            limit=limit,
            show_chunks=show_chunks,
        ))

        if not result["results"]:
            console.print("[yellow]No results found[/yellow]")
            return

        console.print(f"\n[bold]Found {len(result['results'])} results[/bold]")
        console.print(
            f"[dim]Vector: {result['vector_count']}, "
            f"Keyword: {result['keyword_count']}, "
            f"Graph: {result['graph_count']} | "
            f"{result['duration_ms']}ms[/dim]\n"
        )

        for i, r in enumerate(result["results"], 1):
            score_color = "green" if r["score"] > 0.7 else "yellow" if r["score"] > 0.4 else "dim"
            embed_type = f" [{r['embedding_type']}]" if r.get("embedding_type") else ""

            console.print(
                f"[{score_color}]{i}. [{r['source']}]{embed_type}[/{score_color}] "
                f"[bold]{r['title'] or r['path']}[/bold] "
                f"[dim](score: {r['score']:.2f})[/dim]"
            )
            if r.get("text"):
                console.print(f"   [dim]{r['text'][:100]}...[/dim]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("search_failed")
        raise typer.Exit(1) from None


async def _search_async(
    query: str,
    strategy: str,
    limit: int,
    show_chunks: bool,
) -> dict:
    """Run hybrid search asynchronously."""
    from agent_kernel.services.hybrid_search import (
        HybridSearchConfig,
        HybridSearchService,
        SearchStrategy,
    )

    settings = get_settings()
    data_dir = get_data_dir()

    # Initialize stores
    document_store = SQLiteDocumentStore(data_dir / "documents" / "documents.db")
    vector_store = create_vector_store(data_dir / "vectors" / "vectors")
    graph_store = SQLiteGraphStore(data_dir / "graph" / "graph.db")

    # Initialize embedding service
    embedding_service = None
    if settings.openai_api_key:
        from agent_kernel.services.embedding import OpenAIEmbeddingService

        embedding_service = OpenAIEmbeddingService(
            api_key=settings.openai_api_key,
            model=settings.embedding_model,
        )

    # Create search service
    search_service = HybridSearchService(
        document_store=document_store,
        vector_store=vector_store,
        graph_store=graph_store,
        embedding_service=embedding_service,
    )

    # Parse strategy
    strategy_map = {
        "hierarchical": SearchStrategy.HIERARCHICAL,
        "hybrid": SearchStrategy.HYBRID,
        "vector": SearchStrategy.VECTOR_ONLY,
        "keyword": SearchStrategy.KEYWORD_ONLY,
        "graph": SearchStrategy.GRAPH_ONLY,
    }
    search_strategy = strategy_map.get(strategy, SearchStrategy.HIERARCHICAL)

    config = HybridSearchConfig(
        strategy=search_strategy,
        max_results=limit,
        embedding_type_filter="chunk" if show_chunks else None,
    )

    try:
        result = await search_service.search(query, config)

        return {
            "results": [
                {
                    "item_id": r.item_id,
                    "note_id": r.note_id,
                    "score": r.score,
                    "source": r.source,
                    "embedding_type": r.embedding_type,
                    "text": r.text,
                    "path": r.path,
                    "title": r.title,
                }
                for r in result.results
            ],
            "vector_count": result.vector_count,
            "keyword_count": result.keyword_count,
            "graph_count": result.graph_count,
            "duration_ms": result.duration_ms,
        }
    finally:
        document_store.close()
        vector_store.close()
        graph_store.close()


# =============================================================================
# Experience Memory Commands (v1.0.4)
# =============================================================================


@app.command("rate-trace")
def rate_trace(
    trace_id: str = typer.Argument(..., help="Trace ID to rate"),
    label: str = typer.Option(
        "success", "--label", "-l", 
        help="Outcome label: success, partial, failure, regression"
    ),
    rating: int | None = typer.Option(
        None, "--rating", "-r", help="Rating 1-5"
    ),
    category: str | None = typer.Option(
        None, "--category", "-c",
        help="Failure category: misretrieval, misplanning, tool_error, policy_block, hallucination, ux, other"
    ),
    feedback: str | None = typer.Option(
        None, "--feedback", "-f", help="Short feedback text"
    ),
) -> None:
    """Rate a decision trace outcome.
    
    Records user feedback about whether a trace's outcome was good or bad.
    This feedback is used to mine lessons and improve future behavior.
    
    Examples:
        agent-kernel rate-trace trace_01ABC --label success --rating 5
        agent-kernel rate-trace trace_01ABC --label failure --category misretrieval
    """
    from agent_kernel.core.ids import generate_ulid
    from agent_kernel.core.schemas.base import utc_now
    from agent_kernel.core.schemas.experience import (
        FailureCategory,
        OutcomeEvaluation,
        OutcomeLabel,
    )
    from agent_kernel.memory.experience_store import SQLiteExperienceStore

    data_dir = get_data_dir()
    experience_db = data_dir / "experience" / "experience.db"
    experience_db.parent.mkdir(parents=True, exist_ok=True)
    
    store = SQLiteExperienceStore(experience_db)

    # Parse label
    label_map = {
        "success": OutcomeLabel.SUCCESS,
        "partial": OutcomeLabel.PARTIAL,
        "failure": OutcomeLabel.FAILURE,
        "regression": OutcomeLabel.REGRESSION,
        "unknown": OutcomeLabel.UNKNOWN,
    }
    outcome_label = label_map.get(label.lower(), OutcomeLabel.UNKNOWN)

    # Parse category
    failure_cat = None
    if category:
        cat_map = {
            "misretrieval": FailureCategory.MISRETRIEVAL,
            "misplanning": FailureCategory.MISPLANNING,
            "tool_error": FailureCategory.TOOL_ERROR,
            "policy_block": FailureCategory.POLICY_BLOCK,
            "hallucination": FailureCategory.HALLUCINATION,
            "ux": FailureCategory.UX,
            "other": FailureCategory.OTHER,
        }
        failure_cat = cat_map.get(category.lower())

    evaluation = OutcomeEvaluation(
        evaluation_id=f"eval_{generate_ulid()}",
        trace_id=trace_id,
        label=outcome_label,
        rating=rating,
        failure_category=failure_cat,
        feedback=feedback,
        evaluator="user",
        created_at=utc_now(),
    )

    store.put_evaluation(evaluation)
    
    console.print(f"[green]✓[/green] Recorded evaluation for trace {trace_id}")
    console.print(f"  Label: {outcome_label.value}")
    if rating:
        console.print(f"  Rating: {rating}/5")
    if failure_cat:
        console.print(f"  Category: {failure_cat.value}")


@app.command("list-evals")
def list_evals(
    since: str | None = typer.Option(
        None, "--since", help="Show evals since (e.g., 7d, 30d)"
    ),
    label: str | None = typer.Option(
        None, "--label", "-l", help="Filter by label"
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
) -> None:
    """List outcome evaluations.
    
    Examples:
        agent-kernel list-evals --since 7d
        agent-kernel list-evals --label failure
    """
    from datetime import timedelta
    from agent_kernel.core.schemas.experience import OutcomeLabel
    from agent_kernel.memory.experience_store import SQLiteExperienceStore

    data_dir = get_data_dir()
    experience_db = data_dir / "experience" / "experience.db"
    
    if not experience_db.exists():
        console.print("[yellow]No experience data found. Run some traces first.[/yellow]")
        return

    store = SQLiteExperienceStore(experience_db)

    # Parse since
    since_dt = None
    if since:
        from agent_kernel.core.schemas.base import utc_now
        if since.endswith("d"):
            days = int(since[:-1])
            since_dt = utc_now() - timedelta(days=days)

    # Parse label
    label_filter = None
    if label:
        label_map = {
            "success": OutcomeLabel.SUCCESS,
            "partial": OutcomeLabel.PARTIAL,
            "failure": OutcomeLabel.FAILURE,
            "regression": OutcomeLabel.REGRESSION,
        }
        label_filter = label_map.get(label.lower())

    evals = store.list_evaluations(since=since_dt, label=label_filter, limit=limit)

    if not evals:
        console.print("[yellow]No evaluations found[/yellow]")
        return

    table = Table(title="Outcome Evaluations")
    table.add_column("ID", style="cyan", max_width=20)
    table.add_column("Trace", style="dim", max_width=20)
    table.add_column("Label", style="bold")
    table.add_column("Rating")
    table.add_column("Category")
    table.add_column("Created")

    for ev in evals:
        label_style = {
            "success": "green",
            "partial": "yellow",
            "failure": "red",
            "regression": "red bold",
        }.get(ev.label.value, "white")
        
        table.add_row(
            ev.evaluation_id[:20],
            ev.trace_id[:20],
            f"[{label_style}]{ev.label.value}[/{label_style}]",
            str(ev.rating) if ev.rating else "-",
            ev.failure_category.value if ev.failure_category else "-",
            ev.created_at.strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)


@app.command("show-case")
def show_case(
    case_id: str = typer.Argument(..., help="Case ID to show"),
) -> None:
    """Show details of an experience case."""
    from agent_kernel.memory.experience_store import SQLiteExperienceStore

    data_dir = get_data_dir()
    experience_db = data_dir / "experience" / "experience.db"
    
    if not experience_db.exists():
        console.print("[red]Experience database not found[/red]")
        return

    store = SQLiteExperienceStore(experience_db)
    case = store.get_case(case_id)

    if not case:
        console.print(f"[red]Case not found: {case_id}[/red]")
        return

    console.print(Panel(
        f"[bold]Case ID:[/bold] {case.case_id}\n"
        f"[bold]Trace:[/bold] {case.trace_id}\n"
        f"[bold]Intent:[/bold] {case.intent[:100]}...\n"
        f"[bold]Workflow:[/bold] {case.workflow_id or '-'}\n"
        f"[bold]Agent:[/bold] {case.agent_profile_id or '-'}\n"
        f"[bold]Label:[/bold] {case.label.value}\n"
        f"[bold]Rating:[/bold] {case.rating or '-'}\n"
        f"[bold]Capabilities:[/bold] {', '.join(case.capability_names) or '-'}\n"
        f"[bold]Sources:[/bold] {', '.join(case.sources_used) or '-'}\n"
        f"[bold]Entity Types:[/bold] {', '.join(case.entity_types_used) or '-'}",
        title="Experience Case",
    ))

    if case.context_summary:
        console.print(f"\n[bold]Context Summary:[/bold]\n{case.context_summary}")
    if case.plan_summary:
        console.print(f"\n[bold]Plan Summary:[/bold]\n{case.plan_summary}")
    if case.outcome_summary:
        console.print(f"\n[bold]Outcome Summary:[/bold]\n{case.outcome_summary}")


@app.command("list-lessons")
def list_lessons(
    status: str | None = typer.Option(
        None, "--status", "-s", help="Filter by status: active, candidate, deprecated"
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
) -> None:
    """List lessons learned."""
    from agent_kernel.memory.experience_store import SQLiteExperienceStore

    data_dir = get_data_dir()
    experience_db = data_dir / "experience" / "experience.db"
    
    if not experience_db.exists():
        console.print("[yellow]No experience data found[/yellow]")
        return

    store = SQLiteExperienceStore(experience_db)
    lessons = store.list_lessons(status=status, limit=limit)

    if not lessons:
        console.print("[yellow]No lessons found[/yellow]")
        return

    table = Table(title="Lessons Learned")
    table.add_column("ID", style="cyan", max_width=15)
    table.add_column("Title", style="bold", max_width=40)
    table.add_column("Status", style="green")
    table.add_column("Confidence")
    table.add_column("Scope")

    for lesson in lessons:
        status_style = {
            "active": "green",
            "candidate": "yellow",
            "deprecated": "dim",
        }.get(lesson.status, "white")
        
        scope_parts = []
        if lesson.scope.workflow_id:
            scope_parts.append(f"wf:{lesson.scope.workflow_id}")
        if lesson.scope.capability_name:
            scope_parts.append(f"cap:{lesson.scope.capability_name}")
        
        table.add_row(
            lesson.lesson_id[:15],
            lesson.title[:40],
            f"[{status_style}]{lesson.status}[/{status_style}]",
            f"{lesson.confidence:.2f}",
            ", ".join(scope_parts) or "-",
        )

    console.print(table)


# =============================================================================
# Retention Commands (v1.0.4)
# =============================================================================


@app.command("compact-traces")
def compact_traces(
    older_than: str = typer.Option(
        "14d", "--older-than", help="Compact traces older than (e.g., 14d, 30d)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be compacted without doing it"
    ),
) -> None:
    """Compact old traces into experience cases.
    
    Creates ExperienceCase records from old traces for efficient retrieval.
    
    Examples:
        agent-kernel compact-traces --older-than 14d
        agent-kernel compact-traces --dry-run
    """
    from agent_kernel.memory.experience_store import SQLiteExperienceStore
    from agent_kernel.services.retention_jobs import TraceCompactorJob
    from agent_kernel.core.schemas.retention import TraceRetentionPolicy

    data_dir = get_data_dir()
    
    # Parse days
    days = 14
    if older_than.endswith("d"):
        days = int(older_than[:-1])

    console.print(f"[bold blue]Compacting traces older than {days} days[/bold blue]")
    if dry_run:
        console.print("[yellow]Dry run - no changes will be made[/yellow]")

    # Initialize stores
    trace_db = data_dir / "traces" / "traces.db"
    experience_db = data_dir / "experience" / "experience.db"
    experience_db.parent.mkdir(parents=True, exist_ok=True)

    from agent_kernel.tracing.sinks.sqlite_sink import SQLiteTraceSink
    
    trace_store = SQLiteTraceSink(trace_db)
    experience_store = SQLiteExperienceStore(experience_db)

    from agent_kernel.core.schemas.retention import RetentionPolicy
    policy = RetentionPolicy(
        traces=TraceRetentionPolicy(hot_days=days)
    )

    job = TraceCompactorJob(
        trace_store=trace_store,
        experience_store=experience_store,
        policy=policy,
    )

    result = job.run(dry_run=dry_run)

    console.print(Panel(
        f"[bold]Status:[/bold] {result.status}\n"
        f"[bold]Processed:[/bold] {result.items_processed}\n"
        f"[bold]Compacted:[/bold] {result.items_compacted}\n"
        f"[bold]Errors:[/bold] {len(result.errors)}",
        title="Compaction Result",
    ))

    if result.errors:
        for error in result.errors[:5]:
            console.print(f"[red]• {error}[/red]")


@app.command("prune-vectors")
def prune_vectors(
    dry_run: bool = typer.Option(
        True, "--dry-run/--execute", help="Show what would be pruned"
    ),
) -> None:
    """Prune old vector embeddings.
    
    Removes old chunk embeddings while keeping summary embeddings.
    Use --execute to actually delete vectors.
    
    Examples:
        agent-kernel prune-vectors --dry-run
        agent-kernel prune-vectors --execute
    """
    from agent_kernel.services.retention_jobs import VectorPrunerJob

    data_dir = get_data_dir()
    vector_base = data_dir / "vectors" / "vectors"
    vector_db = vector_base.with_suffix(".db")
    vector_lance = vector_base.with_suffix(".lance")

    if not vector_db.exists() and not vector_lance.exists():
        console.print("[yellow]No vector database found[/yellow]")
        return

    console.print("[bold blue]Pruning vector embeddings[/bold blue]")
    if dry_run:
        console.print("[yellow]Dry run - use --execute to delete[/yellow]")

    vector_store = create_vector_store(vector_base)
    job = VectorPrunerJob(vector_store=vector_store)
    result = job.run(dry_run=dry_run)

    console.print(Panel(
        f"[bold]Status:[/bold] {result.status}\n"
        f"[bold]Processed:[/bold] {result.items_processed}\n"
        f"[bold]Would delete:[/bold] {result.items_deleted}",
        title="Vector Pruning Result",
    ))


@app.command("migrate-vectors")
def migrate_vectors(
    keep_backup: bool = typer.Option(
        True, "--keep-backup/--no-backup",
        help="Rename old .db to .db.bak after migration",
    ),
) -> None:
    """Migrate vectors from SQLite to LanceDB.

    Reads all vectors from data/vectors/vectors.db and batch-inserts them
    into a new LanceDB store at data/vectors/vectors.lance.

    Examples:
        agent-kernel migrate-vectors
        agent-kernel migrate-vectors --no-backup
    """
    from agent_kernel.memory.vector_store import LANCEDB_AVAILABLE, LanceDBVectorStore

    if not LANCEDB_AVAILABLE:
        console.print(
            "[bold red]LanceDB is not installed.[/bold red]\n"
            "Install with: pip install lancedb pyarrow pandas"
        )
        raise typer.Exit(1)

    data_dir = get_data_dir()
    sqlite_path = data_dir / "vectors" / "vectors.db"

    if not sqlite_path.exists():
        console.print("[yellow]No SQLite vector database found at "
                       f"{sqlite_path}[/yellow]")
        raise typer.Exit(1)

    lance_path = data_dir / "vectors" / "vectors.lance"
    if lance_path.exists():
        console.print(
            f"[yellow]LanceDB store already exists at {lance_path}[/yellow]\n"
            "Delete it first if you want to re-migrate."
        )
        raise typer.Exit(1)

    console.print("[bold blue]Migrating vectors from SQLite to LanceDB...[/bold blue]")

    # Open source (SQLite)
    src = SQLiteVectorStore(sqlite_path)
    total = src.count()
    console.print(f"  Source vectors: {total}")

    if total == 0:
        console.print("[yellow]No vectors to migrate.[/yellow]")
        src.close()
        return

    # Open destination (LanceDB)
    dst = LanceDBVectorStore(lance_path)

    # Read all vectors from SQLite and batch-insert
    import json
    import numpy as np

    cursor = src._conn.execute(
        "SELECT item_id, vector_blob, dimensions, metadata_json FROM vectors"
    )
    batch: list[tuple[str, list[float], dict | None]] = []
    batch_size = 500
    migrated = 0

    for row in cursor.fetchall():
        item_id = row["item_id"]
        vector = np.frombuffer(row["vector_blob"], dtype=np.float32).tolist()
        metadata = json.loads(row["metadata_json"])
        batch.append((item_id, vector, metadata))

        if len(batch) >= batch_size:
            dst.upsert_batch(batch)
            migrated += len(batch)
            console.print(f"  Migrated {migrated}/{total}...")
            batch = []

    if batch:
        dst.upsert_batch(batch)
        migrated += len(batch)

    console.print(f"  Migrated {migrated}/{total} vectors.")

    # Verify
    dst_count = dst.count()
    console.print(f"  LanceDB count: {dst_count}")

    src.close()
    dst.close()

    if keep_backup:
        backup_path = sqlite_path.with_suffix(".db.bak")
        sqlite_path.rename(backup_path)
        console.print(f"  Old database backed up to: {backup_path}")
    else:
        sqlite_path.unlink()
        console.print(f"  Old database removed: {sqlite_path}")

    console.print("[bold green]Migration complete![/bold green]")


@app.command("retention-status")
def retention_status() -> None:
    """Show retention status and data sizes."""
    data_dir = get_data_dir()

    console.print("[bold blue]Retention Status[/bold blue]\n")

    # Check each store
    stores = [
        ("Traces", data_dir / "traces" / "traces.db"),
        ("Documents", data_dir / "documents" / "documents.db"),
        ("Vectors", data_dir / "vectors" / "vectors.db"),
        ("Graph", data_dir / "graph" / "graph.db"),
        ("Events", data_dir / "events" / "events.db"),
        ("Experience", data_dir / "experience" / "experience.db"),
    ]

    table = Table(title="Data Stores")
    table.add_column("Store", style="cyan")
    table.add_column("Size", style="bold")
    table.add_column("Status")

    for name, path in stores:
        if path.exists():
            size_bytes = path.stat().st_size
            if size_bytes > 1024 * 1024:
                size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
            elif size_bytes > 1024:
                size_str = f"{size_bytes / 1024:.1f} KB"
            else:
                size_str = f"{size_bytes} B"
            status = "[green]✓[/green]"
        else:
            size_str = "-"
            status = "[dim]not created[/dim]"

        table.add_row(name, size_str, status)

    console.print(table)

    # Show retention policy summary
    console.print("\n[bold]Retention Policy:[/bold]")
    console.print("  Traces: 14d hot → 90d warm → 365d cold")
    console.print("  Chunks: 180d retention")
    console.print("  Summaries: 3650d retention (~10 years)")
    console.print("  Auto edges: prune if <0.55 confidence AND >365d old")


@app.command("resource-extract")
def resource_extract(
    path: str = typer.Argument(..., help="Path to resource file or note containing resource links"),
    output_folder: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Target folder for summary notes (default: Resources/)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview extraction without creating notes",
    ),
    skip_existing: bool = typer.Option(
        True,
        "--skip-existing/--force",
        help="Skip already processed resources",
    ),
) -> None:
    """Extract and summarize linked resources (pptx, pdf, docx, etc.).

    Extracts content from resource files and creates summary notes
    organized by project in your Obsidian vault.

    Can process:
    - A single resource file directly
    - A note file to find and process all linked resources

    Examples:
        agent-kernel resource-extract presentation.pptx
        agent-kernel resource-extract /path/to/meeting-notes.md
        agent-kernel resource-extract document.pdf --output "Projects/Alpha"
    """
    import asyncio
    from pathlib import Path as PathLib

    from agent_kernel.services.resource_extraction import (
        ResourceExtractionService,
        ResourceExtractor,
        ResourceType,
    )
    from agent_kernel.services.llm import get_llm_service
    from agent_kernel.core.config import get_settings

    settings = get_settings()
    file_path = PathLib(path)

    if not file_path.exists():
        console.print(f"[red]File not found: {path}[/red]")
        raise typer.Exit(1)

    async def run_extraction():
        llm_service = get_llm_service()
        extractor = ResourceExtractor()

        # Determine vault path
        vault_path = settings.obsidian_vault_path if settings.obsidian_vault_path else None

        service = ResourceExtractionService(
            llm_service=llm_service,
            vault_path=vault_path,
            extractor=extractor,
        )

        console.print(f"[bold blue]Resource Extraction[/bold blue]\n")

        # Check if it's a note or a resource file
        resource_type = extractor.get_resource_type(file_path)

        if resource_type == ResourceType.MARKDOWN or file_path.suffix.lower() == ".md":
            # Process note to find resource links
            console.print(f"Scanning note for resource links: [cyan]{file_path}[/cyan]\n")

            content = file_path.read_text(encoding="utf-8")
            links = service.find_resource_links(content)

            if not links:
                console.print("[yellow]No resource links found in note[/yellow]")
                return

            console.print(f"Found [bold]{len(links)}[/bold] resource links:\n")
            for link in links:
                console.print(f"  • {link}")

            console.print()

            with console.status("Processing resources..."):
                results = await service.process_note_resources(
                    note_path=file_path,
                    create_notes=not dry_run,
                )

            # Display results
            successful = [r for r in results if r[0].success]
            failed = [r for r in results if not r[0].success]

            console.print(f"\n[bold]Extraction Summary[/bold]")
            console.print(f"  [green]Successful: {len(successful)}[/green]")
            if failed:
                console.print(f"  [red]Failed: {len(failed)}[/red]")

            for extraction, summary, note_path in successful:
                console.print(f"\n[cyan]{extraction.metadata.file_name}[/cyan]")
                if summary:
                    console.print(f"  Title: {summary.title}")
                    if summary.key_points:
                        console.print(f"  Key points: {len(summary.key_points)}")
                    if summary.topics:
                        console.print(f"  Topics: {', '.join(summary.topics)}")
                if note_path and not dry_run:
                    console.print(f"  [green]Note created: {note_path}[/green]")

        else:
            # Process single resource file
            if resource_type == ResourceType.UNKNOWN:
                console.print(f"[red]Unsupported file type: {file_path.suffix}[/red]")
                console.print("Supported types: .pptx, .ppt, .pdf, .docx, .doc, .xlsx, .xls")
                raise typer.Exit(1)

            console.print(f"Processing: [cyan]{file_path}[/cyan]")
            console.print(f"Type: {resource_type.value}\n")

            if skip_existing and service.is_processed(file_path):
                existing_note = service.get_processed_note(file_path)
                console.print(f"[yellow]Already processed. Existing note: {existing_note}[/yellow]")
                console.print("Use --force to reprocess")
                return

            with console.status("Extracting and summarizing..."):
                extraction, summary = await service.extract_and_summarize(file_path)

            if not extraction.success:
                console.print(f"[red]Extraction failed: {extraction.error}[/red]")
                raise typer.Exit(1)

            # Display extraction info
            console.print(f"[green]✓ Content extracted[/green]")
            console.print(f"  Words: ~{extraction.metadata.word_count or 0}")
            if extraction.metadata.page_count:
                console.print(f"  Pages: {extraction.metadata.page_count}")
            if extraction.metadata.slide_count:
                console.print(f"  Slides: {extraction.metadata.slide_count}")

            if summary:
                console.print(f"\n[bold]Summary[/bold]")
                console.print(f"  Title: {summary.title}")
                console.print(f"  Confidence: {summary.confidence:.2f}")

                if summary.summary:
                    # Truncate long summary for display
                    display_summary = summary.summary[:200] + "..." if len(summary.summary) > 200 else summary.summary
                    console.print(f"\n  {display_summary}")

                if summary.key_points:
                    console.print(f"\n[bold]Key Points ({len(summary.key_points)}):[/bold]")
                    for point in summary.key_points[:5]:
                        console.print(f"  • {point}")
                    if len(summary.key_points) > 5:
                        console.print(f"  ... and {len(summary.key_points) - 5} more")

                if summary.topics:
                    console.print(f"\n  Topics: {', '.join(summary.topics)}")

                if summary.suggested_project:
                    console.print(f"  Suggested project: {summary.suggested_project}")

                # Create note if not dry run
                if not dry_run and vault_path:
                    target = output_folder or None
                    note_path = await service.create_summary_note(
                        extraction=extraction,
                        summary=summary,
                        target_folder=target,
                    )
                    if note_path:
                        console.print(f"\n[green]✓ Summary note created: {note_path}[/green]")
                elif dry_run:
                    console.print(f"\n[yellow]Dry-run mode - note not created[/yellow]")
                elif not vault_path:
                    console.print(f"\n[yellow]Vault path not configured - note not created[/yellow]")
                    console.print("Set OBSIDIAN_VAULT_PATH in .env to enable note creation")

    asyncio.run(run_extraction())


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-H", help="Bind address"),
    port: int = typer.Option(8787, "--port", "-p", help="Port to listen on"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload"),
) -> None:
    """Start the Agent Kernel REST API server.

    Wires up all components including bridge endpoints for
    knowledge graph, trace ingestion, and context assembly.
    """
    console.print(
        f"[bold blue]Starting Agent Kernel API on {host}:{port}[/bold blue]"
    )

    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]uvicorn is required: uv pip install uvicorn[/red]"
        )
        raise typer.Exit(1)

    settings = get_settings()
    data_dir = get_data_dir()

    # Initialize stores
    event_log = SQLiteEventLog(data_dir / "events" / "events.db")
    trace_store = SQLiteTraceSink(data_dir / "traces" / "traces.db")
    graph_store = SQLiteGraphStore(data_dir / "graph" / "graph.db")

    # Multi-sink trace store
    jsonl_sink = JSONLTraceSink(data_dir / "traces" / "traces.jsonl")
    multi_trace_store = MultiSinkTraceStore(trace_store, [jsonl_sink])

    # Capability registry
    registry = CapabilityRegistry()
    registry.load_from_directory(settings.configs_dir / "capabilities")

    # Context graph services (for bridge endpoints)
    from agent_kernel.context_graph.ingestion import ContextGraphIngestion
    from agent_kernel.context_graph.query import ContextGraphQueryService

    cg_query = ContextGraphQueryService(graph_store)
    cg_ingestion = ContextGraphIngestion(
        graph_store=graph_store,
        event_log=event_log,
    )

    # Context assembler
    document_store = SQLiteDocumentStore(
        data_dir / "documents" / "documents.db",
    )
    vector_store = create_vector_store(data_dir / "vectors" / "vectors")
    assembler = ContextAssembler(
        document_store=document_store,
        vector_store=vector_store,
        graph_store=graph_store,
        context_graph_query=cg_query,
        skills_dir=settings.skills_dir,
        packs_config_dir=settings.configs_dir / "context_packs",
        sources_config_dir=settings.configs_dir / "sources",
    )

    # Workflow store for persistent approvals
    workflow_store = SQLiteWorkflowRunStore(
        data_dir / "workflows" / "workflows.db"
    )

    # Build WorkflowRunner for REST API workflow execution
    timeout_manager = AdaptiveTimeoutManager(trace_store=multi_trace_store)

    retry_config = None
    if settings.tool_broker_retry_enabled:
        retry_config = RetryConfig(
            max_retries=settings.tool_broker_retry_max_retries,
            base_delay_ms=settings.tool_broker_retry_base_delay_ms,
            max_delay_ms=settings.tool_broker_retry_max_delay_ms,
        )

    broker = ToolBroker(
        registry=registry,
        event_log=event_log,
        retry_config=retry_config,
        enable_circuit_breaker=settings.tool_broker_circuit_breaker_enabled,
        timeout_manager=timeout_manager,
    )
    register_builtin_tools(broker)
    _configure_library_tools(broker, settings.configs_dir)
    _configure_skill_scripts(broker, registry, settings)

    approval_gate = _build_approval_gate(settings, event_log)

    executor = DeterministicExecutor(
        tool_broker=broker,
        trace_store=multi_trace_store,
        approval_gate=approval_gate,
        event_log=event_log,
    )

    from agent_kernel.services.llm import create_llm_service

    try:
        provider = settings.default_llm_provider
        provider_configs = {
            "openai": (settings.openai_api_key, settings.openai_model),
            "anthropic": (settings.anthropic_api_key, settings.anthropic_model),
        }
        api_key, model = provider_configs.get(
            provider, (settings.openai_api_key, settings.openai_model)
        )
        llm_service = create_llm_service(
            provider=provider,
            api_key=api_key or None,
            model=model or None,
            base_url=settings.openai_base_url or None,
        )
    except ValueError as exc:
        console.print(f"[yellow]LLM service unavailable: {exc} — workflow execution disabled[/yellow]")
        llm_service = None

    workflow_runner = None
    if llm_service is not None:
        cost_anomaly_detector = CostAnomalyDetector(
            event_log=event_log,
            trace_store=multi_trace_store,
        )

        workflow_runner = WorkflowRunner(
            context_assembler=assembler,
            executor=executor,
            event_log=event_log,
            configs_dir=settings.configs_dir,
            workflow_store=workflow_store,
            trace_store=multi_trace_store,
            cost_anomaly_detector=cost_anomaly_detector,
        )

        engine = CustomEngine(llm_service=llm_service, capability_registry=registry)
        workflow_runner.register_engine(engine)

    from agent_kernel.api.server import create_app

    # Create app with all dependencies
    api_app = create_app(
        workflow_runner=workflow_runner,
        trace_store=multi_trace_store,
        capability_registry=registry,
        context_graph_query=cg_query,
        context_graph_ingestion=cg_ingestion,
        context_assembler=assembler,
        event_log=event_log,
        workflow_store=workflow_store,
    )

    console.print(
        f"[green]Components wired: graph_store, event_log, trace_store, "
        f"context_graph, context_assembler, workflow_runner, "
        f"{len(registry.list_capabilities())} capabilities[/green]"
    )

    uvicorn.run(api_app, host=host, port=port, reload=reload)


@app.command("health")
def health(
    format: OutputFormat = typer.Option(OutputFormat.TEXT, "--format", help="Output format: text or json"),
) -> None:
    """Check health of all kernel components.

    Probes document store, vector store, graph store, event log,
    trace store, workflow store, experience store, and LLM service.

    Examples:
        agent-kernel health
        agent-kernel health --format json
    """
    from agent_kernel.services.health import HealthChecker, ComponentStatus

    data_dir = get_data_dir()
    settings = get_settings()

    # Build stores that exist
    doc_store = None
    doc_db = data_dir / "documents" / "documents.db"
    if doc_db.exists():
        doc_store = SQLiteDocumentStore(doc_db)

    vec_store = None
    vec_base = data_dir / "vectors" / "vectors"
    if vec_base.with_suffix(".lance").exists() or vec_base.with_suffix(".db").exists():
        vec_store = create_vector_store(vec_base)

    graph_store = None
    graph_db = data_dir / "graph" / "graph.db"
    if graph_db.exists():
        graph_store = SQLiteGraphStore(graph_db)

    event_log = None
    events_db = data_dir / "events" / "events.db"
    if events_db.exists():
        event_log = SQLiteEventLog(events_db)

    trace_store = None
    trace_db = data_dir / "traces" / "traces.db"
    if trace_db.exists():
        trace_store = SQLiteTraceSink(trace_db)

    workflow_store = None
    workflows_db = data_dir / "workflows" / "workflows.db"
    if workflows_db.exists():
        workflow_store = SQLiteWorkflowRunStore(workflows_db)

    experience_store = None
    experience_db = data_dir / "experience" / "experience.db"
    if experience_db.exists():
        from agent_kernel.memory.experience_store import SQLiteExperienceStore
        experience_store = SQLiteExperienceStore(experience_db)

    llm_service = None
    try:
        from agent_kernel.services.llm import create_llm_service
        llm_service = create_llm_service(
            provider=settings.default_llm_provider,
            api_key=(
                settings.openai_api_key
                if settings.default_llm_provider == "openai"
                else settings.anthropic_api_key
            ) or None,
            model=(
                settings.openai_model
                if settings.default_llm_provider == "openai"
                else settings.anthropic_model
            ) or None,
        )
    except Exception:
        pass

    checker = HealthChecker(
        document_store=doc_store,
        vector_store=vec_store,
        graph_store=graph_store,
        event_log=event_log,
        trace_store=trace_store,
        workflow_store=workflow_store,
        experience_store=experience_store,
        llm_service=llm_service,
    )

    result = checker.check_all()

    if format == OutputFormat.JSON:
        import json as json_lib
        print(json_lib.dumps({
            "status": result.status.value,
            "healthy_count": result.healthy_count,
            "total_count": result.total_count,
            "checked_at": result.checked_at.isoformat(),
            "components": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "latency_ms": c.latency_ms,
                    "message": c.message,
                }
                for c in result.components
            ],
        }, indent=2, default=str))
        return

    # Status color
    status_colors = {
        ComponentStatus.HEALTHY: "green",
        ComponentStatus.DEGRADED: "yellow",
        ComponentStatus.UNHEALTHY: "red",
        ComponentStatus.UNCONFIGURED: "dim",
    }

    overall_color = status_colors.get(result.status, "white")
    console.print(Panel(
        f"[bold]Status:[/bold] [{overall_color}]{result.status.value}[/{overall_color}]\n"
        f"[bold]Healthy:[/bold] {result.healthy_count}/{result.total_count} configured components\n"
        f"[bold]Checked at:[/bold] {result.checked_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        title="System Health",
    ))

    table = Table(title="Component Health")
    table.add_column("Component", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Latency (ms)")
    table.add_column("Message")

    for comp in result.components:
        color = status_colors.get(comp.status, "white")
        latency_str = f"{comp.latency_ms:.1f}" if comp.latency_ms is not None else "-"
        table.add_row(
            comp.name,
            f"[{color}]{comp.status.value}[/{color}]",
            latency_str,
            comp.message,
        )

    console.print(table)


@app.command("suggest-policy")
def suggest_policy(
    period_days: int = typer.Option(
        30, "--period", "-p", help="Analysis window in days"
    ),
    min_samples: int = typer.Option(
        5, "--min-samples", help="Minimum samples before recommending"
    ),
) -> None:
    """Analyze approval history and suggest policy changes.

    Examines resolved approval requests to recommend auto-approve
    or blocklist policies based on observed patterns. Advisory only.

    Examples:
        agent-kernel suggest-policy
        agent-kernel suggest-policy --period 90
        agent-kernel suggest-policy --min-samples 10
    """
    from agent_kernel.services.approval_optimizer import ApprovalPolicyOptimizer

    data_dir = get_data_dir()
    workflows_db = data_dir / "workflows" / "workflows.db"

    if not workflows_db.exists():
        console.print("[yellow]No workflow database found. Run some workflows first.[/yellow]")
        return

    workflow_store = SQLiteWorkflowRunStore(workflows_db)

    experience_store = None
    experience_db = data_dir / "experience" / "experience.db"
    if experience_db.exists():
        from agent_kernel.memory.experience_store import SQLiteExperienceStore
        experience_store = SQLiteExperienceStore(experience_db)

    optimizer = ApprovalPolicyOptimizer(
        workflow_store=workflow_store,
        experience_store=experience_store,
        min_samples=min_samples,
    )

    analysis = optimizer.analyze(period_days=period_days)

    console.print(Panel(
        f"[bold]Approvals Analyzed:[/bold] {analysis.analyzed_count}\n"
        f"[bold]Period:[/bold] {analysis.period_days} days\n"
        f"[bold]Recommendations:[/bold] {len(analysis.recommendations)}",
        title="Approval Policy Analysis",
    ))

    if not analysis.recommendations:
        console.print(
            "[dim]No recommendations. Need more approval history "
            f"(min {min_samples} samples per capability).[/dim]"
        )
        return

    table = Table(title="Policy Recommendations")
    table.add_column("Capability", style="cyan")
    table.add_column("Recommendation", style="bold")
    table.add_column("Confidence")
    table.add_column("Evidence")
    table.add_column("Reason")

    for rec in analysis.recommendations:
        rec_color = "green" if rec.recommendation == "auto_approve" else "red"
        conf_color = "green" if rec.confidence >= 0.9 else "yellow"
        evidence_str = (
            f"approved={rec.evidence.get('approved', 0)}, "
            f"denied={rec.evidence.get('denied', 0)}"
        )
        table.add_row(
            rec.capability_name,
            f"[{rec_color}]{rec.recommendation}[/{rec_color}]",
            f"[{conf_color}]{rec.confidence:.0%}[/{conf_color}]",
            evidence_str,
            rec.reason,
        )

    console.print(table)


def _render_validation_result(result: object) -> None:
    """Render a ValidationResult as a Rich table."""
    from agent_kernel.validators.results import CheckStatus  # noqa: PLC0415

    status_colors = {
        CheckStatus.PASS: "green",
        CheckStatus.WARN: "yellow",
        CheckStatus.ERROR: "red",
        CheckStatus.SKIP: "dim",
    }

    table = Table(title=f"Validation: {result.target}")
    table.add_column("Status", style="bold", width=8)
    table.add_column("Check", style="cyan")
    table.add_column("Message")
    table.add_column("Detail", style="dim")

    for check in result.checks:
        color = status_colors.get(check.status, "white")
        table.add_row(
            f"[{color}]{check.status.value}[/{color}]",
            check.name,
            check.message,
            check.detail or "",
        )

    console.print(table)

    if result.error_count:
        console.print(
            f"[red bold]{result.error_count} error(s)[/red bold], "
            f"{result.warn_count} warning(s)"
        )
    elif result.warn_count:
        console.print(
            f"[green]No errors[/green], "
            f"[yellow]{result.warn_count} warning(s)[/yellow]"
        )
    else:
        console.print("[green]All checks passed[/green]")


@app.command("validate-config")
def validate_config() -> None:
    """Validate kernel configuration settings.

    Checks API keys, data paths, store backend consistency,
    debug mode, provider/model match, embedding config, and timezone.

    Examples:
        agent-kernel validate-config
    """
    from agent_kernel.validators.config_validator import ConfigValidator  # noqa: PLC0415

    settings = get_settings()
    validator = ConfigValidator(settings)
    result = validator.validate()
    _render_validation_result(result)

    if not result.passed:
        raise typer.Exit(code=1)


@app.command("validate-skill")
def validate_skill(
    skill_id: str | None = typer.Argument(
        None, help="Skill ID to validate (validates all if omitted)"
    ),
) -> None:
    """Validate skill(s) integrity.

    Checks SKILL.md presence, frontmatter fields, references,
    script shebangs, and script permissions.

    Examples:
        agent-kernel validate-skill
        agent-kernel validate-skill mermaid-diagrams
    """
    from agent_kernel.validators.skill_validator import SkillValidator  # noqa: PLC0415

    settings = get_settings()
    validator = SkillValidator(settings.skills_dir)

    has_errors = False

    if skill_id:
        result = validator.validate(skill_id)
        _render_validation_result(result)
        if not result.passed:
            has_errors = True
    else:
        results = validator.validate_all()
        if not results:
            console.print("[yellow]No skills found in skills directory.[/yellow]")
            return
        for result in results:
            _render_validation_result(result)
            console.print()
            if not result.passed:
                has_errors = True

    if has_errors:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


@app.command("scheduler-start")
def scheduler_start(
    poll_interval: int = typer.Option(
        60, "--poll-interval", "-p", help="Seconds between schedule checks"
    ),
    workflow_filter: list[str] = typer.Option(
        None, "--workflow", "-w",
        help="Only schedule specific workflow IDs",
    ),
) -> None:
    """Start the scheduler daemon — runs cron-triggered workflows on schedule.

    Loads all workflow specs from configs/workflows/, registers those with
    cron triggers, and polls on the given interval. Ctrl+C to stop.

    Examples:
        agent-kernel scheduler-start
        agent-kernel scheduler-start --poll-interval 30
        agent-kernel scheduler-start -w daily_checkin -w weekly_review
    """
    console.print("[bold blue]Starting scheduler...[/bold blue]")

    try:
        asyncio.run(_scheduler_start_async(poll_interval, workflow_filter or []))
    except KeyboardInterrupt:
        console.print("\n[yellow]Scheduler stopped.[/yellow]")
    except Exception as e:
        console.print(f"[red]Scheduler error: {e}[/red]")
        raise typer.Exit(1)


async def _scheduler_start_async(
    poll_interval: int,
    workflow_filter: list[str],
) -> None:
    """Start the scheduler asynchronously."""
    from agent_kernel.scheduler.scheduler import Scheduler
    from agent_kernel.workflows.spec import TriggerType

    settings = get_settings()
    data_dir = get_data_dir()

    # Initialize stores
    event_log = SQLiteEventLog(data_dir / "events" / "events.db")
    trace_store = SQLiteTraceSink(data_dir / "traces" / "traces.db")
    document_store = SQLiteDocumentStore(data_dir / "documents" / "documents.db")
    vector_store = create_vector_store(data_dir / "vectors" / "vectors")
    graph_store = SQLiteGraphStore(data_dir / "graph" / "graph.db")
    experience_store = SQLiteExperienceStore(data_dir / "experience" / "experience.db")

    jsonl_sink = JSONLTraceSink(data_dir / "traces" / "traces.jsonl")
    multi_trace_store = MultiSinkTraceStore(trace_store, [jsonl_sink])

    # Set up adaptive timeout manager
    timeout_manager = AdaptiveTimeoutManager(trace_store=multi_trace_store)

    registry = CapabilityRegistry()
    registry.load_from_directory(settings.configs_dir / "capabilities")

    retry_config = None
    if settings.tool_broker_retry_enabled:
        retry_config = RetryConfig(
            max_retries=settings.tool_broker_retry_max_retries,
            base_delay_ms=settings.tool_broker_retry_base_delay_ms,
            max_delay_ms=settings.tool_broker_retry_max_delay_ms,
        )

    broker = ToolBroker(
        registry=registry,
        event_log=event_log,
        retry_config=retry_config,
        enable_circuit_breaker=settings.tool_broker_circuit_breaker_enabled,
        timeout_manager=timeout_manager,
    )
    register_builtin_tools(broker)
    _configure_library_tools(broker, settings.configs_dir)
    _configure_skill_scripts(broker, registry, settings)
    await _configure_mcp_adapter(broker, settings.configs_dir)

    assembler = ContextAssembler(
        document_store=document_store,
        vector_store=vector_store,
        graph_store=graph_store,
        skills_dir=settings.skills_dir,
        packs_config_dir=settings.configs_dir / "context_packs",
        sources_config_dir=settings.configs_dir / "sources",
        experience_store=experience_store,
    )

    executor = DeterministicExecutor(
        tool_broker=broker,
        trace_store=multi_trace_store,
        event_log=event_log,
    )

    # Register LLM engine
    from agent_kernel.services.llm import create_llm_service

    llm_service = create_llm_service(
        provider=settings.default_llm_provider,
        api_key=(
            settings.openai_api_key
            if settings.default_llm_provider == "openai"
            else settings.anthropic_api_key
        ) or None,
        model=(
            settings.openai_model
            if settings.default_llm_provider == "openai"
            else settings.anthropic_model
        ) or None,
        base_url=getattr(settings, "openai_base_url", None),
    )

    # Set up feedback loop components
    cost_anomaly_detector = CostAnomalyDetector(
        event_log=event_log,
        trace_store=multi_trace_store,
    )
    experience_miner = ExperienceMiner(
        experience_store=experience_store,
        event_log=event_log,
        llm_service=llm_service,
    )

    workflow_store = SQLiteWorkflowRunStore(data_dir / "workflows" / "workflows.db")
    runner = WorkflowRunner(
        context_assembler=assembler,
        executor=executor,
        event_log=event_log,
        configs_dir=settings.configs_dir,
        workflow_store=workflow_store,
        trace_store=multi_trace_store,
        cost_anomaly_detector=cost_anomaly_detector,
        experience_miner=experience_miner,
    )

    engine = CustomEngine(llm_service=llm_service, capability_registry=registry)
    runner.register_engine(engine)

    # Create scheduler and register workflows
    scheduler = Scheduler(workflow_runner=runner, event_log=event_log)

    workflows_dir = settings.configs_dir / "workflows"
    registered = 0
    cron_count = 0

    for yaml_file in sorted(workflows_dir.glob("*.yaml")):
        workflow_id = yaml_file.stem
        if workflow_filter and workflow_id not in workflow_filter:
            continue

        try:
            spec = runner.load_workflow(workflow_id)
            job = scheduler.register_workflow(spec)
            registered += 1
            if spec.trigger.type == TriggerType.CRON and spec.trigger.schedule:
                cron_count += 1
        except Exception as e:
            logger.warning("skip_workflow", workflow_id=workflow_id, error=str(e))

    # Show registered jobs
    table = Table(title="Registered Workflows")
    table.add_column("Workflow ID", style="cyan")
    table.add_column("Trigger", style="green")
    table.add_column("Schedule")
    table.add_column("Next Run")
    table.add_column("Enabled", style="bold")

    for job in scheduler.list_jobs():
        next_run = ""
        if job.next_run:
            next_run = job.next_run.strftime("%Y-%m-%d %H:%M UTC")
        table.add_row(
            job.workflow_id,
            job.trigger_type.value,
            job.schedule or "-",
            next_run or "-",
            "yes" if job.enabled else "no",
        )

    console.print(table)
    console.print(
        f"\n[green]{registered} workflows registered "
        f"({cron_count} cron-scheduled)[/green]"
    )
    console.print(
        f"[dim]Polling every {poll_interval}s. Press Ctrl+C to stop.[/dim]\n"
    )

    try:
        await scheduler.start(poll_interval=float(poll_interval))
    finally:
        event_log.close()
        multi_trace_store.close()
        document_store.close()
        vector_store.close()
        graph_store.close()
        workflow_store.close()
        if hasattr(experience_store, "close"):
            experience_store.close()


@app.command("scheduler-list")
def scheduler_list() -> None:
    """List all workflows with their trigger types and schedules."""
    from agent_kernel.scheduler.scheduler import CronExpression
    from agent_kernel.workflows.spec import TriggerType, WorkflowSpec

    import yaml

    settings = get_settings()
    workflows_dir = settings.configs_dir / "workflows"

    if not workflows_dir.exists():
        console.print("[yellow]No workflows directory found.[/yellow]")
        return

    table = Table(title="Workflow Schedules")
    table.add_column("Workflow ID", style="cyan")
    table.add_column("Name")
    table.add_column("Trigger", style="green")
    table.add_column("Schedule")
    table.add_column("Next Run")
    table.add_column("Agent")

    for yaml_file in sorted(workflows_dir.glob("*.yaml")):
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            spec = WorkflowSpec(**data)

            next_run = "-"
            if spec.trigger.type == TriggerType.CRON and spec.trigger.schedule:
                try:
                    cron = CronExpression(spec.trigger.schedule)
                    next_run = cron.next_run().strftime("%Y-%m-%d %H:%M UTC")
                except ValueError:
                    next_run = "[red]invalid[/red]"

            table.add_row(
                spec.workflow_id,
                spec.name or "-",
                spec.trigger.type.value,
                spec.trigger.schedule or "-",
                next_run,
                spec.agent_profile_id or "-",
            )
        except Exception as e:
            table.add_row(
                yaml_file.stem,
                "[red]error[/red]",
                "-",
                "-",
                "-",
                str(e)[:40],
            )

    console.print(table)


def main() -> None:
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
