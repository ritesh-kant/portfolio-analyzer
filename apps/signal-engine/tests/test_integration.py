"""Integration tests: signal_agent + order_agent with mocked LLM and MongoDB repos.

The LLM is replaced by a MagicMock that returns preset JSON. MongoDB repositories
are replaced by AsyncMock so no real database connection is required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.pipeline.agents.order_agent import OrderAgent
from src.pipeline.agents.signal_agent import SignalAgent
from src.pipeline.state import TradingState


# ─── Helpers ──────────────────────────────────────────────────────────────────

_LLM_BONUS = 5
_LLM_REASONING = "Strong bullish confluence across momentum and volume indicators."


def _mock_llm(bonus: int = _LLM_BONUS, reasoning: str = _LLM_REASONING) -> MagicMock:
    """LLM mock that returns preset JSON for signal_agent's _llm_bonus call."""
    mock = MagicMock()
    mock.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=f'{{"bonus_score": {bonus}, "reasoning": "{reasoning}", "direction": "BUY"}}'
        )
    )
    return mock


def _base_state() -> TradingState:
    return TradingState(
        run_id="inttest-run-001",
        date="2025-01-15",
        ai_provider="test",
        llm=_mock_llm(),
        selected_stocks=["RELIANCE.NS", "TCS.NS"],
        guard_result={"passed": ["RELIANCE.NS", "TCS.NS"], "blocked": []},
        technical_data={
            # RELIANCE: all non-sector/news signals fire
            # rsi(12) + macd(12) + ema20(10) + ema50(12) + vol(10) + market(8) = 64 base min
            "RELIANCE.NS": {
                "rsi": 35.0,
                "macd_hist": 0.05,
                "above_ema20": True,
                "above_ema50": True,
                "volume_ratio": 2.5,
                "close": 2500.0,
                "ema20": 2480.0,
                "ema50": 2450.0,
                "change_pct_today": 1.0,
            },
            # TCS: no signals fire except maybe market
            "TCS.NS": {
                "rsi": 55.0,
                "macd_hist": -0.01,
                "above_ema20": False,
                "above_ema50": False,
                "volume_ratio": 0.8,
                "close": 3800.0,
                "ema20": 3820.0,
                "ema50": 3850.0,
                "change_pct_today": 0.5,
            },
        },
        market_data={
            "nifty_change_pct": 0.8,
            "fii_net_crore": 1200.0,
            "vix": 13.5,
        },
        sectors=[{"name": "ENERGY", "direction": "bullish", "score": 75}],
        classified_news=[
            {
                "sentiment": "positive",
                "affected_stocks": ["RELIANCE"],
                "affected_sectors": ["ENERGY"],
                "headline": "Reliance AGM: record dividend announced",
            }
        ],
    )


# ─── SignalAgent integration ───────────────────────────────────────────────────


