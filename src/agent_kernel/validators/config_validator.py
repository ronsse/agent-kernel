"""Configuration validator for kernel settings."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog

from agent_kernel.core.config import Settings
from agent_kernel.validators.results import (
    CheckStatus,
    ValidationCheck,
    ValidationResult,
)

logger = structlog.get_logger(__name__)

# Provider → env var mapping
_PROVIDER_KEY_MAP: dict[str, str] = {
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
}

# Provider → expected model prefix
_PROVIDER_MODEL_PREFIX: dict[str, list[str]] = {
    "openai": ["gpt-", "o1-", "o3-", "o4-"],
    "anthropic": ["claude-"],
}


class ConfigValidator:
    """Validates kernel configuration settings."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def validate(self) -> ValidationResult:
        """Run all configuration checks."""
        result = ValidationResult(target="config")
        result.checks.append(self._check_api_keys())
        result.checks.append(self._check_data_paths())
        result.checks.append(self._check_store_backend())
        result.checks.append(self._check_debug_mode())
        result.checks.append(self._check_provider_model())
        result.checks.append(self._check_embedding_config())
        result.checks.append(self._check_timezone())
        result.checks.append(self._check_task_backend())
        result.checks.append(self._check_obsidian_vault())
        return result

    def _check_api_keys(self) -> ValidationCheck:
        """Check that API keys are set for the configured provider."""
        provider = self._settings.default_llm_provider
        if not provider:
            return ValidationCheck(
                name="api_keys",
                status=CheckStatus.SKIP,
                message="No LLM provider configured",
            )

        key_field = _PROVIDER_KEY_MAP.get(provider)
        if key_field is None:
            return ValidationCheck(
                name="api_keys",
                status=CheckStatus.PASS,
                message=f"Provider '{provider}' does not require a known API key",
            )

        key_value = getattr(self._settings, key_field, "")
        if not key_value:
            return ValidationCheck(
                name="api_keys",
                status=CheckStatus.ERROR,
                message=f"Provider '{provider}' requires {key_field} but it is not set",
                detail=f"Set {key_field.upper()} in .env or environment",
            )

        return ValidationCheck(
            name="api_keys",
            status=CheckStatus.PASS,
            message=f"API key set for provider '{provider}'",
        )

    def _check_data_paths(self) -> ValidationCheck:
        """Check that the data directory exists and is writable."""
        data_dir = self._settings.data_dir
        if not data_dir.exists():
            return ValidationCheck(
                name="data_paths",
                status=CheckStatus.ERROR,
                message=f"Data directory does not exist: {data_dir}",
                detail="Run 'agent-kernel init' to create it",
            )

        if not os.access(data_dir, os.W_OK):
            return ValidationCheck(
                name="data_paths",
                status=CheckStatus.ERROR,
                message=f"Data directory is not writable: {data_dir}",
            )

        return ValidationCheck(
            name="data_paths",
            status=CheckStatus.PASS,
            message=f"Data directory OK: {data_dir}",
        )

    def _check_store_backend(self) -> ValidationCheck:
        """Check store backend consistency."""
        backend = self._settings.store_backend
        if backend == "postgres" and not self._settings.supabase_db_host:
            return ValidationCheck(
                name="store_backend",
                status=CheckStatus.WARN,
                message="store_backend=postgres but supabase_db_host is not set",
                detail="Set SUPABASE_DB_HOST or switch to store_backend=sqlite",
            )

        return ValidationCheck(
            name="store_backend",
            status=CheckStatus.PASS,
            message=f"Store backend '{backend}' configured",
        )

    def _check_debug_mode(self) -> ValidationCheck:
        """Check if debug mode is enabled."""
        if self._settings.debug:
            return ValidationCheck(
                name="debug_mode",
                status=CheckStatus.WARN,
                message="Debug mode is enabled",
                detail="Set DEBUG=false for production use",
            )

        return ValidationCheck(
            name="debug_mode",
            status=CheckStatus.PASS,
            message="Debug mode disabled",
        )

    def _check_provider_model(self) -> ValidationCheck:
        """Check that the model matches the configured provider."""
        provider = self._settings.default_llm_provider
        if not provider:
            return ValidationCheck(
                name="provider_model",
                status=CheckStatus.SKIP,
                message="No LLM provider configured",
            )

        prefixes = _PROVIDER_MODEL_PREFIX.get(provider)
        if prefixes is None:
            return ValidationCheck(
                name="provider_model",
                status=CheckStatus.PASS,
                message=f"No prefix check for provider '{provider}'",
            )

        # Check the provider's own model field
        model_field = f"{provider}_model"
        model = getattr(self._settings, model_field, "")
        if model and not any(model.startswith(p) for p in prefixes):
            return ValidationCheck(
                name="provider_model",
                status=CheckStatus.WARN,
                message=(
                    f"Model '{model}' may not match provider '{provider}' "
                    f"(expected prefix: {', '.join(prefixes)})"
                ),
            )

        return ValidationCheck(
            name="provider_model",
            status=CheckStatus.PASS,
            message=f"Model matches provider '{provider}'",
        )

    def _check_embedding_config(self) -> ValidationCheck:
        """Check embedding configuration completeness."""
        vst = self._settings.vector_store_type
        if vst == "none":
            return ValidationCheck(
                name="embedding_config",
                status=CheckStatus.PASS,
                message="Vector store disabled",
            )

        if not self._settings.embedding_model:
            return ValidationCheck(
                name="embedding_config",
                status=CheckStatus.WARN,
                message=(
                    f"vector_store_type='{vst}' but embedding_model is empty"
                ),
                detail="Set EMBEDDING_MODEL to enable vector search",
            )

        return ValidationCheck(
            name="embedding_config",
            status=CheckStatus.PASS,
            message=f"Embedding model: {self._settings.embedding_model}",
        )

    def _check_timezone(self) -> ValidationCheck:
        """Check that scheduler_timezone is a valid IANA timezone."""
        tz = self._settings.scheduler_timezone
        try:
            ZoneInfo(tz)
        except (KeyError, Exception):
            return ValidationCheck(
                name="timezone",
                status=CheckStatus.WARN,
                message=f"Invalid IANA timezone: '{tz}'",
                detail="Use a valid timezone like 'America/Denver' or 'UTC'",
            )

        return ValidationCheck(
            name="timezone",
            status=CheckStatus.PASS,
            message=f"Timezone: {tz}",
        )

    def _check_task_backend(self) -> ValidationCheck:
        """Check task backend integration configuration."""
        token = getattr(self._settings, "task_backend_api_token", "")
        if not token:
            return ValidationCheck(
                name="task_backend",
                status=CheckStatus.SKIP,
                message="Task backend not configured (TASK_BACKEND_API_TOKEN not set)",
            )

        issues: list[str] = []
        if not getattr(self._settings, "task_backend_default_project", ""):
            issues.append("TASK_BACKEND_DEFAULT_PROJECT not set")

        if issues:
            return ValidationCheck(
                name="task_backend",
                status=CheckStatus.WARN,
                message="Task backend API token set but optional config missing",
                detail="; ".join(issues),
            )

        return ValidationCheck(
            name="task_backend",
            status=CheckStatus.PASS,
            message="Task backend configured with API token and projects",
        )

    def _check_obsidian_vault(self) -> ValidationCheck:
        """Check Obsidian vault path configuration."""
        vault_path = getattr(self._settings, "obsidian_vault_path", "")
        if not vault_path:
            return ValidationCheck(
                name="obsidian_vault",
                status=CheckStatus.SKIP,
                message="Obsidian vault not configured (OBSIDIAN_VAULT_PATH not set)",
            )

        vault_dir = Path(vault_path)
        if not vault_dir.exists():
            return ValidationCheck(
                name="obsidian_vault",
                status=CheckStatus.ERROR,
                message=f"Obsidian vault path does not exist: {vault_path}",
                detail="Set OBSIDIAN_VAULT_PATH to a valid directory",
            )

        if not vault_dir.is_dir():
            return ValidationCheck(
                name="obsidian_vault",
                status=CheckStatus.ERROR,
                message=f"Obsidian vault path is not a directory: {vault_path}",
            )

        return ValidationCheck(
            name="obsidian_vault",
            status=CheckStatus.PASS,
            message=f"Obsidian vault: {vault_path}",
        )
