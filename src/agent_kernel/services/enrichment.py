"""LLM Enrichment Service for auto.* fields (v1.0.5).

Implements the ENRICHMENT WORKER from the integration patterns:
- Auto tags/classification written to `auto.*` namespace
- Suggestions only, NOT destructive edits to human content
- Uses structured prompts with JSON output parsing

The enrichment worker adds machine-generated metadata that:
1. Lives in the `auto:` frontmatter namespace (separate from human tags)
2. Can always be regenerated from content
3. Requires no approval (safe auto-apply)

v1.0.5: Generalized to support any entity type with source-specific prompts.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from agent_kernel.core.schemas.enrichment_config import (
    DEFAULT_OBSIDIAN_CONFIG,
    SourceEnrichmentConfig,
)

if TYPE_CHECKING:
    from agent_kernel.core.schemas.entity import EntityRef
    from agent_kernel.services.enrichment_registry import EnrichmentConfigRegistry
    from agent_kernel.services.llm import LLMService

logger = structlog.get_logger(__name__)


# Default classification categories
DEFAULT_CLASSIFICATIONS = [
    "meeting",
    "architecture",
    "reference",
    "journal",
    "project",
    "brainstorm",
    "documentation",
    "task-list",
    "research",
    "notes",
]


@dataclass
class EnrichmentResult:
    """Result of LLM enrichment for a note.

    All fields are optional suggestions that can be written
    to the `auto:` frontmatter namespace.
    """

    # Auto-generated tags (separate from human tags)
    auto_tags: list[str] = field(default_factory=list)

    # Primary classification
    auto_class: str | None = None

    # Optional brief summary
    auto_summary: str | None = None

    # Importance score (0.0-1.0) — how foundational/critical the content is
    auto_importance: float = 0.0

    # Confidence scores (0.0-1.0)
    tag_confidence: float = 0.0
    class_confidence: float = 0.0

    # Raw LLM response for debugging
    raw_response: str | None = None

    # Whether enrichment was successful
    success: bool = True
    error: str | None = None

    def to_frontmatter(self) -> dict[str, Any]:
        """Convert to frontmatter `auto:` structure.

        Returns:
            Dictionary suitable for YAML frontmatter.
        """
        result: dict[str, Any] = {}

        if self.auto_tags:
            result["tags"] = self.auto_tags

        if self.auto_class:
            result["class"] = self.auto_class

        if self.auto_summary:
            result["summary"] = self.auto_summary

        if self.auto_importance > 0.0:
            result["importance"] = round(self.auto_importance, 2)

        return result

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging/storage."""
        return {
            "auto_tags": self.auto_tags,
            "auto_class": self.auto_class,
            "auto_summary": self.auto_summary,
            "auto_importance": self.auto_importance,
            "tag_confidence": self.tag_confidence,
            "class_confidence": self.class_confidence,
            "success": self.success,
            "error": self.error,
        }


# Prompt templates for enrichment

ENRICHMENT_SYSTEM_PROMPT_WITH_SUMMARY = """\
You are an expert at analyzing notes and documents to suggest metadata.

Your task is to analyze the content and suggest:
1. **Tags**: 2-5 relevant topic tags (lowercase, hyphenated)
2. **Classification**: A single category that best describes the note type
3. **Summary**: A concise 1-2 sentence summary that captures the main topic and purpose
4. **Importance**: A score from 0.0 to 1.0 indicating how foundational or critical this content is

Available classifications: {classifications}

IMPORTANT RULES:
- Tags should be general topics, not specific to the note content
- Use existing tag patterns if the note has human tags
- Classification must be from the provided list
- Summary MUST always be provided - it will be used for semantic search
- Summary should be semantic-rich: include key concepts, entities, and relationships
- Be conservative - only suggest high-confidence tags
- Importance reflects how foundational the content is (not recency):
  - 0.9-1.0: Core architecture decisions, key project specs, critical reference docs
  - 0.6-0.8: Active project notes, important meeting notes, design discussions
  - 0.3-0.5: General notes, routine updates, standard communications
  - 0.0-0.2: Ephemeral content, scratch notes, temporary items

Respond in JSON format:
{{
  "tags": ["tag1", "tag2"],
  "class": "classification",
  "summary": "Concise summary capturing the main topic, key concepts, and purpose of the note.",
  "importance": 0.5,
  "tag_confidence": 0.85,
  "class_confidence": 0.90
}}
"""

