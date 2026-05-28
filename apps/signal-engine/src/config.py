"""Application configuration loaded from environment variables."""

import os

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_API_KEY = "local-dev-key-change-in-prod"
_DEFAULT_MONGO_CREDS = "admin:admin123"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),  # root .env first, local apps/signal-engine/.env overrides
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # MongoDB
    mongodb_uri: str = f"mongodb://{_DEFAULT_MONGO_CREDS}@localhost:27017/portfolio_analyzer?authSource=admin"
    mongodb_db_name: str = "portfolio_analyzer"

    # AI provider — mirrors Node.js AI_PROVIDER env var
    ai_provider: str = "ollama"

    # Per-provider model names
    anthropic_model: str = "claude-sonnet-4-5"
    openai_model: str = "gpt-4o"
    gemini_model: str = "gemini-2.0-flash"
    kimi_model: str = "moonshotai/kimi-k2"
    ollama_model: str = "llama3.1"

    # Provider credentials
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"
    ollama_base_url: str = "http://localhost:11434"
    ollama_timeout: int = 600_000

    # Trading mode
    trading_mode: str = "paper"
    virtual_portfolio_initial: float = 100_000.0
    min_signal_confidence: float = 60.0
    max_positions: int = 8
    position_size_pct: float = 12.0
    max_sector_positions: int = 3       # max open positions in any single sector
    daily_loss_limit_pct: float = 3.0   # halt new orders if portfolio drops ≥3% today
    portfolio_floor_pct: float = 70.0   # absolute halt if total_value < 70% of initial

    # News-trader Telegram alerts
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # News-trader SQS queue URLs (injected by serverless.yml at deploy time)
    news_raw_queue_url: str = ""
    news_signals_queue_url: str = ""

    # News-trader dev flags
    nt_bypass_market_hours: bool = False      # set true in .env to test outside 09:00–15:35
    nt_bypass_market_holiday: bool = False    # set true in .env to test on NSE holidays

    # News-trader position rules
    nt_position_size_inr: float = 1_000.0   # fixed rupees per trade
    nt_max_positions: int = 10                 # max simultaneous open positions
    nt_max_stocks_per_signal: int = 2         # max entries from a single news event
    nt_sl_pct: float = 0.015                  # trailing stop-loss distance (1.5%)
    nt_target_pct: float = 0.08               # profit target (8%)
    nt_max_hold_days: int = 5                 # force-close on day 5
    nt_news_delay_seconds: int = 900          # SQS delay after classification (15 min)
    nt_classifier_prompt_version: str = "1.0.0"  # bump when classifier prompt changes

    # Auth
    signal_engine_api_key: str = _DEFAULT_API_KEY

    # LLM safety limits
    llm_timeout_s: float = 30.0             # asyncio.wait_for budget per LLM call
    llm_daily_spend_limit_usd: float = 5.0  # halt new LLM calls when daily cost exceeds this

    # Observability
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "trading-signals"
    alert_webhook_url: str = ""

    @model_validator(mode="after")
    def _reject_default_secrets(self) -> "Settings":
        """Refuse to start in non-dev environments with insecure default credentials."""
        stage = os.getenv("STAGE", "dev")
        if stage == "dev":
            return self
        insecure: list[str] = []
        if self.signal_engine_api_key == _DEFAULT_API_KEY:
            insecure.append("SIGNAL_ENGINE_API_KEY is still the default dev value")
        if _DEFAULT_MONGO_CREDS in self.mongodb_uri:
            insecure.append("MONGODB_URI contains default dev credentials")
        if not any([self.anthropic_api_key, self.openai_api_key, self.gemini_api_key, self.nvidia_api_key, self.deepseek_api_key]):
            insecure.append("No LLM provider API key is set")
        if insecure:
            raise ValueError(
                f"Refusing to start in STAGE={stage!r} with insecure defaults:\n"
                + "\n".join(f"  • {msg}" for msg in insecure)
            )
        return self
