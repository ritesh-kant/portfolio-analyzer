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
STOP_PCT = 0.04         # tightened from 0.05 → faster stop-out on real losers
TARGET_PCT = 0.08       # tightened from 0.10 → faster lock-in of gains
REWARD_TO_RISK = 2.0
MAX_KELLY_CAP = 0.25
MAX_HOLD_DAYS = 30      # extended to give chandelier trail room to ride trends

# Pure chandelier trailing stop ("Zerodha cover-order" style).
# The trailing_stop starts at the initial 1.5×ATR stop and ratchets up daily as
# `max(prev_trail, highest_close − TRAIL_ATR_MULTIPLE × ATR)`. There is no fixed
# upside target and no partial profit-taking — winners run until the trail fires.
TRAIL_ATR_MULTIPLE = 2.5

# Config defaults — must mirror config.py
MIN_SIGNAL_CONFIDENCE = 60.0
POSITION_SIZE_PCT = 12.0
MAX_POSITIONS = 8
MAX_SECTOR_POSITIONS = 3
DAILY_LOSS_LIMIT_PCT = 3.0    # halt if daily loss ≥ 3%
PORTFOLIO_FLOOR_PCT = 70.0    # halt if total_value < 70% of initial

# ── Transaction costs (Indian equities — delivery trading) ───────────────────
# Set COSTS_ENABLED=False for cost-free runs (useful for isolating strategy alpha
# from cost drag — always run with True before evaluating live readiness).
COSTS_ENABLED = True
SLIPPAGE_PCT = 0.0015          # 15 bps per side — conservative for Nifty 100 liquidity
_BROKERAGE_PCT = 0.0003        # 3 bps; ₹20 cap applies (at ₹50k–₹120k positions ≈ ₹15–₹36)
_STAMP_DUTY_PCT = 0.00015      # 0.015% — buy-side only
_STT_DELIVERY_PCT = 0.001      # 0.1% — sell-side only (equity delivery STT)
_EXCHANGE_TXN_PCT = 0.0000325  # NSE/BSE transaction charge
_SEBI_PCT = 0.000001           # SEBI turnover fee
_GST_RATE = 0.18               # GST on brokerage + exchange charges


