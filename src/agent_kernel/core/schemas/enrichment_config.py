"""Enrichment and Summarization Configuration Schemas (v1.0.5).

Provides source-agnostic configuration for LLM enrichment of any entity type.
Each source (Obsidian, Slack, Outlook, etc.) can have its own thresholds
and prompts tailored to the content type.

v1.0.5: Generalized from note-only to Universal Entity Model support.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EnrichmentThresholds(BaseModel):
    """Thresholds for when to enrich an entity.

    Controls which entities get enriched based on size thresholds,
    content type, path patterns, and tags. Entities that don't meet
    the criteria can either skip enrichment entirely or still get
    tags/classification without a summary.
    """

    # -------------------------------------------------------------------------
    # Size Thresholds (0 = disabled)
    # -------------------------------------------------------------------------
    min_char_count: int = Field(
        default=500,
        ge=0,
        description="Minimum character count to trigger summarization. 0 = disabled.",
    )
    min_word_count: int = Field(
        default=100,
        ge=0,
        description="Minimum word count to trigger summarization. 0 = disabled.",
    )

    # -------------------------------------------------------------------------
    # Exclusions
    # -------------------------------------------------------------------------
    excluded_entity_types: list[str] = Field(
        default_factory=list,
        description="Entity types to exclude (e.g., 'quick-capture', 'log').",
    )
    excluded_paths: list[str] = Field(
        default_factory=lambda: ["Daily Notes/", "Journal/"],
        description="Path prefixes to exclude (for file-based sources).",
    )
    excluded_tags: list[str] = Field(
        default_factory=lambda: ["no-summary", "private"],
        description="Tags that exclude an entity from summarization.",
    )
    excluded_classifications: list[str] = Field(
        default_factory=lambda: ["journal", "daily-note"],
        description="Classifications to exclude from summarization.",
    )

    # -------------------------------------------------------------------------
    # Force Include (overrides exclusions)
    # -------------------------------------------------------------------------
    force_include_tags: list[str] = Field(
        default_factory=lambda: ["summarize", "important"],
        description="Tags that force summarization even if otherwise excluded.",
    )

    # -------------------------------------------------------------------------
    # Skip Behavior
    # -------------------------------------------------------------------------
    skip_behavior: Literal["skip_entirely", "enrich_no_summary"] = Field(
        default="enrich_no_summary",
        description=(
            "What to do when an entity is excluded from summarization: "
            "'skip_entirely' = no enrichment at all, "
            "'enrich_no_summary' = still generate tags/class but no summary."
        ),
    )

    def should_summarize(
        self,
        content: str,
        path: str | None = None,
        entity_type: str | None = None,
        tags: list[str] | None = None,
        classification: str | None = None,
    ) -> tuple[bool, str]:
        """Determine if an entity should be summarized.

        Args:
            content: Full entity content.
            path: Entity path (for file-based sources).
            entity_type: Type of entity.
            tags: Entity tags (both human and auto).
            classification: Entity classification.

        Returns:
            Tuple of (should_summarize, reason).
        """
        tags = tags or []

        # Force include check (highest priority)
        for tag in self.force_include_tags:
            if tag in tags:
                return True, f"force_include_tag:{tag}"

        # Excluded tags check
        for tag in self.excluded_tags:
            if tag in tags:
                return False, f"excluded_tag:{tag}"

        # Excluded entity types check
        if entity_type and entity_type in self.excluded_entity_types:
            return False, f"excluded_entity_type:{entity_type}"

        # Excluded paths check (for file-based sources)
        if path:
            for excluded_path in self.excluded_paths:
                if path.startswith(excluded_path):
                    return False, f"excluded_path:{excluded_path}"

        # Excluded classification check
        if classification and classification in self.excluded_classifications:
            return False, f"excluded_classification:{classification}"

        # Size thresholds check
        if self.min_char_count > 0 and len(content) < self.min_char_count:
            return False, f"below_min_chars:{len(content)}<{self.min_char_count}"

        if self.min_word_count > 0:
            word_count = len(content.split())
            if word_count < self.min_word_count:
                return False, f"below_min_words:{word_count}<{self.min_word_count}"

        return True, "meets_all_criteria"

    def should_enrich(
        self,
        content: str,
        path: str | None = None,
        entity_type: str | None = None,
        tags: list[str] | None = None,
        classification: str | None = None,
    ) -> tuple[bool, bool, str]:
        """Determine if an entity should be enriched and if summary should be included.

        Args:
            content: Full entity content.
            path: Entity path (for file-based sources).
            entity_type: Type of entity.
            tags: Entity tags (both human and auto).
            classification: Entity classification.

        Returns:
            Tuple of (should_enrich, include_summary, reason).
        """
        should_summarize, reason = self.should_summarize(
            content=content,
            path=path,
            entity_type=entity_type,
            tags=tags,
            classification=classification,
        )

        if should_summarize:
            return True, True, reason

        # Entity is excluded from summarization
        if self.skip_behavior == "skip_entirely":
            return False, False, f"skipped:{reason}"
        else:  # enrich_no_summary
            return True, False, f"no_summary:{reason}"


class SourceEnrichmentConfig(BaseModel):
    """Enrichment configuration for a specific source type.

    Each source (Obsidian, Slack, Outlook, etc.) can have its own:
    - System prompt tailored to the content type
    - Classifications relevant to that source
    - Thresholds for when to enrich
    - Output schema (what to extract)
    """

    source_id: str = Field(
        ...,
        description="Source identifier (obsidian, slack, outlook, etc.)",
    )
    entity_types: list[str] = Field(
        default_factory=list,
        description="Entity types this config applies to (note, message, email, etc.)",
    )
    description: str = Field(
        default="",
        description="Human-readable description of this source config.",
    )

    # -------------------------------------------------------------------------
    # Prompt Configuration
    # -------------------------------------------------------------------------
    system_prompt: str = Field(
        ...,
        description="LLM system prompt for enriching entities from this source.",
    )
    user_prompt_template: str = Field(
        default="",
        description="User prompt template with {title}, {content}, {existing_tags} placeholders.",
    )

    # -------------------------------------------------------------------------
    # Output Schema
    # -------------------------------------------------------------------------
    extract_summary: bool = Field(
        default=True,
        description="Whether to extract summaries for this source.",
    )
    extract_tags: bool = Field(
        default=True,
        description="Whether to extract tags for this source.",
    )
    extract_classification: bool = Field(
        default=True,
        description="Whether to extract classification for this source.",
    )
    classifications: list[str] = Field(
        default_factory=list,
        description="Valid classifications for this source.",
    )

    # -------------------------------------------------------------------------
    # LLM Settings
    # -------------------------------------------------------------------------
    model: str | None = Field(
        default=None,
        description="LLM model override for this source (None = use default).",
    )
    temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="LLM temperature for this source.",
    )
    max_content_length: int = Field(
        default=4000,
        gt=0,
        description="Max content characters to send to LLM.",
    )

    # -------------------------------------------------------------------------
    # Thresholds
    # -------------------------------------------------------------------------
    thresholds: EnrichmentThresholds = Field(
        default_factory=EnrichmentThresholds,
        description="Thresholds for when to enrich entities from this source.",
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return self.model_dump()


# =============================================================================
# Default Configurations
# =============================================================================

# Default thresholds (for backwards compatibility)
DEFAULT_ENRICHMENT_THRESHOLDS = EnrichmentThresholds()

# Alias for backwards compatibility with v1.0.5 initial release
SummarizationConfig = EnrichmentThresholds
DEFAULT_SUMMARIZATION_CONFIG = DEFAULT_ENRICHMENT_THRESHOLDS

# Default Obsidian prompt
DEFAULT_OBSIDIAN_SYSTEM_PROMPT = """\
You are an expert at analyzing notes and documents to suggest metadata.