ENRICHMENT_SYSTEM_PROMPT_NO_SUMMARY = """\
You are an expert at analyzing notes and documents to suggest metadata.

Your task is to analyze the content and suggest:
1. **Tags**: 2-5 relevant topic tags (lowercase, hyphenated)
2. **Classification**: A single category that best describes the note type
3. **Importance**: A score from 0.0 to 1.0 indicating how foundational or critical this content is

Available classifications: {classifications}

IMPORTANT RULES:
- Tags should be general topics, not specific to the note content
- Use existing tag patterns if the note has human tags
- Classification must be from the provided list
- Be conservative - only suggest high-confidence tags
- Importance reflects how foundational the content is (not recency):
  - 0.9-1.0: Core architecture decisions, key project specs, critical reference docs
  - 0.6-0.8: Active project notes, important meeting notes, design discussions
  - 0.3-0.5: General notes, routine updates, standard communications
  - 0.0-0.2: Ephemeral content, scratch notes, temporary items

Respond in JSON format:
{{
  "tags": ["tag1", "tag2"],
  "class": "classification",
  "importance": 0.5,
  "tag_confidence": 0.85,
  "class_confidence": 0.90
}}
"""

# Alias for backwards compatibility
ENRICHMENT_SYSTEM_PROMPT = ENRICHMENT_SYSTEM_PROMPT_WITH_SUMMARY

ENRICHMENT_USER_PROMPT_WITH_SUMMARY = """Analyze this note and suggest metadata:

---
Title: {title}
{existing_tags_section}
---

{content}

---

Respond with JSON only, no markdown formatting. Summary is REQUIRED.
"""

ENRICHMENT_USER_PROMPT_NO_SUMMARY = """Analyze this note and suggest metadata:

---
Title: {title}
{existing_tags_section}
---

{content}

---

Respond with JSON only, no markdown formatting. Do NOT include a summary.
"""

# Alias for backwards compatibility
ENRICHMENT_USER_PROMPT = ENRICHMENT_USER_PROMPT_WITH_SUMMARY


