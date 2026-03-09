"""Source Descriptor schemas for v1.0.2 flexible context retrieval.

Source Descriptors formalize "index descriptions" - what metadata fields
exist, what operators are legal, and what constraints apply to each source.
This enables schema-aware retrieval planning that doesn't hallucinate
impossible filters.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agent_kernel.core.schemas.base import KernelModel, VersionedModel

# Type aliases for field types
FieldType = Literal["string", "number", "boolean", "datetime", "enum", "list_string"]

# Type aliases for filter operators
FilterOp = Literal[
    "eq",       # Equals
    "neq",      # Not equals
    "in",       # In list
    "not_in",   # Not in list
    "gt",       # Greater than
    "gte",      # Greater than or equal
    "lt",       # Less than
    "lte",      # Less than or equal
    "contains", # String contains / list contains
    "not_contains",
    "prefix",   # String prefix match
    "suffix",   # String suffix match
    "any_in",   # Any element in list matches
    "all_in",   # All elements in list match
    "exists",   # Field exists and is not null
    "not_exists",
]


class FieldDescriptor(KernelModel):
    """Describes a single filterable/searchable field in a source.

    This is the building block for schema-aware retrieval planning.
    When an LLM generates retrieval directives, filters must reference
    fields that actually exist with operators that are actually allowed.
    """

    name: str = Field(
        description="Field name (e.g., 'path', 'tags', 'frontmatter.project')",
    )
    type: FieldType = Field(
        description="Data type of the field",
    )
    allowed_ops: list[FilterOp] = Field(
        default_factory=list,
        description="Filter operators that can be applied to this field",
    )
    description: str | None = Field(
        default=None,
        description="Human-readable description for LLM understanding",
    )
    examples: list[str] = Field(
        default_factory=list,
        description="Example values for LLM understanding",
    )

    def supports_op(self, op: str) -> bool:
        """Check if this field supports the given operator."""
        return op in self.allowed_ops


class SourceConstraint(KernelModel):
    """Constraints that apply to a source.

    These are hard rules about what can and cannot be done with this source.
    For example, Slack messages shouldn't be stored long-term due to policy.
    """

    can_store_text: bool = Field(
        default=True,
        description="Whether full text can be stored in derived indexes",
    )
    max_retention_days: int | None = Field(
        default=None,
        description="Maximum days to retain data (None = indefinite)",
    )
    allowed_entity_types: list[str] = Field(
        default_factory=list,
        description="Entity types that can be retrieved from this source",
    )
    requires_live_fetch: bool = Field(
        default=False,
        description="Whether this source requires on-demand retrieval (not indexed)",
    )
    notes: str | None = Field(
        default=None,
        description="Additional notes about source constraints",
    )


class SourceDescriptor(VersionedModel):
    """Describes a context source and its queryable schema.

    Source Descriptors enable the Instructed Retrieval Planner to generate
    valid, schema-aware retrieval directives. Without these, LLMs tend to
    propose impossible filters like 'frontmatter.department' when that
    field doesn't exist.

    Sources include:
    - obsidian: Obsidian vault notes
    - graph: Knowledge graph nodes/edges
    - tasks: Extracted task entities
    - calendar: Calendar events
    - slack: Slack messages (live fetch only)
    - keep: Google Keep notes (capture inbox)
    """

    source_id: str = Field(
        description="Unique identifier for this source",
    )
    description: str = Field(
        description="Human-readable description of the source",
    )
    fields: list[FieldDescriptor] = Field(
        default_factory=list,
        description="Queryable/filterable fields in this source",
    )
    constraints: SourceConstraint = Field(
        default_factory=SourceConstraint,
        description="Constraints that apply to this source",
    )

    def get_field(self, name: str) -> FieldDescriptor | None:
        """Get a field descriptor by name."""
        for field in self.fields:
            if field.name == name:
                return field
        return None

    def has_field(self, name: str) -> bool:
        """Check if a field exists in this source."""
        return self.get_field(name) is not None

    def validate_filter(self, field_name: str, op: str) -> tuple[bool, str | None]:
        """Validate a filter against this source's schema.

        Args:
            field_name: Name of the field to filter on.
            op: Filter operator to apply.

        Returns:
            Tuple of (is_valid, error_message).
        """
        field = self.get_field(field_name)
        if field is None:
            return False, f"Field '{field_name}' does not exist in source '{self.source_id}'"

        if not field.supports_op(op):
            return False, (
                f"Operator '{op}' is not allowed for field '{field_name}' "
                f"(allowed: {field.allowed_ops})"
            )

        return True, None

    def list_entity_types(self) -> list[str]:
        """Get list of entity types this source can provide."""
        return self.constraints.allowed_entity_types

    def can_store(self) -> bool:
        """Check if this source allows text storage in derived indexes."""
        return self.constraints.can_store_text
