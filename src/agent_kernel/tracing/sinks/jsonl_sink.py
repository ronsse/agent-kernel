"""JSONL file-based trace sink for backup and portability."""

from __future__ import annotations

import fcntl
from pathlib import Path
from typing import TextIO

import structlog

from agent_kernel.core.schemas import DecisionTrace

logger = structlog.get_logger(__name__)


class JSONLTraceSink:
    """Append-only JSONL file sink for traces.

    Provides a simple, portable backup of all traces. Each line
    is a complete JSON representation of a DecisionTrace.

    Thread-safe via file locking.
    """

    def __init__(self, file_path: str | Path) -> None:
        """Initialize JSONL sink.

        Args:
            file_path: Path to the JSONL file.
        """
        self._file_path = Path(file_path)
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO | None = None
        self._open_file()
        logger.info("jsonl_trace_sink_initialized", file_path=str(self._file_path))

    def _open_file(self) -> None:
        """Open the file for appending."""
        self._file = open(self._file_path, "a", encoding="utf-8")

    def write(self, trace: DecisionTrace) -> None:
        """Append a trace to the JSONL file.

        Uses file locking for thread safety.
        """
        if self._file is None or self._file.closed:
            self._open_file()

        line = trace.model_dump_json() + "\n"

        # Acquire exclusive lock for writing
        fcntl.flock(self._file.fileno(), fcntl.LOCK_EX)
        try:
            self._file.write(line)
            self._file.flush()
        finally:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)

        logger.debug("trace_appended_to_jsonl", trace_id=trace.trace_id)

    def read_all(self) -> list[DecisionTrace]:
        """Read all traces from the file.

        Returns:
            List of all traces in chronological order.
        """
        if not self._file_path.exists():
            return []

        traces = []
        with open(self._file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    traces.append(DecisionTrace.model_validate_json(line))

        return traces

    def read_recent(self, n: int = 50) -> list[DecisionTrace]:
        """Read the N most recent traces.

        Note: This reads the entire file. For large files, use SQLite.

        Args:
            n: Number of traces to return.

        Returns:
            List of most recent traces.
        """
        all_traces = self.read_all()
        return all_traces[-n:]

    def close(self) -> None:
        """Close the file handle."""
        if self._file and not self._file.closed:
            self._file.close()
            self._file = None
        logger.info("jsonl_trace_sink_closed")

    def __enter__(self) -> JSONLTraceSink:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()
