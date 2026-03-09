"""Built-in notes tool implementations.

Provides note management functionality backed by SQLite.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now

logger = structlog.get_logger(__name__)


@dataclass
class NoteRecord:
    """A note record."""

    note_id: str
    title: str
    content: str
    project_id: str | None
    folder: str | None
    tags: list[str]
    source: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "note_id": self.note_id,
            "title": self.title,
            "content": self.content,
            "project_id": self.project_id,
            "folder": self.folder,
            "tags": self.tags,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class NoteStore:
    """SQLite-backed note store."""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        """Initialize note store.

        Args:
            db_path: Path to SQLite database, or :memory: for in-memory.
        """
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_schema(self) -> None:
        """Initialize database schema."""
        conn = self._get_conn()

        # Main notes table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                note_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT DEFAULT '',
                project_id TEXT,
                folder TEXT,
                tags_json TEXT DEFAULT '[]',
                source TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Indexes
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_notes_project ON notes(project_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder)
        """)

        conn.commit()

    def create(
        self,
        title: str,
        content: str = "",
        project_id: str | None = None,
        folder: str | None = None,
        tags: list[str] | None = None,
        source: str | None = None,
    ) -> NoteRecord:
        """Create a new note."""
        note_id = generate_ulid()
        now = utc_now().isoformat()

        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO notes
            (note_id, title, content, project_id, folder, tags_json, source,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note_id,
                title,
                content,
                project_id,
                folder,
                json.dumps(tags or []),
                source,
                now,
                now,
            ),
        )
        conn.commit()

        logger.info("note_created", note_id=note_id, title=title)

        return NoteRecord(
            note_id=note_id,
            title=title,
            content=content,
            project_id=project_id,
            folder=folder,
            tags=tags or [],
            source=source,
            created_at=now,
            updated_at=now,
        )

    def get(self, note_id: str) -> NoteRecord | None:
        """Get a note by ID."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM notes WHERE note_id = ?",
            (note_id,),
        ).fetchone()

        if row is None:
            return None

        return self._row_to_record(row)

    def update(
        self,
        note_id: str,
        title: str | None = None,
        content: str | None = None,
        project_id: str | None = None,
        folder: str | None = None,
        tags: list[str] | None = None,
    ) -> NoteRecord | None:
        """Update a note."""
        note = self.get(note_id)
        if note is None:
            return None

        updates = []
        values = []

        if title is not None:
            updates.append("title = ?")
            values.append(title)
        if content is not None:
            updates.append("content = ?")
            values.append(content)
        if project_id is not None:
            updates.append("project_id = ?")
            values.append(project_id)
        if folder is not None:
            updates.append("folder = ?")
            values.append(folder)
        if tags is not None:
            updates.append("tags_json = ?")
            values.append(json.dumps(tags))

        if not updates:
            return note

        now = utc_now().isoformat()
        updates.append("updated_at = ?")
        values.append(now)
        values.append(note_id)

        conn = self._get_conn()
        conn.execute(
            f"UPDATE notes SET {', '.join(updates)} WHERE note_id = ?",
            values,
        )
        conn.commit()

        logger.info("note_updated", note_id=note_id)

        return self.get(note_id)

    def delete(self, note_id: str) -> bool:
        """Delete a note."""
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM notes WHERE note_id = ?",
            (note_id,),
        )
        conn.commit()

        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("note_deleted", note_id=note_id)

        return deleted

    def list(
        self,
        project_id: str | None = None,
        folder: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[NoteRecord], int]:
        """List notes with filters.

        Returns:
            Tuple of (notes, total_count).
        """
        conn = self._get_conn()

        # Build query
        where_clauses = []
        params: list[Any] = []

        if project_id:
            where_clauses.append("project_id = ?")
            params.append(project_id)

        if folder:
            where_clauses.append("folder = ?")
            params.append(folder)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Get total count
        count_row = conn.execute(
            f"SELECT COUNT(*) as count FROM notes WHERE {where_sql}",
            params,
        ).fetchone()
        total_count = count_row["count"] if count_row else 0

        # Get notes
        rows = conn.execute(
            f"""
            SELECT * FROM notes
            WHERE {where_sql}
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()

        notes = [self._row_to_record(row) for row in rows]

        return notes, total_count

    def search(
        self,
        query: str,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[tuple[NoteRecord, float]]:
        """Search notes using keyword matching.

        Returns:
            List of (note, score) tuples.
        """
        conn = self._get_conn()

        # Get all matching notes
        sql = """
            SELECT * FROM notes
            WHERE (title LIKE ? OR content LIKE ? OR tags_json LIKE ?)
        """
        pattern = f"%{query}%"
        params: list[Any] = [pattern, pattern, pattern]

        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)

        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()

        # Calculate simple relevance scores
        query_terms = query.lower().split()
        results = []

        for row in rows:
            note = self._row_to_record(row)
            # Calculate score based on term frequency
            text = (note.title + " " + note.content).lower()
            matches = sum(1 for term in query_terms if term in text)
            score = matches / len(query_terms) if query_terms else 0.0
            results.append((note, score))

        # Sort by score descending
        results.sort(key=lambda x: x[1], reverse=True)

        return results

    def get_folders(self) -> list[str]:
        """Get all unique folders."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT folder FROM notes WHERE folder IS NOT NULL"
        ).fetchall()
        return [row["folder"] for row in rows]

    def clear(self) -> None:
        """Clear all notes (for testing)."""
        conn = self._get_conn()
        conn.execute("DELETE FROM notes")
        conn.commit()

    def _row_to_record(self, row: sqlite3.Row) -> NoteRecord:
        """Convert a database row to NoteRecord."""
        return NoteRecord(
            note_id=row["note_id"],
            title=row["title"],
            content=row["content"] or "",
            project_id=row["project_id"],
            folder=row["folder"],
            tags=json.loads(row["tags_json"]) if row["tags_json"] else [],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# Global note store instance
_note_store: NoteStore | None = None


def get_note_store(db_path: Path | str | None = None) -> NoteStore:
    """Get the note store instance."""
    global _note_store
    if _note_store is None:
        _note_store = NoteStore(db_path or ":memory:")
    return _note_store


def set_note_store(store: NoteStore) -> None:
    """Set the note store instance (for testing)."""
    global _note_store
    _note_store = store


# =============================================================================
# Capability Functions (exposed via Tool Broker)
# =============================================================================


def search_notes(
    query: str,
    limit: int = 10,
    min_score: float = 0.0,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Search notes using full-text search.

    Args:
        query: Search query.
        limit: Maximum results.
        min_score: Minimum relevance score (not used with FTS).
        project_id: Filter by project ID.

    Returns:
        Dict with results list and total count.
    """
    store = get_note_store()
    results = store.search(query, limit=limit, project_id=project_id)

    formatted_results = []
    for note, score in results:
        if score >= min_score:
            # Create excerpt
            excerpt = _create_excerpt(note.content, query.split())
            formatted_results.append({
                "note_id": note.note_id,
                "title": note.title,
                "excerpt": excerpt,
                "score": round(score, 2),
                "tags": note.tags,
                "folder": note.folder,
                "created_at": note.created_at,
            })

    return {
        "results": formatted_results,
        "total_count": len(formatted_results),
    }


