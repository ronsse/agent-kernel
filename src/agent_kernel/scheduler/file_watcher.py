"""File Watcher - triggers workflows based on file system events.

Uses watchdog for cross-platform file system monitoring.
"""

from __future__ import annotations

import asyncio
import fnmatch
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now

logger = structlog.get_logger(__name__)

# Type for watch handlers
WatchHandler = Callable[[str, "FileEvent"], Coroutine[Any, Any, None]]


class FileEventType(str, Enum):
    """Types of file system events."""

    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    MOVED = "moved"


@dataclass
class FileEvent:
    """A file system event."""

    event_id: str = field(default_factory=generate_ulid)
    event_type: FileEventType = FileEventType.MODIFIED
    path: str = ""
    src_path: str | None = None  # For MOVED events
    is_directory: bool = False
    timestamp: str = field(default_factory=lambda: utc_now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "path": self.path,
            "src_path": self.src_path,
            "is_directory": self.is_directory,
            "timestamp": self.timestamp,
        }


@dataclass
class WatchConfig:
    """Configuration for a file watch."""

    watch_id: str = field(default_factory=generate_ulid)
    path: str = ""  # Path to watch
    patterns: list[str] = field(default_factory=lambda: ["*"])  # Glob patterns
    ignore_patterns: list[str] = field(default_factory=list)
    event_types: list[FileEventType] = field(
        default_factory=lambda: list(FileEventType)
    )
    recursive: bool = True
    debounce_seconds: float = 0.5  # Debounce rapid changes
    enabled: bool = True


