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
    gemini_model: str = "gemini-2.5-flash"
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
    #
    # Capital allocation has a single source of truth: nt_total_capital_inr.
    # Per-trade size is *derived* as total / nt_max_positions (equal-weight per
    # slot), so the per-trade and total controls can never drift out of sync —
    # the failure that once sized every trade at ₹50k when ₹3k was intended.
    # A hard backstop in trade_decision additionally refuses any entry that
    # would push total deployed capital over nt_total_capital_inr.
    nt_total_capital_inr: float = 100_000.0   # max cash deployed across all open positions
    nt_max_positions: int = 10                 # max open positions == number of equal-weight slots
    nt_max_stocks_per_signal: int = 2         # max entries from a single news event
    nt_max_positions_per_sector: int = 2     # per-sector open position cap — prevents
                                             #   sector concentration (e.g. 3 Pharma
                                             #   positions all losing on one bad news day)
    # Split stop-loss (see trailing_sl.update_stop). A position gets a WIDE
    # initial stop to survive normal post-entry noise, then switches to a TIGHT
    # trailing stop only once it is genuinely in profit. The old single 1.5%
    # trailing stop fired on entry-timing wiggle before the thesis could play
    # out (every closed trade on 2026-06-02 exited via SL, none hit target).
    nt_initial_sl_pct: float = 0.03           # wide stop at entry (3%) — breathing room
    nt_trail_sl_pct: float = 0.015            # tight trail (1.5%) below the high, once active
    nt_trail_activate_pct: float = 0.02       # start trailing only after +2% in profit
    nt_sl_pct: float = 0.015                  # LEGACY: pure-trail width for positions opened
                                              #   before the split-stop change (sl_monitor fallback)
    nt_target_pct: float = 0.05               # profit target (5%) — was 8%, unreachable under a
                                              #   1.5% trail; now a sane ceiling, trail is primary exit
    nt_max_hold_days: int = 5                 # force-close on day 5
    nt_news_delay_seconds: int = 900          # SQS delay after classification (15 min)
    # Hard cutoff: no new entries after this IST minute-of-day (870 = 14:30).
    # Primary control is the EventBridge schedule (ingester stops at 08:45 UTC =
    # 14:15 IST), but SQS messages can sit in the queue longer than expected.
    # This gate in trade_decision catches any stragglers. Bypassed when
    # nt_bypass_market_hours=true so dev/test runs are not affected.
    nt_entry_cutoff_ist: int = 870            # 14:30 IST (14×60+30)
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