def create_note(
    title: str,
    content: str = "",
    project_id: str | None = None,
    folder: str | None = None,
    tags: list[str] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Create a new note.

    Args:
        title: Note title.
        content: Note content.
        project_id: Associated project.
        folder: Folder path.
        tags: Tags for categorization.
        source: Source reference (e.g., file path).

    Returns:
        Dict with created note details.
    """
    store = get_note_store()
    note = store.create(
        title=title,
        content=content,
        project_id=project_id,
        folder=folder,
        tags=tags,
        source=source,
    )

    return {
        "note_id": note.note_id,
        "title": note.title,
        "created_at": note.created_at,
    }


def get_note(note_id: str) -> dict[str, Any]:
    """Get a note by ID.

    Args:
        note_id: The note ID.

    Returns:
        Dict with note details or error.
    """
    store = get_note_store()
    note = store.get(note_id)

    if note is None:
        return {"error": "Note not found", "note_id": note_id}

    return {"note": note.to_dict()}


def update_note(
    note_id: str,
    title: str | None = None,
    content: str | None = None,
    project_id: str | None = None,
    folder: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Update a note.

    Args:
        note_id: The note ID.
        title: New title.
        content: New content.
        project_id: New project ID.
        folder: New folder.
        tags: New tags.

    Returns:
        Dict with updated note details or error.
    """
    store = get_note_store()
    note = store.update(
        note_id=note_id,
        title=title,
        content=content,
        project_id=project_id,
        folder=folder,
        tags=tags,
    )

    if note is None:
        return {"error": "Note not found", "note_id": note_id}

    return {"note": note.to_dict()}


def delete_note(note_id: str) -> dict[str, Any]:
    """Delete a note.

    Args:
        note_id: The note ID.

    Returns:
        Dict with deletion status.
    """
    store = get_note_store()
    deleted = store.delete(note_id)

    return {"deleted": deleted, "note_id": note_id}


def list_notes(
    project_id: str | None = None,
    folder: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List notes with optional filters.

    Args:
        project_id: Filter by project.
        folder: Filter by folder.
        limit: Maximum results.
        offset: Pagination offset.

    Returns:
        Dict with notes and total count.
    """
    store = get_note_store()
    notes, total_count = store.list(
        project_id=project_id,
        folder=folder,
        limit=limit,
        offset=offset,
    )

    return {
        "notes": [n.to_dict() for n in notes],
        "total_count": total_count,
    }


def list_folders() -> dict[str, Any]:
    """List all note folders.

    Returns:
        Dict with folders list.
    """
    store = get_note_store()
    folders = store.get_folders()

    return {"folders": folders}


def _create_excerpt(
    content: str,
    query_terms: list[str],
    max_length: int = 200,
) -> str:
    """Create a relevant excerpt from content."""
    if not content:
        return ""

    # Find first occurrence of a query term
    content_lower = content.lower()
    first_pos = len(content)

    for term in query_terms:
        pos = content_lower.find(term.lower())
        if pos != -1 and pos < first_pos:
            first_pos = pos

    if first_pos == len(content):
        first_pos = 0

    # Extract context around the match
    start = max(0, first_pos - 50)
    end = min(len(content), first_pos + max_length - 50)

    excerpt = content[start:end]
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(content):
        excerpt = excerpt + "..."

    return excerpt


def add_note(
    title: str,
    content: str,
    project_id: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Add a note to the store (for testing/seeding).

    Args:
        title: Note title.
        content: Note content.
        project_id: Optional project ID.
        tags: Optional tags.

    Returns:
        Created note details.
    """
    return create_note(
        title=title,
        content=content,
        project_id=project_id,
        tags=tags,
    )


def clear_notes() -> None:
    """Clear all notes (for testing)."""
    store = get_note_store()
    store.clear()
