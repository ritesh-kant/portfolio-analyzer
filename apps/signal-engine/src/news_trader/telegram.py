"""Telegram Bot API alerts for the news-trader.

All functions are fire-and-forget: they log on failure but never raise.
"""

import logging
import time

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"

# Per-process debounce for failure alerts — prevents alert storms when many
# Lambda invocations fail in the same outage window. Keyed by "category:provider".
# Lambda warm starts reuse the module, so this persists across invocations within
# the same execution environment.
_last_alert_time: dict[str, float] = {}
_ALERT_COOLDOWN_S = 1800  # 30 minutes per error category


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


_EXIT_SHORT = {
    "target_hit": "target",
    "sl_hit": "sl",
    "time_stop": "time",
    "eod_close": "eod",
    "day5": "day5",
}


def alert_eod_summary(
    bot_token: str,
    chat_id: str,
    day: str,
    closed_count: int,
    wins: int,
    losses: int,
    net_pnl: float,
    by_exit: dict[str, int],
    open_count: int,
) -> None:
    """End-of-day roll-up for the equity (news-trader) book."""
    pnl_icon = "✅" if net_pnl >= 0 else "❌"
    breakdown = " · ".join(
        f"{_EXIT_SHORT.get(r, r)} {n}" for r, n in sorted(by_exit.items(), key=lambda kv: -kv[1])
    )
    lines = [
        f"📊 <b>TRADING EOD — {day}</b>",
        f"{pnl_icon} Closed: {closed_count} ({wins}W/{losses}L)  Net: ₹{net_pnl:+,.0f}",
    ]
    if breakdown:
        lines.append(f"By exit: {breakdown}")
    lines.append(f"Open carried: {open_count}")
    _send(bot_token, chat_id, "\n".join(lines))


def alert_classifier_failure(
    bot_token: str,
    chat_id: str,
    provider: str,
    error: str,
    failed_count: int,
    run_id: str,
) -> None:
    """Alert when the LLM classifier fails an entire pipeline run (quota, auth, etc.).

    Debounced per provider — at most one alert per 30 minutes per execution
    environment, so a prolonged outage (e.g. 38 consecutive failed runs) sends
    one Telegram message, not 38.
    """
    category = f"classifier_failure:{provider}"
    now = time.monotonic()
    if now - _last_alert_time.get(category, 0) < _ALERT_COOLDOWN_S:
        logger.debug("telegram_classifier_alert_debounced provider=%s", provider)
        return
    _last_alert_time[category] = now

    excerpt = error[:150]
    text = (
        f"🚨 <b>CLASSIFIER DOWN — {provider.upper()}</b>\n"
        f"Failed articles: {failed_count}\n"
        f"Error: <code>{excerpt}</code>\n"
        f"Run: <code>{run_id}</code>"
    )
    _send(bot_token, chat_id, text)