class TestSignalAgentIntegration:
    async def test_reliance_gets_high_confidence_signal(self) -> None:
        state = _base_state()
        mock_signals_repo = AsyncMock()
        mock_analyses_repo = AsyncMock()

        with (
            patch("src.pipeline.agents.signal_agent.TradingSignalsRepository", return_value=mock_signals_repo),
            patch("src.pipeline.agents.signal_agent.StockAnalysesRepository", return_value=mock_analyses_repo),
            patch("src.pipeline.agents.signal_agent.get_db", return_value=MagicMock()),
        ):
            agent = SignalAgent()
            result = await agent._execute(state)

        reliance = next((s for s in result.signals if s["symbol"] == "RELIANCE.NS"), None)
        assert reliance is not None
        # min base: rsi(12)+macd(12)+ema20(10)+ema50(12)+vol(10)+news(14)+market(8) = 78
        assert reliance["base_score"] >= 78
        assert reliance["llm_bonus"] == _LLM_BONUS
        assert reliance["confidence"] == reliance["base_score"] + _LLM_BONUS
        assert reliance["direction"] == "BUY"
        assert reliance["entry_price"] == 2500.0

    async def test_tcs_low_score_below_threshold(self) -> None:
        state = _base_state()
        mock_signals_repo = AsyncMock()
        mock_analyses_repo = AsyncMock()

        with (
            patch("src.pipeline.agents.signal_agent.TradingSignalsRepository", return_value=mock_signals_repo),
            patch("src.pipeline.agents.signal_agent.StockAnalysesRepository", return_value=mock_analyses_repo),
            patch("src.pipeline.agents.signal_agent.get_db", return_value=MagicMock()),
        ):
            agent = SignalAgent()
            result = await agent._execute(state)

        tcs = next((s for s in result.signals if s["symbol"] == "TCS.NS"), None)
        assert tcs is not None
        # TCS only gets market_positive(8) + LLM bonus(5) = 13 → well below 60
        assert tcs["meets_threshold"] is False

    async def test_both_symbols_persisted_to_db(self) -> None:
        state = _base_state()
        mock_signals_repo = AsyncMock()
        mock_analyses_repo = AsyncMock()

        with (
            patch("src.pipeline.agents.signal_agent.TradingSignalsRepository", return_value=mock_signals_repo),
            patch("src.pipeline.agents.signal_agent.StockAnalysesRepository", return_value=mock_analyses_repo),
            patch("src.pipeline.agents.signal_agent.get_db", return_value=MagicMock()),
        ):
            agent = SignalAgent()
            await agent._execute(state)

        assert mock_signals_repo.insert_signal.call_count == 2
        assert mock_analyses_repo.upsert.call_count == 2

    async def test_no_passed_symbols_returns_state_unchanged(self) -> None:
        state = _base_state()
        state = state.model_copy(update={"guard_result": {"passed": [], "blocked": []}})

        with patch("src.pipeline.agents.signal_agent.get_db", return_value=MagicMock()):
            agent = SignalAgent()
            result = await agent._execute(state)

        assert result.signals == []

    async def test_llm_none_uses_zero_bonus(self) -> None:
        state = _base_state()
        state = state.model_copy(update={"llm": None})
        mock_signals_repo = AsyncMock()
        mock_analyses_repo = AsyncMock()

        with (
            patch("src.pipeline.agents.signal_agent.TradingSignalsRepository", return_value=mock_signals_repo),
            patch("src.pipeline.agents.signal_agent.StockAnalysesRepository", return_value=mock_analyses_repo),
            patch("src.pipeline.agents.signal_agent.get_db", return_value=MagicMock()),
        ):
            agent = SignalAgent()
            result = await agent._execute(state)

        reliance = next((s for s in result.signals if s["symbol"] == "RELIANCE.NS"), None)
        assert reliance is not None
        assert reliance["llm_bonus"] == 0  # no LLM → no bonus


# ─── OrderAgent integration ────────────────────────────────────────────────────


def _signal_state(confidence: float = 80.0, meets_threshold: bool = True) -> TradingState:
    return TradingState(
        run_id="inttest-order-001",
        date="2025-01-15",
        signals=[
            {
                "symbol": "RELIANCE.NS",
                "direction": "BUY",
                "confidence": confidence,
                "entry_price": 2500.0,
                "meets_threshold": meets_threshold,
                "reasoning": "Test reasoning",
            }
        ],
    )


def _mock_portfolio(open_positions: int = 2, cash: float = 80_000.0) -> dict:
    return {
        "cash": cash,
        "total_value": 100_000.0,
        "invested": 20_000.0,
        "open_positions": open_positions,
    }


