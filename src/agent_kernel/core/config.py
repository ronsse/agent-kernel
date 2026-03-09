"""Configuration management using Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from agent_kernel.core.schemas.enrichment_config import SummarizationConfig


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.secrets"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Core Settings
    # -------------------------------------------------------------------------
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"

    # -------------------------------------------------------------------------
    # Database / Storage
    # -------------------------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/agent_kernel.db"

    # Store backend selection: "sqlite" (local-first) or "postgres" (cloud/Supabase)
    store_backend: str = "sqlite"

    # Supabase / PostgreSQL settings (used when store_backend = "postgres")
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_db_host: str = ""
    supabase_db_port: int = 5432
    supabase_db_name: str = "postgres"
    supabase_db_user: str = "postgres"
    supabase_db_password: str = ""

    # Connection pool settings
    postgres_min_connections: int = 1
    postgres_max_connections: int = 10

    # Vector Store ("auto" selects LanceDB when available, falls back to SQLite)
    vector_store_type: str = "auto"

    # -------------------------------------------------------------------------
    # LLM Providers
    # -------------------------------------------------------------------------
    default_llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    # -------------------------------------------------------------------------
    # Tool Broker
    # -------------------------------------------------------------------------
    tool_broker_max_concurrency: int = 5
    tool_broker_default_timeout_ms: int = 30000
    mcp_tools_repo_path: str = ""

    # Retry configuration
    tool_broker_retry_enabled: bool = True
    tool_broker_retry_max_retries: int = 3
    tool_broker_retry_base_delay_ms: int = 1000
    tool_broker_retry_max_delay_ms: int = 30000

    # Circuit breaker configuration
    tool_broker_circuit_breaker_enabled: bool = True
    tool_broker_circuit_breaker_failure_threshold: int = 5
    tool_broker_circuit_breaker_reset_timeout_ms: int = 30000

    # -------------------------------------------------------------------------
    # Tracing
    # -------------------------------------------------------------------------
    trace_store_path: str = "./data/traces"
    trace_jsonl_enabled: bool = True
    trace_jsonl_path: str = "./data/traces/traces.jsonl"

    # -------------------------------------------------------------------------
    # Memory Subsystem
    # -------------------------------------------------------------------------
    document_store_path: str = "./data/documents"
    event_log_path: str = "./data/events"
    graph_store_type: str = "sqlite"

    # -------------------------------------------------------------------------
    # Context Assembler
    # -------------------------------------------------------------------------
    context_max_tokens: int = 8000
    context_max_items: int = 50
    context_max_notes: int = 20
    context_max_tasks: int = 30
    context_max_events: int = 10
    skills_dir: str = "~/.agent_kernel/skills"
    skills_enable_scripts: bool = False
    skills_allowed_script_skills: str = ""
    skills_allowed_script_origins: str = "local"
    skills_script_extensions: str = ".py"
    skills_script_timeout_ms: int = 30000

    # -------------------------------------------------------------------------
    # Scheduler & Timezone
    # -------------------------------------------------------------------------
    # IANA timezone (e.g., America/Denver, America/New_York, UTC)
    scheduler_timezone: str = "UTC"

    # -------------------------------------------------------------------------
    # Obsidian Integration
    # -------------------------------------------------------------------------
    obsidian_vault_path: str = ""
    
    # Agent-Rules directory within the vault (canonical specs)
    # These are indexed as type=spec and made available to agents
    vault_agent_rules_dir: str = "Agent-Rules"
    
    # -------------------------------------------------------------------------
    # Context Layers (v1.0.8)
    # -------------------------------------------------------------------------
    # Local project context directory (relative to project root)
    # Contains project-specific rules, configs, and context
    local_context_dir: str = "configs"
    
    # External context directories (comma-separated paths)
    # Additional context sources outside the project and vault
    external_context_dirs: str = ""
    
    # Whether to auto-sync vault Agent-Rules to local .cursor/rules/
    sync_vault_rules_to_cursor: bool = True
    
    # Whether to auto-generate CLAUDE.md from vault Agent-Rules
    generate_claude_md: bool = True

    # -------------------------------------------------------------------------
    # Cursor Runner (v1.0.7)
    # -------------------------------------------------------------------------
    # Disable when Cursor CLI cannot run in restricted environments (e.g., VPC)
    cursor_runner_enabled: bool = True
    cursor_runner_disabled_reason: str = ""

    # -------------------------------------------------------------------------
    # Claude Code Runner
    # -------------------------------------------------------------------------
    # Uses `claude --print` (non-interactive mode). Requires Claude Code CLI
    # on PATH (installed via `npm install -g @anthropic-ai/claude-code`).
    claude_runner_enabled: bool = True
    claude_runner_disabled_reason: str = ""

    # -------------------------------------------------------------------------
    # Enrichment (Auto-Tagging)
    # -------------------------------------------------------------------------
    enrichment_model: str = "gpt-4o-mini"
    enrichment_temperature: float = 0.3
    enrichment_max_content_length: int = 4000

    # -------------------------------------------------------------------------
    # Embeddings
    # -------------------------------------------------------------------------
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    embedding_chunk_size: int = 500
    embedding_chunk_overlap: int = 50

    # -------------------------------------------------------------------------
    # Summarization Thresholds (v1.0.5)
    # -------------------------------------------------------------------------
    # Minimum character count to trigger summarization (0 = disabled)
    summarization_min_chars: int = 500
    # Minimum word count to trigger summarization (0 = disabled)
    summarization_min_words: int = 100
    # Folders to exclude from summarization (comma-separated)
    summarization_excluded_folders: str = "Daily Notes/,Journal/"
    # Tags that exclude a note from summarization (comma-separated)
    summarization_excluded_tags: str = "no-summary,private"
    # Classifications to exclude from summarization (comma-separated)
    summarization_excluded_classifications: str = "journal,daily-note"
    # Tags that force summarization even if otherwise excluded (comma-separated)
    summarization_force_include_tags: str = "summarize,important"
    # Behavior when excluded: skip_entirely | enrich_no_summary
    summarization_skip_behavior: str = "enrich_no_summary"

    # -------------------------------------------------------------------------
    # LLM Semantic Cache
    # -------------------------------------------------------------------------
    llm_cache_enabled: bool = True
    llm_cache_db_path: str = "./data/llm_cache.db"
    llm_cache_default_ttl_seconds: int = 86400

    # -------------------------------------------------------------------------
    # Rate Limiting
    # -------------------------------------------------------------------------
    tool_broker_rate_limiting_enabled: bool = True

    # -------------------------------------------------------------------------
    # Idempotency Store
    # -------------------------------------------------------------------------
    idempotency_store_enabled: bool = True
    idempotency_default_ttl_hours: int = 24

    # -------------------------------------------------------------------------
    # LLM Circuit Breaker
    # -------------------------------------------------------------------------
    llm_circuit_breaker_enabled: bool = True
    llm_circuit_breaker_failure_threshold: int = 3
    llm_circuit_breaker_reset_timeout_ms: int = 60000

    # -------------------------------------------------------------------------
    # Thinking Policy Thresholds
    # -------------------------------------------------------------------------
    thinking_high_escalation_threshold: float = 0.3
    thinking_low_success_threshold: float = 0.7
    thinking_model_success_threshold: float = 0.85

    # -------------------------------------------------------------------------
    # Code Tools
    # -------------------------------------------------------------------------
    # Comma-separated list of allowed repository root paths
    code_tools_allowed_repo_roots: str = ""
    # Path to the repositories registry config
    code_tools_repositories_config: str = "configs/repositories.yaml"

    # -------------------------------------------------------------------------
    # Security
    # -------------------------------------------------------------------------
    require_approval_external_writes: bool = True
    auto_approve_read_only: bool = True

    # -------------------------------------------------------------------------
    # Approval Notifications
    # -------------------------------------------------------------------------
    # "log" or "" (disabled). Extend with custom notification adapters.
    approval_notify_channel: str = ""

    @property
    def postgres_url(self) -> str:
        """Build PostgreSQL connection URL from Supabase settings.

        Falls back to database_url if it starts with 'postgresql'.
        """
        if self.database_url.startswith("postgresql"):
            return self.database_url
        if self.supabase_db_host and self.supabase_db_password:
            return (
                f"postgresql://{self.supabase_db_user}:"
                f"{self.supabase_db_password}@"
                f"{self.supabase_db_host}:{self.supabase_db_port}/"
                f"{self.supabase_db_name}"
            )
        return ""

    @property
    def data_dir(self) -> Path:
        """Get the data directory path."""
        return Path("./data")

    @property
    def configs_dir(self) -> Path:
        """Get the configs directory path."""
        return Path("./configs")

    def get_summarization_config(self) -> SummarizationConfig:
        """Build SummarizationConfig from settings.

        Converts comma-separated strings to lists.
        """
        from agent_kernel.core.schemas.enrichment_config import SummarizationConfig

        def parse_list(value: str) -> list[str]:
            """Parse comma-separated string into list of non-empty strings."""
            return [s.strip() for s in value.split(",") if s.strip()]

        return SummarizationConfig(
            min_char_count=self.summarization_min_chars,
            min_word_count=self.summarization_min_words,
            excluded_folders=parse_list(self.summarization_excluded_folders),
            excluded_tags=parse_list(self.summarization_excluded_tags),
            excluded_classifications=parse_list(self.summarization_excluded_classifications),
            force_include_tags=parse_list(self.summarization_force_include_tags),
            skip_behavior=self.summarization_skip_behavior,  # type: ignore[arg-type]
        )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
