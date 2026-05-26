"""Telegram Bot API alerts for the news-trader.

All functions are fire-and-forget: they log on failure but never raise.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"


def _send(bot_token: str, chat_id: str, text: str) -> None:
    if not bot_token or not chat_id:
        logger.debug("telegram_skipped no token/chat_id configured")
        return
    try:
        url = _API_BASE.format(token=bot_token)
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
            if not resp.is_success:
                logger.warning("telegram_send_failed status=%d body=%s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("telegram_error err=%s", exc)


def alert_trade_entered(
    bot_token: str,
    chat_id: str,
    symbol: str,
    signal: str,
    entry_price: float,
    qty: int,
    trailing_sl: float,
    target_price: float,
    confidence: str,
    reasoning: str,
    paper: bool,
) -> None:
    mode = "📝 PAPER" if paper else "🔴 LIVE"
    arrow = "📈" if signal == "bullish" else "📉"
    value = entry_price * qty
    text = (
        f"{mode} {arrow} <b>TRADE ENTERED</b>\n"
        f"Symbol: <b>{symbol}</b>\n"
        f"Entry: ₹{entry_price:,.2f} × {qty} = ₹{value:,.0f}\n"
        f"SL: ₹{trailing_sl:,.2f}  Target: ₹{target_price:,.2f}\n"
        f"Confidence: {confidence.upper()}  Signal: {signal}\n"
        f"Reason: {reasoning}"
    )
    _send(bot_token, chat_id, text)


def alert_trade_closed(
    bot_token: str,
    chat_id: str,
    symbol: str,
    exit_reason: str,
    entry_price: float,
    exit_price: float,
    qty: int,
    net_pnl: float,
    paper: bool,
) -> None:
    mode = "📝 PAPER" if paper else "🔴 LIVE"
    pnl_icon = "✅" if net_pnl >= 0 else "❌"
    reason_map = {"sl_hit": "Stop-loss hit", "target_hit": "Target reached", "day5": "Day-5 close"}
    text = (
        f"{mode} {pnl_icon} <b>TRADE CLOSED — {reason_map.get(exit_reason, exit_reason)}</b>\n"
        f"Symbol: <b>{symbol}</b>\n"
        f"Entry: ₹{entry_price:,.2f}  Exit: ₹{exit_price:,.2f}\n"
        f"Qty: {qty}  Net P&L: ₹{net_pnl:+,.0f}"
    )
    _send(bot_token, chat_id, text)


def alert_sl_updated(
    bot_token: str,
    chat_id: str,
    symbol: str,
    old_sl: float,
    new_sl: float,
    current_price: float,
) -> None:
    text = (
        f"🔒 <b>SL RAISED</b> {symbol}\n"
        f"Price: ₹{current_price:,.2f}  SL: ₹{old_sl:,.2f} → ₹{new_sl:,.2f}"
    )
    _send(bot_token, chat_id, text)
