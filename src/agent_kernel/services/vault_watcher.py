"""Vault Watcher - connects file watcher to vault indexer.

Watches the Obsidian vault for changes and triggers indexing.
Implements debouncing to handle rapid edits.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import structlog

from agent_kernel.core.config import get_settings
from agent_kernel.memory.document_store import DocumentStore
from agent_kernel.memory.graph_store import GraphStore
from agent_kernel.memory.vector_store import VectorStore
from agent_kernel.scheduler.file_watcher import (
    FileEvent,
    FileEventType,
    FileWatcher,
    WatchConfig,
)
from agent_kernel.services.embedding import EmbeddingService
from agent_kernel.services.index_state import IndexStateStore
from agent_kernel.services.vault_indexer import IndexSummary, VaultIndexer
from agent_kernel.tools.builtin.obsidian import ObsidianVault

logger = structlog.get_logger(__name__)


class VaultWatcher:
    """Watches an Obsidian vault and triggers indexing on changes.

    Implements the pattern from docs/design/12-integration-patterns.md:
    - File watcher as primary trigger
    - Debouncing to prevent spam on rapid edits
    - Batch processing of accumulated changes
    """

    def __init__(
        self,
        vault: ObsidianVault | None = None,
        vault_path: str | Path | None = None,
        document_store: DocumentStore | None = None,
        graph_store: GraphStore | None = None,
        vector_store: VectorStore | None = None,
        embedding_service: EmbeddingService | None = None,
        index_state_store: IndexStateStore | None = None,
        debounce_seconds: float = 10.0,
        batch_interval: float = 30.0,
    ) -> None:
        """Initialize the vault watcher.

        Args:
            vault: Existing ObsidianVault instance.
            vault_path: Path to vault (used if vault not provided).
            document_store: Store for full document content.
            graph_store: Store for relationship graph.
            vector_store: Store for vector embeddings.
            embedding_service: Service for generating embeddings.
            index_state_store: Store for tracking indexing state (v1.0.1).
            debounce_seconds: Seconds to wait for file stability.
            batch_interval: Seconds between batch processing runs.
        """
        # Get vault path from settings if not provided
        if vault is None:
            if vault_path is None:
                settings = get_settings()
                vault_path = settings.obsidian_vault_path
                if not vault_path:
                    msg = "No vault path provided. Set OBSIDIAN_VAULT_PATH in .env"
                    raise ValueError(msg)
            self.vault = ObsidianVault(vault_path)
        else:
            self.vault = vault

        # Store for later access
        self.index_state_store = index_state_store

        # Create indexer with index state tracking (v1.0.1)
        self.indexer = VaultIndexer(
            vault=self.vault,
            document_store=document_store,
            graph_store=graph_store,
            vector_store=vector_store,
            embedding_service=embedding_service,
            index_state_store=index_state_store,
        )

        # Create file watcher
        self.file_watcher = FileWatcher(poll_interval=2.0)

        # Configuration
        self.debounce_seconds = debounce_seconds
        self.batch_interval = batch_interval

        # Track pending changes
        self._pending_changes: dict[str, FileEventType] = {}
        self._pending_lock = asyncio.Lock()
        self._batch_task: asyncio.Task | None = None
        self._running = False

        # Watch ID
        self._watch_id: str | None = None

        # Event callbacks
        self._on_index_complete: list[callable] = []

    def on_index_complete(self, callback: callable[[IndexSummary], None]) -> None:
        """Register callback for when indexing completes.

        Args:
            callback: Function to call with IndexSummary.
        """
        self._on_index_complete.append(callback)

    async def _handle_file_event(
        self, watch_id: str, event: FileEvent  # noqa: ARG002
    ) -> None:
        """Handle a file system event.

        Args:
            watch_id: Watch ID that triggered.
            event: The file event.
        """
        # Only process markdown files
        if not event.path.endswith(".md"):
            return

        # Get relative path from vault
        vault_path = Path(self.vault.vault_path)
        try:
            rel_path = str(Path(event.path).relative_to(vault_path))
        except ValueError:
            # Path not under vault
            return

        logger.debug(
            "vault_file_event",
            event_type=event.event_type.value,
            path=rel_path,
        )

        # Queue for batch processing
        async with self._pending_lock:
            # Store the latest event type for this path
            self._pending_changes[rel_path] = event.event_type

    async def _batch_processor(self) -> None:
        """Process accumulated changes in batches."""
        while self._running:
            try:
                await asyncio.sleep(self.batch_interval)

                # Get pending changes
                async with self._pending_lock:
                    if not self._pending_changes:
                        continue

                    changes = dict(self._pending_changes)
                    self._pending_changes.clear()

                # Process changes
                await self._process_changes(changes)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("batch_processor_error")

    async def _process_changes(self, changes: dict[str, FileEventType]) -> None:
        """Process a batch of changes.

        Args:
            changes: Dict of path -> event type.
        """
        logger.info(
            "processing_vault_changes",
            count=len(changes),
        )

        # Separate by event type
        to_index: list[str] = []
        to_delete: list[str] = []

        for path, event_type in changes.items():
            if event_type == FileEventType.DELETED:
                to_delete.append(path)
            else:
                to_index.append(path)

        # Index changed/created notes
        if to_index:
            summary = await self.indexer.index_changed(to_index)

            logger.info(
                "vault_changes_indexed",
                total=summary.total_notes,
                created=summary.created,
                updated=summary.updated,
                errors=summary.errors,
            )

            # Notify callbacks
            for callback in self._on_index_complete:
                try:
                    callback(summary)
                except Exception:
                    logger.exception("callback_error")

        # Handle deletions - clean up from all stores
        if to_delete:
            logger.info("vault_notes_deleted", count=len(to_delete))
            deleted_count = 0
            for path in to_delete:
                if await self.indexer.delete_note_by_path(path):
                    deleted_count += 1
            logger.info(
                "vault_deletions_processed",
                requested=len(to_delete),
                deleted=deleted_count,
            )

    async def start(self) -> None:
        """Start watching the vault."""
        if self._running:
            return

        self._running = True

        # Create watch config
        config = WatchConfig(
            path=str(self.vault.vault_path),
            patterns=["*.md"],
            ignore_patterns=[".obsidian/*", ".trash/*"],
            event_types=[
                FileEventType.CREATED,
                FileEventType.MODIFIED,
                FileEventType.DELETED,
            ],
            recursive=True,
            debounce_seconds=self.debounce_seconds,
        )

        # Add watch
        self._watch_id = self.file_watcher.add_watch(config, self._handle_file_event)

        # Start file watcher
        await self.file_watcher.start()

        # Start batch processor
        self._batch_task = asyncio.create_task(self._batch_processor())

        logger.info(
            "vault_watcher_started",
            vault_path=str(self.vault.vault_path),
            watch_id=self._watch_id,
        )

    async def stop(self) -> None:
        """Stop watching the vault."""
        self._running = False

        # Stop batch processor
        if self._batch_task:
            self._batch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._batch_task

        # Stop file watcher
        await self.file_watcher.stop()

        logger.info("vault_watcher_stopped")

    async def full_sync(self, force: bool = False) -> IndexSummary:
        """Run a full vault sync.

        Args:
            force: Force re-indexing even if unchanged.

        Returns:
            IndexSummary with results.
        """
        logger.info(
            "vault_full_sync_started",
            vault_path=str(self.vault.vault_path),
            force=force,
        )

        summary = await self.indexer.index_folder(force=force)

        logger.info(
            "vault_full_sync_completed",
            total=summary.total_notes,
            created=summary.created,
            updated=summary.updated,
            unchanged=summary.unchanged,
            errors=summary.errors,
        )

        return summary

    async def reconcile(self, dry_run: bool = False) -> dict[str, Any]:
        """Run reconciliation to fix any drift.

        Args:
            dry_run: If True, report but don't fix.

        Returns:
            Reconciliation results.
        """
        return await self.indexer.reconcile(dry_run=dry_run)


async def create_vault_watcher(
    vault_path: str | Path | None = None,
    document_store: DocumentStore | None = None,
    graph_store: GraphStore | None = None,
    vector_store: VectorStore | None = None,
    embedding_service: EmbeddingService | None = None,
    index_state_store: IndexStateStore | None = None,
) -> VaultWatcher:
    """Create and configure a vault watcher.

    Args:
        vault_path: Path to Obsidian vault (uses config if not provided).
        document_store: Optional document store.
        graph_store: Optional graph store.
        vector_store: Optional vector store.
        embedding_service: Optional embedding service.
        index_state_store: Optional index state store (v1.0.1).

    Returns:
        Configured VaultWatcher.
    """
    return VaultWatcher(
        vault_path=vault_path,
        document_store=document_store,
        graph_store=graph_store,
        vector_store=vector_store,
        embedding_service=embedding_service,
        index_state_store=index_state_store,
    )