class TestOrderAgentIntegration:
    async def test_order_placed_for_actionable_signal(self) -> None:
        state = _signal_state()
        mock_portfolio_repo = AsyncMock()
        mock_portfolio_repo.get.return_value = _mock_portfolio()
        mock_orders_repo = AsyncMock()
        mock_signals_repo = AsyncMock()

        with (
            patch("src.pipeline.agents.order_agent.VirtualPortfolioRepository", return_value=mock_portfolio_repo),
            patch("src.pipeline.agents.order_agent.PaperOrdersRepository", return_value=mock_orders_repo),
            patch("src.pipeline.agents.order_agent.TradingSignalsRepository", return_value=mock_signals_repo),
            patch("src.pipeline.agents.order_agent.get_db", return_value=MagicMock()),
        ):
            agent = OrderAgent()
            result = await agent._execute(state)

        assert len(result.orders) == 1
        order = result.orders[0]
        assert order["symbol"] == "RELIANCE.NS"
        assert order["shares"] >= 1
        assert order["stop_loss"] < 2500.0
        assert order["target"] > 2500.0
        assert order["status"] == "OPEN"
        assert order["mode"] == "paper"
        mock_orders_repo.insert_order.assert_called_once()

    async def test_below_threshold_signal_skipped(self) -> None:
        state = _signal_state(meets_threshold=False)
        mock_portfolio_repo = AsyncMock()
        mock_orders_repo = AsyncMock()
        mock_signals_repo = AsyncMock()

        with (
            patch("src.pipeline.agents.order_agent.VirtualPortfolioRepository", return_value=mock_portfolio_repo),
            patch("src.pipeline.agents.order_agent.PaperOrdersRepository", return_value=mock_orders_repo),
            patch("src.pipeline.agents.order_agent.TradingSignalsRepository", return_value=mock_signals_repo),
            patch("src.pipeline.agents.order_agent.get_db", return_value=MagicMock()),
        ):
            agent = OrderAgent()
            result = await agent._execute(state)

        assert result.orders == []
        mock_orders_repo.insert_order.assert_not_called()

    async def test_max_positions_cap_blocks_new_order(self) -> None:
        state = _signal_state()
        mock_portfolio_repo = AsyncMock()
        mock_portfolio_repo.get.return_value = _mock_portfolio(open_positions=8)  # at default max
        mock_orders_repo = AsyncMock()
        mock_signals_repo = AsyncMock()

        with (
            patch("src.pipeline.agents.order_agent.VirtualPortfolioRepository", return_value=mock_portfolio_repo),
            patch("src.pipeline.agents.order_agent.PaperOrdersRepository", return_value=mock_orders_repo),
            patch("src.pipeline.agents.order_agent.TradingSignalsRepository", return_value=mock_signals_repo),
            patch("src.pipeline.agents.order_agent.get_db", return_value=MagicMock()),
        ):
            agent = OrderAgent()
            result = await agent._execute(state)

        assert result.orders == []

    async def test_stop_loss_and_target_set_correctly(self) -> None:
        state = _signal_state(confidence=75.0)
        mock_portfolio_repo = AsyncMock()
        mock_portfolio_repo.get.return_value = _mock_portfolio()
        mock_orders_repo = AsyncMock()
        mock_signals_repo = AsyncMock()

        with (
            patch("src.pipeline.agents.order_agent.VirtualPortfolioRepository", return_value=mock_portfolio_repo),
            patch("src.pipeline.agents.order_agent.PaperOrdersRepository", return_value=mock_orders_repo),
            patch("src.pipeline.agents.order_agent.TradingSignalsRepository", return_value=mock_signals_repo),
            patch("src.pipeline.agents.order_agent.get_db", return_value=MagicMock()),
        ):
            agent = OrderAgent()
            result = await agent._execute(state)

        order = result.orders[0]
        entry = 2500.0
        # stop = entry * (1 - 0.05) = 2375.0
        assert abs(order["stop_loss"] - entry * 0.95) < 0.01
        # target = entry * (1 + 0.10) = 2750.0
        assert abs(order["target"] - entry * 1.10) < 0.01

    async def test_portfolio_none_returns_empty_orders(self) -> None:
        state = _signal_state()
        mock_portfolio_repo = AsyncMock()
        mock_portfolio_repo.get.return_value = None  # simulate missing portfolio doc
        mock_orders_repo = AsyncMock()
        mock_signals_repo = AsyncMock()

        with (
            patch("src.pipeline.agents.order_agent.VirtualPortfolioRepository", return_value=mock_portfolio_repo),
            patch("src.pipeline.agents.order_agent.PaperOrdersRepository", return_value=mock_orders_repo),
            patch("src.pipeline.agents.order_agent.TradingSignalsRepository", return_value=mock_signals_repo),
            patch("src.pipeline.agents.order_agent.get_db", return_value=MagicMock()),
        ):
            agent = OrderAgent()
            result = await agent._execute(state)

        assert result.orders == []


# ─── BaseAgent error recovery ─────────────────────────────────────────────────


class TestBaseAgentRecovery:
    async def test_agent_exception_appended_to_errors(self) -> None:
        """Verify BaseAgent.run() catches exceptions and returns state with error logged."""
        state = _base_state()
        mock_signals_repo = AsyncMock()
        mock_signals_repo.insert_signal.side_effect = RuntimeError("DB connection lost")
        mock_analyses_repo = AsyncMock()

        with (
            patch("src.pipeline.agents.signal_agent.TradingSignalsRepository", return_value=mock_signals_repo),
            patch("src.pipeline.agents.signal_agent.StockAnalysesRepository", return_value=mock_analyses_repo),
            patch("src.pipeline.agents.signal_agent.get_db", return_value=MagicMock()),
            patch("src.pipeline.agents.base.AgentLogsRepository", return_value=AsyncMock()),
            patch("src.pipeline.agents.base.get_db", return_value=MagicMock()),
        ):
            agent = SignalAgent()
            result = await agent.run(state)  # use run(), not _execute()

        # Pipeline must NOT crash — state must be returned
        assert isinstance(result, TradingState)
        # Error must be recorded
        assert any("signal_agent" in e for e in result.errors)
