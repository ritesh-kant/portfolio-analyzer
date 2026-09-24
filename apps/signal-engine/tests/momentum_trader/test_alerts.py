"""Telegram bodies: one fact per line, HTML-safe, no phone-number-looking digits."""
import re

import pandas as pd
from src.momentum_trader.alerts import (
    attention_message,
    entry_message,
    eod_message,
    exit_message,
    money,
    watchlist_message,
)
from src.momentum_trader.engine import AttentionEvent, Candidate, ClosedTrade, Setup

NOW = pd.Timestamp("2026-09-24 10:01", tz="Asia/Kolkata")


def _trade(symbol: str = "IKS", exit_px: float = 1962.50, net: float = 420.0) -> ClosedTrade:
    setup = Setup(name="attention_1m_confirmation", trigger=1935.10, stop=1921.40)
    cand = Candidate(symbol=symbol, time=NOW, setup=setup, day_chg_pct=5.0, rvol=3.0,
                     catalyst=0, event_type="", candle_tags=[])
    return ClosedTrade(cand=cand, entry_time=NOW, entry=1935.10,
                       exit_time=NOW + pd.Timedelta(minutes=83), exit=exit_px,
                       exit_reason="target", qty=18, gross_inr=net + 73.0, costs_inr=73.0,
                       net_inr=net)


def test_money_puts_the_sign_before_the_currency():
    assert money(1234.4, "₹", signed=True) == "+₹1,234"
    assert money(-56.0, "₹", signed=True) == "-₹56"
    assert money(12.3, "$", decimals=2) == "$12.30"
    assert money(0.0, "₹", signed=True) == "₹0"


def test_exit_message_leads_with_the_money_and_marks_profit_or_loss():
    win = exit_message(_trade()).split("\n")
    assert win[0] == "💰 <b>EXIT IKS</b> +₹420"
    assert "(+1.42% · +2.0R)" in win[2]
    assert win[3] == "Costs: ₹73 · held 1h23m"
    loss = exit_message(_trade(exit_px=1921.40, net=-320.0))
    assert loss.startswith("🔻 <b>EXIT IKS</b> -₹320") and "-1.0R" in loss


def test_exit_message_escapes_symbols_and_uses_dollars_for_us():
    msg = exit_message(_trade("M&M"), cur="$")
    assert "EXIT M&amp;M</b> +$420.00" in msg


def test_attention_message_does_not_repeat_the_pattern_tag():
    a = AttentionEvent(symbol="SUNTV", time=NOW, day_chg_pct=3.2, rvol=1.6,
                       reason="pattern:doji:1m", candle_tags=("doji",))
    assert attention_message(a) == "👀 <b>SUNTV</b> doji 1m · +3.2% · RVOL 1.6x"
    b = AttentionEvent(symbol="X", time=NOW, day_chg_pct=2.0, rvol=2.0,
                       reason="pattern:doji:1m", candle_tags=("doji", "hammer"))
    assert "[hammer]" in attention_message(b)


def test_eod_message_explains_a_no_trade_day():
    msg = eod_message(day="2026-09-24", trades=[], attention=12, candidates=0,
                      rejection_reasons=["late"] * 400 + ["spread"] * 150 + ["rvol"] * 50
                      + ["x"] * 6,
                      extra=["📊 Bars: 96.5% exchange candles (50,021 of 51,820 min)"])
    lines = msg.split("\n")
    assert lines[0] == "🏁 <b>EOD 2026-09-24</b>"
    assert lines[1] == "⚪ Trades: 0 (0W/0L) · net <b>₹0</b>"
    assert "12 attention → 0 candidates → 0 trades" in msg
    assert ("🚫 Rejected 606: <code>late</code> 400, <code>spread</code> 150, "
            "<code>rvol</code> 50, other 6") in lines
    assert "setups={}" not in msg
    # Telegram links `digits/digits` as a phone number.
    assert not re.search(r"\d/\d", msg)


def test_eod_message_names_best_and_worst_when_trades_happened():
    msg = eod_message(day="d", trades=[_trade(net=420.0), _trade("ABC", net=-300.0)],
                      attention=3, candidates=2, rejection_reasons=[])
    assert "💰 Trades: 2 (1W/1L) · net <b>+₹120</b>" in msg
    assert "Best IKS +₹420 · worst ABC -₹300" in msg


def test_entry_message_uses_dollars_for_us():
    from tests.momentum_trader.test_scanner_guardrails import _iks_position
    msg = entry_message(_iks_position(), fixed_exit=False, cur="$")
    assert "Entry: <b>$1,935.10</b>" in msg and "risk $246.60" in msg


def test_watchlist_message_shows_the_screen_numbers_and_never_invents_missing_ones():
    msg = watchlist_message("YMAT", price=1.71, day_chg_pct=33.59, rvol=33.66,
                            float_shares=664_113)
    assert msg == ("📋 <b>YMAT</b> added to watchlist · $1.71 · +33.6% · "
                   "RVOL 33.7x · float 664K")
    missing = watchlist_message("GLNDW", price=1.2, day_chg_pct=37.1, rvol=None,
                                float_shares=None)
    assert "RVOL n/a · float n/a" in missing


def test_watchlist_message_warns_when_the_engine_cannot_enter():
    msg = watchlist_message("A&B", price=5.0, day_chg_pct=12.0, rvol=6.0,
                            float_shares=4e6, can_enter=False)
    assert "<b>A&amp;B</b>" in msg and "float 4.0M" in msg
    assert "cannot enter" in msg.split("\n")[1]
