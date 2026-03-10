"""File Indexer service for non-text file ingestion.

Pointer-only storage: the kernel stores URIs, metadata, and LLM-generated
descriptions but never copies file bytes. Extracted knowledge persists
even if the original file is deleted or moved.

Pipeline (modeled on VaultIndexer's 7-stage approach):
1. Discover files via directory scan or explicit path
2. Generate stable IDs (stored in sidecar DB, not in file)
3. Extract text content via ResourceExtractor
4. Store description in document store (pointer + extracted text)
5. Create graph nodes with URI, metadata, file type
6. Enrich via LLM (summary, tags, classification, importance)
7. Embed description/summary in vector store

Staleness detection:
- Tracks content_hash (md5 of file bytes) per file
- On re-index: compares hash to detect changes
- On missing file: marks graph node as STALE, keeps extracted knowledge
"""

from __future__ import annotations

import hashlib
import inspect
import mimetypes
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.graph import EdgeType, NodeType
from agent_kernel.memory.document_store import DocumentStore
from agent_kernel.memory.graph_store import GraphStore
from agent_kernel.memory.vector_store import VectorStore
from agent_kernel.services.embedding import EmbeddingService
from agent_kernel.services.resource_extraction import (
    EXTENSION_MAPPING,
    ResourceExtractor,
    ResourceType,
)

if TYPE_CHECKING:
    from agent_kernel.services.enrichment import (
        EnrichmentResult,
        EnrichmentService,
    )
    from agent_kernel.services.index_state import IndexStateStore

logger = structlog.get_logger(__name__)

# File extensions we can attempt to index
SUPPORTED_EXTENSIONS = set(EXTENSION_MAPPING.keys()) | {
    ".csv", ".json", ".yaml", ".yml", ".toml",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".mp3", ".wav", ".mp4", ".mov", ".avi", ".mkv",
}

# Extensions where we can extract text content
TEXT_EXTRACTABLE = set(EXTENSION_MAPPING.keys()) | {
    ".csv", ".json", ".yaml", ".yml", ".toml",
}

# Media files: pointer-only, no text extraction
MEDIA_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".mp3", ".wav", ".mp4", ".mov", ".avi", ".mkv",
}


@dataclass
class FileIndexResult:
    """Result of indexing a single file."""

    file_id: str
    path: str
    action: str  # "created", "updated", "unchanged", "stale", "error"
    resource_type: str = ""
    graph_updated: bool = False
    vector_updated: bool = False
    enriched: bool = False
    auto_tags: list[str] | None = None
    auto_summary: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "path": self.path,
            "action": self.action,
            "resource_type": self.resource_type,
            "graph_updated": self.graph_updated,
            "vector_updated": self.vector_updated,
            "enriched": self.enriched,
            "auto_tags": self.auto_tags,
            "auto_summary": self.auto_summary,
            "error": self.error,
        }


@dataclass
class FileIndexSummary:
    """Summary of a file indexing run."""

    started_at: datetime
    completed_at: datetime | None = None
    total_files: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    stale: int = 0
    errors: int = 0
    results: list[FileIndexResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        completed = (
            self.completed_at.isoformat() if self.completed_at else None
        )
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": completed,
            "total_files": self.total_files,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "stale": self.stale,
            "errors": self.errors,
            "results": [r.to_dict() for r in self.results],
        }


async def _await_if_needed(value: Any) -> Any:
    """Await value if it's awaitable; otherwise return as-is."""
    if inspect.isawaitable(value):
        return await value
    return value


