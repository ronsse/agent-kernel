"""Obsidian vault integration capabilities.

Provides tools for reading, creating, and searching notes
in an Obsidian vault (local markdown files).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
import yaml

from agent_kernel.core.schemas.base import utc_now

logger = structlog.get_logger(__name__)


@dataclass
class ObsidianNote:
    """Represents an Obsidian note."""

    path: str
    title: str
    content: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    created: datetime | None = None
    modified: datetime | None = None
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "path": self.path,
            "title": self.title,
            "content": self.content,
            "frontmatter": self.frontmatter,
            "created": self.created.isoformat() if self.created else None,
            "modified": self.modified.isoformat() if self.modified else None,
            "tags": self.tags,
            "links": self.links,
        }


class ObsidianVault:
    """Interface to an Obsidian vault (folder of markdown files)."""

    def __init__(self, vault_path: str | Path) -> None:
        """Initialize vault interface.

        Args:
            vault_path: Path to the Obsidian vault root.
        """
        self.vault_path = Path(vault_path)
        if not self.vault_path.exists():
            logger.warning("vault_not_found", path=str(vault_path))

    def _parse_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        """Parse YAML frontmatter from note content.

        Args:
            content: Full note content.

        Returns:
            Tuple of (frontmatter dict, remaining content).
        """
        if not content.startswith("---"):
            return {}, content

        # Find end of frontmatter
        end_match = re.search(r"\n---\n", content[3:])
        if not end_match:
            return {}, content

        frontmatter_text = content[4:end_match.start() + 3]
        body = content[end_match.end() + 3:]

        try:
            frontmatter = yaml.safe_load(frontmatter_text) or {}
        except yaml.YAMLError:
            frontmatter = {}

        return frontmatter, body

    def _extract_tags(self, content: str) -> list[str]:
        """Extract tags from content.

        Args:
            content: Note content.

        Returns:
            List of tag names.
        """
        # Match #tag patterns (not in code blocks)
        pattern = r"(?<!\w)#([a-zA-Z][a-zA-Z0-9_/-]*)"
        return list(set(re.findall(pattern, content)))

    def _extract_links(self, content: str) -> list[str]:
        """Extract wiki-style links from content.

        Args:
            content: Note content.

        Returns:
            List of linked note names.
        """
        # Match [[link]] patterns
        pattern = r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]"
        return list(set(re.findall(pattern, content)))

    def read_note(self, path: str) -> ObsidianNote | None:
        """Read a note from the vault.

        Args:
            path: Relative path within vault.

        Returns:
            ObsidianNote or None if not found.
        """
        full_path = self.vault_path / path
        if not path.endswith(".md"):
            full_path = self.vault_path / f"{path}.md"

        if not full_path.exists():
            return None

        content = full_path.read_text(encoding="utf-8")
        frontmatter, body = self._parse_frontmatter(content)

        # Get file stats
        stat = full_path.stat()
        created = datetime.fromtimestamp(stat.st_ctime)
        modified = datetime.fromtimestamp(stat.st_mtime)

        # Extract title (first H1 or filename)
        title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        title = title_match.group(1) if title_match else full_path.stem

        # Extract tags and links
        tags = self._extract_tags(content)
        if "tags" in frontmatter:
            fm_tags = frontmatter["tags"]
            if isinstance(fm_tags, list):
                tags.extend(fm_tags)
            elif isinstance(fm_tags, str):
                tags.extend(fm_tags.split())

        links = self._extract_links(content)

        return ObsidianNote(
            path=str(full_path.relative_to(self.vault_path)),
            title=title,
            content=body,
            frontmatter=frontmatter,
            created=created,
            modified=modified,
            tags=list(set(tags)),
            links=links,
        )

    def create_note(
        self,
        path: str,
        content: str,
        frontmatter: dict[str, Any] | None = None,
    ) -> ObsidianNote:
        """Create a new note.

        Args:
            path: Relative path for the note.
            content: Note content (markdown).
            frontmatter: Optional YAML frontmatter.

        Returns:
            Created note.
        """
        if not path.endswith(".md"):
            path = f"{path}.md"

        full_path = self.vault_path / path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # Build content with frontmatter
        if frontmatter:
            fm_text = yaml.dump(frontmatter, default_flow_style=False)
            full_content = f"---\n{fm_text}---\n\n{content}"
        else:
            full_content = content

        full_path.write_text(full_content, encoding="utf-8")

        logger.info("obsidian_note_created", path=path)

        return self.read_note(path)

    def update_note(
        self,
        path: str,
        content: str | None = None,
        frontmatter: dict[str, Any] | None = None,
        append: bool = False,
    ) -> ObsidianNote | None:
        """Update an existing note.

        Args:
            path: Note path.
            content: New content (replaces existing unless append=True).
            frontmatter: New frontmatter (merged with existing).
            append: If True, append content instead of replacing.

        Returns:
            Updated note or None if not found.
        """
        note = self.read_note(path)
        if note is None:
            return None

        if not path.endswith(".md"):
            path = f"{path}.md"

        full_path = self.vault_path / path

        # Merge frontmatter
        new_frontmatter = {**note.frontmatter}
        if frontmatter:
            new_frontmatter.update(frontmatter)

        # Handle content
        if content is not None:
            if append:
                new_content = note.content + "\n" + content
            else:
                new_content = content
        else:
            new_content = note.content

        # Write back
        if new_frontmatter:
            fm_text = yaml.dump(new_frontmatter, default_flow_style=False)
            full_content = f"---\n{fm_text}---\n\n{new_content}"
        else:
            full_content = new_content

        full_path.write_text(full_content, encoding="utf-8")

        logger.info("obsidian_note_updated", path=path)

        return self.read_note(path)

    def delete_note(self, path: str) -> bool:
        """Delete a note.

        Args:
            path: Note path.

        Returns:
            True if deleted.
        """
        if not path.endswith(".md"):
            path = f"{path}.md"

        full_path = self.vault_path / path
        if not full_path.exists():
            return False

        full_path.unlink()
        logger.info("obsidian_note_deleted", path=path)
        return True

    def list_notes(
        self,
        folder: str | None = None,
        recursive: bool = True,
    ) -> list[str]:
        """List notes in the vault.

        Args:
            folder: Subfolder to list.
            recursive: Include subfolders.

        Returns:
            List of note paths.
        """
        base_path = self.vault_path
        if folder:
            base_path = base_path / folder

        if not base_path.exists():
            return []

        if recursive:
            paths = base_path.rglob("*.md")
        else:
            paths = base_path.glob("*.md")

        return [
            str(p.relative_to(self.vault_path))
            for p in paths
            if not p.name.startswith(".")
        ]

    def search(
        self,
        query: str,
        folder: str | None = None,
        limit: int = 20,
    ) -> list[tuple[ObsidianNote, float]]:
        """Search notes by content.

        Args:
            query: Search query.
            folder: Limit to folder.
            limit: Max results.

        Returns:
            List of (note, score) tuples.
        """
        results = []
        query_lower = query.lower()
        query_terms = query_lower.split()

        for path in self.list_notes(folder=folder):
            note = self.read_note(path)
            if note is None:
                continue

            # Calculate score based on term frequency
            content_lower = (note.title + " " + note.content).lower()
            matches = sum(1 for term in query_terms if term in content_lower)

            if matches > 0:
                score = matches / len(query_terms) if query_terms else 0
                results.append((note, score))

        # Sort by score descending
        results.sort(key=lambda x: x[1], reverse=True)

        return results[:limit]

    def get_daily_note(self, date: datetime | None = None) -> ObsidianNote | None:
        """Get daily note for a date.

        Args:
            date: Date (default: today).

        Returns:
            Daily note or None.
        """
        if date is None:
            date = datetime.now()

        # Common daily note formats
        formats = [
            f"Daily/{date:%Y-%m-%d}",
            f"{date:%Y-%m-%d}",
            f"daily/{date:%Y-%m-%d}",
            f"Daily Notes/{date:%Y-%m-%d}",
        ]

        for fmt in formats:
            note = self.read_note(fmt)
            if note:
                return note

        return None

    def create_daily_note(
        self,
        date: datetime | None = None,
        template: str | None = None,
    ) -> ObsidianNote:
        """Create a daily note.

        Args:
            date: Date (default: today).
            template: Optional template content.

        Returns:
            Created daily note.
        """
        if date is None:
            date = datetime.now()

        path = f"Daily/{date:%Y-%m-%d}"

        if template is None:
            template = f"# {date:%A, %B %d, %Y}\n\n## Tasks\n\n## Notes\n\n"

        frontmatter = {
            "date": date.strftime("%Y-%m-%d"),
            "type": "daily",
        }

        return self.create_note(path, template, frontmatter)


# =============================================================================
# Global vault instance
# =============================================================================

_vault: ObsidianVault | None = None


def get_vault(vault_path: str | Path | None = None) -> ObsidianVault:
    """Get the Obsidian vault instance.

    Uses OBSIDIAN_VAULT_PATH from config if no path provided.
    """
    global _vault
    if _vault is None:
        if vault_path is None:
            from agent_kernel.core.config import get_settings

            settings = get_settings()
            if settings.obsidian_vault_path:
                vault_path = Path(settings.obsidian_vault_path)
            else:
                vault_path = Path.home() / "Notes"  # Default path
        _vault = ObsidianVault(vault_path)
    return _vault


def reset_vault() -> None:
    """Reset the vault instance (for testing or reconfiguration)."""
    global _vault
    _vault = None


def set_vault(vault: ObsidianVault) -> None:
    """Set the vault instance (for testing)."""
    global _vault
    _vault = vault


# =============================================================================
# Capability Functions
# =============================================================================


def obsidian_read(path: str) -> dict[str, Any]:
    """Read a note from the Obsidian vault.

    Args:
        path: Note path relative to vault root.

    Returns:
        Dict with note content or error.
    """
    vault = get_vault()
    note = vault.read_note(path)

    if note is None:
        return {"error": "Note not found", "path": path}

    return {"note": note.to_dict()}


def obsidian_create(
    path: str,
    content: str,
    title: str | None = None,
    tags: list[str] | None = None,
    frontmatter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new note in the Obsidian vault.

    Args:
        path: Note path relative to vault root.
        content: Note content (markdown).
        title: Optional title (added as H1).
        tags: Optional tags for frontmatter.
        frontmatter: Optional frontmatter fields to merge.

    Returns:
        Dict with created note info.
    """
    vault = get_vault()

    if title:
        content = f"# {title}\n\n{content}"

    frontmatter_data = dict(frontmatter or {})
    if tags:
        existing_tags = frontmatter_data.get("tags", [])
        if isinstance(existing_tags, str):
            existing_tags = existing_tags.split()
        if not isinstance(existing_tags, list):
            existing_tags = []
        frontmatter_data["tags"] = sorted(set(existing_tags + tags))
    frontmatter_data.setdefault("created", utc_now().isoformat())

    note = vault.create_note(path, content, frontmatter_data)

    return {
        "path": note.path,
        "title": note.title,
        "created": note.created.isoformat() if note.created else None,
    }


