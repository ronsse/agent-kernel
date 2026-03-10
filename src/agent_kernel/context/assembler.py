"""Context Assembler - deterministic context retrieval and assembly.

The Context Assembler gathers relevant context from multiple sources
(documents, vectors, graph) and assembles it into a ContextPacket.

v1.0.2: Refactored to use ContextPackResolver, RetrievalPlanner,
RetrievalExecutor, and RetrievalGateRunner for flexible retrieval.

v1.0.3: Added ThinkingConfig integration for tier-based retrieval.

v1.0.6: Added ContextGraphQueryService integration for knowledge
and episodic memory retrieval from the context graph.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.context.executor import RetrievalExecutor
from agent_kernel.context.gates import RetrievalGateRunner
from agent_kernel.context.pack_resolver import ContextPackResolver
from agent_kernel.context.planner import BaselineRetrievalPlanner, RetrievalPlanner
from agent_kernel.context.source_registry import SourceRegistry
from agent_kernel.core.ids import generate_ulid
from agent_kernel.core.schemas import (
    AgentProfile,
    ContextBudget,
    ContextItem,
    ContextPacket,
    ContextPolicy,
    ContextRef,
    GraphSlice,
    QueryRecord,
    RefType,
    RetrievalLimits,
    RetrievalReport,
    SkillManifest,
)
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.schemas.context_pack import ContextPack, ContextPackScope
from agent_kernel.core.schemas.retrieval import RetrievalScope
from agent_kernel.core.schemas.thinking import RetrievalConfig
from agent_kernel.memory.document_store import DocumentStore
from agent_kernel.memory.graph_store import GraphStore
from agent_kernel.memory.vector_store import VectorStore
from agent_kernel.prompting.system_prompts import split_context_items

if TYPE_CHECKING:
    from agent_kernel.context_graph.query import ContextGraphQueryService
    from agent_kernel.memory.experience_store import ExperienceStore
    from agent_kernel.services.embedding import EmbeddingService
    from agent_kernel.services.index_state import IndexStateStore
    from agent_kernel.skills.store import SkillStore

logger = structlog.get_logger(__name__)


# Default config directories
DEFAULT_PACKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "configs" / "context_packs"
DEFAULT_SOURCES_DIR = Path(__file__).parent.parent.parent.parent.parent / "configs" / "sources"
DEFAULT_SKILL_MANIFEST_LIMIT = 5


class ContextAssembler:
    """Assembles context from multiple sources into a ContextPacket.

    The assembler is deterministic - given the same inputs and state,
    it produces the same output. This enables reproducibility.

    v1.0.2: Enhanced with context packs, retrieval planning, and gates.
    """

    def __init__(
        self,
        document_store: DocumentStore | None = None,
        vector_store: VectorStore | None = None,
        graph_store: GraphStore | None = None,
        embedding_service: EmbeddingService | None = None,
        # v1.0.2 components
        pack_resolver: ContextPackResolver | None = None,
        source_registry: SourceRegistry | None = None,
        planner: RetrievalPlanner | None = None,
        executor: RetrievalExecutor | None = None,
        gate_runner: RetrievalGateRunner | None = None,
        index_state_store: IndexStateStore | None = None,
        packs_config_dir: str | Path | None = None,
        sources_config_dir: str | Path | None = None,
        # v1.1.x skills
        skill_store: SkillStore | None = None,
        skills_dir: str | Path | None = None,
        # v1.0.6 context graph
        context_graph_query: ContextGraphQueryService | None = None,
        # v1.2 experience memory
        experience_store: ExperienceStore | None = None,
    ) -> None:
        """Initialize context assembler.

        Args:
            document_store: Optional document store for keyword search.
            vector_store: Optional vector store for semantic search.
            graph_store: Optional graph store for relationship queries.
            embedding_service: Optional embedding service for auto-embedding.
            pack_resolver: Optional custom pack resolver (v1.0.2).
            source_registry: Optional custom source registry (v1.0.2).
            planner: Optional custom retrieval planner (v1.0.2).
            executor: Optional custom retrieval executor (v1.0.2).
            gate_runner: Optional custom gate runner (v1.0.2).
            index_state_store: Optional index state store for parity checks.
            packs_config_dir: Directory for context pack configs.
            sources_config_dir: Directory for source descriptor configs.
        """
        self._document_store = document_store
        self._vector_store = vector_store
        self._graph_store = graph_store
        self._embedding_service = embedding_service
        self._index_state_store = index_state_store
        self._skill_store = skill_store
        self._context_graph_query = context_graph_query
        self._experience_store = experience_store

        # Initialize v1.0.2 components
        packs_dir = Path(packs_config_dir) if packs_config_dir else DEFAULT_PACKS_DIR
        sources_dir = Path(sources_config_dir) if sources_config_dir else DEFAULT_SOURCES_DIR

        self._pack_resolver = pack_resolver or ContextPackResolver(
            config_dir=packs_dir if packs_dir.exists() else None
        )

        self._source_registry = source_registry or SourceRegistry(
            config_dir=sources_dir if sources_dir.exists() else None
        )

        self._planner = planner or BaselineRetrievalPlanner(
            source_registry=self._source_registry
        )

        self._executor = executor or RetrievalExecutor(
            source_registry=self._source_registry,
            document_store=document_store,
            vector_store=vector_store,
            graph_store=graph_store,
            embedding_service=embedding_service,
        )

        self._gate_runner = gate_runner or RetrievalGateRunner(
            source_registry=self._source_registry,
            index_state_store=index_state_store,
        )

        if self._skill_store is None and skills_dir is not None:
            from agent_kernel.skills.store import SkillStoreLocalFS

            skills_path = Path(skills_dir).expanduser()
            if skills_path.exists():
                self._skill_store = SkillStoreLocalFS(skills_path)

        logger.info(
            "context_assembler_initialized",
            has_documents=document_store is not None,
            has_vectors=vector_store is not None,
            has_graph=graph_store is not None,
            has_embeddings=embedding_service is not None,
            packs_loaded=len(self._pack_resolver.list_packs()),
            sources_loaded=len(self._source_registry.list_sources()),
            skills_enabled=self._skill_store is not None,
            context_graph_enabled=self._context_graph_query is not None,
            experience_enabled=self._experience_store is not None,
        )

    def set_embedding_service(self, service: EmbeddingService) -> None:
        """Set the embedding service.

        Args:
            service: The embedding service to use.
        """
        self._embedding_service = service
        logger.info("embedding_service_set")

    async def assemble_async(
        self,
        intent: str,
        policy: ContextPolicy,
        project_id: str | None = None,
        seed_node_ids: list[str] | None = None,
        embedding: list[float] | None = None,
        # v1.0.2 scope parameters
        vault_id: str | None = None,
        workflow_id: str | None = None,
        agent_profile_id: str | None = None,
        path: str | None = None,
    ) -> ContextPacket:
        """Assemble a context packet asynchronously.

        v1.0.2: Uses context packs, retrieval planning, and gates.

        Args:
            intent: The user's intent or query.
            policy: Context retrieval policy (from AgentProfile).
            project_id: Optional project scope.
            seed_node_ids: Optional graph seed nodes.
            embedding: Optional pre-computed query embedding.
            vault_id: Optional vault ID for pack resolution.
            workflow_id: Optional workflow ID for pack resolution.
            agent_profile_id: Optional agent profile ID for pack resolution.
            path: Optional path for pack resolution.

        Returns:
            Assembled ContextPacket.
        """
        start_time = time.time()

        # Step 1: Resolve context packs
        pack_scope = ContextPackScope(
            vault_id=vault_id,
            project_id=project_id,
            workflow_id=workflow_id,
            agent_profile_id=agent_profile_id,
            path=path,
        )
        packs = self._pack_resolver.resolve(pack_scope)
        pack_refs = self._pack_resolver.get_all_refs(packs)

        # Step 2: Create retrieval plan
        retrieval_scope = RetrievalScope(
            intent=intent,
            vault_id=vault_id,
            project_id=project_id,
            workflow_id=workflow_id,
            agent_profile_id=agent_profile_id,
            path=path,
        )
        plan = await self._planner.plan(retrieval_scope, packs, policy)

        # Step 3: Execute retrieval plan
        execution_result = await self._executor.execute(plan)
        items = execution_result.all_items
        query_records = execution_result.all_query_records

        # Step 4: Add pack refs as high-priority items
        pack_items = self._create_pack_items(pack_refs)
        skill_items = await self._create_skill_items_async(intent)
        items = pack_items + skill_items + items

        # Step 5: Deduplicate by ref_id
        items = self._deduplicate_items(items)

        # Step 6: Rank by relevance (with pack boost)
        items = self._rank_items(items, packs)

        # Step 7: Run coverage gates
        quality_report = self._gate_runner.run(items, packs, plan)

        # Step 8: Apply budget limits (evidence items only)
        prompt_items, evidence_items = split_context_items(items)
        budget = ContextBudget(
            max_tokens=policy.max_tokens,
            max_items=policy.max_notes + policy.max_tasks + policy.max_events,
            retrieval_limits=RetrievalLimits(
                max_notes=policy.max_notes,
                max_tasks=policy.max_tasks,
                max_events=policy.max_events,
            ),
        )
        evidence_items = evidence_items[:budget.max_items]

        # Trim by token budget
        total_tokens = self._estimate_tokens(evidence_items)
        while total_tokens > budget.max_tokens and evidence_items:
            evidence_items.pop()
            total_tokens = self._estimate_tokens(evidence_items)

        items = prompt_items + evidence_items

        # Step 9: Build graph slice (if needed)
        graph_slice = None
        if self._graph_store and seed_node_ids:
            graph_slice, graph_query = self._get_graph_context(
                seed_node_ids,
                depth=2,
            )
            query_records.append(graph_query)

        # Build retrieval report
        retrieval_time_ms = int((time.time() - start_time) * 1000)
        retrieval_report = RetrievalReport(
            queries_run=query_records,
            filters_applied=[],
            items_considered=execution_result.total_items + len(pack_items) + len(skill_items),
            items_selected=len(items),
            selection_strategy="relevance_ranked",
            retrieval_plan_id=plan.retrieval_plan_id,
            retrieval_plan=plan,
            quality=quality_report,
        )

        # Build packet
        packet = ContextPacket(
            packet_id=generate_ulid(),
            intent=intent,
            project_id=project_id,
            generated_at=utc_now(),
            budget=budget,
            items=items,
            graph_slice=graph_slice,
            retrieval_report=retrieval_report,
            retrieval_mode=plan.mode,
            context_packs=[p.pack_id for p in packs],
        )

        logger.info(
            "context_assembled",
            packet_id=packet.packet_id,
            items_count=len(items),
            total_tokens=total_tokens,
            retrieval_time_ms=retrieval_time_ms,
            packs_used=len(packs),
            gates_passed=quality_report.all_gates_passed,
        )

        return packet

    def assemble(
        self,
        intent: str,
        policy: ContextPolicy,
        project_id: str | None = None,
        seed_node_ids: list[str] | None = None,
        embedding: list[float] | None = None,
    ) -> ContextPacket:
        """Assemble a context packet for an intent (sync version).

        This is the legacy synchronous interface. For v1.0.2 features,
        use assemble_async() instead.

        Args:
            intent: The user's intent or query.
            policy: Context retrieval policy (from AgentProfile).
            project_id: Optional project scope.
            seed_node_ids: Optional graph seed nodes.
            embedding: Optional pre-computed query embedding.

        Returns:
            Assembled ContextPacket.
        """
        start_time = time.time()
        queries_run: list[QueryRecord] = []
        items: list[ContextItem] = []
        filters_applied: list[str] = []

        # Build budget from policy
        budget = ContextBudget(
            max_tokens=policy.max_tokens,
            max_items=policy.max_notes + policy.max_tasks + policy.max_events,
            retrieval_limits=RetrievalLimits(
                max_notes=policy.max_notes,
                max_tasks=policy.max_tasks,
                max_events=policy.max_events,
            ),
        )

        # Apply scope filter
        if policy.allowed_scopes and project_id:
            if project_id not in policy.allowed_scopes:
                msg = f"project_id restricted to: {policy.allowed_scopes}"
                filters_applied.append(msg)
                project_id = None  # Don't filter if not in allowed scopes

        if project_id:
            filters_applied.append(f"project_id={project_id}")

        # 1. Document search (keyword/FTS)
        if self._document_store:
            doc_items, doc_query = self._search_documents(
                intent,
                limit=policy.max_notes,
                project_id=project_id,
            )
            items.extend(doc_items)
            queries_run.append(doc_query)

        # Skill manifests (optional)
        skill_items = self._create_skill_items(self._search_skills_sync(intent))
        items.extend(skill_items)

        # 2. Vector search (semantic)
        if self._vector_store and embedding:
            vector_items, vector_query = self._search_vectors(
                embedding,
                limit=policy.max_notes,
                project_id=project_id,
            )
            items.extend(vector_items)
            queries_run.append(vector_query)

        # 3. Graph traversal
        graph_slice = None
        if self._graph_store and seed_node_ids:
            graph_slice, graph_query = self._get_graph_context(
                seed_node_ids,
                depth=2,
            )
            queries_run.append(graph_query)

        # Deduplicate by ref_id
        items = self._deduplicate_items(items)

        # Rank by relevance
        items = self._rank_items(items, [])

        # Apply budget limits
        items = items[:budget.max_items]

        # Estimate tokens and trim if needed
        total_tokens = self._estimate_tokens(items)
        while total_tokens > budget.max_tokens and items:
            items.pop()
            total_tokens = self._estimate_tokens(items)

        # Build retrieval report
        retrieval_time_ms = int((time.time() - start_time) * 1000)
        retrieval_report = RetrievalReport(
            queries_run=queries_run,
            filters_applied=filters_applied,
            items_considered=len(items),
            items_selected=len(items),
            selection_strategy="relevance_ranked",
        )

        packet = ContextPacket(
            packet_id=generate_ulid(),
            intent=intent,
            project_id=project_id,
            generated_at=utc_now(),
            budget=budget,
            items=items,
            graph_slice=graph_slice,
            retrieval_report=retrieval_report,
        )

        logger.info(
            "context_assembled",
            packet_id=packet.packet_id,
            items_count=len(items),
            total_tokens=total_tokens,
            retrieval_time_ms=retrieval_time_ms,
        )

        return packet

    def _create_pack_items(
        self,
        refs: list[ContextRef],
    ) -> list[ContextItem]:
        """Create ContextItems from pack refs."""
        items = []
        for ref in refs:
            item = ContextItem(
                ref=ref,
                excerpt=ref.metadata.get("description", ""),
                summary=ref.metadata.get("title"),
                relevance_score=1.0,  # Highest priority for pack items
                included_reason="context_pack",
            )
            items.append(item)
        return items

    async def _create_skill_items_async(self, intent: str) -> list[ContextItem]:
        if not self._skill_store:
            return []
        try:
            manifests = await self._skill_store.search(
                intent,
                top_k=DEFAULT_SKILL_MANIFEST_LIMIT,
            )
        except Exception as exc:
            logger.warning("skill_search_failed", error=str(exc))
            return []
        return self._create_skill_items(manifests)

    def _search_skills_sync(self, intent: str) -> list[SkillManifest]:
        if not self._skill_store:
            return []
        try:
            return self._skill_store.search_sync(
                intent,
                top_k=DEFAULT_SKILL_MANIFEST_LIMIT,
            )
        except Exception as exc:
            logger.warning("skill_search_failed", error=str(exc))
            return []

    def _create_skill_items(self, manifests: list[SkillManifest]) -> list[ContextItem]:
        items: list[ContextItem] = []
        for idx, manifest in enumerate(manifests):
            metadata = {
                "title": manifest.name,
                "name": manifest.name,
                "description": manifest.description,
                "skill_id": manifest.skill_id,
                "allowed_tools": manifest.allowed_tools or [],
                "origin_kind": manifest.origin.kind,
                "origin_path": manifest.origin.path or "",
                **manifest.metadata,
            }
            ref = ContextRef(
                ref_type=RefType.SKILL,
                ref_id=manifest.skill_id,
                uri=manifest.origin.path,
                hash=manifest.origin.content_hash,
                metadata=metadata,
            )
            item = ContextItem(
                ref=ref,
                excerpt=manifest.description or "",
                summary=manifest.name,
                relevance_score=max(0.2, 0.6 - (idx * 0.05)),
                included_reason="skill_manifest",
            )
            items.append(item)
        return items

    def _deduplicate_items(
        self,
        items: list[ContextItem],
    ) -> list[ContextItem]:
        """Deduplicate items by ref_id."""
        seen_refs: set[str] = set()
        unique_items: list[ContextItem] = []
        for item in items:
            if item.ref.ref_id not in seen_refs:
                seen_refs.add(item.ref.ref_id)
                unique_items.append(item)
        return unique_items

    def _rank_items(
        self,
        items: list[ContextItem],
        packs: list[ContextPack],
    ) -> list[ContextItem]:
        """Rank items by relevance score with pack boost.

        Items from context packs get priority boost.
        """
        # Get pack ref_ids for boosting
        pack_ref_ids = set()
        for pack in packs:
            for ref in pack.refs:
                pack_ref_ids.add(ref.ref_id)

        # Apply pack boost
        for item in items:
            if item.ref.ref_id in pack_ref_ids:
                item.relevance_score = max(item.relevance_score, 1.0)

        return sorted(items, key=lambda x: x.relevance_score, reverse=True)

    def _search_documents(
        self,
        query: str,
        limit: int,
        project_id: str | None,
    ) -> tuple[list[ContextItem], QueryRecord]:
        """Search documents using keyword/FTS."""
        start = time.time()

        filters = {}
        if project_id:
            filters["project_id"] = project_id

        results = self._document_store.search(
            query,
            limit=limit,
            filters=filters if filters else None,
        )

        items = []
        for doc in results:
            doc_metadata = doc.get("metadata", {})
            ref = ContextRef(
                ref_type=RefType.DOCUMENT,
                ref_id=doc["doc_id"],
                hash=self._content_hash(doc.get("content", "")),
                metadata=doc_metadata,
            )
            base_score = abs(doc.get("rank", 0))
            importance = float(doc_metadata.get("auto_importance", 0.0))
            item = ContextItem(
                ref=ref,
                excerpt=doc.get("content", "")[:500],
                relevance_score=base_score * (1.0 + importance),
                included_reason="keyword_search",
            )
            items.append(item)

        duration_ms = int((time.time() - start) * 1000)
        query_record = QueryRecord(
            source="document",
            query=query,
            results_count=len(results),
            duration_ms=duration_ms,
        )

        return items, query_record

    def _search_vectors(
        self,
        embedding: list[float],
        limit: int,
        project_id: str | None,
    ) -> tuple[list[ContextItem], QueryRecord]:
        """Search vectors for semantic similarity."""
        start = time.time()

        filters = {}
        if project_id:
            filters["project_id"] = project_id

        results = self._vector_store.query(
            embedding,
            top_k=limit,
            filters=filters if filters else None,
        )

        items = []
        for result in results:
            metadata = result.get("metadata", {})
            ref = ContextRef(
                ref_type=RefType(metadata.get("ref_type", "doc")),
                ref_id=result["item_id"],
                metadata=metadata,
            )
            base_score = result.get("score", 0)
            importance = float(metadata.get("auto_importance", 0.0))
            item = ContextItem(
                ref=ref,
                excerpt=metadata.get("excerpt", ""),
                relevance_score=base_score * (1.0 + importance),
                included_reason="semantic_search",
            )
            items.append(item)

        duration_ms = int((time.time() - start) * 1000)
        query_record = QueryRecord(
            source="vector",
            query=f"embedding[{len(embedding)}d]",
            results_count=len(results),
            duration_ms=duration_ms,
        )

        return items, query_record

    def _get_graph_context(
        self,
        seed_ids: list[str],
        depth: int,
    ) -> tuple[GraphSlice, QueryRecord]:
        """Get subgraph around seed nodes."""
        start = time.time()

        subgraph = self._graph_store.get_subgraph(seed_ids, depth=depth)

        graph_slice = GraphSlice(
            nodes=subgraph.get("nodes", []),
            edges=subgraph.get("edges", []),
        )

        duration_ms = int((time.time() - start) * 1000)
        query_record = QueryRecord(
            source="graph",
            query=f"subgraph(seeds={seed_ids}, depth={depth})",
            results_count=len(subgraph.get("nodes", [])),
            duration_ms=duration_ms,
        )

        return graph_slice, query_record

    def _estimate_tokens(self, items: list[ContextItem]) -> int:
        """Estimate token count for items.

        Uses a simple heuristic of ~4 chars per token.
        """
        total_chars = sum(len(item.excerpt) for item in items)
        return total_chars // 4

    def _content_hash(self, content: str) -> str:
        """Generate a hash for content."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    # v1.0.2 accessors
    @property
    def pack_resolver(self) -> ContextPackResolver:
        """Get the pack resolver."""
        return self._pack_resolver

    @property
    def source_registry(self) -> SourceRegistry:
        """Get the source registry."""
        return self._source_registry

    # v1.0.3: ThinkingConfig integration
    async def assemble_with_thinking(
        self,
        intent: str,
        agent_profile: AgentProfile,
        retrieval_config: dict[str, Any] | RetrievalConfig | None = None,
        max_context_tokens: int | None = None,
        project_id: str | None = None,
        seed_node_ids: list[str] | None = None,
        embedding: list[float] | None = None,
        vault_id: str | None = None,
        workflow_id: str | None = None,
        path: str | None = None,
    ) -> ContextPacket:
        """Assemble context using thinking-config-aware retrieval.

        This method respects the retrieval configuration from ThinkingConfig,
        enabling features like graph expansion based on thinking tier.

        Args:
            intent: The user's intent or query.
            agent_profile: Agent profile (may contain thinking_config).
            retrieval_config: Optional override for retrieval settings.
            max_context_tokens: Optional token limit override.
            project_id: Optional project scope.
            seed_node_ids: Optional graph seed nodes.
            embedding: Optional pre-computed query embedding.
            vault_id: Optional vault ID for pack resolution.
            workflow_id: Optional workflow ID for pack resolution.
            path: Optional path for pack resolution.

        Returns:
            Assembled ContextPacket.
        """
        start_time = time.time()
        queries_run: list[QueryRecord] = []
        items: list[ContextItem] = []

        # Extract retrieval config from thinking config or use override
        if retrieval_config is None and agent_profile.thinking_config:
            retrieval_config = agent_profile.thinking_config.retrieval

        # Normalize to dict for easy access
        if isinstance(retrieval_config, RetrievalConfig):
            retrieval_opts = {
                "semantic_search": retrieval_config.semantic_search,
                "keyword_search": retrieval_config.keyword_search,
                "graph_expansion": retrieval_config.graph_expansion,
                "graph_expansion_hops": retrieval_config.graph_expansion_hops,
                "recency_boost": retrieval_config.recency_boost,
                "recency_days": retrieval_config.recency_days,
            }
        elif isinstance(retrieval_config, dict):
            retrieval_opts = retrieval_config
        else:
            retrieval_opts = {
                "semantic_search": True,
                "keyword_search": True,
                "graph_expansion": False,
                "graph_expansion_hops": 1,
                "recency_boost": True,
                "recency_days": 7,
            }

        # Use context policy from agent profile
        policy = agent_profile.context_policy

        # Override token limit if specified
        if max_context_tokens:
            policy = ContextPolicy(
                max_tokens=max_context_tokens,
                max_notes=policy.max_notes,
                max_tasks=policy.max_tasks,
                max_events=policy.max_events,
                allowed_scopes=policy.allowed_scopes,
            )

        # Step 1: Resolve context packs
        pack_scope = ContextPackScope(
            vault_id=vault_id,
            project_id=project_id,
            workflow_id=workflow_id,
            agent_profile_id=agent_profile.agent_profile_id,
            path=path,
        )
        packs = self._pack_resolver.resolve(pack_scope)
        pack_refs = self._pack_resolver.get_all_refs(packs)
        pack_items = self._create_pack_items(pack_refs)
        items.extend(pack_items)

        skill_items = await self._create_skill_items_async(intent)
        items.extend(skill_items)

        # Step 2: Keyword search (if enabled)
        if retrieval_opts.get("keyword_search") and self._document_store:
            doc_items, doc_query = self._search_documents(
                intent,
                limit=policy.max_notes,
                project_id=project_id,
            )
            items.extend(doc_items)
            queries_run.append(doc_query)

        # Step 3: Semantic search (if enabled and embedding available)
        if retrieval_opts.get("semantic_search") and self._vector_store:
            # Generate embedding if not provided
            query_embedding = embedding
            if query_embedding is None and self._embedding_service:
                query_embedding = await self._embedding_service.embed(intent)

            if query_embedding:
                vector_items, vector_query = self._search_vectors(
                    query_embedding,
                    limit=policy.max_notes,
                    project_id=project_id,
                )
                items.extend(vector_items)
                queries_run.append(vector_query)

        # Step 4: Graph expansion (if enabled)
        graph_slice = None
        if retrieval_opts.get("graph_expansion") and self._graph_store:
            # Use seed nodes or extract from retrieved items
            expand_seeds = seed_node_ids or []

            # Extract node IDs from retrieved items
            for item in items:
                if item.ref.ref_type in (RefType.NOTE, RefType.DOCUMENT):
                    expand_seeds.append(item.ref.ref_id)

            if expand_seeds:
                hops = retrieval_opts.get("graph_expansion_hops", 1)
                graph_slice, graph_query = self._get_graph_context(
                    expand_seeds[:10],  # Limit seeds
                    depth=hops,
                )
                queries_run.append(graph_query)

                # Add related nodes as items
                graph_items = self._graph_slice_to_items(graph_slice)
                items.extend(graph_items)

        # Step 4.5: Context graph search (v1.0.6)
        if self._context_graph_query:
            cg_items, cg_query = await self._search_context_graph(
                intent, limit=policy.max_notes,
            )
            items.extend(cg_items)
            queries_run.append(cg_query)

        # Step 4.6: Experience retrieval (v1.2)
        if self._experience_store:
            exp_items, exp_query = self._search_experience(
                intent=intent,
                workflow_id=workflow_id,
                limit=5,
            )
            items.extend(exp_items)
            queries_run.append(exp_query)

        # Step 5: Deduplicate and rank
        items = self._deduplicate_items(items)
        items = self._rank_items(items, packs)

        # Step 6: Apply budget limits (evidence items only)
        prompt_items, evidence_items = split_context_items(items)
        budget = ContextBudget(
            max_tokens=policy.max_tokens,
            max_items=policy.max_notes + policy.max_tasks + policy.max_events,
            retrieval_limits=RetrievalLimits(
                max_notes=policy.max_notes,
                max_tasks=policy.max_tasks,
                max_events=policy.max_events,
            ),
        )
        evidence_items = evidence_items[:budget.max_items]

        # Trim by token budget
        total_tokens = self._estimate_tokens(evidence_items)
        while total_tokens > budget.max_tokens and evidence_items:
            evidence_items.pop()
            total_tokens = self._estimate_tokens(evidence_items)

        items = prompt_items + evidence_items

        # Build retrieval report
        retrieval_time_ms = int((time.time() - start_time) * 1000)
        retrieval_report = RetrievalReport(
            queries_run=queries_run,
            filters_applied=[f"retrieval_opts={list(retrieval_opts.keys())}"],
            items_considered=len(items),
            items_selected=len(items),
            selection_strategy="thinking_config_aware",
        )

        # Build packet
        packet = ContextPacket(
            packet_id=generate_ulid(),
            intent=intent,
            project_id=project_id,
            generated_at=utc_now(),
            budget=budget,
            items=items,
            graph_slice=graph_slice,
            retrieval_report=retrieval_report,
            retrieval_mode="thinking_config",
            context_packs=[p.pack_id for p in packs],
        )

        logger.info(
            "context_assembled_with_thinking",
            packet_id=packet.packet_id,
            items_count=len(items),
            total_tokens=total_tokens,
            retrieval_time_ms=retrieval_time_ms,
            semantic_search=retrieval_opts.get("semantic_search"),
            keyword_search=retrieval_opts.get("keyword_search"),
            graph_expansion=retrieval_opts.get("graph_expansion"),
        )

        return packet

    async def _search_context_graph(
        self,
        intent: str,
        limit: int = 20,
    ) -> tuple[list[ContextItem], QueryRecord]:
        """Search the context graph for relevant knowledge and episodic memory.

        Returns items from two sources:
        1. Semantic memory: relevant knowledge nodes (concepts, insights, systems)
        2. Episodic memory: similar past trajectories

        v1.0.6 addition.
        """
        start = time.time()
        items: list[ContextItem] = []

        if not self._context_graph_query:
            return items, QueryRecord(
                source="context_graph",
                query=intent,
                results_count=0,
                duration_ms=0,
            )

        # 1. Semantic memory: relevant knowledge nodes
        knowledge_nodes = await self._context_graph_query.find_relevant_knowledge(
            intent=intent,
            limit=limit // 2,
        )

        for node in knowledge_nodes:
            ref = ContextRef(
                ref_type=RefType.KNOWLEDGE,
                ref_id=node.node_id,
                metadata={
                    "title": node.properties.get("title", ""),
                    "node_type": node.node_type,
                },
            )
            item = ContextItem(
                ref=ref,
                excerpt=self._format_knowledge_excerpt(node.properties),
                summary=node.properties.get("title"),
                relevance_score=node.relevance_score * 0.8,  # Slightly below direct search
                included_reason="context_graph_knowledge",
            )
            items.append(item)

            # Record access for freshness tracking
            await self._context_graph_query.record_access(node.node_id)

        # 2. Episodic memory: similar past trajectories
        trajectories = await self._context_graph_query.find_similar_trajectories(
            intent=intent,
            limit=limit // 4,
        )

        for traj in trajectories:
            ref = ContextRef(
                ref_type=RefType.TRAJECTORY,
                ref_id=traj.node_id,
                metadata={
                    "intent": traj.properties.get("intent", ""),
                    "outcome_status": traj.properties.get("outcome_status", ""),
                },
            )
            item = ContextItem(
                ref=ref,
                excerpt=self._format_trajectory_excerpt(traj.properties),
                summary=traj.properties.get("intent"),
                relevance_score=traj.relevance_score * 0.7,  # Lower than knowledge
                included_reason="context_graph_episodic",
            )
            items.append(item)

        duration_ms = int((time.time() - start) * 1000)
        query_record = QueryRecord(
            source="context_graph",
            query=intent,
            results_count=len(items),
            duration_ms=duration_ms,
        )

        return items, query_record

    @staticmethod
    def _format_knowledge_excerpt(props: dict[str, Any]) -> str:
        """Format a knowledge node's properties as a readable excerpt."""
        title = props.get("title", "")
        desc = props.get("description", "")
        node_type = props.get("node_type", "")

        parts = []
        if title:
            parts.append(title)
        if desc:
            parts.append(desc[:300])
        return " | ".join(parts) if parts else f"[{node_type}]"

    @staticmethod
    def _format_trajectory_excerpt(props: dict[str, Any]) -> str:
        """Format a trajectory node's properties as a readable excerpt."""
        intent = props.get("intent", "")
        outcome = props.get("outcome_summary", "")
        status = props.get("outcome_status", "")
        caps = props.get("capabilities_used", [])

        parts = [f"Past: {intent}"]
        if outcome:
            parts.append(f"Outcome ({status}): {outcome}")
        if caps:
            parts.append(f"Used: {', '.join(caps[:3])}")
        return " | ".join(parts)

    def _search_experience(
        self,
        intent: str,
        workflow_id: str | None = None,
        limit: int = 5,
    ) -> tuple[list[ContextItem], QueryRecord]:
        """Search experience store for relevant cases and lessons.

        Returns items from two sources:
        1. Similar cases from past workflow runs
        2. Active lessons scoped to the current workflow

        v1.2 addition.
        """
        import time as _time

        start = _time.time()
        items: list[ContextItem] = []

        if not self._experience_store:
            return items, QueryRecord(
                source="experience",
                query=intent,
                results_count=0,
                duration_ms=0,
            )

        # 1. Similar cases (limit to half the budget)
        case_limit = max(1, limit // 2)
        try:
            cases = self._experience_store.find_similar_cases(
                workflow_id=workflow_id,
                limit=case_limit,
            )
        except Exception as exc:
            logger.warning("experience_case_search_failed", error=str(exc))
            cases = []

        for idx, case in enumerate(cases):
            ref = ContextRef(
                ref_type=RefType.CASE,
                ref_id=case.case_id,
                metadata={
                    "intent": case.intent,
                    "workflow_id": case.workflow_id or "",
                    "label": case.label.value if hasattr(case.label, "value") else str(case.label),
                },
            )
            item = ContextItem(
                ref=ref,
                excerpt=self._format_case_excerpt(case),
                summary=case.intent,
                relevance_score=max(0.1, 0.5 - (idx * 0.05)),
                included_reason="experience_case",
            )
            items.append(item)

        # 2. Active lessons for this workflow
        lesson_limit = limit - len(items)
        if lesson_limit > 0:
            try:
                from agent_kernel.core.schemas.experience import LessonScope

                scope = LessonScope(workflow_id=workflow_id) if workflow_id else None
                lessons = self._experience_store.list_lessons(
                    scope=scope,
                    status="active",
                    limit=lesson_limit,
                )
            except Exception as exc:
                logger.warning("experience_lesson_search_failed", error=str(exc))
                lessons = []

            for idx, lesson in enumerate(lessons):
                ref = ContextRef(
                    ref_type=RefType.LESSON,
                    ref_id=lesson.lesson_id,
                    metadata={
                        "title": lesson.title,
                        "confidence": lesson.confidence,
                    },
                )
                item = ContextItem(
                    ref=ref,
                    excerpt=f"{lesson.title}: {lesson.lesson_text[:300]}",
                    summary=lesson.title,
                    relevance_score=max(0.1, 0.5 - (idx * 0.05)),
                    included_reason="experience_lesson",
                )
                items.append(item)

        duration_ms = int((_time.time() - start) * 1000)
        query_record = QueryRecord(
            source="experience",
            query=intent,
            results_count=len(items),
            duration_ms=duration_ms,
        )

        return items, query_record

    @staticmethod
    def _format_case_excerpt(case: Any) -> str:
        """Format an experience case as a readable excerpt."""
        parts = [f"Past: {case.intent}"]
        if case.outcome_summary:
            parts.append(f"Outcome: {case.outcome_summary}")
        label = case.label.value if hasattr(case.label, "value") else str(case.label)
        parts.append(f"Result: {label}")
        if case.capability_names:
            parts.append(f"Used: {', '.join(case.capability_names[:3])}")
        return " | ".join(parts)

    def _graph_slice_to_items(
        self,
        graph_slice: GraphSlice,
    ) -> list[ContextItem]:
        """Convert graph nodes to context items."""
        items = []
        for node in graph_slice.nodes:
            # Only include certain node types
            node_type = node.get("type", "")
            if node_type in ("note", "task", "project"):
                ref = ContextRef(
                    ref_type=RefType.NOTE if node_type == "note" else RefType.TASK,
                    ref_id=node.get("id", ""),
                    metadata=node.get("properties", {}),
                )
                item = ContextItem(
                    ref=ref,
                    excerpt=node.get("properties", {}).get("title", "")[:200],
                    relevance_score=0.5,  # Lower than direct search results
                    included_reason="graph_expansion",
                )
                items.append(item)
        return items
