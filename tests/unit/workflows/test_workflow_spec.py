"""Unit tests for workflow spec parsing."""

from agent_kernel.workflows.spec import WorkflowSpec


def test_workflow_spec_parses_vault_sync_config() -> None:
    spec = WorkflowSpec(
        workflow_id="obsidian_enrichment",
        name="Obsidian Enrichment",
        agent_profile_id="obsidian_hygiene_agent",
        steps=["vault_sync", "assemble_context"],
        vault_sync={
            "with_enrichment": True,
            "with_embeddings": False,
            "summarize_all": True,
            "folder": "Projects",
        },
    )

    assert spec.vault_sync.with_enrichment is True
    assert spec.vault_sync.with_embeddings is False
    assert spec.vault_sync.summarize_all is True
    assert spec.vault_sync.folder == "Projects"
