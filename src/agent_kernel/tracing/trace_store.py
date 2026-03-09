"""TraceStore interface and multi-sink implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Protocol, runtime_checkable

from agent_kernel.core.schemas import DecisionTrace


@runtime_checkable
class TraceSink(Protocol):
    """Protocol for trace storage backends.

    Sinks are write-focused; they receive traces and persist them.
    """

    def write(self, trace: DecisionTrace) -> None:
        """Write a trace to the sink.

        Args:
            trace: The DecisionTrace to persist.
        """
        ...

    def close(self) -> None:
        """Close the sink and release resources."""
        ...


class TraceStore(ABC):
    """Abstract base class for trace storage with read capabilities.

    TraceStore extends TraceSink with query capabilities.
    """

    @abstractmethod
    def write(self, trace: DecisionTrace) -> None:
        """Write a trace to storage.

        Args:
            trace: The DecisionTrace to persist.
        """

    @abstractmethod
    def get(self, trace_id: str) -> DecisionTrace | None:
        """Retrieve a trace by ID.

        Args:
            trace_id: The unique trace identifier.

        Returns:
            The DecisionTrace if found, None otherwise.
        """

    @abstractmethod
    def list_traces(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        agent_profile_id: str | None = None,
        workflow_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[DecisionTrace]:
        """List traces with optional filtering.

        Args:
            limit: Maximum number of traces to return.
            offset: Number of traces to skip.
            agent_profile_id: Filter by agent profile.
            workflow_id: Filter by workflow (run_id prefix).
            since: Filter traces after this time.
            until: Filter traces before this time.

        Returns:
            List of matching DecisionTrace objects.
        """

    @abstractmethod
    def count(
        self,
        *,
        agent_profile_id: str | None = None,
        since: datetime | None = None,
    ) -> int:
        """Count traces matching criteria.

        Args:
            agent_profile_id: Filter by agent profile.
            since: Count traces after this time.

        Returns:
            Number of matching traces.
        """

    @abstractmethod
    def close(self) -> None:
        """Close the store and release resources."""

    def store_trace(self, trace: DecisionTrace) -> None:
        """Legacy helper for older call sites/tests."""
        self.write(trace)

    def get_trace(self, trace_id: str) -> DecisionTrace | None:
        """Legacy helper for older call sites/tests."""
        return self.get(trace_id)


class MultiSinkTraceStore(TraceStore):
    """TraceStore that writes to multiple sinks.

    Uses a primary store for reads and writes to all sinks.
    """

    def __init__(
        self,
        primary: TraceStore,
        additional_sinks: list[TraceSink] | None = None,
    ) -> None:
        """Initialize multi-sink store.

        Args:
            primary: The primary store (used for reads).
            additional_sinks: Additional sinks to write to.
        """
        self._primary = primary
        self._sinks: list[TraceSink] = additional_sinks or []

    def add_sink(self, sink: TraceSink) -> None:
        """Add an additional sink.

        Args:
            sink: The sink to add.
        """
        self._sinks.append(sink)

    def write(self, trace: DecisionTrace) -> None:
        """Write trace to primary store and all sinks."""
        self._primary.write(trace)
        for sink in self._sinks:
            sink.write(trace)

    def get(self, trace_id: str) -> DecisionTrace | None:
        """Retrieve trace from primary store."""
        return self._primary.get(trace_id)

    def list_traces(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        agent_profile_id: str | None = None,
        workflow_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[DecisionTrace]:
        """List traces from primary store."""
        return self._primary.list_traces(
            limit=limit,
            offset=offset,
            agent_profile_id=agent_profile_id,
            workflow_id=workflow_id,
            since=since,
            until=until,
        )

    def count(
        self,
        *,
        agent_profile_id: str | None = None,
        since: datetime | None = None,
    ) -> int:
        """Count traces in primary store."""
        return self._primary.count(
            agent_profile_id=agent_profile_id,
            since=since,
        )

    def close(self) -> None:
        """Close primary store and all sinks."""
        self._primary.close()
        for sink in self._sinks:
            sink.close()
