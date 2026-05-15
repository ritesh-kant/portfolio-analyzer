"""TradingState — shared state passed between all LangGraph agent nodes."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TradingState(BaseModel):
    """Mutable state threaded through every node in the LangGraph pipeline."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ── Immutable run metadata ─────────────────────────────────────────────
    run_id: str = ""
    date: str = ""
    ai_provider: str = "ollama"
    llm: Any = None  # BaseChatModel — injected at pipeline startup

    # ── Agent outputs (populated progressively) ───────────────────────────
    raw_news: list[dict[str, Any]] = Field(default_factory=list)
    classified_news: list[dict[str, Any]] = Field(default_factory=list)

    # sectors: [{ name, direction, score, news_ids[] }]
    sectors: list[dict[str, Any]] = Field(default_factory=list)

    # selected symbols e.g. ["SUNPHARMA.NS", "DRREDDY.NS"]
    selected_stocks: list[str] = Field(default_factory=list)

    # technical_data: { symbol: { rsi, macd, ema20, ema50, volume_ratio, ... } }
    technical_data: dict[str, Any] = Field(default_factory=dict)

    # market_data: { nifty_change, vix, fii_net, dii_net }
    market_data: dict[str, Any] = Field(default_factory=dict)

    # guard_result: { passed: [sym, ...], blocked: [{ sym, reason }, ...] }
    guard_result: dict[str, Any] = Field(
        default_factory=lambda: {"passed": [], "blocked": []}
    )

    # Final signals with confidence scores
    signals: list[dict[str, Any]] = Field(default_factory=list)

    # Paper orders placed
    orders: list[dict[str, Any]] = Field(default_factory=list)

    # Non-fatal errors accumulated across agents
    errors: list[str] = Field(default_factory=list)

    # Per-agent wall-clock duration in milliseconds
    agent_timings: dict[str, float] = Field(default_factory=dict)