def obsidian_search(
    query: str,
    folder: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search notes in the Obsidian vault.

    Args:
        query: Search query.
        folder: Limit search to folder.
        limit: Maximum results.

    Returns:
        Dict with search results.
    """
    vault = get_vault()
    results = vault.search(query, folder=folder, limit=limit)

    return {
        "results": [
            {
                "path": note.path,
                "title": note.title,
                "score": round(score, 2),
                "excerpt": note.content[:200] + "..." if len(note.content) > 200 else note.content,
            }
            for note, score in results
        ],
        "total_count": len(results),
    }


def obsidian_list(
    folder: str | None = None,
    recursive: bool = True,
) -> dict[str, Any]:
    """List notes in the Obsidian vault.

    Args:
        folder: Folder to list.
        recursive: Include subfolders.

    Returns:
        Dict with note paths.
    """
    vault = get_vault()
    paths = vault.list_notes(folder=folder, recursive=recursive)

    return {
        "notes": paths,
        "total_count": len(paths),
    }


def obsidian_daily(date: str | None = None) -> dict[str, Any]:
    """Get or create today's daily note.

    Args:
        date: Date in YYYY-MM-DD format (default: today).

    Returns:
        Dict with daily note info.
    """
    vault = get_vault()

    if date:
        dt = datetime.strptime(date, "%Y-%m-%d")
    else:
        dt = datetime.now()

    note = vault.get_daily_note(dt)

    if note is None:
        note = vault.create_daily_note(dt)

    return {"note": note.to_dict()}


def obsidian_update(
    path: str,
    content: str | None = None,
    append: bool = False,
    tags: list[str] | None = None,
    frontmatter: dict[str, Any] | None = None,
    skip_if_no_change: bool = False,
) -> dict[str, Any]:
    """Update an existing note.

    Args:
        path: Note path.
        content: New content or content to append.
        append: If True, append content.
        tags: Tags to add/update in frontmatter.
        frontmatter: Optional frontmatter fields to merge.

    Returns:
        Dict with updated note info or error.
    """
    vault = get_vault()
    note = vault.read_note(path)
    if note is None:
        return {"error": "Note not found", "path": path}

    frontmatter_data = dict(frontmatter or {})
    if tags:
        existing_tags = frontmatter_data.get("tags", [])
        if isinstance(existing_tags, str):
            existing_tags = existing_tags.split()
        if not isinstance(existing_tags, list):
            existing_tags = []
        frontmatter_data["tags"] = sorted(set(existing_tags + tags))

    new_frontmatter = {**note.frontmatter}
    if frontmatter_data:
        new_frontmatter.update(frontmatter_data)

    if content is not None:
        new_content = note.content + "\n" + content if append else content
    else:
        new_content = note.content

    if skip_if_no_change:
        existing_frontmatter = dict(note.frontmatter)
        existing_frontmatter.pop("modified", None)
        candidate_frontmatter = dict(new_frontmatter)
        candidate_frontmatter.pop("modified", None)
        if new_content == note.content and candidate_frontmatter == existing_frontmatter:
            return {"note": note.to_dict(), "skipped": True}

    frontmatter_data["modified"] = utc_now().isoformat()
    updated = vault.update_note(
        path=path,
        content=content,
        frontmatter=frontmatter_data,
        append=append,
    )

    if updated is None:
        return {"error": "Note not found", "path": path}

    return {"note": updated.to_dict(), "skipped": False}
