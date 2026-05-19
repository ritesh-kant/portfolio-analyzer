"""order_simulator — portfolio bookkeeping + Indian transaction costs.

Surviving from the demolition phase. The original module ported the production
order_agent's position sizing into a pure synchronous simulator. The sizing
chain (_WIN_PROB_TABLE → _conservative_win_prob → _half_kelly → calc_position)
was deleted because the win probabilities were hardcoded constants, never
updated from realized outcomes. Stubs raising NotImplementedError remain so
callers fail loudly until quant/portfolio/posterior_kelly.py lands in Month 3.

What still works and is reused:
    compute_trade_cost — Indian delivery cost model (slippage + brokerage +
                         STT + stamp + exchange + SEBI + GST). Correct as-is.
    Position / ClosedTrade / Portfolio dataclasses — bookkeeping schema.
    open_position / close_position / should_close — order lifecycle
                         (currently break because calc_position raises; will
                         be unblocked when posterior_kelly is wired in).
    check_circuit_breakers — daily-loss + portfolio-floor halt logic.
    compute_trade_cost — Indian delivery cost model.

Constants kept for the new pipeline to consume:
    REWARD_TO_RISK    = 2.0
    MAX_KELLY_CAP     = 0.25
    MAX_HOLD_DAYS     — TBD per strategy; default 20 stays for now
    POSITION_SIZE_PCT = 12.0
    MAX_POSITIONS     = 8
    MAX_SECTOR_POSITIONS = 3
    DAILY_LOSS_LIMIT_PCT = 3.0
    PORTFOLIO_FLOOR_PCT  = 70.0
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
MAX_HOLD_DAYS = 20      # extended from 10 → give signals more time to play out

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

# NOTE: The static _WIN_PROB_TABLE was removed during the rebuild's demolition
# phase. Win probabilities will be estimated online from realized trade outcomes
# via a Bayesian Beta-Bernoulli posterior, stratified by (strategy, regime,
# model_decile). See quant/portfolio/posterior_kelly.py (to be built Month 3).


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


# ── Core sizing functions (STUBBED — to be replaced in Month 3) ────────────────
#
# The old confidence → win_prob → half-Kelly → confidence-scale chain was based
# on hardcoded values that were never updated from realized trade outcomes.
# These stubs raise NotImplementedError so any caller fails loudly until the
# Bayesian posterior-Kelly sizer lands in quant/portfolio/posterior_kelly.py.


def _half_kelly(confidence: float, r: float = REWARD_TO_RISK) -> float:
    raise NotImplementedError(
        "Static Kelly was removed in the rebuild's demolition phase. "
        "Wire in quant.portfolio.posterior_kelly.kelly_from_posterior() "
        "once Month 3 lands."
    )


def calc_position(
    confidence: float,
    portfolio_value: float,
    entry_price: float,
    available_cash: float,
    *,
    position_size_pct: float = POSITION_SIZE_PCT,
) -> tuple[int, float, float]:
    raise NotImplementedError(
        "calc_position depended on the deleted _half_kelly + _confidence_scale. "
        "Use quant.portfolio.posterior_kelly.size_position() once Month 3 lands."
    )


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
    # NOTE: calc_position raises NotImplementedError until quant/portfolio/
    # posterior_kelly.py replaces the deleted static-Kelly chain (Month 3).
    # Until then, open_position is intentionally non-functional.
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

    # ATR-based stop/target when available (2:1 R:R maintained), else fixed %
    if atr is not None and atr > 0:
        stop_loss = round(fill_price - 1.5 * atr, 2)
        target = round(fill_price + 3.0 * atr, 2)
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

    Applies sell-side slippage and exit transaction costs (STT, brokerage,
    exchange, GST, SEBI) to compute net P&L.

    Updates:
        portfolio.cash       += net exit proceeds (after costs)
        portfolio.invested   -= position_value (entry)
        portfolio.daily_pnl  += realised_pnl (net of all costs)
        portfolio.open_positions — removes the closed position
        portfolio.closed_trades  — appends the ClosedTrade
    """
    # Slippage-adjusted fill price (sell below close)
    fill_price = round(exit_price * (1 - SLIPPAGE_PCT), 2) if COSTS_ENABLED else exit_price

    gross_exit = position.shares * fill_price
    exit_cost = compute_trade_cost(gross_exit, "SELL")
    net_exit = gross_exit - exit_cost

    pnl = net_exit - position.position_value
    return_pct = (pnl / position.position_value * 100) if position.position_value > 0 else 0.0

    trade = ClosedTrade(
        symbol=position.symbol,
        sector=position.sector,
        entry_date=position.entry_date,
        exit_date=exit_date,
        entry_price=position.entry_price,
        exit_price=round(fill_price, 2),
        shares=position.shares,
        position_value=position.position_value,
        confidence=position.confidence,
        kelly_fraction=position.kelly_fraction,
        exit_reason=exit_reason,
        pnl=round(pnl, 2),
        return_pct=round(return_pct, 4),
        was_correct=(return_pct > 0),
        # Attribution fields — carried from open position
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
    portfolio.invested = max(0.0, portfolio.invested - position.position_value)
    portfolio.daily_pnl += pnl
    portfolio.open_positions.remove(position)
    portfolio.closed_trades.append(trade)

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
