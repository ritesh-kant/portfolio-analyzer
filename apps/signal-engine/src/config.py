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
    nt_target_pct: float = 0.01               # profit target — recalibrated to actual move distribution:
                                              #   avg intraday MFE ~+0.5%, peak at ~97 min post-entry.
                                              #   1% TP is the primary winner exit; trail/time-stop
                                              #   handle everything else.
    nt_max_hold_days: int = 5                 # force-close on day 5 (backstop, EOD-close supersedes)
    nt_max_hold_minutes: int = 90             # intraday time-stop: close 90 min after entry.
                                              #   Captures the ~97-min avg MFE peak; prevents holding
                                              #   past the drift-decay window into negative territory.
    nt_force_close_eod: bool = True           # close all open positions at 15:15 IST.
                                              #   Eliminates overnight gap risk (D5: −₹1,184 from
                                              #   gap-downs; D7: carry cohort −₹408).
    nt_enable_shorts: bool = True             # trade bearish signals as intraday (MIS) shorts.
                                              #   BT6 (2026-06-10): 67 bearish high-conf mod/major
                                              #   signals discarded in 7 days by the long-only
                                              #   gate — roughly half the actionable universe.
                                              #   Safe only because the system is pure intraday:
                                              #   EOD force-close at 15:15 IST lands before
                                              #   Zerodha's MIS auto square-off (~15:20), so no
                                              #   overnight short exposure.
    nt_nifty50_exclusion: bool = True         # skip NIFTY50 large-caps entirely.
                                              #   BT5 (2026-06-10, n=33): large-caps had
                                              #   NEGATIVE GROSS P&L (−₹363 on 7 trades,
                                              #   14% wins) — news is priced in before our
                                              #   15-min delayed entry, no drift left to
                                              #   capture. Non-large-caps were gross-positive.
    nt_news_delay_seconds: int = 900          # SQS delay after classification (15 min)
    # Hard cutoff: no new entries after this IST minute-of-day (870 = 14:30).
    # Primary control is the EventBridge schedule (ingester stops at 08:45 UTC =
    # 14:15 IST), but SQS messages can sit in the queue longer than expected.
    # This gate in trade_decision catches any stragglers. Bypassed when
    # nt_bypass_market_hours=true so dev/test runs are not affected.
    nt_entry_cutoff_ist: int = 870            # 14:30 IST (14×60+30)
    nt_classifier_prompt_version: str = "1.0.0"  # bump when classifier prompt changes

    # ── Options-trader (opt_*) ────────────────────────────────────────────────
    # All settings prefixed opt_* — completely isolated from nt_* config.

    # Capital: separate budget from the equity system.
    opt_total_capital_inr: float = 150_000.0   # margin budget across all open straddles
    opt_max_positions: int = 3                 # max concurrent paper straddles
    opt_max_stocks_per_signal: int = 1         # 1 straddle per signal (keep it simple)
    opt_max_lots_per_position: int = 1         # 1 lot per straddle (min tradeable unit)

    # Pricing
    opt_iv_baseline: float = 0.30              # fallback IV for BS pricing (30%) when no
                                               #   live ATM IV is available from the NSE chain.
    # Slippage: fraction of premium turnover charged per round trip ONLY when no
    # real bid/ask is captured for the legs. When a live NSE quote exists, the
    # cost model crosses the real half-spread instead (see calc_straddle_costs).
    # 1% was the original flat guess; it dominated day-1 P&L (~80% of all costs).
    opt_slippage_rate: float = 0.01
    # Max age (minutes) of a chain snapshot still considered a usable live quote
    # for entry IV / exit half-spread. Stale quotes fall back to baseline/rate.
    opt_quote_max_age_minutes: int = 10

    # Exit rules (applied to premium P&L, not underlying price)
    opt_target_pct: float = 0.40               # exit when 40% of premium received (decayed away)
    opt_stop_pct: float = 2.00                 # exit when premium doubles (2× loss)
    # Time-stop. <= 0 DISABLES it so the theta strategy runs to EOD/target/stop.
    # A short straddle harvests theta over the whole session, not in 90 min — the
    # inherited 90-min equity stop force-closed 7/7 day-1 trades before any
    # meaningful decay (see options_paper_trading_log). Default off.
    opt_max_hold_minutes: int = 0
    opt_force_close_eod: bool = True           # close all at 15:15 IST

    # Timing
    opt_entry_cutoff_ist: int = 810            # 13:30 IST (earlier than equity — needs monitor time)

    # Auth
    signal_engine_api_key: str = _DEFAULT_API_KEY

    # LLM safety limits
    llm_timeout_s: float = 30.0             # asyncio.wait_for budget per LLM call
    llm_daily_spend_limit_usd: float = 5.0  # halt new LLM calls when daily cost exceeds this

    # ── Momentum-trader (mt_*) + Upstox ──────────────────────────────────────
    # Isolated from nt_*/opt_* by prefix. Spec: research/specs/warrior-patterns-nse.md
    upstox_api_key: str = ""
    upstox_api_secret: str = ""
    upstox_redirect_uri: str = "http://127.0.0.1:8765/callback"
    # Long-lived, read-only token for market-data and WebSocket paper scanning.
    # It is preferred over the legacy daily OAuth token when both are present.
    upstox_analytics_token: str = ""
    upstox_access_token: str = ""             # legacy daily OAuth token; falls back to SSM if empty
    mt_universe_csv: str = "research/data/mt_universe.csv"      # repo-relative or absolute
    mt_extra_universe_files: str = ""         # comma-separated symbol lists (Smallcap/Microcap 250)
    mt_risk_inr: float = 500.0                # rupees at risk per trade (stop distance × qty)
    mt_max_notional_inr: float = 50_000.0     # cap on entry × qty
    mt_max_positions: int = 5                 # concurrent open paper positions
    mt_log_csv: str = "research/backtests/mt_forward_log.csv"  # spec §6 forward log
    mt_cache_dir: str = ".cache_upstox"       # instrument master + candle cache
    mt_strategy: str = "baseline"             # baseline | catalyst_first_pullback | attention_1m
    mt_attention_day_chg_min: float = 1.5      # promotion only; never sufficient to enter
    mt_attention_rvol_min: float = 1.5         # promotion only; never sufficient to enter
    mt_bypass_market_hours: bool = False      # run the loop outside 09:15–15:35 (tests)

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
        # The ECS momentum scanner has no HTTP API and makes no LLM calls. It
        # still validates its database credential, but must not require secrets
        # for components that are not part of that task.
        scanner_only = os.getenv("MOMENTUM_SCANNER_ONLY", "").lower() == "true"
        insecure: list[str] = []
        if not scanner_only and self.signal_engine_api_key == _DEFAULT_API_KEY:
            insecure.append("SIGNAL_ENGINE_API_KEY is still the default dev value")
        if _DEFAULT_MONGO_CREDS in self.mongodb_uri:
            insecure.append("MONGODB_URI contains default dev credentials")
        if not scanner_only and not any(
            [
                self.anthropic_api_key,
                self.openai_api_key,
                self.gemini_api_key,
                self.nvidia_api_key,
                self.deepseek_api_key,
            ]
        ):
            insecure.append("No LLM provider API key is set")
        if insecure:
            raise ValueError(
                f"Refusing to start in STAGE={stage!r} with insecure defaults:\n"
                + "\n".join(f"  • {msg}" for msg in insecure)
            )
        return self
