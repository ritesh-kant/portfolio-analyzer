"""Telegram alerts for the options-trader — self-contained, fire-and-forget.

Kept independent of news_trader.telegram so the options module stays fully
isolated (its own send path; a change there can never affect equity alerts).
All functions log on failure but never raise. Options messages carry a 🎲 tag
so they're distinguishable from equity trades in the shared chat.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"

_EXIT_LABELS = {
    "target_hit": "Target reached (premium decayed)",
    "stop_hit": "Stopped out (premium doubled)",
    "time_stop": "Time-stop (90 min)",
    "eod_close": "EOD close (15:15)",
}


def _send(bot_token: str, chat_id: str, text: str) -> None:
    if not bot_token or not chat_id:
        logger.debug("opt_telegram_skipped no token/chat_id configured")
        return
    try:
        url = _API_BASE.format(token=bot_token)
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
            if not resp.is_success:
                logger.warning("opt_telegram_send_failed status=%d body=%s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("opt_telegram_error err=%s", exc)


def alert_straddle_entered(
    bot_token: str,
    chat_id: str,
    symbol: str,
    signal_type: str,
    strike: float,
    expiry: str,
    lots: int,
    lot_size: int,
    entry_total_prem: float,
    spot: float,
) -> None:
    qty = lots * lot_size
    credit = entry_total_prem * qty
    text = (
        f"📝🎲 <b>STRADDLE SOLD</b>\n"
        f"Symbol: <b>{symbol}</b> ({signal_type} news)\n"
        f"Strike: {strike:g} CE+PE  Exp: {expiry}\n"
        f"Premium in: ₹{entry_total_prem:,.2f} × {qty} = ₹{credit:,.0f}\n"
        f"Lots: {lots} ({qty})  Spot: ₹{spot:,.2f}"
    )
    _send(bot_token, chat_id, text)


def alert_straddle_closed(
    bot_token: str,
    chat_id: str,
    symbol: str,
    exit_reason: str,
    entry_total_prem: float,
    exit_total_prem: float,
    net_pnl: float,
) -> None:
    pnl_icon = "✅" if net_pnl >= 0 else "❌"
    text = (
        f"📝🎲 {pnl_icon} <b>STRADDLE CLOSED — {_EXIT_LABELS.get(exit_reason, exit_reason)}</b>\n"
        f"Symbol: <b>{symbol}</b>\n"
        f"Premium: ₹{entry_total_prem:,.2f} → ₹{exit_total_prem:,.2f}\n"
        f"Net P&L: ₹{net_pnl:+,.0f}"
    )
    _send(bot_token, chat_id, text)


def alert_eod_summary(
    bot_token: str,
    chat_id: str,
    day: str,
    closed_count: int,
    wins: int,
    losses: int,
    net_pnl: float,
    open_count: int,
    nse_count: int,
    synth_count: int,
) -> None:
    total_snaps = nse_count + synth_count
    nse_pct = (nse_count / total_snaps * 100) if total_snaps else 0.0
    pnl_icon = "✅" if net_pnl >= 0 else "❌"
    text = (
        f"🎲 <b>OPTIONS EOD — {day}</b>\n"
        f"{pnl_icon} Closed: {closed_count} ({wins}W/{losses}L)  Net: ₹{net_pnl:+,.0f}\n"
        f"Open carried: {open_count}\n"
        f"IV capture: {nse_count}/{total_snaps} NSE ({nse_pct:.0f}%)"
    )
    _send(bot_token, chat_id, text)
