"""Vault Indexer service for processing Obsidian notes.

Implements the indexing pipeline (v1.0.1):
1. Parse notes and extract metadata
2. Generate stable IDs if missing (note_01J... format)
3. Track index state across stores (eventual consistency)
4. Update graph store with v1.0.1 ontology (NodeType/EdgeType)
5. Generate embeddings for vector store
6. Optionally enrich with LLM (auto.* fields)
"""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import structlog
import yaml


def _serialize_for_json(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable objects to strings."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize_for_json(item) for item in obj]
    return obj

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.enrichment_config import (
    DEFAULT_ENRICHMENT_THRESHOLDS,
    DEFAULT_OBSIDIAN_CONFIG,
    EnrichmentThresholds,
    SourceEnrichmentConfig,
)
from agent_kernel.core.schemas.entity import EntityRef, KnownEntityTypes, KnownSources
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.memory.document_store import DocumentStore
from agent_kernel.memory.graph_store import GraphStore
from agent_kernel.memory.vector_store import VectorStore
from agent_kernel.services.embedding import EmbeddingService
from agent_kernel.services.index_state import EntityIndexState, IndexStatus
from agent_kernel.services.task_parser import get_task_parser
from agent_kernel.tools.builtin.obsidian import ObsidianNote, ObsidianVault

if TYPE_CHECKING:
    from agent_kernel.services.enrichment import EnrichmentResult, EnrichmentService
    from agent_kernel.services.enrichment_registry import EnrichmentConfigRegistry
    from agent_kernel.services.index_state import IndexStateStore

# Backwards compatibility alias
SummarizationConfig = EnrichmentThresholds
DEFAULT_SUMMARIZATION_CONFIG = DEFAULT_ENRICHMENT_THRESHOLDS

logger = structlog.get_logger(__name__)


@dataclass
class IndexResult:
    """Result of indexing a single note."""

    note_id: str
    path: str
    action: str  # "created", "updated", "unchanged", "error"
    graph_updated: bool = False
    vector_updated: bool = False
    stable_id_added: bool = False
    tasks_extracted: int = 0  # v1.0.1: Number of tasks found
    enriched: bool = False  # v1.0.1: Whether LLM enrichment was applied
    auto_tags: list[str] | None = None  # v1.0.1: Auto-generated tags
    auto_class: str | None = None  # v1.0.1: Auto-classification
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "note_id": self.note_id,
            "path": self.path,
            "action": self.action,
            "graph_updated": self.graph_updated,
            "vector_updated": self.vector_updated,
            "stable_id_added": self.stable_id_added,
            "tasks_extracted": self.tasks_extracted,
            "enriched": self.enriched,
            "auto_tags": self.auto_tags,
            "auto_class": self.auto_class,
            "error": self.error,
        }


@dataclass
class IndexSummary:
    """Summary of a full indexing run."""

    started_at: datetime
    completed_at: datetime | None = None
    total_notes: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0
    results: list[IndexResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "total_notes": self.total_notes,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "errors": self.errors,
            "results": [r.to_dict() for r in self.results],
        }


async def _await_if_needed(value: Any) -> Any:
    """Await value if it's awaitable; otherwise return as-is."""
    if inspect.isawaitable(value):
        return await value
    return value


