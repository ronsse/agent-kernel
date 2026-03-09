"""Tests for File Watcher."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent_kernel.scheduler.file_watcher import (
    FileEvent,
    FileEventType,
    FileWatcher,
    WatchConfig,
)


class TestFileEvent:
    """Tests for FileEvent."""

    def test_create_event(self):
        """Test creating an event."""
        event = FileEvent(
            event_type=FileEventType.CREATED,
            path="/tmp/test.txt",
        )

        assert event.event_type == FileEventType.CREATED
        assert event.path == "/tmp/test.txt"
        assert event.event_id is not None

    def test_to_dict(self):
        """Test converting to dict."""
        event = FileEvent(
            event_type=FileEventType.MODIFIED,
            path="/tmp/test.txt",
        )

        data = event.to_dict()

        assert data["event_type"] == "modified"
        assert data["path"] == "/tmp/test.txt"


class TestWatchConfig:
    """Tests for WatchConfig."""

    def test_default_values(self):
        """Test default configuration."""
        config = WatchConfig(path="/tmp/test")

        assert config.path == "/tmp/test"
        assert config.patterns == ["*"]
        assert config.recursive is True
        assert config.enabled is True

    def test_custom_values(self):
        """Test custom configuration."""
        config = WatchConfig(
            path="/home/user/docs",
            patterns=["*.md", "*.txt"],
            ignore_patterns=[".*"],
            event_types=[FileEventType.MODIFIED],
            recursive=False,
        )

        assert config.patterns == ["*.md", "*.txt"]
        assert ".*" in config.ignore_patterns
        assert config.recursive is False


class TestFileWatcher:
    """Tests for FileWatcher."""

    def test_init(self):
        """Test watcher initialization."""
        watcher = FileWatcher(poll_interval=0.5)
        assert watcher._poll_interval == 0.5
        assert len(watcher._watches) == 0

    def test_add_watch(self, tmp_path):
        """Test adding a watch."""
        watcher = FileWatcher()
        config = WatchConfig(path=str(tmp_path))

        watch_id = watcher.add_watch(config)

        assert watch_id in watcher._watches
        assert len(watcher.list_watches()) == 1

    def test_add_watch_with_handler(self, tmp_path):
        """Test adding watch with handler."""
        watcher = FileWatcher()
        handler = AsyncMock()

        config = WatchConfig(path=str(tmp_path))
        watch_id = watcher.add_watch(config, handler)

        assert watch_id in watcher._handlers
        assert handler in watcher._handlers[watch_id]

    def test_remove_watch(self, tmp_path):
        """Test removing a watch."""
        watcher = FileWatcher()
        config = WatchConfig(path=str(tmp_path))
        watch_id = watcher.add_watch(config)

        result = watcher.remove_watch(watch_id)

        assert result is True
        assert watch_id not in watcher._watches

    def test_remove_nonexistent_watch(self):
        """Test removing nonexistent watch."""
        watcher = FileWatcher()
        result = watcher.remove_watch("nonexistent")
        assert result is False

    def test_get_watch(self, tmp_path):
        """Test getting a watch."""
        watcher = FileWatcher()
        config = WatchConfig(path=str(tmp_path))
        watch_id = watcher.add_watch(config)

        retrieved = watcher.get_watch(watch_id)

        assert retrieved is not None
        assert retrieved.path == str(tmp_path)

    def test_scan_directory(self, tmp_path):
        """Test scanning directory."""
        # Create test files
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.txt").write_text("content2")
        (tmp_path / "file3.md").write_text("content3")

        watcher = FileWatcher()
        config = WatchConfig(path=str(tmp_path), patterns=["*.txt"])

        states = watcher._scan_directory(config)

        # Should only find .txt files
        assert len(states) == 2
        paths = [Path(p).name for p in states.keys()]
        assert "file1.txt" in paths
        assert "file2.txt" in paths
        assert "file3.md" not in paths

    def test_scan_directory_with_ignore(self, tmp_path):
        """Test scanning with ignore patterns."""
        (tmp_path / "file.txt").write_text("content")
        (tmp_path / ".hidden.txt").write_text("hidden")

        watcher = FileWatcher()
        config = WatchConfig(
            path=str(tmp_path),
            patterns=["*"],
            ignore_patterns=[".*"],
        )

        states = watcher._scan_directory(config)

        paths = [Path(p).name for p in states.keys()]
        assert "file.txt" in paths
        assert ".hidden.txt" not in paths

    def test_scan_nonexistent_directory(self):
        """Test scanning nonexistent directory."""
        watcher = FileWatcher()
        config = WatchConfig(path="/nonexistent/path")

        states = watcher._scan_directory(config)

        assert states == {}

    @pytest.mark.asyncio
    async def test_detect_created_file(self, tmp_path):
        """Test detecting file creation."""
        watcher = FileWatcher(poll_interval=0.1)
        events_received = []

        async def handler(watch_id: str, event: FileEvent):
            events_received.append(event)

        config = WatchConfig(
            path=str(tmp_path),
            patterns=["*.txt"],
            debounce_seconds=0.05,
        )
        watcher.add_watch(config, handler)

        # Start watcher
        await watcher.start()

        # Create a file
        await asyncio.sleep(0.1)
        (tmp_path / "new_file.txt").write_text("content")

        # Wait for detection
        await asyncio.sleep(0.3)
        await watcher.stop()

        # Should have received creation event
        created_events = [e for e in events_received if e.event_type == FileEventType.CREATED]
        assert len(created_events) >= 1

    @pytest.mark.asyncio
    async def test_detect_modified_file(self, tmp_path):
        """Test detecting file modification."""
        # Create file before watching
        test_file = tmp_path / "existing.txt"
        test_file.write_text("original")

        watcher = FileWatcher(poll_interval=0.1)
        events_received = []

        async def handler(watch_id: str, event: FileEvent):
            events_received.append(event)

        config = WatchConfig(
            path=str(tmp_path),
            patterns=["*.txt"],
            debounce_seconds=0.05,
        )
        watcher.add_watch(config, handler)

        await watcher.start()

        # Modify the file
        await asyncio.sleep(0.2)
        test_file.write_text("modified content")

        # Wait for detection
        await asyncio.sleep(0.3)
        await watcher.stop()

        # Should have received modification event
        mod_events = [e for e in events_received if e.event_type == FileEventType.MODIFIED]
        assert len(mod_events) >= 1

    @pytest.mark.asyncio
    async def test_detect_deleted_file(self, tmp_path):
        """Test detecting file deletion."""
        test_file = tmp_path / "to_delete.txt"
        test_file.write_text("content")

        watcher = FileWatcher(poll_interval=0.1)
        events_received = []

        async def handler(watch_id: str, event: FileEvent):
            events_received.append(event)

        config = WatchConfig(
            path=str(tmp_path),
            patterns=["*.txt"],
            debounce_seconds=0.05,
        )
        watcher.add_watch(config, handler)

        await watcher.start()

        # Delete the file
        await asyncio.sleep(0.2)
        test_file.unlink()

        # Wait for detection
        await asyncio.sleep(0.3)
        await watcher.stop()

        # Should have received deletion event
        del_events = [e for e in events_received if e.event_type == FileEventType.DELETED]
        assert len(del_events) >= 1

    @pytest.mark.asyncio
    async def test_disabled_watch(self, tmp_path):
        """Test that disabled watches don't trigger."""
        watcher = FileWatcher(poll_interval=0.1)
        events_received = []

        async def handler(watch_id: str, event: FileEvent):
            events_received.append(event)

        config = WatchConfig(
            path=str(tmp_path),
            patterns=["*.txt"],
            enabled=False,  # Disabled
        )
        watcher.add_watch(config, handler)

        await watcher.start()

        # Create a file
        await asyncio.sleep(0.1)
        (tmp_path / "file.txt").write_text("content")

        await asyncio.sleep(0.3)
        await watcher.stop()

        # No events should be received
        assert len(events_received) == 0

    def test_create_simple_watch(self, tmp_path):
        """Test simple watch creation."""
        handler = AsyncMock()

        watcher, watch_id = FileWatcher.create_simple_watch(
            path=str(tmp_path),
            patterns=["*.txt"],
            handler=handler,
        )

        assert watcher is not None
        assert watch_id is not None
        assert watch_id in watcher._watches
