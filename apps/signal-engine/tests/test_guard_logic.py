"""Unit tests for guard_agent kill-switch logic.

All network calls are mocked via unittest.mock — no real NSE/BSE requests made.
Tests cover global kill-switches (VIX, Nifty) and per-stock checks
(ASM, GSM, earnings, price-already-moved).
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.pipeline.agents.guard_agent import (
    GuardAgent,
    _NIFTY_DROP_THRESHOLD,
    _PRICE_MOVE_THRESHOLD,
    _VIX_THRESHOLD,
)
from src.pipeline.state import TradingState


# ─── Helpers ──────────────────────────────────────────────────────────────────

_SYMBOLS = ["RELIANCE.NS", "TCS.NS"]

_CLEAN_GUARD = AsyncMock(return_value=(set(), set(), {}))


def _state(
    vix: float | None = 14.0,
    nifty_chg: float = 0.5,
    fii: float = 500.0,
    symbols: list[str] | None = None,
    tech: dict | None = None,
) -> TradingState:
    return TradingState(
        run_id="guard-test",
        date="2025-01-15",
        selected_stocks=symbols if symbols is not None else list(_SYMBOLS),
        market_data={"vix": vix, "nifty_change_pct": nifty_chg, "fii_net_crore": fii},
        technical_data=tech
        or {
            "RELIANCE.NS": {"change_pct_today": 1.0},
            "TCS.NS": {"change_pct_today": 0.5},
        },
    )


@pytest.fixture
def agent() -> GuardAgent:
    return GuardAgent()


# ─── Global kill-switches ─────────────────────────────────────────────────────


class TestVixKillSwitch:
    async def test_vix_above_threshold_blocks_all(self, agent: GuardAgent) -> None:
        state = _state(vix=_VIX_THRESHOLD + 0.1)
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", _CLEAN_GUARD):
            result = await agent._execute(state)
        assert result.guard_result["passed"] == []
        assert len(result.guard_result["blocked"]) == len(_SYMBOLS)
        assert all("VIX" in b["reason"] for b in result.guard_result["blocked"])

    async def test_vix_exactly_at_threshold_passes(self, agent: GuardAgent) -> None:
        # condition is strict: vix > _VIX_THRESHOLD
        state = _state(vix=_VIX_THRESHOLD)
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", _CLEAN_GUARD):
            result = await agent._execute(state)
        assert len(result.guard_result["passed"]) == 2

    async def test_vix_none_does_not_block(self, agent: GuardAgent) -> None:
        state = _state(vix=None)
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", _CLEAN_GUARD):
            result = await agent._execute(state)
        assert len(result.guard_result["passed"]) == 2

    async def test_vix_well_below_threshold_passes(self, agent: GuardAgent) -> None:
        state = _state(vix=12.0)
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", _CLEAN_GUARD):
            result = await agent._execute(state)
        assert len(result.guard_result["passed"]) == 2


class TestNiftyKillSwitch:
    async def test_nifty_below_threshold_blocks_all(self, agent: GuardAgent) -> None:
        state = _state(nifty_chg=_NIFTY_DROP_THRESHOLD - 0.1)
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", _CLEAN_GUARD):
            result = await agent._execute(state)
        assert result.guard_result["passed"] == []
        assert all("Nifty" in b["reason"] for b in result.guard_result["blocked"])

    async def test_nifty_exactly_at_threshold_passes(self, agent: GuardAgent) -> None:
        # condition: nifty_chg < threshold (strict)
        state = _state(nifty_chg=_NIFTY_DROP_THRESHOLD)
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", _CLEAN_GUARD):
            result = await agent._execute(state)
        assert len(result.guard_result["passed"]) == 2

    async def test_nifty_none_does_not_block(self, agent: GuardAgent) -> None:
        state = _state(nifty_chg=None)  # type: ignore[arg-type]
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", _CLEAN_GUARD):
            result = await agent._execute(state)
        assert len(result.guard_result["passed"]) == 2


# ─── Per-stock kill-switches ──────────────────────────────────────────────────


class TestPerStockKillSwitches:
    async def test_asm_stock_blocked_other_passes(self, agent: GuardAgent) -> None:
        state = _state()
        guard_data = AsyncMock(return_value=({"RELIANCE"}, set(), {}))
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", guard_data):
            result = await agent._execute(state)
        blocked = [b["symbol"] for b in result.guard_result["blocked"]]
        assert "RELIANCE.NS" in blocked
        assert "TCS.NS" in result.guard_result["passed"]

    async def test_gsm_stock_blocked(self, agent: GuardAgent) -> None:
        state = _state()
        guard_data = AsyncMock(return_value=(set(), {"TCS"}, {}))
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", guard_data):
            result = await agent._execute(state)
        blocked = [b["symbol"] for b in result.guard_result["blocked"]]
        assert "TCS.NS" in blocked
        assert "RELIANCE.NS" in result.guard_result["passed"]

    async def test_earnings_within_window_blocked(self, agent: GuardAgent) -> None:
        state = _state()
        guard_data = AsyncMock(return_value=(set(), set(), {"RELIANCE": "2025-01-20"}))
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", guard_data):
            result = await agent._execute(state)
        blocked = [b["symbol"] for b in result.guard_result["blocked"]]
        assert "RELIANCE.NS" in blocked
        assert any("Earnings" in b["reason"] for b in result.guard_result["blocked"])

    async def test_price_moved_above_threshold_blocked(self, agent: GuardAgent) -> None:
        state = _state(tech={
            "RELIANCE.NS": {"change_pct_today": _PRICE_MOVE_THRESHOLD + 0.1},
            "TCS.NS": {"change_pct_today": 1.0},
        })
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", _CLEAN_GUARD):
            result = await agent._execute(state)
        blocked = [b["symbol"] for b in result.guard_result["blocked"]]
        assert "RELIANCE.NS" in blocked
        assert "TCS.NS" in result.guard_result["passed"]

    async def test_price_moved_negative_above_threshold_blocked(self, agent: GuardAgent) -> None:
        state = _state(tech={
            "RELIANCE.NS": {"change_pct_today": -(_PRICE_MOVE_THRESHOLD + 0.5)},
            "TCS.NS": {"change_pct_today": 0.5},
        })
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", _CLEAN_GUARD):
            result = await agent._execute(state)
        blocked = [b["symbol"] for b in result.guard_result["blocked"]]
        assert "RELIANCE.NS" in blocked

    async def test_price_exactly_at_threshold_passes(self, agent: GuardAgent) -> None:
        # condition: abs(change_pct) > threshold (strict)
        state = _state(tech={
            "RELIANCE.NS": {"change_pct_today": _PRICE_MOVE_THRESHOLD},
            "TCS.NS": {"change_pct_today": 0.5},
        })
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", _CLEAN_GUARD):
            result = await agent._execute(state)
        assert "RELIANCE.NS" in result.guard_result["passed"]

    async def test_all_clean_all_passed(self, agent: GuardAgent) -> None:
        state = _state()
        with patch("src.pipeline.agents.guard_agent._fetch_guard_data", _CLEAN_GUARD):
            result = await agent._execute(state)
        assert set(result.guard_result["passed"]) == set(_SYMBOLS)
        assert result.guard_result["blocked"] == []

    async def test_empty_stocks_returns_empty(self, agent: GuardAgent) -> None:
        state = _state(symbols=[])
        result = await agent._execute(state)
        assert result.guard_result["passed"] == []
        assert result.guard_result["blocked"] == []