class VaultIndexer:
    """Indexes Obsidian vault notes into the kernel's memory stores.

    Maintains derived indexes that can always be rebuilt from the vault:
    - Document Store: Full note content with metadata
    - Graph Store: Nodes and edges for relationships
    - Vector Store: Embeddings for semantic search
    """

    def __init__(
        self,
        vault: ObsidianVault,
        document_store: DocumentStore | None = None,
        graph_store: GraphStore | None = None,
        vector_store: VectorStore | None = None,
        embedding_service: EmbeddingService | None = None,
        index_state_store: IndexStateStore | None = None,
        enrichment_service: EnrichmentService | None = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        enable_enrichment: bool = False,
        summarization_config: SummarizationConfig | None = None,
        source_enrichment_config: SourceEnrichmentConfig | None = None,
        enrichment_registry: EnrichmentConfigRegistry | None = None,
    ) -> None:
        """Initialize the vault indexer.

        Args:
            vault: The Obsidian vault to index.
            document_store: Store for full document content.
            graph_store: Store for relationship graph.
            vector_store: Store for vector embeddings.
            embedding_service: Service for generating embeddings.
            index_state_store: Store for tracking indexing state (v1.0.1).
            enrichment_service: Service for LLM enrichment (v1.0.1).
            chunk_size: Target size for text chunks.
            chunk_overlap: Overlap between chunks.
            enable_enrichment: Whether to enable LLM enrichment (v1.0.1).
            summarization_config: Legacy thresholds config (deprecated, use source_enrichment_config).
            source_enrichment_config: Source-specific enrichment config (v1.0.5).
            enrichment_registry: Registry for loading source configs from YAML (v1.0.5).
        """
        self.vault = vault
        self.document_store = document_store
        self.graph_store = graph_store
        self.vector_store = vector_store
        self.embedding_service = embedding_service
        self.index_state_store = index_state_store
        self.enrichment_service = enrichment_service
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.enable_enrichment = enable_enrichment
        self.enrichment_registry = enrichment_registry

        # Source-specific enrichment config (v1.0.5)
        # Priority: explicit config > registry lookup > legacy config > default
        if source_enrichment_config is not None:
            self.source_config = source_enrichment_config
        elif enrichment_registry is not None:
            self.source_config = enrichment_registry.get_or_default(KnownSources.OBSIDIAN)
        elif summarization_config is not None:
            # Legacy: wrap in a source config
            self.source_config = DEFAULT_OBSIDIAN_CONFIG.model_copy(
                update={"thresholds": summarization_config}
            )
        else:
            self.source_config = DEFAULT_OBSIDIAN_CONFIG

        # Backwards compatibility alias
        self.summarization_config = self.source_config.thresholds

        # Track content hashes for change detection
        self._content_hashes: dict[str, str] = {}

    def _compute_content_hash(self, content: str) -> str:
        """Compute SHA-256 hash of content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def _get_stable_id(self, note: ObsidianNote) -> tuple[str, bool]:
        """Get or generate stable ID for a note.

        Returns:
            Tuple of (note_id, was_generated).
        """
        # Check frontmatter for existing ID
        existing_id = note.frontmatter.get("id")
        if existing_id:
            return existing_id, False

        # Generate new ID
        new_id = f"note_{generate_ulid()}"
        return new_id, True

    def _inject_stable_id(self, path: str, note_id: str) -> bool:
        """Inject stable ID into note frontmatter (v1.0.1 writeback safety).

        Implements safe YAML patching:
        - Preserves existing frontmatter structure
        - Uses safe YAML dumping (no python objects)
        - Checks file hasn't changed since reading
        - Adds 'id' as first field in frontmatter

        Args:
            path: Note path in vault.
            note_id: The stable ID to inject.

        Returns:
            True if successfully updated.
        """
        try:
            full_path = self.vault.vault_path / path

            if not full_path.exists():
                return False

            # Get file mtime before reading
            original_mtime = full_path.stat().st_mtime

            content = full_path.read_text(encoding="utf-8")

            # Parse existing frontmatter
            if content.startswith("---"):
                # Find end of frontmatter
                end_idx = content.find("\n---\n", 3)
                if end_idx > 0:
                    frontmatter_text = content[4:end_idx]
                    body = content[end_idx + 5 :]

                    try:
                        frontmatter = yaml.safe_load(frontmatter_text) or {}
                    except yaml.YAMLError:
                        frontmatter = {}

                    # Add ID at the beginning by creating new ordered dict
                    new_frontmatter = {"id": note_id}
                    new_frontmatter.update(frontmatter)

                    # Rebuild content with YAML safe dump
                    # Use block style for readability, don't sort keys
                    new_fm = yaml.dump(
                        new_frontmatter,
                        default_flow_style=False,
                        sort_keys=False,
                        allow_unicode=True,
                    )
                    new_content = f"---\n{new_fm}---\n{body}"
                else:
                    # Malformed frontmatter, add new
                    new_content = f"---\nid: {note_id}\n---\n\n{content}"
            else:
                # No frontmatter, add it
                new_content = f"---\nid: {note_id}\n---\n\n{content}"

            # Check file hasn't been modified since we read it (writeback safety)
            current_mtime = full_path.stat().st_mtime
            if current_mtime != original_mtime:
                logger.warning(
                    "stable_id_injection_aborted_file_changed",
                    path=path,
                    note_id=note_id,
                )
                return False

            full_path.write_text(new_content, encoding="utf-8")
            logger.info("stable_id_injected", path=path, note_id=note_id)
            return True  # noqa: TRY300

        except Exception:
            logger.exception("stable_id_injection_failed", path=path)
            return False

    def _inject_auto_fields(
        self,
        path: str,
        enrichment: EnrichmentResult,
    ) -> bool:
        """Inject auto.* fields into note frontmatter (v1.0.1 writeback safety).

        Implements safe YAML patching for machine-generated metadata:
        - Writes to `auto:` namespace only (never touches human fields)
        - Preserves existing frontmatter structure
        - Uses safe YAML dumping (no python objects)
        - Checks file hasn't changed since reading

        Args:
            path: Note path in vault.
            enrichment: EnrichmentResult with auto fields.

        Returns:
            True if successfully updated.
        """
        try:
            auto_fields = enrichment.to_frontmatter()
            if not auto_fields:
                return False  # Nothing to write

            full_path = self.vault.vault_path / path

            if not full_path.exists():
                return False

            # Get file mtime before reading
            original_mtime = full_path.stat().st_mtime

            content = full_path.read_text(encoding="utf-8")

            # Parse existing frontmatter
            if content.startswith("---"):
                # Find end of frontmatter
                end_idx = content.find("\n---\n", 3)
                if end_idx > 0:
                    frontmatter_text = content[4:end_idx]
                    body = content[end_idx + 5 :]

                    try:
                        frontmatter = yaml.safe_load(frontmatter_text) or {}
                    except yaml.YAMLError:
                        frontmatter = {}

                    # Update auto: namespace only
                    frontmatter["auto"] = auto_fields

                    # Rebuild content with YAML safe dump
                    new_fm = yaml.dump(
                        frontmatter,
                        default_flow_style=False,
                        sort_keys=False,
                        allow_unicode=True,
                    )
                    new_content = f"---\n{new_fm}---\n{body}"
                else:
                    # Malformed frontmatter, add auto section
                    auto_section = yaml.dump(
                        {"auto": auto_fields},
                        default_flow_style=False,
                        allow_unicode=True,
                    )
                    new_content = f"---\n{auto_section}---\n\n{content}"
            else:
                # No frontmatter, add it
                auto_section = yaml.dump(
                    {"auto": auto_fields},
                    default_flow_style=False,
                    allow_unicode=True,
                )
                new_content = f"---\n{auto_section}---\n\n{content}"

            # Check file hasn't been modified since we read it (writeback safety)
            current_mtime = full_path.stat().st_mtime
            if current_mtime != original_mtime:
                logger.warning(
                    "auto_fields_injection_aborted_file_changed",
                    path=path,
                )
                return False

            full_path.write_text(new_content, encoding="utf-8")
            logger.info(
                "auto_fields_injected",
                path=path,
                auto_tags=auto_fields.get("tags"),
                auto_class=auto_fields.get("class"),
            )
            return True  # noqa: TRY300

        except Exception:
            logger.exception("auto_fields_injection_failed", path=path)
            return False

    def _chunk_content(self, content: str) -> list[dict[str, Any]]:
        """Split content into chunks for embedding.

        Args:
            content: Full note content.

        Returns:
            List of chunks with metadata.
        """
        chunks = []

        # Split by paragraphs first
        paragraphs = content.split("\n\n")

        current_chunk = ""
        current_start = 0

        for para in paragraphs:
            if len(current_chunk) + len(para) + 2 <= self.chunk_size:
                if current_chunk:
                    current_chunk += "\n\n"
                current_chunk += para
            else:
                # Save current chunk if not empty
                if current_chunk.strip():
                    chunks.append(
                        {
                            "text": current_chunk.strip(),
                            "start_offset": current_start,
                            "end_offset": current_start + len(current_chunk),
                        }
                    )

                # Start new chunk with overlap
                if self.chunk_overlap > 0 and current_chunk:
                    overlap_text = current_chunk[-self.chunk_overlap :]
                    current_chunk = overlap_text + "\n\n" + para
                else:
                    current_chunk = para

                current_start += len(current_chunk) - len(para) - 2

        # Don't forget the last chunk
        if current_chunk.strip():
            chunks.append(
                {
                    "text": current_chunk.strip(),
                    "start_offset": current_start,
                    "end_offset": current_start + len(current_chunk),
                }
            )

        return chunks

    def _update_index_state_start(
        self, note_id: str, path: str, content_hash: str
    ) -> None:
        """Initialize or update index state when starting indexing."""
        if not self.index_state_store:
            return

        existing_state = self.index_state_store.get(note_id)
        if existing_state:
            # Mark as stale if content changed
            if existing_state.content_hash != content_hash:
                self.index_state_store.mark_stale(note_id, content_hash)
        else:
            # Create new index state
            new_state = EntityIndexState(
                entity_id=note_id,
                entity_type="note",
                source_path=path,
                content_hash=content_hash,
                doc_status=IndexStatus.INDEXING,
                graph_status=IndexStatus.INDEXING,
                vector_status=IndexStatus.PENDING,
            )
            self.index_state_store.save(new_state)

    def _update_doc_index_state(self, note_id: str) -> None:
        """Update document index state to INDEXED."""
        if self.index_state_store:
            self.index_state_store.update_doc_status(note_id, IndexStatus.INDEXED)

    def _update_graph_index_state(self, note_id: str) -> None:
        """Update graph index state to INDEXED."""
        if self.index_state_store:
            self.index_state_store.update_graph_status(note_id, IndexStatus.INDEXED)

    def _update_vector_index_state(self, note_id: str) -> None:
        """Update vector index state to INDEXED."""
        if self.index_state_store:
            self.index_state_store.update_vector_status(note_id, IndexStatus.INDEXED)

    def _note_export_todo(self, frontmatter: dict[str, Any]) -> bool:
        export = frontmatter.get("export")
        if isinstance(export, dict):
            value = export.get("todo")
            return bool(value)
        value = frontmatter.get("export.todo")
        return bool(value)

    def _meeting_metadata(
        self,
        note: ObsidianNote,
    ) -> tuple[bool, str | None]:
        frontmatter = note.frontmatter
        note_type = str(frontmatter.get("type", "")).lower()
        auto = frontmatter.get("auto", {})
        auto_class = ""
        if isinstance(auto, dict):
            auto_class = str(auto.get("class", "")).lower()
        tags = [tag.lower() for tag in note.tags]
        is_meeting = (
            note_type == "meeting"
            or auto_class == "meeting"
            or "meeting" in tags
        )
        meeting_date = frontmatter.get("meeting_date") or frontmatter.get("date")
        if isinstance(meeting_date, (datetime, date)):
            meeting_date = meeting_date.isoformat()
        elif meeting_date is not None:
            meeting_date = str(meeting_date)
        return is_meeting, meeting_date if is_meeting else None

    async def _extract_and_index_tasks(
        self,
        note: ObsidianNote,
        note_id: str,
        node_id: str,
        content: str,
        path: str,
    ) -> int:
        """Extract tasks from note content and create Task nodes in graph.

        Args:
            note_id: The note's stable ID.
            node_id: The note's graph node ID (e.g., "note:note_01J...").
            content: Full note content.
            path: Note path for logging.

        Returns:
            Number of tasks extracted.
        """
        if not self.graph_store:
            return 0

        parser = get_task_parser()
        result = parser.parse_note(content, note_id=note_id, note_path=path)
        tasks = result.tasks
        note_export_todo = self._note_export_todo(note.frontmatter)
        is_meeting, meeting_date = self._meeting_metadata(note)
        note_summary = None
        auto_fields = note.frontmatter.get("auto", {})
        if isinstance(auto_fields, dict):
            note_summary = auto_fields.get("summary")
        note_project = note.frontmatter.get("project") or note.frontmatter.get(
            "project_ref"
        )
        if isinstance(note_project, list):
            note_project = note_project[0] if note_project else None
        if note_project is not None:
            note_project = str(note_project)

        # Track current task targets for stale edge deletion
        current_task_targets: list[str] = []

        for task in tasks:
            task_entity = parser.to_task_entity(task)
            task_project = task_entity.project_ref or note_project
            task_id = task_entity.id
            task_node_id = f"task:{task_id}"
            current_task_targets.append(task_node_id)
            task_tags = task_entity.labels
            task_contexts = [task.context] if task.context else []
            due_value = task_entity.due
            due_iso = due_value.isoformat() if due_value else None

            promotion_state = (
                "proposed_external" if (task.should_sync or note_export_todo) else "local_only"
            )

            # Create/update Task node
            await _await_if_needed(
                self.graph_store.upsert_node(
                    node_id=task_node_id,
                    node_type=NodeType.TASK.value,
                    properties={
                        "task_id": task_id,
                        "text": task_entity.title,
                        "status": task_entity.status.value,
                        "priority": task_entity.priority.value,
                        "due_date": due_iso,
                        "project": task_project,
                        "line_number": task.line_number,
                        "tags": task_tags,
                        "contexts": task_contexts,
                        "is_complete": task.is_completed,
                        "source_note_id": note_id,
                        "source_path": path,
                        "extracted_by": "vault_indexer",
                        "should_sync": task.should_sync,
                        "sync_marker": task.sync_marker,
                        "block_id": task.block_id,
                        "promotion_state": promotion_state,
                        "note_export_todo": note_export_todo,
                        "meeting_group_id": note_id if is_meeting else None,
                        "meeting_title": note.title if is_meeting else None,
                        "meeting_date": meeting_date,
                        "note_tags": note.tags,
                        "note_summary": note_summary,
                    },
                )
            )

            # Create edge from Note to Task
            await _await_if_needed(
                self.graph_store.upsert_edge(
                    source_id=node_id,
                    target_id=task_node_id,
                    edge_type=EdgeType.NOTE_HAS_TASK.value,
                    properties={
                        "line_number": task.line_number,
                        "extracted_by": "vault_indexer",
                    },
                )
            )

            # Track task tag targets for stale edge deletion
            current_task_tag_targets: list[str] = []

            # Create edges for task tags
            for tag in task_tags:
                tag_node_id_for_task = f"tag:{tag}"
                current_task_tag_targets.append(tag_node_id_for_task)
                await _await_if_needed(
                    self.graph_store.upsert_node(
                        node_id=tag_node_id_for_task,
                        node_type=NodeType.TAG.value,
                        properties={
                            "name": tag,
                            "extracted_by": "vault_indexer",
                        },
                    )
                )
                await _await_if_needed(
                    self.graph_store.upsert_edge(
                        source_id=task_node_id,
                        target_id=tag_node_id_for_task,
                        edge_type=EdgeType.NOTE_TAGGED_WITH_TAG.value,
                        properties={
                            "confidence": 1.0,
                            "extracted_by": "vault_indexer",
                        },
                    )
                )

            # Delete stale task tag edges (tags removed from task)
            await _await_if_needed(
                self.graph_store.delete_edges_from_source(
                    source_id=task_node_id,
                    edge_type=EdgeType.NOTE_TAGGED_WITH_TAG.value,
                    exclude_targets=current_task_tag_targets,
                )
            )

        # Delete stale task edges (tasks removed from note)
        # Note: Only delete if we have tasks; if no tasks, delete all task edges
        deleted_tasks = await _await_if_needed(
            self.graph_store.delete_edges_from_source(
                source_id=node_id,
                edge_type=EdgeType.NOTE_HAS_TASK.value,
                exclude_targets=current_task_targets if tasks else [],
            )
        )
        deleted_task_count = (
            int(deleted_tasks) if isinstance(deleted_tasks, int) else 0
        )
        if deleted_task_count > 0:
            logger.info(
                "stale_task_edges_deleted",
                note_id=note_id,
                count=deleted_task_count,
            )

        logger.debug(
            "tasks_extracted",
            note_id=note_id,
            path=path,
            count=len(tasks),
        )

        return len(tasks)

    async def index_note(  # noqa: PLR0912, PLR0915
        self,
        path: str,
        force: bool = False,
        inject_id: bool = True,
    ) -> IndexResult:
        """Index a single note.

        Args:
            path: Note path relative to vault root.
            force: Force re-indexing even if unchanged.
            inject_id: Inject stable ID if missing.

        Returns:
            IndexResult with details of the operation.
        """
        note = self.vault.read_note(path)

        if note is None:
            return IndexResult(
                note_id="",
                path=path,
                action="error",
                error="Note not found",
            )

        try:
            # Get or generate stable ID
            note_id, id_was_generated = self._get_stable_id(note)

            # Check if content has changed
            content_hash = self._compute_content_hash(note.content)
            cached_hash = self._content_hashes.get(path)
            existing_state = (
                self.index_state_store.get(note_id)
                if self.index_state_store
                else None
            )

            if not force and cached_hash == content_hash:
                return IndexResult(
                    note_id=note_id,
                    path=path,
                    action="unchanged",
                )

            # Initialize or update index state (v1.0.1)
            self._update_index_state_start(note_id, path, content_hash)

            # Inject stable ID if needed
            stable_id_added = False
            if inject_id and id_was_generated and self._inject_stable_id(path, note_id):
                stable_id_added = True
                # Re-read note to get updated frontmatter
                note = self.vault.read_note(path)

            # Determine action
            action = "created" if cached_hash is None else "updated"

            # Update document store
            if self.document_store:
                doc_id = f"obsidian:{note_id}"
                # Serialize frontmatter to ensure JSON compatibility
                metadata = _serialize_for_json({
                    "source": "obsidian",
                    "vault_path": path,
                    "title": note.title,
                    "tags": note.tags,
                    "links": note.links,
                    "content_hash": content_hash,
                    "frontmatter": note.frontmatter,
                    "extracted_by": "vault_indexer",  # v1.0.1: Provenance
                })

                # Use legacy store() when present, otherwise put()
                store_fn = getattr(self.document_store, "store", None)
                if callable(store_fn):
                    await _await_if_needed(
                        store_fn(
                            doc_id=doc_id,
                            content=note.content,
                            metadata=metadata,
                        )
                    )
                else:
                    await _await_if_needed(
                        self.document_store.put(
                            doc_id=doc_id,
                            content=note.content,
                            metadata=metadata,
                        )
                    )

                # Update index state if available
                self._update_doc_index_state(note_id)

            # Update graph store with v1.0.1 ontology
            graph_updated = False
            if self.graph_store:
                # Create/update note node using typed NodeType
                node_id = f"note:{note_id}"
                await _await_if_needed(
                    self.graph_store.upsert_node(
                        node_id=node_id,
                        node_type=NodeType.NOTE.value,  # v1.0.1: Use enum
                        properties={
                            "note_id": note_id,
                            "path": path,
                            "title": note.title,
                            "created_at": (
                                note.created.isoformat() if note.created else None
                            ),
                            "modified_at": (
                                note.modified.isoformat() if note.modified else None
                            ),
                            "tags": note.tags,
                            "content_hash": content_hash,
                            "extracted_by": "vault_indexer",  # v1.0.1: Provenance
                        },
                    )
                )

                # Track current targets to delete stale edges later
                current_tag_targets: list[str] = []
                current_link_targets: list[str] = []

                # Create tag nodes and edges using typed enums
                for tag in note.tags:
                    tag_node_id = f"tag:{tag}"
                    current_tag_targets.append(tag_node_id)
                    await _await_if_needed(
                        self.graph_store.upsert_node(
                            node_id=tag_node_id,
                            node_type=NodeType.TAG.value,  # v1.0.1: Use enum
                            properties={
                                "name": tag,
                                "extracted_by": "vault_indexer",
                            },
                        )
                    )
                    await _await_if_needed(
                        self.graph_store.upsert_edge(
                            source_id=node_id,
                            target_id=tag_node_id,
                            edge_type=EdgeType.NOTE_TAGGED_WITH_TAG.value,
                            properties={
                                # Confidence 1.0: Tag from frontmatter is certain
                                "confidence": 1.0,
                                "source": "human",  # Distinguishes from auto-tags
                                "extracted_by": "vault_indexer",
                            },
                        )
                    )

                # Delete stale human tag edges (tags removed from frontmatter)
                # Only delete edges where source="human" and target not in current set
                existing_tag_edges = await _await_if_needed(
                    self.graph_store.get_edges(
                        node_id=node_id,
                        direction="outgoing",
                        edge_type=EdgeType.NOTE_TAGGED_WITH_TAG.value,
                    )
                )
                deleted_tag_count = 0
                for edge in existing_tag_edges:
                    props = edge.get("properties", {})
                    # Only delete human-sourced tag edges not in current targets
                    if props.get("source") == "human":
                        target = edge.get("target_id")
                        if target not in current_tag_targets:
                            await _await_if_needed(
                                self.graph_store.delete_edge(edge["edge_id"])
                            )
                            deleted_tag_count += 1
                if deleted_tag_count > 0:
                    logger.info(
                        "stale_human_tag_edges_deleted",
                        note_id=note_id,
                        count=deleted_tag_count,
                    )

                # Create link edges using typed EdgeType
                for link in note.links:
                    # Try to resolve link to a note
                    linked_note = self.vault.read_note(link)
                    if linked_note:
                        linked_id, _ = self._get_stable_id(linked_note)
                        target_node_id = f"note:{linked_id}"
                    else:
                        # Create placeholder node for unresolved link
                        target_node_id = f"link:{link}"
                        await _await_if_needed(
                            self.graph_store.upsert_node(
                                node_id=target_node_id,
                                node_type="unresolved_link",  # Not in enum, but tracked
                                properties={
                                    "name": link,
                                    "extracted_by": "vault_indexer",
                                },
                            )
                        )

                    current_link_targets.append(target_node_id)
                    await _await_if_needed(
                        self.graph_store.upsert_edge(
                            source_id=node_id,
                            target_id=target_node_id,
                            edge_type=EdgeType.NOTE_LINKS_TO_NOTE.value,  # v1.0.1
                            properties={
                                "confidence": 1.0,  # v1.0.1: Explicit wiki-link
                                "extracted_by": "vault_indexer",
                            },
                        )
                    )

                # Delete stale link edges (links removed from note)
                deleted_links = await _await_if_needed(
                    self.graph_store.delete_edges_from_source(
                        source_id=node_id,
                        edge_type=EdgeType.NOTE_LINKS_TO_NOTE.value,
                        exclude_targets=current_link_targets,
                    )
                )
                deleted_count = (
                    int(deleted_links) if isinstance(deleted_links, int) else 0
                )
                if deleted_count > 0:
                    logger.info(
                        "stale_link_edges_deleted",
                        note_id=note_id,
                        count=deleted_count,
                    )

                graph_updated = True

                # Update index state if available
                self._update_graph_index_state(note_id)

            # Extract and index tasks (v1.0.1)
            tasks_extracted = 0
            if self.graph_store:
                tasks_extracted = await self._extract_and_index_tasks(
                    note=note,
                    note_id=note_id,
                    node_id=f"note:{note_id}",
                    content=note.content,
                    path=path,
                )

            # LLM Enrichment (v1.0.3) - Moved BEFORE embedding to get summary
            enriched = False
            auto_tags: list[str] | None = None
            auto_class: str | None = None
            auto_summary: str | None = None
            enrichment_skipped_reason: str | None = None

            # Check for existing auto.summary in frontmatter
            existing_auto = note.frontmatter.get("auto", {})
            if isinstance(existing_auto, dict):
                auto_summary = existing_auto.get("summary")

            already_enriched = (
                not force
                and existing_state is not None
                and existing_state.content_hash == content_hash
                and existing_state.enriched_at is not None
            )

            if self.enable_enrichment and self.enrichment_service:
                if already_enriched:
                    enrichment_skipped_reason = "already_enriched"
                    logger.debug(
                        "enrichment_skipped",
                        note_id=note_id,
                        path=path,
                        reason=enrichment_skipped_reason,
                        enriched_at=existing_state.enriched_at.isoformat()
                        if existing_state and existing_state.enriched_at
                        else None,
                    )
                    enrichment = None
                else:
                    # Determine if we should enrich and include summary (v1.0.5)
                    # Use source-specific thresholds from the config
                    should_enrich, include_summary, reason = self.source_config.thresholds.should_enrich(
                        content=note.content,
                        path=path,
                        entity_type=KnownEntityTypes.NOTE,
                        tags=note.tags,
                        classification=existing_auto.get("class") if isinstance(existing_auto, dict) else None,
                    )

                    if not should_enrich:
                        # Skip enrichment entirely based on config
                        enrichment_skipped_reason = reason
                        logger.debug(
                            "enrichment_skipped",
                            note_id=note_id,
                            path=path,
                            reason=reason,
                        )
                        enrichment = None
                    else:
                        # Create EntityRef for source-aware enrichment
                        entity_ref = EntityRef.from_note(note_id=note_id, path=path)

                        # Use enrich_entity with source config if available
                        enrichment = await self.enrichment_service.enrich_entity(
                            content=note.content,
                            entity_ref=entity_ref,
                            title=note.title,
                            existing_tags=note.tags,
                            include_summary=include_summary,
                            source_config=self.source_config,
                            config_registry=self.enrichment_registry,
                        )
                        if not include_summary:
                            logger.debug(
                                "summary_skipped",
                                note_id=note_id,
                                path=path,
                                reason=reason,
                            )
                if enrichment and enrichment.success and self._inject_auto_fields(path, enrichment):
                    enriched = True
                    auto_tags = enrichment.auto_tags
                    auto_class = enrichment.auto_class
                    auto_summary = enrichment.auto_summary  # Capture summary for embedding

                    # Wire auto-tags into graph as edges (v1.0.3)
                    if self.graph_store and auto_tags:
                        node_id = f"note:{note_id}"
                        current_auto_tag_targets: list[str] = []

                        for auto_tag in auto_tags:
                            tag_node_id = f"tag:{auto_tag}"
                            current_auto_tag_targets.append(tag_node_id)

                            # Create tag node (upsert - may already exist)
                            self.graph_store.upsert_node(
                                node_id=tag_node_id,
                                node_type=NodeType.TAG.value,
                                properties={
                                    "name": auto_tag,
                                    "extracted_by": "enrichment_service",
                                },
                            )

                            # Create edge with source=auto to distinguish from human tags
                            self.graph_store.upsert_edge(
                                source_id=node_id,
                                target_id=tag_node_id,
                                edge_type=EdgeType.NOTE_TAGGED_WITH_TAG.value,
                                properties={
                                    "confidence": enrichment.tag_confidence,
                                    "source": "auto",  # Distinguishes from human tags
                                    "extracted_by": "enrichment_service",
                                },
                            )

                        # Delete stale auto-tag edges (but keep human-tag edges)
                        # We need to check properties to only delete auto-tagged edges
                        existing_edges = self.graph_store.get_edges(
                            node_id=node_id,
                            direction="outgoing",
                            edge_type=EdgeType.NOTE_TAGGED_WITH_TAG.value,
                        )
                        for edge in existing_edges:
                            props = edge.get("properties", {})
                            # Only delete if it's an auto-tag edge AND not in current set
                            if props.get("source") == "auto":
                                target = edge.get("target_id")
                                if target not in current_auto_tag_targets:
                                    self.graph_store.delete_edge(edge["edge_id"])
                                    logger.debug(
                                        "stale_auto_tag_edge_deleted",
                                        note_id=note_id,
                                        target=target,
                                    )

                        logger.debug(
                            "auto_tags_wired_to_graph",
                            note_id=note_id,
                            auto_tags=auto_tags,
                        )

                    # Update index state enrichment timestamp
                    if self.index_state_store:
                        state = self.index_state_store.get(note_id)
                        if state:
                            state.enriched_at = utc_now()
                            self.index_state_store.save(state)

            # Hierarchical Embedding (v1.0.3) - Summary + Chunks
            vector_updated = False
            if self.vector_store and self.embedding_service:
                # Phase 1: Embed summary (for note-level relevance)
                if auto_summary:
                    summary_id = f"{note_id}:summary"
                    summary_vector = await self.embedding_service.embed(auto_summary)
                    self.vector_store.upsert(
                        item_id=summary_id,
                        vector=summary_vector,
                        metadata={
                            "note_id": note_id,
                            "path": path,
                            "title": note.title,
                            "embedding_type": "summary",  # v1.0.3: Hierarchical
                            "text": auto_summary,
                            "extracted_by": "vault_indexer",
                        },
                    )
                    logger.debug(
                        "summary_embedded",
                        note_id=note_id,
                        summary_length=len(auto_summary),
                    )

                # Phase 2: Embed chunks (for passage retrieval)
                chunks = self._chunk_content(note.content)
                for i, chunk in enumerate(chunks):
                    chunk_id = f"{note_id}:chunk_{i}"
                    embedding_vector = await self.embedding_service.embed(chunk["text"])
                    self.vector_store.upsert(
                        item_id=chunk_id,
                        vector=embedding_vector,
                        metadata={
                            "note_id": note_id,
                            "path": path,
                            "title": note.title,
                            "embedding_type": "chunk",  # v1.0.3: Hierarchical
                            "chunk_index": i,
                            "start_offset": chunk["start_offset"],
                            "end_offset": chunk["end_offset"],
                            "text": chunk["text"][:200],  # Preview
                            "extracted_by": "vault_indexer",
                        },
                    )

                vector_updated = True
                logger.debug(
                    "hierarchical_embeddings_created",
                    note_id=note_id,
                    has_summary=bool(auto_summary),
                    chunk_count=len(chunks),
                )

                # Update index state if available
                self._update_vector_index_state(note_id)

            # Cache the hash
            self._content_hashes[path] = content_hash

            logger.info(
                "note_indexed",
                note_id=note_id,
                path=path,
                action=action,
                graph=graph_updated,
                vector=vector_updated,
                tasks=tasks_extracted,
                enriched=enriched,
            )

            return IndexResult(
                note_id=note_id,
                path=path,
                action=action,
                graph_updated=graph_updated,
                vector_updated=vector_updated,
                stable_id_added=stable_id_added,
                tasks_extracted=tasks_extracted,
                enriched=enriched,
                auto_tags=auto_tags,
                auto_class=auto_class,
            )

        except Exception as e:
            logger.exception("note_index_failed", path=path)
            return IndexResult(
                note_id="",
                path=path,
                action="error",
                error=str(e),
            )

    async def index_folder(
        self,
        folder: str | None = None,
        recursive: bool = True,
        force: bool = False,
        inject_ids: bool = True,
    ) -> IndexSummary:
        """Index all notes in a folder.

        Args:
            folder: Folder to index (None for entire vault).
            recursive: Include subfolders.
            force: Force re-indexing even if unchanged.
            inject_ids: Inject stable IDs if missing.

        Returns:
            IndexSummary with results.
        """
        summary = IndexSummary(started_at=utc_now())

        paths = self.vault.list_notes(folder=folder, recursive=recursive)
        summary.total_notes = len(paths)

        logger.info(
            "vault_index_started",
            folder=folder or "root",
            note_count=len(paths),
        )

        for path in paths:
            result = await self.index_note(
                path=path,
                force=force,
                inject_id=inject_ids,
            )
            summary.results.append(result)

            if result.action == "created":
                summary.created += 1
            elif result.action == "updated":
                summary.updated += 1
            elif result.action == "unchanged":
                summary.unchanged += 1
            elif result.action == "error":
                summary.errors += 1

        summary.completed_at = utc_now()

        logger.info(
            "vault_index_completed",
            total=summary.total_notes,
            created=summary.created,
            updated=summary.updated,
            unchanged=summary.unchanged,
            errors=summary.errors,
            duration_ms=int(
                (summary.completed_at - summary.started_at).total_seconds() * 1000
            ),
        )

        return summary

    async def index_changed(
        self,
        paths: list[str],
        inject_ids: bool = True,
    ) -> IndexSummary:
        """Index specific changed notes.

        Args:
            paths: List of changed note paths.
            inject_ids: Inject stable IDs if missing.

        Returns:
            IndexSummary with results.
        """
        summary = IndexSummary(started_at=utc_now())
        summary.total_notes = len(paths)

        for path in paths:
            result = await self.index_note(
                path=path,
                force=True,  # Always re-index changed notes
                inject_id=inject_ids,
            )
            summary.results.append(result)

            if result.action == "created":
                summary.created += 1
            elif result.action == "updated":
                summary.updated += 1
            elif result.action == "unchanged":
                summary.unchanged += 1
            elif result.action == "error":
                summary.errors += 1

        summary.completed_at = utc_now()
        return summary

    async def reconcile(self, dry_run: bool = False) -> dict[str, Any]:
        """Reconcile indexes with vault state.

        Finds:
        - Notes in vault but not in index
        - Notes in index but deleted from vault
        - Notes with mismatched content hashes

        Args:
            dry_run: If True, report but don't fix issues.

        Returns:
            Dict with reconciliation results.
        """
        results = {
            "missing_in_index": [],
            "orphaned_in_index": [],
            "hash_mismatch": [],
            "fixed": 0,
        }

        # Get all notes from vault
        vault_paths = set(self.vault.list_notes(recursive=True))

        # Get all indexed notes from document store
        indexed_paths: set[str] = set()
        if self.document_store:
            # Query all obsidian documents
            docs = self.document_store.list_documents(limit=1000)
            for doc in docs:
                if doc.metadata and "vault_path" in doc.metadata:
                    indexed_paths.add(doc.metadata["vault_path"])

        # Find missing in index
        for path in vault_paths - indexed_paths:
            results["missing_in_index"].append(path)
            if not dry_run:
                await self.index_note(path, force=True)
                results["fixed"] += 1

        # Find orphaned in index and clean up
        for path in indexed_paths - vault_paths:
            results["orphaned_in_index"].append(path)
            if not dry_run:
                await self.delete_note_by_path(path)
                results["fixed"] += 1

        logger.info(
            "vault_reconcile_complete",
            missing=len(results["missing_in_index"]),
            orphaned=len(results["orphaned_in_index"]),
            fixed=results["fixed"],
            dry_run=dry_run,
        )

        return results

    async def delete_note(self, note_id: str) -> bool:
        """Delete a note from all stores by its ID.

        Removes the note from:
        - Document store
        - Graph store (node + all edges)
        - Vector store (all embeddings)
        - Index state store

        Args:
            note_id: The stable note ID (e.g., note_01J...).

        Returns:
            True if the note was found and deleted.
        """
        deleted = False
        doc_id = f"obsidian:{note_id}"
        node_id = f"note:{note_id}"

        # Delete from document store
        if self.document_store:
            try:
                self.document_store.delete(doc_id)
                deleted = True
                logger.debug("note_deleted_from_docs", note_id=note_id)
            except Exception:
                pass  # May not exist

        # Delete from graph store (node + edges)
        if self.graph_store:
            try:
                # Delete all edges connected to this node first
                edges = self.graph_store.get_edges(node_id=node_id, direction="both")
                for edge in edges:
                    self.graph_store.delete_edge(edge.get("edge_id"))
                
                # Delete the node itself
                self.graph_store.delete_node(node_id)
                deleted = True
                logger.debug("note_deleted_from_graph", note_id=note_id, edges_deleted=len(edges))
            except Exception:
                pass  # May not exist

        # Delete from vector store
        if self.vector_store:
            try:
                # Delete summary embedding
                self.vector_store.delete(f"{note_id}:summary")
                
                # Delete chunk embeddings (they follow pattern note_id:chunk:N)
                # Most vector stores support prefix deletion or we delete known IDs
                for i in range(100):  # Reasonable max chunks
                    chunk_id = f"{note_id}:chunk:{i}"
                    try:
                        self.vector_store.delete(chunk_id)
                    except Exception:
                        break  # No more chunks
                
                deleted = True
                logger.debug("note_deleted_from_vectors", note_id=note_id)
            except Exception:
                pass  # May not exist

        # Delete from index state store
        if self.index_state_store:
            try:
                self.index_state_store.delete(note_id)
                logger.debug("note_deleted_from_index_state", note_id=note_id)
            except Exception:
                pass

        # Clear from content hash cache
        if note_id in self._content_hashes:
            del self._content_hashes[note_id]

        if deleted:
            logger.info("note_deleted", note_id=note_id)
        else:
            logger.warning("note_delete_not_found", note_id=note_id)

        return deleted

    async def delete_note_by_path(self, path: str) -> bool:
        """Delete a note from all stores by its vault path.

        Looks up the note ID from the document store, then deletes.

        Args:
            path: The vault path of the note.

        Returns:
            True if the note was found and deleted.
        """
        if not self.document_store:
            logger.warning("delete_note_by_path_no_doc_store")
            return False

        # Find the note by path
        docs = self.document_store.list_documents(limit=1)
        # We need to search for the document by path
        # This is a simplified approach - in production we'd have a path index
        all_docs = self.document_store.list_documents(limit=10000)
        for doc in all_docs:
            if doc.metadata and doc.metadata.get("vault_path") == path:
                # Extract note_id from doc_id (format: obsidian:note_id)
                if doc.doc_id.startswith("obsidian:"):
                    note_id = doc.doc_id[9:]  # Remove "obsidian:" prefix
                    return await self.delete_note(note_id)

        logger.warning("delete_note_by_path_not_found", path=path)
        return False

    async def delete_notes(self, note_ids: list[str]) -> int:
        """Delete multiple notes from all stores.

        Args:
            note_ids: List of note IDs to delete.

        Returns:
            Number of notes successfully deleted.
        """
        deleted_count = 0
        for note_id in note_ids:
            if await self.delete_note(note_id):
                deleted_count += 1

        logger.info(
            "notes_batch_deleted",
            requested=len(note_ids),
            deleted=deleted_count,
        )
        return deleted_count
