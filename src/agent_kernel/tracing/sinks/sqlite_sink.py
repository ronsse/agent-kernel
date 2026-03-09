"""SQLite-backed trace storage."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.errors import TraceNotFoundError
from agent_kernel.core.schemas import DecisionTrace
from agent_kernel.tracing.trace_store import TraceStore

logger = structlog.get_logger(__name__)


class SQLiteTraceSink(TraceStore):
    """SQLite implementation of TraceStore.

    Stores traces in a SQLite database with indexed columns for
    efficient querying and full JSON for complete trace data.
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialize SQLite trace store.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        logger.info("sqlite_trace_sink_initialized", db_path=str(self._db_path))

    def _init_schema(self) -> None:
        """Initialize database schema for v1.0.1."""
        self._conn.executescript("""
            -- Traces table with v1.0.1 fields
            CREATE TABLE IF NOT EXISTS traces (
                trace_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                workflow_id TEXT,
                agent_profile_id TEXT NOT NULL,
                engine_id TEXT NOT NULL,
                intent TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                outcome_status TEXT NOT NULL,
                trace_json TEXT NOT NULL,
                schema_version TEXT,
                kernel_version TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_traces_timestamp
                ON traces(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_traces_agent_profile
                ON traces(agent_profile_id);
            CREATE INDEX IF NOT EXISTS idx_traces_run_id
                ON traces(run_id);
            CREATE INDEX IF NOT EXISTS idx_traces_workflow_id
                ON traces(workflow_id);
            CREATE INDEX IF NOT EXISTS idx_traces_outcome
                ON traces(outcome_status);

            -- Tool calls table with v1.0.1 policy fields
            -- v1.0.8: Added input_json and output_json columns
            CREATE TABLE IF NOT EXISTS tool_calls (
                tool_call_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                capability_name TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                input_json TEXT,
                output_json TEXT,
                error_json TEXT,
                requested_side_effect TEXT,
                requested_requires_approval INTEGER,
                effective_side_effect TEXT,
                effective_requires_approval INTEGER,
                idempotency_key TEXT,
                schema_version TEXT,
                kernel_version TEXT,
                FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
            );

            CREATE INDEX IF NOT EXISTS idx_tool_calls_trace
                ON tool_calls(trace_id);
            CREATE INDEX IF NOT EXISTS idx_tool_calls_capability
                ON tool_calls(capability_name);

            -- LLM calls table (new in v1.0.1)
            CREATE TABLE IF NOT EXISTS llm_calls (
                llm_call_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                model TEXT NOT NULL,
                provider TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                tier INTEGER,
                request_json TEXT,
                response_json TEXT,
                request_hash TEXT,
                response_hash TEXT,
                schema_version TEXT,
                kernel_version TEXT,
                FOREIGN KEY (trace_id) REFERENCES traces(trace_id)
            );

            CREATE INDEX IF NOT EXISTS idx_llm_calls_trace
                ON llm_calls(trace_id);
            CREATE INDEX IF NOT EXISTS idx_llm_calls_stage
                ON llm_calls(stage);

            -- Workflow runs table (new in v1.0.1)
            CREATE TABLE IF NOT EXISTS workflow_runs (
                run_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                status TEXT NOT NULL,
                intent TEXT,
                started_at TEXT,
                ended_at TEXT,
                last_step TEXT,
                retry_count INTEGER DEFAULT 0,
                error_json TEXT,
                trace_ids_json TEXT,
                metadata_json TEXT,
                schema_version TEXT,
                kernel_version TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow_id
                ON workflow_runs(workflow_id);
            CREATE INDEX IF NOT EXISTS idx_workflow_runs_status
                ON workflow_runs(status);
        """)
        self._conn.commit()

        # Migrate old schema if needed
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Migrate old schema to v1.0.1 if needed."""
        # Check if we need to add new columns to traces
        try:
            self._conn.execute("SELECT workflow_id FROM traces LIMIT 1")
        except sqlite3.OperationalError:
            logger.info("migrating_traces_schema_to_v1.0.1")
            self._conn.executescript("""
                ALTER TABLE traces ADD COLUMN workflow_id TEXT;
                ALTER TABLE traces ADD COLUMN schema_version TEXT;
                ALTER TABLE traces ADD COLUMN kernel_version TEXT;
            """)

        # Check if we need to add new columns to tool_calls (v1.0.1)
        try:
            self._conn.execute("SELECT effective_side_effect FROM tool_calls LIMIT 1")
        except sqlite3.OperationalError:
            logger.info("migrating_tool_calls_schema_to_v1.0.1")
            self._conn.executescript("""
                ALTER TABLE tool_calls ADD COLUMN requested_side_effect TEXT;
                ALTER TABLE tool_calls ADD COLUMN requested_requires_approval INTEGER;
                ALTER TABLE tool_calls ADD COLUMN effective_side_effect TEXT;
                ALTER TABLE tool_calls ADD COLUMN effective_requires_approval INTEGER;
                ALTER TABLE tool_calls ADD COLUMN idempotency_key TEXT;
                ALTER TABLE tool_calls ADD COLUMN schema_version TEXT;
                ALTER TABLE tool_calls ADD COLUMN kernel_version TEXT;
            """)

        # Check if we need to add input/output/error columns (v1.0.8)
        try:
            self._conn.execute("SELECT input_json FROM tool_calls LIMIT 1")
        except sqlite3.OperationalError:
            logger.info("migrating_tool_calls_schema_to_v1.0.8")
            self._conn.executescript("""
                ALTER TABLE tool_calls ADD COLUMN input_json TEXT;
                ALTER TABLE tool_calls ADD COLUMN output_json TEXT;
                ALTER TABLE tool_calls ADD COLUMN error_json TEXT;
            """)

        self._conn.commit()

    def write(self, trace: DecisionTrace) -> None:
        """Write a trace to SQLite."""
        trace_json = trace.model_dump_json()

        self._conn.execute(
            """
            INSERT OR REPLACE INTO traces
            (trace_id, run_id, workflow_id, agent_profile_id, engine_id, intent,
             timestamp, outcome_status, trace_json, schema_version, kernel_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace.trace_id,
                trace.run_id,
                trace.workflow_id,
                trace.agent_profile_id,
                trace.engine_id,
                trace.intent,
                trace.timestamp.isoformat(),
                trace.outcome.status.value,
                trace_json,
                trace.schema_version,
                trace.kernel_version,
            ),
        )

        # Store tool calls for indexed queries
        for tc in trace.tool_calls:
            # Serialize input/output/error as JSON
            input_json = json.dumps(tc.input) if tc.input else None
            output_json = json.dumps(tc.output) if tc.output else None
            error_json = None
            if tc.error:
                error_json = json.dumps({
                    "code": tc.error.code if hasattr(tc.error, 'code') else None,
                    "message": tc.error.message if hasattr(tc.error, 'message') else str(tc.error),
                    "retryable": tc.error.retryable if hasattr(tc.error, 'retryable') else False,
                })

            self._conn.execute(
                """
                INSERT OR REPLACE INTO tool_calls
                (tool_call_id, trace_id, capability_name, status, started_at, duration_ms,
                 input_json, output_json, error_json,
                 requested_side_effect, requested_requires_approval,
                 effective_side_effect, effective_requires_approval,
                 idempotency_key, schema_version, kernel_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tc.tool_call_id,
                    trace.trace_id,
                    tc.capability_name,
                    tc.status.value,
                    tc.started_at.isoformat(),
                    tc.duration_ms,
                    input_json,
                    output_json,
                    error_json,
                    tc.requested_side_effect.value if tc.requested_side_effect else None,
                    1 if tc.requested_requires_approval else 0 if tc.requested_requires_approval is not None else None,
                    tc.effective_side_effect.value,
                    1 if tc.effective_requires_approval else 0,
                    tc.idempotency_key,
                    tc.schema_version,
                    tc.kernel_version,
                ),
            )

        # Store LLM calls (new in v1.0.1)
        for llm in trace.llm_calls:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO llm_calls
                (llm_call_id, trace_id, stage, started_at, ended_at, duration_ms,
                 model, provider, input_tokens, output_tokens, total_tokens, tier,
                 request_json, response_json, request_hash, response_hash,
                 schema_version, kernel_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    llm.llm_call_id,
                    trace.trace_id,
                    llm.stage,
                    llm.started_at.isoformat(),
                    llm.ended_at.isoformat(),
                    llm.duration_ms,
                    llm.request.model,
                    llm.request.provider,
                    llm.response.usage.input_tokens if llm.response.usage else None,
                    llm.response.usage.output_tokens if llm.response.usage else None,
                    llm.response.usage.total_tokens if llm.response.usage else None,
                    llm.tier,
                    llm.request.model_dump_json(),
                    llm.response.model_dump_json(),
                    llm.request_hash,
                    llm.response_hash,
                    llm.schema_version,
                    llm.kernel_version,
                ),
            )

        self._conn.commit()
        logger.debug("trace_written", trace_id=trace.trace_id)

    def get(self, trace_id: str) -> DecisionTrace | None:
        """Retrieve a trace by ID."""
        cursor = self._conn.execute(
            "SELECT trace_json FROM traces WHERE trace_id = ?",
            (trace_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        return DecisionTrace.model_validate_json(row["trace_json"])

    def get_or_raise(self, trace_id: str) -> DecisionTrace:
        """Retrieve a trace by ID or raise if not found."""
        trace = self.get(trace_id)
        if trace is None:
            raise TraceNotFoundError(trace_id)
        return trace

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
        """List traces with optional filtering."""
        conditions: list[str] = []
        params: list[Any] = []

        if agent_profile_id:
            conditions.append("agent_profile_id = ?")
            params.append(agent_profile_id)

        if workflow_id:
            conditions.append("run_id LIKE ?")
            params.append(f"{workflow_id}%")

        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())

        if until:
            conditions.append("timestamp <= ?")
            params.append(until.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        cursor = self._conn.execute(
            f"""
            SELECT trace_json FROM traces
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            """,
            params,
        )

        return [
            DecisionTrace.model_validate_json(row["trace_json"])
            for row in cursor.fetchall()
        ]

    def count(
        self,
        *,
        agent_profile_id: str | None = None,
        since: datetime | None = None,
    ) -> int:
        """Count traces matching criteria."""
        conditions: list[str] = []
        params: list[Any] = []

        if agent_profile_id:
            conditions.append("agent_profile_id = ?")
            params.append(agent_profile_id)

        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        cursor = self._conn.execute(
            f"SELECT COUNT(*) as cnt FROM traces WHERE {where_clause}",
            params,
        )
        return cursor.fetchone()["cnt"]

    def get_tool_call_stats(
        self,
        *,
        capability_name: str | None = None,
        since: datetime | None = None,
    ) -> dict[str, Any]:
        """Get statistics about tool calls.

        Args:
            capability_name: Filter by capability.
            since: Filter by time.

        Returns:
            Dictionary with call counts, success rates, avg duration.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if capability_name:
            conditions.append("capability_name = ?")
            params.append(capability_name)

        if since:
            conditions.append("started_at >= ?")
            params.append(since.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        cursor = self._conn.execute(
            f"""
            SELECT
                COUNT(*) as total_calls,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successes,
                AVG(duration_ms) as avg_duration_ms
            FROM tool_calls
            WHERE {where_clause}
            """,
            params,
        )
        row = cursor.fetchone()

        total = row["total_calls"] or 0
        successes = row["successes"] or 0
        avg_duration = row["avg_duration_ms"] or 0

        return {
            "total_calls": total,
            "successes": successes,
            "failures": total - successes,
            "success_rate": successes / total if total > 0 else 1.0,
            "avg_duration_ms": round(avg_duration, 2),
        }

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
        logger.info("sqlite_trace_sink_closed")
