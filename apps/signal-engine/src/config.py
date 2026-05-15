"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../../.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # MongoDB
    mongodb_uri: str = "mongodb://admin:admin123@localhost:27017/portfolio_analyzer?authSource=admin"
    mongodb_db_name: str = "portfolio_analyzer"

    # AI provider — mirrors Node.js AI_PROVIDER env var
    ai_provider: str = "ollama"

    # Per-provider model names
    anthropic_model: str = "claude-sonnet-4-5"
    openai_model: str = "gpt-4o"
    gemini_model: str = "gemini-2.0-flash"
    kimi_model: str = "moonshotai/kimi-k2-instruct"
    ollama_model: str = "llama3.1"

    # Provider credentials
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    ollama_base_url: str = "http://localhost:11434"
    ollama_timeout: int = 600_000

    # Trading mode
    trading_mode: str = "paper"
    virtual_portfolio_initial: float = 100_000.0
    min_signal_confidence: float = 60.0
    max_positions: int = 8
    position_size_pct: float = 12.0

    # Auth
    signal_engine_api_key: str = "local-dev-key-change-in-prod"

    # Observability
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "trading-signals"
    alert_webhook_url: str = ""
