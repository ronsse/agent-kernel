"""Tests for derivation mapping and suppression stores."""

from datetime import timedelta

from agent_kernel.core.schemas.base import utc_now
from agent_kernel.memory.derivation_store import (
    DerivationMappingRecord,
    DerivationMappingStore,
    SuppressionRecord,
    SuppressionRegistry,
)


def test_put_and_get_mapping(tmp_path):
    db_path = tmp_path / "entity_store.db"
    store = DerivationMappingStore(db_path)
    now = utc_now()
    record = DerivationMappingRecord(
        source_system="google",
        source_container_id="cal_1",
        source_item_id="evt_1",
        derivation_kind="external_task:deadline",
        target_system="external",
        target_item_id="task_123",
        last_synced_etag="etag_1",
        last_synced_at=now,
    )
    store.put_mapping(record)

    fetched = store.get_mapping(
        source_system="google",
        source_container_id="cal_1",
        source_item_id="evt_1",
        derivation_kind="external_task:deadline",
    )
    assert fetched is not None
    assert fetched.target_item_id == "task_123"
    assert fetched.last_synced_etag == "etag_1"


def test_suppression_expiry(tmp_path):
    db_path = tmp_path / "entity_store.db"
    registry = SuppressionRegistry(db_path)
    expired = utc_now() - timedelta(hours=1)
    registry.put_suppression(
        SuppressionRecord(
            source_system="google",
            source_item_id="cal_1:evt_1",
            artifact_kind="obsidian_meeting_note",
            suppressed_until=expired,
            reason="test",
        )
    )

    assert registry.get_suppression(
        source_system="google",
        source_item_id="cal_1:evt_1",
        artifact_kind="obsidian_meeting_note",
    ) is not None

    cleared = registry.clear_expired()
    assert cleared == 1