class FileIndexer:
    """Indexes non-text files into the kernel's memory stores.

    Pointer-only: stores URI + extracted metadata, never copies
    file bytes. Knowledge extracted from files persists even if
    the original is deleted.
    """

    def __init__(
        self,
        document_store: DocumentStore | None = None,
        graph_store: GraphStore | None = None,
        vector_store: VectorStore | None = None,
        embedding_service: EmbeddingService | None = None,
        index_state_store: IndexStateStore | None = None,
        enrichment_service: EnrichmentService | None = None,
        extractor: ResourceExtractor | None = None,
        enable_enrichment: bool = False,
        max_content_length: int = 8000,
    ) -> None:
        self.document_store = document_store
        self.graph_store = graph_store
        self.vector_store = vector_store
        self.embedding_service = embedding_service
        self.index_state_store = index_state_store
        self.enrichment_service = enrichment_service
        self.extractor = extractor or ResourceExtractor()
        self.enable_enrichment = enable_enrichment
        self.max_content_length = max_content_length

        # In-memory hash cache for change detection
        self._content_hashes: dict[str, str] = {}

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute MD5 hash of file bytes (first 16 hex chars)."""
        hash_md5 = hashlib.md5(usedforsecurity=False)
        try:
            with file_path.open("rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hash_md5.update(chunk)
        except OSError:
            return ""
        return hash_md5.hexdigest()[:16]

    def _generate_file_id(self) -> str:
        """Generate a stable file ID."""
        return f"file_{generate_ulid()}"

    def _get_file_id(self, file_path: str) -> str | None:
        """Look up existing file ID from index state store."""
        if not self.index_state_store:
            return None
        state = self.index_state_store.get_by_path(file_path)
        if state:
            return state.entity_id
        return None

    def _get_or_create_file_id(
        self, file_path: str
    ) -> tuple[str, bool]:
        """Get existing or create new file ID."""
        existing = self._get_file_id(file_path)
        if existing:
            return existing, False
        return self._generate_file_id(), True

    def _is_supported(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in SUPPORTED_EXTENSIONS

    def _can_extract_text(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in TEXT_EXTRACTABLE

    def _is_media(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in MEDIA_EXTENSIONS

    @staticmethod
    def _guess_mime_type(file_path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(str(file_path))
        return mime_type or "application/octet-stream"

    async def index_file(
        self,
        file_path: str | Path,
        force: bool = False,
    ) -> FileIndexResult:
        """Index a single file.

        Args:
            file_path: Absolute or relative path to the file.
            force: Force re-indexing even if unchanged.

        Returns:
            FileIndexResult with details of the operation.
        """
        path = Path(file_path).resolve()
        path_str = str(path)

        # Check file exists and is supported
        if not path.exists():
            existing_id = self._get_file_id(path_str)
            if existing_id:
                return await self._mark_stale(existing_id, path_str)
            return FileIndexResult(
                file_id="", path=path_str,
                action="error", error="File not found",
            )

        if not path.is_file():
            return FileIndexResult(
                file_id="", path=path_str,
                action="error", error="Path is not a file",
            )

        if not self._is_supported(path):
            return FileIndexResult(
                file_id="", path=path_str, action="error",
                error=f"Unsupported file type: {path.suffix}",
            )

        try:
            return await self._do_index(path, path_str, force)
        except Exception as e:
            logger.exception("file_indexing_failed", path=path_str)
            return FileIndexResult(
                file_id="", path=path_str,
                action="error", error=str(e),
            )

    async def _do_index(
        self,
        path: Path,
        path_str: str,
        force: bool,
    ) -> FileIndexResult:
        """Core indexing logic for a single file."""
        content_hash = self._compute_file_hash(path)
        cached_hash = self._content_hashes.get(path_str)
        file_id, is_new = self._get_or_create_file_id(path_str)

        if not force and cached_hash == content_hash:
            return FileIndexResult(
                file_id=file_id, path=path_str,
                action="unchanged",
            )

        resource_type = self.extractor.get_resource_type(path)

        # Extract text content (if possible)
        extracted_text = ""
        if self._can_extract_text(path):
            extraction = await self.extractor.extract(path)
            if extraction.success:
                extracted_text = extraction.raw_text

        # File metadata
        stat = path.stat()
        file_metadata = {
            "file_name": path.name,
            "file_size": stat.st_size,
            "mime_type": self._guess_mime_type(path),
            "resource_type": resource_type.value,
            "created_at": datetime.fromtimestamp(
                stat.st_ctime, tz=UTC,
            ).isoformat(),
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime, tz=UTC,
            ).isoformat(),
            "content_hash": content_hash,
            "uri": path.as_uri(),
            "extracted_by": "file_indexer",
            "is_media": self._is_media(path),
            "has_extracted_text": bool(extracted_text),
        }

        # Update index state
        self._init_index_state(file_id, path_str, content_hash)

        # Store in document store
        if self.document_store:
            self._store_document(
                file_id, extracted_text, resource_type,
                path, file_metadata,
            )

        # Create graph node
        graph_updated = self._update_graph(
            file_id, path, path_str, stat, resource_type,
            content_hash, file_metadata,
        )

        # LLM Enrichment
        enriched, auto_tags, auto_summary = await self._run_enrichment(
            file_id, path, extracted_text,
        )

        # Vector embeddings
        vector_updated = await self._update_vectors(
            file_id, path_str, path.name, resource_type,
            auto_summary or extracted_text,
        )

        # Update hash cache
        self._content_hashes[path_str] = content_hash
        action = "created" if is_new else "updated"

        logger.info(
            "file_indexed",
            file_id=file_id, path=path_str,
            action=action,
            resource_type=resource_type.value,
            enriched=enriched, has_text=bool(extracted_text),
        )

        return FileIndexResult(
            file_id=file_id, path=path_str, action=action,
            resource_type=resource_type.value,
            graph_updated=graph_updated,
            vector_updated=vector_updated,
            enriched=enriched,
            auto_tags=auto_tags, auto_summary=auto_summary,
        )

    def _init_index_state(
        self, file_id: str, path_str: str, content_hash: str,
    ) -> None:
        """Initialize or update index state for a file."""
        if not self.index_state_store:
            return
        from agent_kernel.services.index_state import (  # noqa: PLC0415
            EntityIndexState,
            IndexStatus,
        )

        existing = self.index_state_store.get(file_id)
        if existing:
            if existing.content_hash != content_hash:
                self.index_state_store.mark_stale(
                    file_id, content_hash,
                )
        else:
            self.index_state_store.save(EntityIndexState(
                entity_id=file_id,
                entity_type="file",
                source_path=path_str,
                content_hash=content_hash,
                doc_status=IndexStatus.INDEXING,
                graph_status=IndexStatus.INDEXING,
                vector_status=IndexStatus.PENDING,
            ))

    def _store_document(
        self,
        file_id: str,
        extracted_text: str,
        resource_type: ResourceType,
        path: Path,
        file_metadata: dict[str, Any],
    ) -> None:
        """Store file content/description in document store."""
        doc_id = f"file:{file_id}"
        doc_content = (
            extracted_text
            or f"[{resource_type.value} file: {path.name}]"
        )

        store_fn = getattr(self.document_store, "store", None)
        if callable(store_fn):
            store_fn(
                doc_id=doc_id, content=doc_content,
                metadata=file_metadata,
            )
        else:
            self.document_store.put(
                doc_id=doc_id, content=doc_content,
                metadata=file_metadata,
            )

        if self.index_state_store:
            from agent_kernel.services.index_state import IndexStatus  # noqa: PLC0415
            self.index_state_store.update_doc_status(
                file_id, IndexStatus.INDEXED,
            )

    def _update_graph(
        self,
        file_id: str,
        path: Path,
        path_str: str,
        stat: Any,
        resource_type: ResourceType,
        content_hash: str,
        file_metadata: dict[str, Any],
    ) -> bool:
        """Create/update graph node for the file."""
        if not self.graph_store:
            return False

        node_id = f"file:{file_id}"
        self.graph_store.upsert_node(
            node_id=node_id,
            node_type=NodeType.FILE.value,
            properties={
                "file_id": file_id,
                "name": path.name,
                "path": path_str,
                "uri": path.as_uri(),
                "file_size": stat.st_size,
                "mime_type": file_metadata["mime_type"],
                "resource_type": resource_type.value,
                "content_hash": content_hash,
                "created_at": file_metadata["created_at"],
                "modified_at": file_metadata["modified_at"],
                "extracted_by": "file_indexer",
                "status": "active",
            },
        )

        if self.index_state_store:
            from agent_kernel.services.index_state import IndexStatus  # noqa: PLC0415
            self.index_state_store.update_graph_status(
                file_id, IndexStatus.INDEXED,
            )

        return True

    async def _run_enrichment(
        self,
        file_id: str,
        path: Path,
        extracted_text: str,
    ) -> tuple[bool, list[str] | None, str | None]:
        """Run LLM enrichment if enabled and text is available."""
        if not (
            self.enable_enrichment
            and self.enrichment_service
            and extracted_text
        ):
            return False, None, None

        enrichment = await self._enrich_file(
            file_id, path.name, extracted_text,
        )
        if not enrichment or not enrichment.success:
            return False, None, None

        auto_tags = enrichment.auto_tags
        auto_summary = enrichment.auto_summary

        # Wire auto-tags into graph
        if self.graph_store and auto_tags:
            node_id = f"file:{file_id}"
            for tag in auto_tags:
                tag_node_id = f"tag:{tag}"
                self.graph_store.upsert_node(
                    node_id=tag_node_id,
                    node_type=NodeType.TAG.value,
                    properties={
                        "name": tag,
                        "extracted_by": "file_indexer",
                    },
                )
                self.graph_store.upsert_edge(
                    source_id=node_id,
                    target_id=tag_node_id,
                    edge_type=EdgeType.NOTE_TAGGED_WITH_TAG.value,
                    properties={
                        "confidence": enrichment.tag_confidence,
                        "source": "auto",
                        "extracted_by": "file_indexer",
                    },
                )

        # Update enrichment timestamp
        if self.index_state_store:
            state = self.index_state_store.get(file_id)
            if state:
                state.enriched_at = utc_now()
                self.index_state_store.save(state)

        return True, auto_tags, auto_summary

    async def _update_vectors(
        self,
        file_id: str,
        path_str: str,
        file_name: str,
        resource_type: ResourceType,
        embed_text: str,
    ) -> bool:
        """Embed summary/description for semantic search."""
        if not (
            self.vector_store
            and self.embedding_service
            and embed_text
        ):
            return False

        summary_id = f"{file_id}:summary"
        text = embed_text[:self.max_content_length]
        vector = await self.embedding_service.embed(text)
        self.vector_store.upsert(
            item_id=summary_id,
            vector=vector,
            metadata={
                "file_id": file_id,
                "path": path_str,
                "file_name": file_name,
                "embedding_type": "summary",
                "resource_type": resource_type.value,
                "text": text[:500],
                "extracted_by": "file_indexer",
            },
        )

        if self.index_state_store:
            from agent_kernel.services.index_state import IndexStatus  # noqa: PLC0415
            self.index_state_store.update_vector_status(
                file_id, IndexStatus.INDEXED,
            )

        return True

    async def index_directory(
        self,
        directory: str | Path,
        recursive: bool = True,
        force: bool = False,
        exclude_patterns: list[str] | None = None,
    ) -> FileIndexSummary:
        """Index all supported files in a directory.

        Args:
            directory: Directory to scan.
            recursive: Whether to recurse into subdirectories.
            force: Force re-indexing of all files.
            exclude_patterns: Glob patterns to exclude.

        Returns:
            FileIndexSummary with results.
        """
        dir_path = Path(directory).resolve()
        if not dir_path.is_dir():
            summary = FileIndexSummary(started_at=utc_now())
            summary.errors = 1
            summary.completed_at = utc_now()
            return summary

        exclude = set(exclude_patterns or [])
        default_exclude = {
            ".git", ".obsidian", ".trash",
            "__pycache__", "node_modules", ".venv",
        }

        summary = FileIndexSummary(started_at=utc_now())
        glob_fn = dir_path.rglob if recursive else dir_path.glob
        files = list(glob_fn("*"))

        for fp in files:
            if not fp.is_file():
                continue
            parts = fp.relative_to(dir_path).parts
            if any(part in default_exclude for part in parts):
                continue
            if any(fp.match(pat) for pat in exclude):
                continue
            if not self._is_supported(fp):
                continue

            result = await self.index_file(fp, force=force)
            summary.results.append(result)
            summary.total_files += 1

            if result.action == "created":
                summary.created += 1
            elif result.action == "updated":
                summary.updated += 1
            elif result.action == "unchanged":
                summary.unchanged += 1
            elif result.action == "stale":
                summary.stale += 1
            elif result.action == "error":
                summary.errors += 1

        summary.completed_at = utc_now()

        logger.info(
            "directory_indexed",
            directory=str(dir_path),
            total=summary.total_files,
            created=summary.created, updated=summary.updated,
            unchanged=summary.unchanged, stale=summary.stale,
            errors=summary.errors,
        )

        return summary

    async def reconcile(
        self,
        dry_run: bool = False,
    ) -> dict[str, list[str]]:
        """Check indexed files for staleness.

        Compares index state against filesystem to find:
        - Files that were indexed but no longer exist
        - Files that changed since last index

        Args:
            dry_run: If True, report but don't modify state.

        Returns:
            Dict with 'missing', 'changed', 'stale_marked' lists.
        """
        if not self.index_state_store:
            return {
                "missing": [], "changed": [], "stale_marked": [],
            }

        missing: list[str] = []
        changed: list[str] = []
        stale_marked: list[str] = []

        all_states = self.index_state_store.list_by_entity_type(
            "file",
        )

        for state in all_states:
            if not state.source_path:
                continue

            p = Path(state.source_path)

            if not p.exists():
                missing.append(state.source_path)
                if not dry_run:
                    await self._mark_stale(
                        state.entity_id, state.source_path,
                    )
                    stale_marked.append(state.entity_id)
                continue

            current_hash = self._compute_file_hash(p)
            if (
                current_hash
                and state.content_hash
                and current_hash != state.content_hash
            ):
                changed.append(state.source_path)

        logger.info(
            "file_reconciliation",
            missing=len(missing), changed=len(changed),
            stale_marked=len(stale_marked), dry_run=dry_run,
        )

        return {
            "missing": missing,
            "changed": changed,
            "stale_marked": stale_marked,
        }

    async def _mark_stale(
        self, file_id: str, path: str,
    ) -> FileIndexResult:
        """Mark a file as stale in graph and index state."""
        if self.graph_store:
            node_id = f"file:{file_id}"
            try:
                await _await_if_needed(
                    self.graph_store.upsert_node(
                        node_id=node_id,
                        node_type=NodeType.FILE.value,
                        properties={
                            "status": "stale",
                            "stale_since": utc_now().isoformat(),
                            "extracted_by": "file_indexer",
                        },
                    )
                )
            except Exception:
                logger.exception(
                    "mark_stale_graph_failed", file_id=file_id,
                )

        if self.index_state_store:
            self.index_state_store.mark_stale(file_id, "")

        logger.info(
            "file_marked_stale", file_id=file_id, path=path,
        )

        return FileIndexResult(
            file_id=file_id, path=path, action="stale",
        )

    async def _enrich_file(
        self,
        file_id: str,
        file_name: str,
        extracted_text: str,
    ) -> EnrichmentResult | None:
        """Run LLM enrichment on extracted file content."""
        if not self.enrichment_service:
            return None

        content = extracted_text[:self.max_content_length]
        if len(extracted_text) > self.max_content_length:
            content += "\n\n[Content truncated...]"

        try:
            return await self.enrichment_service.enrich(
                content=content,
                title=file_name,
                existing_tags=[],
            )
        except Exception:
            logger.exception(
                "file_enrichment_failed",
                file_id=file_id, file_name=file_name,
            )
            return None
