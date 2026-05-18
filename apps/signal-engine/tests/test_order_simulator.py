"""test_order_simulator — unit tests for backtest/order_simulator.py.

Tests are structured around three concerns:

    1. Sizing parity — _half_kelly, _confidence_scale, calc_position must
       produce identical results to the production order_agent equivalents.

    2. Circuit breakers — daily loss limit and portfolio floor must fire at
       the exact production thresholds and not one tick earlier or later.

    3. Position lifecycle — open_position, should_close, close_position must
       correctly manage Portfolio state (cash, invested, counts, daily_pnl).

Any test failure here means the backtest is simulating different behaviour
than production — which invalidates the performance figures.
"""

import math
import unittest.mock as mock

import pytest

# ── Production modules for parity checks ──────────────────────────────────────
from src.pipeline.agents.order_agent import (
    _calc_position as prod_calc_position,
    _confidence_scale as prod_conf_scale,
    _conservative_win_prob as prod_win_prob,
    _half_kelly as prod_half_kelly,
)

# ── Backtest module under test ─────────────────────────────────────────────────
from backtest.order_simulator import (
    MAX_HOLD_DAYS,
    DAILY_LOSS_LIMIT_PCT,
    PORTFOLIO_FLOOR_PCT,
    POSITION_SIZE_PCT,
    MAX_POSITIONS,
    MAX_SECTOR_POSITIONS,
    STOP_PCT,
    TARGET_PCT,
    TRAIL_ATR_MULTIPLE,
    Position,
    Portfolio,
    ClosedTrade,
    _conservative_win_prob,
    _half_kelly,
    _confidence_scale,
    calc_position,
    check_circuit_breakers,
    close_position,
    clone_portfolio,
    make_portfolio,
    open_position,
    reset_daily_pnl,
    should_close,
    update_trail,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _portfolio(capital: float = 100_000.0) -> Portfolio:
    return make_portfolio(capital)


def _open_pos(
    symbol: str = "TEST.NS",
    sector: str = "IT",
    entry_price: float = 1000.0,
    shares: int = 10,
    days: int = 0,
) -> Position:
    stop = round(entry_price * (1 - STOP_PCT), 2)
    target = round(entry_price * (1 + TARGET_PCT), 2)
    return Position(
        symbol=symbol,
        sector=sector,
        entry_date="2023-01-02",
        entry_price=entry_price,
        shares=shares,
        stop_loss=stop,
        target=target,
        position_value=entry_price * shares,
        confidence=70,
        kelly_fraction=0.02,
        days_held=days,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Sizing parity with production order_agent
# ─────────────────────────────────────────────────────────────────────────────

class TestSizingParity:
    """Each sizing function must return the same value as its production counterpart."""

    @pytest.mark.parametrize("confidence", [60, 62, 65, 67, 70, 72, 75, 77, 80, 85, 95])
    def test_win_prob_matches_production(self, confidence):
        assert _conservative_win_prob(confidence) == prod_win_prob(confidence), (
            f"win_prob diverged at confidence={confidence}"
        )

    @pytest.mark.parametrize("confidence", [60, 62, 65, 67, 70, 72, 75, 77, 80, 85, 95])
    def test_half_kelly_matches_production(self, confidence):
        bt_val = _half_kelly(confidence)
        prod_val = prod_half_kelly(confidence)
        assert math.isclose(bt_val, prod_val, rel_tol=1e-9), (
            f"half_kelly diverged at confidence={confidence}: bt={bt_val} prod={prod_val}"
        )

    @pytest.mark.parametrize("confidence", [60, 63, 65, 68, 70, 73, 75, 78, 80, 90])
    def test_confidence_scale_matches_production(self, confidence):
        assert _confidence_scale(confidence) == prod_conf_scale(confidence), (
            f"confidence_scale diverged at confidence={confidence}"
        )

    @pytest.mark.parametrize("confidence,portfolio_value,entry_price,cash", [
        (60,  100_000, 500.0,  100_000),
        (70,  100_000, 1000.0, 100_000),
        (80,  100_000, 2000.0,  50_000),
        (75,   50_000,  250.0,  50_000),
        (65,  200_000, 3000.0, 200_000),
        (60,  100_000, 100.0,     500),   # cash-constrained
    ])
    def test_calc_position_matches_production(self, confidence, portfolio_value, entry_price, cash):
        bt_shares, bt_val, bt_kf = calc_position(
            confidence, portfolio_value, entry_price, cash
        )
        prod_shares, prod_val, prod_kf = prod_calc_position(
            confidence, portfolio_value, entry_price, POSITION_SIZE_PCT, cash
        )
        assert bt_shares == prod_shares, (
            f"shares diverged conf={confidence}: bt={bt_shares} prod={prod_shares}"
        )
        assert math.isclose(bt_val, prod_val, rel_tol=1e-9), (
            f"position_value diverged conf={confidence}: bt={bt_val} prod={prod_val}"
        )
        assert math.isclose(bt_kf, prod_kf, rel_tol=1e-9), (
            f"kelly_fraction diverged conf={confidence}: bt={bt_kf} prod={prod_kf}"
        )

    def test_calc_position_zero_entry_price(self):
        shares, val, kf = calc_position(70, 100_000, 0.0, 100_000)
        assert shares == 0 and val == 0.0

    def test_calc_position_zero_portfolio(self):
        shares, val, kf = calc_position(70, 0.0, 1000.0, 100_000)
        assert shares == 0 and val == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. Stop / target / constant values match production
# ─────────────────────────────────────────────────────────────────────────────

class TestConstants:
    def test_stop_pct(self):
        assert STOP_PCT == 0.05, "STOP_PCT must match production (5%)"

    def test_target_pct(self):
        assert TARGET_PCT == 0.10, "TARGET_PCT must match production (10%)"

    def test_max_hold_days(self):
        assert MAX_HOLD_DAYS == 10, "MAX_HOLD_DAYS must match monitor_agent (10)"

    def test_daily_loss_limit(self):
        assert DAILY_LOSS_LIMIT_PCT == 3.0, "DAILY_LOSS_LIMIT_PCT must match config (3%)"

    def test_portfolio_floor(self):
        assert PORTFOLIO_FLOOR_PCT == 70.0, "PORTFOLIO_FLOOR_PCT must match config (70%)"

    def test_max_positions(self):
        assert MAX_POSITIONS == 8, "MAX_POSITIONS must match config (8)"

    def test_max_sector_positions(self):
        assert MAX_SECTOR_POSITIONS == 3, "MAX_SECTOR_POSITIONS must match config (3)"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Circuit breakers
# ─────────────────────────────────────────────────────────────────────────────

class TestCircuitBreakers:

    def test_no_breaker_under_normal_conditions(self):
        p = _portfolio(100_000)
        can_trade, reason = check_circuit_breakers(p, "2023-06-01", 100_000.0)
        assert can_trade and reason is None

    def test_daily_loss_fires_at_exact_threshold(self):
        """A daily loss of exactly 3% should halt trading."""
        p = _portfolio(100_000)
        p.daily_pnl = -3_000.0   # exactly 3% of 100_000
        can_trade, reason = check_circuit_breakers(p, "2023-06-01", 97_000.0)
        assert not can_trade
        assert "daily_loss" in reason.lower()

    def test_daily_loss_does_not_fire_just_under_threshold(self):
        """A daily loss of 2.99% must NOT halt trading."""
        p = _portfolio(100_000)
        p.daily_pnl = -2_990.0   # 2.99%
        can_trade, _ = check_circuit_breakers(p, "2023-06-01", 97_010.0)
        assert can_trade

    def test_daily_loss_fires_above_threshold(self):
        """A loss > 3% must also halt."""
        p = _portfolio(100_000)
        p.daily_pnl = -5_000.0   # 5%
        can_trade, _ = check_circuit_breakers(p, "2023-06-01", 95_000.0)
        assert not can_trade

    def test_portfolio_floor_fires_below_70pct(self):
        """Portfolio value < 70% of initial must halt trading."""
        p = _portfolio(100_000)
        can_trade, reason = check_circuit_breakers(p, "2023-06-01", 69_999.0)
        assert not can_trade
        assert "floor" in reason.lower()

    def test_portfolio_floor_does_not_fire_at_exact_70pct(self):
        """Exactly 70% of initial must NOT halt (< not <=)."""
        p = _portfolio(100_000)
        can_trade, _ = check_circuit_breakers(p, "2023-06-01", 70_000.0)
        assert can_trade

    def test_portfolio_floor_does_not_fire_above_70pct(self):
        p = _portfolio(100_000)
        can_trade, _ = check_circuit_breakers(p, "2023-06-01", 85_000.0)
        assert can_trade

    def test_both_breakers_daily_loss_reported_first(self):
        """When both breakers fire, daily loss is checked first (matches production order)."""
        p = _portfolio(100_000)
        p.daily_pnl = -4_000.0   # 4% loss → triggers daily breaker
        can_trade, reason = check_circuit_breakers(p, "2023-06-01", 60_000.0)
        assert not can_trade
        assert "daily_loss" in reason.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 4. Position lifecycle
# ─────────────────────────────────────────────────────────────────────────────

class TestPositionLifecycle:

    def test_open_position_deducts_cash(self):
        p = _portfolio(100_000)
        pos = open_position(p, "RELIANCE.NS", "Energy", "2023-01-02", 2500.0, 70)
        assert pos is not None
        assert p.cash == pytest.approx(100_000 - pos.position_value, rel=1e-6)
        assert p.invested == pytest.approx(pos.position_value, rel=1e-6)

    def test_open_position_increments_count(self):
        p = _portfolio(100_000)
        open_position(p, "INFY.NS", "IT", "2023-01-02", 1500.0, 70)
        assert p.open_position_count == 1

    def test_open_position_sets_stop_and_target(self):
        p = _portfolio(100_000)
        pos = open_position(p, "TCS.NS", "IT", "2023-01-02", 3400.0, 75)
        assert pos is not None
        assert math.isclose(pos.stop_loss, 3400.0 * 0.95, rel_tol=1e-5)
        assert math.isclose(pos.target, 3400.0 * 1.10, rel_tol=1e-5)

    def test_open_position_respects_max_positions(self):
        p = _portfolio(10_000_000)  # large capital so sizing isn't the constraint
        for i in range(MAX_POSITIONS):
            pos = open_position(p, f"SYM{i}.NS", "IT", "2023-01-02", 100.0, 70,
                                max_sector_positions=MAX_POSITIONS + 1)
            assert pos is not None, f"Should have opened position {i}"
        # Next one must be rejected
        extra = open_position(p, "EXTRA.NS", "Banking", "2023-01-02", 100.0, 70,
                              max_sector_positions=MAX_POSITIONS + 1)
        assert extra is None

    def test_open_position_rejects_duplicate_symbol(self):
        p = _portfolio(100_000)
        first = open_position(p, "HDFC.NS", "Banking", "2023-01-02", 1600.0, 70)
        assert first is not None
        second = open_position(p, "HDFC.NS", "Banking", "2023-01-03", 1620.0, 72)
        assert second is None

    def test_open_position_respects_sector_cap(self):
        p = _portfolio(10_000_000)
        for i in range(MAX_SECTOR_POSITIONS):
            sym = f"BANK{i}.NS"
            open_position(p, sym, "Banking", "2023-01-02", 100.0, 70)
        # Next Banking position should be rejected
        rejected = open_position(p, "BANK99.NS", "Banking", "2023-01-02", 100.0, 70)
        assert rejected is None
        # But a different sector is fine
        it_pos = open_position(p, "TCS.NS", "IT", "2023-01-02", 100.0, 70)
        assert it_pos is not None

    def test_open_position_rejects_when_insufficient_cash(self):
        p = _portfolio(1.0)   # ₹1 starting capital
        pos = open_position(p, "RELIANCE.NS", "Energy", "2023-01-02", 2500.0, 70)
        assert pos is None

    def test_close_position_target(self):
        p = _portfolio(100_000)
        pos = open_position(p, "WIPRO.NS", "IT", "2023-01-02", 1000.0, 70)
        assert pos is not None
        initial_cash = p.cash

        exit_price = pos.target + 10   # above target
        trade = close_position(p, pos, "2023-01-10", exit_price, "TARGET")

        assert trade.was_correct
        assert trade.exit_reason == "TARGET"
        assert p.cash == pytest.approx(initial_cash + pos.shares * exit_price, rel=1e-6)
        assert p.open_position_count == 0
        assert len(p.closed_trades) == 1

    def test_close_position_stop(self):
        p = _portfolio(100_000)
        pos = open_position(p, "WIPRO.NS", "IT", "2023-01-02", 1000.0, 70)
        assert pos is not None

        exit_price = pos.stop_loss - 1   # below stop
        trade = close_position(p, pos, "2023-01-06", exit_price, "STOP")

        assert not trade.was_correct
        assert trade.exit_reason == "STOP"
        assert trade.pnl < 0   # a loss

    def test_close_position_updates_daily_pnl(self):
        p = _portfolio(100_000)
        pos = open_position(p, "WIPRO.NS", "IT", "2023-01-02", 1000.0, 70)
        assert pos is not None
        before_pnl = p.daily_pnl

        exit_price = pos.target
        trade = close_position(p, pos, "2023-01-10", exit_price, "TARGET")

        assert p.daily_pnl == pytest.approx(
            before_pnl + trade.pnl, rel=1e-6
        )

    def test_close_position_return_pct_correct(self):
        p = _portfolio(100_000)
        pos = open_position(p, "WIPRO.NS", "IT", "2023-01-02", 1000.0, 70)
        assert pos is not None

        exit_price = 1100.0   # 10% gain
        trade = close_position(p, pos, "2023-01-10", exit_price, "TARGET")

        assert math.isclose(trade.return_pct, 10.0, rel_tol=1e-3)


# ─────────────────────────────────────────────────────────────────────────────
# 5. should_close logic
# ─────────────────────────────────────────────────────────────────────────────

class TestShouldClose:

    @pytest.fixture
    def pos(self):
        return _open_pos(entry_price=1000.0, days=0)

    def test_target_hit_returns_TARGET(self, pos):
        assert should_close(pos, pos.target) == "TARGET"

    def test_above_target_returns_TARGET(self, pos):
        assert should_close(pos, pos.target + 100) == "TARGET"

    def test_stop_hit_returns_STOP(self, pos):
        assert should_close(pos, pos.stop_loss) == "STOP"

    def test_below_stop_returns_STOP(self, pos):
        assert should_close(pos, pos.stop_loss - 50) == "STOP"

    def test_between_stop_and_target_returns_None(self, pos):
        mid = (pos.stop_loss + pos.target) / 2
        assert should_close(pos, mid) is None

    def test_max_age_returns_MAX_AGE(self, pos):
        pos.days_held = MAX_HOLD_DAYS
        mid = (pos.stop_loss + pos.target) / 2
        assert should_close(pos, mid) == "MAX_AGE"

    def test_just_under_max_age_returns_None(self, pos):
        pos.days_held = MAX_HOLD_DAYS - 1
        mid = (pos.stop_loss + pos.target) / 2
        assert should_close(pos, mid) is None

    def test_target_takes_priority_over_max_age(self, pos):
        """If both target hit and max age, TARGET takes priority."""
        pos.days_held = MAX_HOLD_DAYS
        assert should_close(pos, pos.target) == "TARGET"

    def test_stop_takes_priority_over_max_age(self, pos):
        """If both stop hit and max age, STOP takes priority."""
        pos.days_held = MAX_HOLD_DAYS
        assert should_close(pos, pos.stop_loss) == "STOP"

    def test_entry_price_unchanged_returns_None(self, pos):
        """Holding at entry (no movement) should not trigger any exit."""
        assert should_close(pos, pos.entry_price) is None


# ─────────────────────────────────────────────────────────────────────────────
# 6. Portfolio utilities
# ─────────────────────────────────────────────────────────────────────────────

class TestPortfolioUtilities:

    def test_make_portfolio_initialises_correctly(self):
        p = make_portfolio(200_000)
        assert p.initial_capital == 200_000
        assert p.cash == 200_000
        assert p.invested == 0.0
        assert p.open_positions == []
        assert p.closed_trades == []

    def test_reset_daily_pnl_resets_on_new_date(self):
        p = _portfolio()
        p.daily_pnl = -3_500.0
        p.daily_pnl_date = "2023-06-01"
        reset_daily_pnl(p, "2023-06-02")
        assert p.daily_pnl == 0.0
        assert p.daily_pnl_date == "2023-06-02"

    def test_reset_daily_pnl_no_reset_same_date(self):
        p = _portfolio()
        p.daily_pnl = -3_500.0
        p.daily_pnl_date = "2023-06-01"
        reset_daily_pnl(p, "2023-06-01")
        assert p.daily_pnl == -3_500.0   # unchanged

    def test_clone_portfolio_is_deep_copy(self):
        p = _portfolio(100_000)
        open_position(p, "TCS.NS", "IT", "2023-01-02", 3400.0, 75)
        p2 = clone_portfolio(p)
        # Mutating clone does not affect original
        p2.cash = 0.0
        p2.open_positions.clear()
        assert p.cash > 0
        assert len(p.open_positions) == 1

    def test_snapshot_equity_records_mtm_value(self):
        p = _portfolio(100_000)
        pos = open_position(p, "INFY.NS", "IT", "2023-01-02", 1500.0, 70)
        assert pos is not None
        prices = {"INFY.NS": 1600.0}  # position up ₹100/share
        equity = p.snapshot_equity("2023-01-05", prices)
        expected = p.cash + pos.shares * 1600.0
        assert math.isclose(equity, expected, rel_tol=1e-6)
        assert p.daily_values[-1] == equity
        assert p.daily_dates[-1] == "2023-01-05"

    def test_total_value_at_entry(self):
        p = _portfolio(100_000)
        pos = open_position(p, "WIPRO.NS", "IT", "2023-01-02", 500.0, 70)
        assert pos is not None
        # total_value (uses entry price) should equal initial capital
        assert math.isclose(p.total_value, 100_000, rel_tol=1e-4)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Signal replay guard logic
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalReplayGuard:
    """Quick smoke tests for signal_replay.apply_guard — ensure guard thresholds
    match production guard_agent constants."""

    def test_guard_blocks_earnings_within_5_days(self):
        from backtest.signal_replay import apply_guard
        td = {"change_pct_today": 0.5}
        market = {}
        passed, reason = apply_guard("TEST.NS", td, market, earnings_days_away=3)
        assert not passed
        assert "earnings" in reason.lower()

    def test_guard_blocks_earnings_exactly_5_days(self):
        from backtest.signal_replay import apply_guard
        td = {"change_pct_today": 0.5}
        passed, reason = apply_guard("TEST.NS", td, {}, earnings_days_away=5)
        assert not passed

    def test_guard_passes_earnings_6_days_away(self):
        """6 days away is a near-miss penalty, not a kill-switch."""
        from backtest.signal_replay import apply_guard
        td = {"change_pct_today": 0.5}
        passed, _ = apply_guard("TEST.NS", td, {}, earnings_days_away=6)
        assert passed

    def test_guard_blocks_large_intraday_move(self):
        from backtest.signal_replay import apply_guard
        td = {"change_pct_today": 5.1}
        passed, reason = apply_guard("TEST.NS", td, {}, earnings_days_away=None)
        assert not passed
        assert "moved" in reason.lower()

    def test_guard_blocks_large_negative_intraday_move(self):
        from backtest.signal_replay import apply_guard
        td = {"change_pct_today": -5.5}
        passed, _ = apply_guard("TEST.NS", td, {}, earnings_days_away=None)
        assert not passed

    def test_guard_passes_exactly_5pct_intraday(self):
        """Exactly 5% move should still pass (threshold is > 5, not >= 5)."""
        from backtest.signal_replay import apply_guard
        td = {"change_pct_today": 5.0}
        passed, _ = apply_guard("TEST.NS", td, {}, earnings_days_away=None)
        assert passed

    def test_guard_passes_normal_conditions(self):
        from backtest.signal_replay import apply_guard
        td = {"change_pct_today": 1.2}
        passed, reason = apply_guard("TEST.NS", td, {}, earnings_days_away=None)
        assert passed and reason is None


# ─────────────────────────────────────────────────────────────────────────────
# 8. Signal scoring parity with production
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalScoringParity:
    """compute_signal_score must produce the same base_score as
    production signal_agent._score_base() for identical inputs."""

    def _prod_score(self, symbol, td, market_data, sectors):
        from src.pipeline.agents.signal_agent import _score_base
        from src.pipeline.sector_map import STOCK_TO_SECTOR
        # Production _score_base expects classified_news too
        score, triggered, weak = _score_base(symbol, td, market_data, sectors, [])
        return score, triggered, weak

    def _bt_score(self, symbol, td, market_data, sectors):
        from backtest.signal_replay import _score_base
        from src.pipeline.sector_map import STOCK_TO_SECTOR
        score, triggered, weak = _score_base(
            symbol, td, market_data, sectors, STOCK_TO_SECTOR,
            news_mode=False,
        )
        return score, triggered, weak

    @pytest.mark.parametrize("td,market_data,sectors,description", [
        (
            # All bullish signals except news
            {"rsi": 35.0, "macd_hist": 0.05, "above_ema20": True, "above_ema50": True,
             "volume_ratio": 2.0, "close": 2500.0, "ema20": 2480.0, "ema50": 2450.0,
             "change_pct_today": 1.0, "pct_from_high": -2.0, "pct_from_low": 30.0,
             "high_75d": 2551.0, "low_75d": 1800.0, "pcr": None},
            {"nifty_change_pct": 0.8, "fii_net_crore": 1200.0, "vix": 13.5, "vix_caution": False},
            [{"name": "IT", "direction": "bullish", "score": 75}],
            "all_bullish_no_news",
        ),
        (
            # Bearish conditions
            {"rsi": 72.0, "macd_hist": -0.1, "above_ema20": False, "above_ema50": False,
             "volume_ratio": 0.8, "close": 1000.0, "ema20": 1050.0, "ema50": 1100.0,
             "change_pct_today": -1.5, "pct_from_high": -15.0, "pct_from_low": 5.0,
             "high_75d": 1176.0, "low_75d": 950.0, "pcr": None},
            {"nifty_change_pct": -0.3, "fii_net_crore": -500.0, "vix": 20.0, "vix_caution": True},
            [],
            "bearish_conditions",
        ),
        (
            # Mixed: RSI oversold but no sector
            {"rsi": 36.0, "macd_hist": 0.02, "above_ema20": False, "above_ema50": True,
             "volume_ratio": 1.8, "close": 800.0, "ema20": 820.0, "ema50": 780.0,
             "change_pct_today": 0.2, "pct_from_high": -8.0, "pct_from_low": 12.0,
             "high_75d": 869.0, "low_75d": 714.0, "pcr": None},
            {"nifty_change_pct": 0.5, "fii_net_crore": None, "vix": 16.0, "vix_caution": False},
            [],
            "mixed_no_sector",
        ),
    ])
    def test_base_score_matches_production(self, td, market_data, sectors, description):
        """Uses TCS.NS as a known symbol in STOCK_TO_SECTOR (IT sector)."""
        symbol = "TCS.NS"
        prod_score, _, _ = self._prod_score(symbol, td, market_data, sectors)
        bt_score, _, _ = self._bt_score(symbol, td, market_data, sectors)
        assert prod_score == bt_score, (
            f"[{description}] base_score diverged: production={prod_score} backtest={bt_score}"
        )

    def test_near_miss_earnings_penalty_applied(self):
        from backtest.signal_replay import compute_signal_score
        from src.pipeline.sector_map import STOCK_TO_SECTOR

        td = {"rsi": 35.0, "macd_hist": 0.05, "above_ema20": True, "above_ema50": True,
              "volume_ratio": 2.0, "close": 2500.0, "ema20": 2480.0, "ema50": 2450.0,
              "change_pct_today": 1.0, "pct_from_high": -2.0, "pct_from_low": 30.0,
              "high_75d": 2551.0, "low_75d": 1800.0, "pcr": None}
        market = {"nifty_change_pct": 0.8, "fii_net_crore": 1200.0, "vix": 13.5, "vix_caution": False}

        score_no_earnings, _, _ = compute_signal_score(
            "TCS.NS", td, market, [], STOCK_TO_SECTOR, earnings_days_away=None
        )
        score_near_earnings, _, _ = compute_signal_score(
            "TCS.NS", td, market, [], STOCK_TO_SECTOR, earnings_days_away=8
        )
        assert score_near_earnings == score_no_earnings - 5, (
            f"Earnings near-miss should subtract 5 pts: "
            f"no_earnings={score_no_earnings} near={score_near_earnings}"
        )

    def test_rsi_overbought_cap_applied(self):
        from backtest.signal_replay import compute_signal_score
        from src.pipeline.sector_map import STOCK_TO_SECTOR

        td = {"rsi": 80.0, "macd_hist": 0.05, "above_ema20": True, "above_ema50": True,
              "volume_ratio": 2.0, "close": 2500.0, "ema20": 2480.0, "ema50": 2450.0,
              "change_pct_today": 1.0, "pct_from_high": -2.0, "pct_from_low": 30.0,
              "high_75d": 2551.0, "low_75d": 1800.0, "pcr": None}
        market = {"nifty_change_pct": 1.0, "fii_net_crore": 500.0, "vix": 13.0, "vix_caution": False}

        score, triggered, _ = compute_signal_score(
            "TCS.NS", td, market, [{"name": "IT", "direction": "bullish", "score": 80}],
            STOCK_TO_SECTOR, earnings_days_away=None
        )
        assert score <= 68, f"RSI > 75 should cap confidence at 68, got {score}"

    def test_vix_caution_penalty_applied(self):
        from backtest.signal_replay import compute_signal_score
        from src.pipeline.sector_map import STOCK_TO_SECTOR

        td = {"rsi": 35.0, "macd_hist": 0.05, "above_ema20": True, "above_ema50": True,
              "volume_ratio": 2.0, "close": 2500.0, "ema20": 2480.0, "ema50": 2450.0,
              "change_pct_today": 1.0, "pct_from_high": -2.0, "pct_from_low": 30.0,
              "high_75d": 2551.0, "low_75d": 1800.0, "pcr": None}

        market_no_caution  = {"nifty_change_pct": 0.8, "fii_net_crore": 1200.0,
                              "vix": 14.0, "vix_caution": False}
        market_with_caution = {"nifty_change_pct": 0.8, "fii_net_crore": 1200.0,
                               "vix": 20.0, "vix_caution": True}

        score_no_caution, _, _ = compute_signal_score(
            "TCS.NS", td, market_no_caution, [], STOCK_TO_SECTOR
        )
        score_caution, _, _ = compute_signal_score(
            "TCS.NS", td, market_with_caution, [], STOCK_TO_SECTOR
        )
        assert score_caution == score_no_caution - 5, (
            f"VIX caution should subtract 5 pts: no_caution={score_no_caution} caution={score_caution}"
        )



# ─────────────────────────────────────────────────────────────────────────────
# 9. Pure chandelier trailing stop (v10 — Zerodha cover-order style)
#
# Exit rule for ATR-equipped positions:
#   trailing_stop = max(prev_trail, highest_close − TRAIL_ATR_MULTIPLE × ATR,
#                        original_stop)         # ratchets up only; never below 1.5×ATR stop
#   price ≤ trailing_stop ∧ trail ≤ entry  → "STOP"  (losing exit; cooloff)
#   price ≤ trailing_stop ∧ trail >  entry → "TRAIL" (winning trend-end exit)
#   days_held ≥ MAX_HOLD_DAYS              → "MAX_AGE"
# ─────────────────────────────────────────────────────────────────────────────


def _atr_position(
    entry_price: float = 1000.0,
    atr: float = 20.0,
    shares: int = 100,
    days: int = 0,
) -> Position:
    """Build an ATR-equipped Position with chandelier trail seeded at entry."""
    risk_per_share = 1.5 * atr
    stop = round(entry_price - risk_per_share, 2)
    target = round(entry_price + 3.0 * atr, 2)   # legacy display only
    return Position(
        symbol="TRAIL.NS",
        sector="IT",
        entry_date="2024-01-02",
        entry_price=entry_price,
        shares=shares,
        stop_loss=stop,
        target=target,
        position_value=entry_price * shares,
        confidence=70,
        kelly_fraction=0.02,
        days_held=days,
        atr_at_entry=atr,
        original_stop=stop,
        highest_close=entry_price,
        trailing_stop=stop,
    )


class TestChandelierTrail:
    """Pure chandelier exit — no TP1, no fixed target."""

    def test_trail_constant(self):
        assert TRAIL_ATR_MULTIPLE == 3.0

    # ── pre-ratchet (trail still at original_stop) ────────────────────────────
    def test_initial_stop_is_original_1_5_atr_stop(self):
        pos = _atr_position(entry_price=1000.0, atr=20.0)
        assert pos.trailing_stop == 970.0   # 1000 − 1.5×20
        assert pos.original_stop == 970.0

    def test_breach_below_entry_returns_STOP(self):
        pos = _atr_position()
        assert should_close(pos, pos.trailing_stop) == "STOP"
        assert should_close(pos, pos.trailing_stop - 1) == "STOP"

    def test_above_initial_stop_returns_None(self):
        pos = _atr_position()
        assert should_close(pos, pos.entry_price) is None
        assert should_close(pos, pos.entry_price + 100) is None

    def test_no_upside_cap(self):
        """Pure chandelier removes the fixed target — even at +∞ no exit unless
        the trail fires or max-age hits."""
        pos = _atr_position()
        assert should_close(pos, pos.entry_price * 10) is None

    # ── ratchet (trail moves up with highest_close) ───────────────────────────
    def test_update_trail_ratchets_high_water_mark(self):
        pos = _atr_position()
        update_trail(pos, 1010.0)
        assert pos.highest_close == 1010.0
        update_trail(pos, 1005.0)
        assert pos.highest_close == 1010.0
        update_trail(pos, 1050.0)
        assert pos.highest_close == 1050.0

    def test_update_trail_floors_at_original_stop(self):
        """Until highest_close moves up enough that (highest − 3×ATR) > original_stop,
        the trail stays at the initial 1.5×ATR stop."""
        pos = _atr_position(entry_price=1000.0, atr=20.0)
        update_trail(pos, pos.entry_price)
        assert pos.trailing_stop == 970.0

    def test_update_trail_moves_trailing_stop_up_only(self):
        pos = _atr_position(entry_price=1000.0, atr=20.0)
        update_trail(pos, 1100.0)
        assert pos.trailing_stop == 1040.0   # 1100 − 60
        update_trail(pos, 1050.0)
        assert pos.trailing_stop == 1040.0
        update_trail(pos, 1200.0)
        assert pos.trailing_stop == 1140.0   # 1200 − 60

    def test_update_trail_noop_when_no_atr(self):
        pos = _atr_position()
        pos.atr_at_entry = 0.0
        orig_stop = pos.trailing_stop
        update_trail(pos, 9999.0)
        assert pos.trailing_stop == orig_stop
        assert pos.highest_close == pos.entry_price

    # ── post-ratchet exits (trail now above entry) ────────────────────────────
    def test_trail_above_entry_returns_TRAIL(self):
        """Once chandelier ratchets above entry, a breach is a profitable
        trend-end exit (TRAIL), not a STOP — no cooloff."""
        pos = _atr_position(entry_price=1000.0, atr=20.0)
        update_trail(pos, 1100.0)
        assert pos.trailing_stop == 1040.0
        assert should_close(pos, 1040.0) == "TRAIL"
        assert should_close(pos, 1039.0) == "TRAIL"

    def test_trail_at_entry_classified_as_STOP(self):
        """A breach when the trail is exactly at entry is breakeven —
        classify as STOP (the trail hasn't yet earned 'winner' status)."""
        pos = _atr_position(entry_price=1000.0, atr=20.0)
        pos.trailing_stop = pos.entry_price
        assert should_close(pos, 1000.0) == "STOP"

    def test_above_ratcheted_trail_returns_None(self):
        pos = _atr_position(entry_price=1000.0, atr=20.0)
        update_trail(pos, 1100.0)
        assert should_close(pos, 1080.0) is None

    # ── max-age failsafe ──────────────────────────────────────────────────────
    def test_max_age_fires_when_held_too_long(self):
        pos = _atr_position(days=MAX_HOLD_DAYS)
        assert should_close(pos, pos.entry_price + 100) == "MAX_AGE"

    # ── close_position invariants (partial-close path still works) ────────────
    def test_full_close_removes_position(self):
        p = make_portfolio(1_000_000)
        pos = _atr_position(entry_price=1000.0, atr=20.0, shares=100)
        p.cash -= pos.position_value
        p.invested += pos.position_value
        p.open_positions.append(pos)

        close_position(p, pos, "2024-01-20", pos.trailing_stop, "TRAIL")
        assert p.open_position_count == 0
        assert len(p.closed_trades) == 1
        assert p.closed_trades[0].shares == 100
        assert p.closed_trades[0].exit_reason == "TRAIL"

    def test_partial_close_still_supported(self):
        """close_position kept partial-close capability for back-compat; not
        used by v10 exit ladder but the API contract must hold."""
        p = make_portfolio(1_000_000)
        pos = _atr_position(entry_price=1000.0, atr=20.0, shares=100)
        p.cash -= pos.position_value
        p.invested += pos.position_value
        p.open_positions.append(pos)

        trade = close_position(
            p, pos, "2024-01-15", 1050.0, "TRAIL", shares_to_close=40,
        )
        assert trade.shares == 40
        assert pos.shares == 60
        assert p.open_position_count == 1

    # ── end-to-end trend simulation ───────────────────────────────────────────
    def test_end_to_end_uptrend_exits_at_TRAIL_far_above_entry(self):
        """Strong uptrend: trail ratchets daily, trend breaks, single TRAIL exit."""
        p = make_portfolio(1_000_000)
        pos = _atr_position(entry_price=1000.0, atr=20.0, shares=100)
        p.cash -= pos.position_value
        p.invested += pos.position_value
        p.open_positions.append(pos)

        for price in (1050, 1100, 1150, 1200, 1250, 1300):
            update_trail(pos, price)
        assert pos.trailing_stop == 1240.0   # 1300 − 60

        assert should_close(pos, 1240.0) == "TRAIL"
        close_position(p, pos, "2024-02-01", 1240.0, "TRAIL")
        assert len(p.closed_trades) == 1
        assert p.closed_trades[0].pnl > 0
        assert p.closed_trades[0].shares == 100   # single full-position exit
