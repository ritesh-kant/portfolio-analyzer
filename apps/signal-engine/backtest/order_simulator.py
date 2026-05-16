"""order_simulator — replicate order_agent.py sizing and portfolio management.

This module ports the production order_agent's position sizing and circuit-breaker
logic into a pure, synchronous, in-memory simulator for use by the backtest engine.

EXACT VALUES FROM PRODUCTION CODE (src/pipeline/agents/order_agent.py + config.py):

    STOP_PCT              = 0.05   (5% below entry)
    TARGET_PCT            = 0.10   (10% above entry)
    REWARD_TO_RISK        = 2.0
    MAX_KELLY_CAP         = 0.25   (never risk > 25% of portfolio on one trade)

    _WIN_PROB_TABLE (conservative, intentionally below confidence/100):
        ≥80 → 0.60
        ≥75 → 0.57
        ≥70 → 0.54
        ≥65 → 0.52
        ≥60 → 0.50
        <60 → 0.50  (Kelly returns 0 at r=2 when p=0.50 — no position)

    _confidence_scale (graduated multiplier):
        <65%  → ×0.50
        65–70 → ×0.65
        70–75 → ×0.80
        75–80 → ×0.90
        ≥80%  → ×1.00

    position_size_pct     = 12.0   (max 12% of portfolio per position)
    max_positions         = 8
    max_sector_positions  = 3      (config.py line 51 — code says 3, not 2)
    daily_loss_limit_pct  = 3.0    (config.py line 52 — halt if daily loss ≥ 3%)
    portfolio_floor_pct   = 70.0   (config.py line 53 — halt if value < 70% of initial)
    MAX_HOLD_DAYS         = 10     (monitor_agent.py — force-close after 10 days)
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants (must match production exactly) ──────────────────────────────────
STOP_PCT = 0.05
TARGET_PCT = 0.10
REWARD_TO_RISK = 2.0
MAX_KELLY_CAP = 0.25
MAX_HOLD_DAYS = 10

# Config defaults — must mirror config.py
MIN_SIGNAL_CONFIDENCE = 60.0
POSITION_SIZE_PCT = 12.0
MAX_POSITIONS = 8
MAX_SECTOR_POSITIONS = 3
DAILY_LOSS_LIMIT_PCT = 3.0    # halt if daily loss ≥ 3%
PORTFOLIO_FLOOR_PCT = 70.0    # halt if total_value < 70% of initial

_WIN_PROB_TABLE: list[tuple[float, float]] = [
    (80.0, 0.60),
    (75.0, 0.57),
    (70.0, 0.54),
    (65.0, 0.52),
    (60.0, 0.50),
]


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class Position:
    """An open position in the simulated portfolio."""
    symbol: str
    sector: str
    entry_date: str          # ISO date string "YYYY-MM-DD"
    entry_price: float
    shares: int
    stop_loss: float         # entry_price × (1 − STOP_PCT)
    target: float            # entry_price × (1 + TARGET_PCT)
    position_value: float    # shares × entry_price at entry
    confidence: int
    kelly_fraction: float
    days_held: int = 0       # incremented each sim day by the engine


@dataclass
class ClosedTrade:
    """A completed trade with outcome data for metrics and Kelly recalibration."""
    symbol: str
    sector: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    position_value: float
    confidence: int
    kelly_fraction: float
    exit_reason: str         # "TARGET" | "STOP" | "MAX_AGE"
    pnl: float               # rupee P&L
    return_pct: float        # % return
    was_correct: bool        # True if exit_reason == "TARGET"


@dataclass
class Portfolio:
    """Full simulated portfolio state.  Mutated in-place by the simulator."""
    initial_capital: float
    cash: float
    invested: float = 0.0
    open_positions: list[Position] = field(default_factory=list)
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    daily_values: list[float] = field(default_factory=list)  # equity curve
    daily_dates: list[str] = field(default_factory=list)
    daily_pnl: float = 0.0   # running P&L for current calendar day (reset each day)
    daily_pnl_date: str = "" # which date daily_pnl was last reset for

    @property
    def total_value(self) -> float:
        """Mark-to-market portfolio value = cash + sum(position × entry_price).

        The engine updates mark-to-market daily via update_mtm(). This property
        gives a snapshot at any point using entry prices as a floor — the engine
        provides current prices for true MTM.
        """
        invested_value = sum(p.shares * p.entry_price for p in self.open_positions)
        return self.cash + invested_value

    @property
    def open_position_count(self) -> int:
        return len(self.open_positions)

    def sector_count(self, sector: str) -> int:
        return sum(1 for p in self.open_positions if p.sector == sector)

    def has_open_position(self, symbol: str) -> bool:
        return any(p.symbol == symbol for p in self.open_positions)

    def snapshot_equity(self, date_str: str, current_prices: dict[str, float]) -> float:
        """Compute true mark-to-market equity using live prices, record in daily_values."""
        mtm_invested = sum(
            p.shares * current_prices.get(p.symbol, p.entry_price)
            for p in self.open_positions
        )
        total = self.cash + mtm_invested
        self.daily_values.append(total)
        self.daily_dates.append(date_str)
        return total


# ── Core sizing functions (must match order_agent.py exactly) ──────────────────

def _conservative_win_prob(confidence: float) -> float:
    """Port of order_agent._conservative_win_prob()."""
    for min_conf, win_prob in _WIN_PROB_TABLE:
        if confidence >= min_conf:
            return win_prob
    return 0.50


def _half_kelly(confidence: float, r: float = REWARD_TO_RISK) -> float:
    """Port of order_agent._half_kelly()."""
    p = _conservative_win_prob(confidence)
    q = 1.0 - p
    full_kelly = (p * r - q) / r
    half = max(0.0, full_kelly / 2.0)
    return min(half, MAX_KELLY_CAP)


def _confidence_scale(confidence: float) -> float:
    """Port of order_agent._confidence_scale()."""
    if confidence < 65:
        return 0.50
    if confidence < 70:
        return 0.65
    if confidence < 75:
        return 0.80
    if confidence < 80:
        return 0.90
    return 1.00


def calc_position(
    confidence: float,
    portfolio_value: float,
    entry_price: float,
    available_cash: float,
    *,
    position_size_pct: float = POSITION_SIZE_PCT,
) -> tuple[int, float, float]:
    """Return (shares, position_value, kelly_fraction). Port of order_agent._calc_position().

    Returns (0, 0.0, kf) when the position is not feasible (price <= 0, no cash,
    or Kelly fraction rounds to zero shares).
    """
    if entry_price <= 0 or portfolio_value <= 0:
        return 0, 0.0, 0.0

    kf = _half_kelly(confidence)
    scale = _confidence_scale(confidence)
    max_pct_value = portfolio_value * (position_size_pct / 100.0) * scale
    target_value = min(portfolio_value * kf, max_pct_value, available_cash)

    shares = int(target_value // entry_price)
    if shares < 1:
        return 0, 0.0, kf

    actual_value = shares * entry_price
    return shares, actual_value, kf


# ── Circuit breakers ───────────────────────────────────────────────────────────

def check_circuit_breakers(
    portfolio: Portfolio,
    date_str: str,
    current_mtm_value: float,
    *,
    daily_loss_limit_pct: float = DAILY_LOSS_LIMIT_PCT,
    portfolio_floor_pct: float = PORTFOLIO_FLOOR_PCT,
) -> tuple[bool, str | None]:
    """Return (can_trade, reason). Mirrors order_agent circuit-breaker checks.

    Args:
        portfolio:          Current portfolio state.
        date_str:           ISO date string for today ("YYYY-MM-DD").
        current_mtm_value:  Live mark-to-market portfolio value.
        daily_loss_limit_pct: Halt if daily P&L loss ≥ this %.
        portfolio_floor_pct:  Halt if total_value < this % of initial.

    Note: daily_pnl is reset to 0 in Portfolio when the date changes.
    The engine calls this before sizing any new positions on a given day.
    """
    # Daily loss circuit breaker
    if portfolio.initial_capital > 0:
        daily_loss_pct = (portfolio.daily_pnl / portfolio.initial_capital) * 100
        if daily_loss_pct <= -daily_loss_limit_pct:
            return False, (
                f"CIRCUIT_BREAKER daily_loss={daily_loss_pct:.2f}% "
                f"limit={daily_loss_limit_pct:.2f}%"
            )

    # Absolute portfolio floor
    floor_value = portfolio.initial_capital * (portfolio_floor_pct / 100.0)
    if current_mtm_value < floor_value:
        return False, (
            f"CIRCUIT_BREAKER PORTFOLIO_FLOOR total={current_mtm_value:.0f} "
            f"floor={floor_value:.0f} ({portfolio_floor_pct:.0f}% of "
            f"₹{portfolio.initial_capital:.0f})"
        )

    return True, None


# ── Position management ────────────────────────────────────────────────────────

def open_position(
    portfolio: Portfolio,
    symbol: str,
    sector: str,
    entry_date: str,
    entry_price: float,
    confidence: int,
    *,
    position_size_pct: float = POSITION_SIZE_PCT,
    max_positions: int = MAX_POSITIONS,
    max_sector_positions: int = MAX_SECTOR_POSITIONS,
) -> Position | None:
    """Attempt to open a new position. Returns the Position on success, None if skipped.

    Respects max_positions, max_sector_positions, duplicate-position guard, and
    available cash — exactly as order_agent does.
    """
    if portfolio.open_position_count >= max_positions:
        logger.debug("order_sim max_positions=%d reached — skipping %s", max_positions, symbol)
        return None

    if portfolio.has_open_position(symbol):
        logger.debug("order_sim duplicate symbol=%s already OPEN — skipping", symbol)
        return None

    if portfolio.sector_count(sector) >= max_sector_positions:
        logger.debug(
            "order_sim sector_limit symbol=%s sector=%s count=%d limit=%d",
            symbol, sector, portfolio.sector_count(sector), max_sector_positions,
        )
        return None

    mtm_value = portfolio.total_value
    shares, position_value, kelly_frac = calc_position(
        confidence, mtm_value, entry_price, portfolio.cash,
        position_size_pct=position_size_pct,
    )
    if shares < 1:
        logger.debug(
            "order_sim insufficient_cash symbol=%s entry=%.2f cash=%.2f",
            symbol, entry_price, portfolio.cash,
        )
        return None

    stop_loss = round(entry_price * (1 - STOP_PCT), 2)
    target = round(entry_price * (1 + TARGET_PCT), 2)

    pos = Position(
        symbol=symbol,
        sector=sector,
        entry_date=entry_date,
        entry_price=entry_price,
        shares=shares,
        stop_loss=stop_loss,
        target=target,
        position_value=position_value,
        confidence=confidence,
        kelly_fraction=round(kelly_frac, 4),
    )

    # Update portfolio state
    portfolio.cash -= position_value
    portfolio.invested += position_value
    portfolio.open_positions.append(pos)

    logger.debug(
        "order_sim opened symbol=%s shares=%d value=₹%.0f confidence=%d stop=%.2f target=%.2f",
        symbol, shares, position_value, confidence, stop_loss, target,
    )
    return pos


def should_close(
    position: Position,
    current_price: float,
) -> str | None:
    """Return exit reason if the position should be closed, else None.

    Checks (in priority order):
        1. TARGET   — current_price >= target
        2. STOP     — current_price <= stop_loss
        3. MAX_AGE  — position.days_held >= MAX_HOLD_DAYS

    Returns: "TARGET" | "STOP" | "MAX_AGE" | None
    """
    if current_price >= position.target:
        return "TARGET"
    if current_price <= position.stop_loss:
        return "STOP"
    if position.days_held >= MAX_HOLD_DAYS:
        return "MAX_AGE"
    return None


def close_position(
    portfolio: Portfolio,
    position: Position,
    exit_date: str,
    exit_price: float,
    exit_reason: str,
) -> ClosedTrade:
    """Remove the position from the portfolio and record a ClosedTrade.

    Updates:
        portfolio.cash       += exit_value
        portfolio.invested   -= position_value (entry)
        portfolio.daily_pnl  += realised_pnl
        portfolio.open_positions — removes the closed position
        portfolio.closed_trades  — appends the ClosedTrade
    """
    exit_value = position.shares * exit_price
    pnl = exit_value - position.position_value
    return_pct = (pnl / position.position_value * 100) if position.position_value > 0 else 0.0

    trade = ClosedTrade(
        symbol=position.symbol,
        sector=position.sector,
        entry_date=position.entry_date,
        exit_date=exit_date,
        entry_price=position.entry_price,
        exit_price=round(exit_price, 2),
        shares=position.shares,
        position_value=position.position_value,
        confidence=position.confidence,
        kelly_fraction=position.kelly_fraction,
        exit_reason=exit_reason,
        pnl=round(pnl, 2),
        return_pct=round(return_pct, 4),
        was_correct=(exit_reason == "TARGET"),
    )

    portfolio.cash += exit_value
    portfolio.invested = max(0.0, portfolio.invested - position.position_value)
    portfolio.daily_pnl += pnl
    portfolio.open_positions.remove(position)
    portfolio.closed_trades.append(trade)

    logger.debug(
        "order_sim closed symbol=%s exit=%s reason=%s pnl=₹%.0f ret=%.2f%%",
        position.symbol, exit_reason, exit_date, pnl, return_pct,
    )
    return trade


def reset_daily_pnl(portfolio: Portfolio, date_str: str) -> None:
    """Reset the daily P&L counter at the start of each new trading day.

    Mirrors the production order_agent logic where daily_pnl is zeroed
    whenever the date changes.
    """
    if portfolio.daily_pnl_date != date_str:
        portfolio.daily_pnl = 0.0
        portfolio.daily_pnl_date = date_str


def make_portfolio(initial_capital: float) -> Portfolio:
    """Create a fresh simulated portfolio with the given starting capital."""
    return Portfolio(
        initial_capital=initial_capital,
        cash=initial_capital,
    )


def clone_portfolio(portfolio: Portfolio) -> Portfolio:
    """Deep-copy a portfolio for walk-forward fold resets."""
    return deepcopy(portfolio)
