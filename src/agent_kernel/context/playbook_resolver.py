"""Playbook Resolver - matches playbooks to context (v1.0.4).

The resolver finds applicable playbooks based on:
- Workflow ID
- Project ID
- Intent keywords
- Capabilities being used

Playbooks provide behavioral guidance that is injected into context:
- Required entity types and sources
- Output format templates
- Checklists and pitfalls
- Recommended thinking tier

References:
- Design Patch v1.0.4: Universal Context System
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import yaml

from agent_kernel.core.schemas.experience import Playbook, PlaybookSelector
from agent_kernel.core.schemas.base import utc_now
from agent_kernel.core.ids import generate_ulid

if TYPE_CHECKING:
    from agent_kernel.memory.experience_store import ExperienceStore

logger = structlog.get_logger(__name__)


@dataclass
class PlaybookMatch:
    """A matched playbook with match score."""

    playbook: Playbook
    score: float = 0.0
    matched_by: str = ""  # "workflow", "project", "intent", "capability"


@dataclass
class PlaybookResolutionResult:
    """Result of playbook resolution."""

    matches: list[PlaybookMatch] = field(default_factory=list)
    primary_playbook: Playbook | None = None
    recommended_thinking_tier: int | None = None
    required_entity_types: list[str] = field(default_factory=list)
    required_sources: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    pitfalls: list[str] = field(default_factory=list)


class PlaybookResolver:
    """Resolves applicable playbooks for a given context."""

    def __init__(
        self,
        experience_store: ExperienceStore | None = None,
        playbook_dir: Path | str | None = None,
    ) -> None:
        """Initialize the playbook resolver.
        
        Args:
            experience_store: Store for dynamic playbooks
            playbook_dir: Directory for static playbook YAML files
        """
        self._experience_store = experience_store
        self._playbook_dir = Path(playbook_dir) if playbook_dir else None
        self._static_playbooks: list[Playbook] = []
        
        if self._playbook_dir:
            self._load_static_playbooks()

    def _load_static_playbooks(self) -> None:
        """Load playbooks from YAML files."""
        if not self._playbook_dir or not self._playbook_dir.exists():
            return

        for yaml_file in self._playbook_dir.glob("*.yaml"):
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                
                if not data:
                    continue

                # Convert YAML to Playbook
                playbook = self._parse_playbook_yaml(data, yaml_file.stem)
                self._static_playbooks.append(playbook)

                logger.debug(
                    "Loaded static playbook",
                    playbook_id=playbook.playbook_id,
                    name=playbook.name,
                )

            except Exception as e:
                logger.warning(
                    "Failed to load playbook",
                    file=str(yaml_file),
                    error=str(e),
                )

    def _parse_playbook_yaml(self, data: dict, default_id: str) -> Playbook:
        """Parse a playbook YAML into a Playbook schema."""
        now = utc_now()

        selectors = []
        for sel_data in data.get("selectors", []):
            selectors.append(PlaybookSelector(
                workflow_id=sel_data.get("workflow_id"),
                project_id=sel_data.get("project_id"),
                intent_contains=sel_data.get("intent_contains", []),
                capability_names=sel_data.get("capability_names", []),
            ))

        return Playbook(
            playbook_id=data.get("playbook_id", default_id),
            name=data.get("name", default_id),
            description=data.get("description"),
            version=data.get("version", "v1"),
            selectors=selectors,
            required_entity_types=data.get("required_entity_types", []),
            required_sources=data.get("required_sources", []),
            output_format_refs=[],  # Would need ContextRef parsing
            checklist=data.get("checklist", []),
            pitfalls=data.get("pitfalls", []),
            recommended_thinking_tier=data.get("recommended_thinking_tier"),
            derived_from_lessons=data.get("derived_from_lessons", []),
            status=data.get("status", "active"),
            created_at=now,
            updated_at=now,
        )

    def resolve(
        self,
        workflow_id: str | None = None,
        project_id: str | None = None,
        intent: str | None = None,
        capability_names: list[str] | None = None,
    ) -> PlaybookResolutionResult:
        """Resolve playbooks matching the given criteria.
        
        Args:
            workflow_id: Current workflow ID
            project_id: Current project ID
            intent: Intent/goal text for keyword matching
            capability_names: Capabilities being used
            
        Returns:
            Resolution result with matched playbooks and merged guidance
        """
        matches: list[PlaybookMatch] = []

        # Get playbooks from store
        store_playbooks: list[Playbook] = []
        if self._experience_store:
            store_playbooks = self._experience_store.find_playbooks(
                workflow_id=workflow_id,
                capability_names=capability_names,
                intent_keywords=intent.split() if intent else None,
            )

        # Combine with static playbooks
        all_playbooks = store_playbooks + self._static_playbooks

        # Score each playbook
        for playbook in all_playbooks:
            if playbook.status != "active":
                continue

            match_result = self._score_playbook(
                playbook,
                workflow_id=workflow_id,
                project_id=project_id,
                intent=intent,
                capability_names=capability_names,
            )

            if match_result.score > 0:
                matches.append(match_result)

        # Sort by score
        matches.sort(key=lambda m: m.score, reverse=True)

        # Build merged result
        result = PlaybookResolutionResult(matches=matches)

        if matches:
            result.primary_playbook = matches[0].playbook

            # Merge guidance from top playbooks
            seen_entity_types: set[str] = set()
            seen_sources: set[str] = set()
            seen_checklist: set[str] = set()
            seen_pitfalls: set[str] = set()

            for match in matches[:3]:  # Top 3 playbooks
                pb = match.playbook

                for et in pb.required_entity_types:
                    if et not in seen_entity_types:
                        result.required_entity_types.append(et)
                        seen_entity_types.add(et)

                for src in pb.required_sources:
                    if src not in seen_sources:
                        result.required_sources.append(src)
                        seen_sources.add(src)

                for item in pb.checklist:
                    if item not in seen_checklist:
                        result.checklist.append(item)
                        seen_checklist.add(item)

                for item in pb.pitfalls:
                    if item not in seen_pitfalls:
                        result.pitfalls.append(item)
                        seen_pitfalls.add(item)

                # Use highest recommended tier
                if pb.recommended_thinking_tier is not None:
                    if (
                        result.recommended_thinking_tier is None
                        or pb.recommended_thinking_tier > result.recommended_thinking_tier
                    ):
                        result.recommended_thinking_tier = pb.recommended_thinking_tier

        logger.info(
            "Resolved playbooks",
            match_count=len(matches),
            primary=result.primary_playbook.playbook_id if result.primary_playbook else None,
            recommended_tier=result.recommended_thinking_tier,
        )

        return result

    def _score_playbook(
        self,
        playbook: Playbook,
        workflow_id: str | None = None,
        project_id: str | None = None,
        intent: str | None = None,
        capability_names: list[str] | None = None,
    ) -> PlaybookMatch:
        """Score how well a playbook matches the criteria."""
        score = 0.0
        matched_by = ""

        for selector in playbook.selectors:
            selector_score = 0.0
            selector_match = ""

            # Workflow match (highest priority)
            if selector.workflow_id and workflow_id:
                if selector.workflow_id == workflow_id:
                    selector_score += 1.0
                    selector_match = "workflow"

            # Project match
            if selector.project_id and project_id:
                if selector.project_id == project_id:
                    selector_score += 0.8
                    selector_match = selector_match or "project"

            # Capability match
            if selector.capability_names and capability_names:
                caps_matched = sum(
                    1 for cap in capability_names
                    if cap in selector.capability_names
                )
                if caps_matched > 0:
                    selector_score += 0.6 * (caps_matched / len(selector.capability_names))
                    selector_match = selector_match or "capability"

            # Intent keyword match
            if selector.intent_contains and intent:
                intent_lower = intent.lower()
                keywords_matched = sum(
                    1 for kw in selector.intent_contains
                    if kw.lower() in intent_lower
                )
                if keywords_matched > 0:
                    selector_score += 0.4 * (keywords_matched / len(selector.intent_contains))
                    selector_match = selector_match or "intent"

            # Take best selector score
            if selector_score > score:
                score = selector_score
                matched_by = selector_match

        return PlaybookMatch(
            playbook=playbook,
            score=score,
            matched_by=matched_by,
        )


def create_playbook_resolver(
    experience_store: ExperienceStore | None = None,
    playbook_dir: Path | str | None = None,
) -> PlaybookResolver:
    """Create a playbook resolver instance.
    
    Args:
        experience_store: Store for dynamic playbooks
        playbook_dir: Directory for static playbook YAML files
        
    Returns:
        Configured PlaybookResolver
    """
    return PlaybookResolver(
        experience_store=experience_store,
        playbook_dir=playbook_dir,
    )
