"""Obsidian Task Parser (v1.0.5).

Parses markdown tasks from Obsidian notes and converts them to TaskEntity objects.
Supports various task formats and metadata extraction.

Supported formats:
- Basic: `- [ ] Task content`
- Completed: `- [x] Done task`
- Priority: `- [ ] ⏫ High priority task` or `- [ ] [#A] Task`
- Due date: `- [ ] Task 📅 2024-01-15` or `@due(2024-01-15)`
- Tags: `- [ ] Task #label1 #label2`
- Dataview: `- [ ] Task [due:: 2024-01-15] [priority:: high]`

References:
- Design Patch v1.0.5: Task Integration
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any
import hashlib

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.task import (
    RecurrenceRule,
    TaskEntity,
    TaskPriority,
    TaskScope,
    TaskStatus,
)

logger = structlog.get_logger(__name__)


class TaskFormat(str, Enum):
    """Supported task format styles."""

    BASIC = "basic"  # - [ ] Task
    TASKS_PLUGIN = "tasks_plugin"  # Tasks plugin format with emojis
    DATAVIEW = "dataview"  # Dataview inline fields
    CUSTOM = "custom"  # Custom regex patterns


@dataclass
class ParsedTask:
    """Intermediate representation of a parsed task.
    
    v1.0.8 additions:
    - sync_marker: Indicates task should sync to external system (e.g., #sync)
    - block_id: Stable Obsidian block ID for sync tracking (e.g., ^tsk_01J...)
    """

    raw_line: str = ""
    line_number: int = 0
    indentation: int = 0
    content: str = ""
    is_completed: bool = False
    priority: TaskPriority = TaskPriority.P4
    due_date: date | None = None
    due_datetime: datetime | None = None
    recurrence: str | None = None
    tags: list[str] = field(default_factory=list)
    project: str | None = None
    context: str | None = None  # @context tags
    blocked_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Source tracking
    source_note_id: str | None = None
    source_note_path: str | None = None

    # Legacy compatibility fields (v1.0.1 API)
    task_id: str | None = None
    note_id: str | None = None
    text: str | None = None
    status: TaskStatus | None = None
    contexts: list[str] = field(default_factory=list)
    
    # v1.0.8: Sync support
    sync_marker: str | None = None  # e.g., "linear", "jira"
    block_id: str | None = None  # Obsidian block ID (e.g., "tsk_01J...")
    should_sync: bool = False  # True if task has a sync marker
    
    @property
    def has_block_id(self) -> bool:
        """Check if task has a stable block ID for sync tracking."""
        return self.block_id is not None

    @property
    def is_complete(self) -> bool:
        """Legacy alias for completion status."""
        return self.is_completed

    def __post_init__(self) -> None:
        if self.text and not self.content:
            self.content = self.text
        if not self.text:
            self.text = self.content

        if self.status is None:
            self.status = TaskStatus.COMPLETED if self.is_completed else TaskStatus.OPEN
        else:
            self.is_completed = self.status in (TaskStatus.COMPLETED, TaskStatus.COMPLETE)

        if self.note_id and not self.source_note_id:
            self.source_note_id = self.note_id

        if self.context and not self.contexts:
            self.contexts = [self.context]
        elif self.contexts and not self.context:
            self.context = self.contexts[0]

        if not self.task_id:
            stable_note_id = self.note_id or self.source_note_id or ""
            base = f"{stable_note_id}:{self.content}".encode("utf-8")
            digest = hashlib.sha1(base).hexdigest()[:10]
            self.task_id = f"task_{digest}"

        if not self.raw_line:
            self.raw_line = self.content

    def to_dict(self) -> dict[str, Any]:
        """Legacy serialization for tests."""
        status_value = "complete" if self.is_completed else "incomplete"
        priority_value = {
            TaskPriority.P1: "high",
            TaskPriority.P2: "high",
            TaskPriority.P3: "medium",
            TaskPriority.P4: "low",
        }.get(self.priority, "none")

        return {
            "task_id": self.task_id or "",
            "note_id": self.note_id or "",
            "text": self.text or "",
            "status": status_value,
            "line_number": self.line_number,
            "priority": priority_value,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "tags": self.tags,
            "contexts": self.contexts,
        }


@dataclass
class ParseResult:
    """Result of parsing a note for tasks."""

    note_id: str
    note_path: str
    tasks: list[ParsedTask] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def task_count(self) -> int:
        return len(self.tasks)


class ObsidianTaskParser:
    """Parser for extracting tasks from Obsidian markdown notes.

    Supports multiple task formats and can be configured with custom patterns.

    Usage:
        parser = ObsidianTaskParser()
        result = parser.parse_note(note_content, note_id="note_123")
        for task in result.tasks:
            entity = parser.to_task_entity(task)
    """

    # Regex patterns
    TASK_LINE_RE = re.compile(r"^(\s*)[-*] \[([ xX])\] (.+)$", re.MULTILINE)

    # Priority patterns
    PRIORITY_EMOJI_MAP = {
        "⏫": TaskPriority.P1,  # Highest
        "🔺": TaskPriority.P1,
        "🔼": TaskPriority.P2,  # High
        "🔽": TaskPriority.P3,  # Medium
        "⏬": TaskPriority.P4,  # Low
        "🔻": TaskPriority.P4,
    }
    PRIORITY_LETTER_RE = re.compile(r"\[#([A-D])\]", re.IGNORECASE)
    PRIORITY_PAREN_RE = re.compile(r"\(([A-D])\)", re.IGNORECASE)
    PRIORITY_LETTER_MAP = {
        "A": TaskPriority.P1,
        "B": TaskPriority.P2,
        "C": TaskPriority.P3,
        "D": TaskPriority.P4,
    }

    # Date patterns
    DUE_EMOJI_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
    DUE_AT_RE = re.compile(r"@due\((\d{4}-\d{2}-\d{2})\)")
    DUE_TEXT_RE = re.compile(r"\bdue:(\d{4}-\d{2}-\d{2})")
    SCHEDULED_RE = re.compile(r"⏳\s*(\d{4}-\d{2}-\d{2})")
    START_RE = re.compile(r"🛫\s*(\d{4}-\d{2}-\d{2})")

    # Dataview patterns
    DATAVIEW_DUE_RE = re.compile(r"\[due::\s*(\d{4}-\d{2}-\d{2})\]")
    DATAVIEW_PRIORITY_RE = re.compile(r"\[priority::\s*(\w+)\]", re.IGNORECASE)
    DATAVIEW_PROJECT_RE = re.compile(r"\[project::\s*([^\]]+)\]")

    # Tag pattern
    TAG_RE = re.compile(r"#([a-zA-Z][a-zA-Z0-9_-]*)")

    # Context pattern (@context)
    CONTEXT_RE = re.compile(r"@([a-zA-Z][a-zA-Z0-9_-]*)")

    # Recurrence patterns
    RECURRENCE_RE = re.compile(r"🔁\s*([^📅🛫⏳]+?)(?=\s*[📅🛫⏳]|$)")

    def __init__(
        self,
        default_scope: TaskScope = TaskScope.PERSONAL,
        extract_inline_tags: bool = True,
        extract_dataview: bool = True,
    ) -> None:
        """Initialize the parser.

        Args:
            default_scope: Default scope for parsed tasks.
            extract_inline_tags: Whether to extract #tags from content.
            extract_dataview: Whether to parse dataview inline fields.
        """
        self.default_scope = default_scope
        self.extract_inline_tags = extract_inline_tags
        self.extract_dataview = extract_dataview

    def parse_note(
        self,
        content: str,
        note_id: str,
        note_path: str | None = None,
    ) -> ParseResult:
        """Parse all tasks from a note.

        Args:
            content: Markdown content of the note.
            note_id: Unique identifier for the note.
            note_path: Optional file path.

        Returns:
            ParseResult with extracted tasks.
        """
        result = ParseResult(note_id=note_id, note_path=note_path or "")
        lines = content.split("\n")

        for line_num, line in enumerate(lines, start=1):
            match = self.TASK_LINE_RE.match(line)
            if match:
                try:
                    task = self._parse_task_line(line, line_num, note_id, note_path)
                    result.tasks.append(task)
                except Exception as e:
                    result.errors.append(f"Line {line_num}: {e}")
                    logger.warning(
                        "task_parse_error",
                        line_num=line_num,
                        error=str(e),
                    )

        logger.debug(
            "note_parsed",
            note_id=note_id,
            task_count=len(result.tasks),
            error_count=len(result.errors),
        )
        return result

    def _parse_task_line(
        self,
        line: str,
        line_number: int,
        note_id: str,
        note_path: str | None,
    ) -> ParsedTask:
        """Parse a single task line."""
        match = self.TASK_LINE_RE.match(line)
        if not match:
            raise ValueError(f"Not a valid task line: {line}")

        indent, checkbox, content = match.groups()
        is_completed = checkbox.lower() == "x"

        task = ParsedTask(
            raw_line=line,
            line_number=line_number,
            indentation=len(indent),
            content=content.strip(),
            is_completed=is_completed,
            source_note_id=note_id,
            source_note_path=note_path,
        )

        # Extract priority
        task.priority = self._extract_priority(content)

        # Extract due date
        task.due_date = self._extract_due_date(content)

        # Extract recurrence
        task.recurrence = self._extract_recurrence(content)

        # Extract tags
        if self.extract_inline_tags:
            task.tags = self._extract_tags(content)

        # Extract context (@home, @work, etc.)
        task.contexts = self._extract_contexts(content)
        task.context = task.contexts[0] if task.contexts else None

        # Extract dataview fields
        if self.extract_dataview:
            self._extract_dataview_fields(content, task)

        # v1.0.8: Extract sync marker and block ID
        task.sync_marker, task.should_sync = self._extract_sync_marker(content)
        task.block_id = self._extract_block_id(line)  # Use full line for block ID

        # Clean content (remove metadata)
        task.content = self._clean_content(content)

        return task

    def _extract_priority(self, content: str) -> TaskPriority:
        """Extract priority from content."""
        # Check emoji priorities
        for emoji, priority in self.PRIORITY_EMOJI_MAP.items():
            if emoji in content:
                return priority

        # Check letter priorities [#A], [#B], etc.
        match = self.PRIORITY_LETTER_RE.search(content)
        if match:
            letter = match.group(1).upper()
            return self.PRIORITY_LETTER_MAP.get(letter, TaskPriority.P4)

        # Check letter priorities (A), (B), etc.
        match = self.PRIORITY_PAREN_RE.search(content)
        if match:
            letter = match.group(1).upper()
            paren_map = {
                "A": TaskPriority.P1,
                "B": TaskPriority.P3,
                "C": TaskPriority.P4,
                "D": TaskPriority.P4,
            }
            return paren_map.get(letter, TaskPriority.P4)

        # Check dataview priority
        match = self.DATAVIEW_PRIORITY_RE.search(content)
        if match:
            priority_str = match.group(1).lower()
            priority_map = {
                "high": TaskPriority.P1,
                "urgent": TaskPriority.P1,
                "medium": TaskPriority.P2,
                "normal": TaskPriority.P3,
                "low": TaskPriority.P4,
            }
            return priority_map.get(priority_str, TaskPriority.P4)

        return TaskPriority.P4

    def _extract_due_date(self, content: str) -> date | None:
        """Extract due date from content."""
        # Try emoji format: 📅 2024-01-15
        match = self.DUE_EMOJI_RE.search(content)
        if match:
            try:
                return date.fromisoformat(match.group(1))
            except ValueError:
                pass

        # Try @due() format
        match = self.DUE_AT_RE.search(content)
        if match:
            try:
                return date.fromisoformat(match.group(1))
            except ValueError:
                pass

        # Try due:YYYY-MM-DD format
        match = self.DUE_TEXT_RE.search(content)
        if match:
            try:
                return date.fromisoformat(match.group(1))
            except ValueError:
                pass

        # Try dataview format
        match = self.DATAVIEW_DUE_RE.search(content)
        if match:
            try:
                return date.fromisoformat(match.group(1))
            except ValueError:
                pass

        return None

    def _extract_recurrence(self, content: str) -> str | None:
        """Extract recurrence pattern from content."""
        match = self.RECURRENCE_RE.search(content)
        if match:
            return match.group(1).strip()
        return None

    def _extract_tags(self, content: str) -> list[str]:
        """Extract #tags from content."""
        # Exclude common markdown elements that start with #
        exclude = {"task", "tasks", "todo", "done"}
        tags = []
        for match in self.TAG_RE.finditer(content):
            tag = match.group(1).lower()
            if tag not in exclude:
                tags.append(tag)
        return tags

    def _extract_contexts(self, content: str) -> list[str]:
        """Extract all @context values from content."""
        contexts: list[str] = []
        for match in self.CONTEXT_RE.finditer(content):
            context = match.group(1).lower()
            if context in {"due", "scheduled", "start", "todo", "task"}:
                continue
            if context not in contexts:
                contexts.append(context)
        return contexts

    def _extract_context(self, content: str) -> str | None:
        """Extract first @context from content."""
        contexts = self._extract_contexts(content)
        return contexts[0] if contexts else None

    # v1.0.8: Sync marker patterns
    SYNC_MARKER_RE = re.compile(r"(?:^|\s)[#@](todo|task)\b", re.IGNORECASE)
    BLOCK_ID_RE = re.compile(r"\^(tsk_[A-Za-z0-9]+|\w+)$")

    def _extract_sync_marker(self, content: str) -> tuple[str | None, bool]:
        """Extract sync marker (e.g., #todo, @task) from task content.
        
        Returns:
            Tuple of (marker_name, should_sync)
        """
        match = self.SYNC_MARKER_RE.search(content)
        if match:
            marker = match.group(1).lower()
            return marker, True
        return None, False

    def _extract_block_id(self, line: str) -> str | None:
        """Extract Obsidian block ID from end of line.
        
        Format: ^tsk_01J... or ^blockid at end of line
        
        Returns:
            Block ID without the ^ prefix, or None
        """
        match = self.BLOCK_ID_RE.search(line.strip())
        if match:
            return match.group(1)
        return None

    def generate_block_id(self) -> str:
        """Generate a new stable block ID for sync tracking.
        
        Format: tsk_{ulid}
        """
        return f"tsk_{generate_ulid()}"

    def stamp_block_id(self, line: str, block_id: str | None = None) -> str:
        """Add a block ID to a task line if it doesn't have one.
        
        Args:
            line: The full task line
            block_id: Optional block ID to use (generates one if None)
            
        Returns:
            Line with block ID appended (or unchanged if already has one)
        """
        if self.BLOCK_ID_RE.search(line.strip()):
            return line  # Already has a block ID
        
        if block_id is None:
            block_id = self.generate_block_id()
        
        return f"{line.rstrip()} ^{block_id}"

    def _extract_dataview_fields(self, content: str, task: ParsedTask) -> None:
        """Extract dataview inline fields."""
        # Project
        match = self.DATAVIEW_PROJECT_RE.search(content)
        if match:
            task.project = match.group(1).strip()

        # Add any other [key:: value] patterns to metadata
        for match in re.finditer(r"\[(\w+)::\s*([^\]]+)\]", content):
            key = match.group(1).lower()
            value = match.group(2).strip()
            if key not in ("due", "priority", "project"):
                task.metadata[key] = value

    def _clean_content(self, content: str) -> str:
        """Remove metadata from content to get clean title."""
        clean = content

        # Remove priority emojis
        for emoji in self.PRIORITY_EMOJI_MAP:
            clean = clean.replace(emoji, "")

        # Remove priority letters
        clean = self.PRIORITY_LETTER_RE.sub("", clean)
        clean = self.PRIORITY_PAREN_RE.sub("", clean)

        # Remove due date patterns
        clean = self.DUE_EMOJI_RE.sub("", clean)
        clean = self.DUE_AT_RE.sub("", clean)
        clean = self.DUE_TEXT_RE.sub("", clean)
        clean = self.SCHEDULED_RE.sub("", clean)
        clean = self.START_RE.sub("", clean)

        # Remove recurrence
        clean = self.RECURRENCE_RE.sub("", clean)

        # Remove dataview fields
        clean = re.sub(r"\[\w+::\s*[^\]]+\]", "", clean)

        # Remove tags (optional - some may want to keep them)
        # clean = self.TAG_RE.sub("", clean)
        clean = self.SYNC_MARKER_RE.sub("", clean)
        clean = re.sub(r"(?:^|\s)@(?:todo|task)\b", "", clean)

        # Clean up whitespace
        clean = " ".join(clean.split())

        return clean.strip()

    def to_task_entity(
        self,
        parsed: ParsedTask,
        scope: TaskScope | None = None,
    ) -> TaskEntity:
        """Convert ParsedTask to TaskEntity.

        Args:
            parsed: Parsed task data.
            scope: Optional scope override.

        Returns:
            TaskEntity ready for kernel storage.
        """
        # Determine scope from context or use default
        if scope is None:
            if parsed.context in ("work", "office"):
                scope = TaskScope.WORK
            elif parsed.context in ("home", "personal"):
                scope = TaskScope.PERSONAL
            else:
                scope = self.default_scope

        # Build recurrence rule
        recurrence = None
        if parsed.recurrence:
            recurrence = RecurrenceRule(pattern=parsed.recurrence)

        return TaskEntity(
            id=f"task_{generate_ulid()}",
            title=parsed.content,
            description="",
            status=TaskStatus.COMPLETED if parsed.is_completed else TaskStatus.OPEN,
            priority=parsed.priority,
            scope=scope,
            project_ref=parsed.project,
            labels=parsed.tags,
            due=parsed.due_date or parsed.due_datetime,
            recurrence=recurrence,
            source_system="obsidian",
            source_entity_ref=parsed.source_note_id,
            captured_at=utc_now(),
            completed_at=utc_now() if parsed.is_completed else None,
            ext={
                "obsidian": {
                    "note_id": parsed.source_note_id,
                    "note_path": parsed.source_note_path,
                    "line_number": parsed.line_number,
                    "raw_line": parsed.raw_line,
                    "context": parsed.context,
                    "metadata": parsed.metadata,
                }
            },
        )

    def parse_and_convert(
        self,
        content: str,
        note_id: str,
        note_path: str | None = None,
    ) -> list[TaskEntity]:
        """Parse note and convert all tasks to TaskEntity.

        Convenience method that combines parse_note and to_task_entity.

        Args:
            content: Markdown content.
            note_id: Note identifier.
            note_path: Optional file path.

        Returns:
            List of TaskEntity objects.
        """
        result = self.parse_note(content, note_id, note_path)
        return [self.to_task_entity(task) for task in result.tasks]


class TaskRenderer:
    """Renders TaskEntity objects back to Obsidian markdown.

    Used for materializing kernel tasks into Obsidian notes.
    """

    def __init__(
        self,
        include_priority_emoji: bool = True,
        include_due_emoji: bool = True,
        include_tags: bool = True,
    ) -> None:
        self.include_priority_emoji = include_priority_emoji
        self.include_due_emoji = include_due_emoji
        self.include_tags = include_tags

    def render_task(self, task: TaskEntity) -> str:
        """Render a TaskEntity as an Obsidian task line."""
        parts = []

        # Checkbox
        checkbox = "[x]" if task.status == TaskStatus.COMPLETED else "[ ]"
        parts.append(f"- {checkbox}")

        # Priority emoji
        if self.include_priority_emoji and task.priority != TaskPriority.P4:
            emoji_map = {
                TaskPriority.P1: "⏫",
                TaskPriority.P2: "🔼",
                TaskPriority.P3: "🔽",
            }
            parts.append(emoji_map.get(task.priority, ""))

        # Title
        parts.append(task.title)

        # Due date
        if self.include_due_emoji and task.due:
            due_str = task.due.strftime("%Y-%m-%d") if isinstance(task.due, datetime) else str(task.due)
            parts.append(f"📅 {due_str}")

        # Recurrence
        if task.recurrence:
            parts.append(f"🔁 {task.recurrence.pattern}")

        # Tags
        if self.include_tags and task.labels:
            for label in task.labels:
                parts.append(f"#{label}")

        return " ".join(parts)

    def render_task_list(
        self,
        tasks: list[TaskEntity],
        title: str | None = None,
        group_by: str | None = None,
    ) -> str:
        """Render a list of tasks as markdown.

        Args:
            tasks: List of tasks to render.
            title: Optional section title.
            group_by: Optional grouping (project, priority, due, scope).

        Returns:
            Markdown string.
        """
        lines = []

        if title:
            lines.append(f"## {title}\n")

        if group_by == "priority":
            grouped = self._group_by_priority(tasks)
            for priority, group_tasks in grouped.items():
                lines.append(f"### {priority.value.upper()}")
                for task in group_tasks:
                    lines.append(self.render_task(task))
                lines.append("")
        elif group_by == "project":
            grouped = self._group_by_project(tasks)
            for project, group_tasks in grouped.items():
                lines.append(f"### {project or 'No Project'}")
                for task in group_tasks:
                    lines.append(self.render_task(task))
                lines.append("")
        elif group_by == "scope":
            grouped = self._group_by_scope(tasks)
            for scope, group_tasks in grouped.items():
                lines.append(f"### {scope.value.title()}")
                for task in group_tasks:
                    lines.append(self.render_task(task))
                lines.append("")
        else:
            for task in tasks:
                lines.append(self.render_task(task))

        return "\n".join(lines)

    def _group_by_priority(
        self, tasks: list[TaskEntity]
    ) -> dict[TaskPriority, list[TaskEntity]]:
        grouped: dict[TaskPriority, list[TaskEntity]] = {}
        for task in tasks:
            grouped.setdefault(task.priority, []).append(task)
        # Sort by priority
        return dict(sorted(grouped.items(), key=lambda x: x[0].value))

    def _group_by_project(
        self, tasks: list[TaskEntity]
    ) -> dict[str | None, list[TaskEntity]]:
        grouped: dict[str | None, list[TaskEntity]] = {}
        for task in tasks:
            grouped.setdefault(task.project_ref, []).append(task)
        return grouped

    def _group_by_scope(
        self, tasks: list[TaskEntity]
    ) -> dict[TaskScope, list[TaskEntity]]:
        grouped: dict[TaskScope, list[TaskEntity]] = {}
        for task in tasks:
            grouped.setdefault(task.scope, []).append(task)
        return grouped


def get_task_parser() -> ObsidianTaskParser:
    """Get a configured task parser instance."""
    return ObsidianTaskParser()


def get_task_renderer() -> TaskRenderer:
    """Get a configured task renderer instance."""
    return TaskRenderer()


# ─────────────────────────────────────────────────────────────────
# Backwards Compatibility (v1.0.1 API)
# ─────────────────────────────────────────────────────────────────

class TaskParser:
    """Legacy TaskParser wrapper (v1.0.1 API)."""

    def __init__(self, note_id: str = "") -> None:
        self.note_id = note_id
        self._parser = ObsidianTaskParser()

    def parse(self, content: str) -> list[ParsedTask]:
        result = self._parser.parse_note(content, self.note_id or "")
        legacy_tasks: list[ParsedTask] = []
        for task in result.tasks:
            legacy_tasks.append(
                ParsedTask(
                    raw_line=task.raw_line,
                    line_number=task.line_number,
                    indentation=task.indentation,
                    content=task.content,
                    is_completed=task.is_completed,
                    priority=task.priority,
                    due_date=task.due_date or (task.due_datetime.date() if task.due_datetime else None),
                    tags=task.tags,
                    context=task.context,
                    contexts=task.contexts,
                    metadata=task.metadata,
                    note_id=self.note_id,
                )
            )
        return legacy_tasks


def extract_tasks(content: str, note_id: str = "") -> list[ParsedTask]:
    """Extract tasks from markdown content.

    Legacy function for backwards compatibility with v1.0.1 API.

    Args:
        content: Markdown content to parse
        note_id: Optional note identifier

    Returns:
        List of ParsedTask objects
    """
    parser = TaskParser(note_id=note_id)
    return parser.parse(content)
