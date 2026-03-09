"""Experience Store - storage for experience memory (v1.0.4).

Provides:
- Outcome evaluations (user feedback on traces)
- Experience cases (compacted, retrievable case memory)
- Lessons learned (actionable guidance mined from cases)
- Playbooks (versioned behavioral patterns)

The experience store is the foundation for the learning loop:
Traces → Evaluations → Cases → Lessons → Playbooks

References:
- Design Patch v1.0.4: Universal Context System
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.ids import generate_ulid
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

logger = structlog.get_logger(__name__)


class ExperienceStore(ABC):
    """Abstract interface for experience memory storage."""

    # === Outcome Evaluations ===

    @abstractmethod
    def put_evaluation(self, evaluation: OutcomeEvaluation) -> None:
        """Store or update an outcome evaluation."""
        ...

    @abstractmethod
    def get_evaluation(self, evaluation_id: str) -> OutcomeEvaluation | None:
        """Get an evaluation by ID."""
        ...

    @abstractmethod
    def get_evaluations_for_trace(self, trace_id: str) -> list[OutcomeEvaluation]:
        """Get all evaluations for a trace."""
        ...

    @abstractmethod
    def list_evaluations(
        self,
        since: datetime | None = None,
        label: OutcomeLabel | None = None,
        limit: int = 100,
    ) -> list[OutcomeEvaluation]:
        """List evaluations with optional filtering."""
        ...

    # === Experience Cases ===

    @abstractmethod
    def put_case(self, case: ExperienceCase) -> None:
        """Store or update an experience case."""
        ...

    @abstractmethod
    def get_case(self, case_id: str) -> ExperienceCase | None:
        """Get a case by ID."""
        ...

    @abstractmethod
    def get_case_for_trace(self, trace_id: str) -> ExperienceCase | None:
        """Get the case for a trace."""
        ...

    @abstractmethod
    def find_similar_cases(
        self,
        workflow_id: str | None = None,
        capability_names: list[str] | None = None,
        label: OutcomeLabel | None = None,
        limit: int = 10,
    ) -> list[ExperienceCase]:
        """Find cases similar to the given criteria."""
        ...

    @abstractmethod
    def list_cases(
        self,
        workflow_id: str | None = None,
        label: OutcomeLabel | None = None,
        limit: int = 100,
    ) -> list[ExperienceCase]:
        """List cases with optional filtering."""
        ...

    # === Lessons ===

    @abstractmethod
    def put_lesson(self, lesson: LessonLearned) -> None:
        """Store or update a lesson."""
        ...

    @abstractmethod
    def get_lesson(self, lesson_id: str) -> LessonLearned | None:
        """Get a lesson by ID."""
        ...

    @abstractmethod
    def list_lessons(
        self,
        scope: LessonScope | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[LessonLearned]:
        """List lessons with optional filtering."""
        ...

    @abstractmethod
    def activate_lesson(self, lesson_id: str) -> bool:
        """Activate a candidate lesson. Returns True if successful."""
        ...

    @abstractmethod
    def deprecate_lesson(self, lesson_id: str) -> bool:
        """Deprecate a lesson. Returns True if successful."""
        ...

    # === Playbooks ===

    @abstractmethod
    def put_playbook(self, playbook: Playbook) -> None:
        """Store or update a playbook."""
        ...

    @abstractmethod
    def get_playbook(self, playbook_id: str) -> Playbook | None:
        """Get a playbook by ID."""
        ...

    @abstractmethod
    def find_playbooks(
        self,
        workflow_id: str | None = None,
        capability_names: list[str] | None = None,
        intent_keywords: list[str] | None = None,
    ) -> list[Playbook]:
        """Find playbooks matching the given criteria."""
        ...

    @abstractmethod
    def list_playbooks(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Playbook]:
        """List playbooks with optional filtering."""
        ...


class SQLiteExperienceStore(ExperienceStore):
    """SQLite-backed experience store implementation."""

    def __init__(self, db_path: str | Path) -> None:
        """Initialize the SQLite experience store.
        
        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            # Outcome evaluations table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS outcome_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    run_id TEXT,
                    label TEXT NOT NULL,
                    rating INTEGER,
                    failure_category TEXT,
                    feedback TEXT,
                    evaluator TEXT,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT,
                    schema_version TEXT NOT NULL,
                    kernel_version TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_eval_trace 
                ON outcome_evaluations(trace_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_eval_label 
                ON outcome_evaluations(label)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_eval_created 
                ON outcome_evaluations(created_at)
            """)

            # Experience cases table
            conn.execute("""
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
                    capability_names_json TEXT,
                    sources_used_json TEXT,
                    entity_types_used_json TEXT,
                    label TEXT NOT NULL,
                    rating INTEGER,
                    failure_category TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    kernel_version TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cases_workflow 
                ON experience_cases(workflow_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cases_label 
                ON experience_cases(label)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cases_created 
                ON experience_cases(created_at)
            """)

            # Lessons table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lessons (
                    lesson_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    lesson_text TEXT NOT NULL,
                    scope_json TEXT,
                    source_trace_ids_json TEXT,
                    source_case_ids_json TEXT,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    kernel_version TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_lessons_status 
                ON lessons(status)
            """)

            # Playbooks table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS playbooks (
                    playbook_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    version TEXT NOT NULL,
                    selectors_json TEXT,
                    required_entity_types_json TEXT,
                    required_sources_json TEXT,
                    output_format_refs_json TEXT,
                    checklist_json TEXT,
                    pitfalls_json TEXT,
                    recommended_thinking_tier INTEGER,
                    derived_from_lessons_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    kernel_version TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_playbooks_status 
                ON playbooks(status)
            """)

            conn.commit()

    # === Outcome Evaluations ===

    def put_evaluation(self, evaluation: OutcomeEvaluation) -> None:
        """Store or update an outcome evaluation."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO outcome_evaluations (
                    evaluation_id, trace_id, run_id, label, rating,
                    failure_category, feedback, evaluator, created_at,
                    metadata_json, schema_version, kernel_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            conn.commit()

        logger.debug(
            "Stored evaluation",
            evaluation_id=evaluation.evaluation_id,
            trace_id=evaluation.trace_id,
            label=evaluation.label.value,
        )

    def get_evaluation(self, evaluation_id: str) -> OutcomeEvaluation | None:
        """Get an evaluation by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM outcome_evaluations WHERE evaluation_id = ?",
                (evaluation_id,),
            ).fetchone()

        if not row:
            return None

        return self._row_to_evaluation(row)

    def get_evaluations_for_trace(self, trace_id: str) -> list[OutcomeEvaluation]:
        """Get all evaluations for a trace."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM outcome_evaluations WHERE trace_id = ? ORDER BY created_at DESC",
                (trace_id,),
            ).fetchall()

        return [self._row_to_evaluation(row) for row in rows]

    def list_evaluations(
        self,
        since: datetime | None = None,
        label: OutcomeLabel | None = None,
        limit: int = 100,
    ) -> list[OutcomeEvaluation]:
        """List evaluations with optional filtering."""
        conditions = []
        params: list[Any] = []

        if since:
            conditions.append("created_at >= ?")
            params.append(since.isoformat())
        if label:
            conditions.append("label = ?")
            params.append(label.value)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT * FROM outcome_evaluations 
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [self._row_to_evaluation(row) for row in rows]

    def _row_to_evaluation(self, row: sqlite3.Row) -> OutcomeEvaluation:
        """Convert a database row to OutcomeEvaluation."""
        return OutcomeEvaluation(
            evaluation_id=row["evaluation_id"],
            trace_id=row["trace_id"],
            run_id=row["run_id"],
            label=OutcomeLabel(row["label"]),
            rating=row["rating"],
            failure_category=FailureCategory(row["failure_category"]) if row["failure_category"] else None,
            feedback=row["feedback"],
            evaluator=row["evaluator"],
            created_at=datetime.fromisoformat(row["created_at"]),
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        )

    # === Experience Cases ===

    def put_case(self, case: ExperienceCase) -> None:
        """Store or update an experience case."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO experience_cases (
                    case_id, trace_id, intent, intent_embedding_id,
                    context_summary, plan_summary, outcome_summary,
                    workflow_id, agent_profile_id, capability_names_json,
                    sources_used_json, entity_types_used_json,
                    label, rating, failure_category,
                    created_at, updated_at, schema_version, kernel_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case.case_id,
                    case.trace_id,
                    case.intent,
                    case.intent_embedding_id,
                    case.context_summary,
                    case.plan_summary,
                    case.outcome_summary,
                    case.workflow_id,
                    case.agent_profile_id,
                    json.dumps(case.capability_names) if case.capability_names else None,
                    json.dumps(case.sources_used) if case.sources_used else None,
                    json.dumps(case.entity_types_used) if case.entity_types_used else None,
                    case.label.value,
                    case.rating,
                    case.failure_category.value if case.failure_category else None,
                    case.created_at.isoformat(),
                    case.updated_at.isoformat(),
                    SCHEMA_VERSION,
                    get_kernel_version(),
                ),
            )
            conn.commit()

        logger.debug(
            "Stored case",
            case_id=case.case_id,
            trace_id=case.trace_id,
            label=case.label.value,
        )

    def get_case(self, case_id: str) -> ExperienceCase | None:
        """Get a case by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM experience_cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()

        if not row:
            return None

        return self._row_to_case(row)

    def get_case_for_trace(self, trace_id: str) -> ExperienceCase | None:
        """Get the case for a trace."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM experience_cases WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()

        if not row:
            return None

        return self._row_to_case(row)

    def find_similar_cases(
        self,
        workflow_id: str | None = None,
        capability_names: list[str] | None = None,
        label: OutcomeLabel | None = None,
        limit: int = 10,
    ) -> list[ExperienceCase]:
        """Find cases similar to the given criteria."""
        conditions = []
        params: list[Any] = []

        if workflow_id:
            conditions.append("workflow_id = ?")
            params.append(workflow_id)
        if label:
            conditions.append("label = ?")
            params.append(label.value)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT * FROM experience_cases 
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        cases = [self._row_to_case(row) for row in rows]

        # Filter by capability names in Python (JSON array matching is complex in SQLite)
        if capability_names:
            filtered = []
            for case in cases:
                if any(cap in case.capability_names for cap in capability_names):
                    filtered.append(case)
            return filtered[:limit]

        return cases

    def list_cases(
        self,
        workflow_id: str | None = None,
        label: OutcomeLabel | None = None,
        limit: int = 100,
    ) -> list[ExperienceCase]:
        """List cases with optional filtering."""
        return self.find_similar_cases(
            workflow_id=workflow_id,
            label=label,
            limit=limit,
        )

    def _row_to_case(self, row: sqlite3.Row) -> ExperienceCase:
        """Convert a database row to ExperienceCase."""
        return ExperienceCase(
            case_id=row["case_id"],
            trace_id=row["trace_id"],
            intent=row["intent"],
            intent_embedding_id=row["intent_embedding_id"],
            context_summary=row["context_summary"],
            plan_summary=row["plan_summary"],
            outcome_summary=row["outcome_summary"],
            workflow_id=row["workflow_id"],
            agent_profile_id=row["agent_profile_id"],
            capability_names=json.loads(row["capability_names_json"]) if row["capability_names_json"] else [],
            sources_used=json.loads(row["sources_used_json"]) if row["sources_used_json"] else [],
            entity_types_used=json.loads(row["entity_types_used_json"]) if row["entity_types_used_json"] else [],
            label=OutcomeLabel(row["label"]),
            rating=row["rating"],
            failure_category=FailureCategory(row["failure_category"]) if row["failure_category"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # === Lessons ===

    def put_lesson(self, lesson: LessonLearned) -> None:
        """Store or update a lesson."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO lessons (
                    lesson_id, title, lesson_text, scope_json,
                    source_trace_ids_json, source_case_ids_json,
                    confidence, status, created_at, updated_at,
                    schema_version, kernel_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lesson.lesson_id,
                    lesson.title,
                    lesson.lesson_text,
                    lesson.scope.model_dump_json() if lesson.scope else None,
                    json.dumps(lesson.source_trace_ids) if lesson.source_trace_ids else None,
                    json.dumps(lesson.source_case_ids) if lesson.source_case_ids else None,
                    lesson.confidence,
                    lesson.status,
                    lesson.created_at.isoformat(),
                    lesson.updated_at.isoformat(),
                    SCHEMA_VERSION,
                    get_kernel_version(),
                ),
            )
            conn.commit()

        logger.debug(
            "Stored lesson",
            lesson_id=lesson.lesson_id,
            status=lesson.status,
        )

    def get_lesson(self, lesson_id: str) -> LessonLearned | None:
        """Get a lesson by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM lessons WHERE lesson_id = ?",
                (lesson_id,),
            ).fetchone()

        if not row:
            return None

        return self._row_to_lesson(row)

    def list_lessons(
        self,
        scope: LessonScope | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[LessonLearned]:
        """List lessons with optional filtering."""
        conditions = []
        params: list[Any] = []

        if status:
            conditions.append("status = ?")
            params.append(status)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT * FROM lessons 
                {where_clause}
                ORDER BY confidence DESC, created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        lessons = [self._row_to_lesson(row) for row in rows]

        # Filter by scope in Python if provided
        if scope:
            filtered = []
            for lesson in lessons:
                match = True
                if scope.workflow_id and lesson.scope.workflow_id != scope.workflow_id:
                    match = False
                if scope.capability_name and lesson.scope.capability_name != scope.capability_name:
                    match = False
                if scope.entity_type and lesson.scope.entity_type != scope.entity_type:
                    match = False
                if scope.project_id and lesson.scope.project_id != scope.project_id:
                    match = False
                if match:
                    filtered.append(lesson)
            return filtered[:limit]

        return lessons

    def activate_lesson(self, lesson_id: str) -> bool:
        """Activate a candidate lesson."""
        now = utc_now()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE lessons 
                SET status = 'active', updated_at = ?
                WHERE lesson_id = ? AND status = 'candidate'
                """,
                (now.isoformat(), lesson_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def deprecate_lesson(self, lesson_id: str) -> bool:
        """Deprecate a lesson."""
        now = utc_now()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE lessons 
                SET status = 'deprecated', updated_at = ?
                WHERE lesson_id = ?
                """,
                (now.isoformat(), lesson_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def _row_to_lesson(self, row: sqlite3.Row) -> LessonLearned:
        """Convert a database row to LessonLearned."""
        scope_data = json.loads(row["scope_json"]) if row["scope_json"] else {}
        return LessonLearned(
            lesson_id=row["lesson_id"],
            title=row["title"],
            lesson_text=row["lesson_text"],
            scope=LessonScope(**scope_data),
            source_trace_ids=json.loads(row["source_trace_ids_json"]) if row["source_trace_ids_json"] else [],
            source_case_ids=json.loads(row["source_case_ids_json"]) if row["source_case_ids_json"] else [],
            confidence=row["confidence"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # === Playbooks ===

    def put_playbook(self, playbook: Playbook) -> None:
        """Store or update a playbook."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO playbooks (
                    playbook_id, name, description, version,
                    selectors_json, required_entity_types_json,
                    required_sources_json, output_format_refs_json,
                    checklist_json, pitfalls_json, recommended_thinking_tier,
                    derived_from_lessons_json, status,
                    created_at, updated_at, schema_version, kernel_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    playbook.playbook_id,
                    playbook.name,
                    playbook.description,
                    playbook.version,
                    json.dumps([s.model_dump() for s in playbook.selectors]) if playbook.selectors else None,
                    json.dumps(playbook.required_entity_types) if playbook.required_entity_types else None,
                    json.dumps(playbook.required_sources) if playbook.required_sources else None,
                    json.dumps([r.model_dump() for r in playbook.output_format_refs]) if playbook.output_format_refs else None,
                    json.dumps(playbook.checklist) if playbook.checklist else None,
                    json.dumps(playbook.pitfalls) if playbook.pitfalls else None,
                    playbook.recommended_thinking_tier,
                    json.dumps(playbook.derived_from_lessons) if playbook.derived_from_lessons else None,
                    playbook.status,
                    playbook.created_at.isoformat(),
                    playbook.updated_at.isoformat(),
                    SCHEMA_VERSION,
                    get_kernel_version(),
                ),
            )
            conn.commit()

        logger.debug(
            "Stored playbook",
            playbook_id=playbook.playbook_id,
            status=playbook.status,
        )

    def get_playbook(self, playbook_id: str) -> Playbook | None:
        """Get a playbook by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM playbooks WHERE playbook_id = ?",
                (playbook_id,),
            ).fetchone()

        if not row:
            return None

        return self._row_to_playbook(row)

    def find_playbooks(
        self,
        workflow_id: str | None = None,
        capability_names: list[str] | None = None,
        intent_keywords: list[str] | None = None,
    ) -> list[Playbook]:
        """Find playbooks matching the given criteria."""
        # Get all active playbooks and filter in Python
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM playbooks WHERE status = 'active' ORDER BY created_at DESC",
            ).fetchall()

        playbooks = [self._row_to_playbook(row) for row in rows]

        # Filter by criteria
        matched = []
        for playbook in playbooks:
            for selector in playbook.selectors:
                match = True

                if workflow_id and selector.workflow_id:
                    if selector.workflow_id != workflow_id:
                        match = False

                if capability_names and selector.capability_names:
                    if not any(cap in selector.capability_names for cap in capability_names):
                        match = False

                if intent_keywords and selector.intent_contains:
                    if not any(kw in intent_keywords for kw in selector.intent_contains):
                        match = False

                if match:
                    matched.append(playbook)
                    break  # Only add once per playbook

        return matched

    def list_playbooks(
        self,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Playbook]:
        """List playbooks with optional filtering."""
        conditions = []
        params: list[Any] = []

        if status:
            conditions.append("status = ?")
            params.append(status)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT * FROM playbooks 
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [self._row_to_playbook(row) for row in rows]

    def _row_to_playbook(self, row: sqlite3.Row) -> Playbook:
        """Convert a database row to Playbook."""
        from agent_kernel.core.schemas.context import ContextRef

        selectors_data = json.loads(row["selectors_json"]) if row["selectors_json"] else []
        output_refs_data = json.loads(row["output_format_refs_json"]) if row["output_format_refs_json"] else []

        return Playbook(
            playbook_id=row["playbook_id"],
            name=row["name"],
            description=row["description"],
            version=row["version"],
            selectors=[PlaybookSelector(**s) for s in selectors_data],
            required_entity_types=json.loads(row["required_entity_types_json"]) if row["required_entity_types_json"] else [],
            required_sources=json.loads(row["required_sources_json"]) if row["required_sources_json"] else [],
            output_format_refs=[ContextRef(**r) for r in output_refs_data],
            checklist=json.loads(row["checklist_json"]) if row["checklist_json"] else [],
            pitfalls=json.loads(row["pitfalls_json"]) if row["pitfalls_json"] else [],
            recommended_thinking_tier=row["recommended_thinking_tier"],
            derived_from_lessons=json.loads(row["derived_from_lessons_json"]) if row["derived_from_lessons_json"] else [],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # === Stats ===

    def count_evaluations(self, label: OutcomeLabel | None = None) -> int:
        """Count evaluations, optionally by label."""
        with sqlite3.connect(self.db_path) as conn:
            if label:
                result = conn.execute(
                    "SELECT COUNT(*) FROM outcome_evaluations WHERE label = ?",
                    (label.value,),
                ).fetchone()
            else:
                result = conn.execute("SELECT COUNT(*) FROM outcome_evaluations").fetchone()
            return result[0] if result else 0

    def count_cases(self, label: OutcomeLabel | None = None) -> int:
        """Count cases, optionally by label."""
        with sqlite3.connect(self.db_path) as conn:
            if label:
                result = conn.execute(
                    "SELECT COUNT(*) FROM experience_cases WHERE label = ?",
                    (label.value,),
                ).fetchone()
            else:
                result = conn.execute("SELECT COUNT(*) FROM experience_cases").fetchone()
            return result[0] if result else 0

    def count_lessons(self, status: str | None = None) -> int:
        """Count lessons, optionally by status."""
        with sqlite3.connect(self.db_path) as conn:
            if status:
                result = conn.execute(
                    "SELECT COUNT(*) FROM lessons WHERE status = ?",
                    (status,),
                ).fetchone()
            else:
                result = conn.execute("SELECT COUNT(*) FROM lessons").fetchone()
            return result[0] if result else 0

    def count_playbooks(self, status: str | None = None) -> int:
        """Count playbooks, optionally by status."""
        with sqlite3.connect(self.db_path) as conn:
            if status:
                result = conn.execute(
                    "SELECT COUNT(*) FROM playbooks WHERE status = ?",
                    (status,),
                ).fetchone()
            else:
                result = conn.execute("SELECT COUNT(*) FROM playbooks").fetchone()
            return result[0] if result else 0
