"""Reserved Block Parser/Writer for kernel-managed markdown sections.

Implements the reserved block pattern from Vault Patch v1.0.8:
- Kernel may ONLY edit inside reserved blocks
- Humans can delete a block to opt out
- Blocks are delimited by HTML comments

Format:
    <!-- kernel:block:{block_type} begin -->
    ... kernel-managed content only ...
    <!-- kernel:block:{block_type} end -->

Example usage:
    parser = ReservedBlockParser()
    blocks = parser.parse(content)
    
    # Update a block
    new_content = parser.update_block(
        content,
        ReservedBlockType.TASKS_TODAY,
        "- [ ] Task 1\\n- [ ] Task 2"
    )
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from agent_kernel.core.schemas.note import ReservedBlock, ReservedBlockType

logger = structlog.get_logger(__name__)


# Regex pattern to match reserved block markers
# Captures: block_type, content between begin/end
BLOCK_PATTERN = re.compile(
    r"<!--\s*kernel:block:(\w+)\s+begin\s*-->\n?"
    r"(.*?)"
    r"<!--\s*kernel:block:\1\s+end\s*-->",
    re.DOTALL,
)

# Pattern for just the begin marker (for insertions)
BEGIN_MARKER_PATTERN = re.compile(
    r"<!--\s*kernel:block:(\w+)\s+begin\s*-->"
)

# Pattern for just the end marker
END_MARKER_PATTERN = re.compile(
    r"<!--\s*kernel:block:(\w+)\s+end\s*-->"
)


class ReservedBlockParser:
    """Parser for kernel-managed reserved blocks in markdown."""

    def parse(self, content: str) -> list[ReservedBlock]:
        """Parse all reserved blocks from markdown content.
        
        Args:
            content: Full markdown content
            
        Returns:
            List of ReservedBlock objects found in content
        """
        blocks = []
        
        for match in BLOCK_PATTERN.finditer(content):
            block_type_str = match.group(1)
            block_content = match.group(2).strip()
            
            try:
                block_type = ReservedBlockType(block_type_str)
            except ValueError:
                logger.warning(
                    "unknown_reserved_block_type",
                    block_type=block_type_str,
                )
                continue
            
            # Calculate line numbers
            start_pos = match.start()
            end_pos = match.end()
            start_line = content[:start_pos].count("\n") + 1
            end_line = content[:end_pos].count("\n") + 1
            
            blocks.append(
                ReservedBlock(
                    block_type=block_type,
                    content=block_content,
                    start_line=start_line,
                    end_line=end_line,
                )
            )
        
        return blocks

    def get_block(
        self, content: str, block_type: ReservedBlockType
    ) -> ReservedBlock | None:
        """Get a specific reserved block from content.
        
        Args:
            content: Full markdown content
            block_type: Type of block to find
            
        Returns:
            ReservedBlock if found, None otherwise
        """
        blocks = self.parse(content)
        for block in blocks:
            if block.block_type == block_type:
                return block
        return None

    def has_block(self, content: str, block_type: ReservedBlockType) -> bool:
        """Check if content contains a specific reserved block.
        
        Args:
            content: Full markdown content
            block_type: Type of block to check for
            
        Returns:
            True if block exists, False otherwise
        """
        pattern = re.compile(
            rf"<!--\s*kernel:block:{block_type.value}\s+begin\s*-->"
        )
        return bool(pattern.search(content))

    def update_block(
        self,
        content: str,
        block_type: ReservedBlockType,
        new_content: str,
    ) -> str:
        """Update the content of a reserved block.
        
        If the block doesn't exist, the content is returned unchanged.
        Use insert_block() to add a new block.
        
        Args:
            content: Full markdown content
            block_type: Type of block to update
            new_content: New content for the block
            
        Returns:
            Updated markdown content
        """
        pattern = re.compile(
            rf"(<!--\s*kernel:block:{block_type.value}\s+begin\s*-->\n?)"
            rf"(.*?)"
            rf"(<!--\s*kernel:block:{block_type.value}\s+end\s*-->)",
            re.DOTALL,
        )
        
        def replacement(match: re.Match) -> str:
            begin_marker = match.group(1)
            end_marker = match.group(3)
            return f"{begin_marker}{new_content}\n{end_marker}"
        
        new_full_content, count = pattern.subn(replacement, content)
        
        if count > 0:
            logger.debug(
                "reserved_block_updated",
                block_type=block_type.value,
            )
        
        return new_full_content

    def insert_block(
        self,
        content: str,
        block_type: ReservedBlockType,
        block_content: str = "",
        after_heading: str | None = None,
        at_end: bool = False,
    ) -> str:
        """Insert a new reserved block into content.
        
        Args:
            content: Full markdown content
            block_type: Type of block to insert
            block_content: Initial content for the block
            after_heading: Insert after this heading (e.g., "## Tasks")
            at_end: If True, append to end of content
            
        Returns:
            Updated markdown content with new block
        """
        if self.has_block(content, block_type):
            # Block already exists, update it instead
            return self.update_block(content, block_type, block_content)
        
        block_text = self.format_block(block_type, block_content)
        
        if after_heading:
            # Find the heading and insert after it
            heading_pattern = re.compile(
                rf"(^#{1,6}\s*{re.escape(after_heading)}\s*$)",
                re.MULTILINE,
            )
            match = heading_pattern.search(content)
            if match:
                insert_pos = match.end()
                return (
                    content[:insert_pos]
                    + "\n\n"
                    + block_text
                    + content[insert_pos:]
                )
        
        if at_end:
            return content.rstrip() + "\n\n" + block_text + "\n"
        
        # Default: insert after frontmatter
        if content.startswith("---"):
            # Find end of frontmatter
            second_delim = content.find("---", 3)
            if second_delim != -1:
                insert_pos = second_delim + 3
                return (
                    content[:insert_pos]
                    + "\n\n"
                    + block_text
                    + content[insert_pos:]
                )
        
        # Fallback: prepend
        return block_text + "\n\n" + content

    def remove_block(
        self, content: str, block_type: ReservedBlockType
    ) -> str:
        """Remove a reserved block from content.
        
        Args:
            content: Full markdown content
            block_type: Type of block to remove
            
        Returns:
            Updated markdown content with block removed
        """
        pattern = re.compile(
            rf"\n*<!--\s*kernel:block:{block_type.value}\s+begin\s*-->\n?"
            rf"(.*?)"
            rf"<!--\s*kernel:block:{block_type.value}\s+end\s*-->\n*",
            re.DOTALL,
        )
        
        new_content = pattern.sub("\n", content)
        
        if new_content != content:
            logger.debug(
                "reserved_block_removed",
                block_type=block_type.value,
            )
        
        return new_content

    @staticmethod
    def format_block(
        block_type: ReservedBlockType, content: str = ""
    ) -> str:
        """Format a reserved block for insertion.
        
        Args:
            block_type: Type of block
            content: Content for the block
            
        Returns:
            Formatted block string
        """
        return (
            f"<!-- kernel:block:{block_type.value} begin -->\n"
            f"{content}\n"
            f"<!-- kernel:block:{block_type.value} end -->"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Convenience Functions
# ─────────────────────────────────────────────────────────────────────────────

def parse_reserved_blocks(content: str) -> list[ReservedBlock]:
    """Parse all reserved blocks from markdown content."""
    return ReservedBlockParser().parse(content)


def update_reserved_block(
    content: str,
    block_type: ReservedBlockType,
    new_content: str,
) -> str:
    """Update a reserved block in markdown content."""
    return ReservedBlockParser().update_block(content, block_type, new_content)


def insert_reserved_block(
    content: str,
    block_type: ReservedBlockType,
    block_content: str = "",
    after_heading: str | None = None,
    at_end: bool = False,
) -> str:
    """Insert a reserved block into markdown content."""
    return ReservedBlockParser().insert_block(
        content, block_type, block_content, after_heading, at_end
    )