class FileWatcher:
    """Watches file system for changes and triggers handlers.

    This is a polling-based implementation that doesn't require
    external dependencies. For production, consider using watchdog.
    """

    def __init__(
        self,
        poll_interval: float = 1.0,
    ) -> None:
        """Initialize file watcher.

        Args:
            poll_interval: Seconds between polling checks.
        """
        self._poll_interval = poll_interval
        self._watches: dict[str, WatchConfig] = {}
        self._handlers: dict[str, list[WatchHandler]] = {}
        self._running = False
        self._task: asyncio.Task | None = None

        # Track file states for change detection
        self._file_states: dict[str, dict[str, float]] = {}  # watch_id -> {path: mtime}
        self._pending_events: dict[str, tuple[FileEvent, float]] = {}  # For debouncing

        logger.info("file_watcher_initialized")

    def add_watch(
        self,
        config: WatchConfig,
        handler: WatchHandler | None = None,
    ) -> str:
        """Add a file watch.

        Args:
            config: Watch configuration.
            handler: Optional handler for events.

        Returns:
            Watch ID.
        """
        self._watches[config.watch_id] = config
        self._file_states[config.watch_id] = {}

        if handler:
            self.add_handler(config.watch_id, handler)

        # Initialize file states
        self._scan_directory(config)

        logger.info(
            "watch_added",
            watch_id=config.watch_id,
            path=config.path,
            patterns=config.patterns,
        )

        return config.watch_id

    def remove_watch(self, watch_id: str) -> bool:
        """Remove a watch.

        Args:
            watch_id: Watch ID to remove.

        Returns:
            True if removed.
        """
        if watch_id in self._watches:
            del self._watches[watch_id]
            self._file_states.pop(watch_id, None)
            self._handlers.pop(watch_id, None)
            logger.debug("watch_removed", watch_id=watch_id)
            return True
        return False

    def add_handler(self, watch_id: str, handler: WatchHandler) -> None:
        """Add an event handler.

        Args:
            watch_id: Watch to handle.
            handler: Handler coroutine.
        """
        if watch_id not in self._handlers:
            self._handlers[watch_id] = []
        self._handlers[watch_id].append(handler)

    def list_watches(self) -> list[WatchConfig]:
        """List all watches."""
        return list(self._watches.values())

    def get_watch(self, watch_id: str) -> WatchConfig | None:
        """Get a watch by ID."""
        return self._watches.get(watch_id)

    async def start(self) -> None:
        """Start watching for file changes."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info("file_watcher_started")

    async def stop(self) -> None:
        """Stop watching."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("file_watcher_stopped")

    async def _watch_loop(self) -> None:
        """Main watch loop."""
        while self._running:
            try:
                await self._check_changes()
                await self._process_pending_events()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("watch_loop_error", error=str(e))

            await asyncio.sleep(self._poll_interval)

    async def _check_changes(self) -> None:
        """Check for file changes in all watches."""
        for watch_id, config in self._watches.items():
            if not config.enabled:
                continue

            current_states = self._scan_directory(config)
            previous_states = self._file_states.get(watch_id, {})

            # Detect changes
            all_paths = set(current_states.keys()) | set(previous_states.keys())

            for path in all_paths:
                current_mtime = current_states.get(path)
                previous_mtime = previous_states.get(path)

                event = None

                if previous_mtime is None and current_mtime is not None:
                    # New file
                    if FileEventType.CREATED in config.event_types:
                        event = FileEvent(
                            event_type=FileEventType.CREATED,
                            path=path,
                            is_directory=Path(path).is_dir(),
                        )

                elif previous_mtime is not None and current_mtime is None:
                    # Deleted file
                    if FileEventType.DELETED in config.event_types:
                        event = FileEvent(
                            event_type=FileEventType.DELETED,
                            path=path,
                        )

                elif current_mtime != previous_mtime:
                    # Modified file
                    if FileEventType.MODIFIED in config.event_types:
                        event = FileEvent(
                            event_type=FileEventType.MODIFIED,
                            path=path,
                            is_directory=Path(path).is_dir(),
                        )

                if event:
                    await self._queue_event(watch_id, event, config.debounce_seconds)

            # Update states
            self._file_states[watch_id] = current_states

    async def _queue_event(
        self,
        watch_id: str,
        event: FileEvent,
        debounce: float,
    ) -> None:
        """Queue an event for debouncing."""
        import time

        key = f"{watch_id}:{event.path}"

        # Update or add pending event
        self._pending_events[key] = (event, time.time() + debounce)

    async def _process_pending_events(self) -> None:
        """Process debounced events."""
        import time

        now = time.time()
        to_remove = []

        for key, (event, trigger_time) in list(self._pending_events.items()):
            if now >= trigger_time:
                to_remove.append(key)

                # Extract watch_id from key
                watch_id = key.split(":", 1)[0]

                # Dispatch event
                await self._dispatch_event(watch_id, event)

        for key in to_remove:
            del self._pending_events[key]

    async def _dispatch_event(self, watch_id: str, event: FileEvent) -> None:
        """Dispatch event to handlers."""
        handlers = self._handlers.get(watch_id, [])

        logger.debug(
            "file_event",
            watch_id=watch_id,
            event_type=event.event_type.value,
            path=event.path,
        )

        for handler in handlers:
            try:
                await handler(watch_id, event)
            except Exception as e:
                logger.error(
                    "handler_error",
                    watch_id=watch_id,
                    error=str(e),
                )

    def _scan_directory(self, config: WatchConfig) -> dict[str, float]:
        """Scan directory and return file states.

        Args:
            config: Watch configuration.

        Returns:
            Dict of path -> mtime.
        """
        states: dict[str, float] = {}
        root = Path(config.path)

        if not root.exists():
            return states

        def matches_patterns(path: Path) -> bool:
            """Check if path matches configured patterns."""
            name = path.name

            # Check ignore patterns first
            for pattern in config.ignore_patterns:
                if fnmatch.fnmatch(name, pattern):
                    return False

            # Check include patterns
            for pattern in config.patterns:
                if fnmatch.fnmatch(name, pattern):
                    return True

            return False

        if root.is_file():
            if matches_patterns(root):
                states[str(root)] = root.stat().st_mtime
        else:
            if config.recursive:
                paths = root.rglob("*")
            else:
                paths = root.glob("*")

            for path in paths:
                if path.is_file() and matches_patterns(path):
                    try:
                        states[str(path)] = path.stat().st_mtime
                    except (OSError, PermissionError):
                        continue

        return states

    @classmethod
    def create_simple_watch(
        cls,
        path: str,
        patterns: list[str] | None = None,
        handler: WatchHandler | None = None,
    ) -> tuple[FileWatcher, str]:
        """Create a simple file watcher.

        Args:
            path: Path to watch.
            patterns: File patterns (default: all files).
            handler: Event handler.

        Returns:
            Tuple of (watcher, watch_id).
        """
        watcher = cls()
        config = WatchConfig(
            path=path,
            patterns=patterns or ["*"],
        )
        watch_id = watcher.add_watch(config, handler)
        return watcher, watch_id