class EnrichmentService:
    """Service for enriching notes with LLM-generated metadata.

    Uses an LLM to analyze note content and generate suggestions for:
    - auto.tags: Topic tags
    - auto.class: Note classification
    - auto.summary: Brief summary

    All output is written to the `auto:` namespace in frontmatter,
    keeping human-authored metadata separate and intact.
    """

    def __init__(
        self,
        llm_service: LLMService,
        classifications: list[str] | None = None,
        max_content_length: int = 4000,
        temperature: float = 0.3,
        model: str | None = None,
    ) -> None:
        """Initialize the enrichment service.

        Args:
            llm_service: LLM service for generating enrichments.
            classifications: Allowed classification categories.
            max_content_length: Max content chars to send to LLM.
            temperature: LLM temperature (lower = more consistent).
            model: Specific model to use (None = service default).
        """
        self.llm_service = llm_service
        self.classifications = classifications or DEFAULT_CLASSIFICATIONS
        self.max_content_length = max_content_length
        self.temperature = temperature
        self.model = model

        logger.info(
            "enrichment_service_initialized",
            classifications=self.classifications,
            max_content_length=max_content_length,
        )

    async def enrich(
        self,
        content: str,
        title: str = "",
        existing_tags: list[str] | None = None,
        include_summary: bool = True,
    ) -> EnrichmentResult:
        """Enrich a note with LLM-generated metadata.

        Args:
            content: Note content (markdown).
            title: Note title.
            existing_tags: Existing human-authored tags.
            include_summary: Whether to generate a summary (default True).

        Returns:
            EnrichmentResult with suggested metadata.
        """
        try:
            # Truncate content if too long
            truncated_content = content[: self.max_content_length]
            if len(content) > self.max_content_length:
                truncated_content += "\n\n[Content truncated...]"

            # Build prompts based on whether summary is requested
            if include_summary:
                system_prompt = ENRICHMENT_SYSTEM_PROMPT_WITH_SUMMARY.format(
                    classifications=", ".join(self.classifications)
                )
                user_template = ENRICHMENT_USER_PROMPT_WITH_SUMMARY
            else:
                system_prompt = ENRICHMENT_SYSTEM_PROMPT_NO_SUMMARY.format(
                    classifications=", ".join(self.classifications)
                )
                user_template = ENRICHMENT_USER_PROMPT_NO_SUMMARY

            existing_tags_section = ""
            if existing_tags:
                existing_tags_section = f"Existing tags: {', '.join(existing_tags)}"

            user_prompt = user_template.format(
                title=title or "Untitled",
                existing_tags_section=existing_tags_section,
                content=truncated_content,
            )

            # Call LLM
            response = await self.llm_service.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=self.temperature,
                model=self.model,
                max_tokens=500,  # JSON response should be small
            )

            # Parse JSON response
            result = self._parse_response(response)
            result.raw_response = response

            logger.debug(
                "note_enriched",
                title=title,
                auto_tags=result.auto_tags,
                auto_class=result.auto_class,
                include_summary=include_summary,
                has_summary=result.auto_summary is not None,
            )

        except Exception as e:
            logger.exception("enrichment_failed", title=title)
            return EnrichmentResult(
                success=False,
                error=str(e),
            )
        else:
            return result

    def _parse_response(self, response: str) -> EnrichmentResult:
        """Parse LLM JSON response into EnrichmentResult.

        Handles common formatting issues like markdown code blocks.

        Args:
            response: Raw LLM response text.

        Returns:
            Parsed EnrichmentResult.
        """
        # Strip markdown code blocks if present
        text = response.strip()

        # Remove ```json ... ``` wrapper
        if text.startswith("```"):
            # Find end of first line (after ```json or ```)
            first_newline = text.find("\n")
            if first_newline > 0:
                text = text[first_newline + 1 :]
            # Remove trailing ```
            text = text.removesuffix("```").strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            # Try to extract JSON from response
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                except json.JSONDecodeError:
                    return EnrichmentResult(
                        success=False,
                        error=f"Invalid JSON response: {e}",
                        raw_response=response,
                    )
            else:
                return EnrichmentResult(
                    success=False,
                    error=f"No JSON found in response: {e}",
                    raw_response=response,
                )

        # Validate and extract fields
        auto_tags = data.get("tags", [])
        if not isinstance(auto_tags, list):
            auto_tags = []
        # Normalize tags: lowercase, hyphenated
        auto_tags = [self._normalize_tag(t) for t in auto_tags if isinstance(t, str)]

        auto_class = data.get("class")
        if auto_class and auto_class not in self.classifications:
            # Use closest match or None
            auto_class = None

        auto_summary = data.get("summary")
        if auto_summary in {"null", ""}:
            auto_summary = None

        tag_confidence = float(data.get("tag_confidence", 0.8))
        class_confidence = float(data.get("class_confidence", 0.8))
        importance = float(data.get("importance", 0.0))

        return EnrichmentResult(
            auto_tags=auto_tags,
            auto_class=auto_class,
            auto_summary=auto_summary,
            auto_importance=min(max(importance, 0.0), 1.0),
            tag_confidence=min(max(tag_confidence, 0.0), 1.0),
            class_confidence=min(max(class_confidence, 0.0), 1.0),
            success=True,
        )

    def _normalize_tag(self, tag: str) -> str:
        """Normalize a tag to lowercase hyphenated format.

        Args:
            tag: Raw tag string.

        Returns:
            Normalized tag.
        """
        # Convert to lowercase
        tag = tag.lower().strip()
        # Replace spaces and underscores with hyphens
        tag = re.sub(r"[\s_]+", "-", tag)
        # Remove non-alphanumeric except hyphens and slashes
        tag = re.sub(r"[^a-z0-9\-/]", "", tag)
        # Remove consecutive hyphens
        tag = re.sub(r"-+", "-", tag)
        # Strip leading/trailing hyphens
        return tag.strip("-")

    async def batch_enrich(
        self,
        notes: list[dict[str, Any]],
        concurrency: int = 3,
        include_summary: bool = True,
    ) -> list[EnrichmentResult]:
        """Enrich multiple notes in parallel.

        Args:
            notes: List of note dicts with 'content', 'title', 'tags' keys.
            concurrency: Max concurrent LLM calls.
            include_summary: Whether to generate summaries (default True).

        Returns:
            List of EnrichmentResults in same order as input.
        """
        semaphore = asyncio.Semaphore(concurrency)

        async def enrich_with_semaphore(note: dict[str, Any]) -> EnrichmentResult:
            async with semaphore:
                return await self.enrich(
                    content=note.get("content", ""),
                    title=note.get("title", ""),
                    existing_tags=note.get("tags", []),
                    include_summary=note.get("include_summary", include_summary),
                )

        results = await asyncio.gather(
            *[enrich_with_semaphore(note) for note in notes],
            return_exceptions=True,
        )

        # Convert exceptions to error results
        final_results: list[EnrichmentResult] = []
        for r in results:
            if isinstance(r, Exception):
                final_results.append(
                    EnrichmentResult(success=False, error=str(r))
                )
            else:
                final_results.append(r)

        return final_results

    async def enrich_entity(
        self,
        content: str,
        entity_ref: EntityRef | None = None,
        title: str = "",
        existing_tags: list[str] | None = None,
        include_summary: bool = True,
        source_config: SourceEnrichmentConfig | None = None,
        config_registry: EnrichmentConfigRegistry | None = None,
    ) -> EnrichmentResult:
        """Enrich any entity with source-specific LLM-generated metadata.

        This is the generalized version of `enrich()` that uses source-specific
        prompts and thresholds from SourceEnrichmentConfig.

        Args:
            content: Entity content (text/markdown).
            entity_ref: Optional EntityRef for source context.
            title: Entity title.
            existing_tags: Existing human-authored tags.
            include_summary: Whether to generate a summary.
            source_config: Explicit source config (overrides registry lookup).
            config_registry: Registry for source config lookup.

        Returns:
            EnrichmentResult with suggested metadata.
        """
        # Determine source config
        config = source_config
        if config is None and entity_ref is not None and config_registry is not None:
            config = config_registry.get(entity_ref.source_id)

        # Fall back to default Obsidian config
        if config is None:
            config = DEFAULT_OBSIDIAN_CONFIG

        try:
            # Truncate content if too long
            max_length = config.max_content_length
            truncated_content = content[:max_length]
            if len(content) > max_length:
                truncated_content += "\n\n[Content truncated...]"

            # Build classification list
            classifications = config.classifications or self.classifications

            # Build system prompt from config
            system_prompt = config.system_prompt.format(
                classifications=", ".join(classifications)
            )

            # If we're not including summary, modify the prompt
            if not include_summary:
                # Remove summary-related instructions from system prompt
                system_prompt = self._remove_summary_from_prompt(system_prompt)

            # Build user prompt
            existing_tags_section = ""
            if existing_tags:
                existing_tags_section = f"Existing tags: {', '.join(existing_tags)}"

            entity_type = entity_ref.entity_type if entity_ref else "note"
            summary_instruction = " Summary is REQUIRED." if include_summary else " Do NOT include a summary."

            if config.user_prompt_template:
                user_prompt = config.user_prompt_template.format(
                    title=title or "Untitled",
                    existing_tags_section=existing_tags_section,
                    content=truncated_content,
                    entity_type=entity_type,
                    summary_instruction=summary_instruction,
                )
            else:
                # Fall back to default template
                user_prompt = f"""Analyze this {entity_type} and suggest metadata:

---
Title: {title or 'Untitled'}
{existing_tags_section}
---

{truncated_content}

---

Respond with JSON only, no markdown formatting.{summary_instruction}
"""

            # Use config-specific temperature and model
            temperature = config.temperature
            model = config.model or self.model

            # Call LLM
            response = await self.llm_service.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                model=model,
                max_tokens=500,
            )

            # Parse JSON response with config-specific classifications
            result = self._parse_response_with_config(response, classifications)
            result.raw_response = response

            logger.debug(
                "entity_enriched",
                source_id=entity_ref.source_id if entity_ref else "unknown",
                entity_type=entity_type,
                title=title,
                auto_tags=result.auto_tags,
                auto_class=result.auto_class,
                include_summary=include_summary,
                has_summary=result.auto_summary is not None,
            )

        except Exception as e:
            logger.exception(
                "entity_enrichment_failed",
                source_id=entity_ref.source_id if entity_ref else "unknown",
                title=title,
            )
            return EnrichmentResult(
                success=False,
                error=str(e),
            )
        else:
            return result

    def _remove_summary_from_prompt(self, prompt: str) -> str:
        """Remove summary-related instructions from a system prompt.

        Used when include_summary=False to adjust source-specific prompts.
        """
        # Remove lines containing "summary" (case-insensitive)
        lines = prompt.split("\n")
        filtered_lines = [
            line for line in lines
            if "summary" not in line.lower() or "do not" in line.lower()
        ]
        return "\n".join(filtered_lines)

    def _parse_response_with_config(
        self,
        response: str,
        classifications: list[str],
    ) -> EnrichmentResult:
        """Parse LLM JSON response with config-specific classifications.

        Args:
            response: Raw LLM response text.
            classifications: Valid classifications for validation.

        Returns:
            Parsed EnrichmentResult.
        """
        # Strip markdown code blocks if present
        text = response.strip()

        # Remove ```json ... ``` wrapper
        if text.startswith("```"):
            first_newline = text.find("\n")
            if first_newline > 0:
                text = text[first_newline + 1:]
            text = text.removesuffix("```").strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            # Try to extract JSON from response
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                except json.JSONDecodeError:
                    return EnrichmentResult(
                        success=False,
                        error=f"Invalid JSON response: {e}",
                        raw_response=response,
                    )
            else:
                return EnrichmentResult(
                    success=False,
                    error=f"No JSON found in response: {e}",
                    raw_response=response,
                )

        # Validate and extract fields
        auto_tags = data.get("tags", [])
        if not isinstance(auto_tags, list):
            auto_tags = []
        auto_tags = [self._normalize_tag(t) for t in auto_tags if isinstance(t, str)]

        auto_class = data.get("class")
        if auto_class and auto_class not in classifications:
            auto_class = None

        auto_summary = data.get("summary")
        if auto_summary in {"null", ""}:
            auto_summary = None

        tag_confidence = float(data.get("tag_confidence", 0.8))
        class_confidence = float(data.get("class_confidence", 0.8))
        importance = float(data.get("importance", 0.0))

        return EnrichmentResult(
            auto_tags=auto_tags,
            auto_class=auto_class,
            auto_summary=auto_summary,
            auto_importance=min(max(importance, 0.0), 1.0),
            tag_confidence=min(max(tag_confidence, 0.0), 1.0),
            class_confidence=min(max(class_confidence, 0.0), 1.0),
            success=True,
        )
