"""Tests for reserved block updates."""

from agent_kernel.core.schemas.note import ReservedBlockType
from agent_kernel.services.reserved_blocks import update_reserved_block


def test_update_meeting_block_preserves_other_content():
    content = (
        "# Title\n\n"
        "<!-- kernel:block:meeting_auto begin -->\n"
        "Old content\n"
        "<!-- kernel:block:meeting_auto end -->\n\n"
        "## Notes\n"
        "- Keep this\n"
    )

    updated = update_reserved_block(
        content,
        ReservedBlockType.MEETING_AUTO,
        "New content",
    )

    assert "Old content" not in updated
    assert "New content" in updated
    assert "# Title" in updated
    assert "## Notes" in updated
