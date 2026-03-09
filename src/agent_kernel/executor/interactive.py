"""Interactive Approval Prompts - Terminal UI for approval decisions.

Provides rich interactive prompts for approving/denying actions during
workflow execution. Uses Rich library for formatted output.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.table import Table

from agent_kernel.core.schemas import ActionRequest, SideEffect

console = Console()


def prompt_for_approval(
    action: ActionRequest,
    capability_name: str,
    effective_side_effect: SideEffect,
    args: dict[str, Any],
) -> tuple[bool, str | None]:
    """Prompt user for approval of an action.

    Args:
        action: The action request from the plan.
        capability_name: The capability being invoked.
        effective_side_effect: The effective side effect level.
        args: The action arguments.

    Returns:
        Tuple of (approved: bool, reason: str | None).
    """
    # Create approval panel
    console.print()
    console.print("━" * console.width)
    console.print("[bold yellow]⚠️  APPROVAL REQUIRED[/bold yellow]")
    console.print("━" * console.width)

    # Show action details
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value")

    table.add_row("Action ID", action.action_id)
    table.add_row("Capability", f"[bold]{capability_name}[/bold]")
    table.add_row("Side Effect", _format_side_effect(effective_side_effect))

    console.print(table)

    # Show arguments in a formatted way
    if args:
        console.print("\n[bold]Arguments:[/bold]")
        _print_args(args)

    # Prompt for approval
    console.print()
    response = Prompt.ask(
        "[bold]Approve this action?[/bold]",
        choices=["y", "n", "d", "q"],
        default="n",
    )

    if response == "q":
        # Quit - treat as denial
        console.print("[yellow]Workflow cancelled by user[/yellow]")
        return False, "Workflow cancelled by user"

    if response == "d":
        # Show full details
        _show_full_details(action, capability_name, args)
        # Ask again
        return prompt_for_approval(action, capability_name, effective_side_effect, args)

    if response == "y":
        # Approved
        reason = Prompt.ask(
            "Reason (optional, press Enter to skip)",
            default="",
        )
        console.print("[green]✓ Approved - continuing...[/green]")
        return True, reason if reason else None

    # Denied
    reason = Prompt.ask(
        "Reason for denial (optional)",
        default="",
    )
    console.print("[red]✗ Denied - skipping action[/red]")
    return False, reason if reason else "Denied by user"


def prompt_yes_no(message: str, default: bool = False) -> bool:
    """Simple yes/no prompt.

    Args:
        message: The prompt message.
        default: Default value if user just presses Enter.

    Returns:
        True if yes, False if no.
    """
    return Confirm.ask(message, default=default)


def show_dry_run_approval_summary(
    total_actions: int,
    approval_required: list[str],
    auto_approved: list[str],
) -> None:
    """Show summary of what would require approval in dry-run mode.

    Args:
        total_actions: Total number of actions in plan.
        approval_required: List of actions requiring approval.
        auto_approved: List of actions that would be auto-approved.
    """
    console.print()
    console.print(Panel(
        "[bold]Dry Run - Approval Summary[/bold]",
        border_style="cyan",
    ))

    console.print(f"\n[bold]Total actions:[/bold] {total_actions}")
    console.print(f"[bold]Would auto-approve:[/bold] {len(auto_approved)}")
    console.print(f"[bold yellow]Would need approval:[/bold yellow] {len(approval_required)}")

    if auto_approved:
        console.print("\n[bold green]Auto-approved actions:[/bold green]")
        for action in auto_approved:
            console.print(f"  ✓ {action}")

    if approval_required:
        console.print("\n[bold yellow]Actions requiring approval:[/bold yellow]")
        for action in approval_required:
            console.print(f"  ⚠️  {action}")

    console.print()


def _format_side_effect(side_effect: SideEffect) -> str:
    """Format side effect level with color."""
    colors = {
        SideEffect.NONE: "green",
        SideEffect.READ: "green",
        SideEffect.WRITE: "yellow",
        SideEffect.EXECUTE: "red",
        SideEffect.LOCAL_WRITE: "yellow",
        SideEffect.EXTERNAL_WRITE: "red",
    }
    color = colors.get(side_effect, "white")
    return f"[{color}]{side_effect.value}[/{color}]"


def _print_args(args: dict[str, Any], max_str_length: int = 100) -> None:
    """Print arguments in a readable format.

    Args:
        args: The arguments dict.
        max_str_length: Maximum string length before truncating.
    """
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Value")

    for key, value in args.items():
        formatted_value = _format_value(value, max_str_length)
        table.add_row(key, formatted_value)

    console.print(table)


def _format_value(value: Any, max_length: int = 100) -> str:
    """Format a value for display.

    Args:
        value: The value to format.
        max_length: Maximum length before truncating.

    Returns:
        Formatted string.
    """
    if isinstance(value, str):
        if len(value) > max_length:
            lines = value.count("\n") + 1
            return f"[dim]({len(value)} chars, {lines} lines - press 'd' to view)[/dim]"
        return value

    if isinstance(value, (dict, list)):
        json_str = json.dumps(value, indent=2)
        if len(json_str) > max_length:
            return f"[dim]({type(value).__name__} - press 'd' to view)[/dim]"
        return json_str

    return str(value)


def _show_full_details(
    action: ActionRequest,
    capability_name: str,
    args: dict[str, Any],
) -> None:
    """Show full details of an action.

    Args:
        action: The action request.
        capability_name: The capability name.
        args: The arguments.
    """
    console.print("\n[bold]Full Action Details:[/bold]")
    console.print(f"Action ID: {action.action_id}")
    console.print(f"Capability: {capability_name}")

    console.print("\n[bold]Arguments (JSON):[/bold]")
    syntax = Syntax(
        json.dumps(args, indent=2),
        "json",
        theme="monokai",
        line_numbers=False,
    )
    console.print(syntax)

    console.print("\nPress Enter to continue...")
    input()
