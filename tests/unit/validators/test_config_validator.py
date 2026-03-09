"""Tests for ConfigValidator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from agent_kernel.core.config import Settings
from agent_kernel.validators.config_validator import ConfigValidator
from agent_kernel.validators.results import CheckStatus


def _make_settings(**overrides: object) -> Settings:
    """Create a Settings instance with overrides, ignoring .env."""
    defaults = {
        "default_llm_provider": "",
        "openai_api_key": "",
        "anthropic_api_key": "",
        "debug": False,
        "store_backend": "sqlite",
        "supabase_db_host": "",
        "vector_store_type": "none",
        "embedding_model": "text-embedding-3-small",
        "scheduler_timezone": "UTC",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


class TestApiKeys:
    def test_skip_when_no_provider(self) -> None:
        settings = _make_settings(default_llm_provider="")
        result = ConfigValidator(settings).validate()
        api_check = next(c for c in result.checks if c.name == "api_keys")
        assert api_check.status == CheckStatus.SKIP

    def test_error_when_openai_key_missing(self) -> None:
        settings = _make_settings(
            default_llm_provider="openai", openai_api_key=""
        )
        result = ConfigValidator(settings).validate()
        api_check = next(c for c in result.checks if c.name == "api_keys")
        assert api_check.status == CheckStatus.ERROR
        assert "openai_api_key" in api_check.message

    def test_pass_when_openai_key_set(self) -> None:
        settings = _make_settings(
            default_llm_provider="openai", openai_api_key="sk-test"
        )
        result = ConfigValidator(settings).validate()
        api_check = next(c for c in result.checks if c.name == "api_keys")
        assert api_check.status == CheckStatus.PASS

    def test_error_when_anthropic_key_missing(self) -> None:
        settings = _make_settings(
            default_llm_provider="anthropic", anthropic_api_key=""
        )
        result = ConfigValidator(settings).validate()
        api_check = next(c for c in result.checks if c.name == "api_keys")
        assert api_check.status == CheckStatus.ERROR

    def test_pass_unknown_provider(self) -> None:
        settings = _make_settings(default_llm_provider="custom-local")
        result = ConfigValidator(settings).validate()
        api_check = next(c for c in result.checks if c.name == "api_keys")
        assert api_check.status == CheckStatus.PASS


class TestDataPaths:
    def test_error_when_data_dir_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent"
        settings = _make_settings()
        prop = property(lambda self: missing)
        with patch.object(type(settings), "data_dir", new_callable=lambda: prop):
            result = ConfigValidator(settings).validate()
        data_check = next(c for c in result.checks if c.name == "data_paths")
        assert data_check.status == CheckStatus.ERROR

    def test_pass_when_data_dir_exists(self, tmp_path: Path) -> None:
        settings = _make_settings()
        prop = property(lambda self: tmp_path)
        with patch.object(type(settings), "data_dir", new_callable=lambda: prop):
            result = ConfigValidator(settings).validate()
        data_check = next(c for c in result.checks if c.name == "data_paths")
        assert data_check.status == CheckStatus.PASS


class TestDebugMode:
    def test_warn_when_debug_enabled(self) -> None:
        settings = _make_settings(debug=True)
        result = ConfigValidator(settings).validate()
        debug_check = next(c for c in result.checks if c.name == "debug_mode")
        assert debug_check.status == CheckStatus.WARN

    def test_pass_when_debug_disabled(self) -> None:
        settings = _make_settings(debug=False)
        result = ConfigValidator(settings).validate()
        debug_check = next(c for c in result.checks if c.name == "debug_mode")
        assert debug_check.status == CheckStatus.PASS


class TestProviderModel:
    def test_warn_on_mismatch(self) -> None:
        settings = _make_settings(
            default_llm_provider="anthropic",
            anthropic_api_key="sk-ant-test",
            anthropic_model="gpt-4o",
        )
        result = ConfigValidator(settings).validate()
        model_check = next(c for c in result.checks if c.name == "provider_model")
        assert model_check.status == CheckStatus.WARN

    def test_pass_on_match(self) -> None:
        settings = _make_settings(
            default_llm_provider="openai",
            openai_api_key="sk-test",
            openai_model="gpt-4o",
        )
        result = ConfigValidator(settings).validate()
        model_check = next(c for c in result.checks if c.name == "provider_model")
        assert model_check.status == CheckStatus.PASS


class TestStoreBackend:
    def test_warn_postgres_no_host(self) -> None:
        settings = _make_settings(store_backend="postgres", supabase_db_host="")
        result = ConfigValidator(settings).validate()
        store_check = next(c for c in result.checks if c.name == "store_backend")
        assert store_check.status == CheckStatus.WARN

    def test_pass_sqlite(self) -> None:
        settings = _make_settings(store_backend="sqlite")
        result = ConfigValidator(settings).validate()
        store_check = next(c for c in result.checks if c.name == "store_backend")
        assert store_check.status == CheckStatus.PASS


class TestEmbeddingConfig:
    def test_warn_vector_store_no_embedding(self) -> None:
        settings = _make_settings(vector_store_type="sqlite", embedding_model="")
        result = ConfigValidator(settings).validate()
        emb_check = next(c for c in result.checks if c.name == "embedding_config")
        assert emb_check.status == CheckStatus.WARN

    def test_pass_vector_store_none(self) -> None:
        settings = _make_settings(vector_store_type="none")
        result = ConfigValidator(settings).validate()
        emb_check = next(c for c in result.checks if c.name == "embedding_config")
        assert emb_check.status == CheckStatus.PASS


class TestTimezone:
    def test_warn_invalid_timezone(self) -> None:
        settings = _make_settings(scheduler_timezone="FakeZone/Invalid")
        result = ConfigValidator(settings).validate()
        tz_check = next(c for c in result.checks if c.name == "timezone")
        assert tz_check.status == CheckStatus.WARN

    def test_pass_valid_timezone(self) -> None:
        settings = _make_settings(scheduler_timezone="America/Denver")
        result = ConfigValidator(settings).validate()
        tz_check = next(c for c in result.checks if c.name == "timezone")
        assert tz_check.status == CheckStatus.PASS


class TestResultProperties:
    def test_passed_property(self) -> None:
        settings = _make_settings()
        result = ConfigValidator(settings).validate()
        # Default settings with no provider and data_dir may not exist,
        # but we test the property logic regardless
        assert isinstance(result.passed, bool)
        assert isinstance(result.error_count, int)
        assert isinstance(result.warn_count, int)

    def test_all_checks_execute(self) -> None:
        settings = _make_settings()
        result = ConfigValidator(settings).validate()
        check_names = {c.name for c in result.checks}
        expected = {
            "api_keys",
            "data_paths",
            "store_backend",
            "debug_mode",
            "provider_model",
            "embedding_config",
            "timezone",
            "task_backend",
            "obsidian_vault",
        }
        assert check_names == expected


class TestTaskBackend:
    def test_skip_when_no_token(self) -> None:
        settings = _make_settings(task_backend_api_token="")
        result = ConfigValidator(settings).validate()
        check = next(c for c in result.checks if c.name == "task_backend")
        assert check.status == CheckStatus.SKIP

    def test_warn_when_token_but_no_projects(self) -> None:
        settings = _make_settings(
            task_backend_api_token="test-token",  # noqa: S106
            task_backend_default_project="",
        )
        result = ConfigValidator(settings).validate()
        check = next(c for c in result.checks if c.name == "task_backend")
        assert check.status == CheckStatus.WARN
        assert "TASK_BACKEND_DEFAULT_PROJECT" in check.detail

    def test_pass_when_fully_configured(self) -> None:
        settings = _make_settings(
            task_backend_api_token="test-token",  # noqa: S106
            task_backend_default_project="MyProject",
        )
        result = ConfigValidator(settings).validate()
        check = next(c for c in result.checks if c.name == "task_backend")
        assert check.status == CheckStatus.PASS


class TestObsidianVault:
    def test_skip_when_not_configured(self) -> None:
        settings = _make_settings(obsidian_vault_path="")
        result = ConfigValidator(settings).validate()
        check = next(c for c in result.checks if c.name == "obsidian_vault")
        assert check.status == CheckStatus.SKIP

    def test_error_when_path_missing(self) -> None:
        settings = _make_settings(obsidian_vault_path="/nonexistent/vault")
        result = ConfigValidator(settings).validate()
        check = next(c for c in result.checks if c.name == "obsidian_vault")
        assert check.status == CheckStatus.ERROR

    def test_pass_when_valid(self, tmp_path: Path) -> None:
        settings = _make_settings(obsidian_vault_path=str(tmp_path))
        result = ConfigValidator(settings).validate()
        check = next(c for c in result.checks if c.name == "obsidian_vault")
        assert check.status == CheckStatus.PASS