Your task is to analyze the content and suggest:
1. **Tags**: 2-5 relevant topic tags (lowercase, hyphenated)
2. **Classification**: A single category that best describes the note type
3. **Summary**: A concise 1-2 sentence summary that captures the main topic and purpose

Available classifications: {classifications}

IMPORTANT RULES:
- Tags should be general topics, not specific to the note content
- Use existing tag patterns if the note has human tags
- Classification must be from the provided list
- Summary MUST always be provided - it will be used for semantic search
- Summary should be semantic-rich: include key concepts, entities, and relationships
- Be conservative - only suggest high-confidence tags

Respond in JSON format:
{{
  "tags": ["tag1", "tag2"],
  "class": "classification",
  "summary": "Concise summary capturing the main topic, key concepts, and purpose of the note.",
  "tag_confidence": 0.85,
  "class_confidence": 0.90
}}
"""

DEFAULT_OBSIDIAN_USER_PROMPT = """Analyze this {entity_type} and suggest metadata:

---
Title: {title}
{existing_tags_section}
---

{content}

---

Respond with JSON only, no markdown formatting.{summary_instruction}
"""

DEFAULT_OBSIDIAN_CLASSIFICATIONS = [
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

# Default Obsidian config (used if no YAML config exists)
DEFAULT_OBSIDIAN_CONFIG = SourceEnrichmentConfig(
    source_id="obsidian",
    entity_types=["note", "document"],
    description="Default configuration for Obsidian vault notes.",
    system_prompt=DEFAULT_OBSIDIAN_SYSTEM_PROMPT,
    user_prompt_template=DEFAULT_OBSIDIAN_USER_PROMPT,
    extract_summary=True,
    extract_tags=True,
    extract_classification=True,
    classifications=DEFAULT_OBSIDIAN_CLASSIFICATIONS,
    thresholds=EnrichmentThresholds(
        min_char_count=500,
        min_word_count=100,
        excluded_paths=["Daily Notes/", "Journal/"],
        excluded_tags=["no-summary", "private"],
        excluded_classifications=["journal", "daily-note"],
        force_include_tags=["summarize", "important"],
        skip_behavior="enrich_no_summary",
    ),
)