def compute_trade_cost(value: float, side: str) -> float:
    """Total Indian delivery trading costs for one side in rupees.

    Args:
        value: Gross trade value in rupees (shares × price).
        side:  "BUY" (buy-side costs) or "SELL" (sell-side costs incl. STT).
    """
    if not COSTS_ENABLED or value <= 0:
        return 0.0
    brokerage = min(value * _BROKERAGE_PCT, 20.0)
    exchange = value * _EXCHANGE_TXN_PCT
    sebi = value * _SEBI_PCT
    gst = (brokerage + exchange) * _GST_RATE
    stamp = value * _STAMP_DUTY_PCT if side == "BUY" else 0.0
    stt = value * _STT_DELIVERY_PCT if side == "SELL" else 0.0
    return brokerage + exchange + sebi + gst + stamp + stt

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
    entry_price: float       # slippage-adjusted fill price
    shares: int
    stop_loss: float         # anchored to entry_price (ATR-based or fixed %)
    target: float            # anchored to entry_price
    position_value: float    # shares × entry_price at entry
    confidence: int
    kelly_fraction: float
    days_held: int = 0       # incremented each sim day by the engine

    # Signal attribution (set at entry, carried to ClosedTrade for diagnostics)
    triggered_signals: list[str] = field(default_factory=list)
    signal_score_raw: int = 0
    vix_at_entry: float = 0.0
    nifty_above_ema50_at_entry: bool = False
    fii_net_cr_at_entry: float = 0.0
    sector_score_at_entry: int = 0
    had_tier_a_news: bool = False

    # Excursion tracking — updated each sim day in the exit loop
    mfe_pct: float = 0.0    # max favorable excursion: best return % seen during trade
    mae_pct: float = 0.0    # max adverse excursion: worst loss % seen (stored positive)

    # Chandelier trailing-stop state. atr_at_entry is the ATR(14) used at entry;
    # needed for the trail width. trailing_stop ratchets up daily from
    # original_stop (entry − 1.5×ATR) toward (highest_close − 3×ATR).
    atr_at_entry: float = 0.0
    original_stop: float = 0.0  # stop at entry; floor for the trailing stop
    highest_close: float = 0.0  # high-water mark of close price since entry
    trailing_stop: float = 0.0  # max(original_stop, highest_close − 3×ATR)


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
    exit_reason: str         # "TRAIL" | "STOP" | "MAX_AGE" | "TARGET" (legacy)
    pnl: float               # rupee P&L net of transaction costs
    return_pct: float        # % return net of costs
    was_correct: bool        # True if return_pct > 0 (profitable exit)

    # Signal attribution — copied from Position for trade-level analysis
    triggered_signals: list[str] = field(default_factory=list)
    signal_score_raw: int = 0
    vix_at_entry: float = 0.0
    nifty_above_ema50_at_entry: bool = False
    fii_net_cr_at_entry: float = 0.0
    sector_score_at_entry: int = 0
    had_tier_a_news: bool = False

    # Excursion metrics — how close did the trade get to target/stop?
    mfe_pct: float = 0.0
    mae_pct: float = 0.0


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
    signal_details: dict[str, Any] | None = None,
    atr: float | None = None,
) -> Position | None:
    """Attempt to open a new position. Returns the Position on success, None if skipped.

    Respects max_positions, max_sector_positions, duplicate-position guard, and
    available cash — exactly as order_agent does.

    Args:
        signal_details: Optional dict with attribution fields:
            triggered_signals, signal_score_raw, vix_at_entry,
            nifty_above_ema50_at_entry, fii_net_cr_at_entry,
            sector_score_at_entry, had_tier_a_news.
        atr: ATR(14) value for the symbol. When provided, stop and target are
             set at 1.5× and 3.0× ATR from fill price (2:1 R:R). Falls back
             to fixed STOP_PCT / TARGET_PCT when None or zero.
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

    # Slippage-adjusted fill price (buy at above close)
    fill_price = round(entry_price * (1 + SLIPPAGE_PCT), 2) if COSTS_ENABLED else entry_price

    mtm_value = portfolio.total_value
    shares, position_value, kelly_frac = calc_position(
        confidence, mtm_value, fill_price, portfolio.cash,
        position_size_pct=position_size_pct,
    )
    if shares < 1:
        logger.debug(
            "order_sim insufficient_cash symbol=%s entry=%.2f cash=%.2f",
            symbol, fill_price, portfolio.cash,
        )
        return None

    # ATR-based stop when available, else fixed %. The ATR path uses pure
    # chandelier trailing: initial stop at entry − 1.5×ATR, then trail ratchets
    # up daily as max(prev, highest_close − 3×ATR). The `target` field is kept
    # for legacy compatibility but is not consulted in the ATR path.
    atr_at_entry = float(atr) if atr is not None and atr > 0 else 0.0
    if atr_at_entry > 0:
        risk_per_share = 1.5 * atr_at_entry
        stop_loss = round(fill_price - risk_per_share, 2)
        target = round(fill_price + 3.0 * atr_at_entry, 2)  # legacy field; unused in trail path
    else:
        stop_loss = round(fill_price * (1 - STOP_PCT), 2)
        target = round(fill_price * (1 + TARGET_PCT), 2)

    # Entry transaction costs (stamp duty + brokerage + exchange + GST + SEBI)
    entry_cost = compute_trade_cost(position_value, "BUY")

    sd = signal_details or {}
    pos = Position(
        symbol=symbol,
        sector=sector,
        entry_date=entry_date,
        entry_price=fill_price,
        shares=shares,
        stop_loss=stop_loss,
        target=target,
        position_value=position_value,
        confidence=confidence,
        kelly_fraction=round(kelly_frac, 4),
        triggered_signals=list(sd.get("triggered_signals", [])),
        signal_score_raw=int(sd.get("signal_score_raw", 0)),
        vix_at_entry=float(sd.get("vix_at_entry", 0.0)),
        nifty_above_ema50_at_entry=bool(sd.get("nifty_above_ema50_at_entry", False)),
        fii_net_cr_at_entry=float(sd.get("fii_net_cr_at_entry", 0.0)),
        sector_score_at_entry=int(sd.get("sector_score_at_entry", 0)),
        had_tier_a_news=bool(sd.get("had_tier_a_news", False)),
        atr_at_entry=atr_at_entry,
        original_stop=stop_loss,
        highest_close=fill_price,
        trailing_stop=stop_loss,
    )

    # Cash reduced by position value + entry transaction costs
    portfolio.cash -= position_value + entry_cost
    portfolio.invested += position_value
    portfolio.open_positions.append(pos)

    logger.debug(
        "order_sim opened symbol=%s shares=%d value=₹%.0f fill=%.2f conf=%d "
        "stop=%.2f target=%.2f cost=₹%.1f",
        symbol, shares, position_value, fill_price, confidence,
        stop_loss, target, entry_cost,
    )
    return pos


def update_trail(position: Position, current_price: float) -> None:
    """Update high-water mark and chandelier trailing stop (ratchet up only).

    Called once per day per position, after MFE/MAE update, BEFORE should_close.
    The trail is `max(prev_trail, highest_close − 3×ATR)` and never goes below
    the initial 1.5×ATR stop (original_stop is its floor). No-op for non-ATR
    legacy positions.
    """
    if position.atr_at_entry <= 0:
        return
    if current_price > position.highest_close:
        position.highest_close = current_price
    candidate = position.highest_close - TRAIL_ATR_MULTIPLE * position.atr_at_entry
    floor = position.original_stop
    new_trail = max(position.trailing_stop, candidate, floor)
    if new_trail > position.trailing_stop:
        position.trailing_stop = round(new_trail, 2)


def should_close(
    position: Position,
    current_price: float,
) -> str | None:
    """Return exit reason if the position should be closed, else None.

    Pure chandelier exit (ATR-based positions):
        STOP    — price ≤ trailing_stop AND trail still at/below entry
                  (i.e. losing exit; subject to cooloff in engine)
        TRAIL   — price ≤ trailing_stop AND trail has ratcheted above entry
                  (i.e. winning trend-end exit; no cooloff)
        MAX_AGE — position.days_held >= MAX_HOLD_DAYS

    Fallback (atr_at_entry == 0): legacy fixed TARGET / STOP behaviour.

    Returns: "TRAIL" | "STOP" | "MAX_AGE" | "TARGET" (legacy) | None
    """
    # Legacy fixed-pct fallback (no ATR at entry).
    if position.atr_at_entry <= 0:
        if current_price >= position.target:
            return "TARGET"
        if current_price <= position.stop_loss:
            return "STOP"
        if position.days_held >= MAX_HOLD_DAYS:
            return "MAX_AGE"
        return None

    # ATR-based pure chandelier path. Single trail line subsumes stop + target.
    if current_price <= position.trailing_stop:
        # Classify: trail still at/below entry → losing exit (STOP), else TRAIL.
        return "STOP" if position.trailing_stop <= position.entry_price else "TRAIL"
    if position.days_held >= MAX_HOLD_DAYS:
        return "MAX_AGE"
    return None


def close_position(
    portfolio: Portfolio,
    position: Position,
    exit_date: str,
    exit_price: float,
    exit_reason: str,
    *,
    shares_to_close: int | None = None,
) -> ClosedTrade:
    """Close some or all of a position; record a ClosedTrade.

    shares_to_close=None (or equal to position.shares) → full close: position is
    removed from portfolio.open_positions.

    shares_to_close < position.shares → partial close: position is mutated in
    place (shares and position_value reduced pro-rata) and remains open. Used
    by the TP1 ladder to take half off while letting the remainder ride.

    Cost basis for the closed slice = shares_to_close × position.entry_price.
    Applies sell-side slippage and exit transaction costs on the slice only.
    """
    if shares_to_close is None or shares_to_close >= position.shares:
        shares_to_close = position.shares
        is_partial = False
    else:
        if shares_to_close <= 0:
            raise ValueError(f"shares_to_close must be > 0, got {shares_to_close}")
        is_partial = True

    fill_price = round(exit_price * (1 - SLIPPAGE_PCT), 2) if COSTS_ENABLED else exit_price

    cost_basis = shares_to_close * position.entry_price
    gross_exit = shares_to_close * fill_price
    exit_cost = compute_trade_cost(gross_exit, "SELL")
    net_exit = gross_exit - exit_cost

    pnl = net_exit - cost_basis
    return_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0.0

    trade = ClosedTrade(
        symbol=position.symbol,
        sector=position.sector,
        entry_date=position.entry_date,
        exit_date=exit_date,
        entry_price=position.entry_price,
        exit_price=round(fill_price, 2),
        shares=shares_to_close,
        position_value=round(cost_basis, 2),
        confidence=position.confidence,
        kelly_fraction=position.kelly_fraction,
        exit_reason=exit_reason,
        pnl=round(pnl, 2),
        return_pct=round(return_pct, 4),
        was_correct=(return_pct > 0),
        triggered_signals=list(position.triggered_signals),
        signal_score_raw=position.signal_score_raw,
        vix_at_entry=position.vix_at_entry,
        nifty_above_ema50_at_entry=position.nifty_above_ema50_at_entry,
        fii_net_cr_at_entry=position.fii_net_cr_at_entry,
        sector_score_at_entry=position.sector_score_at_entry,
        had_tier_a_news=position.had_tier_a_news,
        mfe_pct=round(position.mfe_pct, 4),
        mae_pct=round(position.mae_pct, 4),
    )

    portfolio.cash += net_exit
    portfolio.invested = max(0.0, portfolio.invested - cost_basis)
    portfolio.daily_pnl += pnl
    portfolio.closed_trades.append(trade)

    if is_partial:
        position.shares -= shares_to_close
        position.position_value = round(position.shares * position.entry_price, 2)
        logger.debug(
            "order_sim partial symbol=%s exit=%s reason=%s fill=%.2f "
            "closed_shares=%d remaining=%d pnl=₹%.0f ret=%.2f%%",
            position.symbol, exit_reason, exit_date, fill_price,
            shares_to_close, position.shares, pnl, return_pct,
        )
    else:
        portfolio.open_positions.remove(position)
        logger.debug(
            "order_sim closed symbol=%s exit=%s reason=%s fill=%.2f "
            "pnl=₹%.0f ret=%.2f%% cost=₹%.1f mfe=%.1f%% mae=%.1f%%",
            position.symbol, exit_reason, exit_date, fill_price,
            pnl, return_pct, exit_cost, position.mfe_pct, position.mae_pct,
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
