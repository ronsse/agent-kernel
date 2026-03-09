"""PostgreSQL implementation of ExperienceStore."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog

from agent_kernel.core.schemas.base import SCHEMA_VERSION, get_kernel_version, utc_now
from agent_kernel.core.schemas.experience import (
    ExperienceCase,
    FailureCategory,
    LessonLearned,
    LessonScope,
    OutcomeEvaluation,
    OutcomeLabel,
    Playbook,
    PlaybookSelector,
)
from agent_kernel.memory.experience_store import ExperienceStore
from agent_kernel.memory.postgres.connection import PostgresConnection, PostgresConnectionPool

logger = structlog.get_logger(__name__)


class PostgresExperienceStore(ExperienceStore):
    """PostgreSQL-backed experience store implementation."""

    def __init__(self, pool: PostgresConnectionPool) -> None:
        self._pool = pool
        self._init_schema()
        logger.info("postgres_experience_store_initialized")

    def _init_schema(self) -> None:
        """Initialize database schema."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS outcome_evaluations (
                        evaluation_id TEXT PRIMARY KEY,
                        trace_id TEXT NOT NULL,
                        run_id TEXT,
                        label TEXT NOT NULL,
                        rating INTEGER,
                        failure_category TEXT,
                        feedback TEXT,
                        evaluator TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        metadata_json JSONB,
                        schema_version TEXT NOT NULL,
                        kernel_version TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_eval_trace
                        ON outcome_evaluations(trace_id);
                    CREATE INDEX IF NOT EXISTS idx_eval_label
                        ON outcome_evaluations(label);
                    CREATE INDEX IF NOT EXISTS idx_eval_created
                        ON outcome_evaluations(created_at);

                    CREATE TABLE IF NOT EXISTS experience_cases (
                        case_id TEXT PRIMARY KEY,
                        trace_id TEXT NOT NULL UNIQUE,
                        intent TEXT NOT NULL,
                        intent_embedding_id TEXT,
                        context_summary TEXT,
                        plan_summary TEXT,
                        outcome_summary TEXT,
                        workflow_id TEXT,
                        agent_profile_id TEXT,
                        capability_names_json JSONB,
                        sources_used_json JSONB,
                        entity_types_used_json JSONB,
                        label TEXT NOT NULL,
                        rating INTEGER,
                        failure_category TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        schema_version TEXT NOT NULL,
                        kernel_version TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_cases_workflow
                        ON experience_cases(workflow_id);
                    CREATE INDEX IF NOT EXISTS idx_cases_label
                        ON experience_cases(label);

                    CREATE TABLE IF NOT EXISTS lessons (
                        lesson_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        lesson_text TEXT NOT NULL,
                        scope_json JSONB,
                        source_trace_ids_json JSONB,
                        source_case_ids_json JSONB,
                        confidence REAL NOT NULL,
                        status TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        schema_version TEXT NOT NULL,
                        kernel_version TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_lessons_status
                        ON lessons(status);

                    CREATE TABLE IF NOT EXISTS playbooks (
                        playbook_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        description TEXT,
                        version TEXT NOT NULL,
                        selectors_json JSONB,
                        required_entity_types_json JSONB,
                        required_sources_json JSONB,
                        output_format_refs_json JSONB,
                        checklist_json JSONB,
                        pitfalls_json JSONB,
                        recommended_thinking_tier INTEGER,
                        derived_from_lessons_json JSONB,
                        status TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        schema_version TEXT NOT NULL,
                        kernel_version TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_playbooks_status
                        ON playbooks(status);
                """)

    # === Outcome Evaluations ===

    def put_evaluation(self, evaluation: OutcomeEvaluation) -> None:
        """Store or update an outcome evaluation."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO outcome_evaluations (
                        evaluation_id, trace_id, run_id, label, rating,
                        failure_category, feedback, evaluator, created_at,
                        metadata_json, schema_version, kernel_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (evaluation_id) DO UPDATE SET
                        label = EXCLUDED.label,
                        rating = EXCLUDED.rating,
                        feedback = EXCLUDED.feedback
                    """,
                    (
                        evaluation.evaluation_id,
                        evaluation.trace_id,
                        evaluation.run_id,
                        evaluation.label.value,
                        evaluation.rating,
                        evaluation.failure_category.value if evaluation.failure_category else None,
                        evaluation.feedback,
                        evaluation.evaluator,
                        evaluation.created_at.isoformat(),
                        json.dumps(evaluation.metadata) if evaluation.metadata else None,
                        SCHEMA_VERSION,
                        get_kernel_version(),
                    ),
                )

    def get_evaluation(self, evaluation_id: str) -> OutcomeEvaluation | None:
        """Get an evaluation by ID."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM outcome_evaluations WHERE evaluation_id = %s",
                    (evaluation_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                columns = [desc[0] for desc in cur.description]
                return self._row_to_evaluation(dict(zip(columns, row)))

    def get_evaluations_for_trace(self, trace_id: str) -> list[OutcomeEvaluation]:
        """Get all evaluations for a trace."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM outcome_evaluations WHERE trace_id = %s ORDER BY created_at DESC",
                    (trace_id,),
                )
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
        return [self._row_to_evaluation(dict(zip(columns, row))) for row in rows]

    def list_evaluations(
        self,
        since: datetime | None = None,
        label: OutcomeLabel | None = None,
        limit: int = 100,
    ) -> list[OutcomeEvaluation]:
        """List evaluations with optional filtering."""
        conditions: list[str] = []
        params: list[Any] = []

        if since:
            conditions.append("created_at >= %s")
            params.append(since.isoformat())
        if label:
            conditions.append("label = %s")
            params.append(label.value)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM outcome_evaluations
                    {where_clause}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
        return [self._row_to_evaluation(dict(zip(columns, row))) for row in rows]

    def _row_to_evaluation(self, row: dict[str, Any]) -> OutcomeEvaluation:
        """Convert a database row dict to OutcomeEvaluation."""
        metadata = row.get("metadata_json")
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}
        elif metadata is None:
            metadata = {}

        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        return OutcomeEvaluation(
            evaluation_id=row["evaluation_id"],
            trace_id=row["trace_id"],
            run_id=row.get("run_id"),
            label=OutcomeLabel(row["label"]),
            rating=row.get("rating"),
            failure_category=FailureCategory(row["failure_category"]) if row.get("failure_category") else None,
            feedback=row.get("feedback"),
            evaluator=row.get("evaluator"),
            created_at=created_at,
            metadata=metadata,
        )

    # === Experience Cases ===

    def put_case(self, case: ExperienceCase) -> None:
        """Store or update an experience case."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO experience_cases (
                        case_id, trace_id, intent, intent_embedding_id,
                        context_summary, plan_summary, outcome_summary,
                        workflow_id, agent_profile_id, capability_names_json,
                        sources_used_json, entity_types_used_json,
                        label, rating, failure_category,
                        created_at, updated_at, schema_version, kernel_version
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (case_id) DO UPDATE SET
                        label = EXCLUDED.label,
                        rating = EXCLUDED.rating,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        case.case_id, case.trace_id, case.intent, case.intent_embedding_id,
                        case.context_summary, case.plan_summary, case.outcome_summary,
                        case.workflow_id, case.agent_profile_id,
                        json.dumps(case.capability_names) if case.capability_names else None,
                        json.dumps(case.sources_used) if case.sources_used else None,
                        json.dumps(case.entity_types_used) if case.entity_types_used else None,
                        case.label.value, case.rating,
                        case.failure_category.value if case.failure_category else None,
                        case.created_at.isoformat(), case.updated_at.isoformat(),
                        SCHEMA_VERSION, get_kernel_version(),
                    ),
                )

    def get_case(self, case_id: str) -> ExperienceCase | None:
        """Get a case by ID."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM experience_cases WHERE case_id = %s", (case_id,))
                row = cur.fetchone()
                if not row:
                    return None
                columns = [desc[0] for desc in cur.description]
                return self._row_to_case(dict(zip(columns, row)))

    def get_case_for_trace(self, trace_id: str) -> ExperienceCase | None:
        """Get the case for a trace."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM experience_cases WHERE trace_id = %s", (trace_id,))
                row = cur.fetchone()
                if not row:
                    return None
                columns = [desc[0] for desc in cur.description]
                return self._row_to_case(dict(zip(columns, row)))

    def find_similar_cases(
        self,
        workflow_id: str | None = None,
        capability_names: list[str] | None = None,
        label: OutcomeLabel | None = None,
        limit: int = 10,
    ) -> list[ExperienceCase]:
        """Find cases similar to the given criteria."""
        conditions: list[str] = []
        params: list[Any] = []

        if workflow_id:
            conditions.append("workflow_id = %s")
            params.append(workflow_id)
        if label:
            conditions.append("label = %s")
            params.append(label.value)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM experience_cases
                    {where_clause}
                    ORDER BY created_at DESC LIMIT %s
                    """,
                    params,
                )
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()

        cases = [self._row_to_case(dict(zip(columns, row))) for row in rows]

        if capability_names:
            filtered = [
                c for c in cases
                if any(cap in c.capability_names for cap in capability_names)
            ]
            return filtered[:limit]

        return cases

    def list_cases(
        self,
        workflow_id: str | None = None,
        label: OutcomeLabel | None = None,
        limit: int = 100,
    ) -> list[ExperienceCase]:
        """List cases with optional filtering."""
        return self.find_similar_cases(workflow_id=workflow_id, label=label, limit=limit)

    def _row_to_case(self, row: dict[str, Any]) -> ExperienceCase:
        """Convert a database row dict to ExperienceCase."""
        def _parse_json_list(val: Any) -> list:
            if isinstance(val, list):
                return val
            if isinstance(val, str):
                return json.loads(val) if val else []
            return val or []

        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        updated_at = row["updated_at"]
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        return ExperienceCase(
            case_id=row["case_id"],
            trace_id=row["trace_id"],
            intent=row["intent"],
            intent_embedding_id=row.get("intent_embedding_id"),
            context_summary=row.get("context_summary"),
            plan_summary=row.get("plan_summary"),
            outcome_summary=row.get("outcome_summary"),
            workflow_id=row.get("workflow_id"),
            agent_profile_id=row.get("agent_profile_id"),
            capability_names=_parse_json_list(row.get("capability_names_json")),
            sources_used=_parse_json_list(row.get("sources_used_json")),
            entity_types_used=_parse_json_list(row.get("entity_types_used_json")),
            label=OutcomeLabel(row["label"]),
            rating=row.get("rating"),
            failure_category=FailureCategory(row["failure_category"]) if row.get("failure_category") else None,
            created_at=created_at,
            updated_at=updated_at,
        )

    # === Lessons ===

    def put_lesson(self, lesson: LessonLearned) -> None:
        """Store or update a lesson."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO lessons (
                        lesson_id, title, lesson_text, scope_json,
                        source_trace_ids_json, source_case_ids_json,
                        confidence, status, created_at, updated_at,
                        schema_version, kernel_version
                    ) VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (lesson_id) DO UPDATE SET
                        lesson_text = EXCLUDED.lesson_text,
                        status = EXCLUDED.status,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        lesson.lesson_id, lesson.title, lesson.lesson_text,
                        lesson.scope.model_dump_json() if lesson.scope else None,
                        json.dumps(lesson.source_trace_ids) if lesson.source_trace_ids else None,
                        json.dumps(lesson.source_case_ids) if lesson.source_case_ids else None,
                        lesson.confidence, lesson.status,
                        lesson.created_at.isoformat(), lesson.updated_at.isoformat(),
                        SCHEMA_VERSION, get_kernel_version(),
                    ),
                )

    def get_lesson(self, lesson_id: str) -> LessonLearned | None:
        """Get a lesson by ID."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM lessons WHERE lesson_id = %s", (lesson_id,))
                row = cur.fetchone()
                if not row:
                    return None
                columns = [desc[0] for desc in cur.description]
                return self._row_to_lesson(dict(zip(columns, row)))

    def list_lessons(
        self,
        scope: LessonScope | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[LessonLearned]:
        """List lessons with optional filtering."""
        conditions: list[str] = []
        params: list[Any] = []

        if status:
            conditions.append("status = %s")
            params.append(status)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM lessons
                    {where_clause}
                    ORDER BY confidence DESC, created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()

        lessons = [self._row_to_lesson(dict(zip(columns, row))) for row in rows]

        if scope:
            filtered = []
            for lesson in lessons:
                match = True
                if scope.workflow_id and lesson.scope.workflow_id != scope.workflow_id:
                    match = False
                if scope.capability_name and lesson.scope.capability_name != scope.capability_name:
                    match = False
                if match:
                    filtered.append(lesson)
            return filtered[:limit]

        return lessons

    def activate_lesson(self, lesson_id: str) -> bool:
        """Activate a candidate lesson."""
        now = utc_now()
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE lessons SET status = 'active', updated_at = %s
                    WHERE lesson_id = %s AND status = 'candidate'
                    """,
                    (now.isoformat(), lesson_id),
                )
                return cur.rowcount > 0

    def deprecate_lesson(self, lesson_id: str) -> bool:
        """Deprecate a lesson."""
        now = utc_now()
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE lessons SET status = 'deprecated', updated_at = %s WHERE lesson_id = %s",
                    (now.isoformat(), lesson_id),
                )
                return cur.rowcount > 0

    def _row_to_lesson(self, row: dict[str, Any]) -> LessonLearned:
        """Convert a database row dict to LessonLearned."""
        scope_data = row.get("scope_json")
        if isinstance(scope_data, str):
            scope_data = json.loads(scope_data) if scope_data else {}
        elif scope_data is None:
            scope_data = {}

        def _parse_json_list(val: Any) -> list:
            if isinstance(val, list):
                return val
            if isinstance(val, str):
                return json.loads(val) if val else []
            return val or []

        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        updated_at = row["updated_at"]
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        return LessonLearned(
            lesson_id=row["lesson_id"],
            title=row["title"],
            lesson_text=row["lesson_text"],
            scope=LessonScope(**scope_data),
            source_trace_ids=_parse_json_list(row.get("source_trace_ids_json")),
            source_case_ids=_parse_json_list(row.get("source_case_ids_json")),
            confidence=row["confidence"],
            status=row["status"],
            created_at=created_at,
            updated_at=updated_at,
        )

    # === Playbooks ===

    def put_playbook(self, playbook: Playbook) -> None:
        """Store or update a playbook."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO playbooks (
                        playbook_id, name, description, version,
                        selectors_json, required_entity_types_json,
                        required_sources_json, output_format_refs_json,
                        checklist_json, pitfalls_json, recommended_thinking_tier,
                        derived_from_lessons_json, status,
                        created_at, updated_at, schema_version, kernel_version
                    ) VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s,%s,%s,%s,%s)
                    ON CONFLICT (playbook_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        playbook.playbook_id, playbook.name, playbook.description, playbook.version,
                        json.dumps([s.model_dump() for s in playbook.selectors]) if playbook.selectors else None,
                        json.dumps(playbook.required_entity_types) if playbook.required_entity_types else None,
                        json.dumps(playbook.required_sources) if playbook.required_sources else None,
                        json.dumps([r.model_dump() for r in playbook.output_format_refs]) if playbook.output_format_refs else None,
                        json.dumps(playbook.checklist) if playbook.checklist else None,
                        json.dumps(playbook.pitfalls) if playbook.pitfalls else None,
                        playbook.recommended_thinking_tier,
                        json.dumps(playbook.derived_from_lessons) if playbook.derived_from_lessons else None,
                        playbook.status,
                        playbook.created_at.isoformat(), playbook.updated_at.isoformat(),
                        SCHEMA_VERSION, get_kernel_version(),
                    ),
                )

    def get_playbook(self, playbook_id: str) -> Playbook | None:
        """Get a playbook by ID."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM playbooks WHERE playbook_id = %s", (playbook_id,))
                row = cur.fetchone()
                if not row:
                    return None
                columns = [desc[0] for desc in cur.description]
                return self._row_to_playbook(dict(zip(columns, row)))

    def find_playbooks(
        self,
        workflow_id: str | None = None,
        capability_names: list[str] | None = None,
        intent_keywords: list[str] | None = None,
    ) -> list[Playbook]:
        """Find playbooks matching the given criteria."""
        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM playbooks WHERE status = 'active' ORDER BY created_at DESC"
                )
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()

        playbooks = [self._row_to_playbook(dict(zip(columns, row))) for row in rows]

        matched = []
        for playbook in playbooks:
            for selector in playbook.selectors:
                match = True
                if workflow_id and selector.workflow_id and selector.workflow_id != workflow_id:
                    match = False
                if capability_names and selector.capability_names:
                    if not any(cap in selector.capability_names for cap in capability_names):
                        match = False
                if intent_keywords and selector.intent_contains:
                    if not any(kw in intent_keywords for kw in selector.intent_contains):
                        match = False
                if match:
                    matched.append(playbook)
                    break
        return matched

    def list_playbooks(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Playbook]:
        """List playbooks with optional filtering."""
        conditions: list[str] = []
        params: list[Any] = []

        if status:
            conditions.append("status = %s")
            params.append(status)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)

        with PostgresConnection(self._pool) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM playbooks
                    {where_clause}
                    ORDER BY created_at DESC LIMIT %s
                    """,
                    params,
                )
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()

        return [self._row_to_playbook(dict(zip(columns, row))) for row in rows]

    def _row_to_playbook(self, row: dict[str, Any]) -> Playbook:
        """Convert a database row dict to Playbook."""
        from agent_kernel.core.schemas.context import ContextRef

        def _parse_json(val: Any) -> Any:
            if isinstance(val, str):
                return json.loads(val) if val else None
            return val

        selectors_data = _parse_json(row.get("selectors_json")) or []
        output_refs_data = _parse_json(row.get("output_format_refs_json")) or []

        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        updated_at = row["updated_at"]
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        return Playbook(
            playbook_id=row["playbook_id"],
            name=row["name"],
            description=row.get("description"),
            version=row["version"],
            selectors=[PlaybookSelector(**s) for s in selectors_data],
            required_entity_types=_parse_json(row.get("required_entity_types_json")) or [],
            required_sources=_parse_json(row.get("required_sources_json")) or [],
            output_format_refs=[ContextRef(**r) for r in output_refs_data],
            checklist=_parse_json(row.get("checklist_json")) or [],
            pitfalls=_parse_json(row.get("pitfalls_json")) or [],
            recommended_thinking_tier=row.get("recommended_thinking_tier"),
            derived_from_lessons=_parse_json(row.get("derived_from_lessons_json")) or [],
            status=row["status"],
            created_at=created_at,
            updated_at=updated_at,
        )
