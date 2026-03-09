"""PostgreSQL-backed trace storage."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog

from agent_kernel.core.errors import TraceNotFoundError
from agent_kernel.core.schemas import DecisionTrace
from agent_kernel.memory.postgres.connection import PostgresConnection, PostgresConnectionPool
from agent_kernel.tracing.trace_store import TraceStore

logger = structlog.get_logger(__name__)


class PostgresTraceSink(TraceStore):
    """PostgreSQL implementation of TraceStore.

    Mirrors the SQLiteTraceSink schema using PostgreSQL with JSONB.
    """

    def __init__(self, pool: PostgresConnectionPool) -> None:
        self._pool = pool
        self._init_schema()
        logger.info("postgres_trace_sink_initialized")

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS traces (
                        trace_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        workflow_id TEXT,
                        agent_profile_id TEXT NOT NULL,
                        engine_id TEXT NOT NULL,
                        intent TEXT NOT NULL,
                        timestamp TIMESTAMPTZ NOT NULL,
                        outcome_status TEXT NOT NULL,
                        trace_json JSONB NOT NULL,
                        schema_version TEXT,
                        kernel_version TEXT,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    );

                    CREATE INDEX IF NOT EXISTS idx_pg_traces_timestamp
                        ON traces(timestamp DESC);
                    CREATE INDEX IF NOT EXISTS idx_pg_traces_agent_profile
                        ON traces(agent_profile_id);
                    CREATE INDEX IF NOT EXISTS idx_pg_traces_run_id
                        ON traces(run_id);
                    CREATE INDEX IF NOT EXISTS idx_pg_traces_workflow_id
                        ON traces(workflow_id);
                    CREATE INDEX IF NOT EXISTS idx_pg_traces_outcome
                        ON traces(outcome_status);

                    -- v1.0.8: Added input_json, output_json, error_json columns
                    CREATE TABLE IF NOT EXISTS tool_calls (
                        tool_call_id TEXT PRIMARY KEY,
                        trace_id TEXT NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
                        capability_name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        started_at TIMESTAMPTZ NOT NULL,
                        duration_ms INTEGER NOT NULL,
                        input_json JSONB,
                        output_json JSONB,
                        error_json JSONB,
                        requested_side_effect TEXT,
                        requested_requires_approval BOOLEAN,
                        effective_side_effect TEXT,
                        effective_requires_approval BOOLEAN,
                        idempotency_key TEXT,
                        schema_version TEXT,
                        kernel_version TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_pg_tool_calls_trace
                        ON tool_calls(trace_id);
                    CREATE INDEX IF NOT EXISTS idx_pg_tool_calls_capability
                        ON tool_calls(capability_name);

                    CREATE TABLE IF NOT EXISTS llm_calls (
                        llm_call_id TEXT PRIMARY KEY,
                        trace_id TEXT NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
                        stage TEXT NOT NULL,
                        started_at TIMESTAMPTZ NOT NULL,
                        ended_at TIMESTAMPTZ NOT NULL,
                        duration_ms INTEGER NOT NULL,
                        model TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        input_tokens INTEGER,
                        output_tokens INTEGER,
                        total_tokens INTEGER,
                        tier INTEGER,
                        request_json JSONB,
                        response_json JSONB,
                        request_hash TEXT,
                        response_hash TEXT,
                        schema_version TEXT,
                        kernel_version TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_pg_llm_calls_trace
                        ON llm_calls(trace_id);

                    CREATE TABLE IF NOT EXISTS workflow_runs (
                        run_id TEXT PRIMARY KEY,
                        workflow_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        intent TEXT,
                        started_at TIMESTAMPTZ,
                        ended_at TIMESTAMPTZ,
                        last_step TEXT,
                        retry_count INTEGER DEFAULT 0,
                        error_json JSONB,
                        trace_ids_json JSONB,
                        metadata_json JSONB,
                        schema_version TEXT,
                        kernel_version TEXT
                    );

                    CREATE INDEX IF NOT EXISTS idx_pg_workflow_runs_workflow
                        ON workflow_runs(workflow_id);
                    CREATE INDEX IF NOT EXISTS idx_pg_workflow_runs_status
                        ON workflow_runs(status);
                """)

    def write(self, trace: DecisionTrace) -> None:
        """Write a trace to PostgreSQL."""
        trace_json = trace.model_dump_json()

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO traces
                        (trace_id, run_id, workflow_id, agent_profile_id, engine_id,
                         intent, timestamp, outcome_status, trace_json,
                         schema_version, kernel_version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (trace_id) DO UPDATE SET
                        trace_json = EXCLUDED.trace_json,
                        outcome_status = EXCLUDED.outcome_status
                    """,
                    (
                        trace.trace_id, trace.run_id, trace.workflow_id,
                        trace.agent_profile_id, trace.engine_id, trace.intent,
                        trace.timestamp.isoformat(), trace.outcome.status.value,
                        trace_json, trace.schema_version, trace.kernel_version,
                    ),
                )

                # Store tool calls
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

                    cur.execute(
                        """
                        INSERT INTO tool_calls
                            (tool_call_id, trace_id, capability_name, status,
                             started_at, duration_ms, input_json, output_json, error_json,
                             requested_side_effect, requested_requires_approval,
                             effective_side_effect, effective_requires_approval,
                             idempotency_key, schema_version, kernel_version)
                        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (tool_call_id) DO NOTHING
                        """,
                        (
                            tc.tool_call_id, trace.trace_id, tc.capability_name,
                            tc.status.value, tc.started_at.isoformat(), tc.duration_ms,
                            input_json, output_json, error_json,
                            tc.requested_side_effect.value if tc.requested_side_effect else None,
                            tc.requested_requires_approval,
                            tc.effective_side_effect.value, tc.effective_requires_approval,
                            tc.idempotency_key, tc.schema_version, tc.kernel_version,
                        ),
                    )

                # Store LLM calls
                for llm in trace.llm_calls:
                    cur.execute(
                        """
                        INSERT INTO llm_calls
                            (llm_call_id, trace_id, stage, started_at, ended_at,
                             duration_ms, model, provider, input_tokens, output_tokens,
                             total_tokens, tier, request_json, response_json,
                             request_hash, response_hash, schema_version, kernel_version)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s)
                        ON CONFLICT (llm_call_id) DO NOTHING
                        """,
                        (
                            llm.llm_call_id, trace.trace_id, llm.stage,
                            llm.started_at.isoformat(), llm.ended_at.isoformat(),
                            llm.duration_ms, llm.request.model, llm.request.provider,
                            llm.response.usage.input_tokens if llm.response.usage else None,
                            llm.response.usage.output_tokens if llm.response.usage else None,
                            llm.response.usage.total_tokens if llm.response.usage else None,
                            llm.tier,
                            llm.request.model_dump_json(), llm.response.model_dump_json(),
                            llm.request_hash, llm.response_hash,
                            llm.schema_version, llm.kernel_version,
                        ),
                    )

        logger.debug("trace_written", trace_id=trace.trace_id)

    def get(self, trace_id: str) -> DecisionTrace | None:
        """Retrieve a trace by ID."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT trace_json FROM traces WHERE trace_id = %s",
                    (trace_id,),
                )
                row = cur.fetchone()

        if row is None:
            return None

        trace_data = row[0]
        if isinstance(trace_data, dict):
            return DecisionTrace.model_validate(trace_data)
        return DecisionTrace.model_validate_json(trace_data)

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
            conditions.append("agent_profile_id = %s")
            params.append(agent_profile_id)

        if workflow_id:
            conditions.append("run_id LIKE %s")
            params.append(f"{workflow_id}%")

        if since:
            conditions.append("timestamp >= %s")
            params.append(since.isoformat())

        if until:
            conditions.append("timestamp <= %s")
            params.append(until.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "TRUE"
        params.extend([limit, offset])

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT trace_json FROM traces
                    WHERE {where_clause}
                    ORDER BY timestamp DESC
                    LIMIT %s OFFSET %s
                    """,
                    params,
                )
                rows = cur.fetchall()

        results = []
        for row in rows:
            trace_data = row[0]
            if isinstance(trace_data, dict):
                results.append(DecisionTrace.model_validate(trace_data))
            else:
                results.append(DecisionTrace.model_validate_json(trace_data))
        return results

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
            conditions.append("agent_profile_id = %s")
            params.append(agent_profile_id)

        if since:
            conditions.append("timestamp >= %s")
            params.append(since.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "TRUE"

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) FROM traces WHERE {where_clause}",
                    params,
                )
                return cur.fetchone()[0]

    def close(self) -> None:
        """No-op; pool manages connections."""
        logger.info("postgres_trace_sink_closed")
